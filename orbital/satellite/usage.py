"""Measuring what an agent run costs: tokens, and how much of the subscription it used.

Agents can't see their own token counts, so the satellite that runs them measures:

- Grok: the grok CLI logs every model call to ~/.grok/logs/unified.jsonl
  (`shell.turn.inference_done`: prompt, cached, completion and reasoning tokens, by process id) and
  the plan's state every 30 s while it runs (`billing: fetched credits config`: creditUsagePercent,
  the period and when it resets). A run's tokens are the calls its process tree made after it
  started; its plan share is how far creditUsagePercent moved meanwhile (other Grok work at the same
  time counts too, so it is an upper bound).
- Claude: `claude -p` runs get a known `--session-id`, so their transcript
  (~/.claude/projects/*/<id>.jsonl) is found exactly; tokens are summed per message.
- Others (codex, gemini, custom): no usage yet.

Usage dicts use the tracker's keys (camelCase), so they go to the tracker item as they are.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

GROK_LOG = Path.home() / ".grok" / "logs" / "unified.jsonl"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
BILLING_LOOKBACK = 4 * 1024 * 1024
MAX_TREE = 200  # processes followed under one agent run


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def engine_of(executable: str | None) -> str | None:
    name = Path(executable or "").name.lower()
    for engine in ("grok", "claude", "codex", "gemini"):
        if name.startswith(engine):
            return engine
    return None


def prepare_argv(argv: list[str]) -> tuple[list[str], str | None]:
    """`claude -p` runs get a session id we choose, so the transcript can be found afterwards."""
    if engine_of(argv[0] if argv else None) != "claude":
        return argv, None
    if not any(a in ("-p", "--print") for a in argv) or any(a.startswith(("--session-id", "--resume", "-r", "--continue")) for a in argv[1:]):
        return argv, None
    session = str(uuid.uuid4())
    return [argv[0], "--session-id", session, *argv[1:]], session


def descendants(pid: int) -> set[int]:
    """pid and every process under it (POSIX); just pid elsewhere."""
    found = {pid}
    if sys.platform == "win32" or pid <= 1:
        return found
    frontier = [pid]
    while frontier and len(found) < MAX_TREE:
        parent = frontier.pop()
        try:
            out = subprocess.run(["pgrep", "-P", str(parent)], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            break
        for line in out.split():
            if line.isdigit() and int(line) not in found:
                found.add(int(line))
                frontier.append(int(line))
    return found


def _billing(line: dict) -> dict | None:
    if line.get("msg") != "billing: fetched credits config":
        return None
    ctx = line.get("ctx") or {}
    config = ctx.get("config") or {}
    pct = config.get("creditUsagePercent")
    if not isinstance(pct, (int, float)):
        return None
    period = (config.get("currentPeriod") or {}).get("type") or ""
    return {
        "percent": float(pct),
        "period": "weekly" if "WEEK" in period else "monthly" if "MONTH" in period else (period.lower() or None),
        "resets_at": (config.get("currentPeriod") or {}).get("end") or config.get("billingPeriodEnd"),
        "tier": ctx.get("subscriptionTier"),
    }


def grok_plan(log: Path = GROK_LOG, before: int | None = None) -> dict | None:
    """The grok plan's latest known state (optionally as of a byte offset in the log)."""
    try:
        size = log.stat().st_size
    except OSError:
        return None
    end = size if before is None else min(before, size)
    start = max(0, end - BILLING_LOOKBACK)
    try:
        with log.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(end - start).decode("utf-8", "replace")
    except OSError:
        return None
    for raw in reversed(chunk.splitlines()):
        if "credits config" not in raw:
            continue
        try:
            found = _billing(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if found:
            return found
    return None


class UsageMeter:
    """Follows one agent run. snapshot() returns the running totals as a tracker usage dict, or None."""

    def __init__(self, executable: str | None, pid: int, started: float, session: str | None = None,
                 grok_log: Path = GROK_LOG, claude_projects: Path = CLAUDE_PROJECTS) -> None:
        self.engine = engine_of(executable)
        self.pid = pid
        self.started = started
        self.session = session
        self.grok_log = grok_log
        self.claude_projects = claude_projects
        self.pids: set[int] = {pid}
        self._grok_offset = 0
        self._grok_totals = {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "calls": 0}
        self._grok_plan_start: dict | None = None
        self._grok_plan_now: dict | None = None
        if self.engine == "grok":
            try:
                self._grok_offset = grok_log.stat().st_size
            except OSError:
                self._grok_offset = 0
            self._grok_plan_start = grok_plan(grok_log, before=self._grok_offset)

    # Grok

    def _read_grok(self) -> None:
        self.pids |= descendants(self.pid)
        try:
            with self.grok_log.open("rb") as handle:
                handle.seek(self._grok_offset)
                data = handle.read()
        except OSError:
            return
        cut = data.rfind(b"\n")
        if cut < 0:
            return
        self._grok_offset += cut + 1
        for raw in data[: cut + 1].decode("utf-8", "replace").splitlines():
            if "inference_done" not in raw and "credits config" not in raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            plan = _billing(line)
            if plan:
                self._grok_plan_now = plan
                continue
            if line.get("msg") != "shell.turn.inference_done" or line.get("pid") not in self.pids:
                continue
            ctx = line.get("ctx") or {}
            prompt = int(ctx.get("prompt_tokens") or 0)
            cached = int(ctx.get("cached_prompt_tokens") or 0)
            self._grok_totals["input"] += max(0, prompt - cached)
            self._grok_totals["cached"] += cached
            self._grok_totals["output"] += int(ctx.get("completion_tokens") or 0)
            self._grok_totals["reasoning"] += int(ctx.get("reasoning_tokens") or 0)
            self._grok_totals["calls"] += 1

    def _grok_usage(self) -> dict | None:
        self._read_grok()
        t = self._grok_totals
        if not t["calls"] and not self._grok_plan_now:
            return None
        out: dict[str, Any] = {"engine": "grok", "inputTokens": t["input"], "cachedTokens": t["cached"],
                               "outputTokens": t["output"], "reasoningTokens": t["reasoning"]}
        now = self._grok_plan_now or self._grok_plan_start
        if now:
            out["planUsedPercent"] = round(now["percent"], 2)
            out["planPeriod"] = now.get("period")
            if now.get("resets_at"):
                out["planResetsAt"] = str(now["resets_at"])[:19] + "Z"
            if self._grok_plan_start:
                out["planPercent"] = round(max(0.0, now["percent"] - self._grok_plan_start["percent"]), 2)
        return out

    # Claude

    def _claude_usage(self) -> dict | None:
        if not self.session:
            return None
        files = glob.glob(str(self.claude_projects / "*" / f"{self.session}.jsonl"))
        if not files:
            return None
        per_message: dict[str, dict] = {}
        model = None
        try:
            with open(files[0], encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    if '"usage"' not in raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    message = row.get("message") or {}
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    model = message.get("model") or model
                    per_message[message.get("id") or row.get("uuid") or str(len(per_message))] = usage
        except OSError:
            return None
        if not per_message:
            return None
        total = {"in": 0, "cached": 0, "out": 0}
        for usage in per_message.values():
            total["in"] += int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
            total["cached"] += int(usage.get("cache_read_input_tokens") or 0)
            total["out"] += int(usage.get("output_tokens") or 0)
        return {"engine": "claude", "model": model, "inputTokens": total["in"], "cachedTokens": total["cached"],
                "outputTokens": total["out"]}

    def snapshot(self) -> dict | None:
        try:
            usage = self._grok_usage() if self.engine == "grok" else self._claude_usage() if self.engine == "claude" else None
        except Exception:  # measuring must never break a job
            return None
        if usage:
            usage["measuredBy"] = "satellite"
            usage["updatedAt"] = _iso(time.time())
        return usage


class TreeUsageMeter:
    """A scheduled script that may start agents itself: Grok calls from its process tree, plus
    non-interactive Claude runs (`claude -p`, transcript entrypoint "sdk-cli") that began while it ran.
    The Claude part is an estimate: two such scripts at the same time share it."""

    def __init__(self, pid: int, started: float, grok_log: Path = GROK_LOG, claude_projects: Path = CLAUDE_PROJECTS) -> None:
        self.grok = UsageMeter("grok", pid, started, grok_log=grok_log)
        self.started = started
        self.claude_projects = claude_projects

    def _claude_sessions(self) -> list[str]:
        out = []
        for path in glob.glob(str(self.claude_projects / "*" / "*.jsonl")):
            try:
                if os.path.getmtime(path) < self.started:
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    head = handle.read(20000)
            except OSError:
                continue
            if '"entrypoint":"sdk-cli"' not in head:
                continue
            first = next((json.loads(line) for line in head.splitlines() if '"timestamp"' in line), None)
            stamp = (first or {}).get("timestamp") or ""
            try:
                began = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            except ValueError:
                continue
            if began >= self.started - 2:
                out.append(Path(path).stem)
        return out

    def snapshot(self) -> dict | None:
        try:
            grok = self.grok._grok_usage() if self.grok.engine else None
            if grok and not self.grok._grok_totals["calls"]:
                grok = None
            claude = None
            for session in self._claude_sessions():
                part = UsageMeter("claude", 0, self.started, session, claude_projects=self.claude_projects)._claude_usage()
                if not part:
                    continue
                if claude is None:
                    claude = dict(part)
                else:
                    for key in ("inputTokens", "cachedTokens", "outputTokens"):
                        claude[key] = claude.get(key, 0) + part.get(key, 0)
        except Exception:
            return None
        if not grok and not claude:
            return None
        if grok and claude:
            out = dict(grok)
            for key in ("inputTokens", "cachedTokens", "outputTokens"):
                out[key] = out.get(key, 0) + claude.get(key, 0)
            out["engine"] = "grok+claude"
        else:
            out = dict(grok or claude)
        out["measuredBy"] = "scheduler" if not claude else "scheduler-estimate"
        out["updatedAt"] = _iso(time.time())
        return out
