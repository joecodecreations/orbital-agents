"""What this machine has: OS, Python, Tailscale, Xcode, Chrome, agent CLIs, and whether the ports are free.

Everything here only reads. `gather()` is what the wizard's first step and `orbital doctor` show.
"""

from __future__ import annotations

import json
import platform
import shutil
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orbital.services.ops import Result, default_exec
from orbital.services.spec import AGENT_CLIS, platform_name

PORT_KEYS = {
    "hub": "hub.port",
    "tracker": "tracker.port",
    "stt": "tracker.stt_port",
    "browser": "browser.bridge_port",
    "satellite": "satellite.port",
}

PORT_LABELS = {
    "hub": "hub",
    "tracker": "tracker app API",
    "stt": "tracker speech sidecar",
    "browser": "browser bridge",
    "satellite": "satellite service",
}

CHROME_MAC = ("/Applications/Google Chrome.app", "~/Applications/Google Chrome.app")
CHROME_LINUX = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


@dataclass
class PortState:
    key: str
    port: int
    free: bool
    ours: bool = False
    detail: str = ""

    @property
    def conflict(self) -> bool:
        return not self.free and not self.ours


@dataclass
class Environment:
    os: str
    os_version: str
    python: str
    python_ok: bool
    tailscale_installed: bool = False
    tailscale_running: bool = False
    tailscale_name: str = ""
    tailscale_dns: str = ""
    tailscale_ip: str = ""
    xcode: str = ""                  # "Xcode 16.2" when full Xcode is present
    command_line_tools: bool = False
    swift: str = ""
    chrome: str = ""
    agents: dict[str, str] = field(default_factory=dict)   # name → path ('' = missing)
    qrencode: str = ""
    brew: str = ""
    ports: dict[str, PortState] = field(default_factory=dict)


def port_in_use(port: int, hosts: tuple[str, ...] = ("127.0.0.1", "0.0.0.0")) -> bool:
    for host in hosts:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except OSError:
            return True
        finally:
            s.close()
    return False


def probe_orbital(port: int, timeout: float = 0.8) -> str:
    """'' when nothing of ours answers on this port, else the service name it reports."""
    for path in ("/v1/health", "/health", "/v1/hub/info"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:  # noqa: S310
                data = json.loads(r.read(4096).decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            continue
        if isinstance(data, dict):
            svc = str(data.get("service") or data.get("app") or "")
            if "orbital" in svc.lower():
                return svc
    return ""


def check_port(key: str, port: int, in_use: Callable[[int], bool] | None = None,
               probe: Callable[[int], str] | None = None) -> PortState:
    in_use = in_use or port_in_use
    probe = probe or probe_orbital
    if not in_use(port):
        return PortState(key, port, True, detail="free")
    svc = probe(port)
    if svc:
        return PortState(key, port, False, ours=True, detail=f"in use by {svc} (Orbital Agents)")
    return PortState(key, port, False, detail="in use by another program")


def next_free_port(start: int, taken: set[int], in_use: Callable[[int], bool] | None = None,
                   limit: int | None = None) -> int:
    """The next port above `start` that nothing uses (up to start + 100, then from 17800)."""
    in_use = in_use or port_in_use
    limit = limit or start + 100
    port = start + 1
    while port <= limit:
        if port not in taken and not in_use(port):
            return port
        port += 1
    port = 17800
    while port in taken or in_use(port):
        port += 1
    return port


def _run(exec_fn, argv) -> Result:
    return exec_fn(argv, timeout=15)


def find_chrome(plat: str) -> str:
    if plat == "macos":
        for cand in CHROME_MAC:
            p = Path(cand).expanduser()
            if p.exists():
                return str(p)
        return ""
    for cand in CHROME_LINUX:
        found = shutil.which(cand)
        if found:
            return found
    return ""


def gather(cfg, exec_fn=default_exec, tailscale_self=None, ports: bool = True,
           in_use: Callable[[int], bool] | None = None, probe: Callable[[int], str] | None = None) -> Environment:
    plat = platform_name()
    env = Environment(
        os=plat,
        os_version=(platform.mac_ver()[0] if plat == "macos" else platform.release()),
        python=platform.python_version(),
        python_ok=sys.version_info >= (3, 11),
    )
    # Tailscale
    from orbital.core import net
    env.tailscale_installed = net.tailscale_bin() is not None
    me = tailscale_self() if tailscale_self else (net.tailscale_self() if env.tailscale_installed else None)
    if me and me.ip:
        env.tailscale_running = True
        env.tailscale_name, env.tailscale_dns, env.tailscale_ip = me.name, me.dns, me.ip
    # Xcode (macOS)
    if plat == "macos":
        r = _run(exec_fn, ["xcode-select", "-p"])
        env.command_line_tools = r.ok and bool(r.stdout.strip())
        r = _run(exec_fn, ["xcodebuild", "-version"])
        if r.ok and r.stdout.strip():
            env.xcode = r.stdout.strip().splitlines()[0]
    r = _run(exec_fn, ["swift", "--version"]) if shutil.which("swift") else Result(1)
    if r.ok:
        first = (r.stdout or r.stderr).strip().splitlines()
        env.swift = first[0] if first else "swift"
    env.chrome = find_chrome(plat)
    env.agents = {name: shutil.which(name) or "" for name in AGENT_CLIS}
    env.qrencode = shutil.which("qrencode") or ""
    env.brew = shutil.which("brew") or ""
    if ports:
        for key, dotted in PORT_KEYS.items():
            env.ports[key] = check_port(key, int(cfg.get(dotted)), in_use=in_use, probe=probe)
    return env


def report(ui, env: Environment, network_mode: str = "tailscale") -> list[str]:
    """Print the preflight results. Returns blocking problems (empty = fine to continue)."""
    blocking: list[str] = []
    ui.ok(f"{'macOS' if env.os == 'macos' else env.os.capitalize()} {env.os_version}")
    if env.python_ok:
        ui.ok(f"Python {env.python}")
    else:
        ui.fail(f"Python {env.python}: 3.11 or newer is needed")
        blocking.append("python")
    tunnel_mode = (network_mode or "tailscale").strip().lower() == "tunnel"
    if tunnel_mode:
        ui.ok("Network mode: tunnel (Tailscale not required)")
        ui.hint("Hub publishes via Cloudflare Tunnel to 127.0.0.1:7700; satellites join over HTTPS.")
    elif env.tailscale_running:
        ui.ok(f"Tailscale: {env.tailscale_dns or env.tailscale_name} ({env.tailscale_ip})")
    elif env.tailscale_installed:
        ui.warn("Tailscale is installed but not logged in / not running")
        ui.hint("Open Tailscale and log in (or run `tailscale up`). Without it, this machine works on its own,")
        ui.hint("but satellites and the iPhone app can't reach it (or choose network.mode = tunnel).")
    else:
        ui.warn("Tailscale is not installed")
        if env.os == "macos":
            ui.hint("Install it from https://tailscale.com/download/mac (or `brew install --cask tailscale`),")
        else:
            ui.hint("Install it: curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up")
        ui.hint("then log in. Or set network.mode = tunnel in answers.toml for hub-relay without Tailscale.")
    if env.os == "macos":
        if env.xcode:
            ui.ok(env.xcode)
        elif env.command_line_tools:
            ui.warn("Xcode command line tools only: the tracker builds, the iPhone/Mac app needs full Xcode")
            ui.hint("Install Xcode from the App Store, open it once, then re-run setup.")
        else:
            ui.warn("No Xcode or command line tools: apps can't be built")
            ui.hint("Run `xcode-select --install` (tracker) and install Xcode from the App Store (apps).")
    if env.chrome:
        ui.ok(f"Chrome: {env.chrome}")
    else:
        ui.info("Chrome not found: the Browser Controller needs Google Chrome (or Chromium)")
    found = [n for n, p in env.agents.items() if p]
    missing = [n for n, p in env.agents.items() if not p]
    if found:
        ui.ok("Agent CLIs: " + ", ".join(found) + (f"  (not found: {', '.join(missing)})" if missing else ""))
    else:
        ui.warn("No agent CLIs on PATH (claude, codex, grok, gemini). The echo test agent still works.")
    for key, ps in env.ports.items():
        label = f"port {ps.port} ({PORT_LABELS[key]})"
        if ps.free:
            continue
        if ps.ours:
            ui.ok(f"{label}: {ps.detail}")
        else:
            ui.warn(f"{label}: {ps.detail}")
    free = [str(ps.port) for ps in env.ports.values() if ps.free]
    if free:
        ui.ok("Ports free: " + ", ".join(free))
    return blocking
