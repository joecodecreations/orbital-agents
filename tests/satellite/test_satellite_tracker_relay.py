"""Jobs on a machine without orbital-track reach the tracker through the hub's /v1/tracker/command relay."""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orbital.core.config import load_config
from orbital.core.secrets import Secrets
from orbital.satellite.jobs import JobManager
from orbital.satellite.tracker import HubTrackerRelay, TrackerReporter
from satellite_helpers import ServerThread, free_port, make_cfg, wait_for, write_config

HUB_TOKEN = "relay-hub-token-0123456789"
NO_CLI = "/nonexistent/orbital-track"


def fake_tracker_hub(status: int = 200):
    app = FastAPI()
    seen: list[dict] = []

    @app.post("/v1/tracker/command")
    async def command(request: Request):
        seen.append({"auth": request.headers.get("authorization"), "body": await request.json()})
        return JSONResponse({"ok": status < 300}, status_code=status)

    return app, seen


def satellite_cfg(home, hub_url: str, role: str = "satellite"):
    make_cfg(home)
    text = (home / "config.toml").read_text().replace('url = ""', f'url = "{hub_url}"').replace('role = "satellite"', f'role = "{role}"')
    write_config(home, text)
    cfg = load_config(home)
    Secrets(cfg.paths).set("ORBITAL_HUB_TOKEN", HUB_TOKEN)
    return cfg


def reporter(cfg, **relay_kw):
    relay = HubTrackerRelay(cfg.paths.home, **relay_kw)
    return TrackerReporter(bin_path=NO_CLI, cfg=cfg, relay=relay), relay


def test_job_without_cli_is_reported_through_the_hub(home):
    app, seen = fake_tracker_hub()
    with ServerThread(app) as hub:
        cfg = satellite_cfg(home, hub.url)
        tracker, relay = reporter(cfg)
        m = JobManager(cfg, tracker=tracker)
        job = m.submit(agent="echo", prompt="tracked over the relay", title="Relay job")
        done = m.wait(job["id"], 10)
        assert done["status"] == "done" and done["tracker_id"]
        assert wait_for(lambda: len(seen) >= 2, 10)
        assert relay.flush(5)
    start, finish = seen[0], seen[1]
    assert start["auth"] == finish["auth"] == f"Bearer {HUB_TOKEN}"
    assert start["body"]["op"] == "start" and finish["body"]["op"] == "done"
    assert start["body"]["id"] == finish["body"]["id"] == done["tracker_id"]
    assert start["body"]["agent"] == "echo" and start["body"]["title"] == "Relay job"
    assert start["body"]["machine"] == finish["body"]["machine"] == "test-machine"
    assert "tracked over the relay" in start["body"]["details"]
    assert finish["body"]["summary"].startswith("Finished") and "tracked over the relay" in finish["body"]["summary"]
    assert start["body"]["sentAt"].endswith("Z")
    assert relay.sent == 2 and relay.dropped == 0


def test_the_default_job_manager_uses_the_relay(home, monkeypatch):
    monkeypatch.setenv("ORBITAL_TRACK_BIN", NO_CLI)   # no CLI on this machine, reporting on
    app, seen = fake_tracker_hub()
    with ServerThread(app) as hub:
        cfg = satellite_cfg(home, hub.url)
        m = JobManager(cfg)
        done = m.wait(m.submit(agent="echo", prompt="default wiring")["id"], 10)
        assert done["tracker_id"]
        assert wait_for(lambda: [s["body"]["op"] for s in seen] == ["start", "done"], 10)


def test_hub_machine_without_a_tracker_skips_silently(home):
    app, seen = fake_tracker_hub()
    with ServerThread(app) as hub:
        cfg = satellite_cfg(home, hub.url, role="hub")
        tracker, relay = reporter(cfg)
        assert relay.target() is None
        m = JobManager(cfg, tracker=tracker)
        done = m.wait(m.submit(agent="echo", prompt="on the hub")["id"], 10)
        time.sleep(0.3)
    assert done["status"] == "done" and done["tracker_id"] is None
    assert seen == []


def test_satellite_not_joined_skips(home):
    cfg = satellite_cfg(home, "")
    tracker, relay = reporter(cfg)
    done = JobManager(cfg, tracker=tracker)
    record = done.wait(done.submit(agent="echo", prompt="no hub")["id"], 10)
    assert record["status"] == "done" and record["tracker_id"] is None


def test_unreachable_hub_never_delays_the_job_and_catches_up_in_order(home):
    port = free_port()
    cfg = satellite_cfg(home, f"http://127.0.0.1:{port}")
    tracker, relay = reporter(cfg, cooldown=0.2, timeout=1.0)
    m = JobManager(cfg, tracker=tracker)
    started = time.monotonic()
    first = m.wait(m.submit(agent="echo", prompt="one")["id"], 10)
    second = m.wait(m.submit(agent="echo", prompt="two")["id"], 10)
    assert time.monotonic() - started < 3, "the job never waits for the hub"
    assert first["status"] == second["status"] == "done"
    app, seen = fake_tracker_hub()
    with ServerThread(app, port=port):
        assert relay.flush(15)
    ops = [(s["body"]["op"], s["body"]["id"]) for s in seen]
    assert ops == [("start", first["tracker_id"]), ("done", first["tracker_id"]),
                   ("start", second["tracker_id"]), ("done", second["tracker_id"])]


def test_rejected_commands_are_dropped_and_logged_once(home, caplog):
    import logging

    app, seen = fake_tracker_hub(status=401)
    log = logging.getLogger("orbital.test-relay")
    log.setLevel(logging.DEBUG)
    with ServerThread(app) as hub:
        cfg = satellite_cfg(home, hub.url)
        relay = HubTrackerRelay(cfg.paths.home, log=log)
        tracker = TrackerReporter(bin_path=NO_CLI, cfg=cfg, relay=relay)
        m = JobManager(cfg, tracker=tracker)
        with caplog.at_level(logging.DEBUG, logger="orbital.test-relay"):
            for prompt in ("a", "b"):
                assert m.wait(m.submit(agent="echo", prompt=prompt)["id"], 10)["status"] == "done"
            assert wait_for(lambda: len(seen) == 4, 10)
            assert relay.flush(5)
    assert relay.dropped == 4 and relay.sent == 0
    infos = [r for r in caplog.records if r.levelno == logging.INFO and "refused" in r.getMessage()]
    assert len(infos) == 1, "a refusal is logged once at info, then at debug"


def test_the_cli_wins_when_it_is_installed(home, tmp_path):
    import stat

    script = tmp_path / "orbital-track"
    script.write_text('#!/bin/sh\nif [ "$1" = "start" ]; then echo "cli01"; fi\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    app, seen = fake_tracker_hub()
    with ServerThread(app) as hub:
        cfg = satellite_cfg(home, hub.url)
        relay = HubTrackerRelay(cfg.paths.home)
        m = JobManager(cfg, tracker=TrackerReporter(bin_path=str(script), cfg=cfg, relay=relay))
        done = m.wait(m.submit(agent="echo", prompt="cli path")["id"], 10)
        time.sleep(0.3)
    assert done["tracker_id"] == "cli01" and seen == [], "the CLI relays by itself; never both"


def test_reporting_off_means_no_relay_either(home, monkeypatch):
    monkeypatch.setenv("ORBITAL_TRACK_BIN", "off")
    app, seen = fake_tracker_hub()
    with ServerThread(app) as hub:
        cfg = satellite_cfg(home, hub.url)
        m = JobManager(cfg)
        assert m.wait(m.submit(agent="echo", prompt="quiet")["id"], 10)["tracker_id"] is None
        time.sleep(0.3)
    assert seen == []
