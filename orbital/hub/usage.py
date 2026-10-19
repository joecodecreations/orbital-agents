"""What the AI work costs, in one place: subscription plans, and the work that used the most.

    GET /v1/usage → {
      "plans":       [{engine, tier, used_percent, period, resets_at, updated_at}],   Grok's plan, read from its CLI log
      "top":         [{kind: "item"|"automation", id, title, agent, machine, status, progress, tokens, plan_percent, ...}],
      "automations": [{id, name, kind: "agent"|"automation", runs, tokens, avg_tokens, plan_percent, avg_plan_percent, last}],
      "totals":      {tokens_today, tokens_week, plan_percent_week, items_measured}
    }

Tokens come from the tracker items (measured by the satellite that ran the agent, or reported by
the agent) and from the scheduler's agent jobs. Nothing here calls a model.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from orbital.satellite.usage import grok_plan


def _epoch(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tokens(usage: dict | None) -> int:
    usage = usage or {}
    return sum(int(usage.get(k) or 0) for k in ("inputTokens", "outputTokens", "reasoningTokens"))


def plans() -> list[dict]:
    out = []
    grok = grok_plan()
    if grok:
        out.append({"engine": "grok", "tier": grok.get("tier"), "used_percent": round(grok["percent"], 1),
                    "period": grok.get("period"), "resets_at": grok.get("resets_at")})
    return out


def summary(tracker_items: list[dict], jobs_snapshot: dict | None, now: float | None = None) -> dict:
    now = now or time.time()
    day, week = now - 86400, now - 7 * 86400
    top: list[dict] = []
    tokens_today = tokens_week = 0
    plan_week = 0.0
    measured = 0
    for item in tracker_items:
        usage = item.get("usage")
        if not isinstance(usage, dict):
            continue
        measured += 1
        tokens = _tokens(usage)
        stamp = _epoch(item.get("completedAt")) or _epoch(item.get("updatedAt")) or now
        if stamp >= day:
            tokens_today += tokens
        if stamp >= week:
            tokens_week += tokens
            plan_week += float(usage.get("planPercent") or 0)
        top.append({
            "kind": "item", "id": item.get("id"), "title": item.get("title"), "agent": item.get("agent"),
            "machine": item.get("machine"), "status": item.get("status"), "progress": item.get("progress"),
            "tokens": tokens, "plan_percent": usage.get("planPercent"), "engine": usage.get("engine"),
            "at": item.get("completedAt") or item.get("updatedAt"),
        })
    automations = []
    for job in (jobs_snapshot or {}).get("jobs") or []:
        totals = job.get("usage_total") or {}
        runs = int(totals.get("runs") or 0)
        tokens = sum(int(totals.get(k) or 0) for k in ("inputTokens", "outputTokens", "reasoningTokens"))
        row = {
            "id": job.get("id"), "name": job.get("name"), "kind": job.get("kind") or "automation",
            "runs": runs, "tokens": tokens, "avg_tokens": round(tokens / runs) if runs else None,
            "plan_percent": totals.get("planPercent"),
            "avg_plan_percent": round(float(totals.get("planPercent") or 0) / runs, 2) if runs else None,
            "engine": totals.get("engine"), "since": totals.get("since"),
            "last": (job.get("last") or {}).get("usage"),
        }
        automations.append(row)
        if runs:
            top.append({"kind": "automation", "id": row["id"], "title": row["name"], "agent": row["engine"],
                        "tokens": tokens, "plan_percent": row["plan_percent"], "runs": runs, "at": totals.get("since")})
    top.sort(key=lambda r: (float(r.get("plan_percent") or 0), r["tokens"]), reverse=True)
    automations.sort(key=lambda r: (r["kind"] != "agent", -(r["tokens"] or 0), str(r["name"]).lower()))
    return {
        "plans": plans(),
        "top": top[:25],
        "automations": automations,
        "totals": {"tokens_today": tokens_today, "tokens_week": tokens_week,
                   "plan_percent_week": round(plan_week, 2), "items_measured": measured},
    }


def add_routes(app, hub) -> None:
    @app.get("/v1/usage")
    async def usage(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        try:
            jobs = hub.jobs.snapshot()
        except Exception:
            jobs = None
        return JSONResponse(summary(hub.tracker.items(), jobs))
