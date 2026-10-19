"""One scheduler for every feed: runs the jobs in ~/.orbital-agents/jobs.json on their timetable.

    python -m orbital.scheduler                 # run (the io.orbitalagents.scheduler service does this)
    python -m orbital.scheduler --plan          # every job, its schedule, next run, last result
    python -m orbital.scheduler --run <id> [<id>…] [--wait]   # queue a run through the running scheduler
    python -m orbital.scheduler --self-test
    orbital jobs | orbital jobs plan | orbital jobs run <id> --wait

How it runs a feed:

- **Schedule.** Every `every` seconds on a fixed grid shifted by `offset` from the top of the hour:
  `every 900, offset 120` runs at :02, :17, :32, :47. A run that lands within a third of an interval
  before a slot (a manual run, the run at start) skips that slot.
- **After a restart or sleep.** A job whose slot passed while the machine was off runs once, not
  once per missed slot. A job that never ran runs at start.
- **Lanes.** Jobs that share something (an account, a browser profile, a rate limit) share a lane;
  a lane runs one job at a time. Lanes run side by side, at most `max_parallel` at once.
- **Timeouts.** A run past its `timeout` gets SIGTERM, then SIGKILL 10 s later, to its whole
  process group, and is recorded as an error.
- **Watched files.** `watch: ["~/somefile"]` runs the job about 3 s after one of them changes.
- **Outcome.** The exit code plus the feed's own result line (`key=value` pairs): `ok`, `warn`
  (finished, but a part reported `error`, `unavailable`, `timeout`, …) or `error` (crashed, timed
  out, could not start), with a one-line summary and the issues it printed.
- **Output.** Each run's stdout and stderr go to data/scheduler/out/<job>.log (capped per run,
  rolled when the file passes `scheduler.max_output_bytes`), and the last run's tail to
  data/scheduler/runs/<job>.log, which `GET /v1/jobs/{id}/log` serves.
- **Environment.** Each run gets ORBITAL_HOME, ORBITAL_JOB_ID, ORBITAL_RUN_REASON (`schedule`,
  `startup`, `catch-up`, `manual`, `refresh-all`, `watch`, `restart`), ORBITAL_SCHEDULE_LABEL,
  ORBITAL_SCHEDULE_EVERY and ORBITAL_NEXT_RUN_AT, and the repo on PYTHONPATH so feeds can
  `from orbital.feeds import HubClient`.

The hub never runs a feed. Its `/v1/jobs` endpoints read data/scheduler/state.json (key
`snapshot`) and write request files into data/scheduler/requests/, which this process picks up
within a second: `{"type": "run", "jobs": [...]}`, `{"type": "run_all", "group": ...}`,
`{"type": "settings", "job", "every"?, "paused"?}`, `{"type": "cancel", "job"}`.
After every change it calls `POST <hub>/v1/jobs/notify` so the apps refresh.

jobs.json (see feeds/jobs.example.json):

    {"version": 1, "max_parallel": 2,
     "groups": [{"id": "home-lab", "title": "Home Lab", "icon": "server.rack"}],
     "lanes": {"web": {"title": "Outbound web requests"}},
     "jobs": [{"id": "uptime", "name": "Website uptime", "group": "home-lab", "lane": "web",
               "every": 300, "offset": 30, "timeout": 120, "cwd": "{repo}",
               "argv": ["{python}", "feeds/examples/website_uptime.py"],
               "targets": [{"section": "website-uptime"}], "env": {}, "watch": []}]}

In argv, cwd, env and watch: `~/` is your home folder, `{repo}` this repository, `{python}` the
Python running the scheduler, `{orbital_home}` ORBITAL_HOME.
"""

from __future__ import annotations

import collections
import json
import os
import re
import secrets
import shutil
try:
    import fcntl
except ImportError:          # Windows: the single-instance lock uses msvcrt instead
    fcntl = None
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from orbital.core.config import Config, load_config
from orbital.core.logs import get_logger
from orbital.core.paths import REPO_ROOT
from orbital.core.secrets import Secrets
from orbital.satellite.agents import search_path

SCHEMA = "orbital.jobs.v1"
TICK = 1.0
HEARTBEAT_EVERY = 10.0
WATCH_DEBOUNCE = 3.0
HISTORY = 24
TAIL_LINES = 400
KILL_GRACE = 10.0
MIN_EVERY = 10
DEFAULT_OUTPUT_CAP = 1_000_000
EVERY_CHOICES = (60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)


def now_ts() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def atomic_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# --------------------------------------------------------------------------- placeholders


def expand(value: str, home: Path | None = None) -> str:
    """`~/` → home folder, `{repo}` → this repository, `{python}` → this Python, `{orbital_home}`."""
    if not isinstance(value, str):
        return value
    out = value.replace("{repo}", str(REPO_ROOT)).replace("{python}", sys.executable)
    if home is not None:
        out = out.replace("{orbital_home}", str(home))
    if out == "~" or out.startswith("~/"):
        out = os.path.expanduser(out)
    return out


def expand_path_list(value: str, home: Path | None = None) -> str:
    return os.pathsep.join(expand(part, home) for part in value.split(os.pathsep))


# --------------------------------------------------------------------------- schedule math


def next_slot(after: float, every: int, offset: int) -> float:
    """First grid time strictly after `after`. The grid is offset + k*every from the epoch,
    which lines up with the top of the hour for any `every` that divides 3600."""
    every = max(1, int(every))
    k = int((after - offset) // every) + 1
    return k * every + offset


def next_due(started: float, every: int, offset: int) -> float:
    """When a job that started at `started` runs next: the next grid slot, unless that slot is
    closer than a third of the interval (a start-up or manual run just before a slot), then the one after."""
    slot = next_slot(started, every, offset)
    if slot - started < every / 3:
        slot = next_slot(slot, every, offset)
    return slot


def every_label(every: int) -> str:
    if every < 60:
        return f"every {every} s"
    if every < 3600:
        minutes = every // 60
        return "every minute" if minutes == 1 else f"every {minutes} min"
    hours = every / 3600
    if hours == 1:
        return "hourly"
    if hours == int(hours):
        return f"every {int(hours)} h"
    return f"every {hours:g} h"


def schedule_label(every: int, offset: int) -> str:
    """'Every 15 min (:02, :17, :32, :47)', 'Hourly at :07', 'Every minute'."""
    base = every_label(every)
    if every < 600 or every > 3600 or 3600 % every:
        return base[:1].upper() + base[1:]
    minutes = sorted({((offset + k * every) % 3600) // 60 for k in range(3600 // every)})
    marks = ", ".join(f":{m:02d}" for m in minutes)
    if every == 3600:
        return f"Hourly at {marks}"
    return f"{base[:1].upper() + base[1:]} ({marks})"


# --------------------------------------------------------------------------- run outcome

BAD_VALUES = {
    "signed_out": "signed out",
    "wrong_account": "signed in as the wrong account",
    "timeout": "timed out",
    "failed": "failed",
    "error": "error",
    "unavailable": "unavailable",
    "unreachable": "unreachable",
    "not_connected": "not connected",
    "quota": "quota hit",
    "rate_limited": "rate limited",
    "offline": "offline",
    "down": "down",
    "denied": "access denied",
    "unauthorized": "unauthorized",
    "forbidden": "forbidden",
    "missing": "missing",
    "stale": "stale",
    "skip": "not read",
}
ANY_PAIR = re.compile(r"(?<![\w-])[a-z][a-z0-9_.-]{0,30}=\S")
KV_TOKEN = re.compile(r"(?<![\w-])([a-z][a-z0-9_.-]{1,30})=([a-z_]+)\b")
DETAIL_LINE = re.compile(r"^(?:\S+\s+)?([a-z][a-z0-9_-]{1,30}): (" + "|".join(BAD_VALUES) + r")\b[ :]*(.*)$")
TIMESTAMP = re.compile(r"^\[?\d{4}-\d{2}-\d{2}[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?\]?\s*")
COUNTED = [
    (re.compile(r"\berrors[ =](\d+)"), "{n} source errors"),
    (re.compile(r"\b(\d+) failed\b"), "{n} failed"),
    (re.compile(r"\bproblems[ =](\d+)"), "{n} problems"),
]
KV_SKIP = {"code", "full", "profile", "revision", "alerts", "new", "state", "status", "source_id", "id", "level"}


def nice(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").capitalize()


def classify(lines: list[str], exit_code: int | None, timed_out: bool = False) -> dict:
    """ok / warn / error, a one-line summary, and every issue the run printed."""
    text_lines = [line.rstrip() for line in lines if line.strip()]
    issues: list[str] = []
    seen: set[str] = set()

    def add(issue: str) -> None:
        key = issue.lower()
        if key not in seen:
            seen.add(key)
            issues.append(issue[:240])

    details: dict[str, str] = {}
    for line in text_lines:
        body = TIMESTAMP.sub("", line)
        match = DETAIL_LINE.match(body)
        if match and match.group(3):
            details[match.group(1)] = match.group(3).strip()
    last_result = ""
    for line in reversed(text_lines):
        body = TIMESTAMP.sub("", line)
        if body.startswith(("hint:", "Traceback", "  File")):
            continue
        last_result = body
        break
    # The feed's own result line (three or more key=value pairs) beats a detail line that
    # stderr happened to write after it.
    for line in reversed(text_lines):
        body = TIMESTAMP.sub("", line)
        if len(ANY_PAIR.findall(body)) >= 3:
            last_result = body
            break
    result_lines = [TIMESTAMP.sub("", line) for line in text_lines if len(ANY_PAIR.findall(line)) >= 3]
    if last_result not in result_lines:
        result_lines.append(last_result)
    for line in result_lines[-6:]:
        for key, value in KV_TOKEN.findall(line):
            if key in KV_SKIP or value not in BAD_VALUES:
                continue
            add(f"{nice(key)}: {details.get(key) or BAD_VALUES[value]}")
    if re.search(r"\bok=(False|false)\b", last_result):
        code = re.search(r"\bcode=([\w-]+)", last_result)
        add(f"Read failed{': ' + code.group(1).replace('_', ' ') if code and code.group(1) != '-' else ''}")
    for pattern, template in COUNTED:
        found = pattern.search(last_result)
        if found and int(found.group(1)) > 0:
            add(template.format(n=found.group(1)))
    traceback_tail = ""
    for index, line in enumerate(text_lines):
        if line.startswith("Traceback"):
            tail = [row for row in text_lines[index:] if row and not row.startswith(" ")]
            traceback_tail = tail[-1] if tail else ""
        if re.match(r"^HTTP \d{3} ", line):
            traceback_tail = traceback_tail or line

    summary = re.sub(r"\s+", " ", last_result)[:160]
    if timed_out:
        status = "error"
        summary = "Timed out and was stopped"
        add("Run passed its time limit; the scheduler stopped it")
    elif exit_code not in (0, None):
        status = "error"
        reason = traceback_tail or last_result or f"exit code {exit_code}"
        summary = re.sub(r"\s+", " ", reason)[:160]
        add(f"Exited with code {exit_code}: {summary}")
    elif issues or traceback_tail:
        status = "warn"
        if traceback_tail:
            add(traceback_tail)
    else:
        status = "ok"
    if not summary:
        summary = {"ok": "Finished", "warn": "Finished with problems", "error": "Failed"}[status]
    return {"status": status, "summary": summary, "issues": issues[:12]}


# --------------------------------------------------------------------------- registry


class Job:
    def __init__(self, raw: dict, order: int, home: Path | None = None):
        self.raw = raw
        self.order = order
        self.id: str = str(raw["id"])
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", self.id):
            raise ValueError(f"job id {self.id!r} must be lowercase letters, digits, - or _")
        self.name: str = raw.get("name") or self.id
        self.group: str = raw.get("group") or ""
        self.lane: str = raw.get("lane") or self.id
        self.default_every = max(MIN_EVERY, int(raw["every"]))
        self.offset = int(raw.get("offset") or 0)
        self.timeout = float(raw.get("timeout") or 600)
        argv = raw["argv"]
        if isinstance(argv, str) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError(f"job {self.id}: argv must be a list of strings")
        self.argv = [expand(part, home) for part in argv]
        self.cwd = expand(raw.get("cwd") or "~", home)
        self.env = {str(key): expand_path_list(str(value), home) if key == "PATH" else expand(str(value), home)
                    for key, value in (raw.get("env") or {}).items()}
        self.watch = [Path(expand(path, home)) for path in raw.get("watch") or []]
        self.quiet_history = bool(raw.get("quiet_history"))
        self.enabled = raw.get("enabled", True) is not False


def load_registry(path: Path, home: Path | None = None) -> tuple[dict, list[Job]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("jobs", []), list):
        raise ValueError("jobs.json must be an object with a \"jobs\" list")
    jobs = [Job(row, index, home) for index, row in enumerate(raw.get("jobs") or [])]
    ids = [job.id for job in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("jobs.json has a duplicate job id")
    lanes = raw.setdefault("lanes", {})
    for job in jobs:
        lanes.setdefault(job.lane, {"title": job.lane})
    return raw, jobs


# --------------------------------------------------------------------------- one run


def roll_log(path: Path, cap: int) -> None:
    """Keep one rolled copy (<job>.log.1) once a job's output log passes `cap` bytes."""
    try:
        if path.stat().st_size > cap:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


class Run:
    def __init__(self, job: Job, reason: str, env_extra: dict[str, str], out_log: Path, cap: int):
        self.job = job
        self.reason = reason
        self.started = now_ts()
        self.tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self.lock = threading.Lock()
        self.killed_at: float | None = None
        self.timed_out = False
        self.cap = cap
        self.written = 0
        self.truncated = False
        env = dict(os.environ)
        env.setdefault("HOME", str(Path.home()))
        env["PATH"] = search_path(env.get("PATH", ""))
        env.update(job.env)
        env.update(env_extra)
        env["PYTHONUNBUFFERED"] = "1"
        py_path = env.get("PYTHONPATH", "")
        if str(REPO_ROOT) not in py_path.split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT), py_path) if p)
        out_log.parent.mkdir(parents=True, exist_ok=True)
        roll_log(out_log, cap)
        self.handle = out_log.open("a", encoding="utf-8")
        self.handle.write(f"\n===== {iso(self.started)} {job.id} ({reason}) =====\n")
        self.handle.flush()
        try:
            self.proc = subprocess.Popen(
                job.argv, cwd=job.cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
        except OSError:
            self.handle.close()
            raise
        self.threads = [
            threading.Thread(target=self._pump, args=(self.proc.stdout, ""), daemon=True),
            threading.Thread(target=self._pump, args=(self.proc.stderr, "[stderr] "), daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _pump(self, stream, prefix: str) -> None:
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                with self.lock:
                    self.tail.append(line.rstrip("\n")[:4000])
                    if self.written < self.cap:
                        text = prefix + line
                        self.handle.write(text)
                        self.handle.flush()
                        self.written += len(text.encode("utf-8", errors="replace"))
                    elif not self.truncated:
                        self.truncated = True
                        self.handle.write(f"… [output past {self.cap} bytes for this run is not kept here; "
                                          f"the last {TAIL_LINES} lines are in runs/{self.job.id}.log]\n")
                        self.handle.flush()
        except (OSError, ValueError):
            pass

    def lines(self) -> list[str]:
        with self.lock:
            return list(self.tail)

    def close(self) -> None:
        with self.lock:
            try:
                self.handle.close()
            except OSError:
                pass

    def stop(self, sig: int) -> None:
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


# --------------------------------------------------------------------------- scheduler


class Scheduler:
    def __init__(self, cfg: Config | None = None, registry_path: Path | None = None, log=None, notify: bool = True):
        self.cfg = cfg or load_config()
        self.paths = self.cfg.paths
        self.home = self.paths.home
        self.dir = self.paths.scheduler_data
        self.state_file = self.dir / "state.json"
        self.settings_file = self.dir / "settings.json"
        self.requests_dir = self.dir / "requests"
        self.runs_dir = self.dir / "runs"
        self.out_dir = self.dir / "out"
        for d in (self.dir, self.requests_dir, self.runs_dir, self.out_dir, self.paths.run):
            d.mkdir(parents=True, exist_ok=True)
        self.registry_path = Path(registry_path) if registry_path else self.paths.jobs_file
        self.log = log or get_logger("scheduler", home=str(self.home))
        self.output_cap = int(self.cfg.get("scheduler.max_output_bytes", DEFAULT_OUTPUT_CAP) or DEFAULT_OUTPUT_CAP)
        self.notify_enabled = notify
        self.registry_mtime: float | None = None
        self.registry_error: str | None = None
        self.meta: dict = {}
        self.jobs: list[Job] = []
        self.by_id: dict[str, Job] = {}
        self.state = read_json(self.state_file, {})
        if not isinstance(self.state, dict):
            self.state = {}
        self.state.setdefault("jobs", {})
        self.settings = read_json(self.settings_file, {"jobs": {}})
        if not isinstance(self.settings, dict):
            self.settings = {"jobs": {}}
        self.settings.setdefault("jobs", {})
        self.queue: list[tuple[str, str, float]] = []  # job id, reason, queued at
        self.running: dict[str, Run] = {}
        self.watch_sigs: dict[str, tuple] = {}
        self.watch_pending: dict[str, float] = {}
        self.dirty = True
        self.last_write = 0.0
        self.started_at = now_ts()
        self.notify_event = threading.Event()
        self.stop_requested = False
        self.reload_registry(force=True)
        self._adopt_after_restart()

    def info(self, msg: str, **fields) -> None:
        self.log.info(msg, extra={"fields": fields} if fields else None)

    # ---- registry and settings

    def reload_registry(self, force: bool = False) -> None:
        try:
            mtime = self.registry_path.stat().st_mtime
        except OSError:
            if self.jobs or force:
                if self.jobs:
                    self.info("jobs.json is gone; no jobs to run", path=str(self.registry_path))
                self.meta, self.jobs, self.by_id = {}, [], {}
                self.registry_error = None if not self.registry_path.exists() else self.registry_error
                self.registry_mtime = None
                self.dirty = True
            return
        if not force and mtime == self.registry_mtime:
            return
        try:
            meta, jobs = load_registry(self.registry_path, self.home)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.registry_error = f"{type(exc).__name__}: {exc}"
            self.log.error("jobs.json not loaded", extra={"fields": {"error": self.registry_error}})
            self.registry_mtime = mtime
            self.dirty = True
            return
        self.registry_error = None
        self.meta, self.jobs = meta, jobs
        self.by_id = {job.id: job for job in jobs}
        self.registry_mtime = mtime
        for job in jobs:
            self.state["jobs"].setdefault(job.id, {})
        self.queue = [row for row in self.queue if row[0] in self.by_id]
        self.dirty = True
        self.info("jobs.json loaded", jobs=len(jobs))

    def max_parallel(self) -> int:
        return max(1, int(self.meta.get("max_parallel") or self.cfg.get("scheduler.max_parallel", 2) or 2))

    def every(self, job: Job) -> int:
        override = (self.settings["jobs"].get(job.id) or {}).get("every")
        return int(override) if override else job.default_every

    def paused(self, job: Job) -> bool:
        return not job.enabled or bool((self.settings["jobs"].get(job.id) or {}).get("paused"))

    def _adopt_after_restart(self) -> None:
        """A run that was in flight when the scheduler stopped is closed out, never left 'running'."""
        for job_id, record in self.state["jobs"].items():
            if record.get("state") == "running":
                pid = record.get("pid")
                if pid:
                    try:
                        os.killpg(int(pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, ValueError, OSError):
                        pass
                record["pid"] = None
                record.setdefault("history", []).append({"at": record.get("started_at"), "status": "warn", "duration_s": None,
                                                         "summary": "Interrupted by a scheduler restart; ran again",
                                                         "reason": record.get("reason")})
                del record["history"][:-HISTORY]
                record["state"] = "queued"
                record["queued_reason"] = "restart"
                record["queued_at"] = iso(now_ts())
                if job_id in self.by_id:
                    self.queue.append((job_id, "restart", now_ts()))
                else:
                    record["state"] = "idle"
            elif record.get("state") == "queued":
                if job_id in self.by_id:
                    self.queue.append((job_id, record.get("queued_reason") or "manual", now_ts()))
                else:
                    record["state"] = "idle"

    # ---- requests from the hub (files in data/scheduler/requests)

    def take_requests(self) -> None:
        try:
            names = sorted(name for name in os.listdir(self.requests_dir) if name.endswith(".json"))
        except FileNotFoundError:
            return
        for name in names:
            path = self.requests_dir / name
            request = read_json(path, None)
            try:
                path.unlink()
            except OSError:
                pass
            if isinstance(request, dict):
                self.handle_request(request)

    def handle_request(self, request: dict) -> None:
        kind = request.get("type")
        by = str(request.get("by") or "app")[:40]
        if kind == "run":
            ids = [job_id for job_id in request.get("jobs") or [] if job_id in self.by_id]
            for job_id in ids:
                self.enqueue(job_id, "manual", front=True)
            self.info("request run", jobs=ids, by=by)
        elif kind == "run_all":
            group = request.get("group")
            ids = [job.id for job in self.jobs if (not group or job.group == group) and job.enabled]
            for job_id in ids:
                self.enqueue(job_id, "refresh-all")
            self.state["batch"] = {
                "id": secrets.token_hex(4),
                "group": group,
                "requested_at": iso(now_ts()),
                "by": by,
                "jobs": ids,
                "stages": self.stages(ids),
                "finished_at": None,
            }
            self.info("request refresh", group=group or "all", jobs=len(ids), by=by)
        elif kind == "settings":
            job_id = request.get("job")
            if job_id not in self.by_id:
                return
            row = self.settings["jobs"].setdefault(job_id, {})
            if "every" in request:
                every = request.get("every")
                if every is None or int(every) == self.by_id[job_id].default_every:
                    row.pop("every", None)
                elif int(every) in EVERY_CHOICES:
                    row["every"] = int(every)
            if "paused" in request:
                if request.get("paused"):
                    row["paused"] = True
                else:
                    row.pop("paused", None)
            if not row:
                self.settings["jobs"].pop(job_id, None)
            atomic_write(self.settings_file, self.settings)
            self.info("settings", job=job_id, every=self.every(self.by_id[job_id]),
                      paused=self.paused(self.by_id[job_id]), by=by)
        elif kind == "cancel":
            job_id = request.get("job")
            self.queue = [row for row in self.queue if row[0] != job_id]
            record = self.state["jobs"].get(job_id) or {}
            if record.get("state") == "queued":
                record["state"] = "idle"
            run = self.running.get(job_id)
            if run:
                run.killed_at = now_ts()
                run.stop(signal.SIGTERM)
            self.info("request cancel", job=job_id, by=by)
        self.dirty = True

    def stages(self, ids: list[str]) -> list[list[str]]:
        """Refresh-all order: lanes side by side, a lane's jobs one after another. Stage n is the
        nth job of every lane."""
        per_lane: dict[str, list[str]] = {}
        for job_id in ids:
            per_lane.setdefault(self.by_id[job_id].lane, []).append(job_id)
        depth = max((len(rows) for rows in per_lane.values()), default=0)
        return [[rows[n] for rows in per_lane.values() if len(rows) > n] for n in range(depth)]

    # ---- queueing

    def enqueue(self, job_id: str, reason: str, front: bool = False) -> None:
        record = self.state["jobs"].setdefault(job_id, {})
        if job_id in self.running:
            # One more pass after this one, for a manual tap or a watched edit during the run.
            if reason in ("manual", "watch", "refresh-all"):
                record["rerun"] = reason
            return
        if any(row[0] == job_id for row in self.queue):
            if front:
                self.queue.sort(key=lambda row: row[0] != job_id)
            return
        row = (job_id, reason, now_ts())
        if front:
            self.queue.insert(0, row)
        else:
            self.queue.append(row)
        record["state"] = "queued"
        record["queued_reason"] = reason
        record["queued_at"] = iso(row[2])
        self.dirty = True

    def due_jobs(self, now: float) -> None:
        for job in self.jobs:
            if self.paused(job) or job.id in self.running or any(row[0] == job.id for row in self.queue):
                continue
            record = self.state["jobs"].get(job.id) or {}
            started = record.get("started_ts")
            if started is None:
                self.enqueue(job.id, "startup")
                continue
            due = next_due(float(started), self.every(job), job.offset)
            if now >= due:
                missed = now - due > max(120, self.every(job))
                self.enqueue(job.id, "catch-up" if missed else "schedule")

    def watch_signature(self, job: Job) -> tuple:
        parts = []
        for path in job.watch:
            try:
                parts.append((str(path), path.stat().st_mtime_ns))
                if path.is_dir():
                    for child in sorted(path.iterdir()):
                        if child.suffix != ".tmp":
                            parts.append((str(child), child.stat().st_mtime_ns))
            except OSError:
                parts.append((str(path), None))
        return tuple(parts)

    def check_watches(self, now: float) -> None:
        for job in self.jobs:
            if not job.watch or self.paused(job):
                continue
            sig = self.watch_signature(job)
            if job.id not in self.watch_sigs:
                self.watch_sigs[job.id] = sig
                continue
            if sig != self.watch_sigs[job.id]:
                self.watch_sigs[job.id] = sig
                self.watch_pending[job.id] = now
            pending = self.watch_pending.get(job.id)
            if pending and now - pending >= WATCH_DEBOUNCE:
                self.watch_pending.pop(job.id, None)
                self.enqueue(job.id, "watch", front=True)

    # ---- running

    def start_ready(self) -> None:
        cap = self.max_parallel()
        busy_lanes = {run.job.lane for run in self.running.values()}
        for job_id, reason, _ in list(self.queue):
            if len(self.running) >= cap:
                break
            job = self.by_id.get(job_id)
            if job is None:
                self.queue = [row for row in self.queue if row[0] != job_id]
                continue
            if job.lane in busy_lanes:
                continue
            self.queue = [row for row in self.queue if row[0] != job_id]
            self.launch(job, reason)
            busy_lanes.add(job.lane)

    def env_for(self, job: Job, reason: str, started: float) -> dict[str, str]:
        upcoming = next_due(started, self.every(job), job.offset)
        return {
            "ORBITAL_HOME": str(self.home),
            "ORBITAL_JOB_ID": job.id,
            "ORBITAL_RUN_REASON": reason,
            "ORBITAL_SCHEDULE_LABEL": schedule_label(self.every(job), job.offset) + ", by the Orbital Agents scheduler",
            "ORBITAL_SCHEDULE_EVERY": str(self.every(job)),
            "ORBITAL_NEXT_RUN_AT": iso(upcoming) or "",
        }

    def launch(self, job: Job, reason: str) -> None:
        record = self.state["jobs"].setdefault(job.id, {})
        started = now_ts()
        if job.watch:
            self.watch_sigs[job.id] = self.watch_signature(job)
        try:
            run = Run(job, reason, self.env_for(job, reason, started), self.out_dir / f"{job.id}.log", self.output_cap)
        except OSError as exc:
            record.update({"state": "idle", "started_ts": started, "started_at": iso(started), "reason": reason, "pid": None})
            self.finish_record(job, record, started, None,
                               {"status": "error", "summary": f"Could not start: {exc}", "issues": [str(exc)]}, reason)
            return
        self.running[job.id] = run
        record.update({
            "state": "running",
            "pid": run.proc.pid,
            "started_ts": started,
            "started_at": iso(started),
            "reason": reason,
            "queued_at": None,
            "queued_reason": None,
        })
        self.dirty = True

    def poll_running(self, now: float) -> None:
        for job_id, run in list(self.running.items()):
            code = run.proc.poll()
            if code is None:
                age = now - run.started
                if run.killed_at is None and age > run.job.timeout:
                    run.timed_out = True
                    run.killed_at = now
                    run.stop(signal.SIGTERM)
                elif run.killed_at is not None and now - run.killed_at > KILL_GRACE:
                    run.stop(signal.SIGKILL)
                continue
            for thread in run.threads:
                thread.join(timeout=2)
            run.close()
            del self.running[job_id]
            job = run.job
            lines = run.lines()
            outcome = classify(lines, code, timed_out=run.timed_out)
            if run.killed_at and not run.timed_out:
                outcome = {"status": "error", "summary": "Stopped on request", "issues": []}
            record = self.state["jobs"].setdefault(job_id, {})
            record["state"] = "idle"
            record["pid"] = None
            record["output_truncated"] = run.truncated
            self.finish_record(job, record, run.started, code, outcome, run.reason)
            try:
                header = f"# {job.name} · {iso(run.started)} · {run.reason} · exit {code} · {outcome['status']}\n"
                (self.runs_dir / f"{job_id}.log").write_text(header + "\n".join(lines[-TAIL_LINES:]) + "\n", encoding="utf-8")
            except OSError:
                pass
            rerun = record.pop("rerun", None)
            if rerun and job_id in self.by_id:
                self.enqueue(job_id, rerun, front=True)
            elif job.watch and self.watch_signature(job) != self.watch_sigs.get(job.id):
                self.watch_pending[job.id] = now

    def finish_record(self, job: Job, record: dict, started: float, code: int | None, outcome: dict, reason: str) -> None:
        finished = now_ts()
        last = {
            "started_at": iso(started),
            "finished_at": iso(finished),
            "duration_s": round(finished - started, 1),
            "exit_code": code,
            "status": outcome["status"],
            "summary": outcome["summary"],
            "issues": outcome["issues"],
            "reason": reason,
        }
        previous = record.get("last") or {}
        record["last"] = last
        if outcome["status"] == "ok":
            record["last_ok_at"] = last["finished_at"]
            record["failing_since"] = None
        elif not record.get("failing_since"):
            record["failing_since"] = last["started_at"]
        history = record.setdefault("history", [])
        if not (job.quiet_history and previous.get("status") == outcome["status"] and history):
            history.append({"at": last["started_at"], "status": outcome["status"], "duration_s": last["duration_s"],
                            "summary": outcome["summary"][:120], "reason": reason})
            del history[:-HISTORY]
        else:
            history[-1]["at"] = last["started_at"]
        record["runs"] = int(record.get("runs") or 0) + 1
        if not job.quiet_history or previous.get("status") != outcome["status"]:
            level = {"ok": "info", "warn": "warning"}.get(outcome["status"], "error")
            getattr(self.log, level)(f"{job.id} {outcome['status']}", extra={"fields": {
                "job": job.id, "status": outcome["status"], "duration_s": last["duration_s"], "reason": reason,
                "summary": outcome["summary"][:200], "exit_code": code}})
        self.close_batch()
        self.dirty = True

    def close_batch(self) -> None:
        batch = self.state.get("batch")
        if not batch or batch.get("finished_at"):
            return
        requested = batch.get("requested_at") or ""
        for job_id in batch.get("jobs") or []:
            record = self.state["jobs"].get(job_id) or {}
            last = record.get("last") or {}
            if record.get("state") != "idle" or (last.get("started_at") or "") < requested:
                return
        batch["finished_at"] = iso(now_ts())
        statuses = collections.Counter((self.state["jobs"].get(job_id, {}).get("last") or {}).get("status")
                                       for job_id in batch["jobs"])
        self.info("refresh finished", batch=batch["id"], statuses=dict(statuses))

    # ---- snapshot (what /v1/jobs serves)

    def snapshot(self, now: float) -> dict:
        jobs_out = []
        for job in self.jobs:
            record = self.state["jobs"].get(job.id) or {}
            every = self.every(job)
            started = record.get("started_ts")
            if record.get("state") in ("running", "queued") or self.paused(job):
                next_at = None
            elif started is None:
                next_at = now
            else:
                next_at = max(next_due(float(started), every, job.offset), now)
            queue_index = next((index for index, row in enumerate(self.queue) if row[0] == job.id), None)
            jobs_out.append({
                "id": job.id,
                "name": job.name,
                "group": job.group,
                "reads": job.raw.get("reads") or "",
                "targets": job.raw.get("targets") or [],
                "lane": job.lane,
                "every_s": every,
                "default_every_s": job.default_every,
                "offset_s": job.offset,
                "schedule": schedule_label(every, job.offset),
                "watches": bool(job.watch),
                "enabled": job.enabled,
                "paused": self.paused(job),
                "timeout_s": int(job.timeout),
                "state": record.get("state") or "idle",
                "queued_reason": record.get("queued_reason"),
                "queue_position": None if queue_index is None else queue_index + 1,
                "started_at": record.get("started_at") if record.get("state") == "running" else None,
                "next_run_at": iso(next_at),
                "last": record.get("last"),
                "last_ok_at": record.get("last_ok_at"),
                "failing_since": record.get("failing_since"),
                "history": (record.get("history") or [])[-HISTORY:],
                "logs": [f"data/scheduler/out/{job.id}.log", f"data/scheduler/runs/{job.id}.log"],
            })
        lanes_out = []
        for lane_id, lane in (self.meta.get("lanes") or {}).items():
            lanes_out.append({
                "id": lane_id,
                "title": (lane or {}).get("title") or lane_id,
                "kind": (lane or {}).get("kind") or "local",
                "busy_with": next((run.job.id for run in self.running.values() if run.job.lane == lane_id), None),
            })
        return {
            "schema": SCHEMA,
            "scheduler": {
                "pid": os.getpid(),
                "host": socket.gethostname().split(".")[0],
                "machine": self.cfg.machine_name,
                "started_at": iso(self.started_at),
                "heartbeat_at": iso(now),
                "max_parallel": self.max_parallel(),
                "running": len(self.running),
                "queued": len(self.queue),
                "every_choices": list(EVERY_CHOICES),
                "registry": str(self.registry_path),
                "registry_error": self.registry_error,
            },
            "groups": self.meta.get("groups") or [],
            "disk": disk_status(self.home),
            "lanes": lanes_out,
            "jobs": jobs_out,
            "batch": self.state.get("batch"),
        }

    def write_state(self, now: float, stopped: bool = False) -> None:
        self.state["snapshot"] = self.snapshot(now)
        atomic_write(self.state_file, self.state)
        beat = {"pid": os.getpid(), "started_at": iso(self.started_at), "heartbeat_at": iso(now),
                "jobs": len(self.jobs), "running": len(self.running), "queued": len(self.queue)}
        if stopped:
            beat["stopped_at"] = iso(now)
        atomic_write(self.paths.run / "scheduler.json", beat)
        self.last_write = now
        if self.dirty:
            self.notify_event.set()
        self.dirty = False

    # ---- loop

    def step(self, now: float | None = None) -> None:
        """One tick: reload jobs.json, take requests, start due work, close finished runs, save state."""
        now = now_ts() if now is None else now
        self.reload_registry()
        self.take_requests()
        self.check_watches(now)
        self.due_jobs(now)
        self.poll_running(now)
        self.start_ready()
        if self.dirty or now - self.last_write >= HEARTBEAT_EVERY:
            self.write_state(now)

    def loop(self) -> None:
        if self.notify_enabled:
            threading.Thread(target=self._notifier, daemon=True).start()
        self.info("scheduler started", pid=os.getpid(), jobs=len(self.jobs))
        while not self.stop_requested:
            try:
                self.step()
            except Exception as exc:  # the loop must survive one bad tick
                self.log.error("tick failed", extra={"fields": {"error": f"{type(exc).__name__}: {exc}"}})
            time.sleep(TICK)
        self.shutdown()

    def shutdown(self, grace: float = 12.0) -> None:
        # Give runs a moment to finish; anything still running is stopped and runs again when the
        # scheduler comes back (see _adopt_after_restart).
        deadline = now_ts() + grace
        while self.running and now_ts() < deadline:
            self.poll_running(now_ts())
            time.sleep(0.2)
        for run in self.running.values():
            run.stop(signal.SIGTERM)
        self.write_state(now_ts(), stopped=True)
        self.info("scheduler stopped")

    def _notifier(self) -> None:
        while not self.stop_requested:
            self.notify_event.wait(timeout=60)
            self.notify_event.clear()
            if self.stop_requested:
                return
            notify_hub(self.cfg)
            time.sleep(1.0)


def notify_hub(cfg: Config) -> bool:
    """Tell the hub the scheduler's picture changed (`POST /v1/jobs/notify`, automation token)."""
    hub = cfg.hub_url()
    token = Secrets(cfg.paths).get("ORBITAL_AUTOMATION_TOKEN")
    if not hub or not token:
        return False
    try:
        response = httpx.post(f"{hub}/v1/jobs/notify", json={}, timeout=5,
                              headers={"Authorization": f"Bearer {token}"})
        return response.status_code < 300
    except httpx.HTTPError:
        return False


DISK_LOW_GB = 5.0


def disk_status(home: Path) -> dict:
    """Free space where ORBITAL_HOME lives, so the apps can warn before writes start failing."""
    try:
        usage = shutil.disk_usage(str(home))
    except OSError:
        return {}
    free = round(usage.free / 1e9, 1)
    return {"free_gb": free, "total_gb": round(usage.total / 1e9), "low": free < DISK_LOW_GB}


# --------------------------------------------------------------------------- command line


def scheduler_alive(cfg: Config, stale_after: float = 90.0) -> bool:
    """A fresh heartbeat from a process that is still there and did not say it stopped."""
    beat = read_json(cfg.paths.run / "scheduler.json", {}) or {}
    stamp = parse_iso(beat.get("heartbeat_at"))
    if stamp is None or now_ts() - stamp >= stale_after or beat.get("stopped_at"):
        return False
    pid = beat.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
        except PermissionError:
            pass
        except (ProcessLookupError, ValueError, OverflowError):
            return False
    return True


def _lock_exclusive(handle) -> bool:
    """Take a non-blocking exclusive lock on an open file; False when another process holds it."""
    if fcntl is None:
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def main(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    cfg.paths.ensure()
    if not cfg.get("scheduler.enabled", True):
        print("scheduler.enabled is false in config.toml; not starting", file=sys.stderr)
        return 0
    lock_path = cfg.paths.run / "scheduler.lock"
    lock = lock_path.open("w")
    if not _lock_exclusive(lock):
        print("scheduler already running", file=sys.stderr)
        return 0
    scheduler = Scheduler(cfg)

    def stop(*_):
        scheduler.stop_requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    scheduler.loop()
    return 0


def plan_rows(cfg: Config) -> list[dict]:
    meta, jobs = load_registry(cfg.paths.jobs_file, cfg.paths.home)
    state = read_json(cfg.paths.scheduler_data / "state.json", {}) or {}
    settings = read_json(cfg.paths.scheduler_data / "settings.json", {"jobs": {}}) or {"jobs": {}}
    now = now_ts()
    rows = []
    for job in jobs:
        record = (state.get("jobs") or {}).get(job.id) or {}
        job_settings = (settings.get("jobs") or {}).get(job.id) or {}
        every = int(job_settings.get("every") or job.default_every)
        started = record.get("started_ts")
        upcoming = next_due(float(started), every, job.offset) if started else now
        paused = not job.enabled or bool(job_settings.get("paused"))
        rows.append({
            "id": job.id, "name": job.name, "group": job.group, "lane": job.lane,
            "schedule": schedule_label(every, job.offset), "paused": paused,
            "state": record.get("state") or "idle",
            "next_run_ts": None if paused else max(upcoming, now),
            "last": record.get("last") or {},
        })
    return rows


def plan(cfg: Config | None = None, out=None) -> int:
    cfg = cfg or load_config()
    out = out or sys.stdout
    if not cfg.paths.jobs_file.is_file():
        print(f"no timetable at {cfg.paths.jobs_file}; copy feeds/jobs.example.json there to start", file=out)
        return 1
    try:
        rows = plan_rows(cfg)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"jobs.json not loaded: {exc}", file=out)
        return 2
    if not scheduler_alive(cfg):
        print("(the scheduler is not running; times show when it would run)", file=out)
    for row in rows:
        when = "paused" if row["paused"] else datetime.fromtimestamp(row["next_run_ts"]).strftime("%H:%M")
        last = row["last"]
        print(f"{row['id']:<20} {row['schedule']:<32} lane {row['lane']:<12} {row['state']:<8} next {when:<7} "
              f"last {last.get('status', '-'):<5} {str(last.get('summary') or '')[:60]}", file=out)
    return 0


def write_request(cfg: Config, body: dict) -> str:
    requests = cfg.paths.scheduler_data / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    ident = f"{time.time_ns()}-{secrets.token_hex(3)}"
    tmp = requests / f".{ident}.tmp"
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, requests / f"{ident}.json")
    return ident


def request_run(ids: list[str], wait: bool, cfg: Config | None = None, timeout: float = 1800, out=None) -> int:
    """Queue jobs through the running scheduler (never run a feed beside it), optionally wait."""
    cfg = cfg or load_config()
    out = out or sys.stdout
    _, jobs = load_registry(cfg.paths.jobs_file, cfg.paths.home)
    known = {job.id for job in jobs}
    unknown = [job_id for job_id in ids if job_id not in known]
    if unknown or not ids:
        print(f"unknown job {', '.join(unknown) or '(none)'}; jobs: {', '.join(sorted(known))}", file=sys.stderr)
        return 2
    state_file = cfg.paths.scheduler_data / "state.json"
    before = {job_id: (((read_json(state_file, {}) or {}).get("jobs") or {}).get(job_id) or {}).get("last", {}).get("started_at")
              for job_id in ids}
    write_request(cfg, {"type": "run", "jobs": ids, "by": "cli"})
    print(f"queued {', '.join(ids)}", file=out)
    if not scheduler_alive(cfg):
        print("the scheduler is not running, so nothing will pick this up; start it with "
              "`orbital services restart scheduler` or run once in the foreground with `orbital jobs run <id> --here`",
              file=sys.stderr)
        return 3 if wait else 0
    if not wait:
        return 0
    deadline = time.time() + timeout
    pending = set(ids)
    worst = 0
    while pending and time.time() < deadline:
        time.sleep(1)
        records = (read_json(state_file, {}) or {}).get("jobs") or {}
        for job_id in list(pending):
            record = records.get(job_id) or {}
            last = record.get("last") or {}
            if record.get("state") == "idle" and last.get("started_at") and last.get("started_at") != before[job_id]:
                pending.discard(job_id)
                print(f"{job_id}: {last.get('status')} in {last.get('duration_s')} s: {last.get('summary')}", file=out)
                for issue in last.get("issues") or []:
                    print(f"  - {issue}", file=out)
                worst = max(worst, {"ok": 0, "warn": 1}.get(last.get("status"), 2))
    if pending:
        print(f"still not finished: {', '.join(sorted(pending))}", file=sys.stderr)
        return 3
    return worst


def run_here(job_id: str, cfg: Config | None = None, out=None) -> int:
    """Run one job in the foreground, now, without the scheduler (for trying a feed out)."""
    cfg = cfg or load_config()
    out = out or sys.stdout
    if scheduler_alive(cfg):
        print("the scheduler is running; use `orbital jobs run <id> --wait` so the feed never runs twice", file=sys.stderr)
        return 2
    _, jobs = load_registry(cfg.paths.jobs_file, cfg.paths.home)
    job = next((j for j in jobs if j.id == job_id), None)
    if job is None:
        print(f"unknown job {job_id}", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["PATH"] = search_path(env.get("PATH", ""))
    env.update(job.env)
    env.update({"ORBITAL_HOME": str(cfg.paths.home), "ORBITAL_JOB_ID": job.id, "ORBITAL_RUN_REASON": "manual"})
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p)
    try:
        proc = subprocess.run(job.argv, cwd=job.cwd, env=env, capture_output=True, text=True, timeout=job.timeout)
    except subprocess.TimeoutExpired:
        print(f"{job_id}: timed out after {job.timeout:g} s", file=out)
        return 2
    except OSError as exc:
        print(f"{job_id}: could not start: {exc}", file=out)
        return 2
    lines = (proc.stdout + proc.stderr).splitlines()
    for line in lines[-40:]:
        print(line, file=out)
    outcome = classify(lines, proc.returncode)
    print(f"{job_id}: {outcome['status']}: {outcome['summary']}", file=out)
    for issue in outcome["issues"]:
        print(f"  - {issue}", file=out)
    return {"ok": 0, "warn": 1}.get(outcome["status"], 2)


def self_test() -> None:
    assert next_slot(0, 900, 120) == 120
    assert next_slot(120, 900, 120) == 1020
    assert next_slot(1019, 900, 120) == 1020
    base = 1_791_000_000 - (1_791_000_000 % 3600)
    assert next_slot(base + 5, 3600, 420) == base + 420
    assert next_slot(base + 421, 3600, 420) == base + 3600 + 420
    assert next_due(1000, 900, 120) == 1920, "a run 20 s before the :17 slot skips it"
    assert next_due(500, 900, 120) == 1020
    assert schedule_label(900, 120) == "Every 15 min (:02, :17, :32, :47)"
    assert schedule_label(3600, 420) == "Hourly at :07"
    assert schedule_label(1800, 1620) == "Every 30 min (:27, :57)"
    assert schedule_label(300, 60) == "Every 5 min"
    assert schedule_label(60, 0) == "Every minute"
    assert schedule_label(10800, 0) == "Every 3 h"
    good = classify(["uptime pushed sites=3 down=0 alerts=0"], 0)
    assert good["status"] == "ok", good
    warn = classify(["weather pushed revision=12 forecast=ok radar=unavailable alerts=0"], 0)
    assert warn["status"] == "warn" and warn["issues"] == ["Radar: unavailable"], warn
    crash = classify(["Traceback (most recent call last):", '  File "x.py", line 1',
                      "ConnectionRefusedError: [Errno 61] Connection refused"], 1)
    assert crash["status"] == "error" and "Connection refused" in crash["summary"], crash
    assert classify([], None, timed_out=True)["status"] == "error"
    print("scheduler self-test ok")


def cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        self_test()
        return 0
    if "--plan" in argv:
        return plan()
    if "--run" in argv:
        names = [arg for arg in argv[argv.index("--run") + 1:] if not arg.startswith("--")]
        return request_run(names, wait="--wait" in argv)
    return main()
