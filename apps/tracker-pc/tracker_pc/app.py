"""The tracker process: single-instance lock, run/tracker.json, the HTTP API, the inbox watcher,
retention, the shared evidence setting, and (unless --headless) the floating panel."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import PRODUCT_NAME, SERVICE, VERSION
from .config import OrbitalConfig, TrackerPaths, platform_name, tmp_inbox
from .jsonlog import JSONLineLogger
from .model import Settings, dumps, now_iso, write_atomic
from .relay import HubRelay
from .server import CommandServer, inbox_scan
from .store import StoreError, TrackerStore


class SingleInstance:
    """run/tracker.lock, held until the process exits (flock on macOS/Linux, msvcrt on Windows)."""

    def __init__(self, lock_file: Path) -> None:
        self.lock_file = lock_file
        self._fh = None

    def acquire(self) -> bool:
        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.lock_file, "a+")
        except OSError:
            return True  # can't lock: don't refuse to run
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True


class TrackerApp:
    def __init__(self, cfg: OrbitalConfig | None = None, port: int | None = None, save_delay: float = 0.4,
                 ui_enabled: bool = True) -> None:
        self.cfg = cfg or OrbitalConfig.load()
        self.cfg.ensure_home()
        self.paths = TrackerPaths.from_config(self.cfg)
        self.paths.ensure_directories()
        self.log = JSONLineLogger(self.paths.log_file, "tracker", self.cfg.log_max_bytes, self.cfg.log_backups,
                                  self.cfg.log_level)
        self.port = port if port is not None else self.cfg.tracker_port
        self.store = TrackerStore(self.cfg, self.paths, self.log, save_delay=save_delay)
        self.relay = HubRelay.from_config(self.cfg, self.paths)
        self.settings = Settings.load(self.paths.settings_file)
        self.settings_version = 0
        self._settings_stamp = self._mtime(self.paths.settings_file)
        self.server: CommandServer | None = None
        self.ui_enabled = ui_enabled
        self.ui: Any = None  # tracker_pc.ui.Panel when the window runs
        self.toast: tuple[str, float] | None = None
        self.on_settings_changed: Callable[[], None] | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.started_at = now_iso()

    # Lifecycle

    def start(self) -> None:
        self.store.apply_retention()
        self.server = CommandServer(self, self.port)
        self.port = self.server.port
        self.server.start()
        info = {"service": SERVICE, "version": VERSION, "port": self.port, "pid": os.getpid(),
                "startedAt": self.started_at, "platform": platform_name(), "app": "tracker-pc"}
        write_atomic(self.paths.run_file, dumps(info, pretty=True))
        self.log.info("tracker started", version=VERSION, pid=os.getpid(), port=self.port,
                      machine=self.cfg.machine_name, role=self.cfg.role, platform=platform_name(),
                      config_file=self.cfg.loaded_from_file, relay_to_hub=self.cfg.relays_to_hub,
                      ui=self.ui_enabled)
        self._loop("inbox", 2.0, self.scan_inbox)
        self._loop("settings", 1.5, self.reload_settings_if_changed)
        self._loop("retention", 86_400, self.store.apply_retention, run_first=False)
        if self.relay is not None:
            self._loop("hub-settings", 30, self.pull_hub_settings)
        self.scan_inbox()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self.server is not None:
            try:
                self.server.stop()
            except OSError:
                pass
        self.store.close()
        try:
            self.paths.run_file.unlink()
        except OSError:
            pass
        self.log.info("tracker stopped")

    def wait(self) -> None:
        while not self._stop.wait(0.5):
            pass

    def _loop(self, name: str, every: float, fn: Callable[[], Any], run_first: bool = True) -> None:
        def run() -> None:
            if not run_first and self._stop.wait(every):
                return
            while not self._stop.is_set():
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    self.log.warning(f"{name} pass failed", error=str(exc))
                if self._stop.wait(every):
                    return

        t = threading.Thread(target=run, name=f"tracker-{name}", daemon=True)
        t.start()
        self._threads.append(t)

    # Inbox

    def scan_inbox(self) -> int:
        n = 0
        for folder in (self.paths.inbox_dir, tmp_inbox()):
            n += inbox_scan(folder, self.paths.failed_inbox_dir, self._apply_from_inbox,
                            lambda name, err: self.log.warning("bad inbox file moved to inbox/failed", file=name, error=err))
        return n

    def _apply_from_inbox(self, cmd: dict) -> None:
        try:
            self.store.apply(cmd)
        except StoreError as exc:
            self.log.warning("inbox command failed", op=cmd.get("op"), id=cmd.get("id"), error=str(exc))

    # Shared settings (settings.json)

    @staticmethod
    def _mtime(path: Path) -> float | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def set_evidence(self, level: str, by: str) -> None:
        if level == self.settings.evidence:
            return
        self.settings = Settings(level, now_iso(), by)
        try:
            self.settings.save(self.paths.settings_file)
        except OSError as exc:
            self.show_toast(f"Couldn't save settings: {exc}")
            self.log.error("settings save failed", error=str(exc))
        self._settings_stamp = self._mtime(self.paths.settings_file)
        self.log.info("evidence level changed", evidence=level, by=self.settings.updated_by)
        self._announce_evidence()
        if self.settings.updated_by == "tracker" and self.relay is not None:
            snapshot = self.settings
            threading.Thread(target=self.relay.push_settings, args=(snapshot, "tracker"), daemon=True).start()

    def reload_settings_if_changed(self) -> None:
        stamp = self._mtime(self.paths.settings_file)
        if stamp == self._settings_stamp:
            return
        self._settings_stamp = stamp
        fresh = Settings.load(self.paths.settings_file)
        changed = fresh.evidence != self.settings.evidence
        self.settings = fresh
        if changed:
            self.log.info("evidence level changed", evidence=fresh.evidence, by=fresh.updated_by)
            self._announce_evidence()

    def pull_hub_settings(self) -> None:
        if self.relay is None:
            return
        remote = self.relay.fetch_settings()
        if remote is None:
            return
        winner = Settings.newest(self.settings, remote)
        if winner == self.settings:
            return
        changed = winner.evidence != self.settings.evidence
        self.settings = winner
        try:
            winner.save(self.paths.settings_file)
        except OSError:
            pass
        self._settings_stamp = self._mtime(self.paths.settings_file)
        if changed:
            self._announce_evidence()

    def _announce_evidence(self) -> None:
        self.settings_version += 1
        text = f"📷 Agent screenshots: {({'off': 'Off', 'min1': 'Min 1', 'all': 'All'})[self.settings.evidence]}"
        if self.settings.updated_by == "app":
            text += " · set from Orbital Agents"
        elif self.settings.updated_by == "cli":
            text += " · set from the CLI"
        self.show_toast(text)

    def show_toast(self, text: str) -> None:
        self.toast = (text, time.time())
        self.settings_version += 1

    # Reports

    def health(self) -> dict:
        return {
            "ok": True, "service": SERVICE, "version": VERSION, "app": PRODUCT_NAME,
            "machine": self.cfg.machine_name, "role": self.cfg.role, "port": self.port, "pid": os.getpid(),
            "active": self.store.active_count, "items": len(self.store.items), "evidence": self.settings.evidence,
            "voice": {"enabled": False, "status": "Voice is not available on this platform"},
            "titles": {"mode": "none", "grok": False},
            "relay_to_hub": self.cfg.relays_to_hub,
            "hotkeys": {"enabled": False, "modifiers": "", "symbols": ""},
            "platform": platform_name(), "ui": self.ui is not None,
        }

    def debug_ui(self, obj: dict) -> dict:
        """Testing hook, like the Mac's POST /v1/debug/ui."""
        out: dict[str, Any] = {"ok": True}
        if "toast" in obj and isinstance(obj["toast"], str):
            self.show_toast(obj["toast"])
        if self.ui is not None:
            out.update(self.ui.debug(obj))
        else:
            out["ui"] = False
        return out


def write_install_marker(paths: TrackerPaths, install_dir: str) -> Path:
    """data/tracker/installed.json: tells the satellite this machine has a tracker even while it isn't running."""
    marker = paths.installed_file
    write_atomic(marker, dumps({"app": "tracker-pc", "version": VERSION, "platform": platform_name(),
                                "path": install_dir, "installedAt": now_iso()}, pretty=True))
    return marker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker_pc", description=PRODUCT_NAME + " (Windows / Linux)")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--headless", action="store_true", help="API, inbox and items.json only; no window")
    parser.add_argument("--port", type=int, help="override tracker.port (tests, a second copy)")
    parser.add_argument("--install-marker", action="store_true",
                        help="write data/tracker/installed.json (for the satellite's capabilities) and exit")
    parser.add_argument("--remove-marker", action="store_true", help="delete data/tracker/installed.json and exit")
    args = parser.parse_args(argv)

    if args.version:
        print(VERSION)
        return 0
    cfg = OrbitalConfig.load()
    paths = TrackerPaths.from_config(cfg)
    if args.install_marker:
        paths.ensure_directories()
        print(write_install_marker(paths, str(Path(__file__).resolve().parents[1])))
        return 0
    if args.remove_marker:
        paths.installed_file.unlink(missing_ok=True)
        return 0

    paths.ensure_directories()
    lock = SingleInstance(paths.lock_file)
    if not lock.acquire():
        print("another tracker is already running; exiting", file=sys.stderr)
        return 0
    # The GUI loop owns the main thread, so Python-level signal handlers would never run there.
    # On macOS/Linux, block the stop signals in every thread and take them in one sigwait thread.
    stop_signals = {s for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)) if s is not None}
    use_sigwait = hasattr(signal, "pthread_sigmask") and hasattr(signal, "sigwait")
    if use_sigwait:
        signal.pthread_sigmask(signal.SIG_BLOCK, stop_signals)
    app = TrackerApp(cfg, port=args.port, ui_enabled=not args.headless)
    try:
        app.start()
    except OSError as exc:
        app.log.error("HTTP server failed to start; agents' commands will queue in the inbox",
                      port=app.port, error=str(exc))
        print(f"tracker: port {app.port} is busy ({exc}); is another tracker running?", file=sys.stderr)
        return 1

    def shut_down(*_: Any) -> None:
        """launchd/systemd stop with SIGTERM: save and quit cleanly (exit code 0)."""
        app.log.info("stop signal received")
        threading.Timer(8, lambda: os._exit(0)).start()  # never hang a service stop
        if app.ui is not None:
            app.ui.quit()  # webview.start() returns; the finally below saves
        else:
            app.stop()

    if use_sigwait:
        def wait_for_signal() -> None:
            signal.sigwait(stop_signals)
            shut_down()

        threading.Thread(target=wait_for_signal, name="tracker-signals", daemon=True).start()
    else:  # Windows: Ctrl+C / Ctrl+Break in a console; Task Scheduler ends the process outright
        for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
            if sig is not None:
                try:
                    signal.signal(sig, shut_down)
                except (ValueError, OSError):
                    pass

    if args.headless:
        app.wait()
        return 0
    try:
        from .ui import run_panel
    except ImportError as exc:
        app.log.warning("pywebview missing; running headless", error=str(exc))
        print("tracker: pywebview is not installed (pip install -r requirements.txt); running headless", file=sys.stderr)
        app.wait()
        return 0
    try:
        run_panel(app)  # blocks on the main thread until the window closes
    except Exception as exc:  # noqa: BLE001 - a broken GUI must not take tracking down with it
        app.log.error("panel failed; running headless", error=f"{type(exc).__name__}: {exc}")
        print(f"tracker: the panel failed ({exc}); running headless", file=sys.stderr)
        app.ui = None
        app.wait()
    finally:
        app.stop()
    return 0
