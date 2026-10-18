"""The scheduler running real feed processes: outcome, env, output cap, timeout, lanes, requests, notify."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest
from fastapi import FastAPI, Request

from orbital.core.config import load_config
from orbital.core.paths import REPO_ROOT
from orbital.core.secrets import Secrets
from orbital.scheduler.scheduler import Scheduler, notify_hub, plan, request_run, write_request
from scheduler_helpers import ServerThread, free_port, py_job, run_until, setup_home


def state_of(s, job_id):
    return s.state["jobs"].get(job_id) or {}


def idle_with_last(s, job_id):
    record = state_of(s, job_id)
    return record.get("state") == "idle" and record.get("last")


def test_a_feed_runs_and_its_outcome_is_recorded(home):
    code = ("import os; print('ORBITAL_JOB_ID=' + os.environ['ORBITAL_JOB_ID']); "
            "print('home=' + os.environ['ORBITAL_HOME']); print('reason=' + os.environ['ORBITAL_RUN_REASON']); "
            "print('pythonpath=' + os.environ['PYTHONPATH']); print('cwd=' + os.getcwd()); "
            "import sys; print('to stderr', file=sys.stderr); print('demo pushed sites=3 down=0 alerts=0')")
    cfg = setup_home(home, [py_job("demo", code, cwd="{repo}", targets=[{"section": "demo"}], group="lab")],
                     groups=[{"id": "lab", "title": "Lab"}])
    s = Scheduler(cfg, notify=False)
    assert run_until(s, lambda: idle_with_last(s, "demo"))
    last = state_of(s, "demo")["last"]
    assert last["status"] == "ok" and last["exit_code"] == 0 and last["reason"] == "startup"
    assert last["summary"] == "demo pushed sites=3 down=0 alerts=0"
    out = (cfg.paths.scheduler_data / "out" / "demo.log").read_text()
    assert "ORBITAL_JOB_ID=demo" in out and f"home={home}" in out and "reason=startup" in out
    assert f"cwd={REPO_ROOT}" in out and str(REPO_ROOT) in out.split("pythonpath=")[1].splitlines()[0]
    assert "[stderr] to stderr" in out
    runs = (cfg.paths.scheduler_data / "runs" / "demo.log").read_text()
    assert runs.startswith("# Demo") and "demo pushed" in runs
    s.write_state(time.time())
    snap = json.loads((cfg.paths.scheduler_data / "state.json").read_text())["snapshot"]
    assert snap["schema"] == "orbital.jobs.v1"
    job = snap["jobs"][0]
    assert job["id"] == "demo" and job["schedule"] == "Hourly at :00" and job["targets"] == [{"section": "demo"}]
    assert job["next_run_at"] and job["history"][-1]["status"] == "ok"
    assert snap["groups"] == [{"id": "lab", "title": "Lab"}]
    beat = json.loads((cfg.paths.run / "scheduler.json").read_text())
    assert beat["heartbeat_at"] and beat["jobs"] == 1
    log = (cfg.paths.logs / "scheduler.log").read_text()
    assert '"job": "demo"' in log


def test_warn_and_error_outcomes(home):
    cfg = setup_home(home, [
        py_job("partial", "print('feed pushed revision=3 source=unavailable alerts=0')"),
        py_job("crash", "raise RuntimeError('source exploded')"),
    ])
    s = Scheduler(cfg, notify=False)
    assert run_until(s, lambda: idle_with_last(s, "partial") and idle_with_last(s, "crash"))
    assert state_of(s, "partial")["last"]["status"] == "warn"
    crash = state_of(s, "crash")
    assert crash["last"]["status"] == "error" and "source exploded" in crash["last"]["summary"]
    assert crash["failing_since"]


def test_output_is_capped_per_run_and_rolled(home):
    cfg = setup_home(home, [py_job("noisy", "[print('y' * 99) for _ in range(500)]; print('noisy pushed lines=500 a=1 b=2')")],
                     config="[scheduler]\nmax_output_bytes = 4000\n")
    out_log = cfg.paths.scheduler_data / "out" / "noisy.log"
    out_log.parent.mkdir(parents=True, exist_ok=True)
    out_log.write_text("old run\n" * 1000)
    s = Scheduler(cfg, notify=False)
    assert run_until(s, lambda: idle_with_last(s, "noisy"))
    assert (out_log.parent / "noisy.log.1").read_text().startswith("old run"), "an oversized log is rolled first"
    text = out_log.read_text()
    assert len(text) < 4000 + 600 and "is not kept here" in text
    assert state_of(s, "noisy")["output_truncated"] is True
    runs = (cfg.paths.scheduler_data / "runs" / "noisy.log").read_text().splitlines()
    assert runs[-1] == "noisy pushed lines=500 a=1 b=2", "the last lines are still kept"
    assert state_of(s, "noisy")["last"]["status"] == "ok"


def test_timeout_stops_the_run(home):
    cfg = setup_home(home, [py_job("hang", "import time; time.sleep(60)", timeout=0.5)])
    s = Scheduler(cfg, notify=False)
    started = time.monotonic()
    assert run_until(s, lambda: idle_with_last(s, "hang"), 15)
    assert time.monotonic() - started < 10
    last = state_of(s, "hang")["last"]
    assert last["status"] == "error" and last["summary"] == "Timed out and was stopped"


def test_lanes_run_one_job_at_a_time(home):
    sleep = "import time; time.sleep(0.6); print('done ok=1 a=1 b=2')"
    cfg = setup_home(home, [py_job("one", sleep, lane="shared"), py_job("two", sleep, lane="shared"),
                            py_job("three", sleep, lane="other")], max_parallel=5)
    s = Scheduler(cfg, notify=False)
    s.step()
    assert set(s.running) == {"one", "three"}, "lanes side by side, one job per lane"
    assert [row[0] for row in s.queue] == ["two"]
    assert run_until(s, lambda: all(idle_with_last(s, j) for j in ("one", "two", "three")))


def test_max_parallel(home):
    sleep = "import time; time.sleep(0.4)"
    cfg = setup_home(home, [py_job(f"j{i}", sleep) for i in range(3)], config="[scheduler]\nmax_parallel = 1\n")
    s = Scheduler(cfg, notify=False)
    s.step()
    assert len(s.running) == 1 and len(s.queue) == 2
    seen = []
    assert run_until(s, lambda: seen.append(len(s.running)) or all(idle_with_last(s, f"j{i}") for i in range(3)))
    assert max(seen) <= 1


def test_requests_run_cancel_settings_and_refresh_all(home):
    cfg = setup_home(home, [py_job("quick", "print('quick ok=1 a=1 b=2')", group="g"),
                            py_job("slow", "import time; time.sleep(30)", group="g", lane="slow-lane")])
    s = Scheduler(cfg, notify=False)
    assert run_until(s, lambda: idle_with_last(s, "quick") and "slow" in s.running)
    # cancel the slow one
    write_request(cfg, {"type": "cancel", "job": "slow", "by": "test"})
    assert run_until(s, lambda: idle_with_last(s, "slow"))
    assert state_of(s, "slow")["last"]["summary"] == "Stopped on request"
    # run now
    first = state_of(s, "quick")["last"]["started_at"]
    time.sleep(1.1)
    write_request(cfg, {"type": "run", "jobs": ["quick", "unknown"], "by": "test"})
    assert run_until(s, lambda: idle_with_last(s, "quick") and state_of(s, "quick")["last"]["started_at"] != first)
    assert state_of(s, "quick")["last"]["reason"] == "manual"
    # settings: every and pause
    write_request(cfg, {"type": "settings", "job": "quick", "every": 300, "by": "test"})
    write_request(cfg, {"type": "settings", "job": "slow", "paused": True, "by": "test"})
    s.step()
    settings = json.loads((cfg.paths.scheduler_data / "settings.json").read_text())
    assert settings["jobs"] == {"quick": {"every": 300}, "slow": {"paused": True}}
    assert s.every(s.by_id["quick"]) == 300 and s.paused(s.by_id["slow"])
    write_request(cfg, {"type": "settings", "job": "quick", "every": 1234, "by": "test"})
    s.step()
    assert s.every(s.by_id["quick"]) == 300, "only the offered choices are accepted"
    # refresh all of group g (slow is paused but refresh-all still runs every job of the group)
    write_request(cfg, {"type": "settings", "job": "slow", "paused": False, "by": "test"})
    write_request(cfg, {"type": "run_all", "group": "g", "by": "test"})
    s.step()
    batch = s.state["batch"]
    assert batch["jobs"] == ["quick", "slow"] and batch["stages"] == [["quick", "slow"]]
    write_request(cfg, {"type": "cancel", "job": "slow", "by": "test"})
    assert run_until(s, lambda: s.state["batch"].get("finished_at"))


def test_restart_adopts_runs_in_flight(home):
    cfg = setup_home(home, [py_job("demo", "print('demo ok=1 a=1 b=2')")])
    state = {"jobs": {"demo": {"state": "running", "pid": None, "started_ts": time.time() - 30,
                               "started_at": "2026-01-01T00:00:00Z", "reason": "schedule"},
                      "gone": {"state": "queued"}}}
    cfg.paths.scheduler_data.mkdir(parents=True, exist_ok=True)
    (cfg.paths.scheduler_data / "state.json").write_text(json.dumps(state))
    s = Scheduler(cfg, notify=False)
    assert s.queue[0][:2] == ("demo", "restart")
    assert state_of(s, "gone")["state"] == "idle"
    assert run_until(s, lambda: idle_with_last(s, "demo"))
    assert state_of(s, "demo")["last"]["reason"] == "restart"
    assert state_of(s, "demo")["history"][0]["summary"].startswith("Interrupted")


def test_watched_file_triggers_a_run(home, tmp_path, monkeypatch):
    watched = tmp_path / "watched.txt"
    watched.write_text("v1")
    import orbital.scheduler.scheduler as sched
    monkeypatch.setattr(sched, "WATCH_DEBOUNCE", 0.1)
    cfg = setup_home(home, [py_job("watcher", "print('w ok=1 a=1 b=2')", watch=[str(watched)])])
    s = Scheduler(cfg, notify=False)
    assert run_until(s, lambda: idle_with_last(s, "watcher"))
    runs = state_of(s, "watcher")["runs"]
    time.sleep(0.05)
    watched.write_text("v2 changed")
    os.utime(watched, (time.time() + 5, time.time() + 5))
    assert run_until(s, lambda: state_of(s, "watcher").get("runs", 0) > runs and idle_with_last(s, "watcher"))
    assert state_of(s, "watcher")["last"]["reason"] == "watch"


def test_jobs_json_is_reloaded_when_it_changes(home):
    cfg = setup_home(home, [py_job("first", "pass")])
    s = Scheduler(cfg, notify=False)
    s.step()
    body = json.loads((home / "jobs.json").read_text())
    body["jobs"].append(py_job("second", "pass"))
    (home / "jobs.json").write_text(json.dumps(body))
    os.utime(home / "jobs.json", (time.time() + 5, time.time() + 5))
    s.step()
    assert "second" in s.by_id


def test_notify_hub_posts_with_the_automation_token(home):
    calls = []
    app = FastAPI()

    @app.post("/v1/jobs/notify")
    async def notify(request: Request):
        calls.append(request.headers.get("authorization"))
        return {"ok": True}

    with ServerThread(app) as srv:
        cfg = setup_home(home, [], config=f"[hub]\nport = {srv.port}\n")
        assert notify_hub(cfg) is False, "no token, no call"
        Secrets(cfg.paths).set("ORBITAL_AUTOMATION_TOKEN", "automation-token-abc")
        assert notify_hub(load_config(home)) is True
    assert calls == ["Bearer automation-token-abc"]


def test_cli_helpers_when_the_scheduler_is_stopped(home, capsys):
    cfg = setup_home(home, [py_job("demo", "print('hi ok=1 a=1 b=2')")])
    assert plan(cfg) == 0
    assert "demo" in capsys.readouterr().out
    assert request_run(["demo"], wait=True, cfg=cfg) == 3, "nothing would pick the request up"
    assert request_run(["nope"], wait=False, cfg=cfg) == 2
    assert list((cfg.paths.scheduler_data / "requests").glob("*.json"))


@pytest.mark.slow
def test_scheduler_process(home):
    cfg = setup_home(home, [py_job("proc", "print('proc pushed a=1 b=2 c=3')")])
    env = dict(os.environ, ORBITAL_HOME=str(home))
    proc = subprocess.Popen([sys.executable, "-m", "orbital.scheduler"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        state_file = cfg.paths.scheduler_data / "state.json"
        deadline = time.monotonic() + 20
        last = None
        while time.monotonic() < deadline:
            try:
                last = ((json.loads(state_file.read_text()).get("jobs") or {}).get("proc") or {}).get("last")
            except (OSError, ValueError):
                last = None
            if last:
                break
            time.sleep(0.2)
        assert last and last["status"] == "ok"
        assert request_run(["proc"], wait=True, cfg=cfg, timeout=20) == 0
        second = subprocess.run([sys.executable, "-m", "orbital.scheduler"], env=env, capture_output=True, text=True, timeout=20)
        assert "already running" in second.stderr
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
