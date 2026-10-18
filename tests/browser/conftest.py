"""Fixtures for the browser controller tests: a temp ORBITAL_HOME, a bridge on a free port, and a
fake extension that polls the bridge (helpers live in browser_helpers.py)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from browser_helpers import SECRET_ENV, FakeExtension, wait_until
from orbital.core import logs


@pytest.fixture
def orbital_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "orbital-home"
    home.mkdir()
    monkeypatch.setenv("ORBITAL_HOME", str(home))
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    logs.reset_for_tests()
    yield home
    logs.reset_for_tests()


@pytest.fixture
def make_bridge(orbital_home):
    """make_bridge(**settings overrides) -> a started BridgeServer on a free port."""
    from orbital.browser.server import BridgeServer, BridgeSettings
    from orbital.core.config import load_config

    started = []

    def _make(**overrides):
        cfg = load_config()
        cfg.paths.ensure()
        settings = BridgeSettings.from_config(cfg, port=overrides.pop("port", 0))
        for key, value in overrides.items():
            setattr(settings, key, value)
        bridge = BridgeServer(settings).start()
        started.append(bridge)
        return bridge

    yield _make
    for bridge in started:
        bridge.stop()


@pytest.fixture
def bridge(make_bridge):
    return make_bridge()


@pytest.fixture
def fake_extension(bridge):
    ext = FakeExtension(bridge.url)
    ext.start()
    assert ext.ready.wait(15), "the fake extension never reached the bridge"
    wait_until(lambda: httpx.get(bridge.url + "/health").json()["extension_connected"])
    yield ext
    ext.stop()
