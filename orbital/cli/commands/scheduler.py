"""`orbital jobs`: the scheduler's feeds, their timetable, and running one now.

    orbital jobs                     every feed: schedule, state, next run, last result
    orbital jobs plan                the timetable straight from jobs.json
    orbital jobs run <id> [--wait]   queue a run through the running scheduler
    orbital jobs run <id> --here     run once in the foreground (only when the scheduler is stopped)
    orbital jobs cancel <id>         drop it from the queue or stop the run
    orbital jobs refresh [--group G] run everything (or one group) now, lane by lane
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from orbital.core.config import load_config
from orbital.scheduler.scheduler import (load_registry, parse_iso, plan, read_json, request_run, run_here,
                                         scheduler_alive, write_request)

NAME = "jobs"
HELP = "Scheduler feeds: orbital jobs [list|plan|run <id> [--wait]|cancel <id>|refresh]"


def register(p):
    p.add_argument("action", nargs="?", default="list", choices=["list", "plan", "run", "cancel", "refresh"])
    p.add_argument("ids", nargs="*", help="job ids (run, cancel)")
    p.add_argument("--wait", action="store_true", help="run: wait for the result and print it")
    p.add_argument("--here", action="store_true", help="run: run in this terminal (scheduler must be stopped)")
    p.add_argument("--group", help="refresh: only this group")
    p.add_argument("--json", action="store_true", help="list: print the scheduler snapshot as JSON")


def _when(stamp: str | None) -> str:
    ts = parse_iso(stamp)
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "-"


def list_jobs(cfg, as_json: bool) -> int:
    snap = (read_json(cfg.paths.scheduler_data / "state.json", {}) or {}).get("snapshot")
    if not snap:
        if not cfg.paths.jobs_file.is_file():
            print(f"no feeds yet: copy feeds/jobs.example.json to {cfg.paths.jobs_file} "
                  "(the scheduler picks it up within a second)")
            return 0
        return plan(cfg)
    if as_json:
        print(json.dumps(snap, indent=2))
        return 0
    alive = scheduler_alive(cfg)
    sched = snap.get("scheduler") or {}
    print(f"Scheduler {'running' if alive else 'NOT running'} · {sched.get('running', 0)} running, "
          f"{sched.get('queued', 0)} queued · max {sched.get('max_parallel')} at once")
    if sched.get("registry_error"):
        print(f"  jobs.json problem: {sched['registry_error']}")
    for job in snap.get("jobs") or []:
        last = job.get("last") or {}
        state = job.get("state") or "idle"
        if job.get("paused"):
            state = "paused"
        nxt = _when(job.get("next_run_at")) if state == "idle" else "-"
        print(f"  {job['id']:<20} {job.get('schedule', ''):<30} {state:<8} next {nxt:<6} "
              f"last {last.get('status', '-'):<5} {str(last.get('summary') or '')[:58]}")
    return 0


def run(args) -> int:
    cfg = load_config()
    if args.action == "list":
        return list_jobs(cfg, args.json)
    if args.action == "plan":
        return plan(cfg)
    if not cfg.paths.jobs_file.is_file():
        print(f"no timetable at {cfg.paths.jobs_file}", file=sys.stderr)
        return 1
    if args.action == "run":
        if args.here:
            if len(args.ids) != 1:
                print("--here runs exactly one job", file=sys.stderr)
                return 2
            return run_here(args.ids[0], cfg)
        return request_run(args.ids, args.wait, cfg)
    if args.action == "cancel":
        _, jobs = load_registry(cfg.paths.jobs_file, cfg.paths.home)
        known = {job.id for job in jobs}
        for job_id in args.ids:
            if job_id not in known:
                print(f"unknown job {job_id}", file=sys.stderr)
                return 2
            write_request(cfg, {"type": "cancel", "job": job_id, "by": "cli"})
            print(f"cancel requested for {job_id}")
        return 0
    if args.action == "refresh":
        write_request(cfg, {"type": "run_all", "group": args.group, "by": "cli"})
        print(f"refresh requested for {'group ' + args.group if args.group else 'every feed'}")
        if not scheduler_alive(cfg):
            print("the scheduler is not running, so nothing will pick this up yet", file=sys.stderr)
            return 3
        return 0
    return 2
