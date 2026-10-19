"""Install and control the background services (run at login, restart on crash).

    from orbital.services import install, status, restart
    install(cfg)                       # write + load what this machine should run, remove what it shouldn't
    for s in status(cfg): print(s.name, s.running, s.healthy)
    restart(cfg, ["hub"])

macOS: LaunchAgents `io.orbitalagents.<name>` (launchd.py). Linux: systemd user units
`orbital-<name>.service` (systemd.py). Windows: Task Scheduler tasks `\\OrbitalAgents\\orbital-<name>`
run at logon under a small supervisor (windows.py, winrun.py). Which services run where: spec.py.
Every function takes `ops=Ops(dry_run=True)` to print instead of act, and `home=` / `platform=`
so tests can point it at a temp folder.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from orbital.core.config import Config
from orbital.services.ops import Ops, Result
from orbital.services.spec import (
    LABEL_PREFIX,
    SERVICE_NAMES,
    ServiceSpec,
    build_specs,
    enabled_names,
    health_url,
    platform_name,
    service_path,
    tracker_missing,
    wanted,
)

__all__ = [
    "LABEL_PREFIX", "SERVICE_NAMES", "Ops", "Result", "ServiceSpec", "ServiceStatus", "backend", "build_specs",
    "check_health", "enabled_names", "installed_path", "scheduler_heartbeat", "install", "restart", "service_path", "start", "status", "stop",
    "uninstall", "wait_healthy", "wanted",
]


@dataclass
class ServiceStatus:
    name: str
    wanted: bool
    reason: str = ""            # why it isn't wanted here
    installed: bool = False
    current: bool = False       # file on disk matches what install would write
    loaded: bool = False
    running: bool = False
    pid: int | None = None
    last_exit: int | str | None = None
    state: str = ""
    healthy: bool | None = None  # None = no health endpoint / not checked
    health: str = ""
    file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def ok(self) -> bool:
        if not self.wanted:
            return not self.installed
        return self.installed and self.running and self.healthy is not False


def backend(ops: Ops | None = None, platform: str | None = None, home: Path | None = None):
    ops = ops or Ops()
    plat = platform_name(platform)
    if plat == "macos":
        from orbital.services.launchd import LaunchdBackend
        return LaunchdBackend(ops, home=home)
    if plat == "linux":
        from orbital.services.systemd import SystemdBackend
        return SystemdBackend(ops, home=home)
    if plat == "windows":
        from orbital.services.windows import WindowsBackend
        return WindowsBackend(ops, home=home)
    raise RuntimeError(f"services are supported on macOS, Linux and Windows, not {plat}")


def _select(names: list[str] | None, pool: list[str]) -> list[str]:
    if not names:
        return pool
    unknown = [n for n in names if n not in SERVICE_NAMES]
    if unknown:
        raise ValueError(f"unknown service {', '.join(unknown)} (known: {', '.join(SERVICE_NAMES)})")
    return [n for n in pool if n in names] + [n for n in names if n not in pool]


@dataclass
class Outcome:
    name: str
    ok: bool
    message: str


def install(cfg: Config, *, ops: Ops | None = None, platform: str | None = None, home: Path | None = None,
            names: list[str] | None = None, restart: bool = False, specs: list[ServiceSpec] | None = None,
            remove_unwanted: bool = True) -> tuple[list[Outcome], list[str]]:
    """Install (or update) the services this machine should run. Returns (outcomes, notes).

    Services of ours that are no longer wanted here (component turned off, role changed) are removed.
    A tracker that should run but isn't built yet is reported in notes, not failed.
    """
    ops = ops or Ops()
    be = backend(ops, platform, home)
    specs = specs if specs is not None else build_specs(cfg, platform=platform, home=home)
    if names:
        specs = [s for s in specs if s.name in names]
    outcomes: list[Outcome] = []
    notes: list[str] = []
    ops.mkdir(cfg.paths.logs)
    for spec in specs:
        ok, msg = be.install(spec, restart=restart)
        outcomes.append(Outcome(spec.name, ok, msg))
    if tracker_missing(cfg, platform, home) and (not names or "tracker" in names):
        notes.append("The tracker app isn't installed yet, so its login item was skipped. Build it "
                     "(apps/tracker/scripts/build.sh && apps/tracker/scripts/install.sh), then run "
                     "`orbital services install` again.")
    if remove_unwanted and not names:
        keep = {s.name for s in specs}
        if tracker_missing(cfg, platform, home):
            keep.add("tracker")          # leave an existing tracker item alone until the app is back
        for name in be.installed_names():
            if name in SERVICE_NAMES and name not in keep:
                ok, msg = be.uninstall(name)
                outcomes.append(Outcome(name, ok, f"{msg} ({wanted(cfg, platform).get(name) or 'not wanted'})"))
    notes += be.finish()
    return outcomes, notes


def uninstall(cfg: Config, names: list[str] | None = None, *, ops: Ops | None = None,
              platform: str | None = None, home: Path | None = None) -> list[Outcome]:
    be = backend(ops, platform, home)
    pool = [n for n in SERVICE_NAMES if n in be.installed_names()]
    out = []
    for name in _select(names, pool):
        ok, msg = be.uninstall(name)
        out.append(Outcome(name, ok, msg))
    return out


def _action(verb: str, cfg: Config, names, ops, platform, home) -> list[Outcome]:
    be = backend(ops, platform, home)
    pool = [n for n in SERVICE_NAMES if n in be.installed_names()]
    out = []
    for name in _select(names, pool):
        ok, msg = getattr(be, verb)(name)
        out.append(Outcome(name, ok, msg))
    return out


def restart(cfg: Config, names: list[str] | None = None, *, ops: Ops | None = None,
            platform: str | None = None, home: Path | None = None) -> list[Outcome]:
    return _action("restart", cfg, names, ops, platform, home)


def start(cfg: Config, names: list[str] | None = None, *, ops: Ops | None = None,
          platform: str | None = None, home: Path | None = None) -> list[Outcome]:
    return _action("start", cfg, names, ops, platform, home)


def stop(cfg: Config, names: list[str] | None = None, *, ops: Ops | None = None,
         platform: str | None = None, home: Path | None = None) -> list[Outcome]:
    return _action("stop", cfg, names, ops, platform, home)


def check_health(url: str, timeout: float = 1.5, any_answer: bool = False) -> tuple[bool, str]:
    """GET url. Healthy = HTTP 200 (or any HTTP answer when any_answer)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback URL we built)
            body = resp.read(4096).decode("utf-8", "replace")
            try:
                data = json.loads(body)
                svc = data.get("service") if isinstance(data, dict) else None
            except ValueError:
                svc = None
            return True, f"HTTP {resp.status}" + (f" {svc}" if svc else "")
    except urllib.error.HTTPError as exc:
        if any_answer and exc.code < 500:
            return True, f"HTTP {exc.code} (answering)"
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"no answer ({reason})"


def installed_path(platform: str | None = None, home: Path | None = None,
                   names: tuple[str, ...] = ("satellite", "hub", "scheduler")) -> str | None:
    """The PATH an installed service really runs with (from its plist / unit), or None."""
    import plistlib
    import re

    be = backend(Ops(), platform, home)
    for name in names:
        f = be.file(name)
        if not f.is_file():
            continue
        try:
            if f.suffix == ".plist":
                with f.open("rb") as fh:
                    value = (plistlib.load(fh).get("EnvironmentVariables") or {}).get("PATH")
            elif f.suffix == ".json":          # Windows run spec
                value = (json.loads(f.read_text(encoding="utf-8")).get("env") or {}).get("PATH")
            else:
                m = re.search(r'^Environment="PATH=(.*)"$', f.read_text(encoding="utf-8"), re.M)
                value = m.group(1).replace('\\"', '"').replace("\\\\", "\\") if m else None
        except Exception:  # noqa: BLE001
            value = None
        if value:
            return str(value)
    return None


def scheduler_heartbeat(cfg: Config, limit: float = 180.0) -> tuple[bool, str]:
    """The scheduler writes run/scheduler.json with heartbeat_at every few seconds."""
    from datetime import datetime, timezone

    f = cfg.paths.run / "scheduler.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(str(data.get("heartbeat_at")).replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError):
        return False, "no heartbeat file (run/scheduler.json)"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    if age > limit:
        return False, f"heartbeat {age:.0f}s old"
    return True, f"heartbeat {age:.0f}s ago"


def status(cfg: Config, *, ops: Ops | None = None, platform: str | None = None, home: Path | None = None,
           health: bool = True, names: list[str] | None = None) -> list[ServiceStatus]:
    ops = ops or Ops()
    be = backend(ops, platform, home)
    want = wanted(cfg, platform)
    specs = {s.name: s for s in build_specs(cfg, platform=platform, home=home)}
    out: list[ServiceStatus] = []
    for name in _select(names, list(SERVICE_NAMES)):
        info = be.status(name)
        st = ServiceStatus(name=name, wanted=not want.get(name), reason=want.get(name, ""),
                           installed=info["installed"], loaded=info["loaded"], running=info["running"],
                           pid=info["pid"], last_exit=info["last_exit"], state=info["state"],
                           file=str(be.file(name)))
        if name in specs:
            st.current = be.is_current(specs[name])
            if specs[name].kind == "interval":
                _interval_state(cfg, st, specs[name].interval)
        elif name == "tracker" and st.wanted and not specs.get(name):
            st.reason = "app not installed"
            st.current = st.installed
        if health and st.wanted and st.running:
            url = health_url(cfg, name)
            if url:
                st.healthy, st.health = check_health(url)
            elif name == "scheduler":
                st.healthy, st.health = scheduler_heartbeat(cfg)
        out.append(st)
    return out


def _interval_state(cfg: Config, st: ServiceStatus, interval: int) -> None:
    """A job that runs one pass every `interval` seconds (the watchdog) is idle between passes.

    Count it as running when it is loaded, its last pass exited cleanly, and that pass was recent
    (its state file in run/ was written within three intervals). Otherwise say why not.
    """
    if not st.loaded or st.running:
        return
    state = cfg.paths.run / f"{st.name}.json"
    age = time.time() - state.stat().st_mtime if state.exists() else None
    clean = st.last_exit in (None, 0, "0", "")
    if clean and age is not None and age <= 3 * max(interval, 10):
        st.running = True
        st.state = f"idle, ran {int(age)}s ago"
        st.healthy, st.health = True, "last pass ok"
    elif not clean:
        st.state = f"last pass failed (exit {st.last_exit})"
    elif age is None:
        st.state = "loaded, no pass recorded yet"
    else:
        st.state = f"no pass for {int(age)}s (expected every {interval}s)"


def wait_healthy(cfg: Config, names: list[str] | None = None, timeout: float = 20.0,
                 interval: float = 0.5) -> dict[str, tuple[bool, str]]:
    """Poll the health endpoints of `names` (default: every enabled service that has one)."""
    names = names or [n for n in enabled_names(cfg) if health_url(cfg, n)]
    deadline = time.monotonic() + timeout
    result: dict[str, tuple[bool, str]] = {}
    pending = list(names)
    while pending:
        for name in list(pending):
            url = health_url(cfg, name)
            if not url:
                pending.remove(name)
                continue
            ok, detail = check_health(url)
            result[name] = (ok, detail)
            if ok:
                pending.remove(name)
        if not pending or time.monotonic() > deadline:
            break
        time.sleep(interval)
    return result
