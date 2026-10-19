"""What runs at login on this machine, and how each service is started.

One ServiceSpec per service. The launchd, systemd and windows backends turn a spec into a LaunchAgent
plist, a systemd user unit or a Task Scheduler task; nothing here touches the system.

    hub             python -m orbital.hub                  hub role, always
    scheduler       python -m orbital.scheduler            hub role, scheduler.enabled
    satellite       python -m orbital.satellite            satellite.enabled (hub and satellites)
    browser-bridge  python -m orbital.browser.server       browser.enabled
    watchdog        python -m orbital.scheduler.watchdog   watchdog.enabled, every interval_seconds (one pass per run)
    tracker         the installed tracker app's binary     macOS, tracker.enabled, app installed
"""

from __future__ import annotations

import glob
import os
import plistlib
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from orbital.core.config import Config
from orbital.core.paths import REPO_ROOT

LABEL_PREFIX = "io.orbitalagents."
UNIT_PREFIX = "orbital-"

# Order matters: install brings the hub up before the things that talk to it.
SERVICE_NAMES = ("hub", "satellite", "scheduler", "browser-bridge", "watchdog", "tracker")

MODULES = {
    "hub": "orbital.hub",
    "scheduler": "orbital.scheduler",
    "satellite": "orbital.satellite",
    "browser-bridge": "orbital.browser.server",
    "watchdog": "orbital.scheduler.watchdog",
}

DESCRIPTIONS = {
    "hub": "Orbital Agents hub",
    "scheduler": "Orbital Agents scheduler (data feeds)",
    "satellite": "Orbital Agents satellite service (runs agents on this machine)",
    "browser-bridge": "Orbital Agents Browser Controller bridge",
    "watchdog": "Orbital Agents watchdog (health checks, restarts, log trimming)",
    "tracker": "Orbital Agents - Agent Satellite Tracker",
}

# Same names the satellite's capability check uses (orbital/satellite/join.py).
TRACKER_APP_GLOBS = ("*Agent Satellite Tracker*.app", "Orbital Agents*Tracker*.app")

AGENT_CLIS = ("claude", "codex", "grok", "gemini")


@dataclass
class ServiceSpec:
    name: str
    program: list[str]
    env: dict[str, str]
    log_path: Path
    working_dir: Path
    kind: str = "daemon"            # daemon (always on) | interval (one pass every N s) | app (GUI, restart on crash)
    interval: int = 0
    description: str = ""
    health_url: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LABEL_PREFIX + self.name

    @property
    def unit(self) -> str:
        return UNIT_PREFIX + self.name


def platform_name(platform: str | None = None) -> str:
    p = platform or sys.platform
    if p == "darwin":
        return "macos"
    if p.startswith("linux"):
        return "linux"
    if p in ("win32", "cygwin", "windows"):
        return "windows"
    return p


def repo_python(repo: Path = REPO_ROOT, platform: str | None = None) -> str:
    """The repo's venv interpreter (not resolved: the venv symlink is what makes it the venv)."""
    if platform_name(platform) == "windows":
        venv = repo / ".venv" / "Scripts" / "python.exe"
    else:
        venv = repo / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable


def _bundle_binary(app: Path) -> Path | None:
    info = app / "Contents" / "Info.plist"
    name = None
    if info.is_file():
        try:
            with info.open("rb") as fh:
                name = plistlib.load(fh).get("CFBundleExecutable")
        except Exception:
            name = None
    macos = app / "Contents" / "MacOS"
    if name and (macos / name).is_file():
        return macos / name
    if (macos / "OrbitalTracker").is_file():
        return macos / "OrbitalTracker"
    if macos.is_dir():
        for entry in sorted(macos.iterdir()):
            if entry.is_file() and os.access(entry, os.X_OK):
                return entry
    return None


def find_tracker_app(home: Path | None = None) -> Path | None:
    """The installed tracker app bundle (~/Applications first, then /Applications)."""
    home = Path(home) if home else Path.home()
    override = os.environ.get("ORBITAL_TRACKER_APP")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    for folder in (home / "Applications", Path("/Applications")):
        for pattern in TRACKER_APP_GLOBS:
            for hit in sorted(glob.glob(str(folder / pattern))):
                return Path(hit)
    return None


def tracker_binary(home: Path | None = None) -> Path | None:
    app = find_tracker_app(home)
    if app is None:
        return None
    if app.is_file():          # ORBITAL_TRACKER_APP may point straight at a binary
        return app
    return _bundle_binary(app)


def service_path(home: Path | None = None, repo: Path = REPO_ROOT, extra: list[str] | None = None,
                 platform: str | None = None) -> str:
    """PATH for services. launchd starts with a bare PATH, so agent CLIs (often installed with npm,
    Homebrew or a version manager) would not resolve without this."""
    home = Path(home) if home else Path.home()
    if platform_name(platform) == "windows":
        return _windows_path(home, repo, extra)
    dirs: list[str] = [str(repo / ".venv" / "bin"), str(home / ".local" / "bin")]
    dirs += ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
    for exe in (*AGENT_CLIS, "node", "npm", "npx", "bun", "tailscale", "orbital-track"):
        found = shutil.which(exe)
        if found:
            dirs.append(str(Path(found).parent))
    for rel in (".npm-global/bin", ".bun/bin", ".volta/bin", ".cargo/bin", ".claude/local", ".deno/bin",
                "Library/pnpm", ".yarn/bin"):
        dirs.append(str(home / rel))
    dirs += list(extra or [])
    system = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    out: list[str] = []
    for d in dirs:
        if d and d not in out and d not in system and Path(d).is_dir():
            out.append(d)
    return ":".join(out + system)


def _windows_path(home: Path, repo: Path, extra: list[str] | None) -> str:
    """Windows: a task gets the user's environment already; put the venv and agent CLIs first."""
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    dirs: list[str] = [str(repo / ".venv" / "Scripts"), str(home / ".local" / "bin")]
    for exe in (*AGENT_CLIS, "node", "npm", "npx", "bun", "tailscale", "orbital-track"):
        found = shutil.which(exe)
        if found:
            dirs.append(str(Path(found).parent))
    dirs += [str(appdata / "npm"), str(home / ".bun" / "bin"), str(home / ".cargo" / "bin"),
             str(local / "Programs" / "nodejs"), str(home / ".claude" / "local"), str(home / ".deno" / "bin")]
    dirs += list(extra or [])
    out: list[str] = []
    for d in dirs:
        if d and d not in out and Path(d).is_dir():
            out.append(d)
    for d in os.environ.get("PATH", "").split(";" if os.name == "nt" else os.pathsep):
        if d and d not in out:
            out.append(d)
    return ";".join(out)


def health_url(cfg: Config, name: str) -> str | None:
    if name == "hub":
        return f"http://127.0.0.1:{cfg.get('hub.port')}/v1/health"
    if name == "satellite":
        return f"http://127.0.0.1:{cfg.get('satellite.port')}/v1/health"
    if name == "browser-bridge":
        return f"http://127.0.0.1:{cfg.get('browser.bridge_port')}/health"
    if name == "tracker":
        return f"http://127.0.0.1:{cfg.get('tracker.port')}/v1/health"
    return None


def wanted(cfg: Config, platform: str | None = None) -> dict[str, str]:
    """Every service name → '' when it should run on this machine, or the reason it doesn't."""
    plat = platform_name(platform)
    role = cfg.role
    out: dict[str, str] = {}
    out["hub"] = "" if role == "hub" else "satellites don't run the hub"
    if role != "hub":
        out["scheduler"] = "satellites don't run the scheduler"
    else:
        out["scheduler"] = "" if cfg.get("scheduler.enabled", True) else "scheduler.enabled = false"
    out["satellite"] = "" if cfg.get("satellite.enabled", True) else "satellite.enabled = false"
    out["browser-bridge"] = "" if cfg.get("browser.enabled", True) else "browser.enabled = false"
    out["watchdog"] = "" if cfg.get("watchdog.enabled", True) else "watchdog.enabled = false"
    if plat != "macos":
        out["tracker"] = "the tracker is a macOS app"
    elif not cfg.get("tracker.enabled", True):
        out["tracker"] = "tracker.enabled = false"
    else:
        out["tracker"] = ""
    return out


def enabled_names(cfg: Config, platform: str | None = None) -> list[str]:
    w = wanted(cfg, platform)
    return [n for n in SERVICE_NAMES if not w.get(n)]


def build_specs(cfg: Config, *, platform: str | None = None, python: str | None = None,
                repo: Path = REPO_ROOT, path_value: str | None = None, home: Path | None = None,
                tracker_bin: Path | None | str = "auto") -> list[ServiceSpec]:
    """Specs for the services this machine should run (enabled for its role and config).

    The tracker is left out (with a note in `skipped`) when its app isn't installed yet.
    """
    python = python or repo_python(repo, platform)
    path_value = path_value if path_value is not None else service_path(home, repo, platform=platform)
    logs = cfg.paths.logs
    env = {
        "ORBITAL_HOME": str(cfg.paths.home),
        "PATH": path_value,
        "PYTHONUNBUFFERED": "1",
    }
    specs: list[ServiceSpec] = []
    for name in enabled_names(cfg, platform):
        log = logs / f"{name}.stderr.log"
        if name == "tracker":
            binary = tracker_binary(home) if tracker_bin == "auto" else tracker_bin
            if not binary:
                continue
            specs.append(ServiceSpec(name=name, program=[str(binary)], env=dict(env), log_path=log,
                                     working_dir=cfg.paths.home, kind="app",
                                     description=DESCRIPTIONS[name], health_url=health_url(cfg, name)))
            continue
        program = [python, "-m", MODULES[name]]
        if name == "watchdog":
            specs.append(ServiceSpec(name=name, program=program, env=dict(env), log_path=log,
                                     working_dir=repo, kind="interval",
                                     interval=max(10, int(cfg.get("watchdog.interval_seconds", 60))),
                                     description=DESCRIPTIONS[name]))
            continue
        specs.append(ServiceSpec(name=name, program=program, env=dict(env), log_path=log, working_dir=repo,
                                 kind="daemon", description=DESCRIPTIONS[name],
                                 health_url=health_url(cfg, name)))
    return specs


def tracker_missing(cfg: Config, platform: str | None = None, home: Path | None = None) -> bool:
    """True when the tracker should run here but its app isn't installed."""
    return "tracker" in enabled_names(cfg, platform) and tracker_binary(home) is None
