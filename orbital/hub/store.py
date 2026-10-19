"""One JSON file per section, plus alerts, devices, shared prefs and media. Writes are temp-file then rename.

Layout under data/hub:
  sections/<id>.json   media/<hash> + media/<hash>.json   alerts.json   devices.json   prefs.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from orbital.hub.ops import OpsError, validate_section

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PREF_KEY = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
MEDIA_ID = re.compile(r"^[0-9a-f]{24}$")
MAX_SECTION_BYTES = 2_000_000
MAX_ALERTS = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def catalog_entry(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "title": doc["title"],
        "subtitle": doc.get("subtitle"),
        "icon": doc["icon"],
        "icon_image": doc.get("icon_image"),
        "order": doc["order"],
        "accent": doc.get("accent"),
        "layout": doc["layout"],
        "space": doc.get("space"),
        "revision": doc["revision"],
        "updated_at": doc.get("updated_at"),
        "summary": doc.get("summary"),
        "node_count": len(doc.get("nodes") or []),
    }


DECLARED_KEYS = ("id", "title", "subtitle", "icon", "accent", "order", "sections", "aliases")


def validate_spaces(raw: object) -> list[dict]:
    """The hub's own spaces (spaces.json): [{id, title, subtitle?, icon?, accent?, order?, sections: [...], aliases: [...]}].

    A section listed here is filed in that space whatever its own `space` says, so feeds that rewrite their
    sections can't undo it. `aliases` are other names for the space (an old company name on blocks)."""
    if isinstance(raw, dict):
        raw = raw.get("spaces")
    if not isinstance(raw, list) or len(raw) > 100:
        raise OpsError("spaces must be a list (at most 100)")
    out: list[dict] = []
    seen_ids: set[str] = set()
    claimed: dict[str, str] = {}
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise OpsError(f"spaces[{i}] must be an object")
        ident = row.get("id")
        if not isinstance(ident, str) or not SLUG.match(ident) or ident in seen_ids:
            raise OpsError(f"spaces[{i}].id must be a unique slug")
        seen_ids.add(ident)
        title = row.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 60:
            raise OpsError(f"spaces[{i}].title is required (at most 60 characters)")
        space: dict = {"id": ident, "title": title.strip()}
        for key in ("subtitle", "icon", "accent"):
            if row.get(key) is not None:
                if not isinstance(row[key], str) or len(row[key]) > 120:
                    raise OpsError(f"spaces[{i}].{key} must be a short string")
                space[key] = row[key]
        if row.get("order") is not None:
            if isinstance(row["order"], bool) or not isinstance(row["order"], int):
                raise OpsError(f"spaces[{i}].order must be a number")
            space["order"] = row["order"]
        for key in ("sections", "aliases"):
            values = row.get(key) or []
            if not isinstance(values, list) or len(values) > 200 or any(not isinstance(v, str) or not v or len(v) > 80 for v in values):
                raise OpsError(f"spaces[{i}].{key} must be a list of names")
            space[key] = list(dict.fromkeys(values))
        for section in space["sections"]:
            if section in claimed:
                raise OpsError(f"section {section} is in both {claimed[section]} and {ident}")
            claimed[section] = ident
        out.append(space)
    return out


def spaces_of(docs: list[dict], entries: list[dict], declared: list[dict] | None = None) -> list[dict]:
    """The app's home tiles: the hub's declared spaces first (they claim their sections), then sections
    grouped by their own `space`, titled by the first section that names it."""
    by_id = {doc["id"]: doc for doc in docs}
    spaces: dict[str, dict] = {}
    claimed: set[str] = set()
    for row in declared or []:
        space = {"id": row["id"], "title": row["title"], "icon": row.get("icon") or "square.grid.2x2",
                 "sections": [s for s in row.get("sections") or [] if s in by_id], "declared": True}
        for key in ("subtitle", "accent", "order", "aliases"):
            if row.get(key) is not None:
                space[key] = row[key]
        claimed.update(space["sections"])
        spaces[row["id"]] = space
    for row in entries:
        doc = by_id.get(row["id"]) or {}
        ident = doc.get("space")
        if not ident or row["id"] in claimed:
            continue
        if ident in spaces and spaces[ident].get("declared"):
            spaces[ident]["sections"].append(row["id"])
            continue
        space = spaces.setdefault(ident, {"id": ident, "title": None, "icon": None, "sections": []})
        space["title"] = space["title"] or doc.get("space_title")
        space["icon"] = space["icon"] or doc.get("space_icon")
        space["sections"].append(row["id"])
    for space in spaces.values():
        space["title"] = space["title"] or space["id"].replace("-", " ").title()
        space["icon"] = space["icon"] or "square.grid.2x2"
        space.pop("declared", None)
    return sorted(spaces.values(), key=lambda s: (s.get("order", 10_000), s["title"].lower()))


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.sections = root / "sections"
        self.media = root / "media"
        self.sections.mkdir(parents=True, exist_ok=True)
        self.media.mkdir(parents=True, exist_ok=True)
        self.alerts_path = root / "alerts.json"
        self.devices_path = root / "devices.json"
        self.prefs_path = root / "prefs.json"
        self.spaces_path = root / "spaces.json"
        self._lock = threading.RLock()

    # The hub's own spaces

    def declared_spaces(self) -> list[dict]:
        try:
            return validate_spaces(json.loads(self.spaces_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, OpsError):
            return []

    def save_spaces(self, raw: object) -> list[dict]:
        clean = validate_spaces(raw)
        with self._lock:
            if clean:
                self._write_json(self.spaces_path, {"spaces": clean})
            else:
                try:
                    self.spaces_path.unlink()
                except FileNotFoundError:
                    pass
        return clean

    def _write_json(self, path: Path, obj: object, *, limit: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        if limit is not None and len(raw.encode("utf-8")) > limit:
            raise OpsError("section is larger than 2 MB", status=413)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, path)

    # Sections

    def list_docs(self) -> list[dict]:
        docs = []
        for path in self.sections.glob("*.json"):
            try:
                docs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return docs

    def catalog(self) -> dict:
        docs = self.list_docs()
        entries = [catalog_entry(doc) for doc in docs]
        entries.sort(key=lambda row: (row["order"], row["title"].lower()))
        return {"sections": entries, "spaces": spaces_of(docs, entries, self.declared_spaces())}

    def get(self, section_id: str) -> dict | None:
        if not SLUG.match(section_id):
            return None
        path = self.sections / f"{section_id}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, doc: dict, *, bump: bool) -> dict:
        current = self.get(doc["id"])
        if bump:
            base = int(current["revision"]) if current else 0
            doc["revision"] = base + 1
            doc["updated_at"] = now_iso()
        else:
            doc["revision"] = int((current or {}).get("revision") or doc.get("revision") or 1)
            doc.setdefault("updated_at", now_iso())
        doc["schema"] = "orbital.section.v1"
        doc["nodes"] = sorted(doc.get("nodes") or [], key=lambda node: (node.get("order", 0), node.get("id", "")))
        validate_section(doc)
        self._write_json(self.sections / f"{doc['id']}.json", doc, limit=MAX_SECTION_BYTES)
        return doc

    def apply_batch(self, section_id: str, ops: list, alerts: list, if_revision: int | None):
        from orbital.hub.alerts import validate_alert
        from orbital.hub.ops import apply_ops

        if not SLUG.match(section_id):
            raise OpsError(f"invalid section id {section_id!r}")
        with self._lock:
            current = self.get(section_id)
            if current is None:
                raise OpsError(f"unknown section {section_id}", status=404)
            if if_revision is not None and int(current.get("revision") or 0) != int(if_revision):
                raise OpsError(f"revision conflict: hub has {current.get('revision')}", status=409)
            prepared = [validate_alert(item) for item in alerts]
            if ops:
                new_doc, mutated = apply_ops(current, ops)
            else:
                new_doc, mutated = current, False
            saved = current
            deleted = False
            if new_doc is None:
                path = self.sections / f"{section_id}.json"
                if path.is_file():
                    path.unlink()
                saved = None
                deleted = True
            elif mutated:
                saved = self.save(new_doc, bump=True)
            ids, fresh = self.add_alerts(prepared)
            return {"section": saved, "deleted": deleted, "mutated": mutated, "alert_ids": ids, "fresh_alerts": fresh}

    def validate_batch(self, section_id: str, ops: list, alerts: list, if_revision: int | None) -> dict:
        """Check an ops batch without writing the section or storing alerts."""
        from orbital.hub.alerts import validate_alert
        from orbital.hub.ops import apply_ops

        if not SLUG.match(section_id):
            raise OpsError(f"invalid section id {section_id!r}")
        with self._lock:
            current = self.get(section_id)
            if current is None:
                raise OpsError(f"unknown section {section_id}", status=404)
            if if_revision is not None and int(current.get("revision") or 0) != int(if_revision):
                raise OpsError(f"revision conflict: hub has {current.get('revision')}", status=409)
            for item in alerts:
                validate_alert(item)
            if ops:
                new_doc, mutated = apply_ops(current, ops)
            else:
                new_doc, mutated = current, False
            return {
                "would_write": mutated,
                "would_delete": new_doc is None,
                "revision": current.get("revision"),
                "document": new_doc,
            }

    def put(self, section_id: str, body: dict) -> dict:
        if not SLUG.match(section_id):
            raise OpsError(f"invalid section id {section_id!r}")
        doc = dict(body)
        doc["id"] = section_id
        doc.pop("revision", None)
        doc.pop("updated_at", None)
        for key in ("blocks", "tabs", "nodes"):
            doc.setdefault(key, [])
        with self._lock:
            return self.save(doc, bump=True)

    def delete(self, section_id: str) -> None:
        if not SLUG.match(section_id):
            raise OpsError(f"invalid section id {section_id!r}")
        path = self.sections / f"{section_id}.json"
        with self._lock:
            if not path.is_file():
                raise OpsError(f"unknown section {section_id}", status=404)
            path.unlink()

    def patch_for_action(self, section_id: str, action: dict) -> dict | None:
        """Apply the optimistic patch for one action. Returns the saved section when it changed."""
        from orbital.hub.actions import optimistic_patch

        with self._lock:
            current = self.get(section_id)
            if current is None:
                return None
            patched, changed = optimistic_patch(current, action)
            if not changed:
                return None
            return self.save(patched, bump=True)

    # Alerts

    def load_alerts(self) -> list[dict]:
        if not self.alerts_path.is_file():
            return []
        try:
            data = json.loads(self.alerts_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def save_alerts(self, alerts: list[dict]) -> None:
        self._write_json(self.alerts_path, alerts[-MAX_ALERTS:])

    def add_alerts(self, incoming: list[dict]) -> tuple[list[str], list[dict]]:
        """Return (all ids, newly stored alerts). Duplicates are not pushed again."""
        from orbital.hub.alerts import validate_alert

        with self._lock:
            stored = self.load_alerts()
            known = {row["id"] for row in stored}
            fresh: list[dict] = []
            ids: list[str] = []
            for raw in incoming:
                alert = validate_alert(raw)
                ids.append(alert["id"])
                if alert["id"] in known:
                    # A retracted alert sent again is new again.
                    revived = next((row for row in stored if row["id"] == alert["id"] and row.get("retracted")), None)
                    if revived is None:
                        continue
                    stored.remove(revived)
                alert["acked"] = False
                alert["created_at"] = now_iso()
                stored.append(alert)
                known.add(alert["id"])
                fresh.append(alert)
            if fresh:
                self.save_alerts(stored)
            return ids, fresh

    def retract(self, ids: list[str]) -> list[str]:
        """Mark alerts acked and retracted so apps drop their scheduled notifications."""
        wanted = set(ids)
        done: list[str] = []
        with self._lock:
            rows = self.load_alerts()
            for row in rows:
                if row["id"] in wanted and not row.get("retracted"):
                    row["acked"] = True
                    row["retracted"] = True
                    row["retracted_at"] = now_iso()
                    done.append(row["id"])
            if done:
                self.save_alerts(rows)
        return done

    def recently_retracted(self, days: int = 14) -> list[str]:
        cutoff = time.time() - days * 86400
        out = []
        for row in self.load_alerts():
            when = parse_iso(row.get("retracted_at"))
            if row.get("retracted") and when is not None and when.timestamp() >= cutoff:
                out.append(row["id"])
        return out

    def query_alerts(self, status: str, limit: int) -> list[dict]:
        rows = self.load_alerts()
        if status == "pending":
            rows = [row for row in rows if not row.get("acked")]
        return list(reversed(rows))[:limit]

    def ack(self, ids: list[str]) -> list[str]:
        """Mark alerts done. Returns the ids that changed, so every other device clears them too."""
        wanted = set(ids)
        done: list[str] = []
        with self._lock:
            rows = self.load_alerts()
            for row in rows:
                if row["id"] in wanted and not row.get("acked"):
                    row["acked"] = True
                    row["acked_at"] = now_iso()
                    done.append(row["id"])
            if done:
                self.save_alerts(rows)
        return done

    def acked_ids(self) -> list[str]:
        """Every stored alert that is done. A device that missed the socket clears these on its next sync."""
        return [row["id"] for row in self.load_alerts() if row.get("acked")]

    def trim_alerts(self, cutoff: datetime) -> int:
        with self._lock:
            rows = self.load_alerts()
            kept = [row for row in rows if (parse_iso(row.get("created_at")) or cutoff) >= cutoff]
            if len(kept) != len(rows):
                self.save_alerts(kept)
            return len(rows) - len(kept)

    # Devices and shared prefs

    def load_devices(self) -> list[dict]:
        if not self.devices_path.is_file():
            return []
        try:
            rows = json.loads(self.devices_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return rows if isinstance(rows, list) else []

    def save_device(self, device: dict) -> None:
        """Add or refresh one device. A push token moves with its device: no other row keeps it."""
        token = device.get("apns_token")
        with self._lock:
            devices = []
            for row in self.load_devices():
                if row.get("device_id") == device.get("device_id"):
                    continue
                if token and row.get("apns_token") == token:
                    row = {**row, "apns_token": None}
                devices.append(row)
            devices.append({**device, "last_seen": now_iso()})
            self._write_json(self.devices_path, devices[-50:])

    def drop_apns_token(self, token: str) -> bool:
        """Apple says this token is dead (uninstalled, reset): forget it. The device row stays."""
        with self._lock:
            devices = self.load_devices()
            changed = False
            for row in devices:
                if row.get("apns_token") == token:
                    row["apns_token"] = None
                    row["apns_dropped_at"] = now_iso()
                    changed = True
            if changed:
                self._write_json(self.devices_path, devices)
            return changed

    def load_prefs(self) -> dict:
        """Settings every app shares (the Money goal, a chosen space). Null removes a key."""
        if not self.prefs_path.is_file():
            return {}
        try:
            data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def merge_prefs(self, changes: dict) -> dict:
        """Set the given keys (null removes one). Returns every pref after the change."""
        for key, value in changes.items():
            if not isinstance(key, str) or not PREF_KEY.match(key):
                raise OpsError(f"invalid pref key {key!r}")
            if value is not None and (isinstance(value, (list, dict)) or not isinstance(value, (str, int, float, bool))):
                raise OpsError(f"pref {key} must be a string, number, or true/false")
            if isinstance(value, str) and len(value) > 400:
                raise OpsError(f"pref {key} must be at most 400 characters")
        with self._lock:
            prefs = self.load_prefs()
            for key, value in changes.items():
                if value is None:
                    prefs.pop(key, None)
                else:
                    prefs[key] = value
            if len(prefs) > 64:
                raise OpsError("too many prefs")
            self._write_json(self.prefs_path, prefs)
        return prefs

    # Media

    def save_media(self, data: bytes, content_type: str, name: str) -> dict:
        """Store bytes under a content hash. Uploading the same file again returns the same URL."""
        media_id = hashlib.sha256(data).hexdigest()[:24]
        path = self.media / media_id
        meta_path = self.media / f"{media_id}.json"
        if path.is_file():
            # Still in use: a re-upload keeps it clear of the retention trim.
            os.utime(path, None)
        else:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            meta = {"id": media_id, "content_type": content_type, "name": name[:200], "bytes": len(data), "created_at": now_iso()}
            self._write_json(meta_path, meta)
        return {"id": media_id, "url": f"/v1/media/{media_id}", "content_type": content_type, "bytes": len(data)}

    def media_file(self, media_id: str) -> tuple[Path, str] | None:
        if not MEDIA_ID.match(media_id):
            return None
        path = self.media / media_id
        meta_path = self.media / f"{media_id}.json"
        if not path.is_file():
            return None
        content_type = "application/octet-stream"
        if meta_path.is_file():
            try:
                content_type = json.loads(meta_path.read_text(encoding="utf-8")).get("content_type") or content_type
            except (OSError, json.JSONDecodeError):
                pass
        return path, content_type

    def trim_media(self, cutoff: datetime) -> int:
        """Delete media last uploaded before cutoff that no section still points at."""
        referenced: set[str] = set()
        for path in self.sections.glob("*.json"):
            try:
                referenced.update(re.findall(r"/v1/media/([0-9a-f]{24})", path.read_text(encoding="utf-8")))
            except OSError:
                continue
        removed = 0
        for path in self.media.iterdir():
            if not MEDIA_ID.match(path.name) or path.name in referenced:
                continue
            try:
                if path.stat().st_mtime >= cutoff.timestamp():
                    continue
                path.unlink()
                (self.media / f"{path.name}.json").unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed
