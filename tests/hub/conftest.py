"""Fixtures for the hub tests: a temp ORBITAL_HOME with test secrets and free ports, never the real install."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub_helpers import AUTOMATION, BROWSER, JOIN, SATELLITE, SECRET_ENV, write_config
from orbital.core import logs as core_logs


def pytest_configure(config):
    # Starlette's TestClient warns that it will move off httpx; nothing to act on here.
    config.addinivalue_line("filterwarnings", "ignore:Using `httpx` with `starlette.testclient`")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "orbital-home"
    path.mkdir()
    monkeypatch.setenv("ORBITAL_HOME", str(path))
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORBITAL_AUTOMATION_TOKEN", AUTOMATION)
    monkeypatch.setenv("ORBITAL_JOIN_TOKEN", JOIN)
    monkeypatch.setenv("ORBITAL_SATELLITE_TOKEN", SATELLITE)
    monkeypatch.setenv("ORBITAL_BROWSER_TOKEN", BROWSER)
    monkeypatch.setenv("ORBITAL_HUB_USERNAME", "owner")
    monkeypatch.setenv("ORBITAL_HUB_PASSWORD", "correct horse")
    core_logs.reset_for_tests()
    yield path
    core_logs.reset_for_tests()


@pytest.fixture
def ports(home) -> dict:
    return write_config(home)
