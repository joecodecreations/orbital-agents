"""Helpers for the hub tests: a temp ORBITAL_HOME, free ports, small fake servers in a thread."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orbital.core.config import load_config

AUTOMATION = "test-automation-token-0123456789"
JOIN = "test-join-token-0123456789"
SATELLITE = "test-satellite-token-0123456789"
BROWSER = "test-browser-token-0123456789"
SECRET_ENV = ("ORBITAL_SATELLITE_TOKEN", "ORBITAL_HUB_TOKEN", "ORBITAL_JOIN_TOKEN", "ORBITAL_AUTOMATION_TOKEN",
              "ORBITAL_HUB_USERNAME", "ORBITAL_HUB_PASSWORD", "ORBITAL_BROWSER_TOKEN", "XAI_API_KEY", "FISH_API_KEY",
              "APNS_KEY_ID", "APNS_TEAM_ID", "ORBITAL_HUB_URL", "ORBITAL_STARTUP_WAIT")
AUTH = {"Authorization": f"Bearer {AUTOMATION}"}
LOCAL = ("127.0.0.1", 50000)


def free_port() -> int:
    """A port the OS hands out (never the 7700–7704 range the real install uses)."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if not 7700 <= port <= 7704:
            return port


def write_config(home: Path, *, hub_port: int | None = None, tracker_port: int | None = None,
                 satellite_port: int | None = None, extra: str = "") -> dict:
    ports = {
        "hub": hub_port or free_port(),
        "tracker": tracker_port or free_port(),
        "satellite": satellite_port or free_port(),
        "browser": free_port(),
    }
    (home / "config.toml").write_text(f"""
[system]
role = "hub"
machine_name = "test-hub"

[hub]
port = {ports['hub']}

[tracker]
port = {ports['tracker']}

[satellite]
port = {ports['satellite']}
heartbeat_seconds = 10

[browser]
bridge_port = {ports['browser']}

[logging]
level = "DEBUG"
{extra}
""", encoding="utf-8")
    return ports


def section_body(section_id: str, **extra) -> dict:
    body = {
        "schema": "orbital.section.v1",
        "id": section_id,
        "title": extra.pop("title", section_id),
        "icon": "square.dashed",
        "order": extra.pop("order", 10),
        "layout": extra.pop("layout", "page"),
        "blocks": extra.pop("blocks", []),
        "tabs": extra.pop("tabs", []),
        "nodes": extra.pop("nodes", []),
    }
    body.update(extra)
    return body


def make_client(*, client=LOCAL, background: bool = False, clock=None, **kwargs):
    """(TestClient, hub) for a hub built from the temp home's config. TestClient is imported here, not at
    module level, so its import-time deprecation warning lands inside a test where it is filtered."""
    from fastapi.testclient import TestClient

    from orbital.hub import create_app

    cfg = load_config()
    app = create_app(cfg, background=background, **({"clock": clock} if clock else {}))
    return TestClient(app, client=client, **kwargs), app.state.hub


class Server:
    """A FastAPI app served by uvicorn on 127.0.0.1:<free port> in a thread."""

    def __init__(self, app, port: int | None = None) -> None:
        self.port = port or free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", access_log=False, lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Server":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def fake_satellite(token: str = SATELLITE) -> tuple[FastAPI, dict]:
    """The satellite service API, in memory. `state` records what the hub sent."""
    app = FastAPI()
    state: dict = {"runs": [], "jobs": {}, "cancelled": [], "auth": []}

    def ok(request: Request) -> bool:
        state["auth"].append(request.headers.get("authorization"))
        return request.headers.get("authorization") == f"Bearer {token}"

    @app.get("/v1/health")
    async def health():
        return {"service": "orbital-satellite", "machine": "fake", "version": "0", "agents": ["claude", "echo"]}

    @app.post("/v1/agents/run")
    async def run(request: Request):
        if not ok(request):
            return JSONResponse({"error": "unauthorized", "detail": "unauthorized"}, status_code=401)
        body = await request.json()
        state["runs"].append(body)
        if state.get("busy"):
            return JSONResponse({"error": "2 jobs are already waiting", "detail": "busy"}, status_code=429)
        job_id = f"j{len(state['runs'])}"
        state["jobs"][job_id] = {"id": job_id, "agent": body.get("agent"), "title": body.get("title"), "status": "running",
                                 "exit_code": None, "started_at": "2026-01-01T00:00:00Z", "output_tail": "", "tracker_id": f"tr{job_id}"}
        return {"job_id": job_id, "status": "queued", "agent": body.get("agent"), "machine": "fake"}

    @app.get("/v1/agents/jobs")
    async def jobs(request: Request):
        if not ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return {"machine": "fake", "jobs": list(state["jobs"].values())}

    @app.get("/v1/agents/jobs/{job_id}")
    async def job(job_id: str, request: Request):
        if not ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if job_id not in state["jobs"]:
            return JSONResponse({"error": f"no job {job_id}", "detail": f"no job {job_id}"}, status_code=404)
        return state["jobs"][job_id]

    @app.get("/v1/agents/jobs/{job_id}/log")
    async def log(job_id: str, request: Request):
        if not ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return {"job_id": job_id, "status": state["jobs"][job_id]["status"], "log": "line one\nline two\n"}

    @app.post("/v1/agents/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        if not ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        state["cancelled"].append(job_id)
        state["jobs"][job_id]["status"] = "cancelled"
        return state["jobs"][job_id]

    return app, state


@contextmanager
def joined_satellite(client, fake_port: int, **caps):
    """Join a satellite that lives at 127.0.0.1:<fake_port>. Yields the join answer."""
    body = {
        "name": "Build box", "machine": "build-box", "host": "build-box.example", "ip": "127.0.0.1", "port": fake_port,
        "os": "linux", "satellite_token": SATELLITE, "version": "0.1.0",
        "capabilities": {"agents": ["claude", "echo"], "browser": False, "browser_port": None, "tracker": False, **caps},
    }
    answer = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=body)
    assert answer.status_code == 200, answer.text
    yield answer.json()
