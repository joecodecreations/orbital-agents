"""Janitor: keep ORBITAL_HOME from growing without limit. The watchdog runs it on every pass.

- logs/*.stderr.log and *.stdout.log (written by launchd / systemd, which never roll them): once
  past `logging.max_bytes`, the last `max_bytes` go to <name>.1 (older copies shift to .2 …
  up to `logging.backups`) and the file is truncated in place, so the service keeps writing.
- Rolled logs (*.log.1, *.stderr.log.2, …) older than `logging.retention_days` are deleted.
- Satellite job records and outputs older than `satellite.job_retention_days` (default: the
  logging retention) are deleted; the newest 20 always stay.
- Scheduler outputs (data/scheduler/out, runs) older than `scheduler.output_retention_days`, and
  request files nobody picked up within a day.
- Tracker media the tracker archived under data/tracker/media/ older than
  `tracker.media_retention_days`. items.json is never touched (the tracker app owns it).
- The hub's own data, through orbital.hub.maintenance.trim(cfg) when that module exists.

Nothing outside ORBITAL_HOME is ever deleted: every path is checked before it is removed.
"""

from __future__ import annotations

import importlib
import os
import time
from pathlib import Path
from typing import Any

from orbital.core.config import Config
from orbital.satellite.jobs import trim_job_records

DAY = 86400.0


class OutsideHome(RuntimeError):
    pass


def inside(path: Path, home: Path) -> bool:
    """True when `path` (the entry itself, not where a symlink points) lives inside `home`."""
    try:
        parent = path.parent.resolve()
        root = home.resolve()
    except OSError:
        return False
    return parent == root or root in parent.parents


def safe_unlink(path: Path, home: Path) -> bool:
    if not inside(path, home):
        raise OutsideHome(f"refusing to delete {path}: outside {home}")
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.lstat().st_mtime < cutoff
    except OSError:
        return False


def _files(folder: Path, pattern: str = "*") -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in folder.glob(pattern) if p.is_file() or p.is_symlink()]


# --------------------------------------------------------------------------- logs


def copy_truncate(path: Path, keep_bytes: int, backups: int, home: Path) -> bool:
    """Roll a log another process holds open: shift .1→.2…, copy the tail to .1, truncate in place."""
    if not inside(path, home) or path.is_symlink():
        return False
    backups = max(1, int(backups))
    for n in range(backups, 0, -1):
        src = path.with_name(f"{path.name}.{n}")
        if not src.exists():
            continue
        if n == backups:
            safe_unlink(src, home)
        else:
            os.replace(src, path.with_name(f"{path.name}.{n + 1}"))
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - keep_bytes))
        tail = fh.read(keep_bytes)
    rolled = path.with_name(f"{path.name}.1")
    rolled.write_bytes(tail)
    os.truncate(path, 0)
    return True


def trim_logs(cfg: Config, now: float | None = None) -> dict[str, int]:
    home = cfg.paths.home
    logs = cfg.paths.logs
    now = time.time() if now is None else now
    max_bytes = int(cfg.get("logging.max_bytes", 5_000_000) or 5_000_000)
    backups = int(cfg.get("logging.backups", 3) or 3)
    cutoff = now - float(cfg.get("logging.retention_days", 14) or 14) * DAY
    out = {"rolled": 0, "deleted": 0}
    for path in _files(logs):
        name = path.name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if name.endswith((".stderr.log", ".stdout.log")):
            if size > max_bytes and copy_truncate(path, max_bytes, backups, home):
                out["rolled"] += 1
        elif name.endswith(".log") and size > 4 * max_bytes:
            # A JSON log whose writer does not roll it (or crashed mid-roll): same treatment.
            if copy_truncate(path, max_bytes, backups, home):
                out["rolled"] += 1
    for path in _files(logs):
        stem, _, suffix = path.name.rpartition(".")
        if suffix.isdigit() and stem.endswith(".log") and _older_than(path, cutoff):
            if safe_unlink(path, home):
                out["deleted"] += 1
    return out


# --------------------------------------------------------------------------- data


def trim_scheduler(cfg: Config, now: float | None = None) -> dict[str, int]:
    home = cfg.paths.home
    base = cfg.paths.scheduler_data
    now = time.time() if now is None else now
    cutoff = now - float(cfg.get("scheduler.output_retention_days", 14) or 14) * DAY
    out = {"outputs": 0, "requests": 0}
    for folder in (base / "out", base / "runs"):
        for path in _files(folder):
            if _older_than(path, cutoff) and safe_unlink(path, home):
                out["outputs"] += 1
    for path in _files(base / "requests"):
        if _older_than(path, now - DAY) and safe_unlink(path, home):
            out["requests"] += 1
    for path in _files(base, ".*.tmp"):
        if _older_than(path, now - DAY) and safe_unlink(path, home):
            out["requests"] += 1
    return out


def trim_satellite(cfg: Config) -> dict[str, int]:
    base = cfg.paths.satellite_data
    if not inside(base / "x", cfg.paths.home):
        raise OutsideHome(f"satellite data {base} is outside {cfg.paths.home}")
    days = cfg.get("satellite.job_retention_days") or cfg.get("logging.retention_days", 14) or 14
    return trim_job_records(base, float(days))


def trim_tracker_media(cfg: Config, now: float | None = None) -> dict[str, int]:
    home = cfg.paths.home
    media = cfg.paths.tracker_data / "media"
    now = time.time() if now is None else now
    cutoff = now - float(cfg.get("tracker.media_retention_days", 60) or 60) * DAY
    out = {"files": 0, "folders": 0}
    if not media.is_dir() or media.is_symlink() or not inside(media / "x", home):
        return out
    for root, dirs, files in os.walk(media, topdown=False, followlinks=False):
        folder = Path(root)
        for name in files:
            path = folder / name
            if _older_than(path, cutoff) and safe_unlink(path, home):
                out["files"] += 1
        if folder != media:
            try:
                if not any(folder.iterdir()) and inside(folder, home):
                    folder.rmdir()
                    out["folders"] += 1
            except OSError:
                pass
    return out


def trim_hub(cfg: Config) -> Any:
    """orbital.hub.maintenance.trim(cfg), when the hub is installed here (hub role only)."""
    if cfg.role != "hub":
        return "not a hub"
    try:
        module = importlib.import_module("orbital.hub.maintenance")
    except ImportError:
        return "skipped (orbital.hub.maintenance not available)"
    trim = getattr(module, "trim", None)
    if not callable(trim):
        return "skipped (no trim function)"
    return trim(cfg)


def run_janitor(cfg: Config, now: float | None = None) -> dict[str, Any]:
    """Every trim step; one failing step never stops the others."""
    results: dict[str, Any] = {}
    steps = (
        ("logs", lambda: trim_logs(cfg, now)),
        ("satellite", lambda: trim_satellite(cfg)),
        ("scheduler", lambda: trim_scheduler(cfg, now)),
        ("tracker_media", lambda: trim_tracker_media(cfg, now)),
        ("hub", lambda: trim_hub(cfg)),
    )
    for name, step in steps:
        try:
            results[name] = step()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return results
