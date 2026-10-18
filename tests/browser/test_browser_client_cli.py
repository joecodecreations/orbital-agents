"""The Python client and the `orbital browser` CLI against a running bridge and a fake extension."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import httpx
import pytest

from browser_helpers import REPO, refused_port, wait_until, write_config
from orbital.browser import BrowserClient, BrowserError
from orbital.cli.main import main as cli_main


@pytest.fixture
def client(bridge, fake_extension):
    return BrowserClient(url=bridge.url, timeout=10)


@pytest.fixture
def configured(orbital_home, bridge, fake_extension):
    """config.toml points browser.bridge_port at the test bridge, as a real install would."""
    write_config(orbital_home, f"[browser]\nbridge_port = {bridge.port}\n")
    return bridge


# ── client ────────────────────────────────────────────────────────

def test_client_basic_actions(client, fake_extension):
    assert client.navigate("https://example.com/")["url"] == "https://example.com/"
    assert client.tabs()[0]["id"] == 7
    assert client.eval("document.title") == "value of document.title"
    assert client.click("button.go")["selector"] == "button.go"
    assert client.type("#q", "hello", human=True)["human"] is True
    assert client.wait_for("Welcome")["text"] == "Welcome"
    assert client.press("Enter")["key"] == "Enter"
    assert client.text() == "Hello page"
    assert client.capture(True) == {"capturing": True}
    actions = [c["action"] for c in fake_extension.received]
    assert actions == ["navigate", "list_tabs", "eval", "click", "type", "wait_for", "hotkey", "text", "set_capture"]
    nav = fake_extension.received[0]["params"]
    assert nav["url"] == "https://example.com/" and "timeoutMs" in nav


def test_client_status_includes_extension(client):
    data = client.status()
    assert data["service"] == "orbital-browser-bridge"
    assert data["extension"]["connected"] is True
    assert data["extension_status"]["version"] == "0.1.0"
    assert client.health()["extension_connected"] is True


def test_client_session_is_sent(bridge, fake_extension):
    c = BrowserClient(url=bridge.url, session="agent-2")
    c.navigate("https://example.com/")
    assert fake_extension.received[-1]["params"]["session"] == "agent-2"
    c.call("list_tabs", tabId=5)
    assert "session" not in fake_extension.received[-1]["params"]


def test_client_screenshot(client, tmp_path):
    out = tmp_path / "shots" / "page.png"
    png = client.screenshot(out)
    assert png.startswith(b"\x89PNG") and out.read_bytes() == png


def test_client_raises_on_action_errors(client):
    with pytest.raises(BrowserError) as err:
        client.call("boom")
    assert "the page said no" in str(err.value)
    with pytest.raises(BrowserError):
        client.call("does_not_exist")


def test_client_upload_local_uses_the_real_path(client, fake_extension, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"jpeg")
    result = client.upload("input[type=file]", f)
    assert result["files"] == [str(f.resolve())]
    params = fake_extension.received[-1]["params"]
    assert params["selector"] == "input[type=file]" and params["paths"] == [str(f.resolve())]


def test_client_upload_with_stage(client, bridge, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video-bytes")
    result = client.upload("input[type=file]", f, stage=True, click_labels=["Select file"])
    staged = result["files"][0]
    assert staged.startswith(str(bridge.settings.staged_dir.resolve()))
    assert open(staged, "rb").read() == b"video-bytes"


def test_client_stage_and_pull(client, bridge, tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(os.urandom(300_000))
    remote = client.stage(f)
    assert open(remote, "rb").read() == f.read_bytes()
    listing = client.files()
    assert any(e["path"] == remote for e in listing["entries"])
    back = client.pull(remote, tmp_path / "back.bin")
    assert back.read_bytes() == f.read_bytes()


def test_client_network_and_endpoints(client, bridge):
    httpx.post(bridge.url + "/ext/events", json={"events": [
        {"type": "network.completed", "url": "https://api.example.com/v1/items/99999", "method": "GET", "statusCode": 200, "resourceType": "fetch"},
        {"type": "network.completed", "url": "https://other.example.com/page", "method": "GET", "statusCode": 200, "resourceType": "main_frame"},
    ]})
    assert len(client.network()) == 2
    assert [e["url"] for e in client.network("api.example.com")] == ["https://api.example.com/v1/items/99999"]
    eps = client.endpoints()
    assert [(e["method"], e["path_key"]) for e in eps] == [("GET", "/v1/items/{id}")]
    assert client.export()["endpoints"] == 1
    client.clear()
    assert client.network() == []


def test_client_reports_unreachable_bridge(orbital_home):
    with refused_port() as port:
        c = BrowserClient(url=f"http://127.0.0.1:{port}", timeout=2)
        with pytest.raises(BrowserError) as err:
            c.status()
    assert "could not reach" in str(err.value)


def test_client_local_node_uses_config_port(configured):
    c = BrowserClient()          # node="local"
    assert c.node.url == configured.url
    assert c.health()["ok"] is True


def test_client_reports_missing_extension(bridge):
    c = BrowserClient(url=bridge.url, timeout=0.6)
    with pytest.raises(BrowserError) as err:
        c.navigate("https://example.com/", timeout=0.6)
    assert err.value.status == 503
    assert "not connected" in str(err.value)


# ── CLI ───────────────────────────────────────────────────────────

def run_cli(capsys, *argv):
    code = cli_main(["browser", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_status(configured, capsys):
    code, out, _ = run_cli(capsys, "status")
    assert code == 0
    assert "extension   connected" in out and f"127.0.0.1:{configured.port}" in out
    code, out, _ = run_cli(capsys, "status", "--json")
    assert json.loads(out)["extension"]["connected"] is True


def test_cli_page_commands(configured, capsys, tmp_path, fake_extension):
    code, out, _ = run_cli(capsys, "navigate", "https://example.com/")
    assert code == 0 and json.loads(out)["url"] == "https://example.com/"
    code, out, _ = run_cli(capsys, "eval", "1+1")
    assert json.loads(out) == "value of 1+1"
    code, out, _ = run_cli(capsys, "click", "#go")
    assert json.loads(out)["selector"] == "#go"
    code, out, _ = run_cli(capsys, "type", "#q", "hi there", "--clear")
    assert json.loads(out)["typed"] == 8
    shot = tmp_path / "s.png"
    code, out, _ = run_cli(capsys, "screenshot", "--out", str(shot))
    assert code == 0 and shot.read_bytes().startswith(b"\x89PNG")
    code, out, _ = run_cli(capsys, "tabs")
    assert "https://example.com/" in out
    code, out, _ = run_cli(capsys, "command", "ping")
    assert json.loads(out) == {"pong": True}
    code, out, _ = run_cli(capsys, "--session", "two", "navigate", "https://example.org/")
    assert fake_extension.received[-1]["params"]["session"] == "two"
    code, out, _ = run_cli(capsys, "navigate", "https://example.net/", "--session", "three")
    assert fake_extension.received[-1]["params"]["session"] == "three"


def test_cli_upload_stage_network_endpoints(configured, capsys, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    code, out, _ = run_cli(capsys, "stage", str(f))
    staged = json.loads(out)["path"]
    assert code == 0 and open(staged, "rb").read() == b"%PDF-1.4"
    code, out, _ = run_cli(capsys, "upload", "input[type=file]", str(f))
    assert code == 0 and json.loads(out)["files"] == [str(f.resolve())]
    code, out, _ = run_cli(capsys, "network", "--host", "nothing.example")
    assert code == 0 and json.loads(out) == []
    code, out, _ = run_cli(capsys, "endpoints")
    assert code == 0 and "0 endpoints" in out


def test_cli_errors_and_exit_codes(configured, capsys):
    code, _, err = run_cli(capsys, "command", "boom")
    assert code == 1 and "the page said no" in err
    code, _, err = run_cli(capsys, "command", "ping", "[1]")
    assert code == 2
    code, out, _ = run_cli(capsys)
    assert code == 2 and "usage" in out


def test_cli_unreachable_bridge(orbital_home, capsys):
    with refused_port() as port:
        write_config(orbital_home, f"[browser]\nbridge_port = {port}\n")
        code, _, err = run_cli(capsys, "status")
    assert code == 3 and "orbital browser serve" in err


def test_cli_nodes_without_hub(configured, capsys):
    write_config(configured.settings.home, f'[browser]\nbridge_port = {configured.port}\n[system]\nrole = "satellite"\n')
    code, out, _ = run_cli(capsys, "nodes")
    assert code == 0
    assert "local" in out and "connected" in out
    assert "remote nodes unavailable" in out


# ── the bridge as its own process ─────────────────────────────────

def test_server_module_runs_and_stops(orbital_home):
    env = {**os.environ, "ORBITAL_HOME": str(orbital_home), "PYTHONPATH": str(REPO)}
    # --port 0: the OS picks a free port at bind time; run/bridge.json says which.
    proc = subprocess.Popen([sys.executable, "-m", "orbital.browser.server", "--port", "0"],
                            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    run_file = orbital_home / "run" / "bridge.json"
    try:
        def started():
            try:
                data = json.loads(run_file.read_text())
            except (OSError, ValueError):
                return None
            return data if data.get("pid") == proc.pid else None

        run = wait_until(started, timeout=20)
        port = run["port"]
        assert port > 0
        assert httpx.get(f"http://127.0.0.1:{port}/health", timeout=5).json()["ok"] is True
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    assert "bridge on 127.0.0.1:" in out
    assert not (orbital_home / "run" / "bridge.json").exists()
    log = (orbital_home / "logs" / "browser-bridge.log").read_text()
    assert "bridge started" in log and "bridge stopped" in log


def test_server_respects_browser_disabled(orbital_home):
    write_config(orbital_home, "[browser]\nenabled = false\n")
    env = {**os.environ, "ORBITAL_HOME": str(orbital_home), "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-m", "orbital.browser.server"], cwd=REPO, env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "not starting" in out.stdout


def test_cli_serve_port_in_use(orbital_home, bridge, capsys):
    t0 = time.time()
    code = cli_main(["browser", "serve", "--port", str(bridge.port)])
    assert code == 1 and time.time() - t0 < 10
