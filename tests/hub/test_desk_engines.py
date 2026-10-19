"""The Agent tab's engine chain: subscriptions first, a down engine skipped, no restart mid-reply."""

from __future__ import annotations

import asyncio

from orbital.hub.desk_engines import EngineHealth, FallbackProvider
from orbital.hub.desk_model import ModelError


class Fake:
    def __init__(self, name: str, family: str, chunks=None, error: str | None = None, fail_after: int | None = None):
        self.name, self.family, self.label = name, family, name
        self.chunks, self.error, self.fail_after = chunks or [], error, fail_after
        self.calls = 0

    async def warm(self, *args):
        return None

    async def stream(self, system, messages):
        self.calls += 1
        for i, chunk in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise ModelError(self.error or "boom")
            yield chunk
        if self.error and self.fail_after is None:
            raise ModelError(self.error)

    async def close(self):
        return None


def run(provider) -> str:
    async def go():
        return "".join([c async for c in provider.stream("system", [{"role": "user", "content": "hi"}])])
    return asyncio.run(go())


def test_grok_out_of_credits_falls_back_to_claude_and_is_skipped_next_time(tmp_path):
    grok = Fake("grok-session", "grok", error="402 Payment Required: usage balance exhausted")
    grok_cli = Fake("grok-cli", "grok", chunks=["never"])
    claude = Fake("claude-session", "claude", chunks=["On it."])
    chain = FallbackProvider(grok, [grok_cli, claude], EngineHealth(tmp_path / "engines.json"))
    assert run(chain) == "On it."
    assert grok_cli.calls == 0, "a family-wide failure (quota) skips the rest of that family"
    assert run(chain) == "On it."
    assert grok.calls == 1, "a down engine is not retried on the next turn"
    assert chain.label == "claude-session"


def test_an_engine_that_already_spoke_is_not_replaced_mid_reply(tmp_path):
    grok = Fake("grok-session", "grok", chunks=["Starting the ", "job", "!"], error="pipe closed", fail_after=2)
    claude = Fake("claude-session", "claude", chunks=["Different answer"])
    chain = FallbackProvider(grok, [claude], EngineHealth(tmp_path / "engines.json"))
    assert run(chain) == "Starting the job"
    assert claude.calls == 0


def test_an_empty_reply_counts_as_a_failure(tmp_path):
    grok = Fake("grok-session", "grok", chunks=["  "])
    claude = Fake("claude-session", "claude", chunks=["Here."])
    assert run(FallbackProvider(grok, [claude], EngineHealth(tmp_path / "engines.json"))) == "  Here."


def test_every_engine_failing_raises(tmp_path):
    chain = FallbackProvider(Fake("a", "grok", error="x"), [Fake("b", "claude", error="y")], EngineHealth(tmp_path / "e.json"))
    try:
        run(chain)
    except ModelError:
        return
    raise AssertionError("expected ModelError")
