"""describe_self reachability for tunnel vs Tailscale vs hub-local."""

from __future__ import annotations

from orbital.core.config import load_config
from orbital.satellite.join import describe_self


def test_hub_local_is_always_callback(tmp_path, monkeypatch):
    monkeypatch.setattr("orbital.core.net.tailscale_self", lambda: None)
    cfg = load_config(tmp_path, {"system": {"role": "hub", "machine_name": "hub-mac"},
                                 "network": {"mode": "tunnel"}})
    info = describe_self(cfg, local=True)
    assert info["reachability"] == "callback"
    assert info["ip"] == "127.0.0.1"


def test_tunnel_remote_is_pull_with_empty_ip(tmp_path, monkeypatch):
    monkeypatch.setattr("orbital.core.net.tailscale_self", lambda: None)
    cfg = load_config(tmp_path, {"system": {"role": "satellite", "machine_name": "mini"},
                                 "network": {"mode": "tunnel"},
                                 "satellite": {"bind": "loopback"}})
    info = describe_self(cfg, local=False)
    assert info["reachability"] == "pull"
    assert info["ip"] == ""


def test_tailscale_mode_is_callback(tmp_path, monkeypatch):
    monkeypatch.setattr("orbital.core.net.tailscale_self", lambda: None)
    cfg = load_config(tmp_path, {"system": {"role": "satellite", "machine_name": "mini"},
                                 "network": {"mode": "tailscale"},
                                 "satellite": {"bind": "loopback"}})
    info = describe_self(cfg, local=False)
    assert info["reachability"] == "callback"
    assert info["ip"] == "127.0.0.1"
