"""Browser nodes from the hub's satellite list, direct calls with the browser token, and the hub relay."""

from __future__ import annotations

import json

import pytest

from browser_helpers import FakeExtension, FakeHub, wait_until, write_config
from orbital.browser import BrowserClient, BrowserError
from orbital.browser.nodes import NodeError, list_nodes, resolve_node, satellite_node
from orbital.cli.main import main as cli_main
from orbital.core.config import load_config

AUTOMATION = "automation-token-xyz"
BROWSER = "browser-token-abc"


@pytest.fixture
def remote(make_bridge, orbital_home, monkeypatch):
    """A 'satellite' bridge that treats callers as remote (token required) plus a fake hub listing it."""
    bridge = make_bridge(token=BROWSER, local_networks=())
    ext = FakeExtension(bridge.url)
    ext.start()
    sats = [
        {"satellite_id": "sat-1", "name": "studio", "machine": "studio", "ip": "127.0.0.1", "status": "online",
         "capabilities": {"agents": ["claude"], "browser": True, "browser_port": bridge.port, "tracker": True}},
        {"satellite_id": "sat-2", "name": "buildbox", "machine": "buildbox", "ip": "100.64.9.9", "status": "online",
         "capabilities": {"agents": ["codex"], "browser": False}},
    ]
    hub = FakeHub(sats, token=AUTOMATION, browser_token=BROWSER)
    write_config(orbital_home, f'[system]\nrole = "satellite"\nmachine_name = "laptop"\n[hub]\nurl = "{hub.url}"\n')
    monkeypatch.setenv("ORBITAL_AUTOMATION_TOKEN", AUTOMATION)
    assert ext.ready.wait(15), "the fake extension never reached the bridge"
    wait_until(lambda: bridge.state.extension_connected())
    yield bridge, ext, hub
    ext.stop()
    hub.close()


def test_satellite_node_parsing():
    node = satellite_node({"id": "x1", "name": "mini", "ip": "fd7a:115c:a1e0::9",
                           "capabilities": {"browser": True, "browser_port": 7703}, "status": "offline"})
    assert node.url == "http://[fd7a:115c:a1e0::9]:7703"
    assert node.satellite_id == "x1" and node.status == "offline"
    assert satellite_node({"name": "x", "capabilities": {"browser": False}}) is None


def test_resolve_nodes(remote):
    bridge, _, _ = remote
    node = resolve_node("studio")
    assert node.kind == "satellite" and node.satellite_id == "sat-1"
    assert node.url == f"http://127.0.0.1:{bridge.port}"
    assert resolve_node("sat-1").name == "studio"            # by id
    assert resolve_node("STUDIO").name == "studio"           # case-insensitive
    assert resolve_node("local").kind == "local"
    assert resolve_node("laptop").kind == "local"            # this machine's own name
    with pytest.raises(NodeError, match="no browser bridge"):
        resolve_node("buildbox")
    with pytest.raises(NodeError, match="known: buildbox, studio"):
        resolve_node("nowhere")


def test_list_nodes(remote):
    nodes, err = list_nodes()
    assert err is None
    assert [(n.name, n.kind) for n in nodes] == [("local", "local"), ("studio", "satellite")]


def test_hub_needs_the_automation_token(remote, monkeypatch):
    monkeypatch.setenv("ORBITAL_AUTOMATION_TOKEN", "wrong")
    with pytest.raises(NodeError, match="refused"):
        resolve_node("studio")
    nodes, err = list_nodes()
    assert [n.name for n in nodes] == ["local"] and "refused" in err


def test_hub_down(orbital_home):
    write_config(orbital_home, '[system]\nrole = "satellite"\n[hub]\nurl = "http://127.0.0.1:9"\n')
    with pytest.raises(NodeError, match="could not reach the hub"):
        resolve_node("studio")
    with pytest.raises(BrowserError, match="could not reach the hub"):
        BrowserClient("studio")


def test_direct_with_browser_token(remote, monkeypatch):
    bridge, ext, hub = remote
    monkeypatch.setenv("ORBITAL_BROWSER_TOKEN", BROWSER)
    c = BrowserClient("studio", via="direct", timeout=10)
    assert c.navigate("https://example.com/")["url"] == "https://example.com/"
    assert c.status()["extension"]["connected"] is True
    assert hub.relayed == []                               # went straight to the bridge


def test_direct_with_wrong_token_fails(remote, monkeypatch):
    monkeypatch.setenv("ORBITAL_BROWSER_TOKEN", "wrong")
    with pytest.raises(BrowserError) as err:
        BrowserClient("studio", via="direct", timeout=5).tabs()
    assert err.value.status == 401


def test_through_the_hub_relay(remote):
    bridge, ext, hub = remote
    c = BrowserClient("studio", via="hub", timeout=10)
    assert c.tabs()[0]["id"] == 7
    assert c.eval("document.title") == "value of document.title"
    assert c.status(extension=False)["service"] == "orbital-browser-bridge"
    assert c.call("status")["version"] == "0.1.0"
    commands = [cmd for _, cmd, _ in hub.relayed]
    # the bridge's own status is the bridge_status action; "status" is the extension's
    assert commands == ["list_tabs", "eval", "bridge_status", "status"]
    assert hub.relayed[1][2]["code"] == "document.title"
    # bridge-side endpoints work through the relay too
    import httpx

    httpx.post(bridge.url + "/ext/events", json={"events": [
        {"type": "network.completed", "url": "https://api.example.com/v1/x/123456", "method": "GET", "resourceType": "fetch"}]})
    assert c.network()[0]["url"] == "https://api.example.com/v1/x/123456"
    assert c.endpoints()[0]["path_key"] == "/v1/x/{id}"
    assert c.export()["endpoints"] == 1
    assert c.files()["ok"] is True
    c.clear()
    assert c.network() == []


def test_auto_without_browser_token_uses_the_hub(remote):
    bridge, ext, hub = remote
    c = BrowserClient("studio", timeout=10)                # via auto, no ORBITAL_BROWSER_TOKEN here
    c.navigate("https://example.org/")
    assert hub.relayed and hub.relayed[-1][1] == "navigate"


def test_auto_falls_back_to_hub_when_direct_is_refused(remote, monkeypatch):
    bridge, ext, hub = remote
    monkeypatch.setenv("ORBITAL_BROWSER_TOKEN", "stale-token")
    c = BrowserClient("studio", timeout=10)
    assert c.tabs()[0]["id"] == 7
    assert hub.relayed[-1][1] == "list_tabs"


def test_upload_to_a_remote_node_stages_first(remote, tmp_path, monkeypatch):
    bridge, ext, hub = remote
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video")
    # through the hub (base64 JSON)
    c = BrowserClient("studio", via="hub", timeout=10)
    result = c.upload("input[type=file]", f)
    staged = result["files"][0]
    assert staged.startswith(str(bridge.settings.staged_dir.resolve())) and open(staged, "rb").read() == b"video"
    # direct (raw stream)
    monkeypatch.setenv("ORBITAL_BROWSER_TOKEN", BROWSER)
    c2 = BrowserClient("studio", via="direct", timeout=10)
    path = c2.stage(f)
    assert open(path, "rb").read() == b"video"


def test_cli_with_node(remote, capsys):
    code = cli_main(["browser", "--node", "studio", "tabs", "--json"])
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)[0]["id"] == 7
    code = cli_main(["browser", "nodes"])
    out = capsys.readouterr().out
    assert code == 0 and "studio" in out and "satellite" in out
    code = cli_main(["browser", "nodes", "--json"])
    rows = json.loads(capsys.readouterr().out)["nodes"]
    studio = next(r for r in rows if r["name"] == "studio")
    assert studio["bridge"] == "up" and studio["extension_connected"] is True
    code = cli_main(["browser", "--node", "buildbox", "tabs"])
    assert code == 1 and "no browser bridge" in capsys.readouterr().err


def test_local_node_name_matches_this_machine(orbital_home):
    write_config(orbital_home, '[system]\nmachine_name = "deskbox"\n[browser]\nbridge_port = 17799\n')
    node = resolve_node("deskbox", load_config())
    assert node.kind == "local" and node.url == "http://127.0.0.1:17799"
