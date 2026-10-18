"""Live: the scheduler is up and runs a feed when asked."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.live

DEMO_JOBS = ("hello", "machine-health")


def _job(snapshot: dict, job_id: str) -> dict:
    return next((j for j in snapshot.get("jobs") or [] if j.get("id") == job_id), {})


def test_scheduler_runs_demo_job(cfg, automation):
    """scheduler: responsive, runs the demo job and records the result"""
    if not cfg.get("scheduler.enabled"):
        pytest.skip("scheduler.enabled = false")
    r = automation.get("/v1/jobs")
    assert r.status_code == 200, f"/v1/jobs → HTTP {r.status_code}"
    snap = r.json()
    sched = snap.get("scheduler") or {}
    assert sched.get("up"), f"the scheduler isn't writing heartbeats (age {sched.get('heartbeat_age_s')}s); " \
                            "`orbital services status scheduler`"
    ids = [j.get("id") for j in snap.get("jobs") or []]
    if not ids:
        pytest.skip("no jobs in jobs.json (re-run ./setup --reconfigure to add the example feeds)")
    job_id = next((j for j in DEMO_JOBS if j in ids), ids[0])
    before = (_job(snap, job_id).get("last") or {}).get("finished_at")
    r = automation.post(f"/v1/jobs/{job_id}/run")
    assert r.status_code == 200, f"run {job_id} → HTTP {r.status_code}: {r.text[:200]}"
    timeout = min(180, int(_job(snap, job_id).get("timeout_s") or 60) + 30)
    deadline = time.monotonic() + timeout
    job = {}
    while time.monotonic() < deadline:
        time.sleep(1.0)
        job = _job(automation.get("/v1/jobs").json(), job_id)
        last = job.get("last") or {}
        if last.get("finished_at") and last.get("finished_at") != before and job.get("state") != "running":
            break
    last = job.get("last") or {}
    assert last.get("finished_at") != before, f"{job_id} didn't finish within {timeout}s (state {job.get('state')})"
    assert last.get("status") in ("ok", "warn"), f"{job_id} ended {last.get('status')}: {last.get('summary')}"
