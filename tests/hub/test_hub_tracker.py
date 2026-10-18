"""The tracker read view, its signed media, the shared evidence setting, and the command relay."""

from __future__ import annotations

import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hub_helpers import AUTH, JOIN, SATELLITE, Server, free_port, make_client, write_config

PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 64


def write_items(hub) -> None:
    root = hub.paths.tracker_data
    (root / "media" / "2026-01").mkdir(parents=True, exist_ok=True)
    (root / "media" / "2026-01" / "shot.jpg").write_bytes(PNG)
    items = [
        {"id": "a1", "title": "Fix login", "status": "active", "source": "agent", "agent": "claude", "workspace": "/srv/site",
         "machine": "build-box",
         "createdAt": "2026-01-02T10:00:00Z", "updatedAt": "2026-01-02T10:05:00Z",
         "notes": [{"at": "2026-01-02T10:05:00Z", "text": "Found the bug"}],
         "media": [{"id": "m1", "kind": "image", "archivePath": "media/2026-01/shot.jpg", "caption": "Login page"}],
         "details": "See ![shot](/nowhere/shot.png)"},
        {"id": "b2", "title": "Water plants", "status": "done", "source": "manual", "createdAt": "2026-01-01T09:00:00Z",
         "completedAt": "2026-01-01T09:30:00Z", "summary": "Done."},
    ]
    (root / "items.json").write_text(json.dumps(items))


def test_items_detail_and_signed_media(ports):
    client, hub = make_client()
    write_items(hub)
    res = client.get("/v1/tracker/items", headers=AUTH).json()
    assert res["total"] == 2 and res["active"] == 1
    assert res["items"][0]["id"] == "a1" and res["items"][0]["agent_name"] == "Claude"
    assert res["items"][0]["machine"] == "build-box" and res["items"][1]["machine"] is None
    assert client.get("/v1/tracker/items?who=ai", headers=AUTH).json()["total"] == 1
    assert client.get("/v1/tracker/items?status=done", headers=AUTH).json()["items"][0]["id"] == "b2"
    assert client.get("/v1/tracker/items?q=login", headers=AUTH).json()["total"] == 1
    assert client.get("/v1/tracker/items?scope=year", headers=AUTH).status_code == 400
    assert client.get("/v1/tracker/items").status_code == 401
    detail = client.get("/v1/tracker/items/a1", headers=AUTH).json()
    assert detail["notes"][0]["text"] == "Found the bug"
    assert detail["desk_task"] is None
    thumb = detail["media"][0]["thumb_url"]
    assert "sig=" in thumb
    fetched = client.get(thumb)
    assert fetched.status_code == 200 and fetched.content == PNG, "signed URLs need no header"
    assert client.get(thumb.replace("sig=", "sig=0")).status_code == 401
    assert client.get("/v1/tracker/items/zz", headers=AUTH).status_code == 404


def join(client) -> str:
    answer = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json={
        "machine": "build-box", "port": 17000, "ip": "127.0.0.1", "satellite_token": SATELLITE})
    assert answer.status_code == 200, answer.text
    return answer.json()["hub_token"]


def test_evidence_setting_without_the_tracker_app_writes_the_file(ports):
    client, hub = make_client()
    assert client.get("/v1/tracker/settings", headers=AUTH).json()["evidence"] == "off"
    res = client.post("/v1/tracker/settings", headers=AUTH, json={"evidence": "Min 1"})
    assert res.status_code == 200, res.text
    assert res.json()["evidence"] == "min1"
    assert res.json()["updatedBy"] == res.json()["updated_by"] == "app"
    assert res.json()["updatedAt"], "the tracker reads updatedAt to keep the newest copy"
    saved = json.loads((hub.paths.tracker_data / "settings.json").read_text())
    assert saved["evidence"] == "min1" and saved["updatedBy"] == "app"
    assert client.post("/v1/tracker/settings", headers=AUTH, json={"evidence": "lots"}).status_code == 400
    assert client.get("/v1/tracker/settings").status_code == 401
    assert client.post("/v1/tracker/settings", json={"evidence": "all"}).status_code == 401


def test_settings_accept_a_satellite_hub_token(ports):
    client, hub = make_client()
    satellite = {"Authorization": f"Bearer {join(client)}"}
    assert client.get("/v1/tracker/settings", headers=satellite).json()["evidence"] == "off"
    res = client.post("/v1/tracker/settings", headers=satellite, json={"evidence": "all", "by": "cli"})
    assert res.status_code == 200, res.text
    assert res.json()["updatedBy"] == "cli"
    res = client.post("/v1/tracker/settings", headers=satellite, json={"evidence": "off"})
    assert res.json()["updatedBy"] == "tracker", "a satellite's tracker without a by"
    session = client.post("/v1/login", json={"username": "owner", "password": "correct horse"}).json()["token"]
    res = client.post("/v1/tracker/settings", headers={"Authorization": f"Bearer {session}"}, json={"evidence": "min1", "by": "whatever"})
    assert res.json()["updatedBy"] == "app"
    assert client.get("/v1/tracker/settings", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def fake_tracker_app() -> tuple[FastAPI, dict]:
    app = FastAPI()
    seen: dict = {"commands": [], "raw": [], "settings": []}

    @app.post("/v1/command")
    async def command(request: Request):
        raw = await request.body()
        seen["raw"].append((raw, request.headers.get("content-type")))
        body = json.loads(raw)
        if body.get("type") == "bogus":
            return JSONResponse({"ok": False, "error": "unknown command bogus"}, status_code=400)
        seen["commands"].append(body)
        return {"ok": True, "id": "t9"}

    @app.post("/v1/settings")
    async def settings(request: Request):
        seen["settings"].append(await request.json())
        return {"ok": True}

    return app, seen


SATELLITE_COMMAND = (
    b'{"type": "start",  "title": "Index photos", "agent": "codex", "machine": "build-box",'
    b' "media": [{"path": "/tmp/shot.png", "inlineArchive": "/9j/4AAQSkZJRgABAQ=="}]}'
)


def test_command_relay_forwards_the_body_unchanged(home):
    tracker, seen = fake_tracker_app()
    with Server(tracker) as app_server:
        write_config(home, tracker_port=app_server.port)
        client, hub = make_client()
        satellite = {"Authorization": f"Bearer {join(client)}", "Content-Type": "application/json"}
        res = client.post("/v1/tracker/command", headers=satellite, content=SATELLITE_COMMAND)
        assert res.status_code == 200, res.text
        assert res.json()["delivered"] == "app" and res.json()["result"]["id"] == "t9"
        assert seen["raw"] == [(SATELLITE_COMMAND, "application/json")], "byte for byte, machine and inline media included"
        by_script = client.post("/v1/tracker/command", headers=AUTH, json={"type": "update", "id": "t9", "note": "half way"})
        assert by_script.status_code == 200
        assert "machine" not in seen["commands"][-1], "a script's command is passed on as it came"
        unnamed = client.post("/v1/tracker/command", headers=satellite, json={"type": "update", "id": "t9", "note": "from a script there"})
        assert unnamed.status_code == 200
        assert seen["commands"][-1]["machine"] == "build-box", "a satellite's command without a machine gets its machine"
        refused = client.post("/v1/tracker/command", headers=AUTH, json={"type": "bogus"})
        assert refused.status_code == 400, "the tracker's own refusal reaches the caller, so it isn't retried"
        assert "unknown command" in refused.json()["error"]
        assert client.post("/v1/tracker/command", content=SATELLITE_COMMAND).status_code == 401
        assert client.post("/v1/tracker/command", headers=AUTH, json={}).status_code == 400
        assert client.post("/v1/tracker/command", headers=AUTH, content=b"[1, 2]").status_code == 400
        assert client.post("/v1/tracker/settings", headers=AUTH, json={"evidence": "all"}).status_code == 200
        assert seen["settings"] == [{"evidence": "all", "by": "app"}]


def test_command_relay_queues_in_the_inbox_oldest_first(home):
    write_config(home, tracker_port=free_port())
    client, hub = make_client()
    satellite = {"Authorization": f"Bearer {join(client)}", "Content-Type": "application/json"}
    first = client.post("/v1/tracker/command", headers=satellite, content=SATELLITE_COMMAND)
    second = client.post("/v1/tracker/command", headers=AUTH, json={"type": "done", "id": "t9", "summary": "Indexed."})
    assert first.json()["delivered"] == second.json()["delivered"] == "inbox"
    files = sorted(path.name for path in (hub.paths.tracker_data / "inbox").glob("*.json"))
    assert files == [first.json()["file"], second.json()["file"]], "names sort in the order they were written"
    assert all(re.fullmatch(r"\d{16}-\d{4}-[0-9a-f]{4}\.json", name) for name in files)
    assert (hub.paths.tracker_data / "inbox" / files[0]).read_bytes() == SATELLITE_COMMAND
    assert not list((hub.paths.tracker_data / "inbox").glob(".*")), "no temp files left behind"
    too_big = client.post("/v1/tracker/command", headers=AUTH, content=b'{"x": "' + b"a" * (16 * 1024 * 1024) + b'"}')
    assert too_big.status_code == 413


def test_run_file_port_wins_over_config(home):
    tracker, seen = fake_tracker_app()
    with Server(tracker) as app_server:
        write_config(home, tracker_port=free_port())
        client, hub = make_client()
        (hub.paths.run / "tracker.json").write_text(json.dumps({"port": app_server.port}))
        assert client.post("/v1/tracker/command", headers=AUTH, json={"type": "ping"}).json()["delivered"] == "app"


def test_an_agent_finishing_raises_an_alert(ports):
    client, hub = make_client()
    write_items(hub)
    from orbital.hub.tracker import TrackerEvents

    events = TrackerEvents(hub)
    finished = dict(hub.tracker.items()[0], status="done", completedAt="2026-01-02T11:00:00Z", summary="Login works again. Details follow.")
    with client:
        client.portal.call(events.changed, [finished], [])
    alerts = client.get("/v1/alerts", headers=AUTH).json()["alerts"]
    assert alerts[0]["id"] == "tracker-a1-done"
    assert alerts[0]["body"] == "Login works again."
    assert alerts[0]["launch_url"] == "orbital://tracker?id=a1"
