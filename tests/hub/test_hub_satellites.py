"""Satellites: join, heartbeat and offline timing, the agent relay, the browser relay."""

from __future__ import annotations

import json
import stat

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hub_helpers import AUTH, BROWSER, JOIN, SATELLITE, Server, fake_satellite, joined_satellite, make_client


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def join_body(**extra) -> dict:
    body = {"name": "Laptop", "machine": "laptop", "host": "laptop.example", "ip": "127.0.0.1", "port": 17777,
            "os": "macos", "satellite_token": SATELLITE, "version": "0.1.0",
            "capabilities": {"agents": ["claude"], "browser": True, "browser_port": 17778, "tracker": True}}
    body.update(extra)
    return body


def test_join_needs_the_join_token(ports):
    client, hub = make_client()
    assert client.post("/v1/satellites/join", json=join_body()).status_code == 401
    wrong = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": "wrong-token-xxxxxxxx"}, json=join_body())
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "unauthorized"
    assert client.get("/v1/satellites", headers=AUTH).json()["satellites"] == []


def test_join_refused_when_hub_has_no_join_token(ports, monkeypatch):
    monkeypatch.delenv("ORBITAL_JOIN_TOKEN")
    client, hub = make_client()
    assert client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": ""}, json=join_body()).status_code == 401


def test_join_issues_a_hub_token_and_rejoin_keeps_the_id(ports):
    client, hub = make_client()
    first = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body())
    assert first.status_code == 200, first.text
    answer = first.json()
    assert answer["satellite_id"] == "laptop"
    assert answer["hub_id"] == hub.hub_id
    assert len(answer["hub_token"]) >= 32
    stored = (hub.paths.hub_data / "satellites.json")
    assert answer["hub_token"] not in stored.read_text(), "the hub token is stored hashed"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    listing = client.get("/v1/satellites", headers=AUTH).json()["satellites"]
    assert [row["id"] for row in listing] == ["laptop"]
    assert listing[0]["status"] == "online"
    assert listing[0]["capabilities"]["agents"] == ["claude"]
    assert "satellite_token" not in listing[0] and "hub_token_hash" not in listing[0]
    again = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body(port=17779)).json()
    assert again["satellite_id"] == "laptop"
    assert again["hub_token"] != answer["hub_token"]
    beat_old = client.post("/v1/satellites/heartbeat", headers={"Authorization": f"Bearer {answer['hub_token']}"}, json={"satellite_id": "laptop"})
    assert beat_old.status_code == 401, "joining again replaces the old hub token"
    other = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body(machine="Laptop!", name="Other")).json()
    assert other["satellite_id"] == "laptop-2"


def test_join_validates_the_body_and_the_callback_address(ports):
    client, hub = make_client()
    headers = {"X-Orbital-Join-Token": JOIN}
    assert client.post("/v1/satellites/join", headers=headers, json=join_body(port="x")).status_code == 400
    assert client.post("/v1/satellites/join", headers=headers, json=join_body(satellite_token="short")).status_code == 400
    assert client.post("/v1/satellites/join", headers=headers, json=join_body(machine="", name="")).status_code == 400
    outside = client.post("/v1/satellites/join", headers=headers, json=join_body(ip="203.0.113.9"))
    assert outside.status_code == 400
    assert "allow_networks" in outside.json()["error"]
    from_peer = client.post("/v1/satellites/join", headers=headers, json=join_body(ip=""))
    assert from_peer.status_code == 200, "no ip: the address the join came from"
    assert client.get("/v1/satellites/laptop", headers=AUTH).json()["ip"] == "127.0.0.1"


def test_heartbeat_keeps_it_online_and_three_missed_beats_mark_it_offline(ports):
    clock = Clock()
    client, hub = make_client(clock=clock)
    token = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body(heartbeat_seconds=10)).json()["hub_token"]
    beat = {"Authorization": f"Bearer {token}"}

    def status() -> str:
        return client.get("/v1/satellites/laptop", headers=AUTH).json()["status"]

    assert status() == "online"
    clock.now += 29
    assert status() == "online"
    clock.now += 2
    assert status() == "offline"
    jobs = [{"id": "j1", "status": "running", "agent": "claude", "title": "Fix tests"}]
    res = client.post("/v1/satellites/heartbeat", headers=beat, json={"satellite_id": "laptop", "running_jobs": jobs,
                                                                        "capabilities": {"agents": ["claude", "codex"]}})
    assert res.status_code == 200, res.text
    row = client.get("/v1/satellites/laptop", headers=AUTH).json()
    assert row["status"] == "online"
    assert row["running_jobs"] == 1
    assert row["capabilities"]["agents"] == ["claude", "codex"]
    assert client.post("/v1/satellites/heartbeat", headers={"Authorization": "Bearer nope"}, json={}).status_code == 401
    assert client.post("/v1/satellites/heartbeat", headers=beat, json={"satellite_id": "someone-else"}).status_code == 401
    assert client.post("/v1/satellites/heartbeat", headers=AUTH, json={"satellite_id": "laptop"}).status_code == 401, \
        "the automation token is not a satellite"


def test_heartbeat_carries_system_metrics_to_the_listing(ports):
    client, hub = make_client()
    token = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body()).json()["hub_token"]
    beat = {"Authorization": f"Bearer {token}"}
    metrics = {
        "cpus": 8,
        "load": [1.5, 1.2, 0.9],
        "memory": {"total": 16_000_000_000, "used": 10_000_000_000, "percent": 62.5},
        "disks": [{"mount": "/", "total": 500_000_000_000, "used": 250_000_000_000, "free": 250_000_000_000, "percent": 50.0}],
        "uptime_s": 86400.0,
        "time": 1_000_000.0,
    }
    res = client.post("/v1/satellites/heartbeat", headers=beat, json={"satellite_id": "laptop", "system_metrics": metrics})
    assert res.status_code == 200, res.text
    row = client.get("/v1/satellites/laptop", headers=AUTH).json()
    sm = row["system_metrics"]
    assert sm["cpus"] == 8
    assert sm["load"] == [1.5, 1.2, 0.9]
    assert sm["memory"]["percent"] == 62.5
    assert sm["disks"][0]["mount"] == "/"
    assert sm["uptime_s"] == 86400.0
    listing = client.get("/v1/satellites", headers=AUTH).json()["satellites"]
    assert listing[0]["system_metrics"]["cpus"] == 8
    browser = {"state": "connected", "bridge_up": True, "extension_connected": True, "extension_version": "0.1.0",
               "last_poll_age_s": 1.2, "bind": "tailscale", "hub_can_drive": True, "checked_at": 1_000_000.0}
    client.post("/v1/satellites/heartbeat", headers=beat, json={"satellite_id": "laptop", "browser_status": browser})
    assert client.get("/v1/satellites/laptop", headers=AUTH).json()["browser_status"] == browser
    client.post("/v1/satellites/heartbeat", headers=beat, json={"satellite_id": "laptop", "browser_status": {"state": "weird", "bind": "x"}})
    odd = client.get("/v1/satellites/laptop", headers=AUTH).json()["browser_status"]
    assert odd["state"] == "bridge_down" and odd["bind"] == "loopback"


def test_a_pairing_code_signs_the_app_in_once(ports):
    client, hub = make_client()
    assert client.post("/v1/pair/new", json={}).status_code == 401, "only the app or the CLI may make a code"
    made = client.post("/v1/pair/new", headers=AUTH, json={})
    assert made.status_code == 200, made.text
    code = made.json()["code"]
    paired = client.post("/v1/pair", json={"code": code})
    assert paired.status_code == 200, paired.text
    token = paired.json()["token"]
    assert client.get("/v1/catalog", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/v1/pair", json={"code": code}).status_code == 401, "a code works once"
    assert client.post("/v1/pair", json={"code": "made-up"}).status_code == 401
    assert client.post("/v1/pair", json={}).status_code == 401


def test_a_pairing_code_expires():
    from orbital.hub.auth import PairCodes

    clock = Clock()
    codes = PairCodes(ttl=600, clock=clock)
    code, _ = codes.new("owner")
    clock.now += 601
    assert codes.redeem(code) is None


def test_remove_by_id_or_machine_name(ports):
    client, hub = make_client()
    client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body())
    assert client.delete("/v1/satellites/LAPTOP", headers=AUTH).json()["id"] == "laptop"
    assert client.get("/v1/satellites", headers=AUTH).json()["satellites"] == []
    assert client.delete("/v1/satellites/laptop", headers=AUTH).status_code == 404
    assert client.delete("/v1/satellites/laptop").status_code == 401


def test_agent_run_relay_against_a_fake_satellite(ports):
    app, state = fake_satellite()
    client, hub = make_client()
    with Server(app) as sat, joined_satellite(client, sat.port):
        ping = client.get("/v1/satellites/build-box/ping", headers=AUTH)
        assert ping.status_code == 200 and ping.json()["health"]["service"] == "orbital-satellite"
        res = client.post("/v1/satellites/build-box/agents/run", headers=AUTH,
                          json={"agent": "claude", "prompt": "Make the tests pass", "cwd": "~/code/site", "title": "Fix tests"})
        assert res.status_code == 200, res.text
        assert res.json()["job_id"] == "j1" and res.json()["satellite_id"] == "build-box"
        assert state["runs"][0] == {"agent": "claude", "prompt": "Make the tests pass", "cwd": "~/code/site", "title": "Fix tests", "by": "hub"}
        assert state["auth"][-1] == f"Bearer {SATELLITE}", "the hub calls the satellite with its own satellite token"
        by_name = client.post("/v1/satellites/Build%20box/agents/run", headers=AUTH, json={"prompt": "again"})
        assert by_name.status_code == 200, by_name.text
        assert "agent" not in state["runs"][1], "no agent named: the machine's own default"
        missing = client.post("/v1/satellites/build-box/agents/run", headers=AUTH, json={"agent": "gemini", "prompt": "hi"})
        assert missing.status_code == 400 and "isn't available" in missing.json()["error"]
        assert client.post("/v1/satellites/build-box/agents/run", headers=AUTH, json={"prompt": ""}).status_code == 400
        too_long = client.post("/v1/satellites/build-box/agents/run", headers=AUTH, json={"prompt": "x" * 100_001})
        assert too_long.status_code == 413 and "100,000" in too_long.json()["error"]
        assert client.post("/v1/satellites/nowhere/agents/run", headers=AUTH, json={"prompt": "x"}).status_code == 404

        state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": "All green."})
        job = client.get("/v1/satellites/build-box/agents/jobs/j1", headers=AUTH).json()
        assert job["status"] == "done" and job["output_tail"] == "All green." and job["satellite_id"] == "build-box"
        log = client.get("/v1/satellites/build-box/agents/jobs/j1/log", headers=AUTH).json()
        assert log["log"].startswith("line one")
        text = client.get("/v1/satellites/build-box/agents/jobs/j1/log?format=text", headers=AUTH)
        assert text.headers["content-type"].startswith("text/plain") and text.text == "line one\nline two\n"
        assert client.post("/v1/satellites/build-box/agents/jobs/j2/cancel", headers=AUTH).json()["status"] == "cancelled"
        assert state["cancelled"] == ["j2"]
        unknown = client.get("/v1/satellites/build-box/agents/jobs/j9", headers=AUTH)
        assert unknown.status_code == 404
        assert client.get("/v1/satellites/build-box/agents/jobs/..%2Fx", headers=AUTH).status_code in (400, 404)

        everything = client.get("/v1/agents/jobs", headers=AUTH).json()
        assert {job["id"] for job in everything["jobs"]} == {"j1", "j2"}
        assert all(job["machine"] == "build-box" for job in everything["jobs"])
        assert everything["satellites"][0]["source"] == "live"
        assert client.get("/v1/satellites/build-box/agents/jobs", headers=AUTH).json()["jobs"]

        state["busy"] = True
        busy = client.post("/v1/satellites/build-box/agents/run", headers=AUTH, json={"prompt": "one more"})
        assert busy.status_code == 429
        assert busy.json()["satellite"]["detail"] == "busy"

    # The satellite is gone: the aggregate falls back to what its last answer said.
    cached = client.get("/v1/agents/jobs", headers=AUTH).json()
    assert cached["satellites"][0]["source"] == "cached"
    assert {job["id"] for job in cached["jobs"]} == {"j1", "j2"}
    down = client.get("/v1/satellites/build-box/agents/jobs/j1", headers=AUTH)
    assert down.status_code == 502 and down.json()["code"] == "unavailable"


def test_a_satellite_that_refuses_the_hub_token_is_not_a_401_for_the_app(ports):
    app, state = fake_satellite(token="some-other-token-0000000000")
    client, hub = make_client()
    with Server(app) as sat, joined_satellite(client, sat.port):
        res = client.post("/v1/satellites/build-box/agents/run", headers=AUTH, json={"prompt": "hi"})
        assert res.status_code == 502
        assert "join it again" in res.json()["error"]


def fake_bridge() -> tuple[FastAPI, list]:
    app = FastAPI()
    calls: list = []

    @app.post("/command")
    async def command(request: Request):
        if request.headers.get("authorization") != f"Bearer {BROWSER}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        calls.append(body)
        return {"ok": True, "result": {"tabs": [{"id": 1, "url": "https://example.com"}]} if body["action"] == "tabs" else {"done": True}}

    return app, calls


def test_browser_relay_against_a_fake_bridge(ports):
    bridge, calls = fake_bridge()
    client, hub = make_client()
    with Server(bridge) as br, joined_satellite(client, 17000, browser=True, browser_port=br.port):
        tabs = client.post("/v1/satellites/build-box/browser/tabs", headers=AUTH, json={})
        assert tabs.status_code == 200, tabs.text
        assert tabs.json()["result"]["tabs"][0]["url"] == "https://example.com"
        nav = client.post("/v1/satellites/build-box/browser/navigate", headers=AUTH, json={"params": {"url": "https://example.org"}})
        assert nav.status_code == 200
        flat = client.post("/v1/satellites/build-box/browser/click", headers=AUTH, json={"selector": "#go", "_timeout": 12})
        assert flat.status_code == 200
        assert calls == [{"action": "tabs", "params": {}}, {"action": "navigate", "params": {"url": "https://example.org"}},
                         {"action": "click", "params": {"selector": "#go"}, "timeout": 12}]
        assert client.post("/v1/satellites/build-box/browser/Bad!", headers=AUTH, json={}).status_code in (400, 404)
        assert client.post("/v1/satellites/build-box/browser/tabs", json={}).status_code == 401


def test_browser_relay_needs_a_bridge_and_the_token(ports, monkeypatch):
    client, hub = make_client()
    with joined_satellite(client, 17000, browser=False):
        res = client.post("/v1/satellites/build-box/browser/tabs", headers=AUTH, json={})
        assert res.status_code == 400 and "browser bridge" in res.json()["error"]
    client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN},
                json=join_body(capabilities={"agents": [], "browser": True, "browser_port": 17001}))
    monkeypatch.delenv("ORBITAL_BROWSER_TOKEN")
    res = client.post("/v1/satellites/laptop/browser/tabs", headers=AUTH, json={})
    assert res.status_code == 503 and res.json()["code"] == "not_configured"


def test_satellite_events_reach_the_live_socket(ports):
    client, hub = make_client()
    with client, client.websocket_connect("/v1/live", headers=AUTH) as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body())
        frame = ws.receive_json()
        assert frame["type"] == "satellites" and frame["satellites"][0]["id"] == "laptop"
        assert "satellite_token" not in json.dumps(frame)


def test_a_joined_satellite_may_use_the_relays_but_not_the_data_api(ports):
    app, state = fake_satellite()
    bridge, calls = fake_bridge()
    client, hub = make_client()
    with Server(app) as sat, Server(bridge) as br, joined_satellite(client, sat.port, browser=True, browser_port=br.port) as joined:
        machine = {"Authorization": f"Bearer {joined['hub_token']}"}
        assert [row["id"] for row in client.get("/v1/satellites", headers=machine).json()["satellites"]] == ["build-box"]
        assert client.post("/v1/satellites/build-box/agents/run", headers=machine, json={"prompt": "hi"}).status_code == 200
        assert client.get("/v1/agents/jobs", headers=machine).status_code == 200
        assert client.post("/v1/satellites/build-box/browser/tabs", headers=machine, json={}).status_code == 200
        assert client.delete("/v1/satellites/build-box", headers=machine).status_code == 401, "removing machines is for the app and CLI"
        for path in ("/v1/catalog", "/v1/alerts", "/v1/sync", "/v1/desk"):
            assert client.get(path, headers=machine).status_code == 401, path


def test_a_satellite_may_post_alerts_stamped_with_its_machine(ports):
    client, hub = make_client()
    token = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body()).json()["hub_token"]
    machine = {"Authorization": f"Bearer {token}"}
    alert = {"id": "feed-20260101-abc", "kind": "event", "title": "Self-heal: restarted satellite", "body": "It was not answering."}
    res = client.post("/v1/alerts", headers=machine, json={"alerts": [alert, {**alert, "id": "named", "title": "Laptop: disk full"}]})
    assert res.status_code == 200, res.text
    assert res.json()["alert_ids"] == ["laptop-feed-20260101-abc", "laptop-named"], "ids kept apart from other machines'"
    stored = {row["id"]: row for row in client.get("/v1/alerts", headers=AUTH).json()["alerts"]}
    first = stored["laptop-feed-20260101-abc"]
    assert first["title"] == "Laptop: Self-heal: restarted satellite"
    assert first["machine"] == "laptop" and first["source"] == "satellite"
    assert first["user_info"] == {"machine": "laptop", "satellite_id": "laptop"}
    assert stored["laptop-named"]["title"] == "Laptop: disk full", "a title that names the machine is left alone"
    from_feed = client.post("/v1/alerts", headers=AUTH, json={"alerts": [alert]})
    assert from_feed.json()["alert_ids"] == ["feed-20260101-abc"], "feeds and the CLI are not stamped"
    for method, path in (("put", "/v1/sections/x"), ("post", "/v1/sections/x/ops"), ("post", "/v1/media"), ("post", "/v1/alerts/ack"),
                         ("post", "/v1/prefs")):
        assert getattr(client, method)(path, headers=machine, json={}).status_code == 401, path
    assert client.post("/v1/alerts", headers={"Authorization": "Bearer nope"}, json={"alerts": [alert]}).status_code == 401


def test_join_hands_out_the_fleet_browser_token(ports, monkeypatch):
    client, hub = make_client()
    answer = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body()).json()
    assert answer["browser_token"] == BROWSER
    monkeypatch.delenv("ORBITAL_BROWSER_TOKEN")
    made = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body()).json()["browser_token"]
    assert made and made != BROWSER
    from orbital.core.secrets import Secrets

    assert Secrets(hub.paths).all()["ORBITAL_BROWSER_TOKEN"] == made, "made once and kept in the hub's secrets.env"
    again = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=join_body()).json()["browser_token"]
    assert again == made


def test_a_bridge_with_another_browser_token_says_so(ports, monkeypatch):
    bridge, calls = fake_bridge()
    monkeypatch.setenv("ORBITAL_BROWSER_TOKEN", "a-different-browser-token-000000")
    client, hub = make_client()
    with Server(bridge) as br, joined_satellite(client, 17000, browser=True, browser_port=br.port):
        res = client.post("/v1/satellites/build-box/browser/tabs", headers=AUTH, json={})
        assert res.status_code == 502
        assert res.json()["error"] == "browser token mismatch — run `orbital satellite join` again on build-box"


def test_a_prompt_at_the_shared_limit_goes_through(ports):
    from orbital.core.limits import MAX_PROMPT_CHARS

    app, state = fake_satellite()
    client, hub = make_client()
    with Server(app) as sat, joined_satellite(client, sat.port):
        res = client.post("/v1/satellites/build-box/agents/run", headers=AUTH, json={"prompt": "x" * MAX_PROMPT_CHARS})
        assert res.status_code == 200 and len(state["runs"][0]["prompt"]) == MAX_PROMPT_CHARS


def test_pull_join_nulls_callback_ip_and_two_pull_sats_share_no_url(ports):
    """Tunnel remotes must not all register as 127.0.0.1 with a dialable base_url."""
    from orbital.hub.satellites import Registry

    client, hub = make_client()
    headers = {"X-Orbital-Join-Token": JOIN}
    a = client.post("/v1/satellites/join", headers=headers,
                    json=join_body(machine="mini", name="Mini", ip="127.0.0.1", reachability="pull")).json()
    b = client.post("/v1/satellites/join", headers=headers,
                    json=join_body(machine="laptop", name="Laptop", ip="127.0.0.1", reachability="pull",
                                   satellite_token=SATELLITE + "-b")).json()
    assert a["reachability"] == "pull" and b["reachability"] == "pull"
    assert "control" in a and a["control"]["websocket"] == "/v1/satellites/control"
    listing = client.get("/v1/satellites", headers=AUTH).json()["satellites"]
    by_id = {row["id"]: row for row in listing}
    assert by_id["mini"]["ip"] == "" and by_id["laptop"]["ip"] == ""
    assert by_id["mini"]["reachability"] == "pull"
    row_a = hub.satellites.get("mini")
    row_b = hub.satellites.get("laptop")
    assert row_a["ip"] == "" and row_b["ip"] == ""
    try:
        Registry.base_url(row_a)
        assert False, "pull satellites must not expose a callback URL"
    except ValueError as exc:
        assert "pull" in str(exc)
    assert Registry.browser_url(row_a) is None
    # Relays refuse clearly until the control channel exists.
    res = client.post("/v1/satellites/mini/agents/run", headers=AUTH, json={"prompt": "hi"})
    assert res.status_code == 503
    assert "pull" in res.json()["error"]


def test_callback_join_still_rejects_public_ips(ports):
    client, hub = make_client()
    headers = {"X-Orbital-Join-Token": JOIN}
    outside = client.post("/v1/satellites/join", headers=headers, json=join_body(ip="203.0.113.9", reachability="callback"))
    assert outside.status_code == 400
    assert "allow_networks" in outside.json()["error"]
