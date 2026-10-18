"""Realtime values: small numbers that change every minute and never bump a section revision.

A feed posts the latest value per key. The hub keeps the newest item per key,
fans it out over /v1/live as a `pulse` frame, and hands the full set to /v1/sync.
Contract: GET /v1/contract → realtime.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orbital.hub.ops import OpsError

KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_KEYS = 300
MAX_POINTS = 120
DEFAULT_TTL = 180
KEEP_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OpsError(f"{label} must be a finite JSON number")
    return value


def _text(item: dict, key: str, label: str, limit: int, *, required: bool = False) -> None:
    value = item.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise OpsError(f"{label}.{key} must be a string of at most {limit} characters")


def validate_item(raw: object, index: int, known_sections: set[str]) -> dict:
    label = f"items[{index}]"
    if not isinstance(raw, dict):
        raise OpsError(f"{label} must be an object")
    item = dict(raw)
    key = item.get("key")
    if not isinstance(key, str) or not KEY.match(key):
        raise OpsError(f"{label}.key must match ^[a-z0-9][a-z0-9._-]{{0,127}}$")
    section_id = item.get("section_id")
    if not isinstance(section_id, str) or not SLUG.match(section_id):
        raise OpsError(f"{label}.section_id is required")
    if section_id not in known_sections:
        raise OpsError(f"{label}.section_id {section_id} is not a section. GET /v1/catalog", status=404)
    node_id = item.get("node_id")
    if node_id is not None and (not isinstance(node_id, str) or not SLUG.match(node_id)):
        raise OpsError(f"{label}.node_id must be a node id")
    _text(item, "label", label, 60, required=True)
    _number(item.get("value"), f"{label}.value")
    for key_name, limit in (("display", 40), ("detail", 120), ("unit", 24), ("source", 40), ("platform", 24)):
        _text(item, key_name, label, limit)
    at = item.get("at")
    if at is None:
        item["at"] = _iso(_now())
    elif not isinstance(at, str) or _parse(at) is None:
        raise OpsError(f"{label}.at must be ISO-8601")
    ttl = item.get("ttl_seconds", DEFAULT_TTL)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 10 <= ttl <= 86400:
        raise OpsError(f"{label}.ttl_seconds must be a whole number from 10 to 86400")
    item["ttl_seconds"] = ttl
    order = item.get("order")
    if order is not None and (isinstance(order, bool) or not isinstance(order, int)):
        raise OpsError(f"{label}.order must be a whole number")
    series = item.get("series")
    if series is not None:
        if not isinstance(series, list) or len(series) > MAX_POINTS:
            raise OpsError(f"{label}.series must be an array of at most {MAX_POINTS} points")
        for point_index, point in enumerate(series):
            if not isinstance(point, dict) or not isinstance(point.get("x"), str):
                raise OpsError(f"{label}.series[{point_index}] needs a string x")
            _number(point.get("y"), f"{label}.series[{point_index}].y")
    item["received_at"] = _iso(_now())
    return item


class PulseStore:
    """Newest item per key, kept in memory and mirrored to one small file."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.items: dict[str, dict] = {}
        if path.is_file():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rows = []
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and isinstance(row.get("key"), str):
                    self.items[row["key"]] = row

    @staticmethod
    def _pruned(rows: dict[str, dict]) -> dict[str, dict]:
        cutoff = _now() - timedelta(days=KEEP_DAYS)
        kept = {}
        for key, row in rows.items():
            when = _parse(str(row.get("at") or ""))
            if when is not None and when >= cutoff:
                kept[key] = row
        return kept

    def _write(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(self.items.values()), ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def apply(self, items: list, remove: list, known_sections: set[str]) -> tuple[list[dict], list[str]]:
        prepared = [validate_item(raw, index, known_sections) for index, raw in enumerate(items)]
        if not all(isinstance(key, str) and KEY.match(key) for key in remove):
            raise OpsError("remove must be an array of pulse keys")
        with self._lock:
            updated = dict(self.items)
            removed = [key for key in remove if updated.pop(key, None) is not None]
            for item in prepared:
                updated[item["key"]] = item
            updated = self._pruned(updated)
            if len(updated) > MAX_KEYS:
                raise OpsError(f"more than {MAX_KEYS} pulse keys. Remove old keys with remove")
            self.items = updated
            self._write()
        return prepared, removed

    def all(self) -> list[dict]:
        with self._lock:
            return sorted(self.items.values(), key=lambda row: row["key"])
