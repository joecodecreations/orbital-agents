"""Live voice for the Agent desk: OpenAI's Realtime API, relayed by the hub.

    iPhone mic ──PCM16 24 kHz──▶ hub /v1/desk/realtime ──▶ OpenAI Realtime (speech to speech)
    iPhone speaker ◀──PCM16──── hub ◀──────────────────────── its voice, its tool calls

The phone only moves audio. The hub holds the OpenAI key, runs every tool the voice model calls
(the desk's own data, files on this Mac, agents on every machine), writes both sides of the talk into
the desk thread, and pushes agent results into the live conversation the moment they land, so the
voice tells the user without being asked. Typed desk turns and voice turns share one thread.

Turn-taking is OpenAI's semantic VAD with interrupt_response: when the user talks over the voice,
the server stops the reply, the hub tells the phone (`speech_started`), the phone stops playback and
answers with how much it played, and the hub truncates that reply to what was heard.

Phone → hub: binary frames are mic audio (PCM16 mono 24 kHz little-endian). JSON:
    {"type":"truncate","item_id","audio_end_ms"}  playback stopped there (barge-in)
    {"type":"mute","on":bool}                      mic muted: audio is dropped and the buffer cleared
    {"type":"text","text"}                          typed while in voice
    {"type":"ping"} / {"type":"stop"}
Hub → phone: binary frames are the voice (same format). JSON:
    connecting · ready{model,voice} · speech_started · speech_stopped · user_partial{item_id,delta}
    user_final{text} · audio_start{item_id} · assistant_delta{item_id,delta} · response_done{status}
    tool{name,label,state} · agent_update{text} · error{message,fatal} · closed{reason} · pong

Without OPENAI_API_KEY in secrets.env the socket answers `error` with fatal=true and how to turn it
on; the app then uses its classic voice (on-device speech to text, the desk model, Fish voices).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import time
from datetime import datetime
from typing import Any

from orbital.core import get_logger
from orbital.hub import echo_guard
from orbital.hub.desk_lookup import clip, one_line

OPENAI_WS = "wss://api.openai.com/v1/realtime?model={model}"
REALTIME_MODELS = ["gpt-realtime-2.1", "gpt-realtime-2.1-mini", "gpt-realtime-2", "gpt-realtime"]
REALTIME_VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]
EAGERNESS = ["auto", "low", "medium", "high"]
TRANSCRIBE_MODELS = ["gpt-4o-mini-transcribe", "whisper-1"]
# Announcements outside a call, in the same voice (OpenAI's speech API has marin and cedar too).
SPEECH_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE_SETTINGS = {"engine": "realtime", "model": REALTIME_MODELS[0], "voice": "marin", "eagerness": "high"}
NO_KEY = ("Live voice needs an OpenAI API key on the hub: add OPENAI_API_KEY to ~/.orbital-agents/secrets.env "
          "(picked up without a restart). Until then the app uses its classic voice.")
SAMPLE_RATE = 24000
READ_TIMEOUT = 25.0
INSTRUCTION_REFRESH = 20.0
HISTORY_TURNS = 14

log = get_logger("hub")


def clean_voice_settings(raw: object, *, strict: bool = False) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_VOICE_SETTINGS)
    engine = str(raw.get("engine") or out["engine"]).lower()
    if engine not in ("realtime", "classic"):
        if strict:
            raise ValueError("voice.engine must be realtime or classic")
        engine = out["engine"]
    model = str(raw.get("model") or out["model"]).strip()
    if not re.fullmatch(r"gpt-[a-z0-9.\-]{2,60}", model):
        if strict:
            raise ValueError("voice.model must be an OpenAI realtime model id")
        model = out["model"]
    voice = str(raw.get("voice") or out["voice"]).lower()
    if voice not in REALTIME_VOICES:
        if strict:
            raise ValueError(f"voice.voice must be one of {', '.join(REALTIME_VOICES)}")
        voice = out["voice"]
    eagerness = str(raw.get("eagerness") or out["eagerness"]).lower()
    if eagerness not in EAGERNESS:
        if strict:
            raise ValueError(f"voice.eagerness must be one of {', '.join(EAGERNESS)}")
        eagerness = out["eagerness"]
    return {"engine": engine, "model": model, "voice": voice, "eagerness": eagerness}


# Tools the voice model calls. The read tools are the same ones the typed desk uses as `lookup`.

def _fn(name: str, description: str, props: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False},
    }


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


READ_TOOLS = {"live_status", "search_my_data", "read_page", "agent_task_details", "find_files", "list_folder", "read_file"}
TOOL_SPECS = [
    _fn("live_status", "Everything happening right now: agents running and what they found so far, agents that finished recently with "
        "their reports, machines and their health, unread alerts, automations, every page's summary, the tracker. Instant."),
    _fn("search_my_data", "Search the user's hub data: every page in the app (sales, sites, servers, money…), every agent report, "
        "finding and answer, tracker items and alerts. Instant. Not for files on disk (use list_folder or find_files).",
        {"query": _str("a few key words")}, ["query"]),
    _fn("read_page", "Read one page of the app in full (its numbers, tables and notes).", {"name": _str("page title or id")}, ["name"]),
    _fn("agent_task_details", "One agent task's full report, findings, answer and status.", {"task": _str("task id like t12, or its title")}, ["task"]),
    _fn("find_files", "Find documents and files on the hub Mac (searches contents and names) under ~/projects, Documents, Desktop "
        "and Downloads. With a project and no query: that project's most recently changed files. About a second.",
        {"query": _str("key words; may be empty when project is given"), "project": _str("optional project or folder name")}, ["query", "project"]),
    _fn("list_folder", "List a folder on the hub Mac, newest first. Takes a project or folder name as spoken (\"orbital agents\", \"the smith folder\"), a path under ~/projects, or ~/Documents. The first step for anything in a project's files.",
        {"path": _str("project name or path")}, ["path"]),
    _fn("read_file", "Read a file on the hub Mac: text, Markdown, notes, code, JSON, CSV, PDF, Word. Long files: pass query to get the "
        "parts about a topic, or offset to page on.",
        {"path": _str("path from find_files or list_folder"), "query": _str("optional topic to jump to"), "offset": {"type": "integer", "description": "optional character offset"}},
        ["path", "query", "offset"]),
    _fn("start_agent", "Send an AI agent to do real work in the background: change or build something, research the web, anything on "
        "another machine, long reading or writing. Agents run in parallel while you keep talking; the result comes back to you as an "
        "[agent update]. Use one call per separate job.",
        {
            "title": _str("2-5 word title"),
            "brief": _str("everything the agent needs: the request in full, the project, what done looks like, exactly what to report back, "
                          "and details from this conversation"),
            "project": _str("project name from the list, or empty"),
            "effort": {"type": "string", "enum": ["quick", "medium", "high", "xhigh"], "description": "quick = a lookup it answers fast; high = default for work"},
            "machine": _str("optional machine name; empty = the best one"),
            "agent": _str("optional agent (claude, grok, codex, gemini); empty = the desk picks"),
        },
        ["title", "brief", "project", "effort", "machine", "agent"]),
    _fn("message_agent", "Add to, correct or answer a question from an agent task (running or finished; a finished one starts again).",
        {"task": _str("task id like t12"), "message": _str("what to tell it")}, ["task", "message"]),
    _fn("stop_agent", "Stop a running agent task.", {"task": _str("task id")}, ["task"]),
    _fn("add_reminder", "Put a to-do or a timed reminder in the user's Apple Reminders.",
        {"title": _str("what"), "due": _str("YYYY-MM-DDTHH:MM local time, or empty for a to-do"), "notes": _str("optional")},
        ["title", "due", "notes"]),
    _fn("ask_desk_brain", "Hand a hard question that needs careful reasoning over the user's situation to the desk's main model (their "
        "chosen Claude or Grok). It answers in the background as an [agent update]; keep talking meanwhile.",
        {"question": _str("the question, with the context it needs")}, ["question"]),
]
TOOL_LABELS = {
    "live_status": "Checking on everything…",
    "search_my_data": "Searching your data…",
    "read_page": "Opening the page…",
    "agent_task_details": "Reading the agent's report…",
    "find_files": "Searching your files…",
    "list_folder": "Looking in the folder…",
    "read_file": "Reading the file…",
    "start_agent": "Starting an agent…",
    "message_agent": "Messaging the agent…",
    "stop_agent": "Stopping the agent…",
    "add_reminder": "Adding a reminder…",
    "ask_desk_brain": "Asking the desk model…",
}


VOICE_RULES = """You are the voice of Orbital Agents: the user's hands-free operator. They talk to you from their iPhone, often while driving, walking or doing something else. You run on their hub Mac, you can read their data and files yourself, and you send AI agents to work on their machines.

How you talk
- Natural, warm and quick, like a sharp assistant on a phone call. One to three short sentences unless they ask for detail or ask you to read something out; then read it in full.
- No lists, markdown or symbols. Say numbers, money and dates the way people say them. Never spell out file paths, extensions or links: say "the changelog", "the arrest notes PDF", not "CHANGELOG dot M D".
- They can talk over you at any time. When they do, drop what you were saying and deal with what they just said.
- If you didn't catch it, or it's truly ambiguous which thing they mean, ask one short question.

Getting real answers (the most important part)
- Never guess or give a generic answer about their data, files, cases, documents, money, projects, machines or agents. Look it up with your tools and answer from what you found, specifically. If the tools don't have it, say so in a few words and offer to send an agent (or just send one if they asked you to get it).
- Before your first tool call in a turn, say a few words so they're never left in silence: "Checking on that." "One sec, pulling up the notes." After a tool result, don't speak again until you have the answer: call the next tool silently, or answer.
- Do lookups yourself; they take a second. Pick the right one first: a project or person's folder ("what's in the X folder", "look in X's files") → list_folder with the name as they said it, then read_file on the relevant notes; documents about a topic → find_files; numbers and status on their app pages or in agent reports → search_my_data or read_page; what's running or finished → live_status; one agent's full report → agent_task_details.
- Send an agent with start_agent for real work: making or changing anything, the web, other machines, long reading or writing, or when your own lookups come up empty. Agents run in parallel and you stay in the conversation: say briefly what you sent ("I've got an agent on that, I'll tell you when it's back"), then keep talking. Separate jobs get separate agents. Check live_status first so you don't start a duplicate of a running one; use message_agent to add to it instead.
- [agent update] and [tracker update] messages are news arriving while you talk: an agent answered, asked, got stuck or finished, or something the user is tracking finished. Slip it into the conversation the way a person would ("Oh, and by the way, the store check is done: the listing is live."): the result itself in a sentence or two, without being asked. If they're in the middle of something, answer that first, then mention it. Never read out IDs.
- An agent that asks the user something: put the question to them and pass their answer back with message_agent.
- Never say something is done, sent, found or started unless a tool result or agent update says so.
- ask_desk_brain is for questions that need deep thinking about their situation; it answers in the background like an agent.
"""


class RealtimeError(RuntimeError):
    pass


def build_instructions(desk: Any) -> str:
    settings = desk.settings()
    now = datetime.now().astimezone()
    lines = [VOICE_RULES]
    from orbital.hub.desk import DISPATCH_NOTES  # noqa: PLC0415  (desk imports this module)
    if settings["dispatch"] == "off":
        lines.append("Agents are turned off in settings: don't start any; answer from your lookups, and take reminders.")
    elif settings["dispatch"] == "single":
        lines.append("Dispatch setting: one agent per request; put all of one request's work into a single start_agent call.")
    else:
        lines.append(DISPATCH_NOTES["parallel"].replace("tasks", "start_agent calls"))
    rules = desk.rules_block()
    if rules:
        lines += ["", "The user's routing rules for agents (set machine and agent from them):", rules]
    lines += ["", f"Now: {now.strftime('%A %Y-%m-%d %H:%M')} ({now.tzname()})."]
    projects = desk.projects().rows
    if projects:
        lines.append("Projects (name: about), for start_agent and find_files:")
        for row in projects[:80]:
            lines.append(f"- {row['name']}: {one_line(row.get('about'), 90)}")
    machines = desk.machines_block()
    if machines:
        lines += ["Machines:", machines]
    running = desk.runner.running()
    if running:
        lines.append("Agents running now (live_status has their latest notes):")
        for task in running[:10]:
            pct = f", {task['progress']}%" if task.get("progress") else ""
            ask = f" — ASKS THE USER: {one_line(task['question'], 200)}" if task.get("question") else ""
            lines.append(f"- {task['id']} {task['title']} [{task['status']}{pct}]{ask}")
    else:
        lines.append("Agents running now: none.")
    carry = desk.carried_notes()
    if carry:
        lines += ["Notes from earlier conversations:", clip(carry, 1500)]
    recent = desk.recent_turns(HISTORY_TURNS)
    if recent:
        lines.append("The conversation so far (typed and spoken), oldest first:")
        lines.extend(recent)
    return "\n".join(lines)


async def run_tool(desk: Any, name: str, args: dict, origin: str = "", platform: str | None = None,
                   on_brain: Any = None) -> str:
    """One tool call from the voice model (or a `lookup` from the typed desk). Always returns text."""
    lookup = desk.lookup
    args = args if isinstance(args, dict) else {}
    try:
        if name == "live_status":
            return lookup.status()
        if name == "search_my_data":
            return lookup.search(str(args.get("query") or origin))
        if name == "read_page":
            return lookup.page(str(args.get("name") or ""))
        if name == "agent_task_details":
            return lookup.task(str(args.get("task") or ""))
        if name == "find_files":
            return await asyncio.wait_for(lookup.find_files(str(args.get("query") or ""), str(args.get("project") or "")), READ_TIMEOUT)
        if name == "list_folder":
            return lookup.list_folder(str(args.get("path") or ""))
        if name == "read_file":
            offset = args.get("offset")
            return await asyncio.wait_for(
                lookup.read_file(str(args.get("path") or ""), str(args.get("query") or ""), int(offset) if isinstance(offset, (int, float)) else 0),
                READ_TIMEOUT,
            )
        if name in ("start_agent", "message_agent", "stop_agent", "add_reminder"):
            action = _action_for(name, args)
            event, did = await desk._run_action(action, origin or str(args.get("brief") or args.get("message") or ""), platform)
            if event:
                await desk._append(event)
            if event is None and did is None:
                return "Agents are turned off in settings." if name in ("start_agent", "message_agent") else "Nothing to do."
            text = (event or {}).get("text") or did or "Done."
            if name == "start_agent" and did:
                text += " It works in the background; its result comes back to you as an [agent update]."
            return text
        if name == "ask_desk_brain":
            question = str(args.get("question") or origin).strip()
            if not question:
                return "Say what to ask."
            label = desk.brain_label()
            if label is None:
                return "The desk model isn't available right now. Start an agent instead."
            task = asyncio.get_running_loop().create_task(desk.ask_brain(question))
            if on_brain is not None:
                task.add_done_callback(lambda t: on_brain(question, t))
            return f"Asked {label}. Its answer comes back as an [agent update]; keep talking meanwhile."
        return f"There's no tool called {name}."
    except asyncio.TimeoutError:
        return f"{name} took too long. Start an agent for it instead."
    except Exception as exc:  # a tool must never break the call
        log.warning("desk tool failed", extra={"fields": {"tool": name, "error": str(exc)[:200]}})
        return f"{name} failed: {exc.__class__.__name__}: {str(exc)[:200]}"


def _action_for(name: str, args: dict) -> dict:
    if name == "start_agent":
        return {"type": "task", "title": args.get("title"), "brief": args.get("brief"), "project": args.get("project") or "",
                "effort": args.get("effort") or "high", "machine": args.get("machine") or "", "agent": args.get("agent") or ""}
    if name == "message_agent":
        return {"type": "followup", "task": args.get("task"), "message": args.get("message")}
    if name == "stop_agent":
        return {"type": "cancel", "task": args.get("task")}
    due = str(args.get("due") or "").strip()
    return {"type": "reminder" if due else "todo", "title": args.get("title"), "due": due or None, "notes": args.get("notes") or ""}


def describe_call(name: str, args: dict) -> str:
    """A line for the thread: what the voice looked up."""
    if name == "find_files":
        what = " ".join(x for x in (args.get("query"), f"in {args['project']}" if args.get("project") else "") if x)
        return f"Searched files: {what or 'recent files'}"
    if name == "read_file":
        return f"Read {str(args.get('path') or '').rsplit('/', 1)[-1]}"
    if name == "list_folder":
        return f"Opened folder {str(args.get('path') or '').rstrip('/').rsplit('/', 1)[-1]}"
    if name == "search_my_data":
        return f"Searched your data: {args.get('query')}"
    if name == "read_page":
        return f"Read page {args.get('name')}"
    if name == "agent_task_details":
        return f"Read agent report {args.get('task')}"
    if name == "live_status":
        return "Checked live status"
    if name == "ask_desk_brain":
        return f"Asked the desk model: {one_line(args.get('question'), 120)}"
    return name


class RealtimeBridge:
    """One phone's live voice call: phone socket ⇄ OpenAI Realtime socket, tools run here."""

    def __init__(self, desk: Any, phone: Any, key: str, platform: str | None = None, connect: Any = None) -> None:
        self.desk = desk
        self.phone = phone
        self.key = key
        self.platform = platform or "ios"
        self.settings = clean_voice_settings(desk.settings().get("voice"))
        self._connect_fn = connect
        self.oa: Any = None
        self.model = self.settings["model"]
        self.muted = False
        self.response_active = False
        self.user_speaking = False
        self.user_turn_open = False
        self.audio_item: str | None = None
        self.transcripts: dict[str, str] = {}
        self.truncated: set[str] = set()
        self.calls: list[tuple[str, str, dict, asyncio.Task]] = []
        self.tools_running = 0
        self.outputs_waiting = False
        self.updates: list[str] = []
        self.user_pending: set[str] = set()
        self.user_done = asyncio.Event()
        self.last_user_text = ""
        self.state_seen = desk.state_version
        self.instructions_at = time.time()
        self.closed = False
        self.started = time.time()
        self.stats = {"audio_in": 0, "audio_out": 0, "responses": 0, "cut_ins": 0, "tools": 0}
        self.phone_stats: dict | None = None
        # What its voice said lately, to tell its own words coming back through a speaker from yours.
        self.spoken: list[tuple[float, str]] = []
        self.echoes = 0
        # The app's "say when something finishes" switch (sent as prefs after ready).
        self.announce_tracker = False
        # Roughly when the phone finishes playing what it was sent (it plays in real time).
        self.voice_busy_until = 0.0
        self.recorded_items: set[str] = set()
        self.over_voice: set[str] = set()
        self.warned_stuck = False
        self._phone_lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()

    # Plumbing

    async def _phone(self, message: dict) -> None:
        if self.closed:
            return
        async with self._phone_lock:
            with contextlib.suppress(Exception):
                await self.phone.send_text(json.dumps(message))

    async def _phone_audio(self, data: bytes) -> None:
        if self.closed:
            return
        async with self._phone_lock:
            with contextlib.suppress(Exception):
                await self.phone.send_bytes(data)

    async def _oa(self, message: dict) -> None:
        if self.oa is not None:
            await self.oa.send(json.dumps(message))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def _open(self, model: str):
        if self._connect_fn is not None:
            return await self._connect_fn(model)
        import websockets  # noqa: PLC0415
        return await websockets.connect(
            OPENAI_WS.format(model=model),
            additional_headers={"Authorization": f"Bearer {self.key}"},
            max_size=None,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        )

    async def _connect(self) -> None:
        order = [self.settings["model"], *[m for m in REALTIME_MODELS if m != self.settings["model"]]]
        last = "no model answered"
        for model in order:
            try:
                ws = await self._open(model)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last = f"{model}: {status or ''} {exc}".strip()
                log.info("realtime connect failed", extra={"fields": {"model": model, "status": status, "error": str(exc)[:160]}})
                if status in (401, 403):
                    raise RealtimeError("OpenAI refused the API key (check OPENAI_API_KEY on the hub, and that the account has billing).") from exc
                if status == 429:
                    raise RealtimeError("OpenAI says the account is out of quota or rate limited.") from exc
                continue
            self.oa = ws
            self.model = model
            try:
                await self._configure()
                return
            except RealtimeError as exc:
                last = f"{model}: {exc}"
                with contextlib.suppress(Exception):
                    await ws.close()
                self.oa = None
                if "model" not in str(exc).lower():
                    raise
        raise RealtimeError(f"Couldn't open live voice ({last}).")

    def _session(self, variant: int) -> dict:
        s = self.settings
        detect: dict = {"type": "semantic_vad", "eagerness": s["eagerness"], "create_response": True, "interrupt_response": True}
        if variant >= 3:
            detect = {"type": "server_vad", "threshold": 0.55, "prefix_padding_ms": 300, "silence_duration_ms": 600,
                      "create_response": True, "interrupt_response": True}
        audio_in: dict = {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "turn_detection": detect}
        if variant < 3:
            audio_in["transcription"] = {"model": TRANSCRIBE_MODELS[0 if variant < 2 else 1]}
        if variant == 0:
            audio_in["noise_reduction"] = {"type": "far_field"}
        return {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": build_instructions(self.desk),
            "audio": {"input": audio_in, "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": s["voice"]}},
            "tools": TOOL_SPECS,
            "tool_choice": "auto",
        }

    async def _configure(self) -> None:
        """session.update, dropping the optional parts the server refuses, one at a time."""
        last = ""
        for variant in range(4):
            await self._oa({"type": "session.update", "session": self._session(variant)})
            outcome = await self._await_session(variant)
            if outcome is None:
                return
            last = outcome
            low = last.lower()
            if "model" in low and "transcri" not in low and any(w in low for w in ("not_found", "not found", "does not exist", "invalid model", "not supported")):
                raise RealtimeError(f"model refused: {last}")
        raise RealtimeError(f"OpenAI didn't accept the voice session: {last or 'no answer'}")

    async def _await_session(self, variant: int) -> str | None:
        """None once session.updated arrives; else the error (or timeout) text."""
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(self.oa.recv(), max(0.1, deadline - time.time()))
            except asyncio.TimeoutError:
                return "no answer to session.update"
            if isinstance(raw, bytes):
                continue
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "session.updated":
                self.instructions_at = time.time()
                log.info("realtime session ready", extra={"fields": {"model": self.model, "voice": self.settings["voice"], "variant": variant}})
                return None
            if kind == "error":
                err = event.get("error") or {}
                text = f"{err.get('code') or ''} {err.get('message') or ''}".strip()
                log.info("realtime session.update refused", extra={"fields": {"variant": variant, "error": text[:240]}})
                return text or "refused"
        return "no answer to session.update"

    # The call

    async def run(self) -> None:
        await self._phone({"type": "connecting"})
        try:
            await self._connect()
        except RealtimeError as exc:
            await self._phone({"type": "error", "message": str(exc), "fatal": True})
            return
        self.desk.voice_listeners.add(self.on_update)
        self.desk.voice_sessions += 1
        await self._phone({"type": "ready", "model": self.model, "voice": self.settings["voice"]})
        pumps = [self._spawn(self._from_phone()), self._spawn(self._from_openai()), self._spawn(self._housekeeping())]
        try:
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is not None and not isinstance(task.exception(), asyncio.CancelledError):
                    log.warning("realtime pump stopped", extra={"fields": {"error": repr(task.exception())[:240]}})
        finally:
            self.closed = True
            log.info("realtime call ended", extra={"fields": {
                "seconds": round(time.time() - self.started), "model": self.model, "platform": self.platform,
                "mic_s": round(self.stats["audio_in"] / (SAMPLE_RATE * 2), 1), "voice_s": round(self.stats["audio_out"] / (SAMPLE_RATE * 2), 1),
                "responses": self.stats["responses"], "cut_ins": self.stats["cut_ins"], "tools": self.stats["tools"], "echoes": self.echoes,
                "phone": self.phone_stats}})
            self.desk.voice_listeners.discard(self.on_update)
            self.desk.voice_sessions = max(0, self.desk.voice_sessions - 1)
            for task in list(self._bg):
                task.cancel()
            for _, _, _, call in self.calls:
                call.cancel()
            if self.oa is not None:
                with contextlib.suppress(Exception):
                    await self.oa.close()

    async def _from_phone(self) -> None:
        while True:
            message = await self.phone.receive()
            if message.get("type") == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data:
                self.stats["audio_in"] += len(data)
                if not self.muted:
                    await self._oa({"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode("ascii")})
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "truncate":
                item = str(event.get("item_id") or self.audio_item or "")
                ms = max(0, int(event.get("audio_end_ms") or 0))
                if item and item not in self.truncated:
                    self.truncated.add(item)
                    await self._oa({"type": "conversation.item.truncate", "item_id": item, "content_index": 0, "audio_end_ms": ms})
            elif kind == "mute":
                self.muted = bool(event.get("on"))
                if self.muted:
                    await self._oa({"type": "input_audio_buffer.clear"})
            elif kind == "text":
                said = str(event.get("text") or "").strip()[:4000]
                if said:
                    await self._user_said(said, typed=True)
                    await self._oa({"type": "conversation.item.create",
                                    "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": said}]}})
                    await self._create_response()
            elif kind == "stats":
                # The phone's own view of its mic and the talk-over gate, every 10 s, for the log.
                self.phone_stats = {k: event.get(k) for k in (
                    "buffers", "sent", "heard", "vp", "running", "held", "bargeins", "coupling", "spread", "echo_tests_failed", "room_db", "play_db", "best_over_bar", "mic_db",
                    "talkover", "muted", "phase", "in", "out", "category") if k in event}
                stuck = (event.get("buffers") or 0) > 20 and not (event.get("sent") or 0) and not event.get("muted")
                (log.warning if stuck else log.info)(
                    "realtime phone mic" + (": captures sound but sends none" if stuck else ""), extra={"fields": self.phone_stats})
            elif kind == "log":
                log.info("realtime phone log", extra={"fields": {"line": str(event.get("line") or "")[:400]}})
            elif kind == "prefs":
                self.announce_tracker = bool(event.get("announce_tracker"))
            elif kind == "ping":
                await self._phone({"type": "pong"})
            elif kind == "stop":
                return

    async def _from_openai(self) -> None:
        reason = "the voice session ended"
        try:
            async for raw in self.oa:
                if isinstance(raw, bytes):
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(event)
        except Exception as exc:  # the OpenAI socket dropped: the phone reconnects
            reason = f"OpenAI closed the voice session ({exc.__class__.__name__})"
            log.info("realtime socket closed", extra={"fields": {"error": str(exc)[:200]}})
        await self._phone({"type": "closed", "reason": reason})

    async def _handle(self, event: dict) -> None:
        kind = event.get("type") or ""
        if kind == "input_audio_buffer.speech_started":
            self.user_speaking = True
            self.user_turn_open = True
            if time.time() < self.voice_busy_until + 0.6 and event.get("item_id"):
                self.over_voice.add(event["item_id"])
            await self._phone({"type": "speech_started", "item_id": event.get("item_id")})
        elif kind == "input_audio_buffer.speech_stopped":
            self.user_speaking = False
            await self._phone({"type": "speech_stopped"})
        elif kind == "input_audio_buffer.committed":
            if event.get("item_id"):
                self.user_pending.add(event["item_id"])
                self.user_done.clear()
        elif kind == "conversation.item.input_audio_transcription.delta":
            await self._phone({"type": "user_partial", "item_id": event.get("item_id"), "delta": event.get("delta") or ""})
        elif kind in ("conversation.item.input_audio_transcription.completed", "conversation.item.input_audio_transcription.failed"):
            self.user_pending.discard(event.get("item_id") or "")
            if not self.user_pending:
                self.user_done.set()
            said = str(event.get("transcript") or "").strip()
            if said and self._is_echo(said, str(event.get("item_id") or "")):
                # Its own voice from the speaker, heard as yours: take it out of the conversation and
                # stop any reply to it. (The phone's talk-over gate catches most of these first.)
                self.echoes += 1
                log.info("realtime dropped its own echo", extra={"fields": {"heard": said[:120]}})
                await self._oa({"type": "conversation.item.delete", "item_id": event.get("item_id")})
                if self.response_active:
                    await self._oa({"type": "response.cancel"})
                await self._phone({"type": "echo", "text": said})
            elif said:
                await self._phone({"type": "user_final", "item_id": event.get("item_id"), "text": said})
                await self._user_said(said)
        elif kind == "response.created":
            self.response_active = True
            if self.user_turn_open:
                # The reply to what they just said: it sees any tool results added meanwhile.
                self.user_turn_open = False
                self.outputs_waiting = False
        elif kind == "response.output_audio.delta":
            item = event.get("item_id")
            if item and item != self.audio_item:
                self.audio_item = item
                await self._phone({"type": "audio_start", "item_id": item})
            delta = event.get("delta")
            if delta:
                audio = base64.b64decode(delta)
                self.stats["audio_out"] += len(audio)
                self.voice_busy_until = max(self.voice_busy_until, time.time() + 0.35) + len(audio) / (SAMPLE_RATE * 2)
                await self._phone_audio(audio)
        elif kind == "response.output_audio_transcript.delta":
            item = event.get("item_id") or ""
            self.transcripts[item] = self.transcripts.get(item, "") + (event.get("delta") or "")
            await self._phone({"type": "assistant_delta", "item_id": item, "delta": event.get("delta") or ""})
        elif kind == "response.output_audio_transcript.done":
            item = event.get("item_id") or ""
            if event.get("transcript"):
                self.transcripts[item] = event["transcript"]
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                self._start_call(item)
        elif kind == "response.done":
            self.response_active = False
            response = event.get("response") or {}
            status = response.get("status") or ""
            self.stats["responses"] += 1
            self.stats["cut_ins"] += status == "cancelled"
            await self._phone({"type": "response_done", "status": status})
            spoken = self._spoken(response)
            for _, text in spoken:
                self.spoken.append((time.time(), text))
            self.spoken = self.spoken[-8:]
            if spoken:
                interrupted = status == "cancelled" or any(i in self.truncated for i, _ in spoken)
                self._spawn(self._record_reply(" ".join(t for _, t in spoken), interrupted))
            if self.calls:
                calls, self.calls = self.calls, []
                self._spawn(self._finish_calls(calls))
            elif self.outputs_waiting:
                self.outputs_waiting = False
                await self._create_response()
            else:
                await self._flush_updates()
        elif kind == "error":
            err = event.get("error") or {}
            code = str(err.get("code") or "")
            if code in ("response_cancel_not_active", "conversation_already_has_active_response", "item_truncate_invalid_item_id"):
                log.info("realtime note", extra={"fields": {"code": code}})
                return
            message = str(err.get("message") or code or "voice error")
            log.warning("realtime error", extra={"fields": {"code": code, "error": message[:240]}})
            # A refused response.create never sends response.done; don't wait on it forever.
            self.response_active = False
            await self._phone({"type": "error", "message": message, "fatal": False})

    def _is_echo(self, said: str, item_id: str = "") -> bool:
        now = time.time()
        recent = [text for at, text in self.spoken if now - at < 25] + [t for t in self.transcripts.values() if t]
        if not recent:
            return False
        if echo_guard.voice_should_drop(said, " ".join(recent[-4:]), None):
            return True
        # A word or three that began while its voice was playing, all words it just said
        # ("Nice." while it says "Nice. If you're…"): its own voice. "Stop" or "wait" still gets through.
        heard = echo_guard.tokens(said)
        if item_id in self.over_voice and 0 < len(heard) <= 3:
            said_lately = set(echo_guard.tokens(" ".join(recent[-2:])))
            return all(word in said_lately for word in heard)
        return False

    def _spoken(self, response: dict) -> list[tuple[str, str]]:
        out = []
        for item in response.get("output") or []:
            if item.get("type") != "message":
                continue
            ident = item.get("id") or ""
            if ident and ident in self.recorded_items:
                continue  # the same reply reported again (a cut-in after it finished): saved once
            if ident:
                self.recorded_items.add(ident)
            text = self.transcripts.pop(ident, "")
            if not text:
                for part in item.get("content") or []:
                    text += str(part.get("transcript") or part.get("text") or "")
            if text.strip():
                out.append((item.get("id") or "", text.strip()))
        return out

    async def _create_response(self) -> None:
        if self.response_active or self.user_speaking:
            # Their turn's own reply (the server makes it when they stop) sees the results too.
            self.outputs_waiting = True
            return
        self.response_active = True
        await self._oa({"type": "response.create"})

    # Tools

    def _start_call(self, item: dict) -> None:
        name = str(item.get("name") or "")
        call_id = str(item.get("call_id") or "")
        try:
            args = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        brain = lambda question, task: self._brain_done(question, task)  # noqa: E731
        task = self._spawn(run_tool(self.desk, name, args, origin=self.last_user_text, platform=self.platform, on_brain=brain))
        self.calls.append((call_id, name, args, task))
        self.stats["tools"] += 1
        self._spawn(self._phone({"type": "tool", "name": name, "label": TOOL_LABELS.get(name, "Working on it…"), "state": "running"}))

    async def _finish_calls(self, calls: list[tuple[str, str, dict, asyncio.Task]]) -> None:
        self.tools_running += 1
        try:
            await self._send_outputs(calls)
        finally:
            self.tools_running -= 1
        await self._create_response()

    async def _send_outputs(self, calls: list[tuple[str, str, dict, asyncio.Task]]) -> None:
        results = await asyncio.gather(*[task for *_, task in calls], return_exceptions=True)
        looked = []
        for (call_id, name, args, _), result in zip(calls, results):
            output = result if isinstance(result, str) else f"{name} failed: {result!r}"[:400]
            await self._oa({"type": "conversation.item.create",
                            "item": {"type": "function_call_output", "call_id": call_id, "output": clip(output, 12000)}})
            if name in READ_TOOLS or name == "ask_desk_brain":
                looked.append(describe_call(name, args))
        await self._oa({"type": "conversation.item.create", "item": {"type": "message", "role": "system", "content": [{
            "type": "input_text", "text": "(Tool results are in. Answer the user from them now; if you need another lookup, call it without speaking first.)"}]}})
        await self._phone({"type": "tool", "name": calls[-1][1], "label": "", "state": "done"})
        if looked:
            await self.desk.record_voice_event("lookup", "; ".join(looked))

    def _brain_done(self, question: str, task: asyncio.Task) -> None:
        if task.cancelled() or self.closed:
            return
        answer = task.result() if task.exception() is None else f"failed: {task.exception()}"
        self.on_update(f"[agent update] The desk model answered “{one_line(question, 160)}”: {answer}")

    # Agent updates and the live state

    def on_update(self, text: str, kind: str = "agent") -> None:
        """Called by the desk when an agent answers, asks, gets blocked or finishes, and (when the
        phone asked for it) when a tracker item finishes."""
        if self.closed or (kind == "tracker" and not self.announce_tracker):
            return
        self.updates.append(text)
        self._spawn(self._phone({"type": "agent_update", "text": one_line(re.sub(r"^\[(agent|tracker) update\] ", "", text), 300)}))
        self._spawn(self._flush_updates())

    async def _flush_updates(self) -> None:
        if not self.updates or self.response_active or self.user_speaking or self.calls or self.tools_running:
            return
        batch, self.updates = self.updates, []
        text = ("News that just came in (not from the user):\n" + "\n".join(batch) +
                "\nBring it up now, naturally and briefly, the way a person would mid-conversation: \"Oh, and by the way, ...\" "
                "with the result itself. If several came in, cover them together. If the user is in the middle of something, "
                "answer that first, then mention it. No IDs.")
        await self._oa({"type": "conversation.item.create",
                        "item": {"type": "message", "role": "system", "content": [{"type": "input_text", "text": clip(text, 8000)}]}})
        await self._create_response()

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(1.5)
            if self.updates:
                await self._flush_updates()
            idle = not self.response_active and not self.user_speaking and not self.calls and not self.tools_running
            if idle and self.desk.state_version != self.state_seen and time.time() - self.instructions_at > INSTRUCTION_REFRESH:
                self.state_seen = self.desk.state_version
                self.instructions_at = time.time()
                await self._oa({"type": "session.update", "session": {"type": "realtime", "instructions": build_instructions(self.desk)}})

    # The thread

    async def _user_said(self, text: str, typed: bool = False) -> None:
        self.last_user_text = text
        await self.desk.record_voice("user", text, source="text" if typed else "voice")

    async def _record_reply(self, text: str, interrupted: bool) -> None:
        # The user's words usually finish transcribing after the reply starts; keep the thread in order.
        if self.user_pending:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.user_done.wait(), 3)
        await self.desk.record_voice("assistant", text, model=f"{self.model} · live voice", interrupted=interrupted or None)
