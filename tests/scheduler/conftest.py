"""Fixtures for the scheduler tests: a temp ORBITAL_HOME with no real secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from orbital.core import logs as core_logs
from scheduler_helpers import SECRET_ENV


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "orbital-home"
    path.mkdir()
    monkeypatch.setenv("ORBITAL_HOME", str(path))
    monkeypatch.setenv("ORBITAL_TRACK_BIN", "off")
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    core_logs.reset_for_tests()
    yield path
    core_logs.reset_for_tests()
