"""Helpers for the satellite tests: config, free ports, servers in a thread."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from orbital.core.config import load_config

SECRET_ENV = ("ORBITAL_SATELLITE_TOKEN", "ORBITAL_HUB_TOKEN", "ORBITAL_JOIN_TOKEN", "ORBITAL_AUTOMATION_TOKEN",
              "ORBITAL_HUB_USERNAME", "ORBITAL_HUB_PASSWORD", "ORBITAL_BROWSER_TOKEN")


def write_config(home: Path, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")


def make_cfg(home: Path, workdir: Path | None = None, extra: str = ""):
    workdir = workdir or home.parent
    agents = f"""
[agents.sleeper]
command = ["/bin/sleep", "{{prompt}}"]
[agents.cat]
command = ["/bin/cat"]
[agents.missing]
command = ["orbital-test-agent-that-is-not-installed"]
[agents.py]
command = ["{sys.executable}", "-c", "{{prompt}}"]
"""
    write_config(home, f"""
[system]
role = "satellite"
machine_name = "test-machine"
[satellite]
bind = "loopback"
workdirs = ["{workdir}"]
max_concurrent_jobs = 2
heartbeat_seconds = 1
[hub]
url = ""
{agents}
{extra}
""")
    return load_config(home)


def free_port() -> int:
    """A port from the OS, never the 7700–7704 range a real install uses."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if not 7700 <= port <= 7704:
            return port


class ServerThread:
    """Run an ASGI app with uvicorn on 127.0.0.1:<free port> in a background thread."""

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
        assert self.server.started, "test server did not start"
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
