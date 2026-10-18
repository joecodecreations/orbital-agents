"""Retention: orbital.hub.maintenance.trim(cfg) drops old alerts, answered actions, unused media, idle sessions."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from hub_helpers import AUTH, make_client, section_body
from orbital.core.config import load_config
from orbital.hub.maintenance import trim

PNG_A = b"\x89PNG\r\n\x1a\n" + b"a" * 64
PNG_B = b"\x89PNG\r\n\x1a\n" + b"b" * 64


def old(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_trim_removes_only_what_is_old_and_unused(ports):
    client, hub = make_client()
    root = hub.paths.hub_data
    client.post("/v1/alerts", headers=AUTH, json={"alerts": [
        {"id": "old-one", "kind": "event", "title": "Old", "body": "x"},
        {"id": "new-one", "kind": "event", "title": "New", "body": "y"}]})
    alerts = json.loads((root / "alerts.json").read_text())
    alerts[0]["created_at"] = old(120)
    (root / "alerts.json").write_text(json.dumps(alerts))

    kept_media = client.post("/v1/media", headers=AUTH, files={"file": ("a.png", PNG_A, "image/png")}).json()
    stale_media = client.post("/v1/media", headers=AUTH, files={"file": ("b.png", PNG_B, "image/png")}).json()
    client.put("/v1/sections/gallery", headers=AUTH, json=section_body("gallery", blocks=[
        {"type": "image", "id": "pic", "src": kept_media["url"], "alt": "Kept"}]))
    long_ago = time.time() - 200 * 86400
    for media in (kept_media, stale_media):
        os.utime(root / "media" / media["id"], (long_ago, long_ago))

    for n in range(3):
        client.post("/v1/sections/gallery/actions", headers=AUTH, json={"type": "gallery.refresh", "value": {"n": n}})
    rows = client.get("/v1/sections/gallery/actions", headers=AUTH).json()["actions"]
    client.post("/v1/sections/gallery/actions/results", headers=AUTH, json={"results": [
        {"id": rows[0]["id"], "status": "applied"}, {"id": rows[1]["id"], "status": "applied"}]})
    queue = json.loads((root / "actions" / "gallery.json").read_text())
    for row in queue:
        row["created_at"] = old(200)
        if row.get("resolved_at"):
            row["resolved_at"] = old(100) if row["id"] == rows[0]["id"] else old(1)
    (root / "actions" / "gallery.json").write_text(json.dumps(queue))

    token = client.post("/v1/login", json={"username": "owner", "password": "correct horse"}).json()["token"]
    sessions = json.loads((root / "sessions.json").read_text())
    key = next(iter(sessions))
    sessions[key]["last_used"] = int(time.time()) - 100 * 86400
    (root / "sessions.json").write_text(json.dumps(sessions))

    result = trim(load_config())
    assert result == {"alerts": 1, "actions": 1, "media": 1, "sessions": 1}
    assert [a["id"] for a in client.get("/v1/alerts?status=all", headers=AUTH).json()["alerts"]] == ["new-one"]
    assert client.get(kept_media["url"], headers=AUTH).status_code == 200, "media a section uses is kept"
    assert client.get(stale_media["url"], headers=AUTH).status_code == 404
    statuses = sorted(r["status"] for r in client.get("/v1/sections/gallery/actions?status=all", headers=AUTH).json()["actions"])
    assert statuses == ["applied", "pending"], "the unanswered action stays however old it is"
    assert client.get("/v1/catalog", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert trim(load_config()) == {"alerts": 0, "actions": 0, "media": 0, "sessions": 0}


def test_retention_days_come_from_config(home):
    from hub_helpers import write_config

    write_config(home)
    (home / "config.toml").write_text((home / "config.toml").read_text().replace("[hub]\n", "[hub]\ndata_retention_days = 7\n"))
    client, hub = make_client()
    client.post("/v1/alerts", headers=AUTH, json={"alerts": [{"id": "week-old", "kind": "event", "title": "A", "body": "b"}]})
    alerts = json.loads((hub.paths.hub_data / "alerts.json").read_text())
    alerts[0]["created_at"] = old(8)
    (hub.paths.hub_data / "alerts.json").write_text(json.dumps(alerts))
    assert trim(load_config())["alerts"] == 1


def test_trim_on_a_fresh_install_is_a_no_op(home):
    assert trim(load_config()) == {"alerts": 0, "actions": 0, "media": 0, "sessions": 0}
