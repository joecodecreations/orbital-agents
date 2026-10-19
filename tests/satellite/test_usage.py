"""Measuring an agent run's tokens and plan share from the CLIs' own logs."""

from __future__ import annotations

import json
from pathlib import Path

from orbital.satellite.usage import UsageMeter, engine_of, prepare_argv


def billing(pct: float) -> dict:
    return {"msg": "billing: fetched credits config", "pid": 1, "ctx": {"subscriptionTier": "Plan", "config": {
        "creditUsagePercent": pct, "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "end": "2026-10-23T14:24:32+00:00"}}}}


def inference(pid: int, prompt: int, cached: int, out: int, reasoning: int = 0) -> dict:
    return {"msg": "shell.turn.inference_done", "pid": pid, "ctx": {
        "prompt_tokens": prompt, "cached_prompt_tokens": cached, "completion_tokens": out, "reasoning_tokens": reasoning}}


def append(log: Path, *rows: dict) -> None:
    with log.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_grok_run_counts_only_its_own_process_after_it_started(tmp_path, monkeypatch):
    log = tmp_path / "unified.jsonl"
    append(log, billing(10.0), inference(4242, 999, 0, 999))  # before the run: not counted
    monkeypatch.setattr("orbital.satellite.usage.descendants", lambda pid: {pid, 4243})
    meter = UsageMeter("/usr/local/bin/grok", 4242, 0.0, grok_log=log)
    append(log, inference(4242, 1000, 200, 50, 10), inference(4243, 500, 0, 20), inference(7777, 9000, 0, 9000), billing(12.5))
    usage = meter.snapshot()
    assert usage["engine"] == "grok" and usage["measuredBy"] == "satellite"
    assert usage["inputTokens"] == 800 + 500 and usage["cachedTokens"] == 200
    assert usage["outputTokens"] == 70 and usage["reasoningTokens"] == 10
    assert usage["planUsedPercent"] == 12.5 and usage["planPercent"] == 2.5 and usage["planPeriod"] == "weekly"
    append(log, inference(4242, 100, 0, 5))
    assert meter.snapshot()["outputTokens"] == 75, "totals keep running"


def test_claude_run_reads_its_own_transcript(tmp_path):
    argv, session = prepare_argv(["/opt/bin/claude", "-p", "do it"])
    assert argv[:3] == ["/opt/bin/claude", "--session-id", session] and argv[-2:] == ["-p", "do it"]
    folder = tmp_path / "projects" / "-Users-me-code-site"
    folder.mkdir(parents=True)
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 90, "cache_read_input_tokens": 400, "output_tokens": 30}
    rows = [
        {"message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}},
        {"message": {"id": "m1", "model": "claude-opus-5-5", "usage": usage}},  # the same message streamed twice
        {"message": {"id": "m2", "usage": {"input_tokens": 5, "output_tokens": 7}}},
    ]
    (folder / f"{session}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    meter = UsageMeter(argv[0], 1, 0.0, session, claude_projects=tmp_path / "projects")
    got = meter.snapshot()
    assert got["engine"] == "claude" and got["model"] == "claude-opus-5-5"
    assert got["inputTokens"] == 100 + 5 and got["cachedTokens"] == 400 and got["outputTokens"] == 37


def test_other_agents_and_resumed_claude_are_left_alone(tmp_path):
    assert prepare_argv(["codex", "exec", "x"]) == (["codex", "exec", "x"], None)
    assert prepare_argv(["claude", "--resume", "abc", "-p", "x"])[1] is None
    assert engine_of("C:/tools/gemini.cmd") == "gemini"
    assert UsageMeter("codex", 1, 0.0).snapshot() is None
    assert UsageMeter("grok", 1, 0.0, grok_log=tmp_path / "missing.jsonl").snapshot() is None
