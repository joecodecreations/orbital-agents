"""The tracker for the apps: a read view of what every agent is working on, and the relay to it.

The Agent Satellite Tracker app is the only writer of data/tracker/items.json. The hub reads
that file, serves the screenshots and videos agents attached (signed URLs, so a video player
needs no header), and pushes changes over /v1/live. Two things go through the tracker app's
loopback API (127.0.0.1:<tracker.port>, or the port in run/tracker.json):
  - the shared screenshot-evidence setting (POST /v1/settings), written to settings.json
    directly when the app is closed. The apps and every satellite's tracker read and set it
    through /v1/tracker/settings; `by` says who changed it (app, tracker, cli), and
  - orbital-track commands relayed from other machines (POST /v1/command), forwarded byte for
    byte, or queued in data/tracker/inbox/ (the tracker's own file naming) when the app is closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

from orbital.core import get_logger
from orbital.hub.web import body_of, fail

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
MEDIA_TTL = 6 * 3600
# Screenshot evidence agents attach for UI / website / game work.
EVIDENCE_LEVELS = ("off", "min1", "all")
EVIDENCE_ALIASES = {
    "off": "off", "none": "off", "0": "off", "no": "off", "false": "off", "normal": "off",
    "min1": "min1", "min": "min1", "1": "min1", "one": "min1", "minimum": "min1", "atleast1": "min1", "atleastone": "min1",
    "all": "all", "every": "all", "everything": "all", "full": "all", "max": "all",
}
EVIDENCE_LABELS = {"off": "Off", "min1": "Min 1", "all": "All"}
MAX_COMMAND_BYTES = 16 * 1024 * 1024  # the tracker app's own body limit
INLINE_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def evidence_level(raw: Any) -> str | None:
    """'Min 1', 'min1', '1', 'ALL' → the stored level; anything else → None."""
    if not isinstance(raw, str):
        return None
    return EVIDENCE_ALIASES.get("".join(ch for ch in raw.lower() if ch.isalnum()))


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_window(item: dict, scope: str, now: datetime, keys: tuple[str, ...]) -> bool:
    """True when one of `keys` falls in the today / week / month window. `all` is always in."""
    if scope == "all":
        return True
    created = None
    for key in keys:
        created = _parse(item.get(key))
        if created is not None:
            break
    if created is None:
        return False
    local = created.astimezone()
    if scope == "today":
        return local.date() == now.date()
    if scope == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return local >= start
    if scope == "month":
        return (local.year, local.month) == (now.year, now.month)
    return True


def _agent_name(raw: str | None) -> str:
    s = (raw or "").lower()
    for key, label in (("claude", "Claude"), ("chatgpt", "ChatGPT"), ("codex", "ChatGPT"), ("grok", "Grok"), ("gemini", "Gemini")):
        if key in s:
            return label
    return raw.title() if raw else "You"


ORIGIN_LABELS = {"ios": "iOS app", "mac": "Mac app", "direct": "Direct AI chat", "agent": "Agent"}


def _origin_of(item: dict) -> str | None:
    raw = item.get("origin")
    if isinstance(raw, str) and raw in ORIGIN_LABELS:
        return raw
    return None


def _model_of(item: dict) -> str | None:
    model = item.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()[:120]
    usage = item.get("usage")
    if isinstance(usage, dict):
        m = usage.get("model")
        if isinstance(m, str) and m.strip():
            return m.strip()[:120]
    return None


class TrackerFeed:
    """items.json with an mtime cache, signed media URLs, and a change watcher."""

    def __init__(self, secret: str, root: Path, port: int = 0, run_dir: Path | None = None) -> None:
        self.root = root
        self.port = port
        self.run_dir = run_dir
        self._secret = hashlib.sha256(("tracker-media:" + secret).encode()).digest()
        self._mtime = -1.0
        self._items: list[dict] = []
        self._settings_mtime = -1.0
        self._settings: dict = {}

    # Reading

    @property
    def items_file(self) -> Path:
        return self.root / "items.json"

    def items(self) -> list[dict]:
        try:
            mtime = self.items_file.stat().st_mtime
        except OSError:
            return []
        if mtime != self._mtime:
            try:
                data = json.loads(self.items_file.read_text(encoding="utf-8"))
                self._items = data if isinstance(data, list) else []
                self._mtime = mtime
            except (OSError, json.JSONDecodeError):
                # Mid-write or corrupt: keep the last good copy.
                pass
        return self._items

    def get(self, item_id: str) -> dict | None:
        return next((row for row in self.items() if row.get("id") == item_id), None)

    def query(self, scope: str = "all", who: str = "all", limit: int = 50, offset: int = 0, q: str = "", status: str = "any") -> dict:
        """Items for one range.

        `status=any` (the default) keeps working items in every range, newest update first.
        `status=done` is the history pane: finished items only, newest completion first,
        dated by completedAt, then updatedAt. `status=active` is the live pane.
        `q` keeps items whose text holds every word of it.
        """
        now = datetime.now(timezone.utc).astimezone()
        words = q.lower().split()
        rows = []
        for item in self.items():
            if who == "ai" and item.get("source") != "agent":
                continue
            if who == "mine" and item.get("source") == "agent":
                continue
            if words:
                text = _search_text(item)
                if not all(word in text for word in words):
                    continue
            active = item.get("status") == "active"
            if status == "done":
                if active or scope == "active":
                    continue
                if not _in_window(item, scope, now, ("completedAt", "updatedAt", "createdAt")):
                    continue
            elif status == "active" or scope == "active":
                if not active:
                    continue
            elif scope != "all" and not active:
                if not _in_window(item, scope, now, ("updatedAt", "createdAt")):
                    continue
            rows.append(item)
        if status == "done":
            rows.sort(key=lambda r: r.get("completedAt") or r.get("updatedAt") or r.get("createdAt") or "", reverse=True)
        else:
            rows.sort(key=lambda r: (r.get("status") == "active", r.get("updatedAt") or r.get("createdAt") or ""), reverse=True)
        total = len(rows)
        page = rows[offset : offset + max(1, min(limit, 200))]
        return {
            "items": [self.summary(row) for row in page],
            "total": total,
            "done": sum(1 for row in rows if row.get("status") != "active"),
            "active": sum(1 for row in self.items() if row.get("status") == "active"),
        }

    def summary(self, item: dict) -> dict:
        media = item.get("media") or []
        cover = next((m for m in media if m.get("kind") in ("image", "video")), None)
        notes = item.get("notes") or []
        subs = item.get("subagents") or []
        origin = _origin_of(item)
        model = _model_of(item)
        return {
            "id": item.get("id"),
            "title": item.get("title") or "Untitled",
            "status": item.get("status") or "active",
            "source": item.get("source") or "manual",
            "origin": origin,
            "origin_label": ORIGIN_LABELS.get(origin) if origin else None,
            "model": model,
            "agent": item.get("agent"),
            "agent_name": _agent_name(item.get("agent")) if item.get("source") == "agent" else "You",
            "agent_label": item.get("agentLabel"),
            "machine": item.get("machine"),
            "workspace": item.get("workspace"),
            "project": Path(item["workspace"]).name if item.get("workspace") else None,
            "color_index": item.get("colorIndex") or 0,
            "created_at": item.get("createdAt"),
            "updated_at": item.get("updatedAt"),
            "completed_at": item.get("completedAt"),
            "completed_by": item.get("completedBy"),
            "last_note": notes[-1]["text"] if notes else None,
            "summary": _first_line(item.get("summary")),
            "media_count": len(media),
            "video_count": sum(1 for m in media if m.get("kind") == "video"),
            "cover": self._media_urls(item["id"], cover, None) if cover else None,
            "subagents": {"total": len(subs), "active": sum(1 for s in subs if s.get("status") == "active")},
            "processing": bool(item.get("processing")),
            "progress": item.get("progress"),
            "shown_progress": shown_progress(item),
            "usage": item.get("usage") if isinstance(item.get("usage"), dict) else None,
        }

    def detail(self, item: dict) -> dict:
        out = self.summary(item)
        all_media = list(item.get("media") or [])
        out.update(
            {
                "details": self._rewrite_markdown(item["id"], item.get("details") or "", all_media),
                "transcript": item.get("transcript"),
                "summary": self._rewrite_markdown(item["id"], item.get("summary") or "", all_media) or None,
                "notes": [{"at": n.get("at"), "text": n.get("text") or ""} for n in item.get("notes") or []],
                "media": [self._media_urls(item["id"], m, None) for m in item.get("media") or [] if m.get("kind") in ("image", "video")],
                "completed_by": item.get("completedBy"),
                "subagents": [
                    {
                        "id": sub.get("id"),
                        "title": sub.get("title") or "Subtask",
                        "agent_name": _agent_name(sub.get("agent")),
                        "status": sub.get("status") or "active",
                        "details": self._rewrite_markdown(item["id"], sub.get("details") or "", sub.get("media") or []),
                        "summary": self._rewrite_markdown(item["id"], sub.get("summary") or "", sub.get("media") or []) or None,
                        "created_at": sub.get("createdAt"),
                        "completed_at": sub.get("completedAt"),
                        "notes": [{"at": n.get("at"), "text": n.get("text") or ""} for n in sub.get("notes") or []],
                        "media": [self._media_urls(item["id"], m, sub.get("id")) for m in sub.get("media") or [] if m.get("kind") in ("image", "video")],
                        "progress": sub.get("progress"),
                        "usage": sub.get("usage") if isinstance(sub.get("usage"), dict) else None,
                        "model": _model_of(sub),
                    }
                    for sub in item.get("subagents") or []
                ],
            }
        )
        return out

    # Settings (shared with the tracker app and orbital-track)

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    def settings(self) -> dict:
        """The apps' view of settings.json. A missing or unreadable file means evidence off."""
        try:
            mtime = self.settings_file.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._settings_mtime:
            raw: dict = {}
            if mtime:
                try:
                    data = json.loads(self.settings_file.read_text(encoding="utf-8"))
                    raw = data if isinstance(data, dict) else {}
                except (OSError, json.JSONDecodeError):
                    return self._settings or self._public_settings({})
            self._settings = self._public_settings(raw)
            self._settings_mtime = mtime
        return self._settings

    @staticmethod
    def _public_settings(raw: dict) -> dict:
        level = evidence_level(raw.get("evidence")) or "off"
        return {
            "evidence": level,
            "label": EVIDENCE_LABELS[level],
            "updated_at": raw.get("updatedAt"),
            "updated_by": raw.get("updatedBy"),
            # The tracker app reads these names to keep whichever copy changed last.
            "updatedAt": raw.get("updatedAt"),
            "updatedBy": raw.get("updatedBy"),
        }

    def _app_port(self) -> int | None:
        """The tracker app's loopback port: run/tracker.json when it wrote one, else tracker.port."""
        port = 0
        if self.run_dir is not None:
            try:
                info = json.loads((self.run_dir / "tracker.json").read_text(encoding="utf-8"))
                port = int(info.get("port") or 0)
            except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
                port = 0
        port = port or int(self.port or 0)
        return port if 0 < port < 65536 else None

    def command(self, body: bytes) -> dict:
        """Hand one orbital-track command (the JSON body exactly as it came) to the tracker app.

        When the app is closed it is queued in inbox/, which the app applies oldest first when it
        starts. Same rule as the CLI: tracking never blocks work, so a closed app is not an error.
        """
        port = self._app_port()
        if port is not None:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/command", data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return {"ok": True, "delivered": "app", "status": res.status, "result": _json_or_text(res.read())}
            except urllib.error.HTTPError as exc:
                # The tracker refused it (a bad id, a bad field): retrying can't help, so say so.
                answer = _json_or_text(exc.read())
                error = answer.get("error") if isinstance(answer, dict) else None
                return {"ok": False, "delivered": "app", "status": exc.code, "error": str(error or answer)[:300], "result": answer}
            except (OSError, urllib.error.URLError):
                pass  # the app is closed: queue it
        name = inbox_name()
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        tmp = inbox / f".{name}.tmp"
        tmp.write_bytes(body)
        os.replace(tmp, inbox / name)
        return {"ok": True, "delivered": "inbox", "file": name}

    def set_evidence(self, level: str, by: str = "app") -> dict:
        """Saves a new evidence level and returns the settings. Raises ValueError for an unknown level."""
        stored = evidence_level(level)
        if stored is None:
            raise ValueError("evidence is off, min1 or all")
        port = self._app_port()
        if port is not None:
            body = json.dumps({"evidence": stored, "by": by}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/settings", data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=2) as res:
                    if 200 <= res.status < 300:
                        return self.settings()
            except (OSError, urllib.error.URLError):
                pass  # app gone or older than this setting: write the file; it reloads it
        payload = {
            "evidence": stored,
            "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updatedBy": by,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.settings_file)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return self.settings()

    # Media

    def _find_media(self, item: dict, media_id: str) -> dict | None:
        for ref in item.get("media") or []:
            if ref.get("id") == media_id:
                return ref
        for sub in item.get("subagents") or []:
            for ref in sub.get("media") or []:
                if ref.get("id") == media_id:
                    return ref
        return None

    def _original(self, ref: dict) -> Path | None:
        raw = ref.get("originalPath")
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
            return None
        return path if path.is_file() else None

    def _archive(self, ref: dict) -> Path | None:
        rel = ref.get("archivePath")
        if not rel:
            return None
        path = (self.root / rel).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def media_file(self, item_id: str, media_id: str, variant: str) -> tuple[Path, str] | None:
        item = self.get(item_id)
        if item is None:
            return None
        ref = self._find_media(item, media_id)
        if ref is None or ref.get("kind") not in ("image", "video"):
            return None
        path = None
        if variant in ("full", "stream"):
            path = self._original(ref)
            if path is None and ref.get("kind") == "image":
                path = self._archive(ref)
        else:
            path = self._archive(ref) or (self._original(ref) if ref.get("kind") == "image" else None)
        if path is None:
            return None
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".mov":
            mime = "video/quicktime"
        return path, mime

    def sign(self, item_id: str, media_id: str, variant: str, exp: int) -> str:
        msg = f"{item_id}:{media_id}:{variant}:{exp}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()[:32]

    def verify(self, item_id: str, media_id: str, variant: str, exp: str | None, sig: str | None) -> bool:
        if not exp or not sig:
            return False
        try:
            exp_i = int(exp)
        except ValueError:
            return False
        if exp_i < time.time():
            return False
        return hmac.compare_digest(self.sign(item_id, media_id, variant, exp_i), sig)

    def url(self, item_id: str, media_id: str, variant: str) -> str:
        # Rounded to 10 minutes so the same media keeps one URL for a while and the app's cache hits.
        exp = (int(time.time()) // 600) * 600 + MEDIA_TTL
        sig = self.sign(item_id, media_id, variant, exp)
        return f"/v1/tracker/media/{quote(item_id)}/{quote(media_id)}?variant={variant}&exp={exp}&sig={sig}"

    def _media_urls(self, item_id: str, ref: dict, sub_id: str | None) -> dict:
        kind = ref.get("kind")
        original = self._original(ref)
        archive = self._archive(ref)
        out: dict[str, Any] = {
            "id": ref.get("id"),
            "kind": kind,
            "caption": ref.get("caption"),
            "file_name": Path(ref.get("originalPath") or "").name,
            "added_at": ref.get("addedAt"),
            "bytes": ref.get("originalBytes"),
            "duration": ref.get("durationSeconds"),
            "sub_id": sub_id,
            "archived_only": original is None,
            "thumb_url": self.url(item_id, ref["id"], "thumb") if (archive or (kind == "image" and original)) else None,
            "full_url": None,
            "stream_url": None,
        }
        if kind == "image" and (original or archive):
            out["full_url"] = self.url(item_id, ref["id"], "full")
        if kind == "video" and original:
            out["stream_url"] = self.url(item_id, ref["id"], "stream")
        return out

    def _rewrite_markdown(self, item_id: str, text: str, media: list[dict]) -> str:
        """Local image paths in markdown point at files the app cannot open. Swap in served URLs."""
        if not text:
            return text
        by_path = {}
        for ref in media:
            raw = ref.get("originalPath")
            if raw:
                by_path[str(Path(raw).expanduser())] = ref

        def swap(match: re.Match) -> str:
            alt, raw = match.group(1), match.group(2)
            if raw.startswith(("http://", "https://")):
                return match.group(0)
            path = raw[7:] if raw.startswith("file://") else raw
            ref = by_path.get(str(Path(path).expanduser()))
            if ref is None or ref.get("kind") != "image":
                return f"*{alt or 'image'}*" if alt else ""
            return f"![{alt}]({self.url(item_id, ref['id'], 'full')})"

        return INLINE_IMAGE.sub(swap, text)

    # Watching

    async def watch(
        self,
        on_change: Callable[[list[dict], list[str]], Awaitable[None]],
        interval: float = 1.5,
        on_settings: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        seen: dict[str, str] = {row.get("id"): row.get("updatedAt") or "" for row in self.items()}
        seen_settings = self.settings()
        while True:
            await asyncio.sleep(interval)
            try:
                if on_settings is not None:
                    current_settings = self.settings()
                    if current_settings != seen_settings:
                        seen_settings = current_settings
                        await on_settings(current_settings)
                rows = self.items()
                current = {row.get("id"): row.get("updatedAt") or "" for row in rows}
                changed = [row for row in rows if seen.get(row.get("id")) != current.get(row.get("id"))]
                removed = [key for key in seen if key not in current]
                seen = current
                if changed or removed:
                    await on_change(changed, removed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the watcher die
                get_logger("hub").warning("tracker watch error", extra={"fields": {"error": str(exc)[:200]}})


def _search_text(item: dict) -> str:
    """Everything a search can match on one item: title, agent, project, details, notes, result, subagents."""
    parts = [
        item.get("title"),
        item.get("agent"),
        item.get("agentLabel"),
        Path(item["workspace"]).name if item.get("workspace") else None,
        item.get("details"),
        item.get("transcript"),
        item.get("summary"),
    ]
    parts += [note.get("text") for note in item.get("notes") or []]
    for sub in item.get("subagents") or []:
        parts += [sub.get("title"), sub.get("summary")]
    return "\n".join(part for part in parts if isinstance(part, str)).lower()


CHECKBOX = re.compile(r"^[ \t]*[-*+] +\[([ xX])\]")


def _percent(value) -> int | None:
    try:
        return min(100, max(0, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def shown_progress(item: dict) -> dict:
    """The progress bar's figure, the same rule as the Mac tracker's `ShownProgress`: done is 100;
    else the agent's own %; else checked `- [x]` boxes in the details; else the subagents' average.
    Estimates stop at 99 until the work is done. Nothing to go on yet: `{"percent": 0, "source": "none"}`,
    so the app knows not to look further."""
    if item.get("status") == "done":
        return {"percent": 100, "source": "done"}
    reported = _percent(item.get("progress"))
    if reported is not None:
        return {"percent": reported, "source": "reported"}
    boxes = [m.group(1) for line in str(item.get("details") or "").splitlines() if (m := CHECKBOX.match(line))]
    if boxes:
        checked = sum(1 for b in boxes if b in "xX")
        return {"percent": min(99, int(checked / len(boxes) * 100 + 0.5)), "source": "checklist"}
    subs = [s for s in item.get("subagents") or [] if isinstance(s, dict)]
    if subs:
        total = sum(100 if s.get("status") == "done" else (_percent(s.get("progress")) or 0) for s in subs)
        return {"percent": min(99, int(total / len(subs) + 0.5)), "source": "subagents"}
    return {"percent": 0, "source": "none"}


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith("|") and not line.startswith("!["):
            return line[:240]
    return None


_inbox_last = 0
_inbox_seq = 0


def inbox_name() -> str:
    """`<unix microseconds, 16 digits>-<sequence, 4>-<random>.json`, the tracker's own inbox naming,
    so files sort in the order they were written even within one millisecond."""
    global _inbox_last, _inbox_seq
    micros = time.time_ns() // 1000
    if micros <= _inbox_last:
        micros = _inbox_last + 1
    _inbox_last = micros
    _inbox_seq = (_inbox_seq + 1) % 10_000
    return f"{micros:016d}-{_inbox_seq:04d}-{secrets.token_hex(2)}.json"


def _json_or_text(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:500]


WRITERS = {"tracker", "cli", "app"}


def first_sentence(text: str | None, limit: int = 240) -> str:
    line = _first_line(text) or ""
    line = re.sub(r"[*_`]+", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    match = re.match(r"(.+?[.!?])(\s|$)", line)
    sentence = match.group(1) if match else line
    return sentence[:limit].strip()


class TrackerEvents:
    """Tracker changes → the live socket, and an alert when an agent finishes something.

    Items the Desk started are left to the desk, which raises its own alert.
    """

    def __init__(self, hub: Any) -> None:
        self.hub = hub
        self._status = {row.get("id"): row.get("status") for row in hub.tracker.items()}

    async def changed(self, changed: list[dict], removed: list[str]) -> None:
        # `finished` lets the apps say so out loud; an item the user closed himself is not news.
        finished: list[dict] = []
        for row in changed:
            before = self._status.get(row.get("id"))
            self._status[row.get("id")] = row.get("status")
            if row.get("status") == "done" and before == "active" and row.get("source") == "agent":
                finished.append(row)
        await self.hub.live.broadcast(
            {
                "type": "tracker",
                "changed": [self.hub.tracker.summary(row) for row in changed],
                "removed": removed,
                "finished": [row["id"] for row in finished if row.get("completedBy") != "user"],
            }
        )
        desk_items = self.hub.desk.tracker_ids() if self.hub.desk is not None else set()
        for row in finished:
            if row.get("id") in desk_items or row.get("completedBy") == "user":
                continue
            who = _agent_name(row.get("agent"))
            await self.hub.raise_alerts([{
                "schema": "orbital.alert.v1",
                "id": f"tracker-{row['id']}-done",
                "kind": "event",
                "title": f"{who} finished: {row.get('title') or 'a task'}",
                "subtitle": Path(row["workspace"]).name if row.get("workspace") else None,
                "body": first_sentence(row.get("summary"), 300) or "Finished.",
                "thread_id": "tracker",
                "interruption_level": "passive",
                "launch_url": f"orbital://tracker?id={row['id']}",
                "user_info": {"source": "tracker", "tracker_id": row["id"]},
            }])
        for ident in removed:
            self._status.pop(ident, None)

    async def settings_changed(self, current: dict) -> None:
        # Changed on the Mac (menu or orbital-track) or from another device: every open app follows.
        await self.hub.live.broadcast({"type": "tracker_settings", "settings": current})


def add_routes(app, hub) -> None:
    feed = hub.tracker

    @app.get("/v1/tracker/items")
    async def tracker_items(
        request: Request,
        scope: str = "all",
        who: str = "all",
        limit: int = 50,
        offset: int = 0,
        q: str = "",
        status: str = "any",
    ) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        if scope not in {"active", "today", "week", "month", "all"} or who not in {"all", "ai", "mine"} or status not in {"any", "active", "done"}:
            return fail("scope is active|today|week|month|all, who is all|ai|mine, status is any|active|done", 400)
        return JSONResponse(feed.query(scope, who, limit, max(0, offset), q[:200], status))

    @app.get("/v1/tracker/items/{item_id}")
    async def tracker_item(item_id: str, request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        item = feed.get(item_id)
        if item is None:
            return fail("no such item", 404)
        detail = feed.detail(item)
        desk_task = hub.desk.task_for_tracker(item_id) if hub.desk is not None else None
        detail["desk_task"] = desk_task
        # Desk tasks store the app platform; use it when the tracker item predates origin.
        if not detail.get("origin") and isinstance(desk_task, dict):
            platform = desk_task.get("client_platform")
            if platform in ORIGIN_LABELS:
                detail["origin"] = platform
                detail["origin_label"] = ORIGIN_LABELS[platform]
        return JSONResponse(detail)

    def tracker_caller(request: Request) -> str | None:
        """"app" for an app session or the automation token, "satellite" for a satellite's hub_token."""
        token = hub.token(request)
        if hub.auth.is_user(token):
            return "app"
        if hub.satellites.by_token(token) is not None:
            return "satellite"
        return None

    @app.get("/v1/tracker/settings")
    async def tracker_settings(request: Request) -> JSONResponse:
        if tracker_caller(request) is None:
            return fail("unauthorized", 401, code="unauthorized")
        return JSONResponse(feed.settings())

    @app.post("/v1/tracker/settings")
    async def tracker_settings_set(request: Request) -> JSONResponse:
        """{"evidence": "off|min1|all", "by"?: "tracker|cli|app"}: the screenshot level every agent follows."""
        caller = tracker_caller(request)
        if caller is None:
            return fail("unauthorized", 401, code="unauthorized")
        body = await body_of(request)
        level = body.get("evidence")
        by = str(body.get("by") or "").lower()
        if by not in WRITERS:
            by = "tracker" if caller == "satellite" else "app"
        try:
            current = await asyncio.to_thread(feed.set_evidence, level if isinstance(level, str) else "", by)
        except ValueError as exc:
            return fail(str(exc), 400)
        except OSError as exc:
            return fail(f"couldn't save the tracker settings: {exc}", 500)
        await hub.tracker_events.settings_changed(current)
        return JSONResponse(current)

    @app.get("/v1/tracker/media/{item_id}/{media_id}")
    async def tracker_media(item_id: str, media_id: str, request: Request, variant: str = "thumb"):
        if variant not in {"thumb", "full", "stream"}:
            return fail("variant is thumb|full|stream", 400)
        q = request.query_params
        if not hub.auth.authorized(request) and not feed.verify(item_id, media_id, variant, q.get("exp"), q.get("sig")):
            return fail("unauthorized", 401, code="unauthorized")
        found = feed.media_file(item_id, media_id, variant)
        if found is None:
            return fail("media is gone", 404)
        path, mime = found
        # FileResponse answers Range requests, so a player can seek a long recording over Tailscale.
        return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/v1/tracker/command")
    async def tracker_command(request: Request) -> JSONResponse:
        """One orbital-track command from a machine without a tracker (satellite hub_token) or a script.

        The body goes to the tracker exactly as it came: `machine`, and media[].inlineArchive (base64
        JPEG) from satellites, pass through untouched. A satellite's command without a `machine` is
        stamped with that satellite's machine name, so the item isn't shown as the hub's.
        """
        caller = tracker_caller(request)
        if caller is None:
            return fail("unauthorized", 401, code="unauthorized")
        raw = await request.body()
        if len(raw) > MAX_COMMAND_BYTES:
            return fail("command is larger than 16 MB", 413)
        try:
            parsed = json.loads(raw) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = None
        if not isinstance(parsed, dict) or not parsed:
            return fail("send the orbital-track command as a JSON object", 400)
        if caller == "satellite" and not str(parsed.get("machine") or "").strip():
            satellite = hub.satellites.by_token(hub.token(request)) or {}
            name = satellite.get("machine") or satellite.get("name") or satellite.get("id")
            if name:
                parsed["machine"] = str(name)
                raw = json.dumps(parsed).encode()
        result = await asyncio.to_thread(feed.command, raw)
        status = 200 if result.get("ok") else int(result.get("status") or 502)
        return JSONResponse(result, status_code=status)
