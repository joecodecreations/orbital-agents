"""`orbital satellite status | run | jobs | join` against a live satellite and a fake hub."""

from __future__ import annotations

import json
import tomllib

from fastapi import FastAPI, Request

from orbital.cli.main import main as orbital
from orbital.core.config import load_config
from orbital.core.secrets import Secrets
from orbital.satellite.jobs import JobManager
from orbital.satellite.server import create_app
from orbital.satellite.tracker import TrackerReporter
from satellite_helpers import ServerThread, free_port, make_cfg, write_config

TOKEN = "cli-satellite-token-0123456789"


def live_satellite(home, tmp_path):
    port = free_port()
    make_cfg(home, workdir=tmp_path)
    write_config(home, (home / "config.toml").read_text().replace("[satellite]", f"[satellite]\nport = {port}"))
    cfg = load_config(home)
    Secrets(cfg.paths).set("ORBITAL_SATELLITE_TOKEN", TOKEN)
    manager = JobManager(cfg, tracker=TrackerReporter(enabled=False))
    return ServerThread(create_app(cfg, manager), port=port), manager


def test_status_run_and_jobs(home, tmp_path, capsys):
    server, manager = live_satellite(home, tmp_path)
    with server:
        assert orbital(["satellite", "run", "echo", "hello", "from", "the", "cli", "--cwd", str(tmp_path), "--wait"]) == 0
        out = capsys.readouterr().out
        assert "hello from the cli" in out and ": done" in out
        assert orbital(["satellite", "status", "--json"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["up"] is True and status["jobs"][0]["agent"] == "echo"
        assert orbital(["satellite", "status"]) == 0
        text = capsys.readouterr().out
        assert "up at" in text and "missing" in text and "not found" in text
        assert orbital(["satellite", "jobs"]) == 0
        assert "echo" in capsys.readouterr().out
        assert orbital(["satellite", "run", "missing", "x"]) == 1
    manager.shutdown(timeout=5)
    assert orbital(["satellite", "status"]) == 1, "down once the service stops"


def test_join_from_the_cli(home, capsys):
    app = FastAPI()
    seen = []

    @app.post("/v1/satellites/join")
    async def join(request: Request):
        seen.append(request.headers.get("x-orbital-join-token"))
        return {"ok": True, "satellite_id": "test-machine", "hub_token": "issued-token", "hub_id": "h9"}

    make_cfg(home)
    with ServerThread(app) as hub:
        assert orbital(["satellite", "join", hub.url, "the-join-token"]) == 0
    assert seen == ["the-join-token"]
    assert "joined" in capsys.readouterr().out
    data = tomllib.loads((home / "config.toml").read_text())
    assert data["system"]["role"] == "satellite" and data["hub"]["url"].startswith("http://127.0.0.1:")
    assert Secrets(load_config(home).paths).get("ORBITAL_HUB_TOKEN") == "issued-token"
    assert orbital(["satellite", "join", "http://127.0.0.1:9", "x"]) == 1
