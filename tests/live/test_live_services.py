"""Live: login services and logs on this machine."""

from __future__ import annotations

import pytest

from orbital import services
from orbital.services.spec import tracker_missing

pytestmark = pytest.mark.live


def test_services_up(cfg):
    """services: every enabled service is installed, loaded, running and answering"""
    rows = services.status(cfg)
    problems = []
    for s in rows:
        if not s.wanted:
            if s.installed:
                problems.append(f"{s.name}: installed but not wanted here ({s.reason}); `orbital services install` removes it")
            continue
        if s.name == "tracker" and tracker_missing(cfg):
            continue        # covered by the tracker check, which explains how to build it
        if not s.installed:
            problems.append(f"{s.name}: not installed")
        elif not s.running:
            problems.append(f"{s.name}: {s.state or 'not running'}")
        elif s.healthy is False:
            problems.append(f"{s.name}: running but unhealthy ({s.health})")
    assert not problems, "; ".join(problems) + "  (fix: orbital doctor --fix)"


def test_logs_exist(cfg):
    """logs: a log file exists for each running service"""
    logs = cfg.paths.logs
    assert logs.is_dir(), f"no log folder at {logs}"
    running = [s.name for s in services.status(cfg, health=False) if s.wanted and s.running]
    if not running:
        pytest.skip("no services running")
    missing = []
    for name in running:
        candidates = [logs / f"{name}.log", logs / f"{name}.stderr.log"]
        if not any(c.exists() for c in candidates):
            missing.append(name)
    assert not missing, "no log file for: " + ", ".join(missing)


def test_log_rotation(cfg):
    """logs: rotation settings are sane and no file is over max_bytes x 1.1"""
    max_bytes = int(cfg.get("logging.max_bytes"))
    backups = int(cfg.get("logging.backups"))
    assert 10_000 <= max_bytes <= 1_000_000_000, f"logging.max_bytes = {max_bytes} looks wrong"
    assert 0 <= backups <= 50, f"logging.backups = {backups} looks wrong"
    logs = cfg.paths.logs
    if not logs.is_dir():
        pytest.skip("no log folder yet")
    too_big = [f"{p.name} ({p.stat().st_size // 1024} KB)" for p in logs.iterdir()
               if p.is_file() and p.stat().st_size > max_bytes * 1.1]
    assert not too_big, "over the size limit: " + ", ".join(too_big) + " (the watchdog trims these; is it running?)"
    for component in {p.name.split(".")[0] for p in logs.glob("*.log")}:
        rolled = list(logs.glob(f"{component}.log.*"))
        assert len(rolled) <= backups + 1, f"{component}: {len(rolled)} rolled files, backups = {backups}"
