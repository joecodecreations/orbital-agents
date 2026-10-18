"""Fixtures for the service installer tests: a temp HOME and ORBITAL_HOME, and a fake command runner
so nothing ever reaches the real launchctl or systemctl."""

from __future__ import annotations

from pathlib import Path

import pytest

from orbital.core import logs
from orbital.core.config import load_config
from orbital.services.ops import Result

SAFE_ENV = ("ORBITAL_JOIN_TOKEN", "ORBITAL_HUB_PASSWORD", "ORBITAL_AUTOMATION_TOKEN", "XAI_API_KEY",
            "FISH_API_KEY", "XDG_CONFIG_HOME", "ORBITAL_LIVE")


class FakeExec:
    """Records commands. `rules` maps an argv prefix (tuple) to a Result or a callable(argv) -> Result."""

    def __init__(self, rules: dict | None = None, default: Result | None = None):
        self.calls: list[list[str]] = []
        self.rules = dict(rules or {})
        self.default = default or Result(0)

    def __call__(self, argv, timeout=60, **kw):
        argv = list(argv)
        self.calls.append(argv)
        best = None
        for prefix, res in self.rules.items():
            if tuple(argv[: len(prefix)]) == tuple(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, res)
        if best:
            res = best[1]
            return res(argv) if callable(res) else res
        return self.default

    def verbs(self, tool: str) -> list[str]:
        return [" ".join(c[1:3]) for c in self.calls if Path(c[0]).name == tool]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    orbital_home = tmp_path / "orbital-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORBITAL_HOME", str(orbital_home))
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "/usr/bin/false")      # belt and braces: never the real one
    monkeypatch.setenv("ORBITAL_SYSTEMCTL", "/usr/bin/false")
    monkeypatch.setenv("ORBITAL_TRACKER_APP", str(tmp_path / "no-tracker.app"))
    for key in SAFE_ENV:
        monkeypatch.delenv(key, raising=False)
    logs.reset_for_tests()
    yield home, orbital_home
    logs.reset_for_tests()


@pytest.fixture
def make_cfg(homes):
    """load_config() for the temp ORBITAL_HOME with overrides; ports moved off the real ones."""
    def make(**overrides):
        base = {"hub": {"port": 17750}, "tracker": {"port": 17751, "stt_port": 17752},
                "browser": {"bridge_port": 17753}, "satellite": {"port": 17754}}
        for dotted, value in overrides.items():
            table, key = dotted.split("__", 1)
            base.setdefault(table, {})[key] = value
        return load_config(overrides=base)
    return make


@pytest.fixture
def fake_exec():
    return FakeExec
