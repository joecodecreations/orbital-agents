"""Helpers for the scheduler and watchdog tests."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from orbital.core.config import load_config

SECRET_ENV = ("ORBITAL_SATELLITE_TOKEN", "ORBITAL_HUB_TOKEN", "ORBITAL_JOIN_TOKEN", "ORBITAL_AUTOMATION_TOKEN",
              "ORBITAL_HUB_USERNAME", "ORBITAL_HUB_PASSWORD", "ORBITAL_BROWSER_TOKEN")
PY = sys.executable


def setup_home(home: Path, jobs: list[dict] | None, config: str = "", **meta) -> object:
    (home / "config.toml").write_text(config, encoding="utf-8")
    if jobs is not None:
        body = {"version": 1, "jobs": jobs}
        body.update(meta)
        (home / "jobs.json").write_text(json.dumps(body), encoding="utf-8")
    return load_config(home)


def py_job(job_id: str, code: str, **extra) -> dict:
    row = {"id": job_id, "name": job_id.title(), "every": 3600, "offset": 0, "timeout": 30,
           "cwd": "~", "argv": [PY, "-c", code]}
    row.update(extra)
    return row


def run_until(scheduler, predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scheduler.step()
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def free_port() -> int:
    """A port from the OS, never the 7700–7704 range a real install uses."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if not 7700 <= port <= 7704:
            return port


class ServerThread:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 0):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port or free_port()))
        self.port = self.sock.getsockname()[1]
        self.url = f"http://{host}:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()
