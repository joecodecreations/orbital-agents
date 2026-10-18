"""The real hub and the real satellite together, as on a hub machine: join, heartbeat, run an agent through the relay."""

from __future__ import annotations

import httpx
import pytest

from orbital.core.config import load_config
from orbital.core.secrets import Secrets
from orbital.satellite.heartbeat import Heartbeat
from orbital.satellite.jobs import JobManager
from orbital.satellite.server import create_app as satellite_app
from orbital.satellite.tracker import TrackerReporter
from satellite_helpers import ServerThread, free_port, wait_for, write_config

hub_module = pytest.importorskip("orbital.hub")

AUTOMATION = "it-automation-token-0123456789"
JOIN = "it-join-token-0123456789abcdef"


def test_hub_relays_an_echo_job_to_its_own_satellite(home, tmp_path):
    hub_port, sat_port = free_port(), free_port()
    write_config(home, f"""
[system]
role = "hub"
machine_name = "lab-hub"
[hub]
port = {hub_port}
[satellite]
port = {sat_port}
bind = "loopback"
workdirs = ["{tmp_path}"]
heartbeat_seconds = 5
[tracker]
enabled = false
[browser]
enabled = false
""")
    cfg = load_config(home)
    store = Secrets(cfg.paths)
    store.set("ORBITAL_AUTOMATION_TOKEN", AUTOMATION)
    store.set("ORBITAL_JOIN_TOKEN", JOIN)
    auth = {"Authorization": f"Bearer {AUTOMATION}"}
    hub = hub_module.create_app(cfg, background=False)
    manager = JobManager(cfg, tracker=TrackerReporter(enabled=False))
    with ServerThread(hub, port=hub_port) as hub_srv, ServerThread(satellite_app(cfg, manager), port=sat_port):
        beat = Heartbeat(cfg, manager, interval=5)
        beat.tick()                                   # joins the local hub, then beats
        assert beat.last.get("ok") is True, beat.last
        listing = httpx.get(f"{hub_srv.url}/v1/satellites", headers=auth).json()["satellites"]
        assert len(listing) == 1
        me = listing[0]
        assert me["status"] == "online" and me["machine"] == "lab-hub"
        assert "echo" in me["capabilities"]["agents"]
        ping = httpx.get(f"{hub_srv.url}/v1/satellites/{me['id']}/ping", headers=auth).json()
        assert ping["health"]["service"] == "orbital-satellite"

        started = httpx.post(f"{hub_srv.url}/v1/satellites/{me['id']}/agents/run", headers=auth,
                             json={"agent": "echo", "prompt": "relayed by the hub", "cwd": str(tmp_path), "title": "Relay test"})
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]

        def finished():
            job = httpx.get(f"{hub_srv.url}/v1/satellites/{me['id']}/agents/jobs/{job_id}", headers=auth).json()
            return job if job.get("status") == "done" else None

        job = wait_for(finished, 10)
        assert job and job["output_tail"] == "relayed by the hub\n" and job["satellite_id"] == me["id"]
        log = httpx.get(f"{hub_srv.url}/v1/satellites/{me['id']}/agents/jobs/{job_id}/log", headers=auth).json()
        assert log["log"] == "relayed by the hub\n"
        everywhere = httpx.get(f"{hub_srv.url}/v1/agents/jobs", headers=auth).json()["jobs"]
        assert any(j["id"] == job_id and j["machine"] == "lab-hub" for j in everywhere)
        refused = httpx.post(f"{hub_srv.url}/v1/satellites/{me['id']}/agents/run", headers=auth,
                             json={"agent": "echo", "prompt": "x", "cwd": "/"})
        assert refused.status_code == 400 and "outside satellite.workdirs" in refused.json()["error"]
        beat.tick()
        assert beat.last.get("ok") is True
    manager.shutdown(timeout=5)
