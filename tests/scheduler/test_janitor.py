"""Janitor: trims logs and old data by retention, and never touches anything outside ORBITAL_HOME."""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

from orbital.satellite.util import iso
from orbital.scheduler.janitor import (OutsideHome, inside, run_janitor, safe_unlink, trim_logs, trim_scheduler,
                                       trim_tracker_media)
from scheduler_helpers import setup_home

DAY = 86400


def age(path: Path, days: float) -> Path:
    t = time.time() - days * DAY
    os.utime(path, (t, t), follow_symlinks=False)
    return path


def write(path: Path, text: str = "x", days: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return age(path, days) if days is not None else path


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "not-orbital"
    folder.mkdir()
    victim = write(folder / "precious.log.1", "keep me", days=400)
    return victim


def test_logs_are_rolled_and_old_rolls_deleted(home, outside):
    cfg = setup_home(home, None, config="[logging]\nmax_bytes = 1000\nbackups = 2\nretention_days = 14\n")
    logs = cfg.paths.logs
    big = write(logs / "satellite.stderr.log", "e" * 5000)
    write(logs / "satellite.stderr.log.1", "older roll")
    old_roll = write(logs / "hub.log.3", "old", days=30)
    fresh_roll = write(logs / "hub.log.1", "fresh", days=1)
    current = write(logs / "hub.log", "{}\n", days=60)
    huge_json = write(logs / "app.log", "j" * 5000)
    (logs / "linked.log.1").symlink_to(outside)
    age(logs / "linked.log.1", 400)
    result = trim_logs(cfg)
    assert big.stat().st_size == 0, "truncated in place so the service keeps writing"
    assert (logs / "satellite.stderr.log.1").read_text() == "e" * 1000
    assert (logs / "satellite.stderr.log.2").read_text() == "older roll"
    assert not old_roll.exists() and fresh_roll.exists()
    assert current.exists(), "a current log is never deleted for its age"
    assert huge_json.stat().st_size == 0 and (logs / "app.log.1").stat().st_size == 1000
    assert not (logs / "linked.log.1").is_symlink(), "the link inside ORBITAL_HOME is removed"
    assert outside.read_text() == "keep me", "but what it pointed to is untouched"
    assert result["rolled"] == 2 and result["deleted"] >= 2


def test_scheduler_outputs_and_stale_requests(home):
    cfg = setup_home(home, None, config="[scheduler]\noutput_retention_days = 7\n")
    base = cfg.paths.scheduler_data
    old_out = write(base / "out" / "feed.log", days=10)
    new_out = write(base / "out" / "other.log", days=1)
    old_run = write(base / "runs" / "feed.log", days=8)
    stale = write(base / "requests" / "1-abc.json", "{}", days=2)
    fresh = write(base / "requests" / "2-def.json", "{}")
    state = write(base / "state.json", "{}", days=100)
    out = trim_scheduler(cfg)
    assert not old_out.exists() and not old_run.exists() and not stale.exists()
    assert new_out.exists() and fresh.exists() and state.exists()
    assert out == {"outputs": 2, "requests": 1}


def test_tracker_media_only_under_its_media_folder(home):
    cfg = setup_home(home, None, config="[tracker]\nmedia_retention_days = 30\n")
    tracker = cfg.paths.tracker_data
    items = write(tracker / "items.json", json.dumps({"items": []}), days=400)
    inbox = write(tracker / "inbox" / "old.json", "{}", days=400)
    old = write(tracker / "media" / "2025-01" / "shot.png", days=90)
    new = write(tracker / "media" / "2026-10" / "clip.mp4", days=2)
    out = trim_tracker_media(cfg)
    assert not old.exists() and not (tracker / "media" / "2025-01").exists()
    assert new.exists() and items.exists() and inbox.exists()
    assert out == {"files": 1, "folders": 1}


def test_satellite_records_trimmed_by_retention(home):
    cfg = setup_home(home, None, config="[satellite]\njob_retention_days = 5\n")
    jobs = cfg.paths.satellite_data / "jobs"
    for i in range(25):
        write(jobs / f"j{i}.json", json.dumps({"id": f"j{i}", "status": "done", "finished_at": iso(time.time() - 10 * DAY - i)}))
    result = run_janitor(cfg)
    assert result["satellite"]["records"] == 5
    assert len(list(jobs.glob("*.json"))) == 20


def test_hub_maintenance_is_called_when_available(home, monkeypatch):
    cfg = setup_home(home, None)
    calls = []
    fake = types.ModuleType("orbital.hub.maintenance")
    fake.trim = lambda c: calls.append(c) or {"alerts": 3}
    monkeypatch.setitem(sys.modules, "orbital.hub.maintenance", fake)
    result = run_janitor(cfg)
    assert result["hub"] == {"alerts": 3} and calls == [cfg]
    sat = setup_home(home, None, config='[system]\nrole = "satellite"\n')
    assert run_janitor(sat)["hub"] == "not a hub"


def test_a_failing_step_does_not_stop_the_others(home, monkeypatch):
    cfg = setup_home(home, None)
    fake = types.ModuleType("orbital.hub.maintenance")

    def broken(c):
        raise RuntimeError("hub trim broke")

    fake.trim = broken
    monkeypatch.setitem(sys.modules, "orbital.hub.maintenance", fake)
    result = run_janitor(cfg)
    assert "hub trim broke" in result["hub"]["error"]
    assert "rolled" in result["logs"]


def test_nothing_outside_home_can_be_deleted(home, outside):
    cfg = setup_home(home, None)
    assert inside(cfg.paths.logs / "a.log", cfg.paths.home)
    assert not inside(outside, cfg.paths.home)
    with pytest.raises(OutsideHome):
        safe_unlink(outside, cfg.paths.home)
    with pytest.raises(OutsideHome):
        safe_unlink(cfg.paths.home / ".." / "not-orbital" / "precious.log.1", cfg.paths.home)
    # A whole run of the janitor with old files everywhere around ORBITAL_HOME.
    sibling = write(home.parent / "sibling.stderr.log", "s" * 10_000_000)
    run_janitor(cfg)
    assert outside.read_text() == "keep me"
    assert sibling.stat().st_size == 10_000_000


def test_tracker_media_symlink_out_of_home_is_ignored(home, tmp_path):
    cfg = setup_home(home, None)
    elsewhere = tmp_path / "elsewhere-media"
    victim = write(elsewhere / "2020-01" / "photo.png", days=900)
    cfg.paths.tracker_data.mkdir(parents=True, exist_ok=True)
    (cfg.paths.tracker_data / "media").symlink_to(elsewhere)
    assert trim_tracker_media(cfg) == {"files": 0, "folders": 0}
    assert victim.exists()
