"""The satellite HTTP API on a real port: auth, allow list, run/list/get/log/cancel."""

from __future__ import annotations

import json
import time

import httpx
from fastapi.testclient import TestClient

from orbital.core.limits import MAX_PROMPT_CHARS
from orbital.core.secrets import Secrets
from orbital.satellite.jobs import JobError, JobManager
from orbital.satellite.server import create_app
from orbital.satellite.tracker import TrackerReporter
from satellite_helpers import ServerThread, make_cfg, wait_for

TOKEN = "test-satellite-token-0123456789"


def app_for(cfg, **kw):
    manager = JobManager(cfg, tracker=TrackerReporter(enabled=False), kill_grace=1.0, **kw)
    return create_app(cfg, manager, token=TOKEN), manager


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_needs_no_token_and_lists_available_agents(home):
    cfg = make_cfg(home)
    app, _ = app_for(cfg)
    with ServerThread(app) as srv:
        body = httpx.get(f"{srv.url}/v1/health").json()
    assert body["service"] == "orbital-satellite"
    assert body["machine"] == "test-machine"
    assert "echo" in body["agents"] and "missing" not in body["agents"]
    assert body["version"]


def test_everything_else_needs_the_satellite_token(home):
    cfg = make_cfg(home)
    app, _ = app_for(cfg)
    with ServerThread(app) as srv:
        assert httpx.get(f"{srv.url}/v1/agents/jobs").status_code == 401
        wrong = httpx.get(f"{srv.url}/v1/agents/jobs", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401 and wrong.json()["error"]
        assert httpx.post(f"{srv.url}/v1/agents/run", json={"agent": "echo", "prompt": "x"}).status_code == 401
        assert httpx.get(f"{srv.url}/v1/agents/jobs", headers=auth()).status_code == 200


def test_token_is_created_in_secrets_when_missing(home):
    cfg = make_cfg(home)
    manager = JobManager(cfg, tracker=TrackerReporter(enabled=False))
    create_app(cfg, manager)
    stored = Secrets(cfg.paths).get("ORBITAL_SATELLITE_TOKEN")
    assert len(stored) >= 20
    assert (cfg.paths.secrets_file.stat().st_mode & 0o777) == 0o600


def test_allow_list_rejects_other_networks(home):
    cfg = make_cfg(home)
    app, _ = app_for(cfg)
    outsider = TestClient(app, client=("203.0.113.9", 50000))
    assert outsider.get("/v1/health").status_code == 403
    assert outsider.get("/v1/agents/jobs", headers=auth()).status_code == 403
    tailnet = TestClient(app, client=("100.101.102.103", 50000))
    assert tailnet.get("/v1/health").status_code == 200
    assert tailnet.get("/v1/agents/jobs", headers=auth()).status_code == 200


def test_allow_list_from_config_can_exclude_loopback(home):
    cfg = make_cfg(home)
    cfg.data["satellite"]["allow_networks"] = ["100.64.0.0/10"]
    app, _ = app_for(cfg)
    with ServerThread(app) as srv:
        assert httpx.get(f"{srv.url}/v1/health").status_code == 403


def test_run_get_log_list_cancel(home, tmp_path):
    cfg = make_cfg(home)
    app, _ = app_for(cfg)
    with ServerThread(app) as srv:
        r = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(),
                       json={"agent": "echo", "prompt": "api says hi", "cwd": str(tmp_path), "title": "API job"})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        done = wait_for(lambda: (j := httpx.get(f"{srv.url}/v1/agents/jobs/{job_id}", headers=auth()).json())["status"] == "done" and j, 10)
        assert done["exit_code"] == 0 and done["title"] == "API job"
        assert done["output_tail"] == "api says hi\n"
        log = httpx.get(f"{srv.url}/v1/agents/jobs/{job_id}/log", headers=auth()).json()
        assert log["log"] == "api says hi\n" and log["status"] == "done"
        plain = httpx.get(f"{srv.url}/v1/agents/jobs/{job_id}/log?format=text", headers=auth())
        assert plain.text == "api says hi\n"
        jobs = httpx.get(f"{srv.url}/v1/agents/jobs", headers=auth()).json()["jobs"]
        assert jobs[0]["id"] == job_id and jobs[0]["job_id"] == job_id

        slow = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "sleeper", "prompt": "20"}).json()["job_id"]
        wait_for(lambda: httpx.get(f"{srv.url}/v1/agents/jobs/{slow}", headers=auth()).json()["status"] == "running", 5)
        assert httpx.post(f"{srv.url}/v1/agents/jobs/{slow}/cancel", headers=auth()).status_code == 200
        started = time.monotonic()
        final = wait_for(lambda: (j := httpx.get(f"{srv.url}/v1/agents/jobs/{slow}", headers=auth()).json())["status"] == "cancelled" and j, 5)
        assert final and time.monotonic() - started < 5


def test_api_errors(home, tmp_path):
    cfg = make_cfg(home, workdir=tmp_path / "work")
    (tmp_path / "work").mkdir()
    app, _ = app_for(cfg)
    with ServerThread(app) as srv:
        outside = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "echo", "prompt": "x", "cwd": "/"})
        assert outside.status_code == 400 and "outside" in outside.json()["error"]
        missing = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "missing", "prompt": "x"})
        assert missing.status_code == 409
        unknown = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "zzz", "prompt": "x"})
        assert unknown.status_code == 404
        bad = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "echo", "prompt": 5})
        assert bad.status_code == 422 and bad.json()["error"]
        assert httpx.get(f"{srv.url}/v1/agents/jobs/jnothere", headers=auth()).status_code == 404
        assert httpx.get(f"{srv.url}/v1/agents/jobs/..%2F..%2Fsecrets/log", headers=auth()).status_code == 404
        agents = httpx.get(f"{srv.url}/v1/agents", headers=auth()).json()["agents"]
        assert {a["name"]: a["available"] for a in agents}["missing"] is False


def test_prompt_limit_is_the_shared_one(home, tmp_path):
    cfg = make_cfg(home, workdir=tmp_path / "work")
    (tmp_path / "work").mkdir()
    app, manager = app_for(cfg)
    assert MAX_PROMPT_CHARS == 100_000
    with ServerThread(app) as srv:
        too_long = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "echo", "prompt": "x" * (MAX_PROMPT_CHARS + 1)})
        assert too_long.status_code == 413 and "100,000" in too_long.json()["error"]
        empty = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "echo", "prompt": "  "})
        assert empty.status_code == 400
        at_limit = httpx.post(f"{srv.url}/v1/agents/run", headers=auth(), json={"agent": "echo", "prompt": "x" * MAX_PROMPT_CHARS})
        assert at_limit.status_code == 200, at_limit.text
    try:
        manager.submit(agent="echo", prompt="y" * (MAX_PROMPT_CHARS + 1))
    except JobError as exc:
        assert exc.status == 413
    else:
        raise AssertionError("the job runner takes the same limit")


def test_bind_hosts_follow_satellite_bind(home, monkeypatch):
    from orbital.satellite import server as srv

    cfg = make_cfg(home)
    monkeypatch.setattr(srv.net, "tailscale_ip", lambda: "100.101.102.103")
    assert srv.bind_hosts(cfg) == (["127.0.0.1"], False), "loopback binds 127.0.0.1 only"
    cfg.data["satellite"]["bind"] = "tailscale"
    assert srv.bind_hosts(cfg) == (["127.0.0.1", "100.101.102.103"], False)
    monkeypatch.setattr(srv.net, "tailscale_ip", lambda: None)
    assert srv.bind_hosts(cfg) == (["127.0.0.1"], True), "waits for Tailscale on loopback"


def answers(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def test_serve_rebinds_when_tailscale_comes_up(home, monkeypatch):
    import threading

    from orbital.satellite import server as srv
    from satellite_helpers import free_port

    port = free_port()
    cfg = make_cfg(home)
    cfg.data["satellite"]["bind"] = "tailscale"
    cfg.data["satellite"]["port"] = port
    address = {"ip": None}
    monkeypatch.setattr(srv.net, "tailscale_ip", lambda: address["ip"])
    monkeypatch.setattr(srv, "REBIND_CHECK_SECONDS", 0.2)
    stop = threading.Event()
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("code", srv.serve(cfg, heartbeat=False, stop=stop)), daemon=True)
    thread.start()
    run_file = cfg.paths.run / "satellite.json"
    try:
        assert wait_for(lambda: run_file.is_file() and json.loads(run_file.read_text())["hosts"] == ["127.0.0.1"], 10)
        assert wait_for(lambda: answers(f"http://127.0.0.1:{port}/v1/health"), 10)
        address["ip"] = "::1"          # stands in for this machine's Tailscale address
        assert wait_for(lambda: json.loads(run_file.read_text())["hosts"] == ["127.0.0.1", "::1"], 10)
        assert wait_for(lambda: answers(f"http://[::1]:{port}/v1/health"), 10)
    finally:
        stop.set()
        thread.join(timeout=20)
    assert result.get("code") == 0 and not run_file.exists()


def test_second_copy_leaves_the_running_one_alone(home):
    """A second `python -m orbital.satellite` on a taken port stops before it touches anything:
    the running copy's jobs keep running and its run file stays."""
    import os
    import socket
    import subprocess

    from orbital.satellite import server as srv
    from satellite_helpers import free_port

    port = free_port()
    cfg = make_cfg(home)
    cfg.data["satellite"]["bind"] = "loopback"
    cfg.data["satellite"]["port"] = port
    cfg.paths.ensure()
    running = socket.socket()                      # stands in for the copy that is already serving
    running.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    running.bind(("127.0.0.1", port))
    running.listen()
    sleeper = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    jobs = cfg.paths.home / "data" / "satellite" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    record = {"id": "j-running", "status": "running", "pid": sleeper.pid, "executable": "/bin/sleep", "agent": "sleeper"}
    (jobs / "j-running.json").write_text(json.dumps(record))
    run_file = cfg.paths.run / "satellite.json"
    run_file.write_text(json.dumps({"pid": os.getpid() + 1, "port": port}))
    try:
        assert srv.serve(cfg, heartbeat=False) == 1
        assert sleeper.poll() is None, "the running copy's job process was stopped"
        assert json.loads((jobs / "j-running.json").read_text())["status"] == "running"
        assert json.loads(run_file.read_text())["pid"] == os.getpid() + 1, "the running copy's run file changed"
    finally:
        sleeper.kill()
        sleeper.wait()
        running.close()
