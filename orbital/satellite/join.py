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
import time
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
    """The Agent Satellite Tracker is running (run/tracker.json), the PC tracker's installer left its
    marker (data/tracker/installed.json), or the macOS app is in /Applications."""
    if not cfg.get("tracker.enabled", True):
        return False
    if (cfg.paths.run / "tracker.json").is_file() or (cfg.paths.tracker_data / "installed.json").is_file():
        return True
    if sys.platform != "darwin":
        return False
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


def system_disk() -> str:
    """The disk the OS runs from: `/`, or the system drive (`C:\\`) on Windows."""
    if sys.platform == "win32":
        return (os.environ.get("SystemDrive") or "C:").rstrip("\\/") + "\\"
    return "/"


def system_metrics(disk_paths: list[str] | None = None) -> dict[str, Any]:
    """Lightweight CPU/memory/disk snapshot for heartbeats. No external deps; never raises.

    `load` (1/5/15 min load average) where the OS has one; Windows has none and sends
    `cpu_percent` (busy share of all cores since the previous heartbeat) instead.
    """
    import shutil
    import time as _time

    disk_paths = disk_paths or [system_disk()]
    out: dict[str, Any] = {"cpus": os.cpu_count() or 1, "time": _time.time()}

    load = _load_average()
    if load:
        out["load"] = load
    cpu = _cpu_percent()
    if cpu is not None:
        out["cpu_percent"] = cpu

    mem = _collect_memory()
    if mem:
        out["memory"] = mem

    seen: set[int] = set()
    disks: list[dict] = []
    for raw in disk_paths:
        path = os.path.expanduser(raw)
        try:
            dev = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except (OSError, ValueError):
            continue
        if dev in seen:
            continue
        seen.add(dev)
        disks.append({"mount": raw, "total": usage.total, "used": usage.used, "free": usage.free,
                      "percent": round(100 * usage.used / usage.total, 1) if usage.total else 0.0})
    if disks:
        out["disks"] = disks

    uptime = _uptime_seconds()
    if uptime is not None:
        out["uptime_s"] = uptime
    return out


def _load_average() -> list[float] | None:
    getloadavg = getattr(os, "getloadavg", None)     # missing on Windows
    if getloadavg is None:
        return None
    try:
        return [round(v, 2) for v in getloadavg()]
    except OSError:
        return None


def _kernel32():
    """Windows kernel32 (tests replace this with a fake)."""
    import ctypes
    return ctypes.WinDLL("kernel32", use_last_error=True)


_cpu_last: tuple[int, int] | None = None     # (idle, total) 100-ns ticks at the previous sample
_sleep = time.sleep


def _cpu_times() -> tuple[int, int] | None:
    import ctypes
    idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if not _kernel32().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return idle.value, kernel.value + user.value      # kernel time includes idle time


def _cpu_percent() -> float | None:
    """Windows: busy share of all cores since the last call (the first call samples 0.2 s)."""
    global _cpu_last
    if sys.platform != "win32":
        return None
    try:
        prev = _cpu_last
        if prev is None:
            prev = _cpu_times()
            _sleep(0.2)
        cur = _cpu_times()
    except Exception:
        return None
    if prev is None or cur is None:
        return None
    _cpu_last = cur
    idle, total = cur[0] - prev[0], cur[1] - prev[1]
    if total <= 0:
        return None
    return round(min(100.0, max(0.0, 100.0 * (1 - idle / total))), 1)


def _uptime_seconds() -> float | None:
    import subprocess
    import time as _time
    try:
        if sys.platform == "win32":
            import ctypes
            tick = _kernel32().GetTickCount64
            tick.restype = ctypes.c_ulonglong
            return tick() / 1000.0
        if sys.platform == "darwin":
            text = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
            found = re.search(r"sec = (\d+)", text)
            return _time.time() - int(found.group(1)) if found else None
        proc = Path("/proc/uptime")
        return float(proc.read_text().split()[0]) if proc.is_file() else None
    except Exception:
        return None


def browser_status(cfg: Config, *, local: bool = False, timeout: float = 2.0) -> dict[str, Any]:
    """This machine's Chrome bridge, as the dashboards show it.

    state: connected | no_extension | bridge_down | disabled. hub_can_drive: the hub can reach the bridge
    (the hub runs here, or the bridge binds Tailscale).
    """
    import time as _time

    enabled = bool(cfg.get("browser.enabled", False))
    port = int(cfg.get("browser.bridge_port", 7703))
    bind = str(cfg.get("browser.bind", "loopback")).lower()
    out: dict[str, Any] = {"state": "disabled", "bridge_up": False, "extension_connected": False,
                           "extension_version": None, "last_poll_age_s": None, "bind": bind,
                           "hub_can_drive": False, "checked_at": round(_time.time(), 1)}
    if not enabled:
        return out
    token = Secrets(cfg.paths).get(BROWSER_TOKEN_KEY)
    base = f"http://127.0.0.1:{port}"
    try:
        response = httpx.get(f"{base}/status", headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=timeout)
        data = response.json() if response.status_code == 200 else None
        if data is None:
            response = httpx.get(f"{base}/health", timeout=timeout)
            data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        data = None
    if not isinstance(data, dict):
        out["state"] = "bridge_down"
        return out
    ext = data.get("extension") if isinstance(data.get("extension"), dict) else {}
    connected = bool(ext.get("connected", data.get("extension_connected")))
    out.update({
        "state": "connected" if connected else "no_extension",
        "bridge_up": True,
        "extension_connected": connected,
        "extension_version": ext.get("version"),
        "last_poll_age_s": ext.get("last_poll_age_s"),
        "bind": str(data.get("bind") or bind),
        "hub_can_drive": local or str(data.get("bind") or bind) == "tailscale",
    })
    return out


def _collect_memory() -> dict[str, Any] | None:
    """{total, used, percent} in bytes, or None. Never raises."""
    try:
        return _read_memory()
    except Exception:
        return None


def _windows_memory() -> dict[str, Any] | None:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)) or not status.ullTotalPhys:
        return None
    total = int(status.ullTotalPhys)
    used = max(0, total - int(status.ullAvailPhys))
    return {"total": total, "used": used, "percent": round(100 * used / total, 1)}


def _read_memory() -> dict[str, Any] | None:
    import subprocess
    if sys.platform == "win32":
        return _windows_memory()
    if sys.platform == "darwin":
        try:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip() or "0")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
        try:
            text = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        size = re.search(r"page size of (\d+) bytes", text)
        page = int(size.group(1)) if size else 4096

        def pages(label: str) -> int:
            found = re.search(rf"{label}:\s+(\d+)", text)
            return int(found.group(1)) if found else 0

        used = (pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")) * page
        if not total:
            return None
        return {"total": total, "used": min(used, total), "percent": round(100 * used / total, 1)}
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values: dict[str, int] = {}
        for line in meminfo.read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
        total_mem, available = values.get("MemTotal"), values.get("MemAvailable")
        if total_mem and available is not None:
            used = total_mem - available
            return {"total": total_mem, "used": used, "percent": round(100 * used / total_mem, 1)}
    return None


def describe_self(cfg: Config, local: bool = False) -> dict[str, Any]:
    """Where the hub reaches this satellite. local=True: the hub runs on this same machine.

    reachability:
      callback — hub dials this machine (Tailscale or same-host loopback)
      pull — satellite opens the control channel (tunnel mode remotes); no callback IP
    """
    me = net.tailscale_self()
    host = (me.dns if me and me.dns else "") or socket.gethostname()
    use_tailscale = str(cfg.get("satellite.bind", "tailscale")).lower() == "tailscale"
    # Hub-local always callbacks on loopback. Tunnel remotes pull (never claim 127.0.0.1).
    if local:
        reachability = "callback"
        ip = "127.0.0.1"
    elif cfg.network_mode == "tunnel":
        reachability = "pull"
        ip = ""
    else:
        reachability = "callback"
        ip = "127.0.0.1" if not (me and me.ip and use_tailscale) else me.ip
    return {
        "name": cfg.machine_name,
        "machine": cfg.machine_name,
        "host": host,
        "ip": ip,
        "port": int(cfg.get("satellite.port", 7704)),
        "os": os_name(),
        "os_version": platform.release(),
        "reachability": reachability,
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
