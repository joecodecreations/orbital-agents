"""Contract test with the real hub: its browser relay (/v1/satellites/<id>/browser/<command>) reaches a
real bridge with the browser token, and BrowserClient(node=...) works through it."""

from __future__ import annotations

import threading

import httpx
import pytest

from browser_helpers import FakeExtension, bound_socket, wait_until, write_config

AUTOMATION = "contract-automation-token-0123456789"
JOIN = "contract-join-token-0123456789"
BROWSER = "contract-browser-token-0123456789"

hub_module = pytest.importorskip("orbital.hub")
uvicorn = pytest.importorskip("uvicorn")


@pytest.fixture
def stack(orbital_home, make_bridge, monkeypatch):
    for key, value in (("ORBITAL_AUTOMATION_TOKEN", AUTOMATION), ("ORBITAL_JOIN_TOKEN", JOIN),
                       ("ORBITAL_BROWSER_TOKEN", BROWSER), ("ORBITAL_HUB_USERNAME", "admin"),
                       ("ORBITAL_HUB_PASSWORD", "contract-password-0123456789")):
        monkeypatch.setenv(key, value)
    hub_sock = bound_socket()             # held from now on: no gap for anything else to take the port
    hub_port = hub_sock.getsockname()[1]
    write_config(orbital_home, f'[system]\nmachine_name = "laptop"\n[hub]\nport = {hub_port}\n')
    bridge = make_bridge(token=BROWSER, local_networks=())       # every caller must show the token
    ext = FakeExtension(bridge.url)
    ext.start()

    from orbital.core.config import load_config

    app = hub_module.create_app(load_config(), background=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=hub_port, log_level="warning",
                                           access_log=False, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [hub_sock]}, daemon=True)
    thread.start()
    hub_url = f"http://127.0.0.1:{hub_port}"
    wait_until(lambda: server.started and _up(hub_url))
    assert ext.ready.wait(15), "the fake extension never reached the bridge"
    joined = httpx.post(f"{hub_url}/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json={
        "name": "Studio", "machine": "studio", "host": "studio.example", "ip": "127.0.0.1", "port": 1,   # its satellite service is never called here
        "os": "macos", "satellite_token": "contract-satellite-token-0123456789", "version": "0.1.0",
        "capabilities": {"agents": [], "browser": True, "browser_port": bridge.port, "tracker": False}})
    assert joined.status_code == 200, joined.text
    wait_until(lambda: bridge.state.extension_connected())
    yield bridge, ext, hub_url, joined.json()["satellite_id"]
    server.should_exit = True
    thread.join(15)
    hub_sock.close()
    ext.stop()


def _up(url):
    try:
        return httpx.get(url + "/v1/health", timeout=1).status_code < 500
    except httpx.HTTPError:
        return False


def test_hub_relay_reaches_the_bridge(stack):
    bridge, ext, hub_url, sat_id = stack
    auth = {"Authorization": f"Bearer {AUTOMATION}"}
    r = httpx.post(f"{hub_url}/v1/satellites/{sat_id}/browser/navigate", headers=auth,
                   json={"params": {"url": "https://example.com/"}, "timeout": 10}, timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["url"] == "https://example.com/"
    assert ext.received[-1]["params"] == {"url": "https://example.com/"}


def test_browser_client_through_the_real_hub(stack, tmp_path):
    from orbital.browser import BrowserClient

    bridge, ext, hub_url, sat_id = stack
    c = BrowserClient("studio", via="hub", timeout=10)
    assert c.node.satellite_id == sat_id
    assert c.tabs()[0]["id"] == 7
    assert c.eval("1+2") == "value of 1+2"
    assert c.status()["service"] == "orbital-browser-bridge"      # bridge_status through /command
    f = tmp_path / "clip.bin"
    f.write_bytes(b"remote-bytes")
    staged = c.upload("input[type=file]", f)["files"][0]
    assert open(staged, "rb").read() == b"remote-bytes"
    with pytest.raises(Exception) as err:
        c.call("boom")
    assert "the page said no" in str(err.value)


def test_hub_relay_with_the_wrong_browser_token(stack, monkeypatch):
    bridge, ext, hub_url, sat_id = stack
    bridge.settings.token = "something-else"
    r = httpx.post(f"{hub_url}/v1/satellites/{sat_id}/browser/tabs", headers={"Authorization": f"Bearer {AUTOMATION}"},
                   json={}, timeout=30)
    assert r.status_code >= 400
    assert all(c["action"] != "list_tabs" for c in ext.received)   # refused before it was queued
