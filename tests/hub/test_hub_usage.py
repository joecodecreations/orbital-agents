"""The usage summary: top consumers and per-automation averages."""

from __future__ import annotations

from orbital.hub.usage import summary

NOW = 1_800_000_000.0


def iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_summary_ranks_by_plan_share_and_averages_agent_automations(monkeypatch):
    monkeypatch.setattr("orbital.hub.usage.plans", lambda: [{"engine": "grok", "used_percent": 13.0}])
    items = [
        {"id": "a", "title": "Small fix", "status": "done", "completedAt": iso(NOW - 3600),
         "usage": {"engine": "claude", "inputTokens": 1000, "outputTokens": 200}},
        {"id": "b", "title": "Big build", "status": "active", "progress": 27, "updatedAt": iso(NOW - 60),
         "usage": {"engine": "grok", "inputTokens": 50000, "outputTokens": 9000, "reasoningTokens": 1000, "planPercent": 4.5}},
        {"id": "c", "title": "Old", "status": "done", "completedAt": iso(NOW - 10 * 86400),
         "usage": {"engine": "grok", "inputTokens": 10, "outputTokens": 10, "planPercent": 9.0}},
        {"id": "d", "title": "No usage", "status": "done"},
    ]
    jobs = {"jobs": [
        {"id": "loop", "name": "Marketing loop", "kind": "agent",
         "usage_total": {"runs": 4, "inputTokens": 40000, "outputTokens": 4000, "planPercent": 2.0, "engine": "claude"}},
        {"id": "weather", "name": "Weather", "kind": "automation"},
    ]}
    out = summary(items, jobs, now=NOW)
    assert out["plans"][0]["used_percent"] == 13.0
    assert [row["id"] for row in out["top"]][:2] == ["c", "b"], "biggest share of the plan first"
    assert out["totals"]["tokens_today"] == 1200 + 60000
    assert out["totals"]["plan_percent_week"] == 4.5
    assert out["totals"]["items_measured"] == 3
    loop = out["automations"][0]
    assert loop["id"] == "loop" and loop["avg_tokens"] == 11000 and loop["avg_plan_percent"] == 0.5
    assert out["automations"][1] == {**out["automations"][1], "kind": "automation", "runs": 0, "avg_tokens": None}
