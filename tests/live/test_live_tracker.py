"""Live: the Agent Satellite Tracker round trip (orbital-track → tracker app → hub)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from orbital.services.spec import service_path, tracker_binary

pytestmark = pytest.mark.live


def _orbital_track() -> str | None:
    return shutil.which("orbital-track") or shutil.which("orbital-track", path=service_path())


def _run(exe: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ORBITAL_TRACK_PARENT", None)       # this check makes its own top-level item
    env.pop("ORBITAL_TRACK_DISABLE", None)
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=30, env=env)


def _relay_round_trip(cfg, hub, item: str) -> None:
    """start + done through POST /v1/tracker/command with this satellite's hub token."""
    from orbital.core.secrets import Secrets

    token = Secrets(cfg.paths).get("ORBITAL_HUB_TOKEN")
    if not token:
        pytest.fail("ORBITAL_HUB_TOKEN is missing from secrets.env: join the hub (orbital satellite join <hub_url> <token>)")
    machine = {"machine": cfg.machine_name} if cfg.machine_name else {}
    start = {"op": "start", "id": item, "agent": "orbital-test", "title": "Live Test Item",
             "details": "Created by `orbital test --live` on a satellite without a tracker; finished right away.", **machine}
    r = hub.post("/v1/tracker/command", token=token, json=start)
    assert r.status_code == 200, f"the hub's tracker relay refused this machine's start (HTTP {r.status_code}: {r.text[:200]})"
    done = hub.post("/v1/tracker/command", token=token, json={"op": "done", "id": item, "summary": "live test done", **machine})
    assert done.status_code == 200, f"the hub's tracker relay refused this machine's done (HTTP {done.status_code}: {done.text[:200]})"


def test_tracker_round_trip(cfg, hub, nonce):
    """tracker: orbital-track start/update/done shows up in the hub's /v1/tracker/items"""
    if cfg.role == "hub" and sys.platform == "darwin" and cfg.get("tracker.enabled") and tracker_binary() is None:
        pytest.fail("the tracker is enabled but its app isn't installed: "
                    "apps/tracker/scripts/build.sh && apps/tracker/scripts/install.sh --no-build")
    if cfg.role == "hub" and not (sys.platform == "darwin" and cfg.get("tracker.enabled")):
        pytest.skip("no tracker on this hub (tracker.enabled = false or not macOS)")
    exe = _orbital_track()
    if not exe and cfg.role != "hub" and not (sys.platform == "darwin" and cfg.get("tracker.enabled")):
        # No tracker on this satellite (Linux, or tracker.enabled = false): check that the hub's tracker
        # relay takes this machine's commands instead.
        _relay_round_trip(cfg, hub, nonce.replace("orbital-live-", "olt"))
        return
    if not exe:
        pytest.fail("orbital-track isn't on PATH (apps/tracker/scripts/install.sh links it into ~/.local/bin)")
    item = nonce.replace("orbital-live-", "olt")
    r = _run(exe, "start", "--agent", "orbital-test", "--title", "Live Test Item", "--id", item,
             "--details", "Created by `orbital test --live`; finished right away.", "--quiet")
    assert r.returncode == 0, f"orbital-track start failed: {r.stderr.strip()[:200]}"
    printed = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    item = printed or item
    try:
        r = _run(exe, "update", item, "--note", "live test update", "--quiet")
        assert r.returncode == 0, f"orbital-track update failed: {r.stderr.strip()[:200]}"
    finally:
        done = _run(exe, "done", item, "--summary", "live test done", "--quiet")
    assert done.returncode == 0, f"orbital-track done failed: {done.stderr.strip()[:200]}"
    if cfg.role != "hub":
        # A satellite can't read the hub's items (no app/automation token here); delivery is what we can check.
        ping = _run(exe, "ping")
        assert ping.returncode == 0, f"orbital-track ping: nothing reachable ({ping.stdout.strip()[:200]})"
        return
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        last = hub.get(f"/v1/tracker/items/{item}")
        if last.status_code == 200:
            break
        time.sleep(0.5)
    assert last is not None and last.status_code == 200, \
        f"item {item} not visible at /v1/tracker/items/{item} (HTTP {last.status_code if last else '?'})"
    status = str(last.json().get("status") or last.json().get("item", {}).get("status") or "")
    assert status in ("", "done", "completed"), f"item status is {status!r}, expected done"
