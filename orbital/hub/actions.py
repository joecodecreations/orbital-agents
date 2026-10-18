"""App taps that write back to a feed. One queue file per section. Schema: orbital.action.v1.

The hub never touches the owner's source. It stores the action, patches the section so
every app redraws at once (the tapped row or button shows as pending), and leaves the rest
to the owning feed: it reads GET /v1/sections/{id}/actions, applies each one to its own
source, answers with POST .../actions/results, and pushes the rebuilt section.

Any dotted type works (`server.restart`, `garden.water`); its value is stored as sent.
A few types have a built-in optimistic patch: task.set_done, task.add, insight.*, and any
action posted from a button (the button turns pending).
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from orbital.hub.ops import OpsError

ACTION_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RESULT_STATUS = {"applied", "failed"}
KEEP_RESOLVED = 300
MAX_VALUE = 8000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: object, label: str, *, limit: int, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise OpsError(f"{label} is required")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise OpsError(f"{label} must be a string of at most {limit} characters")
    text = value.strip()
    if required and not text:
        raise OpsError(f"{label} is required")
    return text or None


def _iso_day(value: object, label: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise OpsError(f"{label} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise OpsError(f"{label} must be YYYY-MM-DD") from exc
    return value


INSIGHT_TYPES = {"insight.email.approve", "insight.agent.approve", "insight.snooze", "insight.dismiss"}
INSIGHT_ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def _insight_value(kind: str, item_id: str | None, value: dict) -> dict:
    """insight.* taps on an insight_cards card. item_id is the card id."""
    if kind not in INSIGHT_TYPES:
        raise OpsError(f"unknown insight action {kind}; use one of {', '.join(sorted(INSIGHT_TYPES))}")
    if not item_id:
        raise OpsError(f"{kind} needs item_id (the card id)")
    if kind in ("insight.email.approve", "insight.agent.approve"):
        action_id = value.get("action_id")
        if not isinstance(action_id, str) or not INSIGHT_ACTION_ID.match(action_id):
            raise OpsError(f"{kind} value.action_id must be the card action's id")
        clean: dict = {"action_id": action_id}
        digest = _text(value.get("digest"), f"{kind} value.digest", limit=80)
        if digest:
            clean["digest"] = digest
        return clean
    if kind == "insight.snooze":
        days = value.get("days")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30:
            raise OpsError("insight.snooze value.days must be a whole number from 1 to 30")
        return {"days": days}
    return {}


def validate_action(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise OpsError("action must be an object")
    if raw.get("schema") not in (None, "orbital.action.v1"):
        raise OpsError("action schema must be orbital.action.v1")
    kind = raw.get("type")
    if not isinstance(kind, str) or not ACTION_TYPE.match(kind):
        raise OpsError("action type must be dotted lowercase, for example task.set_done")
    value = raw.get("value", {})
    if not isinstance(value, dict):
        raise OpsError("action value must be an object")
    if len(json.dumps(value)) > MAX_VALUE:
        raise OpsError("action value is larger than 8 KB")
    item_id = raw.get("item_id")
    if item_id is not None and (not isinstance(item_id, str) or not ITEM_ID.match(item_id)):
        raise OpsError("action item_id must be 1–128 letters, numbers, or . _ : -")
    for key in ("block_id", "node_id", "tab_id"):
        ident = raw.get(key)
        if ident is not None and (not isinstance(ident, str) or not SLUG.match(ident)):
            raise OpsError(f"action {key} is not a valid id")

    clean = dict(value)
    if kind == "task.set_done":
        if not item_id:
            raise OpsError(f"{kind} needs item_id")
        if not isinstance(value.get("done"), bool):
            raise OpsError(f"{kind} value.done must be true or false")
        clean = {"done": value["done"]}
    elif kind == "task.add":
        clean = {"title": _text(value.get("title"), "task.add value.title", limit=200, required=True)}
        for key, limit in (("group", 64), ("note", 2000)):
            text = _text(value.get(key), f"task.add value.{key}", limit=limit)
            if text is not None:
                clean[key] = text
        due = _iso_day(value.get("due"), "task.add value.due")
        if due:
            clean["due"] = due
        remind = value.get("remind_days")
        if remind is not None:
            if not isinstance(remind, list) or len(remind) > 12 or any(
                isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 120 for day in remind
            ):
                raise OpsError("task.add value.remind_days must be up to 12 whole numbers from 0 to 120")
            clean["remind_days"] = sorted(set(remind), reverse=True)
    elif kind.startswith("insight."):
        clean = _insight_value(kind, item_id, value)

    source = raw.get("source")
    return {
        "schema": "orbital.action.v1",
        "type": kind,
        "item_id": item_id,
        "block_id": raw.get("block_id"),
        "node_id": raw.get("node_id"),
        "tab_id": raw.get("tab_id"),
        "value": clean,
        "source": source if source in ("tap", "notification", "form", "button") else None,
    }


def blocks_of(doc: dict):
    """Every block list in a section, with the node and tab it sits on."""
    yield None, None, doc.get("blocks") or []
    for tab in doc.get("tabs") or []:
        yield None, tab.get("id"), tab.get("blocks") or []
    for node in doc.get("nodes") or []:
        yield node.get("id"), None, node.get("blocks") or []
        for tab in node.get("tabs") or []:
            yield node.get("id"), tab.get("id"), tab.get("blocks") or []


def optimistic_patch(doc: dict, action: dict) -> tuple[dict, bool]:
    """Return (patched copy, changed). The owner's next push replaces this."""
    working = copy.deepcopy(doc)
    changed = False
    kind = action["type"]
    stamp = action["created_at"]
    if kind == "task.set_done":
        on = action["value"]["done"]
        for _node, _tab, blocks in blocks_of(working):
            for block in blocks:
                if block.get("type") != "tasks":
                    continue
                for item in block.get("items") or []:
                    if isinstance(item, dict) and item.get("id") == action["item_id"]:
                        item["done"] = on
                        item["done_at"] = stamp if on else None
                        item["pending"] = True
                        changed = True
    elif kind == "task.add" and action.get("block_id"):
        for node_id, tab_id, blocks in blocks_of(working):
            if action.get("node_id") and node_id != action["node_id"]:
                continue
            if action.get("tab_id") and tab_id != action["tab_id"]:
                continue
            for block in blocks:
                if block.get("type") == "tasks" and block.get("id") == action["block_id"]:
                    value = action["value"]
                    row = {
                        "id": f"pending-{action['id']}",
                        "title": value["title"],
                        "done": False,
                        "pending": True,
                        "can_toggle": False,
                        "tags": ["new"],
                    }
                    for key in ("due", "note", "group"):
                        if value.get(key):
                            row[key] = value[key]
                    block.setdefault("items", []).insert(0, row)
                    changed = True
                    break
            if changed:
                break
    elif kind in INSIGHT_TYPES:
        changed = _patch_insight(working, action, stamp)
    elif action.get("block_id") and action.get("item_id"):
        changed = _patch_button(working, action)
    return working, changed


def _patch_button(doc: dict, action: dict) -> bool:
    """A button that posted an action shows as pending until the owner's push replaces it."""
    changed = False
    for node_id, tab_id, blocks in blocks_of(doc):
        if action.get("node_id") and node_id != action["node_id"]:
            continue
        if action.get("tab_id") and tab_id != action["tab_id"]:
            continue
        for block in blocks:
            if block.get("id") != action["block_id"] or block.get("type") not in ("buttons", "hero"):
                continue
            rows = block.get("items") if block.get("type") == "buttons" else block.get("actions")
            for row in rows or []:
                if isinstance(row, dict) and row.get("id") == action["item_id"] and not row.get("pending"):
                    row["pending"] = True
                    changed = True
    return changed


def _patch_insight(doc: dict, action: dict, stamp: str) -> bool:
    """Mark the card (and the approved action) pending until the owner's push replaces it."""
    kind = action["type"]
    value = action["value"]
    changed = False
    for _node, _tab, blocks in blocks_of(doc):
        for block in blocks:
            if block.get("type") != "insight_cards":
                continue
            for card in block.get("cards") or []:
                if not isinstance(card, dict) or card.get("id") != action["item_id"]:
                    continue
                card["pending"] = True
                card["updated_at"] = stamp
                if kind == "insight.dismiss":
                    card["state"] = "dismissed"
                    card["status_text"] = "Dismissed · syncing"
                elif kind == "insight.snooze":
                    until = datetime.now(timezone.utc) + timedelta(days=value["days"])
                    card["state"] = "snoozed"
                    card["snoozed_until"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
                    card["status_text"] = f"Snoozed {value['days']} day{'s' if value['days'] != 1 else ''} · syncing"
                else:
                    # Only an approvable action turns pending. A blocked or finished one is left as it is;
                    # the owner answers the tap with failed and says why.
                    rows = [row for row in card.get("actions") or []
                            if isinstance(row, dict) and row.get("id") == value.get("action_id")
                            and row.get("state", "ready") in ("ready", "failed")]
                    if not rows:
                        card.pop("pending", None)
                        continue
                    for row in rows:
                        row["state"] = "pending"
                        row["status_text"] = "Approved · waiting for the owner feed"
                    card["state"] = "pending"
                    card["status_text"] = "Approved · waiting for the owner feed"
                changed = True
    return changed


class ActionStore:
    def __init__(self, root: Path):
        self.dir = root / "actions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, section_id: str) -> Path:
        if not SLUG.match(section_id):
            raise OpsError(f"invalid section id {section_id!r}")
        return self.dir / f"{section_id}.json"

    def load(self, section_id: str) -> list[dict]:
        path = self._path(section_id)
        if not path.is_file():
            return []
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return rows if isinstance(rows, list) else []

    def _save(self, section_id: str, rows: list[dict]) -> None:
        pending = [row for row in rows if row.get("status") == "pending"]
        resolved = [row for row in rows if row.get("status") != "pending"][-KEEP_RESOLVED:]
        keep = {id(row) for row in pending + resolved}
        rows = [row for row in rows if id(row) in keep]
        path = self._path(section_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def add(self, section_id: str, action: dict, *, device: str | None = None) -> dict:
        stored = dict(action)
        stored["id"] = f"act-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        stored["section_id"] = section_id
        stored["status"] = "pending"
        stored["created_at"] = _now()
        if device:
            stored["device"] = device
        rows = self.load(section_id)
        rows.append(stored)
        self._save(section_id, rows)
        return stored

    def query(self, section_id: str, status: str, limit: int) -> list[dict]:
        rows = self.load(section_id)
        if status == "pending":
            rows = [row for row in rows if row.get("status") == "pending"]
        return rows[-limit:]

    def resolve(self, section_id: str, results: list) -> list[dict]:
        if not isinstance(results, list) or not results:
            raise OpsError("results must be a non-empty array")
        wanted: dict[str, dict] = {}
        for index, result in enumerate(results):
            if not isinstance(result, dict) or not isinstance(result.get("id"), str):
                raise OpsError(f"results[{index}] needs an id")
            if result.get("status") not in RESULT_STATUS:
                raise OpsError(f"results[{index}].status must be applied or failed")
            message = result.get("message")
            if message is not None and (not isinstance(message, str) or len(message) > 500):
                raise OpsError(f"results[{index}].message must be at most 500 characters")
            wanted[result["id"]] = result
        rows = self.load(section_id)
        changed: list[dict] = []
        for row in rows:
            result = wanted.get(row.get("id"))
            if result is None:
                continue
            row["status"] = result["status"]
            row["message"] = result.get("message")
            row["resolved_at"] = _now()
            changed.append(row)
        missing = set(wanted) - {row["id"] for row in changed}
        if missing:
            raise OpsError(f"unknown action id {sorted(missing)[0]}", status=404)
        self._save(section_id, rows)
        return changed

    def trim(self, cutoff: datetime) -> int:
        """Drop resolved actions older than cutoff. Pending ones stay until their owner answers."""
        removed = 0
        for path in self.dir.glob("*.json"):
            section_id = path.stem
            if not SLUG.match(section_id):
                continue
            rows = self.load(section_id)
            kept = [row for row in rows if row.get("status") == "pending" or not _older(row.get("resolved_at") or row.get("created_at"), cutoff)]
            if len(kept) != len(rows):
                removed += len(rows) - len(kept)
                self._save(section_id, kept)
        return removed


def _older(stamp: str | None, cutoff: datetime) -> bool:
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when < cutoff
