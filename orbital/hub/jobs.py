"""The scheduler's view for the apps: schedules, countdowns, last results, and run requests.

The scheduler (orbital/scheduler/) owns every feed's schedule and writes its whole picture to
data/scheduler/state.json ({"snapshot": {...}}). This module serves that picture and turns the
apps' buttons into request files in data/scheduler/requests/ that the scheduler picks up
within a second. Run logs are data/scheduler/runs/<job>.log. The hub never runs a feed itself.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from orbital.hub.ops import OpsError

HEARTBEAT_STALE = 90  # seconds without a heartbeat before the app shows the scheduler as down
LOG_LINES = 400


def _parse(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Jobs:
    def __init__(self, state_dir: Path):
        self.dir = state_dir
        self.state_file = state_dir / "state.json"
        self.requests = state_dir / "requests"
        self.runs = state_dir / "runs"

    def snapshot(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        snap = state.get("snapshot") or {"schema": "orbital.jobs.v1", "scheduler": {}, "groups": [], "lanes": [], "jobs": [], "batch": None}
        scheduler = dict(snap.get("scheduler") or {})
        beat = _parse(scheduler.get("heartbeat_at"))
        age = None if beat is None else max(0, time.time() - beat)
        scheduler["up"] = age is not None and age < HEARTBEAT_STALE
        scheduler["heartbeat_age_s"] = None if age is None else round(age, 1)
        snap["scheduler"] = scheduler
        snap["server_time"] = _now_iso()
        return snap

    def job_ids(self) -> set[str]:
        return {row.get("id") for row in self.snapshot().get("jobs") or []}

    def _request(self, body: dict) -> str:
        self.requests.mkdir(parents=True, exist_ok=True)
        ident = f"{time.time_ns()}-{secrets.token_hex(3)}"
        tmp = self.requests / f".{ident}.tmp"
        tmp.write_text(json.dumps(body), encoding="utf-8")
        os.replace(tmp, self.requests / f"{ident}.json")
        return ident

    def run(self, ids: list[str], by: str) -> dict:
        known = self.job_ids()
        unknown = [job_id for job_id in ids if job_id not in known]
        if not ids or unknown:
            raise OpsError(f"unknown job {', '.join(unknown) or '(none)'}", status=404)
        request = self._request({"type": "run", "jobs": ids, "by": by})
        return {"ok": True, "queued": ids, "request": request}

    def run_for_page(self, section_id: str, node_id: str | None, by: str) -> dict:
        """Every job that writes this page: the node's own jobs, or every job in the section."""
        wanted = []
        for row in self.snapshot().get("jobs") or []:
            for target in row.get("targets") or []:
                if target.get("section") != section_id:
                    continue
                if node_id and target.get("node") not in (None, node_id):
                    continue
                wanted.append(row["id"])
                break
        if not wanted:
            raise OpsError(f"no job writes {section_id}{'/' + node_id if node_id else ''}", status=404)
        return self.run(wanted, by)

    def run_all(self, group: str | None, by: str) -> dict:
        snap = self.snapshot()
        if group and group not in {row.get("id") for row in snap.get("groups") or []}:
            raise OpsError(f"unknown group {group}", status=404)
        request = self._request({"type": "run_all", "group": group, "by": by})
        jobs = [row["id"] for row in snap.get("jobs") or [] if not group or row.get("group") == group]
        return {"ok": True, "queued": jobs, "request": request}

    def settings(self, job_id: str, body: dict, by: str) -> dict:
        snap = self.snapshot()
        row = next((item for item in snap.get("jobs") or [] if item.get("id") == job_id), None)
        if row is None:
            raise OpsError(f"unknown job {job_id}", status=404)
        request: dict = {"type": "settings", "job": job_id, "by": by}
        if "every_s" in body or "every" in body:
            every = body.get("every_s", body.get("every"))
            choices = (snap.get("scheduler") or {}).get("every_choices") or []
            if every is not None and (not isinstance(every, int) or (choices and every not in choices)):
                raise OpsError(f"every_s must be one of {choices}")
            request["every"] = every
        if "paused" in body:
            if not isinstance(body["paused"], bool):
                raise OpsError("paused must be true or false")
            request["paused"] = body["paused"]
        if len(request) == 3:
            raise OpsError("send every_s or paused")
        self._request(request)
        return {"ok": True, "job": job_id}

    def cancel(self, job_id: str, by: str) -> dict:
        if job_id not in self.job_ids():
            raise OpsError(f"unknown job {job_id}", status=404)
        self._request({"type": "cancel", "job": job_id, "by": by})
        return {"ok": True, "job": job_id}

    def log(self, job_id: str) -> dict:
        if job_id not in self.job_ids():
            raise OpsError(f"unknown job {job_id}", status=404)
        path = self.runs / f"{job_id}.log"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        return {"ok": True, "job": job_id, "lines": lines[-LOG_LINES:]}
