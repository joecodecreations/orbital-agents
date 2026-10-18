"""Who may talk to the bridge: allow lists, the bind setting, the browser token, web pages."""

from __future__ import annotations

import httpx
import pytest

from orbital.browser.server import (
    BridgeServer,
    BridgeSettings,
    HttpError,
    allowed_networks,
    bind_addresses,
    bearer_token,
    host_name,
    token_matches,
)

TOKEN = "test-browser-token-123"


# ── pure helpers ──────────────────────────────────────────────────

def test_bind_addresses():
    assert bind_addresses("loopback", "100.64.1.2") == ["127.0.0.1"]
    assert bind_addresses("tailscale", "100.64.1.2") == ["127.0.0.1", "100.64.1.2"]
    assert bind_addresses("tailscale", None) == ["127.0.0.1"]


def test_allowed_networks():
    assert allowed_networks("loopback") == ("127.0.0.0/8", "::1/128")
    assert "100.64.0.0/10" in allowed_networks("tailscale")
    assert "fd7a:115c:a1e0::/48" in allowed_networks("tailscale")


def test_token_helpers():
    assert bearer_token({"Authorization": "Bearer abc"}) == "abc"
    assert bearer_token({"Authorization": "Basic abc"}) == ""
    assert bearer_token({}) == ""
    assert token_matches("abc", "abc")
    assert not token_matches("abc", "abd")
    assert not token_matches("", "")
    assert not token_matches("abc", "")


def test_host_name():
    assert host_name("127.0.0.1:7703") == "127.0.0.1"
    assert host_name("[::1]:7703") == "::1"
    assert host_name("LocalHost:1") == "localhost"
    assert host_name("evil.example") == "evil.example"


# ── check_access without sockets ──────────────────────────────────

def _bridge(tmp_path, **kw) -> BridgeServer:
    settings = BridgeSettings(port=0, data_dir=tmp_path / "data", run_dir=tmp_path / "run", write_run_file=False, **kw)
    return BridgeServer(settings, log=_NullLog())


class _NullLog:
    def __getattr__(self, name):
        return lambda *a, **k: None


def _code(fn):
    try:
        fn()
    except HttpError as exc:
        return exc.status
    return 200


def test_loopback_bind_refuses_tailscale_callers(tmp_path):
    b = _bridge(tmp_path, bind="loopback", token=TOKEN)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert _code(lambda: b.check_access("100.101.102.103", "/status", auth)) == 403
    assert _code(lambda: b.check_access("100.101.102.103", "/health", {})) == 403
    assert _code(lambda: b.check_access("127.0.0.1", "/status", {})) == 200


def test_tailscale_bind_needs_the_token(tmp_path):
    b = _bridge(tmp_path, bind="tailscale", token=TOKEN)
    remote = "100.101.102.103"
    assert _code(lambda: b.check_access(remote, "/status", {})) == 401
    assert _code(lambda: b.check_access(remote, "/status", {"Authorization": "Bearer wrong"})) == 401
    assert _code(lambda: b.check_access(remote, "/command", {"Authorization": f"Bearer {TOKEN}"})) == 200
    assert _code(lambda: b.check_access(remote, "/health", {})) == 200          # health needs no token
    assert _code(lambda: b.check_access("fd7a:115c:a1e0::5", "/status", {"Authorization": f"Bearer {TOKEN}"})) == 200
    assert _code(lambda: b.check_access("8.8.8.8", "/health", {})) == 403       # not tailscale, not loopback
    assert _code(lambda: b.check_access("192.168.1.20", "/status", {"Authorization": f"Bearer {TOKEN}"})) == 403


def test_tailscale_bind_without_a_token_refuses_remote(tmp_path):
    b = _bridge(tmp_path, bind="tailscale", token="")
    assert _code(lambda: b.check_access("100.101.102.103", "/status", {"Authorization": "Bearer "})) == 401
    assert _code(lambda: b.check_access("127.0.0.1", "/status", {})) == 200


def test_extension_endpoints_are_loopback_only(tmp_path):
    b = _bridge(tmp_path, bind="tailscale", token=TOKEN)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert _code(lambda: b.check_access("100.101.102.103", "/ext/commands", auth)) == 403
    assert _code(lambda: b.check_access("100.101.102.103", "/ext/results", auth)) == 403
    assert _code(lambda: b.check_access("127.0.0.1", "/ext/commands", {"Host": "127.0.0.1:7703"})) == 200
    assert _code(lambda: b.check_access("127.0.0.1", "/ext/commands", {"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"})) == 403


def test_web_pages_are_refused(tmp_path):
    b = _bridge(tmp_path)
    assert _code(lambda: b.check_access("127.0.0.1", "/command", {"Origin": "https://evil.example"})) == 403
    assert _code(lambda: b.check_access("127.0.0.1", "/health", {"Origin": "http://localhost:3000"})) == 403
    assert _code(lambda: b.check_access("127.0.0.1", "/ext/commands", {"Origin": "chrome-extension://abcdef"})) == 200


def test_dns_rebinding_host_is_refused(tmp_path):
    b = _bridge(tmp_path, token=TOKEN)
    assert _code(lambda: b.check_access("127.0.0.1", "/command", {"Host": "evil.example:7703"})) == 403
    assert _code(lambda: b.check_access("127.0.0.1", "/command", {"Host": "localhost:7703"})) == 200
    assert _code(lambda: b.check_access("127.0.0.1", "/command", {"Host": "[::1]:7703"})) == 200
    # a caller with the token may use any host name
    assert _code(lambda: b.check_access("127.0.0.1", "/command", {"Host": "my-mac:7703", "Authorization": f"Bearer {TOKEN}"})) == 200


# ── over real sockets: 127.0.0.1 treated as a remote caller ───────

@pytest.fixture
def remote_bridge(make_bridge):
    """A bridge that treats every caller as remote, so the token path runs end to end."""
    return make_bridge(token=TOKEN, local_networks=())


def test_remote_caller_without_token_gets_401(remote_bridge):
    r = httpx.get(remote_bridge.url + "/status")
    assert r.status_code == 401
    assert "token" in r.json()["error"]
    r = httpx.post(remote_bridge.url + "/command", json={"action": "ping"})
    assert r.status_code == 401


def test_remote_caller_with_token_is_accepted(remote_bridge):
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert httpx.get(remote_bridge.url + "/status", headers=h).json()["ok"] is True
    assert httpx.get(remote_bridge.url + "/health").json()["ok"] is True
    assert httpx.get(remote_bridge.url + "/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_extension_still_polls_a_token_protected_bridge(remote_bridge):
    """The extension is on loopback and never has the token; it must keep working."""
    assert httpx.get(remote_bridge.url + "/ext/commands", params={"wait_ms": 0}).status_code == 200
    assert httpx.post(remote_bridge.url + "/ext/events", json={"events": []}).status_code == 200
    r = httpx.get(remote_bridge.url + "/ext/commands", headers={"Host": "rebound.example:1"})
    assert r.status_code == 403


def test_refused_upload_does_not_stage(remote_bridge):
    r = httpx.post(remote_bridge.url + "/stage", params={"name": "x.bin"}, content=b"z" * 1000)
    assert r.status_code == 401
    assert not [p for p in remote_bridge.settings.staged_dir.iterdir() if p.is_file()]


def test_loopback_needs_no_token(make_bridge):
    b = make_bridge(token=TOKEN)
    assert httpx.get(b.url + "/status").json()["ok"] is True


def test_browser_origin_and_preflight_refused(bridge):
    r = httpx.post(bridge.url + "/command", json={"action": "ping"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert httpx.options(bridge.url + "/command").status_code == 403
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    r = httpx.get(bridge.url + "/status", headers={"Host": "attacker.example"})
    assert r.status_code == 403


def test_token_comes_from_secrets_env(orbital_home):
    from orbital.core.config import load_config
    from orbital.core.secrets import Secrets

    cfg = load_config()
    Secrets(cfg.paths).set("ORBITAL_BROWSER_TOKEN", "from-the-file")
    assert BridgeSettings.from_config(cfg).token == "from-the-file"


def test_settings_follow_config(orbital_home):
    from browser_helpers import write_config
    from orbital.core.config import load_config

    write_config(orbital_home, '[browser]\nbridge_port = 17755\nbind = "tailscale"\nmax_events = 500\n')
    s = BridgeSettings.from_config(load_config())
    assert (s.port, s.bind, s.max_events) == (17755, "tailscale", 500)
    assert s.data_dir == orbital_home.resolve() / "data" / "browser"
    assert BridgeSettings.from_config(load_config(), port=17756, bind="loopback").port == 17756


def test_tailscale_bind_without_tailscale_serves_loopback(make_bridge, monkeypatch):
    from orbital.core import net

    monkeypatch.setattr(net, "tailscale_ip", lambda: None)
    b = make_bridge(bind="tailscale", token=TOKEN)
    assert b.listening() == [f"127.0.0.1:{b.port}"]
    assert httpx.get(b.url + "/health").json()["ok"] is True
