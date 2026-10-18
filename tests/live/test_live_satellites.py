"""Live: every satellite answers, runs an agent job end to end, and keeps its heartbeat fresh."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx
import pytest

pytestmark = pytest.mark.live


def _satellites(hub) -> list[dict]:
    r = hub.get("/v1/satellites")
    assert r.status_code == 200, f"/v1/satellites → HTTP {r.status_code}"
    return r.json().get("satellites") or []


def _online(hub, wait: float = 45.0) -> list[dict]:
    # Right after setup the hub's own satellite joins a few seconds after the hub starts answering
    # (it retries every 10 s), so give it a moment before calling the list empty.
    deadline = time.monotonic() + wait
    sats = _satellites(hub)
    while not any(s.get("status") == "online" for s in sats) and time.monotonic() < deadline:
        time.sleep(2)
        sats = _satellites(hub)
    if not sats:
        pytest.fail("no satellites joined (the hub's own satellite should join by itself; is satellite.enabled on?)")
    return [s for s in sats if s.get("status") == "online"]


def test_local_satellite_service(cfg):
    """satellite: this machine's satellite service answers /v1/health"""
    if not cfg.get("satellite.enabled"):
        pytest.skip("satellite.enabled = false")
    r = httpx.get(f"http://127.0.0.1:{cfg.get('satellite.port')}/v1/health", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data.get("service") == "orbital-satellite", data
    assert "echo" in (data.get("agents") or []), f"echo test agent not available: {data.get('agents')}"


def test_this_satellite_known_to_hub(cfg, secrets):
    """satellite: the hub accepts this machine's heartbeat"""
    if cfg.role != "satellite":
        pytest.skip("this machine is the hub")
    token = secrets.get("ORBITAL_HUB_TOKEN")
    assert token, "no ORBITAL_HUB_TOKEN: this machine hasn't joined (orbital setup)"
    ident = {}
    try:
        ident = json.loads((cfg.paths.satellite_data / "identity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    r = httpx.post(f"{cfg.hub_url()}/v1/satellites/heartbeat", json={"satellite_id": ident.get("satellite_id")},
                   headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == 200, f"heartbeat → HTTP {r.status_code}: {r.text[:200]} (join again: ./setup --reconfigure)"


def test_satellites_answer(automation):
    """satellites: every online satellite answers a ping through the hub"""
    online = _online(automation)
    assert online, "every satellite is offline"
    failed = []
    for s in online:
        r = automation.get(f"/v1/satellites/{s['id']}/ping", timeout=15)
        if r.status_code != 200:
            failed.append(f"{s.get('name')}: HTTP {r.status_code} {r.text[:120]}")
    assert not failed, "; ".join(failed)


def test_satellites_echo_job(automation, nonce):
    """satellites: an echo agent job runs end to end on every online satellite"""
    online = _online(automation)
    assert online, "every satellite is offline"
    failed = []
    for s in online:
        name = s.get("name") or s["id"]
        if "echo" not in ((s.get("capabilities") or {}).get("agents") or []):
            failed.append(f"{name}: echo agent not available")
            continue
        r = automation.post(f"/v1/satellites/{s['id']}/agents/run", timeout=20,
                            json={"agent": "echo", "prompt": nonce, "title": "Live test echo", "track": False})
        if r.status_code != 200:
            failed.append(f"{name}: run → HTTP {r.status_code} {r.text[:120]}")
            continue
        job_id = r.json().get("job_id")
        deadline = time.monotonic() + 30
        job = {}
        while time.monotonic() < deadline:
            jr = automation.get(f"/v1/satellites/{s['id']}/agents/jobs/{job_id}", timeout=10)
            if jr.status_code == 200:
                job = jr.json()
                job = job.get("job", job)
                if job.get("status") in ("done", "failed", "cancelled"):
                    break
            time.sleep(0.5)
        if job.get("status") != "done":
            failed.append(f"{name}: job {job_id} ended {job.get('status') or 'unfinished'}")
            continue
        output = str(job.get("output_tail") or "")
        if nonce not in output:
            lr = automation.get(f"/v1/satellites/{s['id']}/agents/jobs/{job_id}/log", timeout=10)
            output = lr.text if lr.status_code == 200 else output
        if nonce not in output:
            failed.append(f"{name}: output doesn't contain the test text")
    assert not failed, "; ".join(failed)


def _age(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def test_satellite_heartbeats_fresh(automation, cfg):
    """satellites: heartbeats are fresh (offline ones listed)"""
    sats = _satellites(automation)
    if not sats:
        pytest.fail("no satellites joined")
    beat = int(cfg.get("satellite.heartbeat_seconds") or 60)
    stale = []
    for s in sats:
        age = _age(s.get("last_seen"))
        if s.get("status") != "online" or age is None or age > 3 * beat:
            stale.append(f"{s.get('name')}: last seen {'never' if age is None else f'{age:.0f}s ago'}")
    assert not stale, "; ".join(stale) + " (on that machine: orbital doctor)"
