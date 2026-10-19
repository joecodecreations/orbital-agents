"""Settings and paths, read from $ORBITAL_HOME/config.resolved.json like the Mac app.

Anything missing falls back to the defaults of orbital/core/config.py, so the tracker also runs
before setup. Mirrors apps/tracker/Sources/OrbitalTrackerCore/{Config,Paths}.swift.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TRACKER_PORT = 7701
DEFAULT_STT_PORT = 7702


def home_directory(env: dict[str, str] | None = None) -> Path:
    """ORBITAL_HOME, else ~/.orbital-agents."""
    env = os.environ if env is None else env
    raw = (env.get("ORBITAL_HOME") or "").strip()
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    return Path.home() / ".orbital-agents"


def short_host_name() -> str:
    """socket.gethostname().split('.')[0], like orbital/core/config.py."""
    try:
        name = socket.gethostname().split(".")[0]
    except OSError:
        name = ""
    return name or "pc"


class _Reader:
    """Dotted-key access into the parsed JSON, tolerant of types."""

    def __init__(self, root: dict) -> None:
        self.root = root

    def value(self, dotted: str) -> Any:
        node: Any = self.root
        for part in dotted.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    def string(self, key: str) -> str | None:
        v = self.value(key)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (str, int, float)):
            return str(v)
        return None

    def int(self, key: str) -> int | None:
        v = self.value(key)
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            try:
                return int(v.strip())
            except ValueError:
                return None
        return None

    def bool(self, key: str) -> bool | None:
        v = self.value(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.lower()
            if s in ("true", "yes", "on", "1"):
                return True
            if s in ("false", "no", "off", "0"):
                return False
        return None

    def port(self, key: str) -> int | None:
        n = self.int(key)
        return n if n is not None and 0 < n < 65536 else None


@dataclass
class OrbitalConfig:
    home: Path
    resolved_file: Path
    loaded_from_file: bool = False
    system_name: str = "Orbital Agents"
    role: str = "hub"
    machine_name: str = field(default_factory=short_host_name)
    hub_url: str = ""
    tracker_enabled: bool = True
    tracker_port: int = DEFAULT_TRACKER_PORT
    history_retention_days: int = 365
    media_retention_days: int = 60
    log_level: str = "INFO"
    log_max_bytes: int = 5_000_000
    log_backups: int = 3
    log_retention_days: int = 14
    tracker_data: Path = Path()
    logs_dir: Path = Path()
    run_dir: Path = Path()
    secrets_file: Path = Path()

    @property
    def is_satellite(self) -> bool:
        return self.role == "satellite"

    @property
    def relays_to_hub(self) -> bool:
        """Satellites copy every command to the hub's tracker as well."""
        return self.is_satellite and bool(self.hub_url)

    @classmethod
    def load(cls, env: dict[str, str] | None = None) -> "OrbitalConfig":
        home = home_directory(env)
        file = home / "config.resolved.json"
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = None
        except (OSError, ValueError):
            data = None
        return cls.parse(data, home)

    @classmethod
    def parse(cls, data: dict | None, home: Path) -> "OrbitalConfig":
        r = _Reader(data or {})

        def path(key: str, fallback: Path) -> Path:
            s = r.string(key)
            return Path(os.path.expanduser(s)) if s else fallback

        home_dir = path("paths.home", home)
        machine = r.string("machine_name") or r.string("system.machine_name") or short_host_name()
        hub = (r.string("hub_url") or r.string("hub.url") or "").strip().strip("/").strip()
        return cls(
            home=home_dir,
            resolved_file=home / "config.resolved.json",
            loaded_from_file=data is not None,
            system_name=r.string("system.name") or "Orbital Agents",
            role="satellite" if (r.string("system.role") or "hub").lower() == "satellite" else "hub",
            machine_name=machine,
            hub_url=hub,
            tracker_enabled=r.bool("tracker.enabled") if r.bool("tracker.enabled") is not None else True,
            tracker_port=r.port("tracker.port") or DEFAULT_TRACKER_PORT,
            history_retention_days=r.int("tracker.history_retention_days") if r.int("tracker.history_retention_days") is not None else 365,
            media_retention_days=r.int("tracker.media_retention_days") if r.int("tracker.media_retention_days") is not None else 60,
            log_level=(r.string("logging.level") or "INFO").upper(),
            log_max_bytes=max(r.int("logging.max_bytes") or 5_000_000, 1024),
            log_backups=max(r.int("logging.backups") if r.int("logging.backups") is not None else 3, 0),
            log_retention_days=r.int("logging.retention_days") if r.int("logging.retention_days") is not None else 14,
            tracker_data=path("paths.tracker_data", home_dir / "data" / "tracker"),
            logs_dir=path("paths.logs", home_dir / "logs"),
            run_dir=path("paths.run", home_dir / "run"),
            secrets_file=path("paths.secrets", home_dir / "secrets.env"),
        )

    def ensure_home(self) -> None:
        """Creates ORBITAL_HOME (mode 0700, like orbital/core/paths.py) when it doesn't exist yet."""
        if not self.home.exists():
            try:
                self.home.mkdir(parents=True, mode=0o700, exist_ok=True)
            except OSError:
                pass


@dataclass(frozen=True)
class TrackerPaths:
    """Where the tracker keeps things (the same layout as the Mac app):

        <tracker_data>/items.json      every item (the app is the only writer)
                       settings.json   screenshot evidence level, shared with the phone app and the CLI
                       inbox/          commands queued while the app wasn't reachable (+ failed/)
                       hub-outbox/     satellites: commands waiting for the hub
                       media/YYYY-MM/  small archived copies of screenshots and video frame sheets
                       icons/          optional provider logos (<agent>.png)
        <logs>/tracker.log             JSON lines, rolled by size
        <run>/tracker.json             {service, version, port, pid, startedAt} while the app runs
    """

    root: Path
    logs_dir: Path
    run_dir: Path

    @classmethod
    def from_config(cls, cfg: OrbitalConfig) -> "TrackerPaths":
        return cls(cfg.tracker_data, cfg.logs_dir, cfg.run_dir)

    @property
    def items_file(self) -> Path:
        return self.root / "items.json"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def inbox_dir(self) -> Path:
        return self.root / "inbox"

    @property
    def failed_inbox_dir(self) -> Path:
        return self.inbox_dir / "failed"

    @property
    def hub_outbox_dir(self) -> Path:
        return self.root / "hub-outbox"

    @property
    def media_dir(self) -> Path:
        return self.root / "media"

    @property
    def icons_dir(self) -> Path:
        return self.root / "icons"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "tracker.log"

    @property
    def run_file(self) -> Path:
        return self.run_dir / "tracker.json"

    @property
    def lock_file(self) -> Path:
        return self.run_dir / "tracker.lock"

    @property
    def installed_file(self) -> Path:
        """Written by `python -m tracker_pc --install-marker` (the autostart installers): lets the
        satellite report capabilities.tracker even while the app isn't running."""
        return self.root / "installed.json"

    def ensure_directories(self) -> None:
        for d in (self.root, self.inbox_dir, self.media_dir, self.icons_dir, self.logs_dir, self.run_dir):
            d.mkdir(parents=True, exist_ok=True)

    def archive_path(self, relative: str) -> Path:
        return self.root / relative


def tmp_inbox() -> Path:
    """Fallback inbox for sandboxed agents that can't write into the data folder (one per user).
    /tmp/orbital-tracker-inbox-<uid> on macOS/Linux (the Mac app's path), %TEMP% on Windows."""
    if hasattr(os, "getuid"):
        return Path(f"/tmp/orbital-tracker-inbox-{os.getuid()}")
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name is not a reason to fail
        user = "user"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user) or "user"
    return Path(tempfile.gettempdir()) / f"orbital-tracker-inbox-{safe}"


def parse_secrets(text: str) -> dict[str, str]:
    """secrets.env: KEY=VALUE lines, # comments, optional quotes (orbital/core/secrets.py rules)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            out[key] = value
    return out


def secret(key: str, file: Path, env: dict[str, str] | None = None) -> str | None:
    """Environment first, then secrets.env. Never logged."""
    env = os.environ if env is None else env
    if env.get(key):
        return env[key]
    try:
        value = parse_secrets(file.read_text(encoding="utf-8")).get(key)
    except OSError:
        return None
    return value or None


def platform_name() -> str:
    return {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)
