"""Bridge behavior: command queue, results, timeouts, capture, staging, files, run file, logs."""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import httpx

from browser_helpers import FakeExtension, wait_until


def post(bridge, path, body=None, **kw):
    return httpx.post(bridge.url + path, json=body if body is not None else {}, timeout=kw.pop("timeout", 30), **kw)


def get(bridge, path, **kw):
    return httpx.get(bridge.url + path, timeout=30, **kw)


# ── health, status, run file ──────────────────────────────────────

def test_health_shape_without_extension(bridge):
    r = get(bridge, "/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["service"] == "orbital-browser-bridge"
    assert data["extension_connected"] is False
    assert data["version"]


def test_health_reports_extension(bridge, fake_extension):
    assert get(bridge, "/health").json()["extension_connected"] is True


def test_status_shape(bridge, fake_extension):
    data = get(bridge, "/status").json()
    assert data["ok"] and data["service"] == "orbital-browser-bridge"
    assert data["port"] == bridge.port
    assert data["bind"] == "loopback"
    assert data["listening"] == [f"127.0.0.1:{bridge.port}"]
    assert data["extension"]["connected"] is True
    assert data["extension"]["version"] == "0.1.0"
    assert data["extension"]["instances"] == 1
    assert data["queue"] == {"waiting": 0, "in_flight": 0, "stored_results": 0}
    assert data["capture"]["secrets_redacted"] is True


def test_run_file_written_and_removed(make_bridge, orbital_home):
    b = make_bridge()
    run = orbital_home / "run" / "bridge.json"
    data = json.loads(run.read_text())
    assert data["port"] == b.port and data["pid"] == os.getpid()
    assert data["service"] == "orbital-browser-bridge"
    b.stop()
    assert not run.exists()


def test_logs_have_commands_but_no_params(bridge, fake_extension, orbital_home):
    secret_text = "do-not-log-this-text"
    post(bridge, "/command", {"action": "type", "params": {"selector": "#q", "text": secret_text}})
    log = (orbital_home / "logs" / "browser-bridge.log").read_text()
    lines = [json.loads(line) for line in log.splitlines()]
    assert any(line["msg"] == "bridge started" for line in lines)
    assert any(line["msg"] == "command" and line["action"] == "type" for line in lines)
    assert secret_text not in log


# ── commands ──────────────────────────────────────────────────────

def test_command_round_trip(bridge, fake_extension):
    r = post(bridge, "/command", {"action": "ping", "params": {}})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["action"] == "ping" and data["result"] == {"pong": True}
    assert data["id"]


def test_params_reach_the_extension(bridge, fake_extension):
    post(bridge, "/command", {"action": "navigate", "params": {"url": "https://example.com/a", "session": "s2"}})
    cmd = fake_extension.received[-1]
    assert cmd["action"] == "navigate"
    assert cmd["params"] == {"url": "https://example.com/a", "session": "s2"}


def test_args_is_an_alias_for_params(bridge, fake_extension):
    data = post(bridge, "/command", {"action": "navigate", "args": {"url": "https://example.org/"}}).json()
    assert data["result"]["url"] == "https://example.org/"


def test_routed_command_shape(bridge, fake_extension):
    """POST /<action> with params as the body: what the hub relay forwards."""
    data = post(bridge, "/navigate", {"url": "https://example.net/", "_timeout": 5}).json()
    assert data["ok"] and data["result"]["url"] == "https://example.net/"
    assert "_timeout" not in fake_extension.received[-1]["params"]
    data = post(bridge, "/command/eval", {"code": "1+1"}).json()
    assert data["result"]["value"] == "value of 1+1"


def test_bridge_actions_through_command(bridge):
    """The hub relay always forwards to /command; the bridge answers its own endpoints there."""
    data = post(bridge, "/command", {"action": "bridge_status", "params": {}}).json()
    assert data["service"] == "orbital-browser-bridge" and data["port"] == bridge.port
    post(bridge, "/ext/events", {"events": [{"type": "page.ready", "url": "https://example.com/"}]})
    assert post(bridge, "/command", {"action": "network", "params": {"limit": 5}}).json()["count"] == 1
    assert post(bridge, "/command", {"action": "health"}).json()["service"] == "orbital-browser-bridge"
    staged = post(bridge, "/command", {"action": "stage", "params": {"name": "a.txt", "content_base64": "aGk="}}).json()
    assert open(staged["path"], "rb").read() == b"hi"
    assert post(bridge, "/command", {"action": "files_read", "params": {"path": staged["path"]}}).json()["bytes"] == 2


def test_bridge_endpoints_also_answer_post(bridge, fake_extension):
    assert post(bridge, "/status").json()["service"] == "orbital-browser-bridge"
    assert post(bridge, "/network", {"limit": 5}).json()["events"] == []


def test_action_failure_is_reported(bridge, fake_extension):
    r = post(bridge, "/command", {"action": "boom"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False and "the page said no" in data["error"]


def test_bad_requests(bridge, fake_extension):
    assert post(bridge, "/command", {"action": "Not An Action"}).status_code == 400
    assert post(bridge, "/command", {"action": "ping", "params": [1, 2]}).status_code == 400
    r = httpx.post(bridge.url + "/command", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert get(bridge, "/nope").status_code == 404
    assert get(bridge, "/navigate").status_code == 404   # commands are POST only


def test_no_extension_gives_503_and_drops_the_command(bridge):
    t0 = time.time()
    r = post(bridge, "/command", {"action": "navigate", "params": {"url": "https://example.com"}, "timeout": 0.6})
    assert r.status_code == 503
    assert "not connected" in r.json()["error"]
    assert time.time() - t0 < 5
    # A browser that connects later must not run the stale command.
    ext = FakeExtension(bridge.url)
    ext.start()
    try:
        assert ext.ready.wait(15)
        first = ext.polls
        wait_until(lambda: ext.polls >= first + 3)          # three full polls after connecting
        assert ext.received == []
    finally:
        ext.stop()


def test_slow_command_times_out_then_result_is_kept(bridge):
    ext = FakeExtension(bridge.url, delay=1.5)
    ext.start()
    try:
        assert ext.ready.wait(15)
        r = post(bridge, "/command", {"action": "ping", "timeout": 0.5})
        assert r.status_code == 504
        cid = r.json()["id"]
        assert f"/result/{cid}" in r.json()["error"]
        assert get(bridge, f"/result/{cid}").status_code in (200, 202)
        done = wait_until(lambda: get(bridge, f"/result/{cid}").status_code == 200)
        assert done
        assert get(bridge, f"/result/{cid}").json()["result"] == {"pong": True}
    finally:
        ext.stop()


def test_wait_false_returns_an_id(bridge, fake_extension):
    r = post(bridge, "/command", {"action": "ping", "wait": False})
    assert r.status_code == 202
    cid = r.json()["id"]
    wait_until(lambda: get(bridge, f"/result/{cid}").status_code == 200)
    assert get(bridge, f"/result?id={cid}").json()["ok"] is True
    assert get(bridge, "/result/unknown123").status_code == 404


def test_long_poll_returns_as_soon_as_a_command_arrives(bridge):
    got = {}

    def poll():
        t0 = time.time()
        r = httpx.get(bridge.url + "/ext/commands", params={"wait_ms": 8000}, timeout=20)
        got["cmds"] = r.json()["commands"]
        got["s"] = time.time() - t0

    t = threading.Thread(target=poll)
    t.start()
    wait_until(lambda: bridge.state.polling_now > 0)         # the poll is parked in the bridge
    post(bridge, "/command", {"action": "ping", "wait": False})
    t.join(10)
    assert got["cmds"][0]["action"] == "ping"
    assert got["s"] < 6          # well under the 8 s the poll would otherwise wait


def test_commands_from_parallel_callers(bridge, fake_extension):
    results = []

    def one(i):
        results.append(post(bridge, "/command", {"action": "eval", "params": {"code": f"n{i}"}}).json())

    threads = [threading.Thread(target=one, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert len(results) == 12
    assert sorted(r["result"]["value"] for r in results) == sorted(f"value of n{i}" for i in range(12))


def test_two_profiles_on_one_bridge_warn(bridge, fake_extension):
    other = FakeExtension(bridge.url, instance="fake2")
    other.start()
    try:
        assert other.ready.wait(15)
        wait_until(lambda: get(bridge, "/status").json()["extension"]["instances"] == 2)
        assert "more than one" in get(bridge, "/status").json()["extension"]["warning"]
    finally:
        other.stop()


def test_clear_empties_queue_and_events(bridge):
    post(bridge, "/command", {"action": "ping", "wait": False})
    post(bridge, "/ext/events", {"events": [{"type": "page.ready", "url": "https://example.com/"}]})
    assert post(bridge, "/clear").json()["ok"]
    status = get(bridge, "/status").json()
    assert status["queue"]["waiting"] == 0 and status["capture"]["events"] == 0


# ── capture ───────────────────────────────────────────────────────

EVENTS = [
    {"type": "network.request_headers", "url": "https://api.example.com/v1/users/123456?access_token=abc&page=2",
     "method": "GET", "headers": {"Authorization": "Bearer xyz", "Cookie": "sid=1", "Accept": "application/json"}},
    {"type": "network.completed", "url": "https://api.example.com/v1/users/123456?access_token=abc&page=2",
     "method": "GET", "statusCode": 200, "resourceType": "xmlhttprequest"},
    {"type": "network.body", "url": "https://api.example.com/v1/login", "method": "POST",
     "requestBody": json.dumps({"user": "someone", "password": "hunter2"}),
     "responseBody": json.dumps({"access_token": "tok", "name": "Someone"}), "status": 200,
     "responseHeaders": {"content-type": "application/json"}},
    {"type": "network.completed", "url": "https://cdn.example.com/logo.png", "method": "GET", "statusCode": 200, "resourceType": "image"},
    {"type": "page.ready", "url": "https://www.example.com/", "title": "Example"},
]


def test_events_are_redacted(bridge):
    assert post(bridge, "/ext/events", {"events": EVENTS}).json()["accepted"] == 5
    events = get(bridge, "/events").json()["events"]
    blob = json.dumps(events)
    for secret in ("Bearer xyz", "sid=1", "hunter2", '"tok"', "access_token=abc"):
        assert secret not in blob
    assert events[0]["headers"]["Accept"] == "application/json"
    assert events[0]["headers"]["Authorization"] == "[redacted]"
    assert "page=2" in events[0]["url"]
    login = events[2]
    assert json.loads(login["responseBody"])["name"] == "Someone"
    # the capture log on disk is redacted too
    log = (bridge.settings.captures_dir / "events.jsonl").read_text()
    assert "hunter2" not in log and "Bearer xyz" not in log


def test_capture_secrets_setting_keeps_values(make_bridge):
    b = make_bridge(capture_secrets=True)
    post(b, "/ext/events", {"events": EVENTS[:1]})
    assert get(b, "/events").json()["events"][0]["headers"]["Authorization"] == "Bearer xyz"


def test_network_filters(bridge):
    post(bridge, "/ext/events", {"events": EVENTS})
    every = get(bridge, "/network").json()["events"]
    assert every[0]["type"] == "page.ready"            # newest first
    api = get(bridge, "/network", params={"api_only": 1}).json()["events"]
    assert {e["url"].split("?")[0] for e in api} == {"https://api.example.com/v1/users/123456", "https://api.example.com/v1/login"}
    cdn = get(bridge, "/network", params={"host": "cdn.example"}).json()["events"]
    assert [e["url"] for e in cdn] == ["https://cdn.example.com/logo.png"]
    assert len(get(bridge, "/network", params={"limit": 2}).json()["events"]) == 2
    assert get(bridge, "/network", params={"path": "/login"}).json()["count"] == 1


def test_endpoint_catalog(bridge):
    post(bridge, "/ext/events", {"events": EVENTS})
    data = get(bridge, "/endpoints").json()
    by_key = {f"{e['method']} {e['host']}{e['path_key']}": e for e in data["endpoints"]}
    users = by_key["GET api.example.com/v1/users/{id}"]
    assert users["count"] == 2 and users["auth_header_present"] is True
    assert users["statuses"] == [200]
    assert "page" in users["query_keys"]
    login = by_key["POST api.example.com/v1/login"]
    assert login["content_type"] == "application/json"
    assert "Someone" in login["response_sample"]
    assert not any("cdn.example.com" in k for k in by_key)
    assert get(bridge, "/endpoints", params={"host": "nothing"}).json()["count"] == 0


def test_export_writes_files(bridge):
    post(bridge, "/ext/events", {"events": EVENTS})
    data = post(bridge, "/export").json()
    full = json.loads(open(data["path"]).read())
    assert full["event_count"] == 5 and len(full["endpoints"]) == 2
    assert json.loads(open(data["catalog"]).read())["endpoints"]


def test_event_buffer_is_capped(make_bridge):
    b = make_bridge(max_events=100)
    post(b, "/ext/events", {"events": [{"type": "page.ready", "url": f"https://e.example/{i}"} for i in range(250)]})
    events = get(b, "/events", params={"limit": 1000}).json()["events"]
    assert len(events) == 100 and events[-1]["url"] == "https://e.example/249"


def test_capture_log_rolls_at_the_cap(make_bridge):
    b = make_bridge(events_log_mb=20_000 / (1024 * 1024))
    for _ in range(20):
        post(b, "/ext/events", {"events": [{"type": "page.ready", "url": "https://e.example/" + "x" * 500} for _ in range(5)]})
    log = b.settings.captures_dir / "events.jsonl"
    assert log.stat().st_size <= 20_000
    assert log.with_name("events.jsonl.1").exists()
    assert log.with_name("events.jsonl.1").stat().st_size <= 20_000


# ── staging and files ─────────────────────────────────────────────

def test_stage_raw_and_json(bridge, tmp_path):
    r = httpx.post(bridge.url + "/stage", params={"name": "../../evil name.txt"}, content=b"hello",
                   headers={"Content-Type": "application/octet-stream"})
    data = r.json()
    assert data["ok"] and data["bytes"] == 5
    path = data["path"]
    assert path.startswith(str(bridge.settings.staged_dir.resolve()))
    assert path.endswith("_evil_name.txt") and open(path, "rb").read() == b"hello"
    r = post(bridge, "/stage", {"name": "b.bin", "content_base64": base64.b64encode(b"\x00\x01").decode()})
    assert open(r.json()["path"], "rb").read() == b"\x00\x01"
    assert post(bridge, "/stage", {"name": "c.bin"}).status_code == 400


def test_stage_chunked_upload(bridge):
    def gen():
        yield b"part1-"
        yield b"part2"

    r = httpx.post(bridge.url + "/stage", params={"name": "chunked.txt"}, content=gen())
    assert open(r.json()["path"], "rb").read() == b"part1-part2"


def test_stage_size_cap(make_bridge):
    b = make_bridge(max_stage_mb=0.001)  # about 1 KB
    r = httpx.post(b.url + "/stage", params={"name": "big.bin"}, content=b"x" * 5000)
    assert r.status_code == 413
    assert not [p for p in b.settings.staged_dir.iterdir() if p.is_file()]


def test_staged_files_are_trimmed(make_bridge):
    b = make_bridge(staged_max_mb=0.01)  # about 10 KB in total
    staged = b.settings.staged_dir
    old = staged / "old.bin"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
    for i in range(4):
        httpx.post(b.url + "/stage", params={"name": f"f{i}.bin"}, content=b"y" * 4000)
    left = [p for p in staged.iterdir() if p.is_file()]
    assert not old.exists()
    assert sum(p.stat().st_size for p in left) <= 4000 * 3
    assert post(b, "/purge_staged", {"max_age_s": 0}).json()["removed"] >= 1
    assert not [p for p in staged.iterdir() if p.is_file()]


def test_files_only_inside_allowed_folders(make_bridge, tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "report.csv").write_text("a,b\n")
    outside = tmp_path / "private.txt"
    outside.write_text("no")
    b = make_bridge(read_roots=[downloads])
    listing = get(b, "/files", params={"path": str(downloads)}).json()
    assert [e["name"] for e in listing["entries"]] == ["report.csv"]
    data = post(b, "/files/read", {"path": str(downloads / "report.csv")}).json()
    assert base64.b64decode(data["content_base64"]) == b"a,b\n"
    assert post(b, "/files/read", {"path": str(outside)}).status_code == 403
    assert post(b, "/files/read", {"path": str(downloads / ".." / "private.txt")}).status_code == 403
    assert get(b, "/files", params={"path": "/"}).status_code == 403
