"""Pair this machine with a hub.

    from orbital.satellite.join import join_hub
    info = join_hub("http://hub-mac.example.ts.net:7700", join_token, cfg)

join_hub() makes sure this machine has a satellite token (ORBITAL_SATELLITE_TOKEN; the hub uses it
to start agents here), posts `/v1/satellites/join` with the join token the hub's setup printed,
stores the hub token it gets back (ORBITAL_HUB_TOKEN, used for heartbeats and relays) and the
satellite id (data/satellite/identity.json), and writes `hub.url` and `system.role = "satellite"`
into config.toml, keeping every other line of that file as it was.

The hub's own satellite joins the local hub the same way, automatically, with write_config=False.
"""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import socket
import sys
import tomllib
from pathlib import Path
from typing import Any

import httpx

from orbital import __version__
from orbital.core import net
from orbital.core.config import Config, load_config, to_toml
from orbital.core.secrets import Secrets
from orbital.satellite.agents import available_agents
from orbital.satellite.util import atomic_write_json, iso, now, read_json

HUB_TOKEN_KEY = "ORBITAL_HUB_TOKEN"
SATELLITE_TOKEN_KEY = "ORBITAL_SATELLITE_TOKEN"
BROWSER_TOKEN_KEY = "ORBITAL_BROWSER_TOKEN"
JOIN_TOKEN_KEY = "ORBITAL_JOIN_TOKEN"
TRACKER_APP_GLOBS = ("*Agent Satellite Tracker*.app", "Orbital Agents*Tracker*.app")


class JoinError(RuntimeError):
    pass


# --------------------------------------------------------------------------- identity


def identity_path(cfg: Config) -> Path:
    return cfg.paths.satellite_data / "identity.json"


def read_identity(cfg: Config) -> dict:
    data = read_json(identity_path(cfg), {})
    return data if isinstance(data, dict) else {}


def write_identity(cfg: Config, data: dict) -> None:
    atomic_write_json(identity_path(cfg), data, mode=0o600)


def clear_identity(cfg: Config) -> None:
    try:
        identity_path(cfg).unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- what this machine offers


def tracker_installed(cfg: Config) -> bool:
    """macOS with the Agent Satellite Tracker app installed (or running)."""
    if sys.platform != "darwin" or not cfg.get("tracker.enabled", True):
        return False
    if (cfg.paths.run / "tracker.json").is_file():
        return True
    for folder in ("/Applications", os.path.expanduser("~/Applications")):
        for pattern in TRACKER_APP_GLOBS:
            if glob.glob(os.path.join(folder, pattern)):
                return True
    return False


def capabilities(cfg: Config) -> dict[str, Any]:
    return {
        "agents": available_agents(cfg),
        "browser": bool(cfg.get("browser.enabled", False)),
        "browser_port": int(cfg.get("browser.bridge_port", 7703)),
        "tracker": tracker_installed(cfg),
    }


def os_name() -> str:
    return {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)


def describe_self(cfg: Config, local: bool = False) -> dict[str, Any]:
    """Where the hub reaches this satellite. local=True: the hub runs on this same machine."""
    me = net.tailscale_self()
    host = (me.dns if me and me.dns else "") or socket.gethostname()
    use_tailscale = str(cfg.get("satellite.bind", "tailscale")).lower() == "tailscale"
    ip = "127.0.0.1" if local or not (me and me.ip and use_tailscale) else me.ip
    return {
        "name": cfg.machine_name,
        "machine": cfg.machine_name,
        "host": host,
        "ip": ip,
        "port": int(cfg.get("satellite.port", 7704)),
        "os": os_name(),
        "os_version": platform.release(),
    }


# --------------------------------------------------------------------------- config.toml edits


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _split_comment(line: str) -> str:
    """The trailing ` # comment` of a `key = value` line, or ''."""
    for match in re.finditer("#", line):
        head = line[: match.start()]
        try:
            tomllib.loads(head.strip())
        except tomllib.TOMLDecodeError:
            continue
        return line[match.start():]
    return ""


def set_config_values(path: Path, values: dict[str, Any]) -> None:
    """Set dotted keys (`table.key`) in a TOML file, keeping its other lines and comments."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    for dotted, value in values.items():
        table, key = dotted.rsplit(".", 1)
        rendered = f"{key} = {_toml_scalar(value)}"
        header = re.compile(rf"^\s*\[\s*{re.escape(table)}\s*\]\s*(#.*)?$")
        start = next((i for i, line in enumerate(lines) if header.match(line)), None)
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{table}]", rendered]
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for i in range(start + 1, end):
            if key_re.match(lines[i]):
                comment = _split_comment(lines[i])
                lines[i] = rendered + (" " * max(1, 33 - len(rendered)) + comment if comment else "")
                break
        else:
            insert_at = start + 1
            for i in range(start + 1, end):
                if lines[i].strip() and not lines[i].lstrip().startswith("#"):
                    insert_at = i + 1
            lines.insert(insert_at, rendered)
    body = "\n".join(lines).rstrip() + "\n"
    try:
        tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        # The file had something this editor does not understand: rewrite it from parsed data.
        try:
            data = tomllib.loads(text) if text else {}
        except tomllib.TOMLDecodeError as exc:
            raise JoinError(f"{path} is not valid TOML: {exc}") from exc
        for dotted, value in values.items():
            node = data
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        body = to_toml(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- join


def normalize_hub_url(hub_url: str) -> str:
    url = (hub_url or "").strip().rstrip("/")
    if not url:
        raise JoinError("no hub address given")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    return url


def join_hub(hub_url: str, join_token: str, cfg: Config | None = None, *, write_config: bool = True,
             local: bool = False, timeout: float = 10.0) -> dict:
    """Join the hub at `hub_url`. Returns {satellite_id, hub_id, hub_url, machine, capabilities, config_written,
    browser_token_changed} (when that is true, restart the browser bridge so it uses the fleet's token).

    Raises JoinError when the hub cannot be reached or refuses the join token.
    """
    cfg = cfg or load_config()
    cfg.paths.ensure()
    url = normalize_hub_url(hub_url)
    if not join_token:
        raise JoinError("no join token given (the hub prints one: `orbital satellites invite`)")
    store = Secrets(cfg.paths)
    satellite_token = store.get(SATELLITE_TOKEN_KEY) or store.ensure_token(SATELLITE_TOKEN_KEY)
    caps = capabilities(cfg)
    body = describe_self(cfg, local=local)
    body.update({"satellite_token": satellite_token, "capabilities": caps, "version": __version__,
                 "heartbeat_seconds": int(float(cfg.get("satellite.heartbeat_seconds", 60) or 60))})
    try:
        response = httpx.post(f"{url}/v1/satellites/join", json=body,
                              headers={"X-Orbital-Join-Token": join_token}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise JoinError(f"could not reach the hub at {url}: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:200]
        raise JoinError(f"the hub refused the join ({response.status_code}): {detail}")
    try:
        data = response.json()
    except ValueError as exc:
        raise JoinError("the hub answered the join with something that is not JSON") from exc
    satellite_id, hub_token = data.get("satellite_id"), data.get("hub_token")
    if not satellite_id or not hub_token:
        raise JoinError("the hub's join answer has no satellite_id or hub_token")
    store.set(HUB_TOKEN_KEY, str(hub_token))
    # One browser token for the fleet: the hub relays to every bridge with its own, so this
    # machine's bridge takes it (every join refreshes it). A running bridge picks it up on restart.
    browser_token = data.get("browser_token")
    browser_token_changed = False
    if isinstance(browser_token, str) and browser_token and store.all().get(BROWSER_TOKEN_KEY) != browser_token:
        store.set(BROWSER_TOKEN_KEY, browser_token)
        browser_token_changed = True
    write_identity(cfg, {
        "satellite_id": str(satellite_id),
        "hub_id": data.get("hub_id"),
        "hub_url": url,
        "machine": cfg.machine_name,
        "joined_at": iso(now()),
        "local": bool(local),
    })
    if write_config:
        set_config_values(cfg.paths.config_file, {"hub.url": url, "system.role": "satellite"})
        load_config(cfg.paths.home).write_resolved()
    return {
        "satellite_id": str(satellite_id),
        "hub_id": data.get("hub_id"),
        "hub_url": url,
        "machine": cfg.machine_name,
        "capabilities": caps,
        "config_written": bool(write_config),
        "browser_token_changed": browser_token_changed,
    }
