"""Agent jobs on this machine: queue, concurrency limit, timeouts, output capture, records on disk.

A job is one run of one agent CLI with one prompt:

    data/satellite/jobs/<job_id>.json   the record (status, times, exit code, tracker item, ...)
    data/satellite/out/<job_id>.log     everything the agent printed (stdout + stderr), capped

Status goes queued → running → done | failed | cancelled. At most `satellite.max_concurrent_jobs`
run at once; the rest wait in order. A run that passes `satellite.job_timeout_minutes` is stopped
(SIGTERM to its whole process group, SIGKILL after a grace period) and recorded as failed.

Output is capped at `satellite.max_output_bytes` (default 2 MB) per job. Past the cap the first
64 KB and the last part are kept, with a marker in between, so the agent's final answer survives.
The watchdog trims old records and outputs (`trim_job_records`).
"""

from __future__ import annotations

import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orbital import __version__
from orbital.core.config import Config
from orbital.core.limits import MAX_PROMPT_CHARS
from orbital.satellite.agents import build_argv, child_env, configured_agents
from orbital.satellite.tracker import TrackerReporter
from orbital.satellite.util import NO_CONSOLE, atomic_write_json, iso, now, parse_iso, read_json

ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "cancelled")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
DEFAULT_OUTPUT_CAP = 2_000_000
HEAD_BYTES = 64_000
TAIL_CHARS = 4_000
DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")  # a nested agent CLI must not think it runs inside another


class JobError(Exception):
    """A request the satellite refuses. `status` is the HTTP status the API answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def new_job_id() -> str:
    return f"j{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{secrets.token_hex(3)}"


def valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.match(job_id or ""))


# --------------------------------------------------------------------------- output


class OutputSink:
    """Writes a job's output to its log file, keeping head + tail once it passes the cap."""

    def __init__(self, path: Path, cap: int = DEFAULT_OUTPUT_CAP, head: int = HEAD_BYTES):
        self.path = path
        self.cap = max(1024, int(cap))
        self.head_cap = min(int(head), self.cap // 2)
        self.tail_cap = self.cap - self.head_cap
        self.total = 0
        self.file_bytes = 0
        self.tail = bytearray()
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("wb")
        self.closed = False

    @property
    def truncated(self) -> bool:
        return self.total > self.cap

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self.lock:
            if self.closed:
                return
            self.total += len(data)
            if self.file_bytes < self.cap:
                take = data[: self.cap - self.file_bytes]
                self.fh.write(take)
                self.fh.flush()
                self.file_bytes += len(take)
            self.tail += data
            if len(self.tail) > self.tail_cap:
                del self.tail[: len(self.tail) - self.tail_cap]

    def _marker(self) -> bytes:
        dropped = self.total - self.head_cap - len(self.tail)
        return (f"\n\n… [{dropped} bytes of output dropped: the first {self.head_cap} and the last "
                f"{len(self.tail)} bytes are kept] …\n\n").encode()

    def _assembled(self) -> bytes:
        try:
            with self.path.open("rb") as fh:
                head = fh.read(self.head_cap)
        except OSError:
            head = b""
        return head + self._marker() + bytes(self.tail)

    def text(self) -> str:
        with self.lock:
            if not self.truncated:
                try:
                    return self.path.read_bytes().decode("utf-8", errors="replace")
                except OSError:
                    return ""
            return self._assembled().decode("utf-8", errors="replace")

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.fh.close()
            if self.truncated:
                body = self._assembled()
                tmp = self.path.with_suffix(".tmp")
                tmp.write_bytes(body)
                os.replace(tmp, self.path)


def _pump(stream, sink: OutputSink) -> None:
    try:
        fd = stream.fileno()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            sink.write(chunk)
    except (OSError, ValueError):
        pass


# --------------------------------------------------------------------------- manager


@dataclass
class _Active:
    job_id: str
    thread: threading.Thread | None = None
    proc: subprocess.Popen | None = None
    sink: OutputSink | None = None
    cancel_requested: bool = False
    stop_reason: str | None = None          # "cancel" | "timeout" | "shutdown"
    killed_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)     # Windows has no SIGKILL


def _kill_group(pid: int, sig: int) -> None:
    """Signal the job's process group; on Windows end its whole process tree (taskkill /T /F)."""
    if sys.platform == "win32":
        try:
            done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                                  timeout=15, creationflags=NO_CONSOLE).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            done = False
        if not done:
            try:
                os.kill(pid, signal.SIGTERM)       # TerminateProcess on the agent itself
            except OSError:
                pass
        return
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {(seconds % 3600) // 60} min"


def _last_line(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line and not line.startswith("…"):
            return line[:300]
    return ""


class JobManager:
    def __init__(self, cfg: Config, *, data_dir: str | os.PathLike | None = None,
                 max_concurrent: int | None = None, timeout_s: float | None = None,
                 output_cap: int | None = None, kill_grace: float = 5.0,
                 tracker: TrackerReporter | None = None, log=None, max_queue: int = 50):
        self.cfg = cfg
        self.dir = Path(data_dir) if data_dir else cfg.paths.satellite_data
        self.jobs_dir = self.dir / "jobs"
        self.out_dir = self.dir / "out"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max(1, int(max_concurrent or cfg.get("satellite.max_concurrent_jobs", 2) or 1))
        if timeout_s is None:
            timeout_s = float(cfg.get("satellite.job_timeout_minutes", 60) or 60) * 60
        self.timeout_s = max(0.1, float(timeout_s))
        self.output_cap = int(output_cap or cfg.get("satellite.max_output_bytes", DEFAULT_OUTPUT_CAP) or DEFAULT_OUTPUT_CAP)
        self.kill_grace = kill_grace
        self.tracker = tracker if tracker is not None else TrackerReporter(cfg=cfg, log=log)
        self.log = log
        self.max_queue = max_queue
        self.machine = cfg.machine_name
        self._lock = threading.RLock()
        self._records: dict[str, dict] = {}
        self._queue: list[str] = []
        self._active: dict[str, _Active] = {}
        self._closing = False
        self._recover()

    # ---- helpers

    def _info(self, msg: str, **fields) -> None:
        if self.log:
            self.log.info(msg, extra={"fields": fields})

    def _warn(self, msg: str, **fields) -> None:
        if self.log:
            self.log.warning(msg, extra={"fields": fields})

    def _record_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def out_path(self, job_id: str) -> Path:
        return self.out_dir / f"{job_id}.log"

    def _save(self, record: dict) -> None:
        try:
            atomic_write_json(self._record_path(record["id"]), record)
        except OSError as exc:
            self._warn("could not save job record", job=record.get("id"), error=str(exc))

    def workdirs(self) -> list[Path]:
        raw = self.cfg.get("satellite.workdirs") or ["~"]
        if isinstance(raw, str):
            raw = [raw]
        out = []
        for entry in raw:
            try:
                out.append(Path(os.path.expanduser(str(entry))).resolve())
            except OSError:
                continue
        return out or [Path.home().resolve()]

    def check_cwd(self, cwd: str | None) -> str:
        roots = self.workdirs()
        if not cwd:
            target = roots[0]
        else:
            target = Path(os.path.expanduser(str(cwd)))
            if not target.is_absolute():
                target = roots[0] / target
        try:
            target = target.resolve()
        except OSError as exc:
            raise JobError(400, f"cwd {cwd} cannot be resolved: {exc}") from exc
        if not any(target == root or root in target.parents for root in roots):
            raise JobError(400, f"cwd {target} is outside satellite.workdirs ({', '.join(str(r) for r in roots)})")
        if not target.is_dir():
            raise JobError(400, f"cwd {target} is not a folder")
        return str(target)

    # ---- recovery after a restart

    def _recover(self) -> None:
        for path in sorted(self.jobs_dir.glob("*.json")):
            record = read_json(path, None)
            if not isinstance(record, dict) or record.get("status") not in ACTIVE:
                continue
            note = "interrupted: the satellite service restarted"
            pid = record.get("pid")
            if pid and _pid_runs(int(pid), record.get("executable") or ""):
                _kill_group(int(pid), signal.SIGTERM)
                note += f"; stopped the leftover process {pid}"
            record.update({"status": "failed", "error": note, "finished_at": iso(now()), "pid": None})
            self._save(record)

    # ---- public API

    def submit(self, agent: str | None = None, prompt: str = "", cwd: str | None = None,
               title: str | None = None, track: bool = True, by: str = "api") -> dict:
        name = (agent or self.cfg.get("satellite.default_agent") or "").strip()
        agents = configured_agents(self.cfg)
        if name not in agents:
            raise JobError(404, f"unknown agent {name or '(none)'}; configured: {', '.join(sorted(agents)) or 'none'}")
        spec = agents[name]
        if not spec.available:
            raise JobError(409, f"agent {name} is not installed on this machine ({spec.command[0]} was not found)")
        prompt = (prompt or "").strip()
        if not prompt:
            raise JobError(400, "prompt is empty")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise JobError(413, f"prompt is longer than {MAX_PROMPT_CHARS:,} characters")
        workdir = self.check_cwd(cwd)
        title = (title or "").strip() or prompt.splitlines()[0][:60]
        with self._lock:
            if self._closing:
                raise JobError(503, "the satellite is shutting down")
            if len(self._queue) >= self.max_queue:
                raise JobError(429, f"{len(self._queue)} jobs are already waiting; try again later")
            job_id = new_job_id()
            while self._record_path(job_id).exists():
                job_id = new_job_id()
            ts = now()
            record = {
                "id": job_id,
                "agent": name,
                "title": title[:200],
                "prompt": prompt,
                "cwd": workdir,
                "track": bool(track),
                "by": str(by or "api")[:40],
                "machine": self.machine,
                "executable": spec.path,
                "status": "queued",
                "exit_code": None,
                "error": None,
                "timed_out": False,
                "created_at": iso(ts),
                "started_at": None,
                "finished_at": None,
                "duration_s": None,
                "pid": None,
                "tracker_id": None,
                "output_bytes": 0,
                "output_truncated": False,
                "version": __version__,
            }
            self._records[job_id] = record
            self._save(record)
            self._queue.append(job_id)
            self._info("job queued", job=job_id, agent=name, by=record["by"])
            self._pump_queue()
            return self.public(record)

    def get(self, job_id: str, with_output: bool = True) -> dict | None:
        if not valid_job_id(job_id):
            return None
        with self._lock:
            record = self._records.get(job_id)
            active = self._active.get(job_id)
            if record is not None:
                out = self.public(record)
                if with_output:
                    text = active.sink.text() if active and active.sink else ""
                    out["output_tail"] = text[-TAIL_CHARS:]
                    if active and active.sink:
                        out["output_bytes"] = active.sink.total
                        out["output_truncated"] = active.sink.truncated
                return out
        record = read_json(self._record_path(job_id), None)
        if not isinstance(record, dict):
            return None
        out = self.public(record)
        if with_output:
            out["output_tail"] = self._read_tail(job_id)
        return out

    def log_text(self, job_id: str) -> str | None:
        if not valid_job_id(job_id):
            return None
        with self._lock:
            active = self._active.get(job_id)
            if active and active.sink:
                return active.sink.text()
            known = job_id in self._records
        path = self.out_path(job_id)
        if path.is_file():
            return path.read_bytes().decode("utf-8", errors="replace")
        if known or self._record_path(job_id).is_file():
            return ""
        return None

    def list(self, limit: int = 100, status: str | None = None) -> list[dict]:
        rows: dict[str, dict] = {}
        for path in self.jobs_dir.glob("*.json"):
            record = read_json(path, None)
            if isinstance(record, dict) and record.get("id"):
                rows[record["id"]] = record
        with self._lock:
            for job_id, record in self._records.items():
                rows[job_id] = dict(record)
        out = [self.public(r, brief=True) for r in rows.values() if not status or r.get("status") == status]
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return out[: max(1, int(limit))]

    def cancel(self, job_id: str) -> dict | None:
        if not valid_job_id(job_id):
            return None
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                stored = read_json(self._record_path(job_id), None)
                return self.public(stored) if isinstance(stored, dict) else None
            if job_id in self._queue:
                self._queue.remove(job_id)
                self._finish_record(record, "cancelled", error="cancelled before it started")
                return self.public(record)
            active = self._active.get(job_id)
            if active is not None:
                active.cancel_requested = True
                active.stop_reason = active.stop_reason or "cancel"
                if active.proc is not None and active.killed_at is None:
                    active.killed_at = now()
                    _kill_group(active.proc.pid, signal.SIGTERM)
            out = self.public(record)
            out["cancel_requested"] = True
            return out

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {"running": len(self._active), "queued": len(self._queue)}

    def running_summary(self) -> list[dict]:
        with self._lock:
            return [self.public(r, brief=True) for r in self._records.values() if r.get("status") in ACTIVE]

    def wait(self, job_id: str, timeout: float = 30.0) -> dict | None:
        """Block until a job finishes (tests and `orbital satellite` use it)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rec = self.get(job_id, with_output=False)
            if rec is None or rec.get("status") in FINISHED:
                return self.get(job_id)
            time.sleep(0.05)
        return self.get(job_id)

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._closing = True
            for job_id in list(self._queue):
                self._queue.remove(job_id)
                record = self._records.get(job_id)
                if record:
                    self._finish_record(record, "cancelled", error="the satellite service stopped before it started")
            actives = list(self._active.values())
            for active in actives:
                active.cancel_requested = True
                active.stop_reason = "shutdown"
                if active.proc is not None and active.killed_at is None:
                    active.killed_at = now()
                    _kill_group(active.proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        for active in actives:
            if active.thread is not None:
                active.thread.join(timeout=max(0.1, deadline - time.monotonic()))

    @staticmethod
    def public(record: dict, brief: bool = False) -> dict:
        out = {k: v for k, v in record.items() if k not in ("prompt", "executable")}
        prompt = record.get("prompt") or ""
        limit = 200 if brief else 2000
        out["prompt"] = prompt if len(prompt) <= limit else prompt[:limit] + "…"
        out["job_id"] = record.get("id")
        return out

    # ---- running

    def _read_tail(self, job_id: str) -> str:
        path = self.out_path(job_id)
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                fh.seek(max(0, size - TAIL_CHARS * 4))
                return fh.read().decode("utf-8", errors="replace")[-TAIL_CHARS:]
        except OSError:
            return ""

    def _pump_queue(self) -> None:
        with self._lock:
            while self._queue and len(self._active) < self.max_concurrent and not self._closing:
                job_id = self._queue.pop(0)
                active = _Active(job_id=job_id)
                self._active[job_id] = active
                thread = threading.Thread(target=self._run, args=(job_id,), name=f"job-{job_id}", daemon=True)
                active.thread = thread
                thread.start()

    def _finish_record(self, record: dict, status: str, exit_code: int | None = None, error: str | None = None) -> None:
        ts = now()
        record["status"] = status
        record["exit_code"] = exit_code
        record["error"] = error
        record["finished_at"] = iso(ts)
        started = parse_iso(record.get("started_at"))
        record["duration_s"] = round(ts - started, 1) if started else None
        record["pid"] = None
        self._save(record)
        self._records.pop(record["id"], None)

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._records[job_id]
            active = self._active[job_id]
        status, code, error = "failed", None, None
        sink: OutputSink | None = None
        try:
            if record.get("track"):
                details = (f"{record['agent']} job `{job_id}` on {self.machine}, in `{record['cwd']}`.\n\n"
                           + (record.get("prompt") or "")[:600])
                tracker_id = self.tracker.start(record["agent"], record["title"], details)
                with self._lock:
                    record["tracker_id"] = tracker_id
            if active.cancel_requested:
                status, error = "cancelled", "cancelled before it started"
                return
            agents = configured_agents(self.cfg)
            spec = agents.get(record["agent"])
            if spec is None or not spec.available:
                error = f"agent {record['agent']} is no longer available"
                return
            argv, prompt_on_stdin = build_argv(spec, record["prompt"], record["cwd"])
            env = child_env({
                "ORBITAL_JOB_ID": job_id,
                "ORBITAL_AGENT": record["agent"],
                "ORBITAL_MACHINE": self.machine,
            })
            if record.get("tracker_id"):
                env["ORBITAL_TRACK_ID"] = record["tracker_id"]
            for key in DROP_ENV:
                env.pop(key, None)
            sink = OutputSink(self.out_path(job_id), self.output_cap)
            active.sink = sink
            try:
                proc = subprocess.Popen(
                    argv, cwd=record["cwd"], env=env,
                    stdin=subprocess.PIPE if prompt_on_stdin else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                    creationflags=NO_CONSOLE,
                )
            except OSError as exc:
                error = f"could not start {argv[0]}: {exc}"
                return
            started = now()
            with self._lock:
                active.proc = proc
                record.update({"status": "running", "pid": proc.pid, "started_at": iso(started)})
                self._save(record)
                if active.cancel_requested and active.killed_at is None:
                    active.killed_at = now()
                    _kill_group(proc.pid, signal.SIGTERM)
            self._info("job started", job=job_id, agent=record["agent"], pid=proc.pid)
            if prompt_on_stdin:
                threading.Thread(target=_feed_stdin, args=(proc, record["prompt"]), daemon=True).start()
            reader = threading.Thread(target=_pump, args=(proc.stdout, sink), daemon=True)
            reader.start()
            deadline = started + self.timeout_s
            while True:
                try:
                    code = proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
                t = now()
                with self._lock:
                    if active.killed_at is None and t > deadline:
                        active.stop_reason = "timeout"
                        active.killed_at = t
                        _kill_group(proc.pid, signal.SIGTERM)
                    elif active.killed_at is not None and t - active.killed_at > self.kill_grace:
                        _kill_group(proc.pid, SIGKILL)
            reader.join(timeout=2)
            if reader.is_alive():
                # The agent exited but something it started still holds the output open.
                _kill_group(proc.pid, SIGKILL)
                reader.join(timeout=2)
            if active.stop_reason == "timeout":
                status = "failed"
                error = f"timed out after {_duration_text(self.timeout_s)} and was stopped"
                with self._lock:
                    record["timed_out"] = True
            elif active.stop_reason in ("cancel", "shutdown") or active.cancel_requested:
                status = "cancelled"
                error = "cancelled" if active.stop_reason != "shutdown" else "stopped: the satellite service shut down"
            elif code == 0:
                status = "done"
            else:
                status = "failed"
                error = f"exit code {code}"
        except Exception as exc:  # a job thread must always close its record
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            text = ""
            if sink is not None:
                sink.close()
                text = sink.text()
                with self._lock:
                    record["output_bytes"] = sink.total
                    record["output_truncated"] = sink.truncated
            with self._lock:
                self._finish_record(record, status, exit_code=code, error=error)
                self._active.pop(job_id, None)
            self._info("job finished", job=job_id, status=status, exit_code=code, duration_s=record.get("duration_s"))
            if record.get("tracker_id"):
                verb = {"done": "Finished", "failed": "Failed", "cancelled": "Cancelled"}[status]
                summary = f"{verb} after {_duration_text(record.get('duration_s'))}"
                if error and status != "done":
                    summary += f" ({error})"
                last = _last_line(text)
                summary += "." + (f"\n\n{last}" if last else "")
                self.tracker.done(record["tracker_id"], summary)
            self._pump_queue()


def _feed_stdin(proc: subprocess.Popen, prompt: str) -> None:
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except (OSError, ValueError, AssertionError):
        pass


def _pid_runs(pid: int, executable: str) -> bool:
    """True when `pid` is alive and still runs `executable` (a reused pid is left alone)."""
    if sys.platform == "win32":
        return _pid_runs_windows(pid, executable)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(out) and bool(executable) and os.path.basename(out) == os.path.basename(executable)


def _pid_runs_windows(pid: int, executable: str) -> bool:
    # os.kill(pid, 0) would send CTRL_C_EVENT on Windows; ask tasklist for the image name instead.
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=10, creationflags=NO_CONSOLE).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    found = re.match(r'^"([^"]+)","(\d+)"', out)
    if not found or int(found.group(2)) != pid or not executable:
        return False
    return found.group(1).lower() == re.split(r"[\\/]", executable)[-1].lower()


# --------------------------------------------------------------------------- trimming (watchdog)


def trim_job_records(data_dir: str | os.PathLike, retention_days: float, keep_min: int = 20) -> dict[str, int]:
    """Delete finished job records and outputs older than `retention_days` (the newest `keep_min` stay)."""
    base = Path(data_dir)
    jobs_dir, out_dir = base / "jobs", base / "out"
    cutoff = now() - float(retention_days) * 86400
    removed = {"records": 0, "outputs": 0}
    finished: list[tuple[float, Path, dict]] = []
    for path in jobs_dir.glob("*.json") if jobs_dir.is_dir() else []:
        record = read_json(path, None)
        if not isinstance(record, dict):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed["records"] += 1
            except OSError:
                pass
            continue
        if record.get("status") in ACTIVE:
            continue
        stamp = parse_iso(record.get("finished_at") or record.get("created_at"))
        if stamp is None:
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
        finished.append((stamp, path, record))
    finished.sort(key=lambda row: row[0], reverse=True)
    keep_ids = {rec.get("id") for _, _, rec in finished[:keep_min]}
    for stamp, path, record in finished[keep_min:]:
        if stamp >= cutoff:
            continue
        try:
            path.unlink()
            removed["records"] += 1
        except OSError:
            pass
        out = out_dir / f"{record.get('id')}.log"
        if valid_job_id(str(record.get("id"))) and out.is_file():
            try:
                out.unlink()
                removed["outputs"] += 1
            except OSError:
                pass
    # Outputs whose record is gone.
    for out in out_dir.glob("*.log") if out_dir.is_dir() else []:
        if out.stem in keep_ids or (jobs_dir / f"{out.stem}.json").exists():
            continue
        try:
            if out.stat().st_mtime < cutoff:
                out.unlink()
                removed["outputs"] += 1
        except OSError:
            pass
    return removed
