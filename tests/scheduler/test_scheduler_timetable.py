"""Due-time math, schedule labels, run classification and jobs.json parsing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from orbital.core.paths import REPO_ROOT
from orbital.scheduler.scheduler import (Scheduler, classify, load_registry, next_due, next_slot, schedule_label,
                                         self_test)
from scheduler_helpers import py_job, setup_home


def test_next_slot_grid():
    assert next_slot(0, 900, 120) == 120
    assert next_slot(120, 900, 120) == 1020, "strictly after"
    assert next_slot(1019, 900, 120) == 1020
    base = 1_791_000_000 - (1_791_000_000 % 3600)
    assert next_slot(base + 5, 3600, 420) == base + 420
    assert next_slot(base + 421, 3600, 420) == base + 3600 + 420


def test_next_due_skips_a_slot_that_is_too_close():
    assert next_due(1000, 900, 120) == 1920, "a run 20 s before the :17 slot skips it"
    assert next_due(500, 900, 120) == 1020
    assert next_due(0, 60, 0) == 60
    assert next_due(59, 60, 0) == 120, "1 s before a slot: the one after"


def test_schedule_labels():
    assert schedule_label(900, 120) == "Every 15 min (:02, :17, :32, :47)"
    assert schedule_label(3600, 420) == "Hourly at :07"
    assert schedule_label(1800, 1620) == "Every 30 min (:27, :57)"
    assert schedule_label(300, 60) == "Every 5 min"
    assert schedule_label(60, 0) == "Every minute"
    assert schedule_label(10800, 0) == "Every 3 h"
    assert schedule_label(45, 0) == "Every 45 s"


def test_classify_outcomes():
    assert classify(["uptime pushed sites=3 down=0 alerts=0"], 0)["status"] == "ok"
    warn = classify(["weather pushed revision=12 forecast=ok radar=unavailable alerts=0"], 0)
    assert warn["status"] == "warn" and warn["issues"] == ["Radar: unavailable"]
    detail = classify(["2026-10-11T03:01:36Z api: unauthorized token expired on the provider",
                       "github pushed repo=example stars=12 api=unauthorized alerts=0"], 0)
    assert detail["status"] == "warn" and detail["issues"] == ["Api: token expired on the provider"]
    counted = classify(["rss pushed revision 4 errors 2"], 0)
    assert counted["status"] == "warn" and "2 source errors" in counted["issues"]
    crash = classify(["Traceback (most recent call last):", '  File "x.py", line 1',
                      "ConnectionRefusedError: [Errno 61] Connection refused"], 1)
    assert crash["status"] == "error" and "Connection refused" in crash["summary"]
    assert classify([], None, timed_out=True)["summary"] == "Timed out and was stopped"
    assert classify([], 0) == {"status": "ok", "summary": "Finished", "issues": []}
    self_test()


def test_registry_placeholders_and_lanes(tmp_path, home):
    jobs = {"version": 1, "lanes": {"web": {"title": "Web"}}, "jobs": [
        {"id": "a", "every": 300, "argv": ["{python}", "{repo}/feeds/examples/x.py", "~/data", "{orbital_home}/f"],
         "cwd": "{repo}", "env": {"PATH": "~/bin:/usr/bin", "OUT": "~/out"}, "lane": "web", "watch": ["~/watched"]},
        {"id": "b", "every": 3, "argv": ["/bin/true"]},
    ]}
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs))
    meta, parsed = load_registry(path, home)
    a, b = parsed
    assert a.argv == [sys.executable, f"{REPO_ROOT}/feeds/examples/x.py", str(Path.home() / "data"), f"{home}/f"]
    assert a.cwd == str(REPO_ROOT)
    assert a.env["PATH"] == f"{Path.home()}/bin:/usr/bin" and a.env["OUT"] == str(Path.home() / "out")
    assert a.watch == [Path.home() / "watched"]
    assert b.lane == "b" and "b" in meta["lanes"], "an undeclared lane is created"
    assert b.default_every == 10, "every has a floor"
    assert b.cwd == str(Path.home())


@pytest.mark.parametrize("bad", [
    {"jobs": [{"id": "a", "every": 60, "argv": ["/bin/true"]}, {"id": "a", "every": 60, "argv": ["/bin/true"]}]},
    {"jobs": [{"id": "Bad Id", "every": 60, "argv": ["/bin/true"]}]},
    {"jobs": [{"id": "a", "every": 60, "argv": "/bin/true"}]},
    {"jobs": [{"id": "a", "argv": ["/bin/true"]}]},
])
def test_registry_rejects_bad_rows(tmp_path, bad):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(bad))
    with pytest.raises((ValueError, KeyError)):
        load_registry(path)


def test_due_jobs_startup_schedule_catchup_paused(home):
    cfg = setup_home(home, [py_job("a", "pass", every=900, offset=120), py_job("b", "pass", every=900, offset=120,
                                                                             enabled=False)])
    s = Scheduler(cfg, notify=False)
    s.due_jobs(1000.0)
    assert s.queue[0][:2] == ("a", "startup")
    assert all(row[0] != "b" for row in s.queue), "a disabled job never runs"
    s.queue.clear()
    s.state["jobs"]["a"] = {"started_ts": 1000.0, "state": "idle"}
    s.due_jobs(1919.0)
    assert not s.queue, "not due before its slot"
    s.due_jobs(1920.0)
    assert s.queue[0][:2] == ("a", "schedule")
    s.queue.clear()
    s.due_jobs(1920.0 + 5000)
    assert s.queue[0][:2] == ("a", "catch-up")
    s.queue.clear()
    s.settings["jobs"]["a"] = {"paused": True}
    s.due_jobs(99999.0)
    assert not s.queue


def test_bad_jobs_json_is_reported_not_fatal(home):
    (home / "jobs.json").write_text("{not json")
    cfg = setup_home(home, None)
    s = Scheduler(cfg, notify=False)
    s.step()
    snap = json.loads((cfg.paths.scheduler_data / "state.json").read_text())["snapshot"]
    assert snap["scheduler"]["registry_error"] and snap["jobs"] == []
