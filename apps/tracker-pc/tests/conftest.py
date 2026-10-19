"""Fixtures for the PC tracker tests: a temporary ORBITAL_HOME, a free port, and a headless tracker.

Nothing here touches ~/.orbital-agents or the real tracker ports (7700-7704).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

PC = Path(__file__).resolve().parents[1]
REPO = PC.parents[1]
sys.path.insert(0, str(PC))

from tracker_pc.app import TrackerApp  # noqa: E402
from tracker_pc.config import OrbitalConfig  # noqa: E402

SWIFT_CLI = REPO / "apps" / "tracker" / ".build" / "debug" / "orbital-track"
# Environment variables that change what orbital-track does; tests start from none of them.
CLEAN_ENV = ("ORBITAL_TRACK_PARENT", "ORBITAL_TRACK_DISABLE", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ORBITAL_HUB_TOKEN")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_config(home: Path, port: int, machine: str = "test-pc", extra: dict | None = None) -> None:
    cfg = {"system": {"role": "hub", "machine_name": machine}, "machine_name": machine,
           "tracker": {"port": port}, "paths": {"home": str(home)}}
    for key, value in (extra or {}).items():
        if isinstance(value, dict):
            cfg.setdefault(key, {}).update(value)
        else:
            cfg[key] = value
    (home / "config.resolved.json").write_text(json.dumps(cfg), encoding="utf-8")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "orbital-home"
    h.mkdir()
    write_config(h, free_port())
    monkeypatch.setenv("ORBITAL_HOME", str(h))
    for key in CLEAN_ENV:
        monkeypatch.delenv(key, raising=False)
    return h


@pytest.fixture
def app(home: Path):
    cfg = OrbitalConfig.load({"ORBITAL_HOME": str(home)})
    tracker = TrackerApp(cfg, save_delay=0, ui_enabled=False)
    tracker.start()
    yield tracker
    tracker.stop()


@pytest.fixture
def png(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "shot.png"
    Image.new("RGBA", (1200, 800), (40, 80, 160, 255)).save(path)
    return path


def cli_env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
    env["ORBITAL_HOME"] = str(home)
    env.update(extra)
    return env


def run_cli(home: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None,
            exe: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = (exe or [sys.executable, str(PC / "orbital_track.py")]) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env or cli_env(home), input=stdin,
                          timeout=60, cwd=str(home))
