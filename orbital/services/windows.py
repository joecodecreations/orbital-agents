"""Windows: one Task Scheduler task per service, run at logon as the current user, no admin needed.

    \\OrbitalAgents\\orbital-<name>      task (logon trigger, no time limit, one instance)
    %LOCALAPPDATA%\\OrbitalAgents\\services\\orbital-<name>.json   run spec: argv, env, folder, log, kind
    %LOCALAPPDATA%\\OrbitalAgents\\services\\orbital-<name>.xml    the task definition we registered

The task's action is `pythonw.exe -m orbital.services.winrun <run spec>`: a supervisor that sets the
environment, sends output to logs/<name>.stderr.log, restarts the service when it exits, and runs the
watchdog every interval (Task Scheduler can do none of that itself). See winrun.py.

When Windows refuses to register a logon task (some locked-down or non-admin accounts get "Access is
denied"), the service falls back to a value under HKCU\\...\\CurrentVersion\\Run, which starts the same
supervisor at logon; start/stop then go through `winrun --detach / --stop`.

Tasks only run while you are signed in. A hub that must run with nobody signed in needs the task set to
"Run whether user is logged on or not" in Task Scheduler (that asks for your Windows password, so we
never do it for you).
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from orbital.services.ops import Ops
from orbital.services.spec import UNIT_PREFIX, ServiceSpec

TASK_FOLDER = "OrbitalAgents"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def schtasks() -> str:
    return os.environ.get("ORBITAL_SCHTASKS", "schtasks")


def powershell() -> str:
    return os.environ.get("ORBITAL_POWERSHELL", "powershell")


def reg() -> str:
    return os.environ.get("ORBITAL_REG", "reg")


def windows_user() -> str:
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def gui_python(program: str) -> str:
    """pythonw.exe next to python.exe (no console window), when there is one."""
    p = Path(program)
    if p.name.lower() == "python.exe":
        w = p.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return program


def _quote(arg: str) -> str:
    """Windows command-line quoting for one argument (CommandLineToArgvW rules)."""
    import subprocess

    return subprocess.list2cmdline([arg])


def run_spec(spec: ServiceSpec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "program": list(spec.program),
        "env": dict(spec.env),
        "working_dir": str(spec.working_dir),
        "log": str(spec.log_path),
        "kind": spec.kind,
        "interval": int(spec.interval or 0),
    }


def render_spec(spec: ServiceSpec) -> str:
    return json.dumps(run_spec(spec), indent=2, sort_keys=True) + "\n"


def action(spec: ServiceSpec, spec_file: Path) -> tuple[str, str]:
    """(command, arguments) for the task: the supervisor with this service's run spec."""
    return gui_python(spec.program[0]), f"-m orbital.services.winrun {_quote(str(spec_file))}"


def render_task(spec: ServiceSpec, spec_file: Path, user: str | None = None) -> str:
    user = escape(user or windows_user())
    command, arguments = action(spec, spec_file)
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-16"?>',
        f'<Task version="1.2" xmlns="{TASK_NS}">',
        "  <RegistrationInfo>",
        f"    <Description>{escape(spec.description or spec.name)} (managed by `orbital services`)</Description>",
        f"    <URI>\\{TASK_FOLDER}\\{UNIT_PREFIX}{escape(spec.name)}</URI>",
        "  </RegistrationInfo>",
        "  <Triggers>",
        "    <LogonTrigger>",
        "      <Enabled>true</Enabled>",
        f"      <UserId>{user}</UserId>",
        "      <Delay>PT15S</Delay>",
        "    </LogonTrigger>",
        "  </Triggers>",
        "  <Principals>",
        '    <Principal id="Author">',
        f"      <UserId>{user}</UserId>",
        "      <LogonType>InteractiveToken</LogonType>",
        "      <RunLevel>LeastPrivilege</RunLevel>",
        "    </Principal>",
        "  </Principals>",
        "  <Settings>",
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
        "    <AllowHardTerminate>true</AllowHardTerminate>",
        "    <StartWhenAvailable>true</StartWhenAvailable>",
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>",
        "    <IdleSettings>",
        "      <StopOnIdleEnd>false</StopOnIdleEnd>",
        "      <RestartOnIdle>false</RestartOnIdle>",
        "    </IdleSettings>",
        "    <AllowStartOnDemand>true</AllowStartOnDemand>",
        "    <Enabled>true</Enabled>",
        "    <Hidden>false</Hidden>",
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>",
        "    <WakeToRun>false</WakeToRun>",
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "    <Priority>7</Priority>",
        "    <RestartOnFailure>",
        "      <Interval>PT1M</Interval>",
        "      <Count>999</Count>",
        "    </RestartOnFailure>",
        "  </Settings>",
        '  <Actions Context="Author">',
        "    <Exec>",
        f"      <Command>{escape(command)}</Command>",
        f"      <Arguments>{escape(arguments)}</Arguments>",
        f"      <WorkingDirectory>{escape(str(spec.working_dir))}</WorkingDirectory>",
        "    </Exec>",
        "  </Actions>",
        "</Task>",
    ]) + "\n"


def encode_task(xml: str) -> bytes:
    """schtasks /XML wants UTF-16 with a BOM to match the declaration."""
    return xml.replace("\n", "\r\n").encode("utf-16")


def _denied(text: str) -> bool:
    t = text.lower()
    return "access is denied" in t or "0x80070005" in t or "access denied" in t


class WindowsBackend:
    kind = "taskscheduler"

    def __init__(self, ops: Ops, home: Path | None = None):
        self.ops = ops
        self.home = Path(home) if home else Path.home()
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local and home is None else self.home / "AppData" / "Local"
        self.dir = base / "OrbitalAgents" / "services"

    # files --------------------------------------------------------------------------------------
    def task_name(self, name: str) -> str:
        return f"\\{TASK_FOLDER}\\{UNIT_PREFIX}{name}"

    def spec_file(self, name: str) -> Path:
        return self.dir / f"{UNIT_PREFIX}{name}.json"

    def task_file(self, name: str) -> Path:
        return self.dir / f"{UNIT_PREFIX}{name}.xml"

    def runkey_file(self, name: str) -> Path:
        return self.dir / f"{UNIT_PREFIX}{name}.runkey"

    def file(self, name: str) -> Path:
        return self.spec_file(name)

    def files(self, name: str) -> list[Path]:
        return [self.spec_file(name), self.task_file(name), self.runkey_file(name)]

    def mode(self, name: str) -> str:
        if self.task_file(name).is_file():
            return "task"
        if self.runkey_file(name).is_file():
            return "runkey"
        return ""

    def render(self, spec: ServiceSpec) -> dict[Path, str]:
        f = self.spec_file(spec.name)
        return {f: render_spec(spec), self.task_file(spec.name): render_task(spec, f)}

    def installed_names(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return [p.name[len(UNIT_PREFIX):-len(".json")] for p in sorted(self.dir.glob(f"{UNIT_PREFIX}*.json"))]

    def is_current(self, spec: ServiceSpec) -> bool:
        f = self.spec_file(spec.name)
        if not f.is_file() or not self.mode(spec.name):
            return False
        try:
            on_disk = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            return False
        want = run_spec(spec)
        for d in (on_disk, want):
            d.get("env", {}).pop("PATH", None)
        if on_disk != want:
            return False
        if self.mode(spec.name) == "task":
            return self.task_file(spec.name).read_bytes() == encode_task(render_task(spec, f))
        return True

    # commands -----------------------------------------------------------------------------------
    def _st(self, *args: str, mutate: bool = True):
        return self.ops.run([schtasks(), *args], mutate=mutate, timeout=60)

    def _winrun(self, name: str, flag: str):
        spec = json.loads(self.spec_file(name).read_text(encoding="utf-8")) if self.spec_file(name).is_file() else {}
        python = gui_python((spec.get("program") or ["python"])[0])
        return self.ops.run([python, "-m", "orbital.services.winrun", flag, str(self.spec_file(name))], timeout=60)

    def _runkey_command(self, spec: ServiceSpec) -> str:
        command, arguments = action(spec, self.spec_file(spec.name))
        return f"{_quote(command)} {arguments}"

    def _register_task(self, spec: ServiceSpec) -> tuple[bool, str]:
        r = self._st("/Create", "/TN", self.task_name(spec.name), "/XML", str(self.task_file(spec.name)), "/F")
        return r.ok, (r.stderr or r.stdout).strip()

    def _register_runkey(self, spec: ServiceSpec) -> tuple[bool, str]:
        cmd = self._runkey_command(spec)
        r = self.ops.run([reg(), "add", RUN_KEY, "/v", f"OrbitalAgents-{spec.name}", "/t", "REG_SZ",
                          "/d", cmd, "/f"], timeout=30)
        if not r.ok:
            return False, (r.stderr or r.stdout).strip() or "reg add failed"
        self.ops.write(self.runkey_file(spec.name), cmd + "\n")
        return True, ""

    def install(self, spec: ServiceSpec, restart: bool = False) -> tuple[bool, str]:
        self.ops.mkdir(spec.log_path.parent)
        self.ops.mkdir(self.dir)
        old_mode = self.mode(spec.name)
        changed = self.ops.write(self.spec_file(spec.name), render_spec(spec))
        how = ""
        if old_mode == "runkey":
            # Stay on the Run key; refresh the command in case the interpreter moved.
            if changed or not self.is_current(spec):
                ok, msg = self._register_runkey(spec)
                if not ok:
                    return False, msg
                changed = True
            how = " (logon Run key)"
        else:
            task_xml = encode_task(render_task(spec, self.spec_file(spec.name)))
            changed |= self.ops.write(self.task_file(spec.name), task_xml)
            if changed or not old_mode:
                if old_mode == "task":
                    self._st("/End", "/TN", self.task_name(spec.name))
                ok, msg = self._register_task(spec)
                if not ok and _denied(msg):
                    self.ops.remove(self.task_file(spec.name))
                    ok, msg2 = self._register_runkey(spec)
                    if not ok:
                        return False, f"Task Scheduler refused ({msg}); Run key failed too ({msg2})"
                    how = " (Task Scheduler refused, using the logon Run key)"
                elif not ok:
                    self.ops.remove(self.task_file(spec.name))
                    return False, msg or "schtasks /Create failed"
        if self.mode(spec.name) == "runkey" or how:
            if changed or restart:
                self._winrun(spec.name, "--stop")
            r = self._winrun(spec.name, "--detach")
        else:
            if restart and not changed and old_mode:
                self._st("/End", "/TN", self.task_name(spec.name))
            r = self._st("/Run", "/TN", self.task_name(spec.name))
        if not r.ok:
            return False, (r.stderr or r.stdout).strip() or "start failed"
        if changed:
            return True, "installed and started" + how
        return True, ("restarted" if restart else "unchanged, started") + how

    def uninstall(self, name: str) -> tuple[bool, str]:
        mode = self.mode(name)
        if mode == "task" or (self.spec_file(name).is_file() and not mode):
            self._st("/End", "/TN", self.task_name(name))
            self._st("/Delete", "/TN", self.task_name(name), "/F")
        if mode == "runkey":
            self._winrun(name, "--stop")
            self.ops.run([reg(), "delete", RUN_KEY, "/v", f"OrbitalAgents-{name}", "/f"], timeout=30)
        removed = False
        for f in self.files(name):
            removed |= self.ops.remove(f)
        return True, "removed" if removed else "not installed"

    def _task_info(self, name: str) -> dict | None:
        """State and last result from PowerShell (English enum names, unlike localized schtasks output)."""
        script = (
            "$ErrorActionPreference='Stop';"
            f"$t=Get-ScheduledTask -TaskPath '\\{TASK_FOLDER}\\' -TaskName '{UNIT_PREFIX}{name}';"
            "$i=$t|Get-ScheduledTaskInfo;"
            "[pscustomobject]@{State=$t.State.ToString();LastTaskResult=$i.LastTaskResult;"
            "LastRunTime=if($i.LastRunTime){$i.LastRunTime.ToString('o')}else{''}}|ConvertTo-Json -Compress"
        )
        r = self.ops.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", script], mutate=False, timeout=30)
        if not r.ok:
            return None
        try:
            data = json.loads(r.stdout.strip() or "{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _supervisor(self, name: str) -> dict:
        from orbital.services import winrun

        try:
            spec = json.loads(self.spec_file(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        state = winrun.read_state(spec)
        state["alive"] = winrun.pid_alive(state.get("supervisor_pid"))
        return state

    def status(self, name: str) -> dict:
        info = {"installed": self.spec_file(name).is_file() and bool(self.mode(name)), "loaded": False,
                "running": False, "pid": None, "last_exit": None, "state": "not installed"}
        if not info["installed"]:
            return info
        sup = self._supervisor(name)
        info["last_exit"] = sup.get("last_exit")
        if self.mode(name) == "runkey":
            info["loaded"] = True
            info["running"] = bool(sup.get("alive"))
            info["state"] = "running (Run key)" if info["running"] else "stopped (Run key)"
        else:
            t = self._task_info(name)
            if t is None:
                info["state"] = "task not registered" if self._st("/Query", "/TN", self.task_name(name),
                                                                     mutate=False).returncode else "unknown (powershell failed)"
                info["loaded"] = info["state"] == "unknown (powershell failed)"
                info["running"] = info["loaded"] and bool(sup.get("alive"))
            else:
                state = str(t.get("State", ""))
                info["loaded"] = state != "Disabled"
                info["running"] = state == "Running"
                info["state"] = state.lower() or "unknown"
                if info["last_exit"] is None and t.get("LastTaskResult") not in (None, 267011):   # 267011: never run
                    info["last_exit"] = t.get("LastTaskResult")
        if info["running"]:
            info["pid"] = sup.get("pid") or sup.get("supervisor_pid")
            if sup.get("restarts"):
                info["state"] += f", restarted {sup['restarts']}x"
        return info

    def restart(self, name: str) -> tuple[bool, str]:
        if not self.mode(name):
            return False, "not installed"
        ok, msg = self.stop(name)
        if not ok:
            return False, msg
        ok, msg = self.start(name)
        return ok, "restarted" if ok else msg

    def start(self, name: str) -> tuple[bool, str]:
        mode = self.mode(name)
        if not mode:
            return False, "not installed"
        r = self._winrun(name, "--detach") if mode == "runkey" else self._st("/Run", "/TN", self.task_name(name))
        return (r.ok, "started" if r.ok else (r.stderr or r.stdout).strip())

    def stop(self, name: str) -> tuple[bool, str]:
        mode = self.mode(name)
        if not mode:
            return True, "not installed"
        r = self._winrun(name, "--stop") if mode == "runkey" else self._st("/End", "/TN", self.task_name(name))
        return (r.ok, "stopped" if r.ok else (r.stderr or r.stdout).strip())

    def finish(self) -> list[str]:
        names = self.installed_names()
        if any(self.mode(n) == "runkey" for n in names):
            return ["Some services use the logon Run key because Task Scheduler refused a logon task. They start "
                    "when you sign in but are not restarted by Windows; the supervisor still restarts them on crash."]
        return []
