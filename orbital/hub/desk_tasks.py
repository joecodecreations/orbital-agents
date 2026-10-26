"""Work the Desk hands out, one satellite job per run, watched to the finish.

Every job starts through this machine's own satellite service
(POST http://127.0.0.1:<satellite.port>/v1/agents/run with ORBITAL_SATELLITE_TOKEN), as the
agent in desk.worker_agent. The satellite owns the process, the time limit, the output and the
tracker item (track: true), so a hub restart does not lose a job: the runner picks it back up
from tasks.json by job id.

The desk channel (docs/ARCHITECTURE.md, "Desk channel"): every run gets a token for its own task.
The agent reports through `orbital desk` (note, finding, answer, ask, blocked) and reads what the
person adds (inbox). An answer is spoken to the person as soon as it arrives; a follow-up on a
running task reaches the agent at its next report instead of waiting for the run to end. A
follow-up the agent never read still starts a new run when this one ends, told what the last one
reported. A satellite that is busy (or not up yet) leaves the task queued, and it starts on a
later tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from orbital.core import get_logger
from orbital.hub.satellites import RelayError, call

MAX_WAIT = 24 * 3600
FINISHED = {"done", "failed", "cancelled"}
EFFORTS = {"quick", "medium", "high", "xhigh"}
EFFORT_NOTES = {
    "quick": ("Effort: quick. This is a question that needs a lookup, not a build. Look only where you need to and "
              "change nothing. Send the answer with `orbital desk answer` the moment you have it (the person is waiting "
              "to hear it), then finish with the same answer as your report."),
    "medium": "Effort: medium. A small edit or lookup; keep it quick.",
    "high": "Effort: high. Do it properly and check your work.",
    "xhigh": "Effort: extra high. A big multi-step job: plan it, build it fully, test it, and verify the result end to end.",
}
# What an agent may send the desk, and the words agents reach for instead.
REPORT_KINDS = ("note", "finding", "answer", "question", "blocked")
REPORT_ALIASES = {"ask": "question", "progress": "note", "update": "note", "fact": "finding", "find": "finding",
                  "block": "blocked", "needs": "blocked", "result": "answer"}
MAX_REPORT_CHARS = 4000
KEEP_REPORTS = 60
KEEP_FINDINGS = 20
KEEP_INBOX = 50
# At the finish the report is read from this much of the job's log, so a long one is not cut mid-word.
REPORT_LOG_CHARS = 24_000
REPORT_KEEP_CHARS = 12_000
MAX_ENGINE_SWITCHES = 3
# Tracker item origins the satellite stamps (same set as the tracker app).
TRACKER_ORIGINS = {"ios", "mac", "direct", "agent"}


def _tracker_origin(raw: str | None) -> str | None:
    """ios/mac/direct/agent when known; None when we cannot source it."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in TRACKER_ORIGINS:
        return s
    if s in ("iphone", "ipad"):
        return "ios"
    if s in ("macos", "os", "catalyst", "desktop", "app"):
        return "mac"
    return None


WORKER_HEADER = """You were started from the Orbital Agents app's Desk (desk task {task_id}). The person who asked is away from this machine. They talk to the desk, often by voice, and the desk knows only what you report to it.

How to work:
- Follow the project's own instructions (CLAUDE.md, AGENTS.md, README) if it has them.
- Do the whole job end to end. Make the sensible call and say what you assumed; ask only when you truly can't go on.
- Deploys, store uploads, emails and public posts only when they asked for them.
- Do only the job in your brief; other agents may be working on other parts of the same request.

Report to the desk with `orbital desk` (already set up for this task; if `orbital` isn't found, use "$ORBITAL_CLI" in its place):
- `orbital desk note "<what just happened>" --progress <0-100>` at each milestone: how much of the whole job is done. It moves the progress bar the person watches and updates the tracker item too, so use it instead of `orbital-track update`. Never start a new tracker item.
- `orbital desk finding "<fact>"` for each fact they may ask about: a number, a name, a link, a file path, an exact quote. One short, self-contained fact each.
- `orbital desk answer "<answer>"` the moment you have the answer to what they asked. It is read aloud to them right away, so give the actual result (the number, the name, the text they asked for) in one to three plain spoken sentences.
- `orbital desk ask "<question>" --wait 300` only when you can't go on without them (which account, which of two options). It is read to them and prints their reply. With no reply, carry on with your best call and say so.
- `orbital desk blocked "<what you need>"` when only they can unblock it (a password, a payment, a login). Then stop.
- Every desk command prints anything new the person said. Treat it as part of your brief. On a long job, run `orbital desk inbox` between steps.
- If a desk command fails, carry on: reporting never blocks the work.

Finish with a short report, starting on a new paragraph. Its first sentence is read aloud, so make it one plain sentence that says the result. Then the details: what changed, where, every fact or piece of text they asked for in full, and anything only they can do.
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


def _set_tracker(task: dict, item_id: object) -> bool:
    """Point the task at its current run's tracker item, keeping the earlier runs' ones."""
    if not item_id or item_id == task.get("tracker_id"):
        return False
    known = task.setdefault("tracker_ids", [])
    for ident in (task.get("tracker_id"), item_id):
        if ident and ident not in known:
            known.append(ident)
    task["tracker_id"] = str(item_id)
    return True


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def _percent(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return min(100, max(0, int(round(float(str(value).strip().rstrip("%"))))))
    except (TypeError, ValueError):
        return None


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
        tracker: Callable[[str], dict | None] | None = None,
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
        # The task's tracker item, so `orbital-track update` notes and progress reach the desk too.
        self.tracker = tracker
        self._lock = threading.Lock()
        self._inbox_ready: dict[str, asyncio.Event] = {}
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
        """What the apps see. Never the channel token."""
        keys = (
            "id", "title", "project", "status", "agent", "job_id", "tracker_id", "created_at", "started_at",
            "finished_at", "last_note", "summary", "spoken", "error", "followups", "runs", "brief", "waiting",
            "effort", "machine", "client_platform", "progress", "answer", "question", "blocked",
        )
        out = {k: task.get(k) for k in keys}
        out["pending_followups"] = len(task.get("queue") or [])
        out["findings"] = [f["text"] for f in (task.get("findings") or [])[-8:]]
        out["notes"] = [{"at": n.get("at"), "text": n.get("text")} for n in (task.get("notes") or [])[-5:]]
        out["inbox_unread"] = sum(1 for m in task.get("inbox") or [] if not m.get("delivered_at"))
        return out

    # Dispatch

    async def create(self, project: str | None, cwd: str | None, title: str, brief: str, origin: str,
                     effort: str | None = None, agent: str | None = None, machine: str | None = None,
                     client_platform: str | None = None) -> dict:
        platform = _tracker_origin(client_platform)
        task = {
            "id": self._next_id(),
            "title": (title or "Agent task").strip()[:60],
            "project": project,
            "cwd": cwd,
            "brief": brief.strip(),
            "origin": origin.strip(),
            "client_platform": platform,
            "agent": None if self.choose else self.agent(),
            "agent_wanted": (agent or "").strip().lower() or None,
            "machine": (machine or "").strip() or None,
            "satellite": None,
            "effort": effort if effort in EFFORTS else "high",
            "engines_down": [],
            # The agent's own key to this task's desk channel (report, inbox); nothing else.
            "channel_token": secrets.token_urlsafe(24),
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
        if task["status"] == "running":
            # A running agent gets it at its next desk command; unread, it starts the next run.
            self._post_inbox(task, message, "reply" if task.get("question") else "followup")
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
        if not task.get("channel_token"):
            task["channel_token"] = secrets.token_urlsafe(24)  # a task saved before the desk channel
        body: dict = {"agent": task["agent"], "prompt": self._prompt(task), "title": task["title"], "track": True, "by": "desk",
                      "desk": {"task": task["id"], "token": task["channel_token"]}}
        if task.get("cwd"):
            body["cwd"] = task["cwd"]
        # Stamp the tracker item with where the user asked from (iOS / Mac app), when known.
        if task.get("client_platform") in TRACKER_ORIGINS:
            body["origin"] = task["client_platform"]
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
                # Asked and blocked belong to a run; findings and the answer stay until replaced.
                "question": None,
                "blocked": None,
                "progress": 0 if not task.get("runs") else task.get("progress"),
            }
        )
        # Everything in the inbox went into this run's prompt.
        for message in task.get("inbox") or []:
            if not message.get("delivered_at"):
                message["delivered_at"] = now_iso()
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
            # Each run is its own satellite job with its own tracker item.
            moved = _set_tracker(task, job.get("tracker_id"))
            if job.get("status") in FINISHED:
                await self._finish(task, job)
                continue
            changed = self._merge_tracker(task) or moved
            if not task.get("notes"):
                # An agent that reports nothing: the last line it printed.
                tail = _tail(job.get("output_tail"), 400)
                note = tail.splitlines()[-1][:400] if tail else None
                if note and note != task.get("last_note"):
                    task["last_note"] = note
                    changed = True
            if changed:
                self._save()
                await self.on_change(self.public(task))

    async def _report_text(self, task: dict) -> str:
        """The end of the job's whole log: `output_tail` is only the last 4,000 characters."""
        if not task.get("job_id"):
            return ""
        try:
            url, token = self._where(task)
            answer = await call(url, "GET", f"/v1/agents/jobs/{task['job_id']}/log", token, timeout=10)
        except RelayError:
            return ""
        log = answer.get("log") if isinstance(answer, dict) else None
        return _tail(log, REPORT_LOG_CHARS) if isinstance(log, str) else ""

    async def _finish(self, task: dict, job: dict) -> None:
        status = job.get("status")
        output = _tail(job.get("output_tail"))
        fuller = await self._report_text(task)
        if len(fuller) > len(output):
            output = fuller
        if output:
            task["summary"] = final_report(output, REPORT_KEEP_CHARS)
        task["question"] = None
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
            task["progress"] = 100
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

    # The desk channel: what the agent reports, and what the person adds while it runs

    def by_token(self, task_id: str, token: str | None) -> dict | None:
        """The task, when `token` is its channel token (constant time); None otherwise."""
        task = self.get(task_id)
        want = (task or {}).get("channel_token")
        if not task or not token or not want:
            return None
        return task if hmac.compare_digest(str(token).encode(), str(want).encode()) else None

    def _add_note(self, task: dict, text: str, at: str, source: str) -> bool:
        notes = task.setdefault("notes", [])
        key = _norm(text)
        if any(_norm(n.get("text")) == key for n in notes[-KEEP_REPORTS:]):
            return False
        notes.append({"at": at, "text": text[:MAX_REPORT_CHARS], "source": source})
        notes.sort(key=lambda n: n.get("at") or "")
        del notes[:-KEEP_REPORTS]
        task["last_note"] = notes[-1]["text"][:400]
        return True

    def _merge_tracker(self, task: dict) -> bool:
        """`orbital-track update` notes and progress on the task's tracker item, this run only."""
        if not self.tracker or not task.get("tracker_id"):
            return False
        try:
            item = self.tracker(task["tracker_id"])
        except Exception:  # noqa: BLE001 - a bad items.json must not stop supervision
            return False
        if not isinstance(item, dict):
            return False
        since = task.get("run_started_at") or ""
        changed = False
        for note in item.get("notes") or []:
            text = str((note or {}).get("text") or "").strip()
            at = str((note or {}).get("at") or "")
            if text and (not since or not at or at >= since):
                changed = self._add_note(task, text, at or now_iso(), "tracker") or changed
        progress = _percent(item.get("progress"))
        updated = str(item.get("updatedAt") or "")
        if progress is not None and progress != task.get("progress") and updated >= (task.get("progress_at") or ""):
            task["progress"] = progress
            task["progress_at"] = updated or now_iso()
            changed = True
        return changed

    def report(self, task: dict, kind: str, text: str, progress: Any = None) -> dict:
        """One report from the task's agent: {"kind", "text", "announce"}. ValueError for a bad one."""
        kind = REPORT_ALIASES.get(str(kind or "").strip().lower(), str(kind or "").strip().lower())
        if kind not in REPORT_KINDS:
            raise ValueError(f"kind must be one of {', '.join(REPORT_KINDS)}")
        text = re.sub(r"[ \t]+\n", "\n", str(text or "")).strip()[:MAX_REPORT_CHARS]
        pct = _percent(progress)
        if not text and not (kind == "note" and pct is not None):
            raise ValueError("text is required")
        at = now_iso()
        running = task.get("status") == "running"
        if pct is not None:
            task["progress"] = pct
            task["progress_at"] = at
        reports = task.setdefault("reports", [])
        reports.append({"at": at, "kind": kind, "text": text[:600], "run": task.get("runs")})
        del reports[:-KEEP_REPORTS]
        announce = False
        if kind == "note" and text:
            self._add_note(task, text, at, "desk")
        elif kind == "finding":
            findings = task.setdefault("findings", [])
            if all(_norm(f.get("text")) != _norm(text) for f in findings):
                findings.append({"at": at, "text": text})
                del findings[:-KEEP_FINDINGS]
        elif kind == "answer":
            announce = _norm(text) != _norm(task.get("answer"))
            task.update({"answer": text, "answer_at": at, "answer_run": task.get("runs")})
        elif kind == "question":
            announce = running
            task.update({"question": text, "question_at": at})
        elif kind == "blocked":
            announce = running
            task.update({"blocked": text, "blocked_at": at})
        self._save()
        return {"kind": kind, "text": text, "announce": announce}

    def _post_inbox(self, task: dict, text: str, kind: str = "followup") -> dict:
        inbox = task.setdefault("inbox", [])
        message = {"n": (inbox[-1]["n"] + 1) if inbox else 1, "at": now_iso(), "kind": kind, "text": text, "delivered_at": None}
        inbox.append(message)
        del inbox[:-KEEP_INBOX]
        ready = self._inbox_ready.get(task["id"])
        if ready is not None:
            ready.set()
        return message

    def take_inbox(self, task: dict) -> list[dict]:
        """What the person added since the agent last looked. Read ones won't start another run."""
        fresh = [m for m in task.get("inbox") or [] if not m.get("delivered_at")]
        if not fresh:
            return []
        at = now_iso()
        queue = task.setdefault("queue", [])
        for message in fresh:
            message["delivered_at"] = at
            if message["text"] in queue:
                queue.remove(message["text"])
        if task.get("question"):
            task["question"] = None  # the person's reply is on its way to the agent
        self._save()
        return [{"n": m["n"], "at": m["at"], "kind": m["kind"], "text": m["text"]} for m in fresh]

    async def wait_inbox(self, task: dict, timeout: float) -> list[dict]:
        """take_inbox, waiting up to `timeout` seconds for something to arrive."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        ready = self._inbox_ready.setdefault(task["id"], asyncio.Event())
        while True:
            ready.clear()
            got = self.take_inbox(task)
            remaining = deadline - loop.time()
            if got or remaining <= 0 or task.get("status") != "running":
                return got
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ready.wait(), remaining)

    def tracker_ids(self) -> set[str]:
        """Every tracker item a desk task made, one per run."""
        return {tid for t in self.tasks for tid in (t.get("tracker_id"), *(t.get("tracker_ids") or [])) if tid}

    def for_tracker(self, item_id: str) -> Any:
        task = next((t for t in self.tasks if item_id == t.get("tracker_id") or item_id in (t.get("tracker_ids") or [])), None)
        return self.public(task) if task else None
