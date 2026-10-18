"""join_hub, the heartbeat (satellite and hub roles) and find_hubs, against small fake hubs."""

from __future__ import annotations

import tomllib

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orbital.core.config import load_config
from orbital.core.net import Peer
from orbital.core.secrets import Secrets
from orbital.satellite import discover
from orbital.satellite.discover import find_hubs
from orbital.satellite.heartbeat import Heartbeat, beat_once
from orbital.satellite.jobs import JobManager
from orbital.satellite.join import JoinError, join_hub, read_identity, set_config_values
from orbital.satellite.tracker import TrackerReporter
from satellite_helpers import ServerThread, make_cfg, wait_for, write_config

JOIN = "join-token-for-tests-0123456789"


class FakeHub:
    def __init__(self, reject_beats: bool = False, browser_token: str | None = None):
        self.browser_token = browser_token
        self.joins: list[dict] = []
        self.beats: list[dict] = []
        self.reject_beats = reject_beats
        self.issued = 0
        app = FastAPI()

        @app.get("/v1/hub/info")
        def info(request: Request):
            if request.headers.get("host", "").startswith("localhost"):
                return {"service": "orbital-hub", "name": "Test Hub", "machine": "hub-box", "version": "9.9", "hub_id": "h1"}
            return {"service": "something-else"}

        @app.post("/v1/satellites/join")
        async def join(request: Request):
            body = await request.json()
            if request.headers.get("x-orbital-join-token") != JOIN:
                return JSONResponse({"ok": False, "error": "bad join token"}, status_code=401)
            self.issued += 1
            self.joins.append({"headers": dict(request.headers), "body": body})
            answer = {"ok": True, "satellite_id": "test-machine", "hub_token": f"hub-token-{self.issued}", "hub_id": "h1"}
            if self.browser_token:
                answer["browser_token"] = self.browser_token
            return answer

        @app.post("/v1/satellites/heartbeat")
        async def heartbeat(request: Request):
            body = await request.json()
            self.beats.append({"auth": request.headers.get("authorization"), "body": body})
            if self.reject_beats:
                return JSONResponse({"ok": False, "error": "unknown satellite: join again"}, status_code=401)
            return {"ok": True}

        self.app = app


def test_join_hub_stores_tokens_identity_and_config(home):
    cfg = make_cfg(home)
    write_config(home, (home / "config.toml").read_text().replace('[hub]\nurl = ""', '[hub]\nurl = ""      # set by join\nport = 7700'))
    hub = FakeHub()
    with ServerThread(hub.app) as srv:
        info = join_hub(srv.url, JOIN, load_config(home))
    assert info["satellite_id"] == "test-machine" and info["hub_url"] == srv.url
    sent = hub.joins[0]["body"]
    secrets = Secrets(load_config(home).paths)
    assert sent["satellite_token"] == secrets.get("ORBITAL_SATELLITE_TOKEN")
    assert sent["machine"] == "test-machine" and sent["port"] == 7704 and sent["os"]
    assert set(sent["capabilities"]) == {"agents", "browser", "browser_port", "tracker"}
    assert "echo" in sent["capabilities"]["agents"] and "missing" not in sent["capabilities"]["agents"]
    assert secrets.get("ORBITAL_HUB_TOKEN") == "hub-token-1"
    assert read_identity(load_config(home))["satellite_id"] == "test-machine"
    text = (home / "config.toml").read_text()
    data = tomllib.loads(text)
    assert data["hub"]["url"] == srv.url and data["system"]["role"] == "satellite"
    assert data["hub"]["port"] == 7700 and data["satellite"]["bind"] == "loopback", "other keys are kept"
    assert "# set by join" in text, "comments are kept"
    assert (home / "config.resolved.json").is_file()


def test_join_takes_the_fleet_browser_token_and_rejoin_refreshes_it(home):
    cfg = make_cfg(home)
    secrets = Secrets(cfg.paths)
    secrets.set("ORBITAL_BROWSER_TOKEN", "this-machines-own-token-000000")
    hub = FakeHub(browser_token="fleet-browser-token-1111111111")
    with ServerThread(hub.app) as srv:
        first = join_hub(srv.url, JOIN, load_config(home), write_config=False)
        assert first["browser_token_changed"] is True
        assert Secrets(cfg.paths).all()["ORBITAL_BROWSER_TOKEN"] == "fleet-browser-token-1111111111"
        again = join_hub(srv.url, JOIN, load_config(home), write_config=False)
        assert again["browser_token_changed"] is False, "same token: nothing to restart"
        hub.browser_token = "fleet-browser-token-2222222222"
        third = join_hub(srv.url, JOIN, load_config(home), write_config=False)
        assert third["browser_token_changed"] is True
        assert Secrets(cfg.paths).all()["ORBITAL_BROWSER_TOKEN"] == "fleet-browser-token-2222222222"


def test_a_hub_without_a_browser_token_leaves_ours_alone(home):
    cfg = make_cfg(home)
    Secrets(cfg.paths).set("ORBITAL_BROWSER_TOKEN", "this-machines-own-token-000000")
    with ServerThread(FakeHub().app) as srv:
        info = join_hub(srv.url, JOIN, load_config(home), write_config=False)
    assert info["browser_token_changed"] is False
    assert Secrets(cfg.paths).all()["ORBITAL_BROWSER_TOKEN"] == "this-machines-own-token-000000"


def test_join_hub_with_a_wrong_token_raises(home):
    cfg = make_cfg(home)
    with ServerThread(FakeHub().app) as srv:
        with pytest.raises(JoinError, match="refused"):
            join_hub(srv.url, "wrong-token", cfg)
    with pytest.raises(JoinError, match="could not reach"):
        join_hub("http://127.0.0.1:9", JOIN, cfg, timeout=1)
    assert not Secrets(cfg.paths).get("ORBITAL_HUB_TOKEN")


def test_set_config_values_adds_missing_tables(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("# my settings\n[system]\nname = \"Lab\"  # shown in the apps\n")
    set_config_values(path, {"system.role": "satellite", "hub.url": "http://hub:7700", "system.name": "Lab 2"})
    data = tomllib.loads(path.read_text())
    assert data == {"system": {"name": "Lab 2", "role": "satellite"}, "hub": {"url": "http://hub:7700"}}
    assert "# my settings" in path.read_text() and "# shown in the apps" in path.read_text()


def test_heartbeat_reaches_the_hub(home):
    cfg = make_cfg(home)
    hub = FakeHub()
    with ServerThread(hub.app) as srv:
        join_hub(srv.url, JOIN, cfg)
        cfg = load_config(home)
        manager = JobManager(cfg, tracker=TrackerReporter(enabled=False))
        manager.submit(agent="sleeper", prompt="10", title="busy")
        beat = Heartbeat(cfg, manager, interval=0.2)
        beat.start()
        try:
            assert wait_for(lambda: len(hub.beats) >= 2, 10)
        finally:
            beat.stop()
            manager.shutdown(timeout=5)
    first = hub.beats[0]
    assert first["auth"] == "Bearer hub-token-1"
    assert first["body"]["satellite_id"] == "test-machine"
    assert "echo" in first["body"]["capabilities"]["agents"]
    assert first["body"]["running_jobs"][0]["title"] == "busy"
    assert (cfg.paths.satellite_data / "heartbeat.json").is_file()


def test_beat_once_without_join_does_nothing(home):
    cfg = make_cfg(home)
    result = beat_once(cfg)
    assert result["ok"] is False and "no hub" in result["detail"]


def test_hub_machine_joins_its_own_hub(home):
    hub = FakeHub()
    with ServerThread(hub.app) as srv:
        cfg = make_cfg(home, extra="")
        write_config(home, (home / "config.toml").read_text()
                     .replace('role = "satellite"', 'role = "hub"')
                     .replace('[hub]\nurl = ""', f'[hub]\nurl = ""\nport = {srv.port}'))
        cfg = load_config(home)
        Secrets(cfg.paths).set("ORBITAL_JOIN_TOKEN", JOIN)
        beat = Heartbeat(cfg, None, interval=0.2)
        beat.tick()
        assert len(hub.joins) == 1 and hub.joins[0]["body"]["ip"] == "127.0.0.1"
        assert len(hub.beats) == 1 and hub.beats[0]["auth"] == "Bearer hub-token-1"
        assert tomllib.loads((home / "config.toml").read_text())["system"]["role"] == "hub", "the hub keeps its role"
        # The hub forgets us: the next tick joins again, then beats with the new token.
        hub.reject_beats = True
        beat.tick()
        assert read_identity(cfg) == {}
        hub.reject_beats = False
        beat.tick()
        assert len(hub.joins) == 2 and hub.beats[-1]["auth"] == "Bearer hub-token-2"


def test_find_hubs_probes_peers(home):
    with ServerThread(FakeHub().app) as srv:
        peers = [
            Peer(name="hub-box", dns="localhost", ip="", os="macOS", online=True),
            Peer(name="other-box", dns="", ip="127.0.0.1", os="linux", online=True),
            Peer(name="sleeping", dns="localhost", ip="127.0.0.1", os="linux", online=False),
            Peer(name="nowhere", dns="no-such-host.invalid", ip="", os="linux", online=True),
        ]
        hubs = find_hubs(srv.port, peers=peers, timeout=1.5)
    assert len(hubs) == 1
    hub = hubs[0]
    assert hub["url"] == f"http://localhost:{srv.port}" and hub["hub_id"] == "h1" and hub["name"] == "Test Hub"
    assert hub["host"] == "hub-box"


def test_find_hubs_uses_tailscale_peers(home, monkeypatch):
    with ServerThread(FakeHub().app) as srv:
        monkeypatch.setattr(discover, "tailscale_peers",
                            lambda include_self=False: [Peer(name="hub-box", dns="localhost", ip="", os="macOS", online=True)])
        assert [h["machine"] for h in find_hubs(srv.port)] == ["hub-box"]
    monkeypatch.setattr(discover, "tailscale_peers", lambda include_self=False: [])
    assert find_hubs(7700) == []
