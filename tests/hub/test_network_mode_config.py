"""network.mode scaffolding and external_hub_url() (PR 1 dual-network modes)."""

from __future__ import annotations

from orbital.core.config import (
    DEFAULT_ALLOW_NETWORKS,
    DEFAULTS,
    LOOPBACK_NETWORKS,
    Config,
    load_config,
)
from orbital.core.paths import Paths


def _cfg(home, overrides: dict | None = None) -> Config:
    return load_config(home, overrides=overrides)


def test_defaults_include_network_tailscale(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.network_mode == "tailscale"
    assert cfg.get("network.mode") == "tailscale"
    assert cfg.get("network.public_url") == ""
    assert cfg.get("network.tunnel_provider") == "cloudflare"
    assert cfg.get("hub.allow_networks") == DEFAULT_ALLOW_NETWORKS
    assert LOOPBACK_NETWORKS == ["127.0.0.0/8", "::1/128"]
    assert "network" in DEFAULTS


def test_hub_url_stays_loopback_on_hub(tmp_path):
    cfg = _cfg(tmp_path, {"system": {"role": "hub"}, "hub": {"port": 7700}})
    assert cfg.hub_url() == "http://127.0.0.1:7700"


def test_external_hub_url_tunnel_uses_public_url(tmp_path):
    cfg = _cfg(
        tmp_path,
        {
            "system": {"role": "hub"},
            "network": {"mode": "tunnel", "public_url": "https://orbital.example.com/"},
        },
    )
    assert cfg.network_mode == "tunnel"
    assert cfg.external_hub_url() == "https://orbital.example.com"
    assert cfg.hub_url() == "http://127.0.0.1:7700"  # local feeds unchanged


def test_external_hub_url_tunnel_empty_when_unset(tmp_path):
    cfg = _cfg(tmp_path, {"system": {"role": "hub"}, "network": {"mode": "tunnel"}})
    assert cfg.external_hub_url() == ""


def test_external_hub_url_satellite_uses_hub_url(tmp_path):
    cfg = _cfg(
        tmp_path,
        {
            "system": {"role": "satellite"},
            "network": {"mode": "tunnel"},
            "hub": {"url": "https://orbital.example.com"},
        },
    )
    assert cfg.hub_url() == "https://orbital.example.com"
    assert cfg.external_hub_url() == "https://orbital.example.com"


def test_external_hub_url_never_loopback_for_invite(tmp_path, monkeypatch):
    """Tailscale path may fall back to machine name, never 127.0.0.1."""
    monkeypatch.setattr("orbital.core.net.tailscale_self", lambda: None)
    cfg = _cfg(
        tmp_path,
        {"system": {"role": "hub", "machine_name": "desk-mac"}, "hub": {"port": 7700}},
    )
    url = cfg.external_hub_url()
    assert "127.0.0.1" not in url
    assert url == "http://desk-mac:7700"


def test_resolved_includes_external_hub_url_and_mode(tmp_path):
    cfg = _cfg(
        tmp_path,
        {
            "system": {"role": "hub"},
            "network": {"mode": "tunnel", "public_url": "https://orbital.example.com"},
        },
    )
    resolved = cfg.resolved()
    assert resolved["network_mode"] == "tunnel"
    assert resolved["external_hub_url"] == "https://orbital.example.com"
    assert resolved["hub_url"] == "http://127.0.0.1:7700"


def test_invalid_mode_falls_back_to_tailscale(tmp_path):
    cfg = _cfg(tmp_path, {"network": {"mode": "wireguard"}})
    assert cfg.network_mode == "tailscale"


def test_answers_keys_include_network():
    from orbital.wizard.answers import KEYS

    assert "network.mode" in KEYS
    assert "network.public_url" in KEYS
