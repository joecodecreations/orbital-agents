"""Watchdog: which checks apply, failure counting, restart commands (mocked), spacing, alerts, probes."""

from __future__ import annotations

import json
import time

from fastapi import FastAPI, Request

from orbital.scheduler.watchdog import (RESTARTS_PER_HOUR, Check, Watchdog, checks_for, probe, restart_command,
                                        service_installed)
from scheduler_helpers import ServerThread, free_port, setup_home


class Fake:
    """Prober/runner/alerter doubles that record what the watchdog asked for."""

    def __init__(self, healthy: dict[str, bool] | None = None, installed: set[str] | None = None, code: int = 0):
        self.healthy = healthy or {}
        self.installed_set = installed if installed is not None else {"hub", "satellite", "browser-bridge", "tracker", "scheduler"}
        self.code = code
        self.commands: list[list[str]] = []
        self.alerts: list[tuple[str, str]] = []

    def prober(self, check: Check):
        ok = self.healthy.get(check.name, True)
        return ok, "HTTP 200" if ok else "ConnectError"

    def runner(self, argv):
        self.commands.append(argv)
        return self.code, ""

    def alerter(self, title, body):
        self.alerts.append((title, body))
        return True

    def installed(self, name):
        return name in self.installed_set


def dog(cfg, fake: Fake, platform="darwin", **kw):
    return Watchdog(cfg, platform=platform, runner=fake.runner, prober=fake.prober, installed=fake.installed,
                    alerter=fake.alerter, janitor=False, **kw)


def test_checks_depend_on_role_and_settings(home):
    hub = setup_home(home, None, config="[browser]\nenabled = true\n")
    assert [c.name for c in checks_for(hub, "darwin")] == ["hub", "satellite", "browser-bridge", "tracker", "scheduler"]
    assert [c.name for c in checks_for(hub, "linux")] == ["hub", "satellite", "browser-bridge", "scheduler"]
    sat = setup_home(home, None, config='[system]\nrole = "satellite"\n[browser]\nenabled = false\n[tracker]\nenabled = false\n')
    assert [c.name for c in checks_for(sat, "darwin")] == ["satellite"]
    urls = {c.name: c.target for c in checks_for(hub, "darwin")}
    assert urls["hub"] == "http://127.0.0.1:7700/v1/health"
    assert urls["satellite"] == "http://127.0.0.1:7704/v1/health"
    assert urls["browser-bridge"] == "http://127.0.0.1:7703/health"
    assert urls["tracker"] == "http://127.0.0.1:7701/v1/health"
    assert urls["scheduler"].endswith("run/scheduler.json")


def test_restart_commands():
    class Cfg:
        def get(self, key, default=None):
            return {"apple.bundle_prefix": "com.example"}.get(key, default)

    cfg = Cfg()
    assert restart_command("hub", cfg, "darwin", uid=501) == ["/bin/launchctl", "kickstart", "-k", "gui/501/io.orbitalagents.hub"]
    assert restart_command("browser-bridge", cfg, "darwin", uid=501)[-1] == "gui/501/io.orbitalagents.browser-bridge"
    assert restart_command("satellite", cfg, "linux") == ["systemctl", "--user", "restart", "orbital-satellite"]
    assert restart_command("tracker", cfg, "darwin", app_path="/Applications/Tracker.app") == ["/usr/bin/open", "-g", "/Applications/Tracker.app"]
    assert restart_command("tracker", cfg, "darwin", app_path="") == ["/usr/bin/open", "-g", "-b", "com.example.orbitalagents.tracker"]
    assert restart_command("hub", cfg, "win32") is None


def test_service_installed_looks_for_unit_files(home, tmp_path):
    cfg = setup_home(home, None)
    agents, units = tmp_path / "LaunchAgents", tmp_path / "units"
    agents.mkdir()
    units.mkdir()
    assert not service_installed("hub", cfg, "darwin", agents_dir=agents)
    (agents / "io.orbitalagents.hub.plist").write_text("<plist/>")
    assert service_installed("hub", cfg, "darwin", agents_dir=agents)
    (units / "orbital-satellite.service").write_text("[Unit]")
    assert service_installed("satellite", cfg, "linux", units_dir=units)
    assert not service_installed("hub", cfg, "linux", units_dir=units)


def test_failures_are_counted_then_the_service_is_restarted(home):
    cfg = setup_home(home, None, config='[watchdog]\nfailures_before_restart = 2\n[tracker]\nenabled = false\n')
    fake = Fake(healthy={"satellite": False})
    t0 = 1_800_000_000.0
    first = dog(cfg, fake).run_once(now=t0)
    assert first["services"]["satellite"]["failures"] == 1 and not fake.commands
    second = dog(cfg, fake).run_once(now=t0 + 60)
    assert fake.commands == [["/bin/launchctl", "kickstart", "-k", f"gui/{__import__('os').getuid()}/io.orbitalagents.satellite"]]
    record = second["services"]["satellite"]
    assert record["status"] == "restarted" and record["failures"] == 0 and record["alerted"] is True
    assert fake.alerts[0][0] == "Self-heal: restarted satellite"
    assert second["services"]["hub"]["status"] == "up"
    saved = json.loads((cfg.paths.run / "watchdog.json").read_text())
    assert saved["services"]["satellite"]["restarts"] == [t0 + 60]
    # Still down right after: counted again, but no second restart inside the gap.
    dog(cfg, fake).run_once(now=t0 + 120)
    dog(cfg, fake).run_once(now=t0 + 150)
    assert len(fake.commands) == 1
    # After the gap it may restart again.
    dog(cfg, fake).run_once(now=t0 + 60 + 200)
    assert len(fake.commands) == 2


def test_a_recovered_service_resets_its_count(home):
    cfg = setup_home(home, None, config='[watchdog]\nfailures_before_restart = 3\n')
    fake = Fake(healthy={"hub": False})
    dog(cfg, fake).run_once(now=1000)
    dog(cfg, fake).run_once(now=1060)
    fake.healthy["hub"] = True
    state = dog(cfg, fake).run_once(now=1120)
    assert state["services"]["hub"]["failures"] == 0 and state["services"]["hub"]["status"] == "up"
    fake.healthy["hub"] = False
    dog(cfg, fake).run_once(now=1180)
    dog(cfg, fake).run_once(now=1240)
    assert not fake.commands, "the count started over"


def test_not_installed_services_are_left_alone(home):
    cfg = setup_home(home, None, config='[watchdog]\nfailures_before_restart = 1\n')
    fake = Fake(healthy={"browser-bridge": False, "tracker": False}, installed={"hub", "satellite", "scheduler"})
    state = dog(cfg, fake).run_once(now=5000)
    assert state["services"]["tracker"]["status"] == "not installed"
    assert not fake.commands and not fake.alerts


def test_dry_run_changes_nothing(home):
    cfg = setup_home(home, None, config='[watchdog]\nfailures_before_restart = 1\n')
    fake = Fake(healthy={"hub": False})
    state = dog(cfg, fake, dry_run=True).run_once(now=5000)
    assert not fake.commands
    assert any("restarting" in note for note in state["notes"])
    assert not (cfg.paths.run / "watchdog.json").exists()


def test_failed_restart_and_restart_ceiling(home):
    cfg = setup_home(home, None, config='[watchdog]\nfailures_before_restart = 1\ninterval_seconds = 1\n')
    fake = Fake(healthy={"satellite": False}, code=3)
    state = dog(cfg, fake).run_once(now=10_000)
    assert state["services"]["satellite"]["status"] == "restart failed"
    assert fake.alerts[0][0] == "Self-heal: could not restart satellite"
    t = 10_000
    for _ in range(12):
        t += 125
        dog(cfg, fake).run_once(now=t)
    assert len(fake.commands) == RESTARTS_PER_HOUR, "at most six restarts an hour"
    assert any("keeps failing" in title for title, _ in fake.alerts)


def test_scheduler_heartbeat_check(home):
    cfg = setup_home(home, None)
    check = next(c for c in checks_for(cfg, "linux") if c.name == "scheduler")
    assert probe(check) == (False, "no heartbeat file")
    cfg.paths.run.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (cfg.paths.run / "scheduler.json").write_text(json.dumps({"heartbeat_at": stamp}))
    assert probe(check)[0] is True
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    (cfg.paths.run / "scheduler.json").write_text(json.dumps({"heartbeat_at": old}))
    healthy, detail = probe(check)
    assert healthy is False and "old" in detail


def test_http_probe_against_real_ports():
    app = FastAPI()

    @app.get("/v1/health")
    def health():
        return {"ok": True}

    with ServerThread(app) as srv:
        assert probe(Check("hub", "http", f"{srv.url}/v1/health"), timeout=2, tries=1) == (True, "HTTP 200")
        assert probe(Check("hub", "http", f"{srv.url}/missing"), timeout=2, tries=1) == (False, "HTTP 404")
    closed = free_port()
    healthy, detail = probe(Check("hub", "http", f"http://127.0.0.1:{closed}/v1/health"), timeout=1, tries=1)
    assert healthy is False and detail == "ConnectError"


def test_janitor_runs_inside_a_pass(home):
    cfg = setup_home(home, None, config='[satellite]\nenabled = false\n[tracker]\nenabled = false\n')
    fake = Fake()
    w = Watchdog(cfg, platform="linux", runner=fake.runner, prober=fake.prober, installed=fake.installed,
                 alerter=fake.alerter, janitor=True)
    state = w.run_once()
    assert set(state["janitor"]) == {"logs", "satellite", "scheduler", "tracker_media", "hub"}


def test_a_stopped_or_dead_scheduler_is_down(home):
    import subprocess
    import sys

    cfg = setup_home(home, None)
    check = next(c for c in checks_for(cfg, "linux") if c.name == "scheduler")
    cfg.paths.run.mkdir(parents=True, exist_ok=True)
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (cfg.paths.run / "scheduler.json").write_text(json.dumps({"heartbeat_at": fresh, "pid": 1, "stopped_at": fresh}))
    assert probe(check)[0] is False and "stopped" in probe(check)[1]
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    (cfg.paths.run / "scheduler.json").write_text(json.dumps({"heartbeat_at": fresh, "pid": gone.pid}))
    healthy, detail = probe(check)
    assert healthy is False and "gone" in detail


def test_self_heal_alert_uses_the_hub_token_on_a_satellite(home):
    from orbital.core.secrets import Secrets
    from orbital.scheduler.watchdog import raise_alert

    seen = []
    app = FastAPI()

    @app.post("/v1/alerts")
    async def alerts(request: Request):
        seen.append({"auth": request.headers.get("authorization"), "body": await request.json()})
        return {"ok": True, "alert_ids": ["x1"]}

    with ServerThread(app) as hub:
        cfg = setup_home(home, None, config=f'[system]\nrole = "satellite"\n[hub]\nurl = "{hub.url}"\n')
        assert raise_alert(cfg, "Self-heal: restarted satellite", "it stopped") is False, "no token yet"
        Secrets(cfg.paths).set("ORBITAL_HUB_TOKEN", "sat-hub-token-0123456789")
        assert raise_alert(cfg, "Self-heal: restarted satellite", "it stopped") is True
    assert seen[0]["auth"] == "Bearer sat-hub-token-0123456789"
    assert seen[0]["body"]["alerts"][0]["title"] == "Self-heal: restarted satellite"
