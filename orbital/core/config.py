"""The one settings file: ~/.orbital-agents/config.toml.

DEFAULTS below is the full list of settings and their defaults; config/orbital.example.toml documents
each one. The file only needs the values that differ. Secrets never live here (see secrets.py).

After any change, `write_resolved()` writes config.resolved.json: the merged settings plus paths,
which the Swift apps (tracker, iPhone/Mac app tooling) read because they don't parse TOML.
"""

from __future__ import annotations

import copy
import json
import os
import socket
import tomllib
from pathlib import Path
from typing import Any

from orbital.core.paths import Paths

# Who may connect by default. Tunnel mode uses loopback only (Cloudflare connector → 127.0.0.1).
LOOPBACK_NETWORKS = ["127.0.0.0/8", "::1/128"]
TAILSCALE_NETWORKS = ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]
DEFAULT_ALLOW_NETWORKS = [*LOOPBACK_NETWORKS, *TAILSCALE_NETWORKS]

DEFAULTS: dict[str, Any] = {
    "system": {
        "name": "Orbital Agents",       # shown in the apps
        "role": "hub",                  # hub | satellite
        "machine_name": "",             # empty = this computer's short hostname
    },
    # How machines reach each other. Tailscale = private mesh (default). Tunnel = hub via
    # Cloudflare (or similar) + satellite pull channel; see docs/designs/2026-dual-network-modes.md.
    "network": {
        "mode": "tailscale",            # tailscale | tunnel
        "public_url": "",               # tunnel: https://orbital.example.com (no trailing slash, no :7700)
        "tunnel_provider": "cloudflare",  # v1: only cloudflare
        "tunnel_id": "",                # named tunnel id (informational)
        "cloudflared_config": "",       # path to cloudflared config.yml if non-default
    },
    "hub": {
        "host": "0.0.0.0",
        "port": 7700,
        "url": "",                      # satellites: the hub's address, e.g. http://my-mac.tailnet.ts.net:7700
        # Who may connect: loopback + Tailscale. Add your LAN (e.g. "192.168.1.0/24") only if you need it.
        "allow_networks": list(DEFAULT_ALLOW_NETWORKS),
        "data_retention_days": 90,
        "max_media_mb": 8,
    },
    "tracker": {
        "enabled": True,
        "port": 7701,
        "stt_port": 7702,
        "voice": False,                 # on-device speech (downloads a ~1.5 GB model on first use)
        "titles": "auto",               # auto | grok | none: who turns speech into short titles
        "grok_model": "",               # model for grok titles (empty = the grok CLI's default)
        "hotkeys": True,                # global shortcuts: mic / show-hide / type an item (M, T, N)
        "hotkey_modifiers": "ctrl+option",  # ctrl+option | ctrl+option+shift | cmd+option | ... (change if another app uses them)
        "history_retention_days": 365,
        "media_retention_days": 60,
    },
    "browser": {
        "enabled": True,
        "bridge_port": 7703,
        "bind": "loopback",             # loopback | tailscale (lets the hub drive this machine's Chrome)
    },
    "satellite": {
        "enabled": True,                # run agents on this machine when the hub asks
        "port": 7704,
        "bind": "tailscale",            # loopback | tailscale
        "max_concurrent_jobs": 2,
        "job_timeout_minutes": 60,
        "workdirs": ["~"],              # agents may only run inside these folders
        "default_agent": "claude",
        "heartbeat_seconds": 60,
        "max_output_bytes": 2_000_000,  # per agent job; the tail is kept
        "job_retention_days": 14,       # finished job records and output
        # Who may call this machine's satellite service (loopback + Tailscale).
        "allow_networks": list(DEFAULT_ALLOW_NETWORKS),
    },
    # How to start each agent CLI. {prompt} and {cwd} are filled in; no shell is involved.
    "agents": {
        "claude": {"command": ["claude", "-p", "{prompt}"]},
        "codex": {"command": ["codex", "exec", "{prompt}"]},
        "grok": {"command": ["grok", "-p", "{prompt}"]},
        "gemini": {"command": ["gemini", "-p", "{prompt}"]},
        "echo": {"command": ["/bin/echo", "{prompt}"]},   # test agent used by `orbital test`
    },
    "scheduler": {
        "enabled": True,
        "max_parallel": 2,
        "output_retention_days": 14,
        "max_output_bytes": 1_000_000,  # per feed run
    },
    "watchdog": {
        "enabled": True,
        "interval_seconds": 60,
        "failures_before_restart": 2,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 5_000_000,         # per log file before it rolls
        "backups": 3,                   # rolled files kept per component
        "retention_days": 14,           # older rolled files are deleted
    },
    "desk": {                           # the app's Desk: fast voice replies + starting agents
        "provider": "auto",             # auto: Grok subscription, then Claude, then on-device; XAI_API_KEY puts the xAI API first; "off" disables
        "model": "grok-4-fast-non-reasoning",
        "worker_agent": "grok",         # first choice for real work; the next installed agent takes over when it can't run
        "projects": [],                 # folders the desk may start work in: [{name, path, about?, aliases?}]
        "reminders_list": "",           # macOS Reminders list for "remind me…" (empty = default list)
    },
    "speech": {
        "fish_model": "",               # optional Fish Audio voice model id for spoken replies (needs FISH_API_KEY)
        "voices": {},                   # Fish voices the app offers: [speech.voices.<Name>] id, language, default
    },
    "apple": {                          # iPhone / Mac app build settings (setup wizard fills these)
        "team_id": "",
        "bundle_prefix": "com.example",
        "app_name": "Orbital Agents",
        "ios_device": "",
    },
    "notifications": {
        "apns": False,                  # direct Apple push when the app is closed; needs an APNs key (docs/NOTIFICATIONS.md)
        "apns_environment": "development",  # development (Xcode/devicectl installs) | production (TestFlight/App Store)
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Merged settings. Read with dotted keys: cfg.get("hub.port")."""

    def __init__(self, data: dict[str, Any], paths: Paths):
        self.data = data
        self.paths = paths

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name, {}))

    @property
    def role(self) -> str:
        return self.get("system.role", "hub")

    @property
    def machine_name(self) -> str:
        return self.get("system.machine_name") or socket.gethostname().split(".")[0]

    def hub_url(self) -> str:
        """Where this machine reaches the hub: itself on a hub, the configured URL on a satellite."""
        if self.role == "hub":
            return f"http://127.0.0.1:{self.get('hub.port')}"
        return self.get("hub.url", "").rstrip("/")

    @property
    def network_mode(self) -> str:
        mode = (self.get("network.mode") or "tailscale").strip().lower()
        return mode if mode in ("tailscale", "tunnel") else "tailscale"

    def external_hub_url(self) -> str:
        """URL other machines and the phone should use to reach the hub.

        Never returns loopback. Invite, wizard end screens, and agent prompts must use this
        (not hub_url(), which stays loopback on the hub for local feeds).
        """
        if self.role == "satellite":
            return (self.get("hub.url") or "").rstrip("/")

        if self.network_mode == "tunnel":
            return (self.get("network.public_url") or "").rstrip("/")

        port = int(self.get("hub.port", 7700))
        try:
            from orbital.core.net import tailscale_self
            me = tailscale_self()
        except Exception:
            me = None
        if me and me.dns:
            return f"http://{me.dns}:{port}"
        if me and me.ip:
            return f"http://{me.ip}:{port}"
        return f"http://{self.machine_name}:{port}"

    def resolved(self) -> dict[str, Any]:
        out = copy.deepcopy(self.data)
        out["machine_name"] = self.machine_name
        out["hub_url"] = self.hub_url()
        out["external_hub_url"] = self.external_hub_url()
        out["network_mode"] = self.network_mode
        out["paths"] = {
            "home": str(self.paths.home),
            "data": str(self.paths.data),
            "hub_data": str(self.paths.hub_data),
            "tracker_data": str(self.paths.tracker_data),
            "scheduler_data": str(self.paths.scheduler_data),
            "satellite_data": str(self.paths.satellite_data),
            "browser_data": str(self.paths.browser_data),
            "feeds_data": str(self.paths.feeds_data),
            "logs": str(self.paths.logs),
            "run": str(self.paths.run),
            "secrets": str(self.paths.secrets_file),
            "jobs": str(self.paths.jobs_file),
        }
        return out

    def write_resolved(self) -> Path:
        self.paths.ensure()
        target = self.paths.resolved_file
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.resolved(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target


def load_config(home: str | os.PathLike | None = None, overrides: dict[str, Any] | None = None) -> Config:
    p = Paths.resolve(home)
    data = copy.deepcopy(DEFAULTS)
    if p.config_file.is_file():
        with p.config_file.open("rb") as fh:
            data = _merge(data, tomllib.load(fh))
    if overrides:
        data = _merge(data, overrides)
    return Config(data, p)


def to_toml(data: dict[str, Any]) -> str:
    """Small TOML writer for the wizard (tables, strings, numbers, bools, lists of scalars)."""

    def scalar(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(scalar(x) for x in v) + "]"
        return json.dumps(str(v))

    lines: list[str] = []

    def table(prefix: str, node: dict[str, Any]) -> None:
        plain = {k: v for k, v in node.items() if not isinstance(v, dict)}
        nested = {k: v for k, v in node.items() if isinstance(v, dict)}
        if plain:
            if prefix:
                lines.append(f"[{prefix}]")
            for k, v in plain.items():
                lines.append(f"{k} = {scalar(v)}")
            lines.append("")
        for k, v in nested.items():
            table(f"{prefix}.{k}" if prefix else k, v)

    table("", data)
    return "\n".join(lines).rstrip() + "\n"
