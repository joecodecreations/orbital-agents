"""Answers file for a non-interactive setup: `./setup --answers answers.toml --yes`.

Every question the wizard asks has a key here (documented, with defaults, in
config/answers.example.toml). Keys that are missing are asked (or defaulted with --yes).
Secrets may instead come from environment variables, so they never have to sit in a file:
ORBITAL_HUB_PASSWORD, ORBITAL_JOIN_TOKEN, XAI_API_KEY, FISH_API_KEY.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

# dotted key → what it answers. Unknown keys in a file are reported (typos would otherwise be silent).
KEYS: dict[str, str] = {
    "role": "auto | hub | satellite (auto+tailscale: satellite when a hub is found on the tailnet, else hub; auto+tunnel: hub unless satellite.hub_url is set)",
    "system.name": "name shown in the apps (hub)",
    "system.machine_name": "this machine's name; empty = short hostname",
    "network.mode": "tailscale (default mesh) | tunnel (hub via Cloudflare + satellite pull; no Tailscale)",
    "network.public_url": "tunnel hub: https URL phones and satellites use (no trailing slash, no :7700)",
    "ports.hub": "hub port (default 7700)",
    "ports.tracker": "tracker app API port, loopback (7701)",
    "ports.stt": "tracker speech sidecar port, loopback (7702)",
    "ports.browser": "browser bridge port (7703)",
    "ports.satellite": "satellite service port (7704)",
    "ports.on_conflict": "next-free | keep | fail: what to do when a port is taken by another program",
    "components.tracker": "run the Agent Satellite Tracker here (macOS)",
    "components.browser": "run the Browser Controller bridge here",
    "components.browser_remote": "let other machines drive this Chrome (bridge binds to Tailscale)",
    "tracker.hotkeys": "tracker keyboard shortcuts on/off (macOS)",
    "tracker.hotkey_modifiers": "shortcut modifiers, e.g. ctrl+option (default) or ctrl+option+shift",
    "components.agents": "run agents on this machine when asked (satellite service)",
    "components.scheduler": "hub: run the scheduler for data feeds",
    "components.feeds": "hub: example feeds to enable: list of job ids, \"all\", \"none\" or \"default\"",
    "components.services": "install the login services (run at login, restart on crash)",
    "login.username": "hub: app sign-in username",
    "login.password": "hub: app sign-in password; empty = keep the current one or generate one (env ORBITAL_HUB_PASSWORD)",
    "login.generate_password": "hub: generate a password when none is set and none is given",
    "keys.XAI_API_KEY": "optional: Desk voice replies (env XAI_API_KEY)",
    "keys.FISH_API_KEY": "optional: spoken replies (env FISH_API_KEY)",
    "apple.team_id": "Apple Developer Team ID; empty = detect from the Apple Development certificate",
    "apple.bundle_prefix": "bundle id prefix, e.g. com.yourname",
    "apple.ios_device": "iPhone to install to; empty = first connected device",
    "apple.build_tracker": "build + install the tracker app (macOS): true | false",
    "apple.build_mac_app": "build + install the Orbital Agents Mac app: true | false",
    "apple.build_iphone_app": "build + install the iPhone app: auto (when a device is connected) | true | false",
    "browser.open_extensions_page": "open chrome://extensions and the extension folder so you can load it",
    "satellite.hub_url": "satellite: the hub's URL; empty = find on the tailnet (tailscale mode) or required https URL (tunnel mode)",
    "satellite.join_token": "satellite: join token the hub printed (env ORBITAL_JOIN_TOKEN)",
    "skills.install": "install the agent skills into ~/.claude, ~/.codex, ~/.grok, ~/.gemini (where they exist)",
    "skills.agents": "which agent homes: list, or \"all\"",
    "skills.rules": "always-on rule block: auto (skip files that already have another tracker rule) | yes | no",
    "verify.live_test": "run `orbital test --live` at the end",
    "verify.wait_seconds": "how long to wait for services to come up before testing",
}

ENV_FALLBACK = {
    "login.password": "ORBITAL_HUB_PASSWORD",
    "satellite.join_token": "ORBITAL_JOIN_TOKEN",
    "keys.XAI_API_KEY": "XAI_API_KEY",
    "keys.FISH_API_KEY": "FISH_API_KEY",
}


def _flatten(data: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


class Answers:
    def __init__(self, data: dict | None = None, source: str = "", env: dict | None = None):
        self.raw = data or {}
        self.flat = _flatten(self.raw)
        self.source = source
        self.env = os.environ if env is None else env

    @classmethod
    def load(cls, path: str | os.PathLike | None, env: dict | None = None) -> "Answers":
        if not path:
            return cls(env=env)
        p = Path(path).expanduser()
        with p.open("rb") as fh:
            data = tomllib.load(fh)
        return cls(data, source=str(p), env=env)

    def unknown_keys(self) -> list[str]:
        return sorted(k for k in self.flat if k not in KEYS)

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def get(self, key: str, default: Any = None) -> Any:
        """The answer, or `default` when the file doesn't give one. An empty string counts as not
        given (so "" means "use the default / detect it"); secrets fall back to their env var."""
        value = self.flat.get(key)
        if value == "":
            value = None
        if value is None and key in ENV_FALLBACK:
            value = self.env.get(ENV_FALLBACK[key]) or None
        return default if value is None else value

    def why(self, key: str) -> str:
        if key in ENV_FALLBACK and (self.flat.get(key) in (None, "")) and self.env.get(ENV_FALLBACK[key]):
            return f"from ${ENV_FALLBACK[key]}"
        return "answers file"
