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
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from orbital.core import get_logger
from orbital.hub.satellites import RelayError, call

MAX_WAIT = 24 * 3600
FINISHED = {"done", "failed", "cancelled"}
EFFORTS = {"medium", "high", "xhigh"}
EFFORT_NOTES = {
    "medium": "Effort: medium. A small edit or lookup; keep it quick.",
    "high": "Effort: high. Do it properly and check your work.",
    "xhigh": "Effort: extra high. A big multi-step job: plan it, build it fully, test it, and verify the result end to end.",
}
MAX_ENGINE_SWITCHES = 3

WORKER_HEADER = """You were started from the Orbital Agents app's Desk (desk task {task_id}). The person who asked is away from this machine and hears about your work only through the desk.

How to work:
- Follow the project's own instructions (CLAUDE.md, AGENTS.md, README) if it has them.
- Do the whole job end to end. Do not stop to ask questions; nobody can answer mid-run. Make the sensible call and say what you assumed.
- If something truly needs the person (a password, a payment, an account login, a decision only they can make), stop there and say exactly what.
- Deploys, store uploads, emails and public posts only when they asked for them.
- Do only the job in your brief; other agents may be working on other parts of the same request.
- Finish with a short report, starting on a new paragraph. Its first sentence is read aloud, so make it one plain sentence that says the result. Then the details: what changed, where, and anything only the person can do.
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


# Where one streamed message ends and the next begins with no space between them ("…lookups.I'll…").
RUN_TOGETHER = re.compile(r"(?<=[.!?:)])(?=(?:\*\*[A-Z]|#{1,3} |[A-Z][a-z']))")


def final_report(text: str | None, limit: int = 6000) -> str:
    """The worker's closing report, without the progress chatter some CLIs print before it."""
    text = (text or "").strip()
    if not text:
        return ""
    parts = [p.strip() for p in RUN_TOGETHER.split(text) if p.strip()]
    for part in reversed(parts):
        if len(part) >= 40 or part is parts[0]:
            start = text.rfind(part)
            return text[start:][-limit:].strip()
    return text[-limit:]


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
        choose: Callable[[dict], dict] | None = None,
        locate: Callable[[str], tuple[str, str] | None] | None = None,
        engine_down: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.dir = root / "desk"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "tasks.json"
        self.satellite_url = satellite_url
        self.satellite_token = satellite_token
        self.agent = agent
        self.on_change = on_change
        self.on_finish = on_finish
        # Which agent and machine a task runs on (the desk's pick), where a machine is, and whether a
        # failed run's output says its engine is out (usage, sign-in) so the task moves to the next one.
        self.choose = choose
        self.locate = locate
        self.engine_down = engine_down
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
            "effort", "machine",
        )
        out = {k: task.get(k) for k in keys}
        out["pending_followups"] = len(task.get("queue") or [])
        return out

    # Dispatch

    async def create(self, project: str | None, cwd: str | None, title: str, brief: str, origin: str,
                     effort: str | None = None, agent: str | None = None, machine: str | None = None) -> dict:
        task = {
            "id": self._next_id(),
            "title": (title or "Agent task").strip()[:60],
            "project": project,
            "cwd": cwd,
            "brief": brief.strip(),
            "origin": origin.strip(),
            "agent": None if self.choose else self.agent(),
            "agent_wanted": (agent or "").strip().lower() or None,
            "machine": (machine or "").strip() or None,
            "satellite": None,
            "effort": effort if effort in EFFORTS else "high",
            "engines_down": [],
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
                url, token = self._where(task)
                await call(url, "POST", f"/v1/agents/jobs/{task['job_id']}/cancel", token, timeout=8)
            except RelayError as exc:
                get_logger("hub").warning("desk cancel failed", extra={"fields": {"task": task_id, "error": str(exc)[:200]}})
        task["status"] = "stopped"
        task["queue"] = []
        task["finished_at"] = now_iso()
        task["summary"] = task.get("summary") or "Stopped from the app."
        self._save()
        return task

    def _where(self, task: dict) -> tuple[str, str]:
        """The satellite a task runs on: its machine when it has one, else this machine's own."""
        if task.get("satellite") and self.locate:
            found = self.locate(task["satellite"])
            if found:
                return found
        return self.satellite_url(), self.satellite_token()

    def _prompt(self, task: dict) -> str:
        header = WORKER_HEADER.format(task_id=task["id"])
        body = (
            f"{header}\nTask: {task['title']}\n"
            + (f"Project folder: {task['cwd']}\n" if task.get("cwd") else "")
            + f"{EFFORT_NOTES.get(task.get('effort') or 'high', '')}\n"
            + f"\nYour job is only the brief below. The person's whole message, for context (it may be a voice transcript with small "
            f"errors, and other agents may be doing its other parts, so don't do those): \"{task['origin']}\"\n\n"
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

    def _wait(self, task: dict, note: str) -> None:
        if task.get("waiting") != note:
            task["waiting"] = note
            task["waiting_since"] = task.get("waiting_since") or now_iso()
            task["last_note"] = note
            self._save()

    async def _start(self, task: dict) -> None:
        if self.choose and not task.get("agent"):
            pick = self.choose(task)
            if pick.get("wait"):
                self._wait(task, str(pick["wait"]))
                return
            task["agent"] = pick["agent"]
            task["satellite"] = pick.get("satellite")
        url, token = self._where(task)
        if not token:
            self._fail(task, "ORBITAL_SATELLITE_TOKEN is not set, so the hub can't start agents on this machine")
            return
        body: dict = {"agent": task["agent"], "prompt": self._prompt(task), "title": task["title"], "track": True, "by": "desk"}
        if task.get("cwd"):
            body["cwd"] = task["cwd"]
        try:
            answer = await call(url, "POST", "/v1/agents/run", token, json_body=body, timeout=15)
        except RelayError as exc:
            if exc.status in (429, 502, 503):
                # Busy, or the satellite isn't up yet: keep it queued and try again on the next tick.
                where = task.get("machine") or "this machine"
                self._wait(task, f"Waiting for {where} to take the job (its agents are busy or it isn't up yet).")
                if self.choose:
                    task["agent"] = None  # pick again next time: another agent may be free
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
                url, token = self._where(task)
                job = await call(url, "GET", f"/v1/agents/jobs/{task['job_id']}", token, timeout=6)
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
            task["summary"] = final_report(output)
        if task.get("queue") and status == "done":
            # More was asked while it ran: a new run picks it up with this report.
            task["status"] = "queued"
            task["job_id"] = None
            self._save()
            await self.on_change(self.public(task))
            await self._start(task)
            return
        if (status == "failed" and self.choose and self.engine_down and task.get("agent")
                and len(task.get("engines_down") or []) < MAX_ENGINE_SWITCHES
                and self.engine_down(task["agent"], output or str(job.get("error") or ""))):
            # Its engine ran out (usage, sign-in), not the job: the next agent that can run picks it up.
            task.setdefault("engines_down", []).append(task["agent"])
            task["last_note"] = f"{task['agent']} can't run right now; handing the job to the next agent."
            task["status"] = "queued"
            task["job_id"] = None
            task["agent"] = None
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
