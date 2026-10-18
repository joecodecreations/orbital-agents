"""Which services run where, and the exact LaunchAgent plists / systemd units they become."""

from __future__ import annotations

import plistlib
from pathlib import Path

from orbital.services import launchd, systemd
from orbital.services.spec import build_specs, enabled_names, service_path, wanted

PY = "/opt/orbital/.venv/bin/python"
REPO = Path("/opt/orbital")
PATHV = "/opt/orbital/.venv/bin:/usr/bin:/bin"


def specs(cfg, platform="darwin", tracker_bin=None):
    return {s.name: s for s in build_specs(cfg, platform=platform, python=PY, repo=REPO, path_value=PATHV,
                                           tracker_bin=tracker_bin)}


def test_hub_runs_everything_on_macos(make_cfg):
    cfg = make_cfg()
    assert enabled_names(cfg, "darwin") == ["hub", "satellite", "scheduler", "browser-bridge", "watchdog", "tracker"]


def test_satellite_runs_no_hub_or_scheduler(make_cfg):
    cfg = make_cfg(system__role="satellite", hub__url="http://hub.example:17750")
    assert enabled_names(cfg, "darwin") == ["satellite", "browser-bridge", "watchdog", "tracker"]
    reasons = wanted(cfg, "darwin")
    assert reasons["hub"] and reasons["scheduler"]


def test_components_turned_off(make_cfg):
    cfg = make_cfg(browser__enabled=False, scheduler__enabled=False, tracker__enabled=False, satellite__enabled=False)
    assert enabled_names(cfg, "darwin") == ["hub", "watchdog"]
    assert wanted(cfg, "darwin")["browser-bridge"] == "browser.enabled = false"


def test_linux_has_no_tracker(make_cfg):
    cfg = make_cfg()
    assert "tracker" not in enabled_names(cfg, "linux")
    assert wanted(cfg, "linux")["tracker"] == "the tracker is a macOS app"


def test_tracker_left_out_until_installed(make_cfg):
    cfg = make_cfg()
    assert "tracker" not in specs(cfg, tracker_bin=None)
    assert specs(cfg, tracker_bin=Path("/Applications/T.app/Contents/MacOS/OrbitalTracker"))["tracker"].kind == "app"


def test_hub_plist_golden(make_cfg):
    cfg = make_cfg()
    hub = specs(cfg)["hub"]
    got = plistlib.loads(launchd.render(hub))
    logs = cfg.paths.logs
    assert got == {
        "Label": "io.orbitalagents.hub",
        "ProgramArguments": [PY, "-m", "orbital.hub"],
        "EnvironmentVariables": {"ORBITAL_HOME": str(cfg.paths.home), "PATH": PATHV, "PYTHONUNBUFFERED": "1"},
        "WorkingDirectory": str(REPO),
        "StandardOutPath": str(logs / "hub.stderr.log"),
        "StandardErrorPath": str(logs / "hub.stderr.log"),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
    }


def test_module_names(make_cfg):
    s = specs(make_cfg())
    assert s["scheduler"].program[1:] == ["-m", "orbital.scheduler"]
    assert s["satellite"].program[1:] == ["-m", "orbital.satellite"]
    assert s["browser-bridge"].program[1:] == ["-m", "orbital.browser.server"]
    assert s["watchdog"].program[1:] == ["-m", "orbital.scheduler.watchdog"]


def test_watchdog_plist_is_interval(make_cfg):
    cfg = make_cfg(watchdog__interval_seconds=45)
    got = plistlib.loads(launchd.render(specs(cfg)["watchdog"]))
    assert got["StartInterval"] == 45
    assert "KeepAlive" not in got
    assert got["RunAtLoad"] is True


def test_tracker_plist(make_cfg):
    cfg = make_cfg()
    binary = Path("/Applications/Orbital Agents - Agent Satellite Tracker.app/Contents/MacOS/OrbitalTracker")
    got = plistlib.loads(launchd.render(specs(cfg, tracker_bin=binary)["tracker"]))
    assert got["ProgramArguments"] == [str(binary)]
    assert got["KeepAlive"] == {"SuccessfulExit": False}
    assert got["LimitLoadToSessionType"] == "Aqua"
    assert got["EnvironmentVariables"]["ORBITAL_HOME"] == str(cfg.paths.home)


def test_plists_differing_only_in_path_count_as_current(make_cfg):
    cfg = make_cfg()
    a = build_specs(cfg, platform="darwin", python=PY, repo=REPO, path_value="/a:/usr/bin", tracker_bin=None)[0]
    b = build_specs(cfg, platform="darwin", python=PY, repo=REPO, path_value="/b:/usr/bin", tracker_bin=None)[0]
    assert launchd.same_except_path(launchd.render(a), launchd.render(b))
    c = build_specs(cfg, platform="darwin", python="/other/python", repo=REPO, path_value="/a", tracker_bin=None)[0]
    assert not launchd.same_except_path(launchd.render(a), launchd.render(c))


def test_systemd_service_golden(make_cfg):
    cfg = make_cfg()
    text = systemd.render_service(specs(cfg, platform="linux")["hub"])
    lines = text.splitlines()
    assert "[Service]" in lines and "[Install]" in lines
    assert f"ExecStart={PY} -m orbital.hub" in lines
    assert f"WorkingDirectory={REPO}" in lines
    assert f'Environment="ORBITAL_HOME={cfg.paths.home}"' in lines
    assert f'Environment="PATH={PATHV}"' in lines
    assert "Restart=always" in lines and "RestartSec=10" in lines
    assert f"StandardError=append:{cfg.paths.logs / 'hub.stderr.log'}" in lines
    assert "WantedBy=default.target" in lines


def test_systemd_watchdog_timer(make_cfg):
    cfg = make_cfg(watchdog__interval_seconds=90)
    spec = specs(cfg, platform="linux")["watchdog"]
    service = systemd.render_service(spec)
    timer = systemd.render_timer(spec)
    assert "Type=oneshot" in service and "Restart=always" not in service and "[Install]" not in service
    assert "OnUnitActiveSec=90s" in timer and "Unit=orbital-watchdog.service" in timer
    assert "WantedBy=timers.target" in timer


def test_systemd_exec_quoting():
    assert systemd._exec_line(["/a b/python", "-m", "x%y"]) == "'/a b/python' -m x%%y"


def test_service_path_has_essentials(tmp_path):
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    value = service_path(home=tmp_path, repo=tmp_path / "repo").split(":")
    assert str(tmp_path / ".local" / "bin") in value
    assert value[-4:] == ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    assert len(value) == len(set(value))
