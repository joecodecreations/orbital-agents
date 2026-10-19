"""The Desk: one thread, a fast model that answers at once, and agents for the real work.

A turn:
1. The user's words are saved and sent to the fast model (desk_model.XAIProvider) with the
   project list from config (desk.projects), the desk's tasks, and the tracker's active items.
2. The model streams a short spoken reply, then `>>>` action lines (task, followup, cancel,
   todo, reminder, clear). The reply streams to the app as it arrives.
3. Actions run after the reply: a task starts desk.worker_agent through this machine's
   satellite service (desk_tasks.TaskRunner); a to-do or reminder goes to Apple Reminders.
4. When a job finishes, the desk posts an update into the thread, raises an alert, and the
   app reads the update aloud if voice is on.

Turns run in the background, so an app that drops mid-reply does not lose the actions.

The user can talk over a reply. The app then sends `interrupt` ({reply_id, heard}) with the
next words: a reply still being written is cut (its actions never run), and the model is told
what was heard and what was missed, so it handles the new words first and then picks the old
point back up.

Without XAI_API_KEY the desk is "not configured": the thread still loads, and every message
gets one clear answer saying what to add.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from orbital.core import get_logger
from orbital.hub import echo_guard, reminders
from orbital.hub.desk_model import ModelError, XAIProvider
from orbital.hub.desk_tasks import TaskRunner, now_iso
from orbital.hub.tracker import _agent_name, first_sentence
from orbital.hub.web import body_of, fail

HISTORY_TURNS = 24
THREAD_KEEP = 2000
MARK = ">>>"
MARK_LINE = re.compile(r"(^|\n)[ \t]*>>>")
NOT_CONFIGURED = (
    "The Desk isn't set up yet: add XAI_API_KEY to ~/.orbital-agents/secrets.env on the hub (it is picked up "
    "without a restart). Until then, start agents from the Agents tab or with `orbital agents run`."
)

SYSTEM_RULES = """You are the front desk of Orbital Agents. The user talks to you from the Orbital Agents app, often by voice while away from the computer. Your job is to answer instantly and hand real work to bigger AI agents that run on the user's machines. You are the dispatcher, not the worker.

Your reply:
- One or two short sentences that will be read aloud. Plain words only: no markdown, lists, emoji, code, or file paths read out character by character.
- Sound like a quick, capable assistant. Confirm what you're doing in the user's terms ("On it, I'll get an agent adding dark mode to the website.").
- Never say work is finished unless the live state below shows it finished. Don't invent results.
- If it's genuinely unclear which project or what they want, ask one short question instead of acting.
- Status questions ("what's running", "is X done", "what did the agent do"): answer from the live state below.
- The user can talk over you, like in a real conversation. When their message says they cut you off, deal with what they just said first (start the work if it's a request). Then, only if the part they missed still matters, come back to it in a few words. Never repeat what they already heard.

Actions: after your reply, write zero or more lines that start with >>> and hold one JSON object each, on one line:
>>> {"type":"task","project":"<name from the project list, or empty>","title":"<2-5 word title>","brief":"<everything the agent needs>"}
>>> {"type":"followup","task":"<task id like t3>","message":"<what to add or change>"}
>>> {"type":"cancel","task":"<task id>"}
>>> {"type":"todo","title":"<short to-do for the user>","notes":"<optional>"}
>>> {"type":"reminder","title":"<what>","due":"<YYYY-MM-DDTHH:MM in the user's local time>","notes":"<optional>"}
>>> {"type":"clear"}

When to use which:
- task: anything that needs work on a computer (code, fixes, features, research, builds, writing, checking a site, reading files to answer). One task per separate job. The brief is the agent's only context: restate the request fully, name the project, say what done looks like, and include details from earlier in this conversation.
- followup: the user adds to, corrects, or extends a task that already exists (running or finished). Prefer this over starting a duplicate.
- cancel: they want a running task stopped.
- todo: something the user has to do personally, no time. reminder: there is a time ("tomorrow at 9", "in two hours"); resolve it against the current time below.
- clear: the user asks you to clear or reset your context. Running work is not affected; say so.
- No action for chat, quick facts you know, or questions the live state answers.
"""

# Clearing the model's context. The thread in the app stays; the model only sees what came after
# the last clear, plus the notes written at the clear and the live state (tasks, tracker).
CLEAR_IDLE = 30 * 60
CLEAR_LONG = 40
CLEAR_ASK = (
    "(Not from the user: the desk is clearing your conversation context now. Write the notes you need to "
    "carry on afterwards: what the user is in the middle of, decisions and preferences they stated, anything "
    "they asked you to remember or hold off on, and questions still open. Skip desk tasks and tracker "
    "items; those stay in the live state. Short plain lines, at most 12, no >>> actions.)"
)
DEAD_REPLIES = {"Sorry, say that again?", NOT_CONFIGURED}


def _new_id(prefix: str = "m") -> str:
    return f"{prefix}{int(time.time() * 1000):x}{secrets.token_hex(2)}"


def _within(at: str | None, seconds: float) -> bool:
    return _age_s(at) <= seconds


def _clean_spoken(text: str) -> str:
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"\s+\n", "\n", text)
    return text.strip()


class ReplySplitter:
    """Streams the spoken reply and holds back `>>>` action lines."""

    def __init__(self) -> None:
        self.text = ""
        self.emitted = 0
        self.cut: int | None = None

    def feed(self, chunk: str) -> str:
        self.text += chunk
        if self.cut is not None:
            return ""
        match = MARK_LINE.search(self.text)
        if match:
            self.cut = match.start()
            end = self.cut
        else:
            line_start = self.text.rfind("\n") + 1
            tail = self.text[line_start:].lstrip()
            # A last line of ">" or ">>" might become an action line; wait for more.
            end = line_start if tail and MARK.startswith(tail) else len(self.text)
        # Trailing whitespace waits for the next chunk, so the reply never ends in a stray newline.
        while end > self.emitted and self.text[end - 1].isspace():
            end -= 1
        out = self.text[self.emitted : end] if end > self.emitted else ""
        self.emitted = max(self.emitted, end)
        return out

    def finish(self) -> tuple[str, list[dict]]:
        reply_lines: list[str] = []
        actions: list[dict] = []
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith(MARK):
                raw = stripped[len(MARK) :].strip()
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    # Tolerate a trailing comma or stray text after the object.
                    end = raw.rfind("}")
                    try:
                        obj = json.loads(raw[: end + 1]) if end > 0 else None
                    except json.JSONDecodeError:
                        obj = None
                if isinstance(obj, dict) and obj.get("type"):
                    actions.append(obj)
            else:
                reply_lines.append(line)
        return _clean_spoken("\n".join(reply_lines)), actions


class Projects:
    """Where desk tasks may run: [[desk.projects]] in config.toml ({name, path, about?, aliases?}), or none."""

    def __init__(self, rows: object) -> None:
        self.rows: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("path"), str):
                aliases = row.get("aliases") if isinstance(row.get("aliases"), list) else []
                self.rows.append({"name": row["name"], "path": row["path"], "about": str(row.get("about") or ""),
                                  "aliases": [str(a) for a in aliases]})

    def prompt_block(self) -> str:
        if not self.rows:
            return "(No projects are configured. Leave project empty: the agent starts in the machine's default folder.)"
        return "\n".join(f"- {r['name']}: {r['about'] or r['path']}" for r in self.rows)

    def resolve(self, name: object) -> dict | None:
        want = str(name or "").strip().lower()
        if not want:
            return None
        for row in self.rows:
            if want in (row["name"].lower(), row["path"].lower(), *(a.lower() for a in row["aliases"])):
                return row
        return None


class Desk:
    def __init__(self, hub: Any, runner: TaskRunner | None = None, provider: Any = None, add_reminder=None) -> None:
        self.hub = hub
        self.dir = hub.paths.hub_data / "desk"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.thread_path = self.dir / "thread.jsonl"
        self._fixed_provider = provider
        self._provider: Any = None
        self._provider_key = ""
        port = int(hub.cfg.get("satellite.port", 7704))
        self.runner = runner or TaskRunner(
            hub.paths.hub_data,
            satellite_url=lambda: f"http://127.0.0.1:{port}",
            satellite_token=lambda: hub.auth.get("ORBITAL_SATELLITE_TOKEN"),
            agent=lambda: str(hub.cfg.get("desk.worker_agent") or "claude"),
            on_change=self._task_changed,
            on_finish=self._task_finished,
        )
        self.add_reminder = add_reminder or reminders.add
        self.messages: list[dict] = self._load()
        self._write_lock = asyncio.Lock()
        self._clear_lock = asyncio.Lock()
        # Replies still being written, by reply id, so the user's next words can cut them off.
        self._live: dict[str, _Turn] = {}

    # The model

    @property
    def provider(self) -> Any:
        """The Agent tab's model: the Grok subscription (then Claude's, then the Mac's own model), the
        xAI API first only when XAI_API_KEY is set. None when desk.provider = "off". Follows secrets.env."""
        if self._fixed_provider is not None:
            return self._fixed_provider
        if str(self.hub.cfg.get("desk.provider") or "auto").lower() in ("off", "none"):
            return None
        key = self.hub.auth.get("XAI_API_KEY") or ""
        if key != self._provider_key or self._provider is None:
            from orbital.hub.desk_engines import build_fallback
            self._provider = build_fallback(
                key, self.hub.paths.hub_data,
                cli_model=str(self.hub.cfg.get("desk.cli_model") or ""),
                claude_model=str(self.hub.cfg.get("desk.claude_model") or "") or None,
                work_note=lambda: "Agents you start run on the satellites as usual.",
            )
            self._provider_key = key
        return self._provider

    def model_info(self) -> dict:
        current = self.provider
        if current is None:
            return {"configured": False, "provider": None, "model": None, "note": NOT_CONFIGURED,
                    "worker_agent": self.hub.cfg.get("desk.worker_agent")}
        return {"configured": True, "provider": current.name, "model": current.label, "note": None,
                "engines": current.public() if hasattr(current, "public") else [],
                "worker_agent": self.hub.cfg.get("desk.worker_agent")}

    def projects(self) -> Projects:
        return Projects(self.hub.cfg.get("desk.projects", []))

    # Thread

    def _load(self) -> list[dict]:
        if not self.thread_path.is_file():
            return []
        rows = []
        with self.thread_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-THREAD_KEEP:]

    async def _append(self, message: dict) -> dict:
        async with self._write_lock:
            self.messages.append(message)
            with self.thread_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
            if len(self.messages) > THREAD_KEEP * 1.5:
                self.messages = self.messages[-THREAD_KEEP:]
                tmp = self.thread_path.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in self.messages), encoding="utf-8")
                tmp.replace(self.thread_path)
        await self.hub.live.broadcast({"type": "desk_message", "message": message})
        return message

    def page(self, before: str | None = None, limit: int = 60) -> list[dict]:
        rows = self.messages
        if before:
            idx = next((i for i, m in enumerate(rows) if m["id"] == before), None)
            if idx is not None:
                rows = rows[:idx]
        return rows[-max(1, min(limit, 300)) :]

    def snapshot(self, limit: int = 60) -> dict:
        return {
            "messages": self.page(limit=limit),
            "tasks": self.runner.recent(),
            "model": self.model_info(),
            "projects": [{"name": r["name"], "about": r["about"]} for r in self.projects().rows],
            "tracker_active": sum(1 for row in self.hub.tracker.items() if row.get("status") == "active"),
        }

    async def warm(self) -> None:
        provider = self.provider
        if provider is not None:
            with contextlib.suppress(Exception):
                await provider.warm()

    # Clearing the context without losing track of work

    def _last_clear(self) -> dict | None:
        return next((m for m in reversed(self.messages) if m.get("kind") == "context"), None)

    def _carried(self) -> str:
        return str((self._last_clear() or {}).get("carry") or "").strip()

    def _since_clear(self) -> list[dict]:
        start = next((i + 1 for i in range(len(self.messages) - 1, -1, -1) if self.messages[i].get("kind") == "context"), 0)
        return self.messages[start:]

    async def _notes_for_clear(self, rows: list[dict]) -> str:
        """The model writes what to carry over; the user's last messages if it can't."""
        if not any(m["role"] == "user" for m in rows):
            return self._carried()
        provider = self.provider
        if provider is not None:
            history = [*self.history(rows[-1]["id"]), {"role": "user", "content": CLEAR_ASK}]
            if len(history) >= 2 and history[-2] == {"role": "user", "content": "(continue)"}:
                history.pop(-2)
            try:
                splitter = ReplySplitter()
                async for chunk in provider.stream(self.system_prompt(), history):
                    splitter.feed(chunk)
                notes, _ = splitter.finish()  # never run actions from a summary
                if notes.strip():
                    return notes.strip()[:2500]
            except Exception as exc:
                get_logger("hub").info("desk clear notes failed", extra={"fields": {"error": str(exc)[:160]}})
        said = [m["text"] for m in rows if m["role"] == "user" and m.get("text")][-8:]
        return "The user's last messages before the clear:\n" + "\n".join(f"- {t[:240]}" for t in said)

    async def clear_context(self, why: str = "cleared") -> dict:
        async with self._clear_lock:
            notes = await self._notes_for_clear(self._since_clear())
            running = len(self.runner.running())
            text = f"Fresh context ({why}). Kept: {running} task{'s' if running != 1 else ''} running or queued, the tracker, and notes from the conversation."
            marker = await self._append(self._event("context", text, carry=notes or None))
            get_logger("hub").info("desk context cleared", extra={"fields": {"why": why}})
            return marker

    async def maybe_clear(self) -> None:
        """Clear in a pause, never mid-reply: after half an hour quiet, or a long talk."""
        if self._live or self._clear_lock.locked():
            return
        rows = self._since_clear()
        said = sum(1 for m in rows if m["role"] == "user")
        if not said or not rows:
            return
        idle = _age_s(rows[-1].get("at"))
        if idle >= CLEAR_IDLE and said >= 2:
            await self.clear_context("new conversation after a pause")
        elif said >= CLEAR_LONG and idle >= 120:
            await self.clear_context("long conversation")

    async def housekeeping(self, interval: float = 60.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.maybe_clear()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                get_logger("hub").warning("desk housekeeping failed", extra={"fields": {"error": str(exc)[:200]}})

    # Prompt

    def system_prompt(self) -> str:
        now = datetime.now().astimezone()
        lines = [SYSTEM_RULES, "Projects (name: about):", self.projects().prompt_block(), "", "Live state"]
        lines.append(f"Now: {now.strftime('%A %Y-%m-%d %H:%M')} ({now.tzname()}).")
        carry = self._carried()
        if carry:
            lines.append("Notes from before your context was last cleared (still true unless the user changed it since):")
            lines.append(carry)
        running = self.runner.running()
        if running:
            lines.append("Desk tasks running or queued:")
            for task in running:
                note = f" — latest: {task['last_note']}" if task.get("last_note") else ""
                where = f" in {task['project']}" if task.get("project") else ""
                lines.append(f"- {task['id']} [{task['status']}] {task['title']}{where} (started {_ago(task.get('started_at') or task['created_at'])}){note}")
        else:
            lines.append("Desk tasks running: none.")
        finished = [t for t in self.runner.tasks if t["status"] in ("done", "failed", "stopped") and _age_s(t.get("finished_at")) < 2 * 86400][-8:]
        if finished:
            lines.append("Desk tasks finished recently:")
            for task in reversed(finished):
                result = first_sentence(task.get("summary")) or task.get("error") or ""
                lines.append(f"- {task['id']} [{task['status']}] {task['title']}, {_ago(task.get('finished_at'))}: {result}")
        desk_items = self.tracker_ids()
        items = self.hub.tracker.items()
        active = [i for i in items if i.get("status") == "active" and i.get("id") not in desk_items][:12]
        if active:
            lines.append("Other work in the tracker right now:")
            for item in active:
                who = _agent_name(item.get("agent")) if item.get("source") == "agent" else "The user"
                where = f" in {Path(item['workspace']).name}" if item.get("workspace") else ""
                notes = item.get("notes") or []
                note = f" — latest: {notes[-1]['text'][:160]}" if notes else ""
                lines.append(f"- {who}: {item.get('title')}{where}{note}")
        done_other = [
            i for i in items
            if i.get("status") == "done" and i.get("id") not in desk_items and _age_s(i.get("completedAt")) < 86400
        ]
        done_other.sort(key=lambda i: i.get("completedAt") or "", reverse=True)
        if done_other:
            lines.append("Finished in the tracker today:")
            for item in done_other[:8]:
                who = _agent_name(item.get("agent")) if item.get("source") == "agent" else "The user"
                lines.append(f"- {who}: {item.get('title')} — {first_sentence(item.get('summary'), 160)}")
        return "\n".join(lines)

    def history(self, upto_id: str) -> list[dict]:
        rows: list[dict] = []
        for msg in self.messages:
            if msg.get("kind") == "context":
                rows = []  # a clear: the model starts over from here
                if msg["id"] == upto_id:
                    break
                continue
            rows.append(msg)
            if msg["id"] == upto_id:
                break
        rows = rows[-HISTORY_TURNS:]
        cuts = {m["interrupt"]["reply_id"]: m["interrupt"] for m in rows if m["role"] == "user" and (m.get("interrupt") or {}).get("reply_id")}
        out = []
        for index, msg in enumerate(rows):
            if msg["role"] == "user":
                content = msg["text"]
                if index == len(rows) - 1 and msg.get("interrupt"):
                    content += "\n\n" + self._cut_note(msg["interrupt"])
                out.append({"role": "user", "content": content})
            elif msg["role"] == "assistant":
                if msg.get("error") or msg["text"] in DEAD_REPLIES:
                    continue  # a reply no model wrote; models copy it if they see it
                text = msg["text"]
                cut = cuts.get(msg["id"])
                if cut is not None:
                    heard, missed = _split_heard(text, cut.get("heard") or "")
                    if not heard:
                        text = f"[The user cut in before hearing this reply: \"{text}\"]"
                    elif missed:
                        text = f"{heard} [The user cut in here and didn't hear: \"{missed}\"]"
                if msg.get("did"):
                    text += " [did: " + "; ".join(msg["did"]) + "]"
                out.append({"role": "assistant", "content": text})
            elif msg.get("kind") != "not_configured":
                out.append({"role": "assistant", "content": f"[update] {msg['text']}"})
        if not out or out[-1]["role"] != "user":
            out.append({"role": "user", "content": "(continue)"})
        return out

    def _cut_note(self, interrupt: dict) -> str:
        reply = next((m for m in reversed(self.messages) if m["id"] == interrupt.get("reply_id")), None)
        heard, missed = _split_heard(reply["text"] if reply else "", interrupt.get("heard") or "")
        if reply is None or not (heard or missed):
            return "(The user said this while you were answering, before hearing your reply.)"
        if not heard:
            return (
                "(The user said this before hearing any of your last reply, so they missed all of it: "
                f"\"{missed}\". Answer this first; bring up anything from that reply they still need.)"
            )
        if not missed:
            return "(The user cut in at the very end of your last reply; they heard all of it.)"
        return (
            f"(The user cut you off. They heard: \"{heard}\" They did not hear: \"{missed}\". Handle what they just said first. "
            "Then, if what they missed still matters, come back to it briefly.)"
        )

    # Turns

    def say(self, text: str, source: str = "text", client_id: str | None = None, interrupt: dict | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        asyncio.get_running_loop().create_task(self._turn(queue, text, source, client_id, interrupt))
        return queue

    async def _cut(self, reply_id: str | None) -> str | None:
        """Stop the reply the user talked over. Returns its id (the latest one when none was named)."""
        turn = self._live.get(reply_id) if reply_id else None
        if turn is None and not reply_id and self._live:
            turn = list(self._live.values())[-1]
        if turn is None:
            if reply_id:
                return reply_id
            last = next((m for m in reversed(self.messages) if m["role"] == "assistant"), None)
            return last["id"] if last else None
        turn.cut = True
        if turn.consumer is not None and not turn.consumer.done():
            turn.consumer.cancel()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(turn.finished.wait(), 8)
        return turn.reply_id

    async def _turn(self, queue: asyncio.Queue, text: str, source: str, client_id: str | None, interrupt: dict | None = None) -> None:
        try:
            await self._turn_inner(queue, text, source, client_id, interrupt)
        except Exception as exc:  # the app always gets an answer
            get_logger("hub").exception("desk turn failed")
            msg = await self._append(self._event("error", f"Something broke on the hub: {exc.__class__.__name__}", speak="Something broke on the hub."))
            await queue.put({"type": "event", "message": msg})
        finally:
            await queue.put({"type": "done"})
            await queue.put(None)

    async def _turn_inner(self, queue: asyncio.Queue, text: str, source: str, client_id: str | None, interrupt: dict | None = None) -> None:
        text = text.strip()
        if client_id:
            existing = next((m for m in self.messages if m.get("client_id") == client_id), None)
            if existing:
                await queue.put({"type": "user", "message": existing, "duplicate": True})
                return
        # The speaker played the assistant's reply into the mic. Drop it before it cuts the
        # real reply or starts another turn. Typed messages are never dropped.
        if source == "voice" and self._voice_is_echo(text):
            get_logger("hub").info("desk echo dropped")
            await queue.put({"type": "echo", "client_id": client_id})
            return
        if interrupt is not None:
            interrupt = {"reply_id": await self._cut(interrupt.get("reply_id")), "heard": (interrupt.get("heard") or "").strip()}
        user = {"id": _new_id(), "role": "user", "text": text, "at": now_iso(), "source": source, "client_id": client_id}
        if interrupt is not None:
            user["interrupt"] = interrupt
        user = await self._append(user)
        await queue.put({"type": "user", "message": user})
        if self.provider is None:
            msg = await self._append(self._event("not_configured", NOT_CONFIGURED, speak="The Desk isn't set up yet. Add an xAI key on the hub."))
            await queue.put({"type": "event", "message": msg})
            return
        reply_id = _new_id()
        turn = _Turn(reply_id)
        self._live[reply_id] = turn
        try:
            await self._reply(queue, turn, user, text)
        finally:
            self._live.pop(reply_id, None)
            turn.finished.set()

    def _voice_is_echo(self, text: str) -> bool:
        """The mic heard the speaker, or a short second copy of what the user just said."""
        spoken: list[str] = []
        previous = None
        previous_at = None
        for message in self.messages[-16:]:
            at = message.get("at")
            if not _within(at, 45):
                continue
            role = message.get("role")
            if role == "assistant" and message.get("text"):
                spoken.append(message["text"])
            elif role == "event" and message.get("speak"):
                spoken.append(message["speak"])
            elif role == "user" and message.get("text"):
                previous = message["text"]
                previous_at = at
        recent_user = previous if previous_at and _within(previous_at, 8) else None
        return echo_guard.voice_should_drop(text, " ".join(spoken[-4:]), recent_user)

    async def _reply(self, queue: asyncio.Queue, turn: "_Turn", user: dict, text: str) -> None:
        reply_id = turn.reply_id
        await queue.put({"type": "start", "id": reply_id})
        started = time.time()
        provider = self.provider
        system = self.system_prompt()
        history = self.history(user["id"])
        splitter = ReplySplitter()
        first_token: float | None = None
        error: str | None = None
        said_sent = False

        async def said() -> None:
            # The spoken part is complete: the app speaks its last sentence now instead of
            # waiting for the >>> action lines to stream and the actions to run.
            nonlocal said_sent
            if not said_sent:
                said_sent = True
                await queue.put({"type": "said", "id": reply_id})

        async def consume() -> None:
            nonlocal first_token, error
            try:
                async for chunk in provider.stream(system, history):
                    if first_token is None:
                        first_token = time.time() - started
                    spoken = splitter.feed(chunk)
                    if spoken:
                        await queue.put({"type": "delta", "id": reply_id, "text": spoken})
                    if splitter.cut is not None:
                        await said()
                if not splitter.text.strip():
                    raise ModelError("empty reply")
                await said()
            except Exception as exc:  # CancelledError (the user cut in) is not an Exception
                error = str(exc) or type(exc).__name__
                get_logger("hub").warning("desk model failed", extra={"fields": {"error": error[:200]}})

        turn.consumer = asyncio.create_task(consume())
        try:
            await turn.consumer
        except asyncio.CancelledError:
            if not turn.cut:
                raise
        cut_short = turn.cut and turn.consumer.cancelled()
        reply, actions = splitter.finish()
        if cut_short:
            # The user talked over a reply still being written: keep what was said, run none of it.
            actions = []
            error = None
        if error and not reply:
            reply = "I can't reach the desk model right now. Try again in a moment."
        if not reply and not actions:
            reply = "…" if cut_short else "Sorry, say that again?"
        assistant = {
            "id": reply_id,
            "role": "assistant",
            "text": reply,
            "at": now_iso(),
            "model": provider.label,
            "latency_ms": int((first_token or (time.time() - started)) * 1000),
            "did": [],
        }
        if error:
            assistant["error"] = error[:300]
        if cut_short:
            assistant["interrupted"] = True
        # Run the actions before saving, so the history records what was done.
        events = []
        for action in actions[:8]:
            try:
                event, did = await self._run_action(action, text)
            except Exception as exc:
                event, did = self._event("error", f"Couldn't do {action.get('type')}: {exc}", speak="One of those didn't go through."), None
            if did:
                assistant["did"].append(did)
            if event:
                events.append(event)
        await self._append(assistant)
        await queue.put({"type": "reply", "message": assistant})
        for event in events:
            saved = await self._append(event)
            await queue.put({"type": "event", "message": saved})
        if any(str(a.get("type") or "").lower() == "clear" for a in actions[:8]):
            # After the reply is saved, so the notes include this exchange.
            asyncio.get_running_loop().create_task(self.clear_context("asked in the thread"))

    def _event(self, kind: str, text: str, speak: str | None = None, **extra: Any) -> dict:
        msg = {"id": _new_id(), "role": "event", "kind": kind, "text": text, "at": now_iso()}
        if speak:
            msg["speak"] = speak
        msg.update({k: v for k, v in extra.items() if v is not None})
        return msg

    async def _run_action(self, action: dict, origin: str) -> tuple[dict | None, str | None]:
        kind = str(action.get("type") or "").lower()
        if kind == "task":
            projects = self.projects()
            wanted = action.get("project")
            project = projects.resolve(wanted)
            if wanted and project is None and projects.rows:
                return self._event("error", f"There's no project called {wanted}.", speak=f"I couldn't find {wanted}. Which project?"), None
            title = str(action.get("title") or "Agent task")
            brief = str(action.get("brief") or origin)
            task = await self.runner.create(project["name"] if project else None, project["path"] if project else None, title, brief, origin)
            await self._task_changed(self.runner.public(task))
            if task["status"] == "failed":
                return self._event("task_failed", f"Couldn't start {title}: {task.get('error')}", speak="I couldn't start that agent.", task_id=task["id"]), None
            where = f" on {project['name']}" if project else ""
            if task["status"] == "queued":
                text = f"Saved for an agent{where}: {task['title']}. {task.get('waiting') or ''}".strip()
                speak = f"Saved {task['title']}. It starts as soon as an agent can run."
            else:
                text = f"Started {task['agent']}{where}: {task['title']}."
                speak = f"Started an agent for {task['title']}. I'll let you know when it's done."
            return self._event("task_started", text, speak=speak, task_id=task["id"]), f"started task {task['id']} {task['title']}"
        if kind == "followup":
            task_id = str(action.get("task") or "")
            message = str(action.get("message") or origin)
            task = await self.runner.followup(task_id, message)
            if task is None:
                return self._event("error", f"There's no task {task_id}.", speak="I couldn't find that task."), None
            await self._task_changed(self.runner.public(task))
            verb = "Queued that for" if task["status"] == "running" else "Restarted"
            return self._event("task_followup", f"{verb} {task['title']}: {message}", speak=f"{verb} the {task['title']} agent.", task_id=task["id"]), f"follow-up to {task['id']}"
        if kind == "cancel":
            task_id = str(action.get("task") or "")
            task = await self.runner.cancel(task_id)
            if task is None:
                return self._event("error", f"There's no task {task_id}.", speak="I couldn't find that task."), None
            await self._task_changed(self.runner.public(task))
            return self._event("task_stopped", f"Stopped {task['title']}.", speak=f"Stopped {task['title']}.", task_id=task["id"]), f"stopped {task['id']}"
        if kind == "clear":
            return None, "cleared the context"
        if kind in ("todo", "reminder"):
            title = str(action.get("title") or "").strip()
            if not title:
                return None, None
            notes = str(action.get("notes") or "")
            due = reminders.parse_due(action.get("due")) if kind == "reminder" else None
            list_name = str(self.hub.cfg.get("desk.reminders_list") or "")
            try:
                landed = await asyncio.to_thread(self.add_reminder, title, notes, due, list_name)
            except Exception as exc:
                return self._event("error", f"Couldn't add “{title}” to Reminders: {exc}", speak="Reminders didn't take that one."), None
            if due:
                when = _spoken_time(due)
                return self._event("reminder", f"Reminder set for {when}: {title}", speak=f"Reminder set for {when}."), f"reminder {title} at {due.isoformat(timespec='minutes')}"
            return self._event("todo", f"Added “{title}” to {landed or 'Reminders'}.", speak=None), f"to-do {title}"
        return None, None

    # Workers

    async def _task_changed(self, task: dict) -> None:
        await self.hub.live.broadcast({"type": "desk_task", "task": task})

    async def _task_finished(self, task: dict) -> None:
        ok = task["status"] == "done"
        spoken = first_sentence(task.get("summary")) if ok else ""
        if ok:
            text = f"Done: {task['title']}. {spoken}".strip()
            speak = f"{task['title']} is done. {spoken}".strip()
        else:
            reason = task.get("error") or "no result"
            text = f"{task['title']} stopped without finishing: {reason}"
            speak = f"{task['title']} stopped without finishing."
        task["spoken"] = speak
        await self._append(self._event("task_done" if ok else "task_failed", text, speak=speak, task_id=task["id"], tracker_id=task.get("tracker_id")))
        await self.hub.raise_alerts([{
            "schema": "orbital.alert.v1",
            "id": f"desk-{task['id']}-{task.get('runs') or 1}",
            "kind": "event",
            "title": f"{'Done' if ok else 'Needs a look'}: {task['title']}",
            "subtitle": task.get("project"),
            "body": (spoken or text)[:400],
            "thread_id": "agent-desk",
            "interruption_level": "timeSensitive" if ok else "active",
            "launch_url": f"orbital://agent?task={task['id']}",
            "user_info": {"source": "agent-desk", "task_id": task["id"], "tracker_id": task.get("tracker_id")},
        }])

    def tracker_ids(self) -> set[str]:
        return self.runner.tracker_ids()

    def task_for_tracker(self, item_id: str) -> dict | None:
        return self.runner.for_tracker(item_id)

    async def close(self) -> None:
        if self._provider is not None:
            await self._provider.close()


class _Turn:
    """A reply being written, so the user's next words can stop it."""

    def __init__(self, reply_id: str) -> None:
        self.reply_id = reply_id
        self.consumer: asyncio.Task | None = None
        self.cut = False
        self.finished = asyncio.Event()


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def _split_heard(full: str, heard: str) -> tuple[str, str]:
    """The reply split into what the user heard (the app reports it) and what they missed."""
    full_words = _words(full)
    count = min(len(_words(heard)), len(full_words))
    return " ".join(full_words[:count]), " ".join(full_words[count:])


def _age_s(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        return time.time() - datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")


def _ago(iso: str | None) -> str:
    seconds = _age_s(iso)
    if seconds == float("inf") or seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def _spoken_time(when: datetime) -> str:
    today = datetime.now().date()
    clock = when.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
    if when.date() == today:
        return f"today at {clock}"
    if when.date() == today + timedelta(days=1):
        return f"tomorrow at {clock}"
    if 0 < (when.date() - today).days < 7:
        return f"{when.strftime('%A')} at {clock}"
    return f"{when.strftime('%b')} {when.day} at {clock}"


def add_routes(app, hub) -> None:
    desk: Desk = hub.desk

    @app.get("/v1/desk")
    async def desk_snapshot(request: Request, limit: int = 60) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        # The app opened the Desk: get the model's connection ready for the first word.
        if hub.background:
            asyncio.create_task(desk.warm())
        return JSONResponse(desk.snapshot(limit))

    @app.get("/v1/desk/messages")
    async def desk_messages(request: Request, before: str | None = None, limit: int = 60) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        return JSONResponse({"messages": desk.page(before, limit)})

    @app.post("/v1/desk/say")
    async def desk_say(request: Request):
        denied = hub.require(request)
        if denied:
            return denied
        body = await body_of(request)
        text = str(body.get("text") or "").strip()
        if not text:
            return fail("text is required", 400)
        if len(text) > 6000:
            return fail("text is longer than 6000 characters", 413)
        source = "voice" if body.get("source") == "voice" else "text"
        client_id = str(body.get("client_id") or "")[:80] or None
        # The user talked over (or before) the last reply: what they heard of it, so the desk can pick up.
        raw_cut = body.get("interrupt")
        interrupt = None
        if isinstance(raw_cut, dict):
            interrupt = {"reply_id": str(raw_cut.get("reply_id") or "")[:80] or None, "heard": str(raw_cut.get("heard") or "")[:4000]}
        queue = desk.say(text, source, client_id, interrupt)

        async def stream():
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield json.dumps(item, ensure_ascii=False) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    @app.post("/v1/desk/clear")
    async def desk_clear(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        return JSONResponse({"message": await desk.clear_context("cleared from the app")})

    @app.post("/v1/desk/warm")
    async def desk_warm(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        await desk.warm()
        return JSONResponse({"ok": True, "configured": desk.provider is not None})

    @app.get("/v1/desk/tasks/{task_id}")
    async def desk_task(task_id: str, request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        task = desk.runner.get(task_id)
        if task is None:
            return fail("no such task", 404)
        return JSONResponse(desk.runner.public(task))

    @app.post("/v1/desk/tasks/{task_id}/cancel")
    async def desk_cancel(task_id: str, request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        task = await desk.runner.cancel(task_id)
        if task is None:
            return fail("no such task", 404)
        public = desk.runner.public(task)
        await hub.live.broadcast({"type": "desk_task", "task": public})
        return JSONResponse(public)
