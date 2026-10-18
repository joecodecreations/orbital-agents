"""install / uninstall / status / restart against fake launchctl and systemctl, in a temp HOME."""

from __future__ import annotations

import io
import plistlib
from pathlib import Path

from orbital import services
from orbital.services.launchd import parse_print
from orbital.services.ops import Ops, Result

PRINT_RUNNING = """gui/501/io.orbitalagents.hub = {
\tactive count = 1
\tpath = /x/io.orbitalagents.hub.plist
\ttype = LaunchAgent
\tstate = running

\tprogram = /x/python
\tdefault environment = {
\t\tPATH => /usr/bin:/bin
\t}
\tpid = 4242
\tlast exit code = (never exited)
}
"""


def lc_rules(loaded: set[str]):
    """Fake launchctl: `print` succeeds for labels in `loaded`; bootstrap/bootout update the set."""
    def printer(argv):
        label = argv[-1].rsplit("/", 1)[-1]
        if label in loaded:
            return Result(0, PRINT_RUNNING.replace("io.orbitalagents.hub", label))
        return Result(113, "", "Could not find service")

    def bootstrap(argv):
        loaded.add(Path(argv[-1]).name[: -len(".plist")])
        return Result(0)

    def bootout(argv):
        loaded.discard(argv[-1].rsplit("/", 1)[-1])
        return Result(0)

    return {("launchctl", "print"): printer, ("launchctl", "bootstrap"): bootstrap,
            ("launchctl", "bootout"): bootout}


def ops_with(fake, dry=False):
    return Ops(dry_run=dry, out=lambda line: None, exec_fn=fake)


def test_parse_print():
    got = parse_print(PRINT_RUNNING)
    assert got == {"state": "running", "pid": 4242, "last_exit": "(never exited)"}


def test_install_writes_plists_and_bootstraps(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    home, _ = homes
    cfg = make_cfg()
    loaded: set[str] = set()
    fake = fake_exec(lc_rules(loaded))
    outcomes, notes = services.install(cfg, ops=ops_with(fake), platform="darwin", home=home)
    names = [o.name for o in outcomes]
    assert names == ["hub", "satellite", "scheduler", "browser-bridge", "watchdog"]
    assert all(o.ok for o in outcomes), outcomes
    agents = home / "Library" / "LaunchAgents"
    assert sorted(p.name for p in agents.iterdir()) == sorted(f"io.orbitalagents.{n}.plist" for n in names)
    assert loaded == {f"io.orbitalagents.{n}" for n in names}
    assert any("tracker app isn't installed" in n for n in notes)
    plist = plistlib.loads((agents / "io.orbitalagents.hub.plist").read_bytes())
    assert plist["EnvironmentVariables"]["ORBITAL_HOME"] == str(cfg.paths.home)
    assert cfg.paths.logs.is_dir()


def test_reinstall_unchanged_does_not_reload(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    home, _ = homes
    cfg = make_cfg()
    loaded: set[str] = set()
    fake = fake_exec(lc_rules(loaded))
    services.install(cfg, ops=ops_with(fake), platform="darwin", home=home)
    fake.calls.clear()
    outcomes, _ = services.install(cfg, ops=ops_with(fake), platform="darwin", home=home)
    assert all(o.message == "unchanged, loaded" for o in outcomes)
    assert not [c for c in fake.calls if c[1] in ("bootstrap", "bootout")]


def test_role_change_removes_unwanted(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    home, _ = homes
    loaded: set[str] = set()
    fake = fake_exec(lc_rules(loaded))
    services.install(make_cfg(), ops=ops_with(fake), platform="darwin", home=home)
    sat = make_cfg(system__role="satellite", hub__url="http://hub.example:17750")
    outcomes, _ = services.install(sat, ops=ops_with(fake), platform="darwin", home=home)
    removed = {o.name for o in outcomes if o.message.startswith("removed")}
    assert removed == {"hub", "scheduler"}
    agents = home / "Library" / "LaunchAgents"
    assert not (agents / "io.orbitalagents.hub.plist").exists()
    assert "io.orbitalagents.hub" not in loaded


def test_dry_run_changes_nothing(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    home, _ = homes
    cfg = make_cfg()
    lines: list[str] = []
    fake = fake_exec(lc_rules(set()))
    ops = Ops(dry_run=True, out=lines.append, exec_fn=fake)
    services.install(cfg, ops=ops, platform="darwin", home=home)
    assert not (home / "Library").exists()
    assert not [c for c in fake.calls if c[1] != "print"], "dry run ran a mutating launchctl command"
    assert any("bootstrap" in line for line in lines) and any("write:" in line for line in lines)


def test_status_restart_stop(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    home, _ = homes
    cfg = make_cfg()
    loaded: set[str] = set()
    fake = fake_exec(lc_rules(loaded))
    services.install(cfg, ops=ops_with(fake), platform="darwin", home=home)
    rows = {r.name: r for r in services.status(cfg, ops=ops_with(fake), platform="darwin", home=home, health=False)}
    assert rows["hub"].running and rows["hub"].pid == 4242 and rows["hub"].current
    assert rows["tracker"].wanted and not rows["tracker"].installed
    fake.calls.clear()
    out = services.restart(cfg, ["hub"], ops=ops_with(fake), platform="darwin", home=home)
    assert out[0].ok and ["launchctl", "kickstart", "-k", f"gui/{__import__('os').getuid()}/io.orbitalagents.hub"] in fake.calls
    out = services.stop(cfg, ["hub"], ops=ops_with(fake), platform="darwin", home=home)
    assert out[0].ok and "io.orbitalagents.hub" not in loaded
    out = services.start(cfg, ["hub"], ops=ops_with(fake), platform="darwin", home=home)
    assert out[0].ok and "io.orbitalagents.hub" in loaded


def test_uninstall_all(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    home, _ = homes
    cfg = make_cfg()
    loaded: set[str] = set()
    fake = fake_exec(lc_rules(loaded))
    services.install(cfg, ops=ops_with(fake), platform="darwin", home=home)
    out = services.uninstall(cfg, ops=ops_with(fake), platform="darwin", home=home)
    assert {o.name for o in out} == {"hub", "satellite", "scheduler", "browser-bridge", "watchdog"}
    assert not loaded
    assert not list((home / "Library" / "LaunchAgents").glob("io.orbitalagents.*"))


def test_unknown_service_name(make_cfg, homes, fake_exec):
    import pytest
    with pytest.raises(ValueError):
        services.restart(make_cfg(), ["nope"], ops=ops_with(fake_exec()), platform="darwin", home=homes[0])


def test_systemd_install_and_status(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_SYSTEMCTL", "systemctl")
    home, _ = homes
    cfg = make_cfg()
    show = Result(0, "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=99\nExecMainStatus=0\n")
    fake = fake_exec({("systemctl", "--user", "show"): show,
                      ("loginctl",): Result(0, "Linger=no\n")})
    outcomes, notes = services.install(cfg, ops=ops_with(fake), platform="linux", home=home)
    assert [o.name for o in outcomes] == ["hub", "satellite", "scheduler", "browser-bridge", "watchdog"]
    units = home / ".config" / "systemd" / "user"
    assert (units / "orbital-hub.service").is_file() and (units / "orbital-watchdog.timer").is_file()
    verbs = [" ".join(c[2:4]) for c in fake.calls if c[0] == "systemctl"]
    assert "daemon-reload" in verbs[0]
    assert "enable orbital-hub.service" in verbs and "restart orbital-hub.service" in verbs
    assert "enable orbital-watchdog.timer" in verbs
    assert any("enable-linger" in n for n in notes)
    rows = {r.name: r for r in services.status(cfg, ops=ops_with(fake), platform="linux", home=home, health=False)}
    assert rows["hub"].running and rows["hub"].pid == 99


def test_cli_status_json(make_cfg, homes, capsys, monkeypatch):
    from orbital.cli.main import main
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = main(["services", "status", "--json", "--no-health"])
    out = capsys.readouterr().out
    assert '"name": "hub"' in out
    assert rc in (0, 1)


def test_installed_path_reads_the_service_files(make_cfg, homes, fake_exec, monkeypatch):
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "launchctl")
    monkeypatch.setenv("ORBITAL_SYSTEMCTL", "systemctl")
    home, _ = homes
    cfg = make_cfg()
    assert services.installed_path("darwin", home) is None
    specs = services.build_specs(cfg, platform="darwin", path_value="/svc/bin:/usr/bin", tracker_bin=None)
    services.install(cfg, ops=ops_with(fake_exec(lc_rules(set()))), platform="darwin", home=home, specs=specs)
    assert services.installed_path("darwin", home) == "/svc/bin:/usr/bin"
    specs = services.build_specs(cfg, platform="linux", path_value='/odd "dir"/bin:/usr/bin', tracker_bin=None)
    services.install(cfg, ops=ops_with(fake_exec()), platform="linux", home=home, specs=specs)
    assert services.installed_path("linux", home) == '/odd "dir"/bin:/usr/bin'
