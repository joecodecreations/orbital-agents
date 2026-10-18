"""`orbital logs`, `orbital status` and `orbital jobs` against a temp ORBITAL_HOME."""

from __future__ import annotations

import json
import time

from fastapi import FastAPI

from orbital.cli.commands import logs as logs_cmd
from orbital.cli.main import main as orbital
from scheduler_helpers import ServerThread, free_port, py_job, setup_home


def stamp(seconds_ago: float) -> str:
    t = time.time() - seconds_ago
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def write_logs(home):
    logs = home / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "hub.log").write_text("\n".join(json.dumps(e) for e in [
        {"ts": stamp(7200), "level": "INFO", "component": "hub", "msg": "hub started"},
        {"ts": stamp(60), "level": "ERROR", "component": "hub", "msg": "section rejected", "section": "demo"},
    ]) + "\n")
    (logs / "satellite.log").write_text("\n".join(json.dumps(e) for e in [
        {"ts": stamp(3000), "level": "WARNING", "component": "satellite", "msg": "heartbeat to the hub failed"},
        {"ts": stamp(30), "level": "INFO", "component": "satellite", "msg": "job finished", "job": "j1"},
    ]) + "\nnot json at all\n")
    (logs / "satellite.stderr.log").write_text("Traceback (most recent call last):\n  boom\n")


def test_parse_since():
    now = 1_000_000.0
    assert logs_cmd.parse_since("30m", now) == now - 1800
    assert logs_cmd.parse_since("2h", now) == now - 7200
    assert logs_cmd.parse_since("1d", now) == now - 86400
    assert logs_cmd.parse_since(None) is None
    assert logs_cmd.parse_since("2026-10-01T12:00") is not None


def test_logs_merge_filter_and_list(home, capsys):
    setup_home(home, None)
    write_logs(home)
    assert orbital(["logs"]) == 0
    out = capsys.readouterr().out.splitlines()
    msgs = [line for line in out if "hub started" in line or "job finished" in line or "section rejected" in line]
    assert "hub started" in msgs[0] and "job finished" in msgs[-1], "merged across components, oldest first"
    assert any("not json at all" in line for line in out)
    assert orbital(["logs", "--level", "WARNING"]) == 0
    out = capsys.readouterr().out
    assert "section rejected" in out and "heartbeat to the hub failed" in out and "job finished" not in out
    assert orbital(["logs", "satellite", "--since", "10m"]) == 0
    out = capsys.readouterr().out
    assert "job finished" in out and "heartbeat" not in out and "hub" not in out.replace("heartbeat to the hub", "")
    assert orbital(["logs", "hub", "--json", "-n", "1"]) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["msg"] == "section rejected" and line["section"] == "demo"
    assert orbital(["logs", "satellite", "--stderr"]) == 0
    assert "Traceback" in capsys.readouterr().out
    assert orbital(["logs", "--list"]) == 0
    listing = capsys.readouterr().out
    assert "hub.log" in listing and "satellite.stderr.log" in listing and "total" in listing
    assert orbital(["logs", "--level", "LOUD"]) == 2


def test_status_reports_services_and_problems(home, capsys):
    app = FastAPI()

    @app.get("/v1/health")
    def health():
        return {"service": "orbital-satellite", "agents": ["echo"]}

    sat_port = free_port()
    setup_home(home, None, config=f"""
[system]
machine_name = "lab-box"
[hub]
port = {free_port()}
[satellite]
port = {sat_port}
[tracker]
enabled = false
[browser]
enabled = false
""")
    write_logs(home)
    with ServerThread(app, port=sat_port):
        assert orbital(["status", "--json"]) == 0
        picture = json.loads(capsys.readouterr().out)
        assert orbital(["status"]) == 0
        text = capsys.readouterr().out
    assert picture["machine"] == "lab-box" and picture["role"] == "hub"
    services = {s["name"]: s for s in picture["services"]}
    assert services["satellite"]["up"] is True and services["satellite"]["agents"] == ["echo"]
    assert services["hub"]["up"] is False and services["scheduler"]["up"] is False
    assert picture["hub"]["reachable"] is False
    assert [p["msg"] for p in picture["problems"]] == ["heartbeat to the hub failed", "section rejected"]
    assert "satellite" in text and "Recent problems" in text


def test_jobs_commands(home, capsys):
    cfg = setup_home(home, [py_job("demo", "print('demo ok=1 a=1 b=2')", group="lab")],
                     groups=[{"id": "lab", "title": "Lab"}])
    assert orbital(["jobs"]) == 0
    assert "demo" in capsys.readouterr().out
    assert orbital(["jobs", "run", "demo"]) == 0
    assert "queued demo" in capsys.readouterr().out
    assert len(list((cfg.paths.scheduler_data / "requests").glob("*.json"))) == 1
    assert orbital(["jobs", "run", "demo", "--here"]) == 0
    out = capsys.readouterr().out
    assert "demo: ok" in out
    assert orbital(["jobs", "cancel", "nope"]) == 2
    assert orbital(["jobs", "refresh", "--group", "lab"]) == 3, "nothing runs the request while the scheduler is stopped"
