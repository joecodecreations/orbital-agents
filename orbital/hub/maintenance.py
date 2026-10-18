"""Data retention for the hub, called by the watchdog (and by the hub itself once a day).

    from orbital.hub.maintenance import trim
    trim(load_config())   # → {"alerts": 3, "actions": 12, "media": 1, "sessions": 0}

Deletes alerts, resolved action results, and media older than hub.data_retention_days. Media a
section still points at is kept however old it is, and so is any action a feed hasn't answered.
App sessions unused for that long are signed out. Safe to run while the hub is up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orbital.core import get_logger
from orbital.hub.actions import ActionStore
from orbital.hub.auth import Sessions
from orbital.hub.store import Store


def trim(cfg, now: datetime | None = None) -> dict:
    days = max(1, int(cfg.get("hub.data_retention_days", 90) or 90))
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    root = cfg.paths.hub_data
    if not root.is_dir():
        return {"alerts": 0, "actions": 0, "media": 0, "sessions": 0}
    store = Store(root)
    result = {
        "alerts": store.trim_alerts(cutoff),
        "actions": ActionStore(root).trim(cutoff),
        "media": store.trim_media(cutoff),
        "sessions": Sessions(root / "sessions.json").trim(days) if (root / "sessions.json").is_file() else 0,
    }
    if any(result.values()):
        get_logger("hub").info("retention trim", extra={"fields": {**result, "days": days}})
    return result

