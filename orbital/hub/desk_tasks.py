"""Work the Desk hands out, one satellite job per run, watched to the finish.

Every job starts through this machine's own satellite service
(POST http://127.0.0.1:<satellite.port>/v1/agents/run with ORBITAL_SATELLITE_TOKEN), as the
agent in desk.worker_agent. The satellite owns the process, the time limit, the output and the
tracker item (track: true), so a hub restart does not lose a job: the runner picks it back up
from tasks.json by job id.

A follow-up on a running task waits for that run to end; a follow-up on a finished task starts
a new run told what the last one reported. A satellite that is busy (or not up yet) leaves the
task queued, and it starts on a later tick.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from orbital.core import get_logger
from orbital.hub.satellites import RelayError, call

MAX_WAIT = 24 * 3600
FINISHED = {"done", "failed", "cancelled"}

WORKER_HEADER = """You were started from the Orbital Agents app's Desk (desk task {task_id}). The person who asked is away from this machine and hears about your work only through the desk.

How to work:
- Follow the project's own instructions (CLAUDE.md, AGENTS.md, README) if it has them.
- Do the whole job end to end. Do not stop to ask questions; nobody can answer mid-run. Make the sensible call and say what you assumed.
- If something truly needs the person (a password, a payment, an account login, a decision only they can make), stop there and say exactly what.
- Deploys, store uploads, emails and public posts only when they asked for them.
- Finish with a short report. Its first sentence is read aloud, so make it one plain sentence that says the result. Then the details: what changed, where, and anything only the person can do.
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tail(text: str | None, limit: int = 6000) -> str:
    text = (text or "").strip()
    return text[-limit:]


class TaskRunner:
    def __init__(
        self,
        root: Path,
        satellite_url: Callable[[], str],
        satellite_token: Callable[[], str],
        agent: Callable[[], str],
        on_change: Callable[[dict], Awaitable[None]],
        on_finish: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.dir = root / "desk"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "tasks.json"
        self.satellite_url = satellite_url
        self.satellite_token = satellite_token
        self.agent = agent
        self.on_change = on_change
        self.on_finish = on_finish
        self._lock = threading.Lock()
        self.tasks: list[dict] = self._load()

    # Persistence

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.tasks[-300:], indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, task_id: str) -> dict | None:
        return next((t for t in self.tasks if t["id"] == task_id), None)

    def recent(self, limit: int = 30) -> list[dict]:
        return [self.public(t) for t in self.tasks[-limit:]][::-1]

    def running(self) -> list[dict]:
        return [t for t in self.tasks if t["status"] in ("queued", "running")]

    def _next_id(self) -> str:
        highest = 0
        for task in self.tasks:
            try:
                highest = max(highest, int(task["id"].lstrip("t")))
            except ValueError:
                continue
        return f"t{highest + 1}"

    @staticmethod
    def public(task: dict) -> dict:
        keys = (
            "id", "title", "project", "status", "agent", "job_id", "tracker_id", "created_at", "started_at",
            "finished_at", "last_note", "summary", "spoken", "error", "followups", "runs", "brief", "waiting",
        )
        out = {k: task.get(k) for k in keys}
        out["pending_followups"] = len(task.get("queue") or [])
        return out

    # Dispatch

    async def create(self, project: str | None, cwd: str | None, title: str, brief: str, origin: str) -> dict:
        task = {
            "id": self._next_id(),
            "title": (title or "Agent task").strip()[:60],
            "project": project,
            "cwd": cwd,
            "brief": brief.strip(),
            "origin": origin.strip(),
            "agent": self.agent(),
            "status": "queued",
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "job_id": None,
            "tracker_id": None,
            "last_note": None,
            "summary": None,
            "spoken": None,
            "error": None,
            "runs": 0,
            "followups": [],
            "queue": [],
        }
        self.tasks.append(task)
        self._save()
        await self._start(task)
        return task

    async def followup(self, task_id: str, message: str) -> dict | None:
        task = self.get(task_id)
        if task is None:
            return None
        task.setdefault("followups", []).append({"at": now_iso(), "text": message})
        task.setdefault("queue", []).append(message)
        if task["status"] not in ("queued", "running"):
            task["status"] = "queued"
            task["job_id"] = None
            task["finished_at"] = None
            task["error"] = None
            self._save()
            await self._start(task)
        else:
            self._save()
        return task

    async def cancel(self, task_id: str) -> dict | None:
        task = self.get(task_id)
        if task is None or task["status"] not in ("queued", "running"):
            return task
        if task.get("job_id"):
            try:
                await call(self.satellite_url(), "POST", f"/v1/agents/jobs/{task['job_id']}/cancel", self.satellite_token(), timeout=8)
            except RelayError as exc:
                get_logger("hub").warning("desk cancel failed", extra={"fields": {"task": task_id, "error": str(exc)[:200]}})
        task["status"] = "stopped"
        task["queue"] = []
        task["finished_at"] = now_iso()
        task["summary"] = task.get("summary") or "Stopped from the app."
        self._save()
        return task

    def _prompt(self, task: dict) -> str:
        header = WORKER_HEADER.format(task_id=task["id"])
        body = (
            f"{header}\nTask: {task['title']}\n"
            + (f"Project folder: {task['cwd']}\n" if task.get("cwd") else "")
            + f"\nThe request (may be a voice transcript with small errors): \"{task['origin']}\"\n\n"
            f"Brief from the desk:\n{task['brief']}\n"
        )
        if task.get("runs", 0) > 0:
            body += "\nAn earlier run already worked on this. Check what is done before you start, and don't redo finished work.\n"
            prior = (task.get("summary") or "").strip()
            if prior:
                body += f"Its last report:\n{prior[-1500:]}\n"
        extra = "\n".join(f"- {m}" for m in task.get("queue") or [])
        if extra:
            body += f"\nAdded since:\n{extra}\n"
        return body

    async def _start(self, task: dict) -> None:
        token = self.satellite_token()
        if not token:
            self._fail(task, "ORBITAL_SATELLITE_TOKEN is not set, so the hub can't start agents on this machine")
            return
        body: dict = {"agent": task["agent"], "prompt": self._prompt(task), "title": task["title"], "track": True, "by": "desk"}
        if task.get("cwd"):
            body["cwd"] = task["cwd"]
        try:
            answer = await call(self.satellite_url(), "POST", "/v1/agents/run", token, json_body=body, timeout=15)
        except RelayError as exc:
            if exc.status in (429, 502, 503):
                # Busy, or the satellite isn't up yet: keep it queued and try again on the next tick.
                note = "Waiting for the satellite service on this machine to take the job."
                if task.get("waiting") != note:
                    task["waiting"] = note
                    task["waiting_since"] = task.get("waiting_since") or now_iso()
                    task["last_note"] = note
                    self._save()
                return
            self._fail(task, str(exc))
            return
        job_id = answer.get("job_id") if isinstance(answer, dict) else None
        if not job_id:
            self._fail(task, "the satellite did not return a job id")
            return
        task.update(
            {
                "status": "running",
                "job_id": job_id,
                "tracker_id": answer.get("tracker_id") or task.get("tracker_id"),
                "runs": task.get("runs", 0) + 1,
                "started_at": task.get("started_at") or now_iso(),
                "run_started_at": now_iso(),
                "waiting": None,
                "waiting_since": None,
                "queue": [],
            }
        )
        if str(task.get("last_note") or "").startswith("Waiting for"):
            task["last_note"] = None
        self._save()

    def _fail(self, task: dict, error: str) -> None:
        task["status"] = "failed"
        task["error"] = error[:500]
        task["finished_at"] = now_iso()
        self._save()

    # Supervision

    async def tick(self) -> None:
        for task in list(self.tasks):
            if task["status"] == "queued":
                waited = _epoch(task.get("waiting_since"))
                if waited is not None and datetime.now(timezone.utc).timestamp() - waited > MAX_WAIT:
                    self._fail(task, f"No agent could start for a day. {task.get('waiting') or ''}".strip())
                    await self.on_finish(task)
                    await self.on_change(self.public(task))
                    continue
                await self._start(task)
                if task["status"] != "queued":
                    await self.on_change(self.public(task))
                continue
            if task["status"] != "running" or not task.get("job_id"):
                continue
            try:
                job = await call(self.satellite_url(), "GET", f"/v1/agents/jobs/{task['job_id']}", self.satellite_token(), timeout=6)
            except RelayError as exc:
                if exc.status == 404:
                    task["error"] = "the satellite no longer knows this job"
                    await self._finish(task, {"status": "failed"})
                continue
            if not isinstance(job, dict):
                continue
            job = job.get("job") if isinstance(job.get("job"), dict) else job
            if job.get("tracker_id") and not task.get("tracker_id"):
                task["tracker_id"] = job["tracker_id"]
            tail = _tail(job.get("output_tail"), 400)
            note = tail.splitlines()[-1][:400] if tail else None
            if job.get("status") in FINISHED:
                await self._finish(task, job)
            elif note and note != task.get("last_note"):
                task["last_note"] = note
                self._save()
                await self.on_change(self.public(task))

    async def _finish(self, task: dict, job: dict) -> None:
        status = job.get("status")
        output = _tail(job.get("output_tail"))
        if output:
            task["summary"] = output
        if task.get("queue") and status == "done":
            # More was asked while it ran: a new run picks it up with this report.
            task["status"] = "queued"
            task["job_id"] = None
            self._save()
            await self.on_change(self.public(task))
            await self._start(task)
            return
        if status == "done":
            task["status"] = "done"
        elif status == "cancelled":
            task["status"] = "stopped"
        else:
            task["status"] = "failed"
            exit_code = job.get("exit_code")
            task["error"] = task.get("error") or (f"the agent exited with code {exit_code}" if exit_code not in (None, 0) else "the agent stopped without a result")
        task["finished_at"] = now_iso()
        self._save()
        await self.on_finish(task)
        await self.on_change(self.public(task))

    async def run_forever(self, interval: float = 3.0) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                get_logger("hub").warning("desk supervisor error", extra={"fields": {"error": str(exc)[:200]}})
            await asyncio.sleep(interval)

    def tracker_ids(self) -> set[str]:
        return {t["tracker_id"] for t in self.tasks if t.get("tracker_id")}

    def for_tracker(self, item_id: str) -> Any:
        task = next((t for t in self.tasks if t.get("tracker_id") == item_id), None)
        return self.public(task) if task else None
