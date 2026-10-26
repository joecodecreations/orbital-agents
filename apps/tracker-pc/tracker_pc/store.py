"""The single writer of items.json (TrackerStore.swift). Everything (HTTP, the inbox folders, the
hub relay, the panel) funnels through one lock here."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import media as media_mod
from .config import OrbitalConfig, TrackerPaths
from .jsonlog import JSONLineLogger
from .model import (
    CommandError, canonical_op, dumps, finished_at, make_id, markdown_paths, normalize_agent,
    clamp_progress, normalize_item, normalize_path, now_iso, parse_date, iso, valid_id, validate_command, write_atomic,
)
from .relay import prune_files

MAX_NOTES = 300
MAX_MEDIA = 200
MAX_SUBAGENTS = 200


class StoreError(ValueError):
    pass


def _normalize_origin(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if s in ("ios", "mac", "direct", "agent"):
        return s
    if s in ("iphone", "ipad"):
        return "ios"
    if s in ("macos", "os", "catalyst", "desktop", "app"):
        return "mac"
    if s in ("chat", "cli", "ai"):
        return "direct"
    if s in ("satellite", "automation", "api", "sub"):
        return "agent"
    return None


def _clean(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    t = s.strip()
    return t or None


class TrackerStore:
    def __init__(self, cfg: OrbitalConfig, paths: TrackerPaths, log: JSONLineLogger,
                 save_delay: float = 0.4) -> None:
        self.cfg = cfg
        self.paths = paths
        self.log = log
        self.save_delay = save_delay
        self.items: list[dict] = []
        self.lock = threading.RLock()
        self.version = 0  # bumps on every change; the panel polls it
        self._next_color = 0
        self._save_timer: threading.Timer | None = None
        self._archiver = ThreadPoolExecutor(max_workers=2, thread_name_prefix="archive")
        self._load()

    @property
    def machine_name(self) -> str:
        return self.cfg.machine_name

    # Persistence

    def _load(self) -> None:
        try:
            raw = self.paths.items_file.read_bytes()
        except OSError:
            return
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("items.json is not a list")
            items = [i for i in map(normalize_item, parsed) if i]
        except ValueError as exc:
            backup = self.paths.items_file.with_name(f"items.corrupt-{int(time.time())}.json")
            try:
                backup.write_bytes(raw)
            except OSError:
                pass
            self.log.error("items.json unreadable; backed up", error=str(exc), backup=str(backup))
            return
        # Nothing is still processing at launch, and finished items never come back onto the panel.
        auto_closed = 0
        for item in items:
            item["processing"] = False
            if item["status"] == "done" and not item["dismissed"]:
                item["dismissed"] = True
                auto_closed += 1
        self.items = items
        self._next_color = max((i["colorIndex"] for i in items), default=-1) + 1
        if auto_closed:
            self._event("auto_closed", count=auto_closed, reason="done items leave the live panel")
            self.save_now()

    def _changed(self) -> None:
        self.version += 1
        if self._save_timer is not None:
            self._save_timer.cancel()
        if self.save_delay <= 0:
            self.save_now()
            return
        self._save_timer = threading.Timer(self.save_delay, self.save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def save_now(self) -> None:
        with self.lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            try:
                write_atomic(self.paths.items_file, dumps(self.items, pretty=True))
            except OSError as exc:
                self.log.error("save failed", error=str(exc))

    def close(self) -> None:
        self._archiver.shutdown(wait=True, cancel_futures=False)
        self.save_now()

    def _event(self, name: str, **fields: Any) -> None:
        self.log.info(f"item {name.replace('_', ' ')}", event=name, **fields)

    # Queries

    def item(self, item_id: str) -> dict | None:
        with self.lock:
            return next((i for i in self.items if i["id"] == item_id), None)

    def snapshot(self, all_items: bool = True) -> list[dict]:
        with self.lock:
            items = self.items if all_items else [i for i in self.items if i["status"] == "active" and not i["dismissed"]]
            return json.loads(json.dumps(items))

    @property
    def active_count(self) -> int:
        with self.lock:
            return sum(1 for i in self.items if i["status"] == "active" and not i["dismissed"])

    def media_ref(self, item_id: str, media_id: str) -> dict | None:
        """A media ref by item (or subtask) id and media id, for GET /v1/media/<item>/<media>."""
        with self.lock:
            for item in self.items:
                if item["id"] == item_id:
                    for m in item["media"]:
                        if m["id"] == media_id:
                            return dict(m)
                for sub in item["subagents"]:
                    if sub["id"] == item_id or item["id"] == item_id:
                        for m in sub["media"]:
                            if m["id"] == media_id:
                                return dict(m)
        return None

    def resolve_media(self, ref: dict, prefer_archive: bool = False) -> tuple[Path, bool] | None:
        """(file, is_archive): the original while it exists on this machine, else the archived JPEG."""
        local = ref.get("machine") in (None, self.machine_name)
        original = ref.get("originalPath") or ""
        archive = None
        rel = ref.get("archivePath")
        if rel and media_mod.is_safe_archive_path(rel):
            path = self.paths.archive_path(rel)
            if path.is_file():
                archive = path
        if prefer_archive and archive is not None:
            return archive, True
        if local and original and os.path.isfile(original):
            return Path(original), False
        return (archive, True) if archive is not None else None

    # Your own items (typed in the panel)

    def _take_color(self) -> int:
        c = self._next_color
        self._next_color += 1
        return c

    def add_personal(self, title: str, details: str = "", source: str = "manual") -> str:
        with self.lock:
            now = now_iso()
            src = source if source in ("voice", "manual") else "manual"
            item = {"id": make_id(), "title": title[:200], "details": details, "source": src,
                    "origin": "mac", "machine": self.machine_name, "colorIndex": self._take_color(),
                    "status": "active", "createdAt": now, "updatedAt": now, "dismissed": False,
                    "processing": False, "notes": [], "media": [], "subagents": []}
            self.items.append(item)
            self._event("added", id=item["id"], title=title, source=src, origin="mac")
            self._changed()
            return item["id"]

    def user_close(self, item_id: str) -> None:
        """The user clicked X: active items become done and leave the panel. History keeps them."""
        with self.lock:
            item = self.item(item_id)
            if item is None:
                return
            if item["status"] == "active":
                self._complete(item, "user", None)
            item["dismissed"] = True
            self._event("dismissed", id=item_id, title=item["title"])
            self._changed()

    def mark_done(self, item_id: str, summary: str | None = None) -> None:
        with self.lock:
            item = self.item(item_id)
            if item is None or item["status"] != "active":
                return
            self._complete(item, "user", summary)
            self._changed()

    def _complete(self, item: dict, by: str, summary: str | None) -> None:
        now = now_iso()
        item["status"] = "done"
        item["completedAt"] = now
        item["completedBy"] = by
        item["updatedAt"] = now
        item["dismissed"] = True
        if summary:
            item["summary"] = summary
        for sub in item["subagents"]:
            if sub["status"] == "active":
                sub["status"] = "done"
                sub["completedAt"] = now
        created = parse_date(item["createdAt"])
        minutes = int((datetime.now(timezone.utc) - created).total_seconds() // 60) if created else 0
        self._event("completed", id=item["id"], title=item["title"], by=by, agent=item.get("agent"),
                    machine=item.get("machine"), minutes=minutes)

    # Agent commands

    def apply(self, cmd: dict) -> str:
        """Applies a command from HTTP, an inbox file or the hub relay. Returns the affected id."""
        try:
            validate_command(cmd)
        except CommandError as exc:
            raise StoreError(str(exc)) from None
        with self.lock:
            return self._apply(cmd)

    def _apply(self, cmd: dict) -> str:
        op = canonical_op(cmd["op"])
        agent = normalize_agent(cmd.get("agent"))
        machine = (_clean(cmd.get("machine")) or self.machine_name)[:64]
        if op == "start":
            if cmd.get("parent"):
                return self._apply({**cmd, "op": "sub"})
            item_id = cmd.get("id") or make_id()
            if self.item(item_id) is not None:
                return self._update(item_id, cmd)
            title = _clean(cmd.get("title"))
            if title is None:
                raise StoreError("title is required")
            now = now_iso()
            sent = parse_date(cmd.get("sentAt"))
            created = min(sent, datetime.now(timezone.utc)) if sent else None
            item: dict[str, Any] = {
                "id": item_id, "title": title[:200], "details": cmd.get("details") or "", "source": "agent",
                "agent": agent, "machine": machine, "colorIndex": self._take_color(), "status": "active",
                "createdAt": iso(created) if created else now, "updatedAt": iso(created) if created else now,
                "dismissed": False, "processing": False, "notes": [], "subagents": [],
            }
            if cmd.get("agentLabel") is not None:
                item["agentLabel"] = cmd["agentLabel"]
            if cmd.get("workspace") is not None:
                item["workspace"] = cmd["workspace"]
            origin = _normalize_origin(cmd.get("origin"))
            if origin:
                item["origin"] = origin
            model = _clean(cmd.get("model"))
            if model:
                item["model"] = model[:120]
            item["media"] = self._accept_media(cmd.get("media"), item_id, None, machine)
            if clamp_progress(cmd.get("progress")) is not None:
                item["progress"] = clamp_progress(cmd.get("progress"))
            if isinstance(cmd.get("usage"), dict):
                item["usage"] = cmd["usage"]
                if not item.get("model") and isinstance(cmd["usage"].get("model"), str) and cmd["usage"]["model"].strip():
                    item["model"] = cmd["usage"]["model"].strip()[:120]
            self.items.append(item)
            self._event("added", id=item_id, title=title, source="agent", agent=agent, machine=machine,
                        workspace=cmd.get("workspace"), origin=origin, model=item.get("model"))
            self._archive_markdown(item_id, None, [cmd.get("details")], machine)
            self._changed()
            return item_id

        if op == "sub":
            parent_id = cmd.get("parent")
            parent = self.item(parent_id) if parent_id else None
            if parent is None:
                raise StoreError(f"unknown parent id '{parent_id or ''}' — subagents need the main agent's item id")
            sub_id = cmd.get("id") or make_id()
            if any(s["id"] == sub_id for s in parent["subagents"]):
                return self._update(sub_id, cmd)
            title = _clean(cmd.get("title"))
            if title is None:
                raise StoreError("title is required")
            now = now_iso()
            sub: dict[str, Any] = {"id": sub_id, "agent": agent, "machine": machine, "title": title[:200],
                                   "details": cmd.get("details") or "", "status": "active", "createdAt": now,
                                   "updatedAt": now, "notes": []}
            if cmd.get("agentLabel") is not None:
                sub["agentLabel"] = cmd["agentLabel"]
            model = _clean(cmd.get("model"))
            if model:
                sub["model"] = model[:120]
            sub["media"] = self._accept_media(cmd.get("media"), parent_id, sub_id, machine)
            if clamp_progress(cmd.get("progress")) is not None:
                sub["progress"] = clamp_progress(cmd.get("progress"))
            if isinstance(cmd.get("usage"), dict):
                sub["usage"] = cmd["usage"]
                if not sub.get("model") and isinstance(cmd["usage"].get("model"), str) and cmd["usage"]["model"].strip():
                    sub["model"] = cmd["usage"]["model"].strip()[:120]
            parent["subagents"].append(sub)
            del parent["subagents"][:-MAX_SUBAGENTS]
            parent["updatedAt"] = now
            self._event("sub_added", id=sub_id, parent=parent_id, title=title, agent=agent, machine=machine)
            self._archive_markdown(parent_id, sub_id, [cmd.get("details")], machine)
            self._changed()
            return sub_id

        if op == "update":
            if not cmd.get("id"):
                raise StoreError("id is required")
            return self._update(cmd["id"], cmd)

        # done
        item_id = cmd.get("id")
        if not item_id:
            raise StoreError("id is required")
        self._update(item_id, cmd, quiet=True)
        item = self.item(item_id)
        if item is not None:
            item["progress"] = 100
            if item["status"] == "active":
                by = normalize_agent(cmd["agent"]) if cmd.get("agent") is not None else (item.get("agent") or "agent")
                self._complete(item, by, cmd.get("summary"))
            elif cmd.get("summary") is not None:
                item["summary"] = cmd["summary"]
        else:
            found = self._sub(item_id)
            if found:
                parent, sub = found
                if sub["status"] == "active":
                    sub["status"] = "done"
                    sub["completedAt"] = now_iso()
                sub["progress"] = 100
                if cmd.get("summary") is not None:
                    sub["summary"] = cmd["summary"]
                self._event("sub_completed", id=item_id, parent=parent["id"], title=sub["title"])
        self._changed()
        return item_id

    def _sub(self, sub_id: str) -> tuple[dict, dict] | None:
        for item in self.items:
            for sub in item["subagents"]:
                if sub["id"] == sub_id:
                    return item, sub
        return None

    def _update(self, target_id: str, cmd: dict, quiet: bool = False) -> str:
        machine = _clean(cmd.get("machine")) or self.machine_name
        texts = [cmd.get("details"), cmd.get("note"), cmd.get("summary")]
        item = self.item(target_id)
        found = None if item is not None else self._sub(target_id)
        if item is None and found is None:
            raise StoreError(f"unknown id '{target_id}'")
        node = item if item is not None else found[1]
        parent_id = target_id if item is not None else found[0]["id"]
        sub_id = None if item is not None else target_id
        title = _clean(cmd.get("title"))
        if title:
            node["title"] = title[:200]
        if cmd.get("details") is not None:
            old = node["details"]
            node["details"] = old + "\n\n" + cmd["details"] if cmd.get("appendDetails") is True and old else cmd["details"]
        note = _clean(cmd.get("note"))
        if note:
            node["notes"].append({"at": now_iso(), "text": note})
            del node["notes"][:-MAX_NOTES]
        progress = clamp_progress(cmd.get("progress"))
        if progress is not None:
            node["progress"] = progress
        origin = _normalize_origin(cmd.get("origin"))
        if origin and not node.get("origin"):
            node["origin"] = origin
        model = _clean(cmd.get("model"))
        if model:
            node["model"] = model[:120]
        usage = cmd.get("usage")
        if isinstance(usage, dict):
            node["usage"] = {**usage, "updatedAt": usage.get("updatedAt") or now_iso()}
            if not node.get("model") and isinstance(usage.get("model"), str) and usage["model"].strip():
                node["model"] = usage["model"].strip()[:120]
        node["media"] += self._accept_media(cmd.get("media"), parent_id, sub_id, machine)
        del node["media"][:-MAX_MEDIA]
        node["updatedAt"] = now_iso()
        self._archive_markdown(parent_id, sub_id, texts, machine)
        if item is not None and not quiet:
            self._event("updated", id=target_id, note=(cmd.get("note") or None) and cmd["note"][:300])
        self._changed()
        return target_id

    # Media

    def _accept_media(self, inputs: list | None, item_id: str, sub_id: str | None, machine: str) -> list[dict]:
        """Keeps refs the CLI already archived (storing inline copies relayed from other machines);
        archives the rest in the background."""
        ready: list[dict] = []
        remote = machine != self.machine_name
        for entry in (inputs or [])[:MAX_MEDIA]:
            ref = entry.get("archived")
            if ref is not None:
                ref = dict(ref)
                if ref.get("caption") is None and entry.get("caption") is not None:
                    ref["caption"] = entry["caption"]
                if not valid_id(ref["id"]):
                    ref["id"] = make_id()
                if remote:
                    ref["machine"] = machine
                    # The satellite's archive path means nothing here: keep only what came inline.
                    if entry.get("inlineArchive"):
                        ref = media_mod.store_inline(entry["inlineArchive"], ref, item_id, self.paths)
                    else:
                        ref.pop("archivePath", None)
                elif ref.get("archivePath") and not media_mod.is_safe_archive_path(ref["archivePath"]):
                    ref.pop("archivePath", None)
                ready.append(ref)
            elif remote:
                # A file on another machine that wasn't archived there: keep the record only.
                rec = {"id": make_id(), "kind": media_mod.kind_for(entry["path"]), "originalPath": entry["path"],
                       "addedAt": now_iso(), "machine": machine}
                if entry.get("caption") is not None:
                    rec["caption"] = entry["caption"]
                ready.append(rec)
            else:
                self._archive_later(normalize_path(entry["path"], cwd=os.path.abspath(os.sep)),
                                    entry.get("caption"), item_id, sub_id)
        return ready

    def _all_media(self, item_id: str, sub_id: str | None) -> list[dict]:
        item = self.item(item_id)
        if item is None:
            return []
        if sub_id:
            sub = next((s for s in item["subagents"] if s["id"] == sub_id), None)
            return sub["media"] if sub else []
        return item["media"]

    def _archive_markdown(self, item_id: str, sub_id: str | None, texts: list, machine: str) -> None:
        if machine != self.machine_name:
            return  # another machine's paths aren't on this disk
        known = {m["originalPath"] for m in self._all_media(item_id, sub_id)}
        for text in texts:
            for path in markdown_paths(text):
                if path not in known:
                    known.add(path)
                    self._archive_later(path, None, item_id, sub_id)

    def _archive_later(self, path: str, caption: str | None, item_id: str, sub_id: str | None) -> None:
        def work() -> None:
            if not os.path.isfile(path):
                return
            ref = media_mod.archive(path, caption, item_id, self.paths)
            with self.lock:
                target = self._all_media(item_id, sub_id) if self.item(item_id) else None
                if target is None or any(m["originalPath"] == path for m in target):
                    return
                target.append(ref)
                del target[:-MAX_MEDIA]
                self._changed()

        try:
            self._archiver.submit(work)
        except RuntimeError:  # shutting down
            pass

    def wait_for_archives(self, timeout: float = 10) -> None:
        """Tests: wait until queued archive jobs have run."""
        done = threading.Event()
        try:
            self._archiver.submit(done.set)
            self._archiver.submit(lambda: None)
        except RuntimeError:
            return
        done.wait(timeout)
        time.sleep(0.05)

    # Retention

    def apply_retention(self, now: datetime | None = None) -> None:
        """Drops finished items past history_retention_days and media archives past
        media_retention_days. Runs at launch and daily."""
        now = now or datetime.now(timezone.utc)
        removed_items = 0
        deleted: list[str] = []
        with self.lock:
            days = self.cfg.history_retention_days
            if days > 0:
                cutoff = now - timedelta(days=days)
                kept = []
                for item in self.items:
                    done_at = parse_date(finished_at(item))
                    if item["status"] == "done" and done_at and done_at < cutoff:
                        removed_items += 1
                    else:
                        kept.append(item)
                self.items = kept
            if self.cfg.media_retention_days > 0:
                cutoff_ts = (now - timedelta(days=self.cfg.media_retention_days)).timestamp()
                root = self.paths.root.resolve()
                for p in prune_files(self.paths.media_dir, cutoff_ts, recursive=True):
                    try:
                        deleted.append(p.resolve().relative_to(root).as_posix())
                    except ValueError:
                        deleted.append(p.as_posix())
                gone = set(deleted)
                for item in self.items:
                    for refs in [item["media"]] + [s["media"] for s in item["subagents"]]:
                        for ref in refs:
                            if ref.get("archivePath") in gone:
                                ref.pop("archivePath", None)
            stale = now.timestamp() - max(self.cfg.log_retention_days, 1) * 86_400
            failed = prune_files(self.paths.failed_inbox_dir, stale)
            if removed_items or deleted:
                self.version += 1
                self.save_now()
        if removed_items or deleted or failed:
            self.log.info("retention", items_removed=removed_items, media_removed=len(deleted),
                          failed_inbox_removed=len(failed), history_days=self.cfg.history_retention_days,
                          media_days=self.cfg.media_retention_days)
