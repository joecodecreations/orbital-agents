"""Windows: the small supervisor each Orbital service runs under (the Task Scheduler action).

    pythonw.exe -m orbital.services.winrun <run-spec.json>            run in the foreground (the task does this)
    pythonw.exe -m orbital.services.winrun --detach <run-spec.json>   start it in the background and return
    pythonw.exe -m orbital.services.winrun --stop <run-spec.json>     stop a running supervisor and its child

Task Scheduler can't set environment variables or redirect output, and it doesn't restart a program that
exits with an error. So windows.py writes a run spec (argv, env, working folder, log file, kind) next to
the task, and this supervisor:

- sets the environment, appends the child's stdout/stderr to the log file, hides its console window;
- restarts a daemon 10 s after it exits (launchd KeepAlive / systemd Restart=always);
- runs an interval service (the watchdog) once every `interval` seconds;
- puts the child in a job object that kills it (and anything it started) when the supervisor ends, so
  `schtasks /End` or `taskkill` never leaves an orphaned hub behind;
- writes run/winsvc-<name>.json (supervisor pid, child pid, restarts, last exit) for `orbital services status`.

Plain Python except the job object (ctypes, Windows only), so the loop is testable anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

THROTTLE = 10.0                     # seconds between a daemon exiting and its restart
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def load_spec(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def state_path(spec: dict) -> Path:
    home = spec.get("env", {}).get("ORBITAL_HOME") or str(Path.home() / ".orbital-agents")
    return Path(home) / "run" / f"winsvc-{spec['name']}.json"


def read_state(spec: dict) -> dict:
    try:
        return json.loads(state_path(spec).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(spec: dict, **fields) -> None:
    path = state_path(spec)
    data = read_state(spec)
    data.update(fields)
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259           # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


class _KillOnCloseJob:
    """A Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. No-op elsewhere."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                       "WriteBytes", "OtherBytes")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = handle
        else:
            kernel32.CloseHandle(handle)

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        import ctypes

        ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]


def _launch(spec: dict, log) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(spec.get("env") or {})
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(spec["program"], cwd=spec.get("working_dir") or None, env=env, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, creationflags=flags)


def supervise(spec: dict, *, max_runs: int | None = None, sleep=time.sleep, throttle: float = THROTTLE) -> int:
    """Run the service until killed (or `max_runs` runs, for tests). Returns the last exit code."""
    job = _KillOnCloseJob()
    log_path = Path(spec["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    interval = int(spec.get("interval") or 0) if spec.get("kind") == "interval" else 0
    runs = 0
    code = 0
    _write_state(spec, supervisor_pid=os.getpid(), pid=None, restarts=0, last_exit=None,
                 started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    while max_runs is None or runs < max_runs:
        with log_path.open("ab", buffering=0) as log:
            stamp = datetime.now().isoformat(timespec="seconds")
            log.write(f"\n[{stamp}] winrun: starting {spec['name']}\n".encode())
            try:
                proc = _launch(spec, log)
            except OSError as exc:
                log.write(f"[{stamp}] winrun: could not start: {exc}\n".encode())
                proc = None
            if proc is not None:
                job.add(proc)
                _write_state(spec, pid=proc.pid)
                code = proc.wait()
            else:
                code = 127
            log.write(f"[{datetime.now().isoformat(timespec='seconds')}] winrun: {spec['name']} exited {code}\n".encode())
        runs += 1
        _write_state(spec, pid=None, last_exit=code, restarts=runs if not interval else 0,
                     last_run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if max_runs is not None and runs >= max_runs:
            break
        sleep(interval if interval else throttle)
    return code


def detach(spec_file: Path, python: str | None = None) -> int:
    """Start `winrun <spec>` in the background (no console, outside our job) and return at once."""
    spec = load_spec(spec_file)
    state = read_state(spec)
    if pid_alive(state.get("supervisor_pid")):
        print(f"{spec['name']} already running (pid {state['supervisor_pid']})")
        return 0
    exe = python or sys.executable
    flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB) if sys.platform == "win32" else 0
    proc = subprocess.Popen([exe, "-m", "orbital.services.winrun", str(spec_file)], cwd=spec.get("working_dir") or None,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags, start_new_session=sys.platform != "win32")
    print(f"{spec['name']} started (pid {proc.pid})")
    return 0


def stop(spec_file: Path) -> int:
    """Stop a supervisor started with --detach (Run-key mode). The job object takes its child with it."""
    spec = load_spec(spec_file)
    state = read_state(spec)
    pids = [p for p in (state.get("supervisor_pid"), state.get("pid")) if pid_alive(p)]
    for pid in pids:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            try:
                os.kill(int(pid), 15)
            except OSError:
                pass
    _write_state(spec, supervisor_pid=None, pid=None)
    print(f"{spec['name']} stopped" if pids else f"{spec['name']} was not running")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--detach" and len(args) == 2:
        return detach(Path(args[1]))
    if args[0] == "--stop" and len(args) == 2:
        return stop(Path(args[1]))
    return supervise(load_spec(args[0]))


if __name__ == "__main__":
    sys.exit(main())
