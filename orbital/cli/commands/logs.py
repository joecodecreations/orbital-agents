"""`orbital logs`: read and merge the JSON-line logs in ~/.orbital-agents/logs/.

    orbital logs                         last 50 lines from every component, merged by time
    orbital logs satellite -f            follow one component
    orbital logs --level WARNING --since 1h
    orbital logs hub --stderr            the service manager's stdout/stderr file for that component
    orbital logs --list                  every log file with its size and age
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from orbital.core.config import load_config

NAME = "logs"
HELP = "Read the logs: orbital logs [component] [-f] [--level WARNING] [--since 1h] [--lines N] [--list]"

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
BASE_KEYS = ("ts", "level", "component", "msg")
COLORS = {"DEBUG": "\033[2m", "INFO": "", "WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[1;31m"}


def register(p):
    p.add_argument("component", nargs="?", help="hub, satellite, scheduler, watchdog, browser-bridge, tracker, app, wizard, cli, feeds")
    p.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    p.add_argument("--level", default=None, help="only this level and above (DEBUG, INFO, WARNING, ERROR)")
    p.add_argument("--since", default=None, help="only lines newer than this: 30m, 2h, 1d, or 2026-10-01T12:00")
    p.add_argument("-n", "--lines", type=int, default=50, help="how many lines (default 50)")
    p.add_argument("--list", action="store_true", help="list the log files with sizes")
    p.add_argument("--stderr", action="store_true", help="show the service's stdout/stderr file instead")
    p.add_argument("--json", action="store_true", help="print raw JSON lines")


# --------------------------------------------------------------------------- reading


def parse_since(text: str | None, now: float | None = None) -> float | None:
    if not text:
        return None
    now = time.time() if now is None else now
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*", text)
    if match:
        unit = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
        return now - float(match.group(1)) * unit
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"--since {text!r}: use 30m, 2h, 1d or a date like 2026-10-01T12:00")


def parse_ts(stamp: object) -> float | None:
    if not isinstance(stamp, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def component_of(path: Path) -> str:
    name = path.name
    name = re.sub(r"\.\d+$", "", name)
    for suffix in (".stderr.log", ".stdout.log", ".log"):
        if name.endswith(suffix):
            return name[: -len(suffix)] + (suffix[:-4] if suffix != ".log" else "")
    return name


def log_files(logs_dir: Path, component: str | None = None, stderr: bool = False, rolled: bool = True) -> list[Path]:
    """Current files first per component, then rolled ones (.1 newest)."""
    if not logs_dir.is_dir():
        return []
    out = []
    for path in sorted(logs_dir.iterdir()):
        if not path.is_file():
            continue
        name = re.sub(r"\.\d+$", "", path.name)
        is_std = name.endswith((".stderr.log", ".stdout.log"))
        if stderr != is_std or not name.endswith(".log"):
            continue
        if not rolled and path.name != name:
            continue
        base = name[: -len(".stderr.log")] if name.endswith(".stderr.log") else (
            name[: -len(".stdout.log")] if name.endswith(".stdout.log") else name[: -len(".log")])
        if component and base != component:
            continue
        out.append(path)
    return out


def parse_line(line: str, path: Path) -> dict:
    line = line.rstrip("\n")
    try:
        entry = json.loads(line)
        if isinstance(entry, dict) and "msg" in entry:
            entry.setdefault("component", component_of(path))
            entry["_t"] = parse_ts(entry.get("ts"))
            return entry
    except ValueError:
        pass
    return {"ts": None, "level": "INFO", "component": component_of(path), "msg": line, "_t": None, "_raw": True}


def read_entries(files: list[Path], since: float | None = None, min_level: int = 0) -> list[dict]:
    entries = []
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    entry = parse_line(line, path)
                    if not keep(entry, since, min_level):
                        continue
                    entries.append(entry)
        except OSError:
            continue
    entries.sort(key=lambda e: (e["_t"] is not None, e["_t"] or 0))
    return entries


def keep(entry: dict, since: float | None, min_level: int) -> bool:
    if LEVELS.get(str(entry.get("level", "INFO")).upper(), 20) < min_level:
        return False
    if since is not None and (entry["_t"] is None or entry["_t"] < since):
        return False
    return True


def recent_problems(logs_dir: Path, limit: int = 5, since_seconds: float = 86400) -> list[dict]:
    """The last WARNING/ERROR lines across every component (for `orbital status`)."""
    files = log_files(logs_dir, rolled=False)
    entries = read_entries(files, since=time.time() - since_seconds, min_level=30)
    return entries[-limit:]


def format_entry(entry: dict, color: bool = False) -> str:
    when = "                   "
    if entry.get("_t") is not None:
        when = datetime.fromtimestamp(entry["_t"]).strftime("%Y-%m-%d %H:%M:%S")
    level = str(entry.get("level", "INFO")).upper()
    extra = " ".join(f"{k}={_short(v)}" for k, v in entry.items() if k not in BASE_KEYS and not k.startswith("_") and k != "error")
    text = f"{when}  {str(entry.get('component', '')):<14} {level:<7} {entry.get('msg', '')}"
    if extra:
        text += f"  {extra}"
    if entry.get("error"):
        text += "\n" + "\n".join("    " + line for line in str(entry["error"]).splitlines()[-8:])
    if color and COLORS.get(level):
        text = COLORS[level] + text + "\033[0m"
    return text


def _short(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= 120 else text[:117] + "…"


def follow(files_fn, since: float | None, min_level: int, as_json: bool, color: bool) -> Iterator[str]:
    positions: dict[Path, int] = {}
    for path in files_fn():
        try:
            positions[path] = path.stat().st_size
        except OSError:
            pass
    while True:
        for path in files_fn():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            pos = positions.get(path, 0)
            if size < pos:            # rolled or truncated: start over
                pos = 0
            if size == pos:
                positions[path] = size
                continue
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                positions[path] = fh.tell()
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                entry = parse_line(line, path)
                if keep(entry, since, min_level):
                    yield line if as_json else format_entry(entry, color)
        time.sleep(0.5)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def run(args) -> int:
    cfg = load_config()
    logs_dir = cfg.paths.logs
    if args.list:
        files = sorted(logs_dir.iterdir()) if logs_dir.is_dir() else []
        total = 0
        for path in files:
            if not path.is_file():
                continue
            st = path.stat()
            total += st.st_size
            age = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"{path.name:<36} {human_size(st.st_size):>10}  {age}")
        print(f"{'total':<36} {human_size(total):>10}  in {logs_dir}")
        return 0
    try:
        since = parse_since(args.since)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    min_level = LEVELS.get(str(args.level or "DEBUG").upper())
    if min_level is None:
        print(f"--level must be one of {', '.join(LEVELS)}", file=sys.stderr)
        return 2
    color = sys.stdout.isatty()
    files = log_files(logs_dir, args.component, stderr=args.stderr)
    if not files and not args.follow:
        where = f" for {args.component}" if args.component else ""
        print(f"no logs{where} in {logs_dir} yet (orbital logs --list shows what is there)")
        return 0
    entries = read_entries(files, since, min_level)
    for entry in entries[-max(1, args.lines):]:
        if args.json and not entry.get("_raw"):
            print(json.dumps({k: v for k, v in entry.items() if not k.startswith("_")}, ensure_ascii=False))
        else:
            print(format_entry(entry, color))
    if args.follow:
        try:
            for line in follow(lambda: log_files(logs_dir, args.component, stderr=args.stderr, rolled=False),
                               since, min_level, args.json, color):
                print(line, flush=True)
        except KeyboardInterrupt:
            return 0
    return 0
