"""Fixtures for the example-feed tests: a temp ORBITAL_HOME, no real secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from feeds_helpers import SECRET_ENV
from orbital.core import logs as core_logs


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "orbital-home"
    path.mkdir()
    (path / "config.toml").write_text('[system]\nmachine_name = "lab-box"\n', encoding="utf-8")
    monkeypatch.setenv("ORBITAL_HOME", str(path))
    monkeypatch.setenv("ORBITAL_TRACK_BIN", "off")
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    core_logs.reset_for_tests()
    yield path
    core_logs.reset_for_tests()
