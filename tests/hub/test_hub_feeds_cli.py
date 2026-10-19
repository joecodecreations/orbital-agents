"""The feed SDK and the `orbital push / satellites / agents` commands against a real hub on a free port."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from hub_helpers import AUTH, AUTOMATION, JOIN, Server, fake_satellite, free_port, write_config
from orbital.cli.main import main as orbital
from orbital.core.config import load_config
from orbital.feeds import HubClient, HubError, blocks as b

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def hub_server(home):
    write_config(home)
    from orbital.hub import create_app

    cfg = load_config()
    app = create_app(cfg, background=False)
    with Server(app, port=int(cfg.get("hub.port"))) as server:
        yield server, app.state.hub


def website_page() -> dict:
    return b.section("website", "Website uptime", [
        b.heading("now", "Right now"),
        b.metrics("vitals", [b.metric("Status", "Up", caption="checked 2 min ago"),
                             b.metric("Response", "182 ms", delta="-12 ms", direction="down")]),
        b.chart("latency", [b.chart_series("p95", [("2026-01-01", 210), ("2026-01-02", 182)])], title="Response time", y_label="ms"),
        b.table("checks", ["When", "Result"], [["09:00", "200 OK"], ["09:05", None]], title="Latest checks"),
        b.list("notes", ["Certificate renews in 40 days"]),
        b.text("about", "Checked every **5 minutes** from two regions."),
        b.callout("cdn", "The CDN reported a short blip at 03:10.", tone="warning"),
        b.key_value("plan", {"Region": "eu-west", "Plan": "Hobby"}),
        b.timeline("deploys", [b.timeline_item("1 Jan", "Deployed v2", tone="success")]),
        b.buttons("actions", [b.button("Open site", url="https://example.com"),
                              b.button("Re-check now", post="site.recheck", id="recheck", value={"region": "all"}),
                              b.button("Ask why", agent="Why was the site slow last night?"),
                              b.button("Investigate", run_agent="Find out why p95 doubled last night.", machine="web-box",
                                       agent_name="claude", cwd="~/site", title="Slow site")]),
        b.status("regions", [b.status_item("EU", "up", detail="182 ms"), b.status_item("US", "slow", since="2026-01-02T03:00:00Z")],
                 title="Regions"),
        b.progress("budget", [b.progress_item("Error budget", 72, caption="this month"),
                              b.progress_item("Bandwidth", 3.1, max=5, display="3.1 / 5 TB", tone="warning")]),
        b.links("more", [b.link("Status page", "https://status.example.com", icon="dot.radiowaves.left.and.right"),
                         b.link("Runbook", "https://docs.example.com/runbook", detail="when the site is slow")]),
        b.tasks("todo", [b.task("renew", "Renew the certificate", due="2026-11-01")], add_label="Add"),
        b.ledger("ledger", [b.ledger_day("2026-01-01", 12.5, count=2, product="Pro plan")], source="Shop"),
        b.series("visits", [b.series_metric("visits", "Visits", [("2026-01-01", 10), ("2026-01-02", 14)])],
                 kind="site", space="Personal", product="Blog"),
        b.product(kind="site", headline="Up for 30 days", metrics=[b.metric("Uptime", "99.98%")]),
    ], icon="globe", space="web", space_title="Web")


def test_sdk_round_trip(hub_server):
    server, hub = hub_server
    with HubClient.from_config(startup_wait=0) as client:
        assert client.base_url == server.url
        assert client.info()["service"] == "orbital-hub"
        page = website_page()
        assert client.validate("website", section=page)["warnings"] == []
        run = next(item for block in page["blocks"] if block["type"] == "buttons" for item in block["items"] if item["id"] == "investigate")
        assert run["action"] == {"type": "run_agent", "prompt": "Find out why p95 doubled last night.", "machine": "web-box",
                                 "agent": "claude", "cwd": "~/site", "title": "Slow site"}
        assert next(blk for blk in page["blocks"] if blk["type"] == "series")["space"] == "Personal"
        assert b.series("x", [], kind="site", product="p", company="Old")["space"] == "Old", "company is still accepted"
        assert client.put_section(website_page())["revision"] == 1
        patched = client.patch_section("website", [{"op": "merge_section", "value": {"summary": "All up"}}])
        assert patched["revision"] == 2
        assert client.upsert_block("website", b.callout("cdn", "All clear.", tone="success"))["revision"] == 3
        stale = client.patch_section("website", [{"op": "merge_section", "value": {"summary": "x"}}], if_revision=1)
        assert stale["revision"] == 4, "a 409 on id-addressed ops is retried once at the hub's revision"
        with pytest.raises(HubError) as conflict:
            client.patch_section("website", [{"op": "replace_section", "section": website_page()}], if_revision=1)
        assert conflict.value.status == 409 and conflict.value.code == "revision_conflict" and conflict.value.heal

        first = client.alert("Site down", "No answer for 5 minutes.", section_id="website", level="warning")
        assert client.alert("Site down", "No answer for 5 minutes.", section_id="website", level="warning") == first
        stored = client.request("GET", "/v1/alerts")["alerts"]
        assert len(stored) == 1 and stored[0]["interruption_level"] == "timeSensitive" and stored[0]["section_id"] == "website"
        assert client.alert("Disk", "low", id="disk-low", level="info") == "disk-low"

        httpx.post(f"{server.url}/v1/sections/website/actions", headers=AUTH,
                   json={"type": "site.recheck", "item_id": "recheck", "block_id": "actions", "value": {"region": "all"}})
        actions = client.get_actions("website")
        assert [(a["type"], a["value"]) for a in actions] == [("site.recheck", {"region": "all"})]
        assert client.post_action_result("website", actions[0]["id"], "applied", "Checked: all regions up.")["resolved"] == [actions[0]["id"]]
        assert client.get_actions("website") == []

        image = Path(os.environ["ORBITAL_HOME"]) / "chart.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        uploaded = client.upload_media(image)
        assert uploaded["url"].startswith("/v1/media/") and uploaded["content_type"] == "image/png"
        assert client.pulse([{"key": "website.rps", "section_id": "website", "label": "Requests/s", "value": 41}])["keys"] == ["website.rps"]
        with pytest.raises(HubError) as missing:
            client.section("nope")
        assert missing.value.code == "not_found"
    bad = HubClient(server.url, "wrong-token", startup_wait=0)
    with pytest.raises(HubError) as denied:
        bad.catalog()
    assert denied.value.status == 401


def test_sdk_reports_a_missing_hub_and_token(home, monkeypatch):
    write_config(home, hub_port=free_port())
    with pytest.raises(HubError) as down:
        HubClient.from_config(startup_wait=0).health()
    assert down.value.code == "unavailable"
    monkeypatch.delenv("ORBITAL_AUTOMATION_TOKEN")
    with pytest.raises(HubError) as no_token:
        HubClient.from_config()
    assert no_token.value.code == "not_configured"


def test_orbital_push(hub_server, tmp_path, capsys):
    server, hub = hub_server
    page = tmp_path / "page.json"
    page.write_text(json.dumps(website_page()))
    assert orbital(["push", str(page), "--validate"]) == 0
    assert "website: ok" in capsys.readouterr().out
    assert orbital(["push", str(page)]) == 0
    assert capsys.readouterr().out.strip() == "website: revision 1"
    batch = tmp_path / "ops.json"
    batch.write_text(json.dumps({"ops": [{"op": "merge_section", "value": {"summary": "Up"}}]}))
    assert orbital(["push", str(batch)]) == 2, "an ops batch needs --section"
    assert orbital(["push", str(batch), "--section", "website"]) == 0
    assert "revision 2" in capsys.readouterr().out
    assert orbital(["push", "--alert", "Backup failed", "--body", "Exit code 2", "--section", "website", "--level", "error"]) == 0
    assert capsys.readouterr().out.startswith("alert website-")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "Bad Id", "title": "x"}))
    assert orbital(["push", str(bad)]) == 1
    assert "invalid_value" in capsys.readouterr().err
    assert orbital(["push"]) == 2


def test_orbital_satellites_and_agents(hub_server, capsys, monkeypatch):
    server, hub = hub_server
    assert orbital(["satellites"]) == 0
    assert "No satellites yet" in capsys.readouterr().out
    assert orbital(["satellites", "invite", "--token", "--json"]) == 0
    invite = json.loads(capsys.readouterr().out)
    assert invite["join_token"] == JOIN and invite["hub_urls"]
    assert orbital(["satellites", "invite", "--json", "--minutes", "5"]) == 0
    window = json.loads(capsys.readouterr().out)
    assert window["open"] is True and 0 < window["seconds_left"] <= 300
    assert orbital(["satellites", "invite", "--close", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["open"] is False

    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        join = httpx.post(f"{server.url}/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json={
            "name": "Build box", "machine": "build-box", "ip": "127.0.0.1", "port": sat.port,
            "satellite_token": "test-satellite-token-0123456789", "capabilities": {"agents": ["claude", "echo"]}})
        assert join.status_code == 200, join.text
        assert orbital(["satellites"]) == 0
        listing = capsys.readouterr().out
        assert "Build box" in listing and "online" in listing and "claude, echo" in listing

        assert orbital(["agents", "run", "--on", "build-box", "--agent", "echo", "--title", "Say hi", "hello", "there"]) == 0
        assert capsys.readouterr().out.strip() == "started j1 on build-box"
        assert state["runs"][0]["prompt"] == "hello there" and state["runs"][0]["by"] == "cli"
        assert orbital(["agents", "run", "hello"]) == 2
        assert orbital(["agents", "jobs"]) == 0
        assert "j1" in capsys.readouterr().out
        state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": "hello there\n"})
        assert orbital(["agents", "show", "build-box", "j1"]) == 0
        assert "hello there" in capsys.readouterr().out
        assert orbital(["agents", "log", "build-box", "j1"]) == 0
        assert capsys.readouterr().out.startswith("line one")
        assert orbital(["agents", "cancel", "build-box", "j1"]) == 0
        capsys.readouterr()

        monkeypatch.setattr(time, "sleep", lambda s: state["jobs"]["j2"].update({"status": "done", "output_tail": "waited"}))
        assert orbital(["agents", "run", "--on", "build-box", "--wait", "wait for me"]) == 0
        assert capsys.readouterr().out.strip() == "waited"

        assert orbital(["agents", "show", "build-box", "j99"]) == 1
        assert "404" in capsys.readouterr().err

    assert orbital(["satellites", "remove", "build-box"]) == 0
    assert capsys.readouterr().out.strip() == "removed build-box"
    assert orbital(["satellites", "remove", "build-box"]) == 1


@pytest.mark.slow
def test_python_m_orbital_hub_serves_and_writes_run_file(home):
    ports = write_config(home)
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    proc = subprocess.Popen([sys.executable, "-m", "orbital.hub"], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{ports['hub']}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                if httpx.get(f"{url}/v1/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline or proc.poll() is not None:
                raise AssertionError(f"hub did not start: {proc.stderr.read().decode() if proc.poll() is not None else 'timeout'}")
            time.sleep(0.1)
        run_file = json.loads((home / "run" / "hub.json").read_text())
        assert run_file["port"] == ports["hub"] and run_file["pid"] == proc.pid
        assert httpx.get(f"{url}/v1/hub/info").json()["hub_id"] == run_file["hub_id"]
        assert httpx.get(f"{url}/v1/catalog", headers={"Authorization": f"Bearer {AUTOMATION}"}).status_code == 200
        log = (home / "logs" / "hub.log").read_text().splitlines()
        assert any(json.loads(line)["msg"] == "hub started" for line in log)
        assert AUTOMATION not in "\n".join(log), "no token in the log"
        # A second copy on the same port stops at once and leaves the running copy's run file alone.
        second = subprocess.run([sys.executable, "-m", "orbital.hub"], cwd=str(REPO), env=env,
                                capture_output=True, text=True, timeout=30)
        assert second.returncode == 1 and "cannot listen" in second.stderr
        assert json.loads((home / "run" / "hub.json").read_text())["pid"] == proc.pid
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
    assert not (home / "run" / "hub.json").exists(), "a clean exit removes the run file"
