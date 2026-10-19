"""The fast model behind the Grok desk.

the user's Grok subscription is the default; the paid API is only used when a key is set.
1. Warm Grok session (default, subscription): one `grok agent stdio` process kept open with a
   no-tools desk profile, in an empty scratch folder. Every permission request is refused. Replies
   stream, and the session's prompt cache makes the first words arrive in about 2 s (the first
   turn of a fresh session takes about 3.5 s).
2. Grok CLI one-shot (`grok --json-schema`): the fallback when the warm session fails. Process
   start plus the full prompt every time, 5–9 seconds, delivered in one piece.
3. xAI API, only if `XAI_API_KEY` is in secrets.env (billed per token): sub-second.

When Grok can't answer at all (usage balance used up, signed out), the desk keeps working on
backups, in order:
4. Claude CLI (`claude -p`, the user's Claude subscription, never the paid API): no tools, run
   outside ~/projects. Streams.
5. Apple's on-device model on the Mac (orbital/hub/ondevice/desk_fm.swift): no account, no network,
   no limit. Talks, takes to-dos and reminders, and queues work for when an agent engine is back.

`EngineHealth` remembers which engines are down and why, so a dead one is skipped instead of
costing the user seconds on every turn, and the desk probes it until it answers again.

All of them yield plain text. The desk splits it into the spoken reply and `>>>` action lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, AsyncIterator

import httpx

from orbital.hub.desk_router import ClaudeRouter, Route
from orbital.hub.desk_tools import tool_env, which

SERVER_DIR = Path(__file__).resolve().parent

XAI_URL = "https://api.x.ai/v1"
DEFAULT_XAI_MODEL = "grok-4.20-0309-non-reasoning"
DEFAULT_CLI_MODEL = "grok-4.7-build-fast"
CLI_TOOLS = ",".join(
    [
        "run_terminal_command", "read_file", "search_replace", "list_dir", "grep", "write", "write_file",
        "kill_command_or_subagent", "todo_write", "get_command_or_subagent_output", "spawn_subagent",
        "scheduler_create", "scheduler_delete", "scheduler_list", "monitor", "search_tool", "use_tool",
        "workflow", "enter_plan_mode", "exit_plan_mode", "ask_user_question", "send_feedback",
        "image_gen", "image_edit", "image_to_video", "reference_to_video", "web_search", "web_fetch",
    ]
)


from orbital.hub.desk_model import ModelError  # noqa: E402  one error type for the desk


# Engine health

QUOTA_RE = re.compile(
    r"\b402\b|payment required|balance (?:is )?exhausted|usage (?:limit|balance)|out of (?:credits|usage)|"
    r"credit balance|quota|hit your (?:usage )?limit|limit reached",
    re.I,
)
AUTH_RE = re.compile(
    r"\b401\b|\b403\b|authenticat|re-?auth|not logged in|log ?in again|/login|token (?:has )?expired|"
    r"unauthori[sz]ed|forbidden|invalid api key",
    re.I,
)
RATE_RE = re.compile(r"\b429\b|\b529\b|rate.?limit|too many requests|overloaded", re.I)
# Claude signed in for pay-per-use API billing instead of the user's subscription: never used.
BILLING_RE = re.compile(r"pay-per-use", re.I)
MISSING_RE = re.compile(r"not found|unavailable|not available|no such file|didn't build", re.I)

# How long an engine is skipped before it's tried again, by why it failed. Quota and sign-in
# problems hit every model on the account, so they take the whole family down; anything else
# only that one way of reaching it, backing off from 30 s.
COOLDOWN = {"quota": 15 * 60, "auth": 10 * 60, "billing": 10 * 60, "rate": 90, "missing": 30 * 60}
FAMILY_WIDE = {"quota", "auth", "billing", "missing"}
FAMILY_NAMES = {"grok": "Grok", "claude": "Claude", "ondevice": "the Mac's built-in model", "xai": "the xAI API"}
FIX_HINTS = {
    ("grok", "quota"): "back when the balance resets or is topped up",
    ("grok", "auth"): "run grok login on the Mac",
    ("claude", "quota"): "back when the usage limit resets",
    ("claude", "auth"): "run claude on the Mac, then /login",
    ("claude", "billing"): "run claude on the Mac, /login, and pick your Claude subscription",
}


GROK_LOG = Path.home() / ".grok" / "logs" / "unified.jsonl"
# The Grok CLI logs its plan's credits every 30 s while any grok process runs; older than this,
# a top-up or early reset can't be seen in it, so the prober checks for itself once an hour.
GROK_BILLING_FRESH = 10 * 60
STALE_RECHECK = 3600
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def grok_billing(log: Path | None = None) -> dict | None:
    """the user's Grok plan from the CLI's own log ("billing: fetched credits config"): how much of
    this period's credit is used, the prepaid balance, and when the period ends. No network."""
    path = log or GROK_LOG
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4 * 1024 * 1024))
            lines = handle.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        if b"billing: fetched credits config" not in raw:
            continue
        try:
            row = json.loads(raw)
            config = (row.get("ctx") or {}).get("config") or {}
            period = config.get("currentPeriod") or {}
            end = _ts(period.get("end") or config.get("billingPeriodEnd"))
            return {
                "used_percent": float(config.get("creditUsagePercent") or 0),
                "prepaid": float((config.get("prepaidBalance") or {}).get("val") or 0),
                "period": str(period.get("type") or "").replace("USAGE_PERIOD_TYPE_", "").lower() or None,
                "resets_at": end,
                "tier": (row.get("ctx") or {}).get("subscriptionTier"),
                "at": _ts(row.get("ts")),
            }
        except (ValueError, TypeError, AttributeError):
            continue
    return None


def parse_reset(text: str, now: datetime | None = None) -> float | None:
    """When a usage limit lifts, from the engine's own message: "…limit reached|1760630400",
    "resets 3pm (America/New_York)", "resets Oct 20, 9:30am"."""
    if not text:
        return None
    match = re.search(r"\|(\d{10})\b", text)
    if match:
        return float(match.group(1))
    match = re.search(
        r"resets?\s+(?:at\s+)?(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
        r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)\b(?:\s*\((?P<tz>[A-Za-z_]+/[A-Za-z_/]+)\))?",
        text,
        re.I,
    )
    if not match or (match.group("mon") and match.group("mon").lower() not in MONTHS):
        return None
    try:
        zone = ZoneInfo(match.group("tz")) if match.group("tz") else None
    except Exception:
        zone = None
    base = (now or datetime.now().astimezone()).astimezone(zone) if zone else (now or datetime.now().astimezone())
    hour = int(match.group("h")) % 12 + (12 if match.group("ap").lower() == "pm" else 0)
    when = base.replace(hour=hour, minute=int(match.group("m") or 0), second=0, microsecond=0)
    if match.group("mon"):
        when = when.replace(month=MONTHS[match.group("mon").lower()], day=int(match.group("day")))
        if when < base - timedelta(days=1):
            when = when.replace(year=when.year + 1)
    elif when <= base:
        when += timedelta(days=1)
    return when.timestamp()


def when_text(epoch: float, now: datetime | None = None) -> str:
    """'today at 3 PM', 'tomorrow at 10:24 AM', 'Monday at 9 AM'."""
    when = datetime.fromtimestamp(epoch).astimezone()
    today = (now or datetime.now().astimezone()).date()
    clock = when.strftime("%-I:%M %p").replace(":00 ", " ")
    days = (when.date() - today).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else when.strftime("%A") if 1 < days < 7 else when.strftime("%b %-d")
    return f"{day} at {clock}"


def quota_reset(family: str, error: str) -> float | None:
    """When this family's usage comes back, if it can be known."""
    if family == "grok":
        billing = grok_billing()
        if billing and billing["resets_at"] and billing["resets_at"] > time.time() and billing["used_percent"] >= 100 and billing["prepaid"] <= 0:
            return billing["resets_at"]
        return None
    return parse_reset(error)


def classify(error: str) -> str:
    """Why an engine failed: quota, auth, rate, missing or error."""
    for kind, pattern in (("billing", BILLING_RE), ("quota", QUOTA_RE), ("auth", AUTH_RE), ("rate", RATE_RE), ("missing", MISSING_RE)):
        if pattern.search(error or ""):
            return kind
    return "error"


def family_of(provider: Any) -> str:
    return getattr(provider, "family", None) or getattr(provider, "name", "model")


class EngineHealth:
    """Which engines are down, why, and until when. Saved, so a restart doesn't re-learn it."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.families: dict[str, dict] = {}
        self.providers: dict[str, dict] = {}
        if path and path.is_file():
            with contextlib.suppress(Exception):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.families = raw.get("families") or {}
                self.providers = raw.get("providers") or {}

    def _save(self) -> None:
        if not self.path:
            return
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"families": self.families, "providers": self.providers}, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def family_down(self, family: str) -> dict | None:
        return self.families.get(family) or None

    def family_ok(self, family: str) -> bool:
        state = self.families.get(family)
        return not state or time.time() >= state.get("until", 0)

    def ok(self, provider: Any) -> bool:
        state = self.providers.get(getattr(provider, "name", ""))
        return self.family_ok(family_of(provider)) and (not state or time.time() >= state.get("until", 0))

    def fail(self, provider_or_family: Any, error: str) -> str:
        """Record a failure; returns its kind."""
        kind = classify(error)
        family = provider_or_family if isinstance(provider_or_family, str) else family_of(provider_or_family)
        name = family if isinstance(provider_or_family, str) else getattr(provider_or_family, "name", family)
        if kind in FAMILY_WIDE or isinstance(provider_or_family, str):
            table, key = self.families, family
        else:
            table, key = self.providers, name
        state = table.get(key) or {}
        failures = int(state.get("failures") or 0) + 1
        wait = COOLDOWN.get(kind) or min(300, 30 * 2 ** (failures - 1))
        now = time.time()
        row = {
            "kind": kind,
            "error": (error or "")[:300],
            "failures": failures,
            "since": state.get("since") or now,
            "checked": now,
            "until": now + wait,
        }
        if kind == "quota":
            # Parked until its usage comes back, not re-tried every few minutes.
            resets_at = quota_reset(family, error)
            if resets_at and resets_at > now:
                row["resets_at"] = resets_at
                row["until"] = resets_at + 30
        table[key] = row
        self._save()
        return kind

    def recheck(self, family: str, billing: dict | None = None) -> None:
        """A parked Grok comes back early when its own billing log shows a top-up or a new
        period; with no fresh log, it's checked once an hour (a refused call costs nothing)."""
        state = self.families.get(family)
        if not state or not state.get("resets_at") or family != "grok":
            return
        now = time.time()
        fresh = billing and billing.get("at") and now - billing["at"] < GROK_BILLING_FRESH
        if fresh:
            if billing["used_percent"] < 100 or billing["prepaid"] > 0 or (billing["resets_at"] or 0) > state["resets_at"] + 60:
                state["until"] = now
                self._save()
        elif now - state.get("checked", now) >= STALE_RECHECK and state["until"] > now:
            state["until"] = now
            self._save()

    def succeed(self, provider_or_family: Any) -> bool:
        """It answered. Returns True when its family had been down (it just came back)."""
        family = provider_or_family if isinstance(provider_or_family, str) else family_of(provider_or_family)
        was_down = self.families.pop(family, None) is not None
        changed = was_down
        if not isinstance(provider_or_family, str):
            changed = self.providers.pop(getattr(provider_or_family, "name", ""), None) is not None or changed
        if changed:
            self._save()
        return was_down

    def due_probes(self) -> list[str]:
        """Down families whose wait is over: time to check whether they're back."""
        now = time.time()
        return [family for family, state in self.families.items() if now >= state.get("until", 0)]

    def reason(self, family: str) -> str:
        """Plain words for the user: why this engine can't answer and what brings it back."""
        name = FAMILY_NAMES.get(family, family)
        state = self.families.get(family)
        if not state:
            return f"{name} is working"
        kind = state.get("kind")
        if kind == "quota" and state.get("resets_at"):
            period = "weekly " if family == "grok" and (grok_billing() or {}).get("period") == "weekly" else ""
            return f"{name}'s {period}usage is used up (resets {when_text(state['resets_at'])})"
        what = {
            "quota": f"{name}'s usage is used up",
            "auth": f"{name} needs a sign-in on the Mac",
            "rate": f"{name} is rate limited",
            "missing": f"{name} isn't available on the Mac",
            "billing": f"{name} is signed in for pay-per-use API billing, not your subscription",
        }.get(kind, f"{name} keeps failing")
        hint = FIX_HINTS.get((family, kind))
        return f"{what} ({hint})" if hint else what

    def public(self) -> list[dict]:
        rows = []
        for family, state in self.families.items():
            rows.append(
                {
                    "engine": family,
                    "name": FAMILY_NAMES.get(family, family),
                    "status": "down",
                    "kind": state.get("kind"),
                    "reason": self.reason(family),
                    "error": state.get("error"),
                    "since": datetime.fromtimestamp(state.get("since") or time.time()).astimezone().isoformat(timespec="seconds"),
                    "retry_at": datetime.fromtimestamp(state.get("until") or time.time()).astimezone().isoformat(timespec="seconds"),
                    "resets_at": datetime.fromtimestamp(state["resets_at"]).astimezone().isoformat(timespec="seconds") if state.get("resets_at") else None,
                }
            )
        return rows


class XAIProvider:
    name = "xai"
    family = "xai"

    def __init__(self, api_key: str, model: str = DEFAULT_XAI_MODEL, effort: str = "", transport=None) -> None:
        self.api_key = api_key
        self._transport = transport
        self.model = model or DEFAULT_XAI_MODEL
        self.effort = effort
        self._client: httpx.AsyncClient | None = None
        self._warm_at = 0.0

    @property
    def label(self) -> str:
        return self.model

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=XAI_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120),
                transport=self._transport,
            )
        return self._client

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        """Open TLS to api.x.ai ahead of the first word. Costs no tokens."""
        if time.time() - self._warm_at < 45:
            return
        self._warm_at = time.time()
        try:
            await self.client().get("/models", timeout=5)
        except httpx.HTTPError:
            pass

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "temperature": 0.4,
            "max_tokens": 900,
        }
        if self.effort:
            body["reasoning_effort"] = self.effort
        self._warm_at = time.time()
        try:
            async with self.client().stream("POST", "/chat/completions", json=body) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise ModelError(f"xAI {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield text
        except httpx.HTTPError as exc:
            raise ModelError(f"xAI unreachable: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def _s(description: str) -> dict:
    return {"type": "string", "description": description}


def _list(description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "object", "additionalProperties": False, "properties": props, "required": required},
    }


# The Grok Build model reaches for tools whatever the prompt says. Constrained JSON keeps it to
# a reply plus actions, and the typed lists make it fill the fields the desk needs.
CLI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reply": _s(
            "One or two short sentences spoken aloud to the user. Answer now from the live state and project list; "
            "never say you are checking, looking, or will find out."
        ),
        "tasks": _list(
            "Work for a Grok agent on the Mac. Empty unless the user asked for work.",
            {
                "project": _s("Exact path from the project list, e.g. my-app"),
                "title": _s("2-5 word title"),
                "brief": _s("Everything the agent needs: the request restated in full, where, what done looks like"),
                "effort": {"type": "string", "enum": ["medium", "high", "xhigh"]},
            },
            ["project", "title", "brief", "effort"],
        ),
        "followups": _list("Additions to an existing task.", {"task": _s("task id like t3"), "message": _s("what to add")}, ["task", "message"]),
        "cancels": _list("Tasks to stop.", {"task": _s("task id")}, ["task"]),
        "todos": _list("Things the user must do themselves, no time.", {"title": _s("short to-do"), "notes": _s("optional")}, ["title"]),
        "reminders": _list("Things with a time.", {"title": _s("what"), "due": _s("YYYY-MM-DDTHH:MM local"), "notes": _s("optional")}, ["title", "due"]),
    },
    "required": ["reply", "tasks", "followups", "cancels", "todos", "reminders"],
}
CLI_KINDS = {"tasks": "task", "followups": "followup", "cancels": "cancel", "todos": "todo", "reminders": "reminder"}


class GrokCLIProvider:
    """No key needed, but slow (5–9 s) and not streamed: the reply arrives in one piece."""

    name = "grok-cli"
    family = "grok"

    def __init__(self, scratch: Path, model: str = DEFAULT_CLI_MODEL, effort: str = "low") -> None:
        self.scratch = scratch
        self.model = model or DEFAULT_CLI_MODEL
        self.effort = effort or "low"
        self.binary = which("grok")

    @property
    def label(self) -> str:
        return f"{self.model} via grok CLI"

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        return None

    @staticmethod
    def transcript(messages: list[dict]) -> str:
        lines = []
        for msg in messages[:-1]:
            who = "User" if msg["role"] == "user" else "You"
            lines.append(f"{who}: {msg['content']}")
        last = messages[-1]["content"] if messages else ""
        history = "\n".join(lines)
        if history:
            return f"Conversation so far:\n{history}\n\nThe user just said: {last}"
        return f"The user just said: {last}"

    @staticmethod
    def render(body: dict) -> str:
        """The schema's lists back into the reply + `>>>` lines the desk already parses."""
        out = [str(body.get("reply") or "").strip()]
        for key, kind in CLI_KINDS.items():
            for item in body.get(key) or []:
                if isinstance(item, dict):
                    out.append(">>> " + json.dumps({"type": kind, **item}, ensure_ascii=False))
        return "\n".join(line for line in out if line)

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        if not self.binary:
            raise ModelError("grok CLI not found and XAI_API_KEY is not set")
        self.scratch.mkdir(parents=True, exist_ok=True)
        prompt_file = self.scratch / f"turn-{int(time.time() * 1000)}.txt"
        # This model only trusts what sits next to the question, so the live state goes here too.
        head, _, live = system.partition("\nLive state\n")
        turn = self.transcript(messages)
        if live:
            turn = f"Live state right now (current; answer status questions from this):\n{live}\n\n{turn}"
        prompt_file.write_text(turn, encoding="utf-8")
        args = [
            self.binary,
            "--prompt-file", str(prompt_file),
            "-m", self.model,
            "--reasoning-effort", self.effort,
            # `--tools ""` does not remove the CLI's tools. Deny every call, so the desk can
            # only talk, and drop the tools it can drop to shrink the prompt.
            "--deny", "*",
            "--disallowed-tools", CLI_TOOLS,
            "--disable-web-search",
            "--no-subagents",
            "--max-turns", "1",
            "--verbatim",
            "--system-prompt-override", system + "\n\nOutput: fill the JSON. reply is spoken; put each action in its list. You cannot use tools.",
            "--json-schema", json.dumps(CLI_SCHEMA),
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self.scratch),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=tool_env(),
            limit=4 * 1024 * 1024,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ModelError("grok CLI timed out") from exc
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            prompt_file.unlink(missing_ok=True)
        detail = (stderr or stdout).decode("utf-8", "replace").strip()[-300:]
        try:
            outer = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ModelError(f"grok CLI: {detail or 'no reply'}") from exc
        # A failed call still prints JSON: {"type": "error", "message": "...402 ... balance exhausted"}.
        # Read as a reply it was empty, and the desk said "Sorry, say that again?" to everything.
        if not isinstance(outer, dict) or outer.get("type") == "error" or proc.returncode:
            message = outer.get("message") if isinstance(outer, dict) else None
            raise ModelError(f"grok CLI: {message or detail or f'exit {proc.returncode}'}")
        try:
            body = json.loads(outer.get("text") or "")
        except json.JSONDecodeError as exc:
            raise ModelError(f"grok CLI: no reply ({detail or 'empty output'})") from exc
        text = self.render(body) if isinstance(body, dict) else ""
        if not text:
            raise ModelError("grok CLI: empty reply")
        yield text

    async def close(self) -> None:
        return None


ACP_OUTPUT_RULES = """
Output: your spoken reply first, as plain text, then any >>> action lines. You have no tools and
never need them: the Mac work happens through the >>> lines, which the desk runs for you."""
ACP_MAX_TURNS = 40
ACP_MAX_CONTEXT = 150_000
ACP_PRIME_AFTER = 240
ACP_PRIME = "(The user just opened the Agent tab and hasn't said anything yet. Don't act. Reply with only: ok)"


def since_last_reply(messages: list[dict]) -> list[dict]:
    """Desk updates a warm session hasn't seen: everything after its own last reply."""
    earlier = messages[:-1]
    for index in range(len(earlier) - 1, -1, -1):
        msg = earlier[index]
        if msg["role"] == "assistant" and not str(msg["content"]).startswith("[update]"):
            return earlier[index + 1 :]
    return []


def turn_prompt(live: str, messages: list[dict], fresh: bool) -> str:
    """One turn for a warm session: the transcript when the session is new, else only what it
    hasn't seen (desk updates), then the live state and what the user just said."""
    parts = []
    if fresh and len(messages) > 1:
        lines = [f"{'User' if m['role'] == 'user' else 'You'}: {m['content']}" for m in messages[:-1]]
        parts.append("Conversation so far (most recent last):\n" + "\n".join(lines))
    elif not fresh:
        updates = [m["content"] for m in since_last_reply(messages) if m["role"] == "assistant"]
        if updates:
            parts.append("Since your last reply:\n" + "\n".join(updates))
    if live:
        parts.append("Live state right now (answer status questions from this):\n" + live.strip())
    last = messages[-1]["content"] if messages else ""
    parts.append(f"The user just said: {last}")
    return "\n\n".join(parts)


class GrokSessionProvider:
    """the user's Grok subscription, kept warm: one `grok agent stdio` session for the desk.

    The profile holds the static prompt (rules + project list) and removes every tool; the
    process runs in an empty scratch folder and every permission request is refused, so the desk
    can only talk. Each turn sends the live state and what the user said. The session remembers the
    conversation itself, so after the first turn its prompt is cached and the first words come in
    about 2 s. A new session (first turn, profile change, or every 40 turns) gets the recent
    transcript instead.
    """

    name = "grok-session"
    family = "grok"

    def __init__(self, scratch: Path, model: str = DEFAULT_CLI_MODEL, effort: str = "low") -> None:
        self.scratch = scratch.resolve()
        self.work = self.scratch / "cwd"
        self.model = model or DEFAULT_CLI_MODEL
        self.effort = effort or "low"
        self.binary = which("grok")
        self.proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self.turns = 0
        self.stale = False
        self._profile_hash = ""
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._updates: asyncio.Queue | None = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._prompt_id: int | None = None
        self._last_used = 0.0

    @property
    def label(self) -> str:
        return f"{self.model} · Grok subscription"

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    # Process

    def _profile(self, head: str) -> str:
        front = [
            "---",
            "name: orbital-desk",
            "description: the Orbital Agents voice desk. Talks and dispatches; never uses tools.",
            "tools: []",
            "disallowedTools: [" + ", ".join(CLI_TOOLS.split(",")) + "]",
            "agents_md: false",
            "mcpInheritance: none",
            "permission_mode: plan",
            "---",
            "",
        ]
        return "\n".join(front) + head.strip() + "\n" + ACP_OUTPUT_RULES + "\n"

    async def _start(self, head: str) -> None:
        if not self.binary:
            raise ModelError("grok CLI not found")
        profile = self._profile(head)
        digest = hashlib.sha1(profile.encode()).hexdigest()
        if self.running and self.session_id and digest == self._profile_hash and not self.stale and self.turns < ACP_MAX_TURNS:
            return
        await self._stop()
        self.work.mkdir(parents=True, exist_ok=True)
        path = self.scratch / "desk-agent.md"
        path.write_text(profile, encoding="utf-8")
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, "agent", "-m", self.model, "--reasoning-effort", self.effort,
            "--agent-profile", str(path), "--no-leader", "stdio",
            cwd=str(self.work),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=tool_env(),
            limit=8 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read())
        try:
            await self._request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                    "clientInfo": {"name": "orbital-desk", "version": "1"},
                },
                timeout=30,
            )
            result = await self._request("session/new", {"cwd": str(self.work), "mcpServers": []}, timeout=30)
        except Exception:
            await self._stop()
            raise
        self.session_id = str((result or {}).get("sessionId") or "")
        if not self.session_id:
            await self._stop()
            raise ModelError("grok session did not start")
        # `grok agent -m` is ignored: a new session starts on grok-4.7 at xhigh reasoning (4–5 s
        # before the first word). Pick the fast model and low effort for the session itself.
        for config_id, value in (("model", self.model), ("reasoning_effort", self.effort)):
            try:
                await self._request("session/set_config_option", {"sessionId": self.session_id, "configId": config_id, "value": value}, timeout=10)
            except ModelError:
                if config_id == "model":
                    with contextlib.suppress(ModelError):
                        await self._request("session/set_model", {"sessionId": self.session_id, "modelId": value}, timeout=10)
        self._profile_hash = digest
        self.turns = 0
        self.stale = False

    async def _stop(self) -> None:
        proc, self.proc = self.proc, None
        self.session_id = None
        if self._reader:
            self._reader.cancel()
            self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ModelError("grok session closed"))
        self._pending.clear()
        if proc and proc.returncode is None:
            proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)

    async def _send(self, message: dict) -> None:
        if not self.running or not self.proc or not self.proc.stdin:
            raise ModelError("grok session is not running")
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    def _new_request(self, method: str, params: dict) -> tuple[int, asyncio.Future]:
        self._next_id += 1
        ident = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[ident] = fut
        return ident, fut

    async def _request(self, method: str, params: dict, timeout: float) -> dict:
        ident, fut = self._new_request(method, params)
        await self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise ModelError(f"grok session: {method} timed out") from exc
        finally:
            self._pending.pop(ident, None)

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = msg.get("method")
            if method is None and "id" in msg:
                fut = self._pending.get(msg["id"])
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(ModelError(f"grok session: {str(msg['error'])[:200]}"))
                    else:
                        fut.set_result(msg.get("result") or {})
            elif method == "session/update":
                if self._updates is not None:
                    await self._updates.put((msg.get("params") or {}).get("update") or {})
            elif method == "session/request_permission":
                # The desk never acts on the Mac by itself. Workers do that.
                await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}})
            elif "id" in msg:
                await self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not available to the desk"}})
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ModelError("grok session ended"))

    # Turns

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        """The phone opened the desk: start the session and fill Grok's prompt cache with a tiny
        turn, so the user's first words get the ~1.5 s cached reply instead of a ~3.5 s cold one.
        Skipped while the cache is still fresh from recent turns."""
        if not system or self._lock.locked() or time.time() - self._last_used < ACP_PRIME_AFTER:
            return
        self._last_used = time.time()
        with contextlib.suppress(Exception):
            async for _ in self.stream(system, [*(messages or []), {"role": "user", "content": ACP_PRIME}]):
                pass

    @staticmethod
    def _since_last_reply(messages: list[dict]) -> list[dict]:
        return since_last_reply(messages)

    def _prompt(self, live: str, messages: list[dict], fresh: bool) -> str:
        return turn_prompt(live, messages, fresh)

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        head, _, live = system.partition("\nLive state\n")
        async with self._lock:
            async with self._start_lock:
                await self._start(head)
            fresh = self.turns == 0
            text = self._prompt(live, messages, fresh)
            self._updates = asyncio.Queue()
            ident, fut = self._new_request("session/prompt", {})
            self._prompt_id = ident
            ok = False
            try:
                await self._send(
                    {"jsonrpc": "2.0", "id": ident, "method": "session/prompt",
                     "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]}}
                )
                deadline = time.time() + 90
                while True:
                    if fut.done() and self._updates.empty():
                        break
                    if time.time() > deadline:
                        raise ModelError("grok session took too long")
                    try:
                        update = await asyncio.wait_for(self._updates.get(), 0.25)
                    except asyncio.TimeoutError:
                        continue
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        chunk = (update.get("content") or {}).get("text") or ""
                        if chunk:
                            yield chunk
                result = fut.result()
                self.turns += 1
                self._last_used = time.time()
                usage = (result.get("_meta") or {}).get("usage") or {}
                if int(usage.get("inputTokens") or 0) > ACP_MAX_CONTEXT:
                    self.stale = True
                ok = True
            except (asyncio.CancelledError, GeneratorExit):
                # The user cut in: stop the generation so the session is free for their next words.
                self.turns += 1
                with contextlib.suppress(Exception):
                    await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session_id}})
                    await asyncio.wait_for(asyncio.shield(fut), 3)
                ok = True
                raise
            except ModelError:
                raise
            except Exception as exc:
                raise ModelError(f"grok session: {exc}") from exc
            finally:
                self._pending.pop(ident, None)
                self._updates = None
                self._prompt_id = None
                if not ok:
                    # A turn the session didn't finish, or one it never saw: start clean next time.
                    self.stale = True

    async def close(self) -> None:
        await self._stop()


DESK_OUTPUT_RULES = """
Output: your spoken reply first, as plain text, then any >>> action lines, each on its own line.
You have no tools and never need them: the Mac work happens through the >>> lines, which the
desk runs for you."""
# The desk only talks and dispatches, so it gets the fast model; the workers get Opus.
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"


def claude_name(model: str) -> str:
    """'claude-haiku-5-5' → 'Haiku 5.5'; aliases like 'sonnet' → 'Sonnet'."""
    match = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d+))?(?:-\d{8})?", model or "")
    if match:
        return f"{match.group(1).capitalize()} {match.group(2)}{'.' + match.group(3) if match.group(3) else ''}"
    return (model or "Claude").capitalize()
# Outside ~/projects, so no project CLAUDE.md (and its persona) loads into the desk.
CLAUDE_SCRATCH = Path.home() / "Library" / "Caches" / "OrbitalAgents" / "desk-claude"


CLAUDE_AUTH_TTL = 300
_claude_auth: dict = {}


def claude_subscription(binary: str | None = None) -> tuple[bool, str]:
    """Is the claude CLI signed in to the user's Claude subscription? Never run Claude on pay-per-use
    API billing: an Opus agent could cost real money on every job. Checked with
    `claude auth status --json`, at most every 5 minutes. Returns (ok, why-not)."""
    binary = binary or which("claude")
    if not binary:
        return False, "claude CLI not found"
    cached = _claude_auth.get(binary)
    if cached and time.time() - cached[0] < CLAUDE_AUTH_TTL:
        return cached[1]
    try:
        out = subprocess.run([binary, "auth", "status", "--json"], capture_output=True, text=True, timeout=20, env=claude_env(), stdin=subprocess.DEVNULL)
        status = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"claude auth status failed: {exc}"
    method = str(status.get("authMethod") or "").lower()
    plan = str(status.get("subscriptionType") or status.get("subscription") or "").lower()
    if not status.get("loggedIn"):
        result = (False, "claude CLI: not logged in (run claude, then /login)")
    elif plan or any(w in method for w in ("claude.ai", "claudeai", "subscription", "max", "pro")):
        result = (True, "")
    else:
        result = (False, f"claude CLI: signed in for pay-per-use API billing ({method or 'unknown'}), not the subscription")
    _claude_auth[binary] = (time.time(), result)
    return result


def claude_env() -> dict[str, str]:
    """the user's Claude subscription only: never a paid API key, never a parent Claude session."""
    env = tool_env()
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(key, None)
    return env


def user_said(messages: list[dict]) -> str:
    """What The user just said, without the desk's cut-in note."""
    last = str(messages[-1]["content"]) if messages else ""
    return last.split("\n\n(The user ", 1)[0]


class ClaudeRouting:
    """Shared by both Claude desk providers: which model answers this turn (desk_router), the
    climb to a higher tier when one fails, and what the last turn used, for the desk log."""

    router: ClaudeRouter | None
    model: str
    current_model: str
    last_turn: dict | None

    def _routing(self, router: ClaudeRouter | None) -> None:
        self.router = router
        self.current_model = self.model
        self.last_turn = None

    @property
    def label(self) -> str:
        return f"Claude {claude_name(self.current_model)} · Claude subscription"

    def _ladder(self, messages: list[dict]) -> tuple[Route | None, list[tuple[str | None, str]]]:
        said = user_said(messages)
        if not self.router or said in (CLAUDE_PRIME, PROBE_TEXT):
            return None, [(None, self.router.model("haiku") if self.router else self.model)]
        route = self.router.route(said)
        return route, [(tier, self.router.model(tier)) for tier in self.router.ladder(route)]

    async def _climb(self, messages: list[dict], once) -> AsyncIterator[str]:
        """`once(model)` for the routed tier, then the ones above it until one answers. An
        account-wide failure (usage, sign-in) or a reply already half spoken is raised as is."""
        route, ladder = self._ladder(messages)
        self.last_turn = None
        started = time.time()
        failed: list[str] = []
        for tier, model in ladder:
            yielded = False
            usage: dict = {}
            try:
                # Closed here, not left to the garbage collector, so a reply the user cuts off
                # interrupts the session at once.
                async with contextlib.aclosing(once(model, usage)) as replies:
                    async for chunk in replies:
                        yielded = True
                        yield chunk
            except ModelError as exc:
                if yielded or not tier or classify(str(exc)) in FAMILY_WIDE or tier == ladder[-1][0]:
                    raise
                self.router.failed(tier)
                failed.append(tier)
                continue
            self.current_model = model
            if tier and self.router:
                self.router.answered(tier)
            self.last_turn = {
                "tier": tier or "fixed",
                "model": model,
                "reason": route.reason if route else "warm-up",
                **({"routed": route.tier, "climbed_past": failed} if failed and route else {}),
                "tokens": usage,
                "ms": int((time.time() - started) * 1000),
            }
            return


def turn_usage(result: dict) -> dict:
    """Tokens one Claude turn used, from its result line."""
    usage = result.get("usage") or {}
    keys = {"input_tokens": "in", "cache_creation_input_tokens": "cache_write", "cache_read_input_tokens": "cache_read", "output_tokens": "out"}
    out = {short: int(usage.get(key) or 0) for key, short in keys.items()}
    if result.get("total_cost_usd") is not None:
        out["cost_usd"] = round(float(result["total_cost_usd"]), 5)
    return out


class ClaudeCLIProvider(ClaudeRouting):
    """Backup desk on the user's Claude subscription: `claude -p` with no tools, streamed.

    Process start plus the prompt each turn, so the first words take a few seconds. A failed
    call (signed out, usage limit) arrives as a fake assistant message followed by an error
    result, so only real streamed text is passed on before the result says it worked. With a
    router, each turn runs on the model it picks.
    """

    name = "claude-cli"
    family = "claude"

    def __init__(self, scratch: Path = CLAUDE_SCRATCH, model: str = DEFAULT_CLAUDE_MODEL, router: ClaudeRouter | None = None) -> None:
        self.scratch = scratch
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.binary = which("claude")
        self._routing(router)

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        return None

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        async for chunk in self._climb(messages, lambda model, usage: self._once(model, usage, system, messages)):
            yield chunk

    async def _once(self, model: str, usage: dict, system: str, messages: list[dict]) -> AsyncIterator[str]:
        if not self.binary:
            raise ModelError("claude CLI not found")
        ok, why = await asyncio.to_thread(claude_subscription, self.binary)
        if not ok:
            raise ModelError(why)
        self.scratch.mkdir(parents=True, exist_ok=True)
        head, _, live = system.partition("\nLive state\n")
        turn = GrokCLIProvider.transcript(messages)
        if live:
            turn = f"Live state right now (current; answer status questions from this):\n{live}\n\n{turn}"
        args = [
            self.binary, "-p",
            "--model", model,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--system-prompt", head.strip() + "\n" + DESK_OUTPUT_RULES,
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        env = claude_env()
        env["MAX_THINKING_TOKENS"] = "0"  # the desk answers at once; workers do the thinking
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self.scratch),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=8 * 1024 * 1024,
        )
        streamed = False
        held: list[str] = []
        result: dict | None = None
        try:
            assert proc.stdin and proc.stdout
            proc.stdin.write(turn.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            deadline = time.time() + 90
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type")
                if kind == "stream_event":
                    event = msg.get("event") or {}
                    delta = event.get("delta") or {}
                    if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
                        streamed = True
                        yield delta["text"]
                elif kind == "assistant":
                    message = msg.get("message") or {}
                    if message.get("model") == "<synthetic>":
                        continue
                    for block in message.get("content") or []:
                        if block.get("type") == "text" and block.get("text"):
                            held.append(block["text"])
                elif kind == "result":
                    result = msg
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ModelError("claude CLI timed out") from exc
        except (asyncio.CancelledError, GeneratorExit):
            proc.kill()
            raise
        if result is not None and result.get("is_error"):
            raise ModelError(f"claude CLI: {str(result.get('result') or result.get('subtype'))[:300]}")
        if result is None:
            detail = ((await proc.stderr.read()) if proc.stderr else b"").decode("utf-8", "replace").strip()[-300:]
            raise ModelError(f"claude CLI: {detail or f'exit {proc.returncode}'}")
        usage.update(turn_usage(result))
        if not streamed:
            text = "".join(held) or str(result.get("result") or "")
            if not text.strip():
                raise ModelError("claude CLI: empty reply")
            yield text

    async def close(self) -> None:
        return None


PROBE_TEXT = "Reply with just: ok"
CLAUDE_PRIME = "(the user hasn't said anything new yet: you're taking over the desk. Don't act. Reply with only: ok)"


class ClaudeSessionProvider(ClaudeRouting):
    """Claude kept warm while it stands in for Grok: one `claude -p --input-format stream-json`
    process that stays open across turns. No process start per message, the session remembers
    the conversation (each turn sends only what it hasn't seen, the live state, and what the user
    said), and its prompt stays cached. The desk starts it as soon as Grok goes out and closes
    it when Grok is back. A new session (first turn, prompt change, every 40 turns, a failed or
    cut-off turn it couldn't settle) gets the recent transcript instead. With a router, the
    session switches model in place (`set_model`) for each turn, keeping the conversation."""

    name = "claude-session"
    family = "claude"

    def __init__(self, scratch: Path = CLAUDE_SCRATCH, model: str = DEFAULT_CLAUDE_MODEL, router: ClaudeRouter | None = None) -> None:
        self.scratch = scratch
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.binary = which("claude")
        self._routing(router)
        self._session_model = ""
        self._cost_seen = 0.0  # the session reports its cost so far; each turn gets the difference
        self.proc: asyncio.subprocess.Process | None = None
        self.turns = 0
        self.stale = False
        self._digest = ""
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._requests = 0

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _start(self, head: str, model: str) -> None:
        if not self.binary:
            raise ModelError("claude CLI not found")
        ok, why = await asyncio.to_thread(claude_subscription, self.binary)
        if not ok:
            await self._stop()
            raise ModelError(why)
        system = head.strip() + "\n" + DESK_OUTPUT_RULES
        digest = hashlib.sha1(system.encode()).hexdigest()
        if self.running and digest == self._digest and not self.stale and self.turns < ACP_MAX_TURNS:
            if model != self._session_model:
                await self._set_model(model)
            return
        await self._stop()
        self.scratch.mkdir(parents=True, exist_ok=True)
        env = claude_env()
        env["MAX_THINKING_TOKENS"] = "0"
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, "-p",
            "--model", model,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--system-prompt", system,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            cwd=str(self.scratch),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=8 * 1024 * 1024,
        )
        self._digest = digest
        self._session_model = model
        self._cost_seen = 0.0
        self.turns = 0
        self.stale = False

    async def _set_model(self, model: str) -> None:
        """Switch the running session to another model; the conversation stays."""
        self._requests += 1
        request_id = f"model-{self._requests}"
        await self._write({"type": "control_request", "request_id": request_id, "request": {"subtype": "set_model", "model": model}})
        deadline = time.time() + 10
        try:
            while True:
                msg = await self._read(deadline)
                if msg is None:
                    raise ModelError("claude session ended")
                response = msg.get("response") or {}
                if msg.get("type") == "control_response" and response.get("request_id") == request_id:
                    if response.get("subtype") == "error":
                        raise ModelError(f"claude session: couldn't switch to {model}: {str(response.get('error'))[:200]}")
                    break
        except asyncio.TimeoutError as exc:
            raise ModelError("claude session didn't switch model") from exc
        self._session_model = model

    async def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
                await asyncio.wait_for(proc.wait(), 5)

    async def _write(self, message: dict) -> None:
        if not self.running or not self.proc or not self.proc.stdin:
            raise ModelError("claude session is not running")
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _read(self, deadline: float) -> dict | None:
        assert self.proc and self.proc.stdout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=remaining)
            if not line:
                return None
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(line)

    async def _turn(self, text: str, usage: dict | None = None) -> AsyncIterator[str]:
        await self._write({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        streamed = False
        held: list[str] = []
        deadline = time.time() + 90
        while True:
            try:
                msg = await self._read(deadline)
            except asyncio.TimeoutError as exc:
                raise ModelError("claude session took too long") from exc
            if msg is None:
                raise ModelError("claude session ended")
            kind = msg.get("type")
            if kind == "stream_event":
                event = msg.get("event") or {}
                delta = event.get("delta") or {}
                if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
                    streamed = True
                    yield delta["text"]
            elif kind == "assistant":
                message = msg.get("message") or {}
                if message.get("model") != "<synthetic>":
                    held.extend(b["text"] for b in message.get("content") or [] if b.get("type") == "text" and b.get("text"))
            elif kind == "result":
                if msg.get("is_error"):
                    raise ModelError(f"claude session: {str(msg.get('result') or msg.get('subtype'))[:300]}")
                tokens = turn_usage(msg)
                if "cost_usd" in tokens:
                    tokens["cost_usd"], self._cost_seen = round(tokens["cost_usd"] - self._cost_seen, 5), tokens["cost_usd"]
                if usage is not None:
                    usage.update(tokens)
                if tokens["in"] + tokens["cache_write"] + tokens["cache_read"] > ACP_MAX_CONTEXT:
                    self.stale = True
                if not streamed:
                    text = "".join(held) or str(msg.get("result") or "")
                    if text.strip():
                        yield text
                return

    async def _interrupt(self) -> bool:
        """Stop a reply the user talked over and wait for the session to settle. False: start clean."""
        try:
            self._requests += 1
            await self._write({"type": "control_request", "request_id": f"cut-{self._requests}", "request": {"subtype": "interrupt"}})
            deadline = time.time() + 3
            while True:
                msg = await self._read(deadline)
                if msg is None:
                    return False
                if msg.get("type") == "result":
                    return True
        except Exception:
            return False

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        """Start the session and fill its prompt cache with a tiny turn, so the user's next words
        don't wait on a cold start. Skipped while it's fresh from recent turns."""
        if not system or self._lock.locked() or (self.running and time.time() - self._last_used < ACP_PRIME_AFTER):
            return
        self._last_used = time.time()
        with contextlib.suppress(Exception):
            async for _ in self.stream(system, [*(messages or []), {"role": "user", "content": CLAUDE_PRIME}]):
                pass

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        async with self._lock:
            async for chunk in self._climb(messages, lambda model, usage: self._once(model, usage, system, messages)):
                yield chunk

    async def _once(self, model: str, usage: dict, system: str, messages: list[dict]) -> AsyncIterator[str]:
        head, _, live = system.partition("\nLive state\n")
        try:
            await self._start(head, model)
        except ModelError:
            self.stale = True
            raise
        text = turn_prompt(live, messages, self.turns == 0)
        ok = False
        try:
            async for chunk in self._turn(text, usage):
                yield chunk
            self.turns += 1
            self._last_used = time.time()
            ok = True
        except (asyncio.CancelledError, GeneratorExit):
            self.turns += 1
            ok = await self._interrupt()
            raise
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"claude session: {exc}") from exc
        finally:
            if not ok:
                self.stale = True

    async def close(self) -> None:
        await self._stop()


ONDEVICE_SOURCE = SERVER_DIR / "ondevice" / "desk_fm.swift"
ONDEVICE_ACTIONS = {"task", "todo", "reminder"}
ONDEVICE_RULES = """You are the user's backup front-desk assistant in their Orbital Agents app. The user talks to you by voice from their iPhone, often while driving or walking. You are running on their Mac's built-in Apple model because {situation}.

Answer what The user just said in one or two short, plain sentences that will be read aloud.
You can't look anything up, read files, or do work yourself. Agents can't run until {engines} is back; work the user asks for is saved and starts by itself then, so say that.
Never say work is finished unless the status below says so. Never make up results.
Only fill todos, reminders or work when the user explicitly asks for them in their latest message.

Now: {now}.
Status, for answering status questions only (these are not requests):
{status}"""
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "can", "will", "please",
    "just", "about", "make", "have", "need", "want", "remind", "reminder", "todo", "tomorrow", "today",
}


def _words_of(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


class OnDeviceProvider:
    """Last resort: Apple's on-device model on the Mac. Small (4K context), so it gets its own
    short prompt and only the last few turns. Compiled from orbital/hub/ondevice/desk_fm.swift on
    first use; the reply arrives in one piece, in about 2–4 s."""

    name = "ondevice"
    family = "ondevice"
    label = "Apple on-device model · Mac"
    allowed_actions = ONDEVICE_ACTIONS

    def __init__(self, build_dir: Path, source: Path = ONDEVICE_SOURCE) -> None:
        self.build_dir = build_dir
        self.source = source
        self.situation = "Grok and Claude can't answer right now"
        self.engines = "Grok or Claude"
        self._build_lock = asyncio.Lock()

    def _binary_path(self) -> Path:
        digest = hashlib.sha1(self.source.read_bytes()).hexdigest()[:10] if self.source.is_file() else "missing"
        return self.build_dir / f"desk-fm-{digest}"

    async def ensure_built(self) -> Path:
        async with self._build_lock:
            if not self.source.is_file():
                raise ModelError("on-device helper source not found")
            binary = self._binary_path()
            if binary.is_file():
                return binary
            xcrun = shutil.which("xcrun") or "/usr/bin/xcrun"
            self.build_dir.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                xcrun, "swiftc", "-O", "-parse-as-library", str(self.source), "-o", str(binary),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=tool_env(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 300)
            if proc.returncode or not binary.is_file():
                raise ModelError(f"on-device helper didn't build: {out.decode('utf-8', 'replace')[-300:]}")
            for old in self.build_dir.glob("desk-fm-*"):
                if old != binary:
                    old.unlink(missing_ok=True)
            return binary

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        return None

    @staticmethod
    def render(result: dict, said: str) -> str:
        """The typed fields back into the reply + `>>>` lines the desk parses. The small model
        sometimes turns a status line into a request, so an item is kept only when it shares a
        word with what The user just said."""
        heard = _words_of(said)
        grounded = lambda text: bool(_words_of(text) & heard)  # noqa: E731
        out = [str(result.get("reply") or "").strip()]
        for title in result.get("todos") or []:
            if isinstance(title, str) and title.strip() and grounded(title):
                out.append(">>> " + json.dumps({"type": "todo", "title": title.strip()}, ensure_ascii=False))
        for item in result.get("reminders") or []:
            if isinstance(item, dict) and item.get("title") and grounded(item["title"]):
                out.append(">>> " + json.dumps({"type": "reminder", "title": item["title"], "due": item.get("due")}, ensure_ascii=False))
        for item in result.get("work") or []:
            if isinstance(item, dict) and item.get("project") and grounded(f"{item.get('title')} {item.get('request')} {item['project']}"):
                task = {"type": "task", "project": item["project"], "title": item.get("title") or "Saved job", "brief": item.get("request") or said, "effort": "high"}
                out.append(">>> " + json.dumps(task, ensure_ascii=False))
        return "\n".join(line for line in out if line)

    def _prompt(self, system: str, messages: list[dict]) -> tuple[str, str]:
        _, _, live = system.partition("\nLive state\n")
        lines = [line for line in live.strip().splitlines() if line.strip()]
        now = lines[0][len("Now: "):] if lines and lines[0].startswith("Now: ") else datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M")
        status = "\n".join(lines[1:])[:1200] or "Nothing running."
        instructions = ONDEVICE_RULES.format(situation=self.situation, engines=self.engines, now=now, status=status)
        recent = []
        for msg in messages[-7:-1]:
            who = "User" if msg["role"] == "user" else "You"
            recent.append(f"{who}: {str(msg['content'])[:300]}")
        last = str(messages[-1]["content"])[:1500] if messages else ""
        prompt = (("Conversation so far:\n" + "\n".join(recent) + "\n\n") if recent else "") + f"The user just said: {last}"
        return instructions, prompt

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        binary = await self.ensure_built()
        instructions, prompt = self._prompt(system, messages)
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=tool_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps({"instructions": instructions, "prompt": prompt}).encode("utf-8")), 60
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ModelError("on-device model timed out") from exc
        except asyncio.CancelledError:
            proc.kill()
            raise
        result, error = None, None
        for line in stdout.decode("utf-8", "replace").splitlines():
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                msg = json.loads(line)
                result = msg.get("result") if isinstance(msg.get("result"), dict) else result
                error = msg.get("error") or error
        if error or proc.returncode or result is None:
            raise ModelError(error or stderr.decode("utf-8", "replace")[-300:] or f"on-device model exit {proc.returncode}")
        said = str(messages[-1]["content"]) if messages else ""
        if not str(result.get("reply") or "").strip():
            raise ModelError("on-device model: empty reply")
        yield self.render(result, said)

    async def close(self) -> None:
        return None


def build_backups(primary: Any, data_dir: Path, claude_model: str | None = None, claude_route: str | None = "auto") -> list:
    """Who answers when the primary can't, in order. Grok's one-shot CLI only backs up Grok.
    Claude picks Haiku, Sonnet or Opus per turn (desk_router); `claude_route` pins one."""
    desk_dir = data_dir / "desk"
    backups: list = []
    if isinstance(primary, (XAIProvider, GrokSessionProvider)):
        backups.append(GrokCLIProvider(desk_dir / "cli-scratch"))
    router = ClaudeRouter(claude_route, claude_model)
    model = router.model("haiku")
    # Claude warm first; its one-shot is the fallback and the one that probes a down Claude.
    backups.append(ClaudeSessionProvider(model=model, router=router))
    backups.append(ClaudeCLIProvider(model=model, router=router))
    backups.append(OnDeviceProvider(desk_dir / "ondevice"))
    return backups


def build_provider(settings: dict, data_dir: Path):
    key = settings.get("xai_api_key") or ""
    if key:
        return XAIProvider(key, settings.get("desk_model") or DEFAULT_XAI_MODEL, settings.get("desk_effort") or "")
    return GrokSessionProvider(data_dir / "desk" / "session", settings.get("desk_cli_model") or DEFAULT_CLI_MODEL)


class FallbackProvider:
    """The Agent tab's model: the subscription engines in order, the way Orbital Classic ran them.

    Grok subscription (warm `grok agent stdio`, then the one-shot CLI), the xAI API only when a key is
    set, then Claude subscription (warm, then one-shot), then the Mac's built-in model. An engine that
    fails is marked down in EngineHealth and skipped until it answers again. Once an engine has spoken,
    a later failure ends the reply there instead of starting over on another engine.
    """

    name = "subscription"

    def __init__(self, primary: Any, backups: list, health: EngineHealth, work_note=None) -> None:
        self.primary = primary
        self.backups = backups
        self.health = health
        self.work_note = work_note or (lambda: "")
        self.answering: Any = None

    @property
    def family(self) -> str:
        return family_of(self.answering or self.primary)

    @property
    def label(self) -> str:
        current = self.answering or next((p for p in self.everyone if self.health.ok(p)), self.primary)
        return getattr(current, "label", current.name)

    @property
    def everyone(self) -> list:
        return [self.primary, *self.backups]

    def chain(self) -> list:
        healthy = [p for p in self.everyone if self.health.ok(p)]
        return healthy + [p for p in self.everyone if p not in healthy]

    def situation(self) -> str:
        down = [f for f in self.health.families if f != "ondevice"]
        reasons = [self.health.reason(f) for f in down] or [f"{FAMILY_NAMES.get(family_of(self.primary), 'Grok')} isn't answering"]
        return " and ".join(reasons)

    def _system_for(self, provider: Any, system: str) -> str:
        if family_of(provider) == family_of(self.primary):
            return system
        if isinstance(provider, OnDeviceProvider):
            provider.situation = self.situation()
            names = [FAMILY_NAMES.get(f, f) for f in ("grok", "claude") if self.health.family_down(f)]
            provider.engines = " or ".join(names) or "Grok"
        note = (f"(Standing in: {self.situation()}. You are answering at the desk in Grok's place, with the same job and rules. "
                f"{self.work_note()} If the user asks what's wrong, tell them plainly.)\n\n")
        return note + system

    def public(self) -> list[dict]:
        return self.health.public()

    async def warm(self, system: str | None = None, messages: list[dict] | None = None) -> None:
        for provider in self.chain():
            if self.health.ok(provider):
                with contextlib.suppress(Exception):
                    await provider.warm(system, messages)
                return

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        out: set[str] = set()
        last_error = "no engine could answer"
        for provider in self.chain():
            if family_of(provider) in out:
                continue
            self.answering = provider
            spoke = False
            try:
                async for chunk in provider.stream(self._system_for(provider, system), messages):
                    if chunk:
                        spoke = spoke or bool(chunk.strip())
                        yield chunk
                if not spoke:
                    raise ModelError("empty reply")
                self.health.succeed(provider)
                return
            except Exception as exc:  # CancelledError (the user cut in) is not an Exception
                last_error = str(exc) or type(exc).__name__
            kind = self.health.fail(provider, last_error)
            if kind in FAMILY_WIDE:
                out.add(family_of(provider))
            if spoke:
                return
        raise ModelError(last_error)

    async def close(self) -> None:
        for provider in self.everyone:
            with contextlib.suppress(Exception):
                await provider.close()


CHAT_ENGINES = ("auto", "grok", "claude", "xai", "ondevice")
CHAT_MODELS = {
    "grok": [DEFAULT_CLI_MODEL, "grok-4.7"],
    "claude": ["auto", "haiku", "sonnet", "opus"],
    "xai": [DEFAULT_XAI_MODEL],
    "ondevice": [],
}


def build_fallback(xai_key: str, data_dir: Path, *, chat: str = "auto", model: str = "", cli_model: str = "",
                   claude_model: str | None = None, claude_route: str | None = "auto", work_note=None) -> FallbackProvider:
    """The Agent tab's engines, the chosen chat AI first and the rest as backups.

    chat: auto (the xAI API with a key, else the Grok subscription), grok, claude, xai or ondevice.
    model: that engine's model (grok CLI model; claude auto/haiku/sonnet/opus; xAI API model)."""
    desk_dir = data_dir / "desk"
    chat = chat if chat in CHAT_ENGINES else "auto"
    if chat == "auto":
        chat = "xai" if xai_key else "grok"
    if chat == "xai" and not xai_key:
        chat = "grok"
    route = model if chat == "claude" and model in ("auto", "haiku", "sonnet", "opus") else claude_route
    router = ClaudeRouter(route, claude_model)
    claude_quick = router.model("haiku")
    grok_model = (model if chat == "grok" and model else "") or cli_model or DEFAULT_CLI_MODEL
    grok = [GrokSessionProvider(desk_dir / "session", grok_model), GrokCLIProvider(desk_dir / "cli-scratch", grok_model)]
    claude = [ClaudeSessionProvider(model=claude_quick, router=router), ClaudeCLIProvider(model=claude_quick, router=router)]
    ondevice = [OnDeviceProvider(desk_dir / "ondevice")]
    if chat == "xai":
        order = [XAIProvider(xai_key, model or DEFAULT_XAI_MODEL), *grok, *claude, *ondevice]
    elif chat == "claude":
        order = [*claude, *grok, *ondevice]
    elif chat == "ondevice":
        order = [*ondevice, *grok, *claude]
    else:
        order = [*grok, *claude, *ondevice]
    health = EngineHealth(desk_dir / "engines.json")
    return FallbackProvider(order[0], order[1:], health, work_note)
