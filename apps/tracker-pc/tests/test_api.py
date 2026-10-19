"""The loopback HTTP API: the same routes, bodies, answers and refusals as the Mac app
(apps/tracker/README.md, "HTTP API")."""

from __future__ import annotations

import base64
import http.client
import io
import json

import pytest

from tracker_pc import SERVICE


def call(app, method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
    hdrs = {"Content-Type": "application/json"} if body is not None or raw is not None else {}
    hdrs.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    ctype = resp.getheader("Content-Type", "")
    return resp.status, (json.loads(payload) if "json" in ctype and payload else payload), resp


def test_health_has_the_mac_keys(app):
    status, body, _ = call(app, "GET", "/v1/health")
    assert status == 200
    for key in ("ok", "service", "version", "app", "machine", "role", "port", "pid", "active", "items",
                "evidence", "voice", "titles", "relay_to_hub", "hotkeys"):
        assert key in body, key
    assert body["service"] == SERVICE == "orbital-tracker"
    assert body["machine"] == "test-pc" and body["port"] == app.port
    assert set(body["voice"]) == {"enabled", "status"} and set(body["titles"]) == {"mode", "grok"}
    assert set(body["hotkeys"]) == {"enabled", "modifiers", "symbols"}


def test_run_file_written_and_removed(home, app):
    info = json.loads((home / "run" / "tracker.json").read_text())
    for key in ("service", "version", "port", "pid", "startedAt"):
        assert key in info
    assert info["port"] == app.port and info["service"] == "orbital-tracker"
    app.stop()
    assert not (home / "run" / "tracker.json").exists()


def test_command_lifecycle(app):
    status, body, _ = call(app, "POST", "/v1/command", {"op": "start", "id": "job-1", "agent": "Claude Code",
                                                       "title": "Fix Login", "details": "**md**", "workspace": "/w"})
    assert (status, body) == (200, {"ok": True, "id": "job-1"})
    assert call(app, "POST", "/v1/command", {"op": "sub", "id": "sub-1", "parent": "job-1", "agent": "codex",
                                             "title": "Audit"})[1] == {"ok": True, "id": "sub-1"}
    assert call(app, "POST", "/v1/command", {"op": "note", "id": "job-1", "note": "half way"})[0] == 200
    assert call(app, "POST", "/v1/command", {"op": "update", "id": "sub-1", "details": "more", "appendDetails": True})[0] == 200
    assert call(app, "POST", "/v1/command", {"op": "finish", "id": "sub-1", "summary": "sub ok"})[0] == 200

    status, active, _ = call(app, "GET", "/v1/items")
    assert status == 200 and [i["id"] for i in active] == ["job-1"]
    item = active[0]
    assert item["agent"] == "claude" and item["source"] == "agent" and item["machine"] == "test-pc"
    assert item["notes"][0]["text"] == "half way"
    sub = item["subagents"][0]
    assert sub["agent"] == "chatgpt" and sub["status"] == "done" and sub["summary"] == "sub ok"
    assert sub["details"] == "more"  # nothing to append to yet

    assert call(app, "POST", "/v1/command", {"op": "complete", "id": "job-1", "summary": "done!"})[0] == 200
    assert call(app, "GET", "/v1/items")[1] == []
    status, everything, _ = call(app, "GET", "/v1/items?all=1")
    done = everything[0]
    assert done["status"] == "done" and done["dismissed"] is True and done["completedBy"] == "claude"
    assert done["summary"] == "done!" and "completedAt" in done
    status, one, _ = call(app, "GET", "/v1/items/job-1")
    assert status == 200 and one["id"] == "job-1"
    assert call(app, "GET", "/v1/items/nope")[0] == 404


def test_start_is_idempotent_and_updates(app):
    call(app, "POST", "/v1/command", {"op": "start", "id": "same", "agent": "grok", "title": "One"})
    call(app, "POST", "/v1/command", {"op": "start", "id": "same", "agent": "grok", "title": "Renamed"})
    items = call(app, "GET", "/v1/items")[1]
    assert len(items) == 1 and items[0]["title"] == "Renamed"


def test_friendly_routes(app):
    status, body, _ = call(app, "POST", "/v1/start", {"title": "Curl Job", "agent": "gemini"})
    assert status == 200 and len(body["id"]) == 8
    item_id = body["id"]
    status, body, _ = call(app, "POST", f"/v1/sub/{item_id}", {"title": "Child"})
    assert status == 200
    sub_id = body["id"]
    assert call(app, "POST", f"/v1/update/{item_id}", {"note": "n"})[0] == 200
    assert call(app, "POST", f"/v1/done/{sub_id}", {})[0] == 200
    assert call(app, "POST", f"/v1/done/{item_id}", {"summary": "s"})[0] == 200
    item = call(app, "GET", f"/v1/items/{item_id}")[1]
    assert item["status"] == "done" and item["subagents"][0]["status"] == "done"


@pytest.mark.parametrize("body,error", [
    ({"op": "start", "id": "x", "title": "t"}, "id must be 2-64"),
    ({"op": "start", "id": "a/b", "title": "t"}, "id must be 2-64"),
    ({"op": "start", "id": "ok-id"}, "title is required"),
    ({"op": "explode", "id": "ok-id"}, "unknown op"),
    ({"op": "update", "id": "missing"}, "unknown id 'missing'"),
    ({"op": "sub", "id": "s1", "parent": "missing", "title": "t"}, "unknown parent id"),
    ({"op": "update"}, "id is required"),
])
def test_bad_commands_are_400(app, body, error):
    status, answer, _ = call(app, "POST", "/v1/command", body)
    assert status == 400 and answer["ok"] is False and error in answer["error"]


def test_bad_json_is_400(app):
    status, answer, _ = call(app, "POST", "/v1/command", raw=b"{nope")
    assert status == 400 and answer["ok"] is False


def test_guard_refuses_browsers_hosts_and_non_json(app):
    assert call(app, "GET", "/v1/health", headers={"Origin": "http://evil.example"})[0] == 403
    assert call(app, "GET", "/v1/health", headers={"Host": "evil.example:7701"})[0] == 403
    assert call(app, "GET", "/v1/health", headers={"Host": "localhost:1"})[0] == 200
    status, answer, _ = call(app, "POST", "/v1/command", raw=b"{}", headers={"Content-Type": "text/plain"})
    assert status == 415 and "application/json" in answer["error"]
    assert call(app, "GET", "/nope")[0] == 404
    assert call(app, "GET", "/v1/whatever")[0] == 404


def test_settings_round_trip(home, app):
    status, body, _ = call(app, "GET", "/v1/settings")
    assert status == 200 and body["settings"]["evidence"] == "off" and body["settings"]["label"] == "Off"
    status, body, _ = call(app, "POST", "/v1/settings", {"evidence": "Min 1", "by": "phone-app-v1"})
    assert status == 200
    s = body["settings"]
    assert s["evidence"] == "min1" and s["label"] == "Min 1" and s["updatedBy"] == "app" and s["updatedAt"].endswith("Z")
    on_disk = json.loads((home / "data" / "tracker" / "settings.json").read_text())
    assert on_disk == {"evidence": "min1", "updatedAt": s["updatedAt"], "updatedBy": "app"}
    assert call(app, "POST", "/v1/settings", {"evidence": "loud"})[0] == 400
    assert call(app, "GET", "/v1/health")[1]["evidence"] == "min1"


def test_settings_changed_on_disk_are_picked_up(home, app):
    (home / "data" / "tracker" / "settings.json").write_text(
        '{"evidence":"all","updatedAt":"2030-01-02T03:04:05Z","updatedBy":"cli"}')
    body = call(app, "GET", "/v1/settings")[1]
    assert body["settings"]["evidence"] == "all" and body["settings"]["updatedBy"] == "cli"


def test_media_original_archive_and_range(home, app, png):
    call(app, "POST", "/v1/command", {"op": "start", "id": "m1", "title": "Pics", "media": [{"path": str(png)}]})
    app.store.wait_for_archives()
    ref = app.store.item("m1")["media"][0]
    assert ref["kind"] == "image" and ref["archivePath"].startswith("media/") and ref["archivePath"].endswith(".jpg")
    archive = home / "data" / "tracker" / ref["archivePath"]
    assert archive.read_bytes()[:3] == b"\xff\xd8\xff"
    from PIL import Image

    with Image.open(archive) as im:
        assert max(im.size) == 480

    status, data, resp = call(app, "GET", f"/v1/media/m1/{ref['id']}")
    assert status == 200 and resp.getheader("Content-Type") == "image/png" and data == png.read_bytes()
    status, data, resp = call(app, "GET", f"/v1/media/m1/{ref['id']}", headers={"Range": "bytes=0-9"})
    assert status == 206 and data == png.read_bytes()[:10]
    assert resp.getheader("Content-Range") == f"bytes 0-9/{png.stat().st_size}"
    png.unlink()  # the original is gone: the archive is served
    status, data, resp = call(app, "GET", f"/v1/media/m1/{ref['id']}")
    assert status == 200 and resp.getheader("Content-Type") == "image/jpeg" and data[:3] == b"\xff\xd8\xff"
    assert call(app, "GET", "/v1/media/m1/nope")[0] == 404


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 40), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_relayed_media_from_another_machine(home, app):
    """The hub relays satellites' commands with media[].inlineArchive: only that copy is kept."""
    ref = {"id": "abc123", "kind": "image", "originalPath": "/home/x/shot.png", "archivePath": "media/2026-01/zz-abc123.jpg",
           "addedAt": "2026-01-02T03:04:05Z", "machine": "laptop"}
    body = {"op": "start", "id": "remote1", "title": "Remote", "machine": "laptop",
            "media": [{"path": "/home/x/shot.png", "archived": ref, "inlineArchive": base64.b64encode(_jpeg()).decode()},
                      {"path": "/home/x/other.png", "archived": dict(ref, id="def456"),
                       "inlineArchive": base64.b64encode(b"not a jpeg").decode()},
                      {"path": "/home/x/raw.mp4"}]}
    assert call(app, "POST", "/v1/command", body)[0] == 200
    media = app.store.item("remote1")["media"]
    assert media[0]["machine"] == "laptop" and media[0]["archivePath"] != ref["archivePath"]
    assert (home / "data" / "tracker" / media[0]["archivePath"]).read_bytes()[:3] == b"\xff\xd8\xff"
    assert "archivePath" not in media[1]  # not a JPEG: dropped
    assert media[2]["kind"] == "video" and media[2]["machine"] == "laptop" and "archivePath" not in media[2]
    assert app.store.item("remote1")["machine"] == "laptop"


def test_unsafe_archive_paths_from_the_cli_are_dropped(app):
    ref = {"id": "abc123", "kind": "image", "originalPath": "/tmp/x.png", "archivePath": "../../secrets.env",
           "addedAt": "2026-01-02T03:04:05Z"}
    call(app, "POST", "/v1/command", {"op": "start", "id": "unsafe", "title": "t", "media": [{"path": "/tmp/x.png", "archived": ref}]})
    assert "archivePath" not in app.store.item("unsafe")["media"][0]


def test_markdown_images_are_archived(app, png):
    call(app, "POST", "/v1/command", {"op": "start", "id": "mdimg", "title": "t", "details": f"look ![after]({png})"})
    app.store.wait_for_archives()
    media = app.store.item("mdimg")["media"]
    assert len(media) == 1 and media[0]["originalPath"] == str(png)
