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
from orbital.hub.auth import bearer
from orbital.hub.desk_model import ModelError, XAIProvider
from orbital.hub.desk_tasks import TRACKER_ORIGINS, TaskRunner, _tracker_origin, now_iso
from orbital.hub.tracker import _agent_name, first_sentence, shown_progress
from orbital.hub.web import body_of, device_of, fail


def resolve_client_platform(hub: Any, request: Request | None = None, body: dict | None = None) -> str | None:
    """ios or mac when the Orbital Agents app (or desktop) told us; never invent."""
    body = body or {}
    # Explicit body field from the app (preferred).
    parsed = _tracker_origin(str(body.get("platform") or body.get("client_platform") or ""))
    if parsed in ("ios", "mac"):
        return parsed
    if request is None:
        return None
    device_id = device_of(request)
    # Desktop Python app sends its product name, not a device UUID.
    if device_id and "desktop" in device_id.lower():
        return "mac"
    for row in hub.store.load_devices():
        if row.get("device_id") == device_id:
            return _tracker_origin(row.get("platform"))
    return None

HISTORY_TURNS = 24
THREAD_KEEP = 2000
# Finished tasks in the live state, and how much of their reports the desk model sees, so "read me
# the email it drafted" is answered from the report instead of "I don't have that".
RECENT_TASKS = 8
REPORT_BUDGET = 9000
REPORT_EACH = 3500
FULL_REPORT_AGE = 12 * 3600
MARK = ">>>"
MARK_LINE = re.compile(r"(^|\n)[ \t]*>>>")
NOT_CONFIGURED = (
    "The Desk isn't set up yet: add XAI_API_KEY to ~/.orbital-agents/secrets.env on the hub (it is picked up "
    "without a restart). Until then, start agents from the Agents tab or with `orbital agents run`."
)

SYSTEM_RULES = """You are the front desk of Orbital Agents. The user talks to you from the Orbital Agents app, often by voice while away from the computer. Your only job is to talk with the user: answer instantly and hand every real piece of work to AI agents that run on the user's machines. You are the dispatcher, never the worker, so you stay free to keep talking while the agents work.

Your reply:
- One or two short sentences that will be read aloud. Plain words only: no markdown, lists, emoji, code, or file paths read out character by character.
- Sound like a quick, capable assistant. Confirm what you're doing in the user's terms ("On it, I've got two agents on it: one fixing the login bug, one checking the store listing.").
- Never say work is finished unless the live state below shows it finished. Don't invent results.
- If it's genuinely unclear which project or what they want, ask one short question instead of acting.
- Status and result questions ("what's running", "is X done", "what did it find", "read me the email", "how many went out", "how's the server", "any alerts"): answer straight from the live state below. It has every agent job with its progress, latest notes, findings and answer, the full reports of tasks that finished recently, plus automations, alerts, machines and page summaries. When they ask for something a report or finding holds (a draft, a number, a list, a link, a quote), give it to them from there, read out in full if they ask for it. Never say you don't have something the live state shows. Dispatch only for what isn't there, and say that's what you're doing.
- Before starting a task, check the running list. If the same job is already running, say how far along it is instead of starting another.
- An agent waiting on the user (marked ASKS THE USER below): put its question to them plainly. Their answer goes back to it with a followup line, and the agent gets it within seconds.
- The user can talk over you, like in a real conversation. When their message says they cut you off, deal with what they just said first (start the work if it's a request). Then, only if the part they missed still matters, come back to it in a few words. Never repeat what they already heard.

Actions: after your reply, write zero or more lines that start with >>> and hold one JSON object each, on one line:
>>> {"type":"task","project":"<name from the project list, or empty>","title":"<2-5 word title>","brief":"<everything the agent needs>","effort":"<quick, medium, high or xhigh>","agent":"<optional: an agent name from Machines>","machine":"<optional: a machine name from Machines>"}
>>> {"type":"followup","task":"<task id like t3>","message":"<what to add or change>"}
>>> {"type":"cancel","task":"<task id>"}
>>> {"type":"todo","title":"<short to-do for the user>","notes":"<optional>"}
>>> {"type":"reminder","title":"<what>","due":"<YYYY-MM-DDTHH:MM in the user's local time>","notes":"<optional>"}
>>> {"type":"clear"}

When to use which:
- task: any ask that needs doing, finding out, or thinking through: code, fixes, features, builds, research, looking anything up (the web, files, accounts, numbers), writing, marketing, checking a site, or a question you can't answer from what you plainly know or the live state. Don't do the work or the research in your reply; dispatch it and say so. Split a request into one task per separate job so agents work in parallel: several jobs in one message means several task lines. The brief is the agent's only context: restate the request fully, name the project, say what done looks like, say exactly what to report back (the number, the text, the link), and include details from earlier in this conversation. effort: "quick" for a question that needs a lookup (a count, a fact in a file or an account, something on the web): the agent sends the answer back the moment it has it, and it is read to the user. "medium" for small edits, "high" by default, "xhigh" for big multi-step builds. Leave agent and machine empty unless the user names one or a machine clearly fits the job (its agents and status are under Machines); the desk then picks the best agent that can run.
- followup: the user adds to, corrects, or extends a task that already exists (running or finished), or answers an agent's question. A running agent gets it within seconds; a finished one starts again with it. Prefer this over starting a duplicate.
- cancel: they want a running task stopped.
- todo: something the user has to do personally, no time. reminder: there is a time ("tomorrow at 9", "in two hours"); resolve it against the current time below.
- clear: the user asks you to clear or reset your context. Running work is not affected; say so.
- No action only for chat, a quick fact you plainly know, or questions the live state answers.
"""

DISPATCH_NOTES = {
    "parallel": "Dispatch mode: parallel. Split separate jobs into separate tasks so agents work at the same time.",
    "single": "Dispatch mode: one agent. Put all the work in one request into a single task, never several.",
    "off": ("Dispatch mode: talk only. The user turned agents off: write no task, followup or cancel lines. "
            "Answer from what you know and the live state, and take to-dos and reminders."),
}
DISPATCH_MODES = tuple(DISPATCH_NOTES)
MAX_RULES = 40


def clean_settings(raw: object, *, default_chat: str = "auto", strict: bool = False) -> dict:
    """The Agent tab's settings, with anything unknown dropped (strict: refused)."""
    from orbital.hub.desk_engines import CHAT_ENGINES

    raw = raw if isinstance(raw, dict) else {}
    chat = str(raw.get("chat") or default_chat).lower()
    if chat not in CHAT_ENGINES:
        if strict:
            raise ValueError(f"chat must be one of {', '.join(CHAT_ENGINES)}")
        chat = "auto"
    model = str(raw.get("model") or "").strip()
    if len(model) > 80 or not re.fullmatch(r"[A-Za-z0-9._:-]*", model):
        if strict:
            raise ValueError("model must be a model name")
        model = ""
    dispatch = str(raw.get("dispatch") or "parallel").lower()
    if dispatch not in DISPATCH_MODES:
        if strict:
            raise ValueError(f"dispatch must be one of {', '.join(DISPATCH_MODES)}")
        dispatch = "parallel"
    rules = []
    for i, row in enumerate(raw.get("rules") or []):
        text = str((row or {}).get("text") if isinstance(row, dict) else row or "").strip()
        if not text:
            continue
        if len(text) > 400:
            if strict:
                raise ValueError(f"rule {i + 1} is longer than 400 characters")
            text = text[:400]
        ident = (str(row.get("id") or "") if isinstance(row, dict) else "") or f"r{i + 1}"
        rules.append({"id": ident[:40], "text": text, "enabled": bool(row.get("enabled", True)) if isinstance(row, dict) else True})
    if len(rules) > MAX_RULES:
        if strict:
            raise ValueError(f"at most {MAX_RULES} rules")
        rules = rules[:MAX_RULES]
    return {"chat": chat, "model": model, "dispatch": dispatch, "rules": rules}


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


def _speakable(text: str, limit: int = 600) -> str:
    """An agent's report as it should be heard: no markdown or links, at most a few sentences."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text or "")
    text = re.sub(r"`[^`]*`", lambda m: m.group(0).strip("`") if len(m.group(0)) < 40 else "", text)
    text = re.sub(r"https?://\S+", "the link", text)
    text = " ".join(_clean_spoken(text).split())
    return _one_line(_sentences(text, 4), limit)


# Some models put the first action on the same line as the reply ("On it. >>> {...}").
MARK_INLINE = re.compile(r"[ \t]*>>>[ \t]*\{")
# The end of the text might be the start of an inline action (" >", " >>", " >>> "); hold it back.
MARK_PARTIAL = re.compile(r"[ \t]+>{1,3}[ \t]*$")


class ReplySplitter:
    """Streams the spoken reply and holds back `>>>` action lines, also one that starts mid-line."""

    def __init__(self) -> None:
        self.text = ""
        self.emitted = 0
        self.cut: int | None = None

    def feed(self, chunk: str) -> str:
        self.text += chunk
        if self.cut is not None:
            return ""
        starts = [m.start() for m in (MARK_LINE.search(self.text), MARK_INLINE.search(self.text)) if m]
        if starts:
            self.cut = min(starts)
            end = self.cut
        else:
            line_start = self.text.rfind("\n") + 1
            tail = self.text[line_start:].lstrip()
            partial = MARK_PARTIAL.search(self.text, line_start)
            # A last line of ">" or ">>" might become an action line; wait for more.
            end = line_start if tail and MARK.startswith(tail) else (partial.start() if partial else len(self.text))
        # Trailing whitespace waits for the next chunk, so the reply never ends in a stray newline.
        while end > self.emitted and self.text[end - 1].isspace():
            end -= 1
        out = self.text[self.emitted : end] if end > self.emitted else ""
        self.emitted = max(self.emitted, end)
        return out

    def finish(self) -> tuple[str, list[dict]]:
        reply_lines: list[str] = []
        actions: list[dict] = []
        text = MARK_INLINE.sub(lambda m: "\n" + m.group(0).lstrip(" \t"), self.text)
        for line in text.splitlines():
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


DOC_NAMES = ("README.md", "readme.md", "AGENTS.md", "CLAUDE.md")
SKIP_DOC_LINE = re.compile(r"^\s*(<|!\[|\[!|---|```|>|\||[-=─]{3,})")
CATALOG_REFRESH = 3600


def _describe(folder: Path) -> str:
    """The first heading and lines of a folder's README / AGENTS.md / CLAUDE.md, one short line."""
    for name in DOC_NAMES:
        try:
            head = (folder / name).read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        title, body = "", []
        for raw in head.splitlines():
            line = raw.strip()
            if not line or SKIP_DOC_LINE.match(line):
                continue
            if line.startswith("#"):
                if not title:
                    title = line.lstrip("#").strip()
                elif body:
                    break
                continue
            body.append(re.sub(r"[*_`]", "", line))
            if sum(len(b) for b in body) > 160:
                break
        text = re.sub(r"\s+", " ", " ".join(body)).strip()
        out = f"{title}: {text}" if title and text else (title or text)
        if out:
            return out[:180]
    return ""


class Projects:
    """Where desk tasks may run.

    [[desk.projects]] in config.toml ({name, path, about?, aliases?}) when set. Otherwise every folder in
    desk.projects_root (default ~/projects), described from its README / AGENTS.md / CLAUDE.md, with
    names, aliases and hidden folders from data/hub/desk/projects.json
    ({"projects": {"<folder>": {"name", "aliases", "about", "kind"}}, "hidden": ["<folder>"]}).
    """

    _cache: dict[str, tuple[float, list[dict]]] = {}

    def __init__(self, rows: object, root: object = None, curated: Path | None = None) -> None:
        self.rows: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("path"), str):
                aliases = row.get("aliases") if isinstance(row.get("aliases"), list) else []
                self.rows.append({"name": row["name"], "path": row["path"], "about": str(row.get("about") or ""),
                                  "aliases": [str(a) for a in aliases]})
        if not self.rows and root:
            self.rows = self._catalog(Path(str(root)).expanduser(), curated)

    @classmethod
    def _catalog(cls, root: Path, curated: Path | None) -> list[dict]:
        key = f"{root}|{curated}"
        cached = cls._cache.get(key)
        if cached and time.time() - cached[0] < CATALOG_REFRESH:
            return cached[1]
        try:
            notes = json.loads(curated.read_text(encoding="utf-8")) if curated else {}
        except (OSError, json.JSONDecodeError):
            notes = {}
        hidden = set(notes.get("hidden") or [])
        named = notes.get("projects") if isinstance(notes.get("projects"), dict) else {}
        rows: list[dict] = []
        seen: set[str] = set()
        for rel, info in named.items():
            folder = root / rel
            if rel in hidden or not folder.is_dir() or not isinstance(info, dict):
                continue
            seen.add(rel)
            about = info.get("about") or _describe(folder)
            if info.get("kind"):
                about = f"[{info['kind']}] {about}".strip()
            rows.append({"name": str(info.get("name") or rel), "path": str(folder), "about": about,
                         "aliases": [str(a) for a in info.get("aliases") or []] + [rel]})
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            children = []
        for folder in children:
            if folder.name in seen or folder.name in hidden:
                continue
            rows.append({"name": folder.name, "path": str(folder), "about": _describe(folder), "aliases": []})
        cls._cache[key] = (time.time(), rows)
        return rows

    def prompt_block(self) -> str:
        if not self.rows:
            return "(No projects are configured. Leave project empty: the agent starts in the machine's default folder.)"
        lines = []
        for r in self.rows:
            alias = f" (aka {', '.join(a for a in r['aliases'][:4] if a != r['name'])})" if [a for a in r["aliases"] if a != r["name"]] else ""
            lines.append(f"- {r['name']}{alias}: {r['about'] or r['path']}")
        return "\n".join(lines)

    def resolve(self, name: object) -> dict | None:
        want = str(name or "").strip().strip("/").lower()
        if want.startswith("~/projects/"):
            want = want[len("~/projects/"):]
        if not want:
            return None
        for row in self.rows:
            if want in (row["name"].lower(), row["path"].lower(), Path(row["path"]).name.lower(), *(a.lower() for a in row["aliases"])):
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
            choose=self.choose_worker,
            locate=self.locate_machine,
            engine_down=self.engine_down,
            tracker=lambda item_id: hub.tracker.get(item_id),
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
        settings = self.settings()
        cache_key = json.dumps([key, settings["chat"], settings["model"]])
        if cache_key != self._provider_key or self._provider is None:
            from orbital.hub.desk_engines import build_fallback
            old = self._provider
            self._provider = build_fallback(
                key, self.hub.paths.hub_data, chat=settings["chat"], model=settings["model"],
                cli_model=str(self.hub.cfg.get("desk.cli_model") or ""),
                claude_model=str(self.hub.cfg.get("desk.claude_model") or "") or None,
                work_note=lambda: "Agents you start run on the satellites as usual.",
            )
            self._provider_key = cache_key
            if old is not None:
                with contextlib.suppress(RuntimeError):
                    asyncio.get_running_loop().create_task(old.close())
        return self._provider

    def model_info(self) -> dict:
        current = self.provider
        if current is None:
            return {"configured": False, "provider": None, "model": None, "note": NOT_CONFIGURED,
                    "worker_agent": self.hub.cfg.get("desk.worker_agent")}
        return {"configured": True, "provider": current.name, "model": current.label, "note": None,
                "engines": current.public() if hasattr(current, "public") else [],
                "worker_agent": self.hub.cfg.get("desk.worker_agent")}

    # Settings the app edits (System → Settings → Agent): the chat AI, its model, how work is handed out,
    # and the user's routing rules in plain words.

    @property
    def settings_path(self) -> Path:
        return self.dir / "settings.json"

    def settings(self) -> dict:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        return clean_settings(raw, default_chat=str(self.hub.cfg.get("desk.provider") or "auto"))

    def save_settings(self, raw: dict) -> dict:
        current = self.settings()
        merged = {**current, **{k: v for k, v in raw.items() if k in ("chat", "model", "dispatch", "rules")}}
        clean = clean_settings(merged, strict=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.settings_path)
        return clean

    def settings_options(self) -> dict:
        from orbital.hub.desk_engines import CHAT_MODELS
        agents: list[str] = []
        machines = []
        for row in self.hub.satellites.listing():
            offered = [a for a in (row.get("capabilities") or {}).get("agents") or [] if a != "echo"]
            agents.extend(a for a in offered if a not in agents)
            machines.append({"id": row["id"], "name": row.get("name") or row["id"], "status": row.get("status"), "agents": offered})
        return {
            "chats": [
                {"id": "auto", "title": "Automatic", "detail": "Grok subscription, or the xAI API when a key is set"},
                {"id": "grok", "title": "Grok", "detail": "Grok subscription (grok CLI)"},
                {"id": "claude", "title": "Claude", "detail": "Claude subscription (claude CLI)"},
                {"id": "xai", "title": "xAI API", "detail": "Paid per token; needs XAI_API_KEY"},
                {"id": "ondevice", "title": "On-device", "detail": "The Mac's built-in Apple model, offline"},
            ],
            "models": CHAT_MODELS,
            "dispatch": [
                {"id": "parallel", "title": "Parallel agents", "detail": "Every real ask goes to agents, split into parallel jobs"},
                {"id": "single", "title": "One agent", "detail": "One agent per request, never split"},
                {"id": "off", "title": "Talk only", "detail": "No agents; it only chats, notes to-dos and reminders"},
            ],
            "machines": machines,
            "agents": agents,
            "xai_key": bool(self.hub.auth.get("XAI_API_KEY")),
        }

    def rules_block(self) -> str:
        rules = [r["text"] for r in self.settings()["rules"] if r.get("enabled", True)]
        if not rules:
            return ""
        return "\n".join(f"- {text}" for text in rules)

    # Workers: which agent on which machine

    def worker_order(self) -> list[str]:
        """Agents in order of preference for real work: desk.worker_agents, else Grok first (the
        subscription), then Claude, Codex and Gemini."""
        raw = self.hub.cfg.get("desk.worker_agents")
        if isinstance(raw, list) and raw:
            return [str(a).lower() for a in raw]
        order = ["grok", "claude", "codex", "gemini"]
        first = str(self.hub.cfg.get("desk.worker_agent") or "").lower()
        return [first, *[a for a in order if a != first]] if first else order

    def _engine_out(self, agent: str) -> bool:
        health = getattr(self._provider, "health", None)
        return bool(health and agent in ("grok", "claude") and health.family_down(agent))

    def _local_machine(self) -> dict | None:
        name = str(self.hub.cfg.machine_name or "").lower()
        rows = self.hub.satellites.all()
        return next((r for r in rows if str(r.get("machine") or "").lower() == name), None) or next(
            (r for r in rows if r.get("ip") == "127.0.0.1"), None)

    def machines_block(self) -> str:
        local = (self._local_machine() or {}).get("id")
        lines = []
        for row in self.hub.satellites.listing():
            agents = [a for a in (row.get("capabilities") or {}).get("agents") or [] if a != "echo"]
            out = [a for a in agents if self._engine_out(a)]
            names = ", ".join(f"{a} (out right now)" if a in out else a for a in agents) or "no agents"
            here = " (this hub)" if row["id"] == local else ""
            lines.append(f"- {row.get('name') or row['id']}{here} [{row.get('status')}]: {names}{self._health_of(row)}")
        return "\n".join(lines)

    def system_lines(self) -> list[str]:
        """Everything the hub already holds, so status questions are answered from here without an agent:
        jobs on every machine, automations, unread alerts, and each page's latest summary."""
        out: list[str] = []
        desk_jobs = {t.get("job_id") for t in self.runner.tasks if t.get("job_id")}
        jobs = []
        for row in self.hub.satellites.all():
            for job in row.get("running_jobs") or []:
                if job.get("status") in ("queued", "running") and job.get("id") not in desk_jobs:
                    jobs.append((row.get("name") or row["id"], job))
        if jobs:
            out.append("Other agent jobs running on machines:")
            for machine, job in jobs[:12]:
                title = job.get("title") or first_sentence(job.get("prompt"), 80) or "job"
                out.append(f"- {machine}: {job.get('agent') or 'agent'} — {title} [{job.get('status')}, started {_ago(job.get('started_at') or job.get('created_at'))}]")
        try:
            autos = (self.hub.jobs.snapshot() or {}).get("jobs") or []
        except Exception:
            autos = []
        if autos:
            failing = [j for j in autos if j.get("failing_since") or (j.get("last") or {}).get("status") not in (None, "ok")]
            running = [j for j in autos if j.get("state") == "running"]
            agentic = sum(1 for j in autos if j.get("kind") == "agent")
            out.append(f"Automations: {len(autos)} scheduled ({agentic} start an agent), {len(running)} running now, {len(failing)} failing.")
            for job in running[:6]:
                out.append(f"- running: {job.get('name') or job['id']} (started {_ago(job.get('started_at'))})")
            for job in failing[:8]:
                last = job.get("last") or {}
                why = first_sentence(last.get("summary") or last.get("error") or "", 140)
                out.append(f"- failing: {job.get('name') or job['id']} since {_ago(job.get('failing_since') or last.get('finished_at'))}: {why}")
        try:
            alerts = [a for a in self.hub.store.load_alerts() if not a.get("acked")]
        except Exception:
            alerts = []
        if alerts:
            out.append(f"Unread alerts ({len(alerts)}), newest first:")
            for alert in sorted(alerts, key=lambda a: a.get("created_at") or "", reverse=True)[:8]:
                out.append(f"- {alert.get('title')} ({_ago(alert.get('created_at'))})")
        try:
            pages = self.hub.store.catalog()["sections"]
        except Exception:
            pages = []
        if pages:
            out.append("Pages in the app (title: latest summary, updated):")
            for page in pages[:40]:
                summary = page.get("summary") or page.get("subtitle") or ""
                out.append(f"- {page.get('title')}: {summary} (updated {_ago(page.get('updated_at'))})")
        return out

    @staticmethod
    def _health_of(row: dict) -> str:
        """CPU, memory, every disk and the browser bridge from the machine's last heartbeat."""
        if row.get("status") != "online":
            return f" (last seen {_ago(row.get('last_seen'))})"
        metrics = row.get("system_metrics") or {}
        parts = []
        load, cpus = (metrics.get("load") or [None])[0], metrics.get("cpus")
        if load is not None and cpus:
            parts.append(f"CPU load {load} on {cpus} cores")
        elif metrics.get("cpu_percent") is not None:
            parts.append(f"CPU {metrics['cpu_percent']}%")
        mem = metrics.get("memory") or {}
        if mem.get("percent") is not None:
            parts.append(f"memory {mem['percent']:.0f}% used")
        for disk in metrics.get("disks") or []:
            free = disk.get("free")
            free_text = f", {free / 1e9:.0f} GB free" if isinstance(free, (int, float)) else ""
            parts.append(f"disk {disk.get('mount')} {disk.get('percent', 0):.0f}% full{free_text}")
        if metrics.get("uptime_s"):
            parts.append(f"up {metrics['uptime_s'] / 86400:.0f} days")
        browser = row.get("browser_status") or {}
        if browser.get("state") and browser["state"] != "disabled":
            parts.append(f"browser {browser['state'].replace('_', ' ')}")
        return f" — {', '.join(parts)}" if parts else ""

    def choose_worker(self, task: dict) -> dict:
        """{"agent", "satellite"} for a task, or {"wait": why} when nothing can take it now."""
        want_machine = task.get("machine")
        if want_machine:
            row = self.hub.satellites.get(want_machine)
            if row is None:
                return {"wait": f"There's no machine called {want_machine}."}
            if self.hub.satellites.status(row) != "online":
                return {"wait": f"{row.get('name')} is offline; the job starts when it's back."}
            if not self.hub.satellites.is_callback(row):
                return {"wait": f"{row.get('name')} joined in tunnel mode, so the hub can't start agents there yet."}
            satellite = row["id"] if row["id"] != (self._local_machine() or {}).get("id") else None
        else:
            row, satellite = self._local_machine(), None
        offered = [a for a in ((row or {}).get("capabilities") or {}).get("agents") or [] if a != "echo"]
        order = [task["agent_wanted"]] if task.get("agent_wanted") else self.worker_order()
        skip = set(task.get("engines_down") or [])
        for agent in order:
            if offered and agent not in offered:
                continue
            if agent in skip or self._engine_out(agent):
                continue
            return {"agent": agent, "satellite": satellite}
        if not offered and not task.get("agent_wanted"):
            return {"agent": str(self.hub.cfg.get("desk.worker_agent") or order[0]), "satellite": satellite}
        where = (row or {}).get("name") or "this machine"
        wanted = task.get("agent_wanted")
        if wanted and offered and wanted not in offered:
            return {"wait": f"{wanted} isn't installed on {where}."}
        return {"wait": f"No agent on {where} can run right now (usage or sign-in); the job starts when one is back."}

    def locate_machine(self, satellite_id: str) -> tuple[str, str] | None:
        row = self.hub.satellites.get(satellite_id)
        if row is None or not self.hub.satellites.is_callback(row):
            return None
        return self.hub.satellites.base_url(row), str(row.get("satellite_token") or "")

    def engine_down(self, agent: str, text: str) -> bool:
        """A failed run whose output says the engine ran out: mark it down so the next agent takes over."""
        from orbital.hub.desk_engines import classify
        kind = classify(text or "")
        if kind not in ("quota", "auth", "rate", "billing"):
            return False
        health = getattr(self.provider, "health", None)
        if health and agent in ("grok", "claude"):
            health.fail(agent, text[-400:])
        return True

    def projects(self) -> Projects:
        return Projects(self.hub.cfg.get("desk.projects", []), self.hub.cfg.get("desk.projects_root", "~/projects"),
                        self.dir / "projects.json")

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
        settings = self.settings()
        lines = [SYSTEM_RULES, DISPATCH_NOTES[settings["dispatch"]]]
        rules = self.rules_block()
        if rules:
            lines += ["", "The user's routing rules. Follow them when you set agent and machine on a task (they override your own pick):", rules]
        lines += ["", "Projects (name: about):", self.projects().prompt_block(), "", "Live state"]
        lines.append(f"Now: {now.strftime('%A %Y-%m-%d %H:%M')} ({now.tzname()}).")
        machines = self.machines_block()
        if machines:
            lines.append("Machines (name [status]: agents that can run there):")
            lines.append(machines)
        lines.extend(self.system_lines())
        carry = self._carried()
        if carry:
            lines.append("Notes from before your context was last cleared (still true unless the user changed it since):")
            lines.extend(_indent(carry))
        lines.extend(self.task_lines())
        lines.extend(self.tracker_lines())
        return "\n".join(lines)

    # The live state's task blocks. Each block is a header line, then lines starting "- " or
    # indented, so a warm session can be sent only the blocks that changed (desk_engines.LiveDiff).

    def task_lines(self) -> list[str]:
        lines: list[str] = []
        running = self.runner.running()
        if running:
            lines.append("Desk tasks running or queued (progress, what the agent reported so far):")
            for task in running:
                lines.extend(self._running_lines(task))
        else:
            lines.append("Desk tasks running or queued: none.")
        finished = [t for t in self.runner.tasks if t["status"] in ("done", "failed", "stopped") and _age_s(t.get("finished_at")) < 2 * 86400]
        finished.sort(key=lambda t: t.get("finished_at") or "")
        finished = finished[-RECENT_TASKS:]
        if finished:
            lines.append("Desk tasks finished recently, newest first. Their reports are what the agents found and wrote; answer from them:")
            budget = REPORT_BUDGET
            for task in reversed(finished):
                block, used = self._finished_lines(task, budget)
                budget -= used
                lines.extend(block)
        return lines

    def _running_lines(self, task: dict) -> list[str]:
        where = f" in {task['project']}" if task.get("project") else ""
        on = f" on {task['machine']}" if task.get("machine") else ""
        who = f" ({task['agent']})" if task.get("agent") else ""
        pct = f", {task['progress']}%" if task.get("progress") not in (None, 0) and task["status"] == "running" else ""
        since = _ago(task.get("started_at") or task["created_at"])
        out = [f"- {task['id']} [{task['status']}{pct}, started {since}] {task['title']}{where}{on}{who}"]
        if task.get("waiting"):
            out.append(f"  waiting: {task['waiting']}")
        notes = [n["text"] for n in (task.get("notes") or [])][-3:]
        if notes:
            out.append("  latest notes: " + " | ".join(_one_line(n, 220) for n in notes))
        elif task.get("last_note"):
            out.append(f"  latest output: {_one_line(task['last_note'], 220)}")
        findings = [f["text"] for f in (task.get("findings") or [])][-8:]
        if findings:
            out.append("  found so far: " + " | ".join(_one_line(f, 300) for f in findings))
        if task.get("answer"):
            out.append(f"  answer so far: {_one_line(task['answer'], 600)}")
        if task.get("question"):
            out.append(f"  ASKS THE USER: \"{_one_line(task['question'], 400)}\" (their reply goes back with a followup to {task['id']})")
        if task.get("blocked"):
            out.append(f"  blocked, needs the user: {_one_line(task['blocked'], 400)}")
        queued = task.get("queue") or []
        if queued:
            out.append("  follow-ups not yet read by the agent: " + " | ".join(_one_line(q, 160) for q in queued[-3:]))
        return out

    def _finished_lines(self, task: dict, budget: int) -> tuple[list[str], int]:
        """A finished task: the full report while the budget lasts, else its first sentences."""
        where = f" in {task['project']}" if task.get("project") else ""
        out = [f"- {task['id']} [{task['status']} {_ago(task.get('finished_at'))}] {task['title']}{where}"]
        used = 0
        if task.get("answer") and task.get("answer_run") == task.get("runs"):
            out.append(f"  answer: {_one_line(task['answer'], 800)}")
        if task["status"] != "done" and task.get("error"):
            out.append(f"  why it stopped: {_one_line(task['error'], 300)}")
        if task.get("blocked"):
            out.append(f"  needed the user for: {_one_line(task['blocked'], 300)}")
        findings = [f["text"] for f in (task.get("findings") or [])][-8:]
        if findings:
            out.append("  findings: " + " | ".join(_one_line(f, 300) for f in findings))
        report = (task.get("summary") or "").strip()
        if report:
            fresh = _age_s(task.get("finished_at")) < FULL_REPORT_AGE
            if fresh and budget >= 600:
                text = _clip(report, min(REPORT_EACH, budget))
                out.append("  report:")
                out.extend(_indent(text, "    "))
                used = len(text)
            else:
                out.append(f"  report starts: {_one_line(_sentences(report, 2), 320)}")
        return out, used

    def tracker_lines(self) -> list[str]:
        """Work the desk didn't start: agents the user runs directly, and the user's own items."""
        lines: list[str] = []
        desk_items = self.tracker_ids()
        items = self.hub.tracker.items()
        active = [i for i in items if i.get("status") == "active" and i.get("id") not in desk_items][:12]
        if active:
            lines.append("Other work in the tracker right now:")
            for item in active:
                who = _agent_name(item.get("agent")) if item.get("source") == "agent" else "The user"
                where = f" in {Path(item['workspace']).name}" if item.get("workspace") else ""
                pct = shown_progress(item)["percent"]
                notes = item.get("notes") or []
                note = f" — latest: {_one_line(notes[-1].get('text'), 300)}" if notes else ""
                lines.append(f"- {who}: {item.get('title')}{where}{f' ({pct}%)' if pct else ''}{note}")
        done_other = [
            i for i in items
            if i.get("status") == "done" and i.get("id") not in desk_items and _age_s(i.get("completedAt")) < 86400
        ]
        done_other.sort(key=lambda i: i.get("completedAt") or "", reverse=True)
        if done_other:
            lines.append("Finished in the tracker today, newest first:")
            for index, item in enumerate(done_other[:8]):
                who = _agent_name(item.get("agent")) if item.get("source") == "agent" else "The user"
                size = 700 if index < 3 else 160
                lines.append(f"- {who}: {item.get('title')} — {_one_line(_sentences(item.get('summary'), 4 if index < 3 else 1), size)}")
        return lines

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

    def say(self, text: str, source: str = "text", client_id: str | None = None, interrupt: dict | None = None,
            client_platform: str | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        platform = client_platform if client_platform in TRACKER_ORIGINS else None
        asyncio.get_running_loop().create_task(self._turn(queue, text, source, client_id, interrupt, platform))
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

    async def _turn(self, queue: asyncio.Queue, text: str, source: str, client_id: str | None,
                    interrupt: dict | None = None, client_platform: str | None = None) -> None:
        try:
            await self._turn_inner(queue, text, source, client_id, interrupt, client_platform)
        except Exception as exc:  # the app always gets an answer
            get_logger("hub").exception("desk turn failed")
            msg = await self._append(self._event("error", f"Something broke on the hub: {exc.__class__.__name__}", speak="Something broke on the hub."))
            await queue.put({"type": "event", "message": msg})
        finally:
            await queue.put({"type": "done"})
            await queue.put(None)

    async def _turn_inner(self, queue: asyncio.Queue, text: str, source: str, client_id: str | None,
                          interrupt: dict | None = None, client_platform: str | None = None) -> None:
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
            await self._reply(queue, turn, user, text, client_platform)
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

    async def _reply(self, queue: asyncio.Queue, turn: "_Turn", user: dict, text: str,
                     client_platform: str | None = None) -> None:
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
                event, did = await self._run_action(action, text, client_platform)
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

    async def _run_action(self, action: dict, origin: str,
                          client_platform: str | None = None) -> tuple[dict | None, str | None]:
        kind = str(action.get("type") or "").lower()
        if kind in ("task", "followup") and self.settings()["dispatch"] == "off":
            return None, None
        if kind == "task":
            projects = self.projects()
            wanted = action.get("project")
            project = projects.resolve(wanted)
            if wanted and project is None and projects.rows:
                return self._event("error", f"There's no project called {wanted}.", speak=f"I couldn't find {wanted}. Which project?"), None
            title = str(action.get("title") or "Agent task")
            brief = str(action.get("brief") or origin)
            task = await self.runner.create(
                project["name"] if project else None, project["path"] if project else None, title, brief, origin,
                effort=str(action.get("effort") or "high").lower(), agent=str(action.get("agent") or ""),
                machine=str(action.get("machine") or ""),
                client_platform=client_platform,
            )
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
            if task["status"] == "running":
                text, speak = f"Passed to {task['title']}: {message}", f"Passed that to the {task['title']} agent."
            else:
                text, speak = f"Restarted {task['title']}: {message}", f"Restarted the {task['title']} agent."
            return self._event("task_followup", text, speak=speak, task_id=task["id"]), f"follow-up to {task['id']}"
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
        # The agent already sent its answer this run, and it was read out when it came in.
        answered = ok and bool(task.get("answer")) and task.get("answer_run") == task.get("runs")
        spoken = first_sentence(task.get("summary")) if ok else ""
        if answered:
            text = f"Done: {task['title']}. {first_sentence(task['answer'], 300)}".strip()
            speak = None
            spoken = task["answer"]
        elif ok:
            text = f"Done: {task['title']}. {spoken}".strip()
            speak = f"{task['title']} is done. {spoken}".strip()
        else:
            reason = task.get("blocked") or task.get("error") or "no result"
            text = f"{task['title']} stopped without finishing: {reason}"
            speak = f"{task['title']} stopped without finishing."
        task["spoken"] = speak or task.get("spoken")
        await self._append(self._event("task_done" if ok else "task_failed", text, speak=speak, task_id=task["id"], tracker_id=task.get("tracker_id")))
        await self.hub.raise_alerts([{
            "schema": "orbital.alert.v1",
            "id": f"desk-{task['id']}-{task.get('runs') or 1}",
            "kind": "event",
            "title": f"{'Done' if ok else 'Needs a look'}: {task['title']}",
            "subtitle": task.get("project"),
            "body": (spoken or text)[:400],
            "thread_id": "agent-desk",
            # An answer already buzzed when it came in.
            "interruption_level": "passive" if answered else "timeSensitive" if ok else "active",
            "launch_url": f"orbital://agent?task={task['id']}",
            "user_info": {"source": "agent-desk", "task_id": task["id"], "tracker_id": task.get("tracker_id")},
        }])

    async def on_report(self, task: dict, result: dict) -> None:
        """An agent reported on its desk channel: the apps follow; an answer, a question or a block
        is said out loud in the thread (the app speaks it while the user is talking to the desk) and
        raised as an alert, so the user hears it without asking."""
        await self._task_changed(self.runner.public(task))
        if not result.get("announce"):
            return
        kind, text, title = result["kind"], result["text"], task["title"]
        heard = _speakable(text)
        if kind == "answer":
            event = self._event("task_answer", f"{title}: {text}", speak=heard, task_id=task["id"], tracker_id=task.get("tracker_id"))
            alert_title, level = f"Answer: {title}", "timeSensitive"
        elif kind == "question":
            event = self._event("task_question", f"{title} asks: {text}", speak=f"The {title} agent asks: {heard}", task_id=task["id"], tracker_id=task.get("tracker_id"))
            alert_title, level = f"Question: {title}", "timeSensitive"
        else:
            event = self._event("task_blocked", f"{title} needs you: {text}", speak=f"{title} needs you. {heard}", task_id=task["id"], tracker_id=task.get("tracker_id"))
            alert_title, level = f"Needs you: {title}", "active"
        await self._append(event)
        await self.hub.raise_alerts([{
            "schema": "orbital.alert.v1",
            "id": f"desk-{task['id']}-{kind}-{_new_id('')}",
            "kind": "event",
            "title": alert_title,
            "subtitle": task.get("project"),
            "body": text[:400],
            "thread_id": "agent-desk",
            "interruption_level": level,
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


def _one_line(text: object, limit: int) -> str:
    """Whitespace folded to single spaces, cut at a word near `limit`."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    return (cut[: cut.rfind(" ")] if " " in cut[limit // 2 :] else cut) + "…"


def _sentences(text: object, count: int) -> str:
    flat = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", flat)
    return " ".join(parts[:count])


def _clip(text: str, limit: int) -> str:
    """At most `limit` characters, ending at a line or sentence, saying when there's more."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("\n"), cut.rfind(". "))
    if end > limit * 0.6:
        cut = cut[: end + 1]
    return cut.rstrip() + "\n[…the report goes on; send the agent a followup if the user needs the rest]"


def _indent(text: str, prefix: str = "  ") -> list[str]:
    return [prefix + line if line.strip() else prefix.rstrip() for line in str(text).strip().splitlines()]


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

    @app.get("/v1/desk/settings")
    async def desk_settings(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        return JSONResponse({"settings": desk.settings(), "options": desk.settings_options(), "model": desk.model_info()})

    @app.put("/v1/desk/settings")
    async def desk_settings_put(request: Request) -> JSONResponse:
        """{chat?, model?, dispatch?, rules?: [{id?, text, enabled?}]}: what's sent replaces those keys."""
        denied = hub.require(request)
        if denied:
            return denied
        try:
            settings = desk.save_settings(await body_of(request))
        except ValueError as exc:
            return fail(str(exc), 400)
        info = desk.model_info()
        await hub.live.broadcast({"type": "desk_settings", "settings": settings, "model": info})
        return JSONResponse({"ok": True, "settings": settings, "model": info})

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
        platform = resolve_client_platform(hub, request, body)
        queue = desk.say(text, source, client_id, interrupt, client_platform=platform)

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

    # The desk channel: an agent the desk started reports here and reads what the person added.
    # Its own task's channel token (ORBITAL_DESK_TOKEN) or the hub's usual sign-in.

    def channel(request: Request, task_id: str) -> tuple[dict | None, bool, JSONResponse | None]:
        """(task, is_the_agent, denied)."""
        agent = desk.runner.by_token(task_id, bearer(request))
        if agent is None and not hub.auth.authorized(request):
            return None, False, fail("unauthorized", 401, code="unauthorized")
        task = agent or desk.runner.get(task_id)
        if task is None:
            return None, False, fail("no such task", 404)
        return task, agent is not None, None

    @app.post("/v1/desk/tasks/{task_id}/report")
    async def desk_task_report(task_id: str, request: Request) -> JSONResponse:
        """{kind: note|finding|answer|question|blocked, text, progress?} → {ok, status, inbox: [...]}."""
        task, is_agent, denied = channel(request, task_id)
        if denied:
            return denied
        body = await body_of(request)
        try:
            result = desk.runner.report(task, body.get("kind"), body.get("text"), body.get("progress"))
        except ValueError as exc:
            return fail(str(exc), 400)
        get_logger("hub").info("desk report", extra={"fields": {"task": task_id, "kind": result["kind"], "chars": len(result["text"])}})
        await desk.on_report(task, result)
        # Whatever the person added rides back on every report, so the agent sees it at its next step.
        inbox = desk.runner.take_inbox(task) if is_agent else []
        if inbox:
            await desk._task_changed(desk.runner.public(task))
        return JSONResponse({"ok": True, "task": task_id, "status": task["status"], "progress": task.get("progress"), "inbox": inbox})

    @app.get("/v1/desk/tasks/{task_id}/inbox")
    async def desk_task_inbox(task_id: str, request: Request, wait: float = 0) -> JSONResponse:
        """What the person added since the agent last looked; `wait` (≤ 120 s) holds until something comes."""
        task, is_agent, denied = channel(request, task_id)
        if denied:
            return denied
        if not is_agent:  # the app or a person looking: show it without marking it read
            return JSONResponse({"task": task_id, "status": task["status"], "messages": task.get("inbox") or []})
        messages = await desk.runner.wait_inbox(task, min(max(float(wait or 0), 0.0), 120.0))
        if messages:
            await desk._task_changed(desk.runner.public(task))
        return JSONResponse({"task": task_id, "status": task["status"], "messages": messages})

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
