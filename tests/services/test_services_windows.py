"""The Windows backend (Task Scheduler + winrun supervisor) against fake schtasks / powershell / reg,
in a temp HOME. Nothing here needs Windows."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

import pytest

from orbital import services
from orbital.services import spec as spec_mod
from orbital.services import windows, winrun
from orbital.services.ops import Ops, Result

NAMES = ["hub", "satellite", "scheduler", "browser-bridge", "watchdog"]
NS = {"t": windows.TASK_NS}


@pytest.fixture(autouse=True)
def win_tools(monkeypatch):
    monkeypatch.setenv("ORBITAL_SCHTASKS", "schtasks")
    monkeypatch.setenv("ORBITAL_POWERSHELL", "powershell")
    monkeypatch.setenv("ORBITAL_REG", "reg")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("USERDOMAIN", "LAB")
    monkeypatch.setenv("USERNAME", "builder")


def ops_with(fake, dry=False):
    return Ops(dry_run=dry, out=lambda line: None, exec_fn=fake)


def task_rules(registered: set[str], running: set[str], create: Result | None = None):
    """Fake schtasks + powershell: /Create, /Delete, /Run, /End update the sets; PowerShell reports them."""
    def name_of(argv):
        return argv[argv.index("/TN") + 1].rsplit("\\", 1)[-1]

    def st_create(argv):
        if create is not None:
            return create
        registered.add(name_of(argv))
        return Result(0, "SUCCESS: The scheduled task was successfully created.")

    def st_delete(argv):
        registered.discard(name_of(argv))
        running.discard(name_of(argv))
        return Result(0)

    def st_run(argv):
        n = name_of(argv)
        if n not in registered:
            return Result(1, "", "ERROR: The system cannot find the file specified.")
        running.add(n)
        return Result(0)

    def st_end(argv):
        running.discard(name_of(argv))
        return Result(0)

    def st_query(argv):
        return Result(0) if name_of(argv) in registered else Result(1, "", "ERROR: not found")

    def ps(argv):
        script = argv[-1]
        task = script.split("-TaskName '", 1)[1].split("'", 1)[0]
        if task not in registered:
            return Result(1, "", "No MSFT_ScheduledTask objects found")
        state = "Running" if task in running else "Ready"
        return Result(0, json.dumps({"State": state, "LastTaskResult": 267009 if state == "Running" else 0,
                                     "LastRunTime": "2026-10-19T08:00:00"}))

    return {("schtasks", "/Create"): st_create, ("schtasks", "/Delete"): st_delete, ("schtasks", "/Run"): st_run,
            ("schtasks", "/End"): st_end, ("schtasks", "/Query"): st_query, ("powershell",): ps}


def svc_dir(home):
    return home / "AppData" / "Local" / "OrbitalAgents" / "services"


def test_platform_name_and_backend(homes):
    assert spec_mod.platform_name("win32") == "windows"
    be = services.backend(Ops(dry_run=True, out=lambda line: None), platform="win32", home=homes[0])
    assert isinstance(be, windows.WindowsBackend)
    with pytest.raises(RuntimeError, match="macOS, Linux and Windows"):
        services.backend(Ops(), platform="sunos5")


def test_windows_specs_use_scripts_python_and_semicolon_path(make_cfg, homes, tmp_path):
    cfg = make_cfg()
    repo = tmp_path / "repo"
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts" / "python.exe").write_text("")
    assert spec_mod.repo_python(repo, "win32").endswith("python.exe")
    path = spec_mod.service_path(homes[0], repo, platform="win32")
    assert path.split(";")[0] == str(repo / ".venv" / "Scripts")
    specs = spec_mod.build_specs(cfg, platform="win32", repo=repo, path_value="C:\\v;C:\\Windows", tracker_bin=None)
    assert [s.name for s in specs] == NAMES          # no tracker on Windows
    assert specs[0].program[0].endswith("python.exe")


def test_install_registers_tasks_and_runs_them(make_cfg, homes, fake_exec):
    home, _ = homes
    cfg = make_cfg()
    registered: set[str] = set()
    running: set[str] = set()
    fake = fake_exec(task_rules(registered, running))
    outcomes, notes = services.install(cfg, ops=ops_with(fake), platform="win32", home=home)
    assert [o.name for o in outcomes] == NAMES
    assert all(o.ok and o.message == "installed and started" for o in outcomes), outcomes
    assert registered == running == {f"orbital-{n}" for n in NAMES}
    assert notes == []                               # no tracker note on Windows, no Run-key note

    d = svc_dir(home)
    run_spec = json.loads((d / "orbital-hub.json").read_text())
    assert run_spec["program"][1:] == ["-m", "orbital.hub"]
    assert run_spec["env"]["ORBITAL_HOME"] == str(cfg.paths.home)
    assert run_spec["log"].endswith("hub.stderr.log") and run_spec["kind"] == "daemon"
    wd = json.loads((d / "orbital-watchdog.json").read_text())
    assert wd["kind"] == "interval" and wd["interval"] >= 10

    raw = (d / "orbital-hub.xml").read_bytes()
    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff")    # UTF-16 with BOM
    root = ET.fromstring(raw.decode("utf-16").replace('encoding="UTF-16"', ""))
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text == "LAB\\builder"
    assert root.find("t:Settings/t:ExecutionTimeLimit", NS).text == "PT0S"
    assert root.find("t:Settings/t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert root.find("t:Principals/t:Principal/t:RunLevel", NS).text == "LeastPrivilege"
    args = root.find("t:Actions/t:Exec/t:Arguments", NS).text
    assert args.startswith("-m orbital.services.winrun ") and "orbital-hub.json" in args

    creates = [c for c in fake.calls if c[:2] == ["schtasks", "/Create"]]
    assert creates[0][2:4] == ["/TN", "\\OrbitalAgents\\orbital-hub"] and creates[0][-1] == "/F"


def test_reinstall_unchanged_does_not_reregister(make_cfg, homes, fake_exec):
    home, _ = homes
    cfg = make_cfg()
    registered: set[str] = set()
    running: set[str] = set()
    fake = fake_exec(task_rules(registered, running))
    services.install(cfg, ops=ops_with(fake), platform="win32", home=home)
    fake.calls.clear()
    outcomes, _ = services.install(cfg, ops=ops_with(fake), platform="win32", home=home)
    assert all(o.message == "unchanged, started" for o in outcomes), outcomes
    assert not [c for c in fake.calls if c[1] in ("/Create", "/Delete", "/End")]
    rows = {r.name: r for r in services.status(cfg, ops=ops_with(fake), platform="win32", home=home, health=False)}
    assert all(rows[n].current for n in NAMES)


def test_status_stop_start_restart(make_cfg, homes, fake_exec):
    home, _ = homes
    cfg = make_cfg()
    registered: set[str] = set()
    running: set[str] = set()
    fake = fake_exec(task_rules(registered, running))
    services.install(cfg, ops=ops_with(fake), platform="win32", home=home)
    # the supervisor's state file, as winrun writes it
    state = cfg.paths.run / "winsvc-hub.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    import os
    state.write_text(json.dumps({"supervisor_pid": os.getpid(), "pid": 4242, "restarts": 2, "last_exit": 1}))

    rows = {r.name: r for r in services.status(cfg, ops=ops_with(fake), platform="win32", home=home, health=False)}
    hub = rows["hub"]
    assert hub.installed and hub.loaded and hub.running and hub.current
    assert hub.pid == 4242 and hub.last_exit == 1 and "restarted 2x" in hub.state
    assert rows["tracker"].reason == "the tracker is a macOS app" and not rows["tracker"].installed

    out = services.stop(cfg, ["hub"], ops=ops_with(fake), platform="win32", home=home)
    assert out[0].ok and "orbital-hub" not in running
    rows = {r.name: r for r in services.status(cfg, ops=ops_with(fake), platform="win32", home=home, health=False)}
    assert rows["hub"].loaded and not rows["hub"].running and rows["hub"].state == "ready"
    out = services.start(cfg, ["hub"], ops=ops_with(fake), platform="win32", home=home)
    assert out[0].ok and "orbital-hub" in running
    fake.calls.clear()
    out = services.restart(cfg, ["hub"], ops=ops_with(fake), platform="win32", home=home)
    assert out[0].ok and out[0].message == "restarted"
    assert [c[1] for c in fake.calls if c[0] == "schtasks"] == ["/End", "/Run"]


def test_role_change_and_uninstall(make_cfg, homes, fake_exec):
    home, _ = homes
    registered: set[str] = set()
    running: set[str] = set()
    fake = fake_exec(task_rules(registered, running))
    services.install(make_cfg(), ops=ops_with(fake), platform="win32", home=home)
    sat = make_cfg(system__role="satellite", hub__url="http://hub.example:17750")
    outcomes, _ = services.install(sat, ops=ops_with(fake), platform="win32", home=home)
    assert {o.name for o in outcomes if o.message.startswith("removed")} == {"hub", "scheduler"}
    assert "orbital-hub" not in registered and not (svc_dir(home) / "orbital-hub.json").exists()
    out = services.uninstall(sat, ops=ops_with(fake), platform="win32", home=home)
    assert {o.name for o in out} == {"satellite", "browser-bridge", "watchdog"}
    assert not registered and not list(svc_dir(home).glob("orbital-*"))


def test_dry_run_changes_nothing(make_cfg, homes, fake_exec):
    home, _ = homes
    lines: list[str] = []
    fake = fake_exec(task_rules(set(), set()))
    services.install(make_cfg(), ops=Ops(dry_run=True, out=lines.append, exec_fn=fake), platform="win32", home=home)
    assert not (home / "AppData").exists()
    assert not [c for c in fake.calls if c[0] in ("schtasks", "reg") or "winrun" in c]
    assert any("schtasks /Create" in line for line in lines)


def test_access_denied_falls_back_to_run_key(make_cfg, homes, fake_exec):
    home, _ = homes
    cfg = make_cfg()
    denied = Result(1, "", "ERROR: Access is denied.")
    fake = fake_exec(task_rules(set(), set(), create=denied))
    outcomes, notes = services.install(cfg, ops=ops_with(fake), platform="win32", home=home, names=["satellite"])
    assert outcomes[0].ok and "Run key" in outcomes[0].message, outcomes
    d = svc_dir(home)
    assert (d / "orbital-satellite.runkey").is_file() and not (d / "orbital-satellite.xml").exists()
    adds = [c for c in fake.calls if c[:2] == ["reg", "add"]]
    assert adds and adds[0][2] == windows.RUN_KEY and adds[0][4] == "OrbitalAgents-satellite"
    assert "orbital.services.winrun" in adds[0][-2]
    assert [c for c in fake.calls if "--detach" in c]           # started now, not just at next logon
    assert any("Run key" in n for n in notes)

    fake.calls.clear()
    out = services.stop(cfg, ["satellite"], ops=ops_with(fake), platform="win32", home=home)
    assert out[0].ok and [c for c in fake.calls if "--stop" in c] and not [c for c in fake.calls if c[0] == "schtasks"]
    # a reinstall stays on the Run key and doesn't retry Task Scheduler
    fake.calls.clear()
    outcomes, _ = services.install(cfg, ops=ops_with(fake), platform="win32", home=home, names=["satellite"])
    assert outcomes[0].ok and not [c for c in fake.calls if c[:2] == ["schtasks", "/Create"]]
    services.uninstall(cfg, ["satellite"], ops=ops_with(fake), platform="win32", home=home)
    assert [c for c in fake.calls if c[:2] == ["reg", "delete"]] and not list(d.glob("orbital-satellite*"))


def test_other_create_error_is_reported(make_cfg, homes, fake_exec):
    home, _ = homes
    fake = fake_exec(task_rules(set(), set(), create=Result(1, "", "ERROR: The task XML is malformed.")))
    outcomes, _ = services.install(make_cfg(), ops=ops_with(fake), platform="win32", home=home, names=["hub"])
    assert not outcomes[0].ok and "malformed" in outcomes[0].message
    assert not (svc_dir(home) / "orbital-hub.xml").exists()       # so status says "not installed", not "current"


def test_installed_path_reads_the_run_spec(make_cfg, homes, fake_exec):
    home, _ = homes
    cfg = make_cfg()
    specs = services.build_specs(cfg, platform="win32", path_value="C:\\svc;C:\\Windows", tracker_bin=None)
    services.install(cfg, ops=ops_with(fake_exec(task_rules(set(), set()))), platform="win32", home=home, specs=specs)
    assert services.installed_path("win32", home) == "C:\\svc;C:\\Windows"


def test_cli_dispatches_to_windows(make_cfg, homes, fake_exec, monkeypatch, capsys):
    """`orbital services install` on a Windows machine goes through the windows backend."""
    from orbital.cli.main import main
    real = services.backend
    fake = fake_exec(task_rules(set(), set()))
    monkeypatch.setattr(services, "backend",
                        lambda ops=None, platform=None, home=None: real(Ops(dry_run=False, out=print, exec_fn=fake),
                                                                      "win32", homes[0]))
    monkeypatch.setattr(services, "build_specs",
                        lambda cfg, platform=None, home=None, **kw: spec_mod.build_specs(cfg, platform="win32", home=home,
                                                                                         tracker_bin=None))
    assert main(["services", "install", "hub"]) == 0
    assert any(c[:2] == ["schtasks", "/Create"] for c in fake.calls)
    assert "installed and started" in capsys.readouterr().out


# --------------------------------------------------------------------------- the supervisor


def _spec(tmp_path, code: str, kind="daemon", interval=0) -> dict:
    return {"name": "demo", "program": [sys.executable, "-c", code], "env": {"ORBITAL_HOME": str(tmp_path / "oh"),
            "DEMO_VALUE": "from-spec"}, "working_dir": str(tmp_path), "log": str(tmp_path / "logs" / "demo.log"),
            "kind": kind, "interval": interval}


def test_winrun_restarts_a_daemon_and_logs(tmp_path):
    spec = _spec(tmp_path, "import os,sys; print('value', os.environ['DEMO_VALUE']); sys.exit(3)")
    sleeps: list[float] = []
    code = winrun.supervise(spec, max_runs=2, sleep=sleeps.append)
    assert code == 3 and sleeps == [winrun.THROTTLE]
    log = (tmp_path / "logs" / "demo.log").read_text()
    assert log.count("value from-spec") == 2 and "winrun: demo exited 3" in log
    state = winrun.read_state(spec)
    assert state["restarts"] == 2 and state["last_exit"] == 3 and state["pid"] is None


def test_winrun_runs_interval_service_every_interval(tmp_path):
    spec = _spec(tmp_path, "print('pass')", kind="interval", interval=60)
    sleeps: list[float] = []
    assert winrun.supervise(spec, max_runs=3, sleep=sleeps.append) == 0
    assert sleeps == [60, 60]
    assert winrun.read_state(spec)["restarts"] == 0


def test_winrun_missing_program_is_logged(tmp_path):
    spec = _spec(tmp_path, "")
    spec["program"] = [str(tmp_path / "nope.exe")]
    assert winrun.supervise(spec, max_runs=1, sleep=lambda s: None) == 127
    assert "could not start" in (tmp_path / "logs" / "demo.log").read_text()


def test_winrun_stop_when_not_running(tmp_path, capsys):
    f = tmp_path / "demo.json"
    f.write_text(json.dumps(_spec(tmp_path, "")))
    assert winrun.main(["--stop", str(f)]) == 0
    assert "was not running" in capsys.readouterr().out


def test_winrun_detach_then_stop(tmp_path):
    """--detach starts the supervisor in the background; --stop ends it and its child (POSIX stand-in)."""
    import time
    spec = _spec(tmp_path, "import time; time.sleep(60)")
    f = tmp_path / "demo.json"
    f.write_text(json.dumps(spec))
    assert winrun.main(["--detach", str(f)]) == 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not winrun.read_state(spec).get("pid"):
        time.sleep(0.1)
    state = winrun.read_state(spec)
    assert winrun.pid_alive(state["supervisor_pid"]) and winrun.pid_alive(state["pid"])
    assert winrun.main(["--detach", str(f)]) == 0           # second start is a no-op
    assert winrun.main(["--stop", str(f)]) == 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and winrun.pid_alive(state["pid"]):
        time.sleep(0.1)
    assert not winrun.pid_alive(state["pid"])
