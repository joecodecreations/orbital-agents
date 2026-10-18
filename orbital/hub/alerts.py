"""Alert objects stored beside sections. Schema: schema/orbital.alert.v1.schema.json."""

from __future__ import annotations

import re
from datetime import datetime

from orbital.hub.ops import OpsError

ALERT_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
LEVELS = {"passive", "active", "timeSensitive", "critical"}
KINDS = {"event", "reminder"}


def _parse_time(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise OpsError(f"deliver_at is not ISO-8601: {value}") from exc


def validate_alert(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise OpsError("alert must be an object")
    if raw.get("schema") not in (None, "orbital.alert.v1"):
        raise OpsError("alert schema must be orbital.alert.v1")
    alert = dict(raw)
    alert["schema"] = "orbital.alert.v1"
    if not isinstance(alert.get("id"), str) or not ALERT_ID.match(alert["id"]):
        raise OpsError("alert id must be 1–128 letters, numbers, dot, underscore, or dash")
    if alert.get("kind") not in KINDS:
        raise OpsError("alert kind must be event or reminder")
    if not isinstance(alert.get("title"), str) or not alert["title"].strip():
        raise OpsError("alert title is required")
    if not isinstance(alert.get("body"), str):
        raise OpsError("alert body is required")
    level = alert.get("interruption_level")
    if level is not None and level not in LEVELS:
        raise OpsError("invalid interruption_level")
    score = alert.get("relevance_score")
    if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= float(score) <= 1):
        raise OpsError("relevance_score must be between 0 and 1")
    actions = alert.get("actions") or []
    if not isinstance(actions, list) or len(actions) > 4:
        raise OpsError("an alert can have at most 4 actions")
    deliver_at = alert.get("deliver_at")
    repeats = alert.get("repeats")
    if deliver_at and repeats:
        raise OpsError("deliver_at and repeats cannot both be set")
    if deliver_at:
        _parse_time(deliver_at)
    if repeats is not None:
        if not isinstance(repeats, dict):
            raise OpsError("repeats must be an object")
        hour = repeats.get("hour")
        minute = repeats.get("minute")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise OpsError("repeats.hour must be 0–23")
        if not isinstance(minute, int) or not 0 <= minute <= 59:
            raise OpsError("repeats.minute must be 0–59")
        weekday = repeats.get("weekday")
        if weekday is not None and (not isinstance(weekday, int) or not 1 <= weekday <= 7):
            raise OpsError("repeats.weekday must be 1–7")
    return alert
