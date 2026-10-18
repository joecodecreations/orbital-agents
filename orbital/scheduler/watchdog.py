"""Watchdog: health checks, restarts and trimming for every Orbital Agents service on this machine.

    python -m orbital.scheduler.watchdog              # one pass (the io.orbitalagents.watchdog service
                                                      # runs this every watchdog.interval_seconds)
    python -m orbital.scheduler.watchdog --loop       # foreground, a pass every interval
    python -m orbital.scheduler.watchdog --dry-run    # report only; restart and delete nothing

Each pass checks, when they apply to this machine:

    hub              GET 127.0.0.1:<hub.port>/v1/health              (role hub)
    satellite        GET 127.0.0.1:<satellite.port>/v1/health        (satellite.enabled)
    browser-bridge   GET 127.0.0.1:<browser.bridge_port>/health      (browser.enabled)
    tracker          GET 127.0.0.1:<tracker.port>/v1/health          (macOS, tracker.enabled, app installed)
    scheduler        run/scheduler.json heartbeat younger than 3 min (role hub, scheduler.enabled)

A service that fails `watchdog.failures_before_restart` passes in a row is restarted through the
service manager (`launchctl kickstart -k gui/<uid>/io.orbitalagents.<name>` on macOS,
`systemctl --user restart orbital-<name>` on Linux, `open -g` for the tracker app), and the hub gets
an alert "Self-heal: restarted <name>". Restarts are spaced out (one per service per few minutes,
at most six an hour) so a service that cannot start is not hammered. Services that are not
installed are reported and left alone. State: run/watchdog.json. Then the janitor runs
(orbital/scheduler/janitor.py).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from orbital.core.config import Config, load_config
from orbital.core.logs import get_logger
from orbital.satellite.util import atomic_write_json, iso, parse_iso, read_json
from orbital.scheduler.janitor import run_janitor

LABEL_PREFIX = "io.orbitalagents."
SYSTEMD_PREFIX = "orbital-"
SCHEDULER_HEARTBEAT_LIMIT = 180.0
RESTARTS_PER_HOUR = 6
STILL_DOWN_ALERT_EVERY = 86400.0
TRACKER_APP_GLOBS = ("*Agent Satellite Tracker*.app", "Orbital Agents*Tracker*.app")


@dataclass
class Check:
    name: str                 # hub | satellite | browser-bridge | tracker | scheduler
    kind: str                 # http | heartbeat
    target: str               # URL, or heartbeat file path
    port: int | None = None


# --------------------------------------------------------------------------- what to check


def tracker_app(cfg: Config) -> str | None:
    for folder in ("/Applications", os.path.expanduser("~/Applications")):
        for pattern in TRACKER_APP_GLOBS:
            found = sorted(glob.glob(os.path.join(folder, pattern)))
            if found:
                return found[0]
    return None


def checks_for(cfg: Config, platform: str = sys.platform) -> list[Check]:
    out: list[Check] = []
    if cfg.role == "hub":
        port = int(cfg.get("hub.port", 7700))
        out.append(Check("hub", "http", f"http://127.0.0.1:{port}/v1/health", port))
    if cfg.get("satellite.enabled", True):
        port = int(cfg.get("satellite.port", 7704))
        out.append(Check("satellite", "http", f"http://127.0.0.1:{port}/v1/health", port))
    if cfg.get("browser.enabled", False):
        port = int(cfg.get("browser.bridge_port", 7703))
        out.append(Check("browser-bridge", "http", f"http://127.0.0.1:{port}/health", port))
    if platform == "darwin" and cfg.get("tracker.enabled", True):
        port = int(cfg.get("tracker.port", 7701))
        out.append(Check("tracker", "http", f"http://127.0.0.1:{port}/v1/health", port))
    if cfg.role == "hub" and cfg.get("scheduler.enabled", True):
        out.append(Check("scheduler", "heartbeat", str(cfg.paths.run / "scheduler.json")))
    return out


def pid_alive(pid) -> bool:
    """True when the process exists (or the file names none); a heartbeat from a dead process is stale."""
    if not pid:
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, OverflowError):
        return False


def probe(check: Check, timeout: float = 5.0, tries: int = 2) -> tuple[bool, str]:
    """(healthy, detail)."""
    if check.kind == "heartbeat":
        beat = read_json(Path(check.target), None)
        if not isinstance(beat, dict):
            return False, "no heartbeat file"
        stamp = parse_iso(beat.get("heartbeat_at"))
        if stamp is None:
            return False, "heartbeat file has no time"
        age = time.time() - stamp
        if age > SCHEDULER_HEARTBEAT_LIMIT:
            return False, f"heartbeat is {int(age)} s old"
        if beat.get("stopped_at"):
            return False, f"stopped at {beat['stopped_at']}"
        if not pid_alive(beat.get("pid")):
            return False, f"not running (process {beat.get('pid')} is gone)"
        return True, f"heartbeat {int(age)} s ago"
    detail = ""
    for attempt in range(max(1, tries)):
        try:
            response = httpx.get(check.target, timeout=timeout)
            if 200 <= response.status_code < 300:
                return True, f"HTTP {response.status_code}"
            detail = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            detail = f"{exc.__class__.__name__}"
        if attempt + 1 < tries:
            time.sleep(1.0)
    return False, detail


# --------------------------------------------------------------------------- service manager


def service_label(name: str) -> str:
    return f"{LABEL_PREFIX}{name}"


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def systemd_user_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def service_installed(name: str, cfg: Config, platform: str = sys.platform,
                      agents_dir: Path | None = None, units_dir: Path | None = None) -> bool:
    if platform == "darwin":
        if name == "tracker":
            return tracker_app(cfg) is not None or (agents_dir or launch_agents_dir()).joinpath(f"{service_label(name)}.plist").is_file()
        return (agents_dir or launch_agents_dir()).joinpath(f"{service_label(name)}.plist").is_file()
    if platform.startswith("linux"):
        return (units_dir or systemd_user_dir()).joinpath(f"{SYSTEMD_PREFIX}{name}.service").is_file()
    return False


def restart_command(name: str, cfg: Config, platform: str = sys.platform, uid: int | None = None,
                    app_path: str | None = None) -> list[str] | None:
    """The command that restarts one service, or None on a platform without a service manager."""
    uid = os.getuid() if uid is None else uid
    if platform == "darwin":
        if name == "tracker":
            app = app_path if app_path is not None else tracker_app(cfg)
            if app:
                return ["/usr/bin/open", "-g", app]
            bundle = f"{cfg.get('apple.bundle_prefix', 'com.example')}.orbitalagents.tracker"
            return ["/usr/bin/open", "-g", "-b", bundle]
        return ["/bin/launchctl", "kickstart", "-k", f"gui/{uid}/{service_label(name)}"]
    if platform.startswith("linux"):
        return ["systemctl", "--user", "restart", f"{SYSTEMD_PREFIX}{name}"]
    return None


def run_command(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{exc.__class__.__name__}: {exc}"
    return proc.returncode, (proc.stderr or proc.stdout).strip()[:300]


# --------------------------------------------------------------------------- alerts


def raise_alert(cfg: Config, title: str, body: str, level: str = "warning") -> bool:
    """Alert through the feeds SDK (orbital.feeds.HubClient). False when the hub can't be reached."""
    try:
        from orbital.feeds import HubClient  # the hub's SDK; imported lazily
    except ImportError:
        return False
    from orbital.core.secrets import Secrets

    store = Secrets(cfg.paths)
    # The hub uses the automation token; a satellite has only its hub token, which may post alerts too.
    token = store.get("ORBITAL_AUTOMATION_TOKEN") or (store.get("ORBITAL_HUB_TOKEN") if cfg.role == "satellite" else "")
    try:
        client = HubClient.from_config(cfg, token=token or None, startup_wait=0)
        try:
            client.alert(title, body, level=level)
        finally:
            client.close()
        return True
    except Exception:  # noqa: BLE001 - hub down, no token: the restart itself already happened
        return False


# --------------------------------------------------------------------------- one pass


class Watchdog:
    def __init__(self, cfg: Config, *, dry_run: bool = False, platform: str = sys.platform,
                 runner: Callable[[list[str]], tuple[int, str]] = run_command,
                 prober: Callable[[Check], tuple[bool, str]] = probe,
                 installed: Callable[[str], bool] | None = None,
                 alerter: Callable[[str, str], bool] | None = None,
                 janitor: bool = True, log=None):
        self.cfg = cfg
        self.dry_run = dry_run
        self.platform = platform
        self.runner = runner
        self.prober = prober
        self.installed = installed or (lambda name: service_installed(name, cfg, platform))
        self.alerter = alerter or (lambda title, body: raise_alert(cfg, title, body))
        self.janitor = janitor
        self.log = log or get_logger("watchdog", home=str(cfg.paths.home))
        self.state_file = cfg.paths.run / "watchdog.json"

    def load_state(self) -> dict:
        state = read_json(self.state_file, {})
        if not isinstance(state, dict):
            state = {}
        state.setdefault("services", {})
        return state

    def min_gap(self) -> float:
        return max(120.0, 2 * float(self.cfg.get("watchdog.interval_seconds", 60) or 60))

    def restart_allowed(self, record: dict, now: float) -> bool:
        restarts = [t for t in record.get("restarts") or [] if now - t < 3600]
        if restarts and now - max(restarts) < self.min_gap():
            return False
        return len(restarts) < RESTARTS_PER_HOUR

    def check_one(self, check: Check, record: dict, now: float, notes: list[str]) -> None:
        threshold = max(1, int(self.cfg.get("watchdog.failures_before_restart", 2) or 2))
        healthy, detail = self.prober(check)
        record["checked_at"] = iso(now)
        record["detail"] = detail
        if healthy:
            if record.get("failures"):
                notes.append(f"{check.name} is answering again")
            record["failures"] = 0
            record["status"] = "up"
            record["last_ok"] = iso(now)
            record.pop("down_since", None)
            return
        if not self.installed(check.name):
            record.update({"status": "not installed", "failures": 0})
            return
        record["failures"] = int(record.get("failures") or 0) + 1
        record["status"] = "down"
        record["last_fail"] = iso(now)
        record.setdefault("down_since", iso(now))
        if record["failures"] < threshold:
            notes.append(f"{check.name} did not answer ({detail}); {record['failures']}/{threshold}")
            return
        if not self.restart_allowed(record, now):
            notes.append(f"{check.name} still down ({detail}); waiting before another restart")
            self.maybe_still_down_alert(check, record, now, notes)
            return
        argv = restart_command(check.name, self.cfg, self.platform)
        if argv is None:
            notes.append(f"{check.name} is down and this platform has no service manager to restart it")
            return
        notes.append(f"{check.name} failed {record['failures']} checks ({detail}); restarting: {' '.join(argv)}")
        if self.dry_run:
            return
        code, output = self.runner(argv)
        record.setdefault("restarts", []).append(now)
        record["restarts"] = [t for t in record["restarts"] if now - t < 86400]
        record["failures"] = 0
        record["status"] = "restarted" if code == 0 else "restart failed"
        record["last_restart"] = {"at": iso(now), "exit_code": code, "output": output}
        self.log.warning("self-heal restart", extra={"fields": {"service": check.name, "exit_code": code,
                                                              "detail": detail, "output": output}})
        title = f"Self-heal: restarted {check.name}" if code == 0 else f"Self-heal: could not restart {check.name}"
        body = (f"{check.name} on {self.cfg.machine_name} stopped answering ({detail}) and the watchdog "
                + ("restarted it." if code == 0 else f"tried to restart it: exit {code} {output}"))
        record["alerted"] = bool(self.alerter(title, body))

    def maybe_still_down_alert(self, check: Check, record: dict, now: float, notes: list[str]) -> None:
        last = record.get("still_down_alert_at")
        restarts = [t for t in record.get("restarts") or [] if now - t < 3600]
        if len(restarts) < RESTARTS_PER_HOUR or self.dry_run:
            return
        if last is not None and now - float(last) < STILL_DOWN_ALERT_EVERY:
            return
        record["still_down_alert_at"] = now
        notes.append(f"{check.name} is still down after {len(restarts)} restarts this hour; alerting")
        self.alerter(f"Self-heal: {check.name} keeps failing",
                     f"{check.name} on {self.cfg.machine_name} has been down since {record.get('down_since')} and "
                     f"{len(restarts)} restarts in the last hour did not bring it back. Check `orbital logs {check.name}`.")

    def run_once(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        self.cfg.paths.ensure()
        state = self.load_state()
        notes: list[str] = []
        if not self.cfg.get("watchdog.enabled", True):
            return {"notes": ["watchdog.enabled is false"], "services": {}}
        active = set()
        for check in checks_for(self.cfg, self.platform):
            active.add(check.name)
            record = state["services"].setdefault(check.name, {})
            try:
                self.check_one(check, record, now, notes)
            except Exception as exc:  # noqa: BLE001 - one bad check never stops the pass
                notes.append(f"{check.name} check failed: {type(exc).__name__}: {exc}")
        for name in list(state["services"]):
            if name not in active:
                state["services"][name] = {"status": "not checked here"}
        if self.janitor and not self.dry_run:
            state["janitor"] = run_janitor(self.cfg, now)
            state["janitor_at"] = iso(now)
        state["checked_at"] = iso(now)
        state["notes"] = notes[-20:]
        if not self.dry_run:
            atomic_write_json(self.state_file, state)
            for note in notes:
                self.log.info(note)
        return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orbital.scheduler.watchdog",
                                     description="Orbital Agents watchdog: health checks, restarts, trimming")
    parser.add_argument("--loop", action="store_true", help="keep running, a pass every watchdog.interval_seconds")
    parser.add_argument("--dry-run", action="store_true", help="report only; restart and delete nothing")
    parser.add_argument("--json", action="store_true", help="print the pass result as JSON")
    args = parser.parse_args(argv)
    while True:
        cfg = load_config()
        state = Watchdog(cfg, dry_run=args.dry_run).run_once()
        if args.json:
            print(json.dumps(state, indent=2, default=str))
        elif args.dry_run or sys.stdout.isatty():
            for name, record in state.get("services", {}).items():
                print(f"{name:<15} {record.get('status', '-'):<15} {record.get('detail', '')}")
            for note in state.get("notes", []):
                print(f"  {note}")
        if not args.loop:
            return 0
        time.sleep(max(5.0, float(cfg.get("watchdog.interval_seconds", 60) or 60)))


if __name__ == "__main__":
    sys.exit(main())
