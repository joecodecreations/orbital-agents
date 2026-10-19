"""Windows safety of the satellite: heartbeats, metrics, stopping jobs, finding agents, importing the CLI.

These run on macOS/Linux CI by faking Windows: sys.platform = "win32", no os.getloadavg, a fake kernel32.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from orbital.core.config import load_config
from orbital.hub.satellites import _sanitize_metrics
from orbital.satellite import agents, jobs, join
from orbital.satellite.heartbeat import beat_once
from orbital.satellite.join import join_hub, system_disk, system_metrics, tracker_installed
from satellite_helpers import ServerThread, make_cfg
from test_satellite_hub_link import JOIN, FakeHub

REPO = Path(__file__).resolve().parents[2]


class FakeKernel32:
    """Fills the ctypes out-parameters the way Windows would."""

    def __init__(self, cpu_samples=None, fail: bool = False):
        self.cpu_samples = list(cpu_samples or [])
        self.fail = fail

        def ticks():
            if self.fail:
                raise OSError("no tick count")
            return 3_600_000
        self.GetTickCount64 = ticks         # a plain function: the code sets .restype on it

    def GetSystemTimes(self, idle, kernel, user):
        if self.fail or not self.cpu_samples:
            return 0
        i, k, u = self.cpu_samples.pop(0)
        idle._obj.value, kernel._obj.value, user._obj.value = i, k, u
        return 1

    def GlobalMemoryStatusEx(self, ref):
        if self.fail:
            return 0
        status = ref._obj
        assert status.dwLength == ctypes.sizeof(status)
        status.ullTotalPhys = 16 * 1024**3
        status.ullAvailPhys = 4 * 1024**3
        return 1


def fake_windows(monkeypatch) -> FakeKernel32:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(os, "getloadavg", raising=False)
    monkeypatch.setattr(join, "_cpu_last", None)
    monkeypatch.setattr(join, "_sleep", lambda s: None)
    kernel = FakeKernel32(cpu_samples=[(100, 200, 100), (150, 300, 200)])
    monkeypatch.setattr(join, "_kernel32", lambda: kernel)
    return kernel


@pytest.fixture
def windows(monkeypatch):
    return fake_windows(monkeypatch)


# --------------------------------------------------------------------------- metrics


def test_metrics_without_getloadavg_still_report(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "getloadavg", raising=False)
    out = system_metrics([str(tmp_path)])
    assert "load" not in out
    assert out["cpus"] >= 1 and out["disks"][0]["mount"] == str(tmp_path)


def test_metrics_survive_a_failing_getloadavg(monkeypatch):
    def broken():
        raise OSError("load average unobtainable")
    monkeypatch.setattr(os, "getloadavg", broken, raising=False)
    assert "load" not in system_metrics()


def test_windows_metrics_from_kernel32(windows, tmp_path):
    out = system_metrics([str(tmp_path)])
    assert "load" not in out
    assert out["cpu_percent"] == 75.0         # 50 idle of 200 ticks between the two samples
    assert out["memory"] == {"total": 16 * 1024**3, "used": 12 * 1024**3, "percent": 75.0}
    assert out["uptime_s"] == 3600.0
    assert out["disks"][0]["mount"] == str(tmp_path)


def test_windows_cpu_uses_the_previous_heartbeat_as_baseline(windows):
    windows.cpu_samples = [(0, 100, 0), (50, 200, 0), (60, 300, 100)]
    assert join._cpu_percent() == 50.0
    assert join._cpu_percent() == 95.0        # 10 idle of 200 since the last call, no new sleep


def test_windows_metrics_never_raise_when_kernel32_fails(windows, monkeypatch):
    windows.fail = True
    out = system_metrics(["/definitely/not/here"])
    assert set(out) == {"cpus", "time"}
    monkeypatch.setattr(join, "_kernel32", lambda: (_ for _ in ()).throw(OSError("no kernel32")))
    assert set(system_metrics()) == {"cpus", "time"}


def test_windows_system_disk(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemDrive", "D:")
    assert system_disk() == "D:\\"
    monkeypatch.delenv("SystemDrive")
    assert system_disk() == "C:\\"
    monkeypatch.setattr(sys, "platform", "linux")
    assert system_disk() == "/"


def test_hub_keeps_cpu_percent_and_drops_nonsense():
    assert _sanitize_metrics({"cpus": 8, "cpu_percent": 42.345})["cpu_percent"] == 42.3
    for bad in (-1, 101, True, "50", None):
        assert "cpu_percent" not in _sanitize_metrics({"cpu_percent": bad})


def test_windows_heartbeat_reaches_the_hub(home, monkeypatch):
    cfg = make_cfg(home)
    hub = FakeHub()
    with ServerThread(hub.app) as srv:       # the fake hub runs as on this OS; only the satellite is "Windows"
        join_hub(srv.url, JOIN, cfg)
        fake_windows(monkeypatch)
        monkeypatch.setattr(join, "available_agents", lambda cfg: ["echo"])   # shutil.which needs real Windows
        result = beat_once(load_config(home))
        monkeypatch.undo()
    assert result["ok"], result
    metrics = hub.beats[0]["body"]["system_metrics"]
    assert "load" not in metrics and metrics["cpu_percent"] == 75.0
    assert metrics["memory"]["percent"] == 75.0


# --------------------------------------------------------------------------- tracker


def test_pc_tracker_marker_counts_on_any_os(home, monkeypatch):
    cfg = make_cfg(home)
    monkeypatch.setattr(sys, "platform", "win32")
    assert tracker_installed(cfg) is False
    cfg.paths.tracker_data.mkdir(parents=True, exist_ok=True)
    (cfg.paths.tracker_data / "installed.json").write_text('{"app": "tracker-pc"}')
    assert tracker_installed(cfg) is True


# --------------------------------------------------------------------------- stopping jobs


def test_windows_kill_ends_the_process_tree(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(jobs.subprocess, "run", lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.setattr(jobs.os, "killpg", lambda *a: pytest.fail("killpg on Windows"), raising=False)
    jobs._kill_group(4242, jobs.SIGKILL)
    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_windows_kill_falls_back_to_terminating_the_agent(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    killed = []
    monkeypatch.setattr(jobs.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 128))
    monkeypatch.setattr(jobs.os, "kill", lambda pid, sig: killed.append(pid))
    jobs._kill_group(4242, jobs.signal.SIGTERM)
    assert killed == [4242]


@pytest.mark.parametrize("listing, executable, expected", [
    ('"claude.exe","4242","Console","1","120,000 K"', r"C:\Users\me\.local\bin\claude.exe", True),
    ('"CLAUDE.EXE","4242","Console","1","120,000 K"', "C:/tools/claude.exe", True),
    ('"notepad.exe","4242","Console","1","9,000 K"', r"C:\tools\claude.exe", False),
    ('"claude.exe","42420","Console","1","9,000 K"', r"C:\tools\claude.exe", False),
    ("INFO: No tasks are running which match the specified criteria.", r"C:\tools\claude.exe", False),
])
def test_windows_pid_check_reads_tasklist(monkeypatch, listing, executable, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(jobs.os, "kill", lambda *a: pytest.fail("os.kill(pid, 0) sends CTRL_C_EVENT on Windows"))
    monkeypatch.setattr(jobs.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=listing + "\r\n"))
    assert jobs._pid_runs(4242, executable) is expected


# --------------------------------------------------------------------------- finding agents


def test_windows_install_folders_are_searched(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    dirs = agents.common_bin_dirs()
    assert str(tmp_path / "Roaming") + "\\npm" in dirs
    assert not any("%" in d for d in dirs)
    assert "/opt/homebrew/bin" not in dirs


def test_paths_with_the_other_slash_are_taken_as_paths(monkeypatch, tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(agents.os, "altsep", "/")
    monkeypatch.setattr(agents.os, "sep", "\\")
    assert agents.resolve_executable(str(exe)) == str(exe)


def test_nvm_version_sort_understands_backslashes():
    assert agents._version_key(r"C:\Users\me\.nvm\versions\node\v20.11.1\bin") == (20, 11, 1)


# --------------------------------------------------------------------------- the CLI loads


def test_the_cli_loads_without_posix_only_modules():
    script = textwrap.dedent("""
        import os, sys
        for name in ("fcntl", "termios", "tty", "pwd", "grp", "resource"):
            sys.modules[name] = None
        if hasattr(os, "getloadavg"):
            del os.getloadavg
        from orbital.cli.main import build_parser
        parser, table = build_parser()
        assert "satellite" in table and "satellites" in table, sorted(table)
        from orbital.satellite.join import system_metrics
        assert "cpus" in system_metrics()
        print("ok")
    """)
    proc = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok"), proc.stderr[-2000:]


def test_scheduler_lock_is_single_instance(tmp_path):
    from orbital.scheduler.scheduler import _lock_exclusive
    path = tmp_path / "scheduler.lock"
    with path.open("w") as first, path.open("w") as second:
        assert _lock_exclusive(first) is True
        assert _lock_exclusive(second) is False
