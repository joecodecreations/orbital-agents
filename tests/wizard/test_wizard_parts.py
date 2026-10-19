"""Answers files, config writing, terminal UI, preflight helpers, Apple detection parsing, doctor checks
and the `orbital test` result table."""

from __future__ import annotations

import io
import json
import os
import stat
import tomllib

import pytest

from orbital.core.config import DEFAULTS, load_config
from orbital.wizard import apple, configfile, preflight
from orbital.wizard.answers import KEYS, Answers
from orbital.wizard.ui import UI

# ---------------------------------------------------------------- answers


def test_answers_file_and_env_fallback(tmp_path):
    f = tmp_path / "answers.toml"
    f.write_text('role = "satellite"\n[satellite]\nhub_url = ""\njoin_token = ""\n[system]\nname = "Lab"\n'
                 '[bogus]\nkey = 1\n')
    a = Answers.load(f, env={"ORBITAL_JOIN_TOKEN": "from-env-token"})
    assert a.get("role") == "satellite" and a.get("system.name") == "Lab"
    assert a.get("satellite.hub_url") is None                  # "" = not given
    assert a.get("satellite.join_token") == "from-env-token"   # env fallback for secrets
    assert a.why("satellite.join_token") == "from $ORBITAL_JOIN_TOKEN"
    assert a.unknown_keys() == ["bogus.key"]
    assert not a.has("components.tracker")


def test_example_answers_file_is_complete():
    from orbital.core.paths import REPO_ROOT
    with open(REPO_ROOT / "config" / "answers.example.toml", "rb") as fh:
        data = tomllib.load(fh)
    a = Answers(data, env={})
    assert a.unknown_keys() == []
    documented = (REPO_ROOT / "config" / "answers.example.toml").read_text()
    for key in KEYS:
        assert key.rsplit(".", 1)[-1] in documented, f"{key} isn't documented in answers.example.toml"


# ---------------------------------------------------------------- config file


def test_minimal_config_only_non_defaults():
    merged = configfile.merge(DEFAULTS, {"hub": {"port": 17790}, "system": {"name": "Lab"}})
    out = configfile.minimal(merged)
    assert out == {"system": {"name": "Lab", "role": "hub"}, "hub": {"port": 17790}}


def test_satellite_config_keeps_hub_url():
    merged = configfile.merge(DEFAULTS, {"system": {"role": "satellite"}, "hub": {"url": "http://h:17790"}})
    out = configfile.minimal(merged)
    assert out["hub"]["url"] == "http://h:17790" and out["system"]["role"] == "satellite"


def test_rendered_config_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBITAL_HOME", str(tmp_path))
    merged = configfile.merge(DEFAULTS, {
        "system": {"name": "Lab \"quoted\" ✓"}, "satellite": {"workdirs": ["~", "/srv/code"]},
        "agents": {"mine": {"command": ["/usr/bin/true", "{prompt}"]}}, "tracker": {"enabled": False},
    })
    (tmp_path / "config.toml").write_text(configfile.render(merged))
    assert load_config(tmp_path).data == merged
    assert (tmp_path / "config.toml").read_text().startswith("# Orbital Agents settings")


# ---------------------------------------------------------------- UI


def ui_with(inputs, secrets=None):
    out = io.StringIO()
    feed = iter(inputs)
    sfeed = iter(secrets or [])
    return UI(out=out, interactive=True, color=False, input_fn=lambda p: next(feed),
              secret_fn=lambda p: next(sfeed)), out


def test_ui_questions():
    ui, out = ui_with(["", "custom", "n", "2", "1 3", "x", "42"])
    assert ui.ask("Name", "default") == "default"
    assert ui.ask("Name", "default") == "custom"
    assert ui.ask_bool("Ok?", True) is False
    assert ui.ask_choice("Pick", [("a", "A"), ("b", "B")], "a") == "b"
    assert ui.ask_multi("Many", [("a", "A"), ("b", "B"), ("c", "C")], ["a"]) == ["a", "c"]
    assert ui.ask_int("Port", 17700) == 42           # "x" rejected, then 42
    assert "please type a number" in out.getvalue()


def test_ui_secret_never_echoed():
    ui, out = ui_with([], secrets=["", "s3cret-value-123"])
    assert ui.ask_secret("Key", current="old-secret-value") == "old-secret-value"
    assert ui.ask_secret("Key") == "s3cret-value-123"
    assert "s3cret-value-123" not in out.getvalue() and "old-secret-value" not in out.getvalue()


def test_ui_non_interactive_prints_choices():
    out = io.StringIO()
    ui = UI(out=out, interactive=False, color=False)
    assert ui.ask("Name", "Lab") == "Lab"
    assert ui.ask_bool("Ok?", True, preset="false", why="answers file") is False
    assert ui.ask_multi("Feeds", [("a", "A"), ("b", "B")], ["a"], preset="all") == ["a", "b"]
    assert ui.ask_secret("Token", preset="abcdefghijklmnop") == "abcdefghijklmnop"
    text = out.getvalue()
    assert "Name: Lab (default)" in text and "Ok?: no (answers file)" in text
    assert "abcdefghijklmnop" not in text


# ---------------------------------------------------------------- preflight


def test_check_port_states():
    free = preflight.check_port("hub", 17701, in_use=lambda p: False, probe=lambda p: "")
    ours = preflight.check_port("hub", 17701, in_use=lambda p: True, probe=lambda p: "orbital-hub")
    other = preflight.check_port("hub", 17701, in_use=lambda p: True, probe=lambda p: "")
    assert free.free and not free.conflict
    assert ours.ours and not ours.conflict
    assert other.conflict


def test_extension_steps_say_what_setup_opened(tmp_path, monkeypatch):
    from orbital.wizard import apps

    monkeypatch.setattr(apps.sys, "platform", "darwin")
    cfg = load_config(tmp_path)
    opened = apps.extension_steps(cfg, tmp_path, opened=True)
    assert "setup opened it for you" in opened[0] and "setup showed it in Finder" in opened[3]
    not_opened = apps.extension_steps(cfg, tmp_path, opened=False)
    assert not any("setup opened" in s or "setup showed" in s for s in not_opened)
    assert str(tmp_path / "browser-extension") in not_opened[3]


def test_next_free_port_skips_taken():
    busy = {17711, 17712}
    assert preflight.next_free_port(17710, {17713}, in_use=lambda p: p in busy) == 17714


# ---------------------------------------------------------------- apple

FAKE_TEAM = "AB" + "CDE12345"   # built at runtime so no team-id-looking literal sits in the repo


def test_parse_certificate_subject():
    subject = f"subject=UID=XYZ, CN=Apple Development: Pat Example (QQQQQQQQQQ), OU={FAKE_TEAM}, O=Pat Example, C=US"
    team = apple.parse_subject(subject)
    assert team.team_id == FAKE_TEAM and team.label == "Pat Example"
    assert apple.parse_subject("subject=CN=nothing") is None


def test_detect_teams_with_fake_tools():
    pem = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"

    def fake(argv, timeout=15):
        from orbital.services.ops import Result
        if argv[0] == "security":
            return Result(0, pem + pem)
        return Result(0, f"subject= OU = {FAKE_TEAM}, O = Pat")
    teams = apple.detect_teams(fake)
    assert [t.team_id for t in teams] == [FAKE_TEAM]


def test_parse_devicectl():
    data = {"result": {"devices": [
        {"deviceProperties": {"name": "Pat's iPhone"}, "hardwareProperties": {"platform": "iOS", "udid": "u1"},
         "connectionProperties": {"pairingState": "paired", "tunnelState": "disconnected"}},
        {"deviceProperties": {"name": "Old iPad"}, "hardwareProperties": {"platform": "iOS", "udid": "u2"},
         "connectionProperties": {"pairingState": "paired", "tunnelState": "unavailable"}},
        {"deviceProperties": {"name": "Watch"}, "hardwareProperties": {"platform": "watchOS"}},
    ]}}
    devices = apple.parse_devicectl(data)
    assert [(d.name, d.connected) for d in devices] == [("Pat's iPhone", True), ("Old iPad", False)]


# ---------------------------------------------------------------- doctor


@pytest.fixture
def doctor_home(tmp_path, monkeypatch):
    from orbital.core import logs
    monkeypatch.setenv("ORBITAL_HOME", str(tmp_path / "oh"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    logs.reset_for_tests()
    yield tmp_path / "oh"
    logs.reset_for_tests()


def test_doctor_without_config(doctor_home):
    from orbital.cli.commands.doctor import Doctor
    findings = Doctor().run()
    assert findings[0].level == "fail" and "setup" in findings[0].fix


def test_doctor_fixes_resolved_and_secret_mode(doctor_home):
    from orbital.cli.commands.doctor import Doctor
    from orbital.core.secrets import Secrets
    doctor_home.mkdir(parents=True)
    (doctor_home / "config.toml").write_text('[system]\nrole = "hub"\n[hub]\nport = 17790\n')
    store = Secrets(load_config().paths)
    for k in ("ORBITAL_HUB_USERNAME", "ORBITAL_HUB_PASSWORD", "ORBITAL_AUTOMATION_TOKEN", "ORBITAL_JOIN_TOKEN",
              "ORBITAL_SATELLITE_TOKEN"):
        store.set(k, "value-" + k.lower())
    os.chmod(doctor_home / "secrets.env", 0o644)
    doc = Doctor()
    cfg = load_config()
    doc.cfg = cfg
    doc.check_resolved(cfg)
    doc.check_secrets(cfg)
    assert {f.area for f in doc.findings if f.level != "ok"} == {"config", "secrets"}
    doc.fix()
    assert (doctor_home / "config.resolved.json").is_file()
    assert stat.S_IMODE((doctor_home / "secrets.env").stat().st_mode) == 0o600
    doc.findings = []
    doc.check_resolved(cfg)
    doc.check_secrets(cfg)
    assert all(f.level == "ok" for f in doc.findings), doc.findings


def test_doctor_config_problems(doctor_home):
    from orbital.cli.commands.doctor import Doctor
    doctor_home.mkdir(parents=True)
    (doctor_home / "config.toml").write_text('[system]\nrole = "satellite"\n[hub]\nport = 17790\n'
                                             '[satellite]\nport = 17790\n[agents.bad]\ncommand = "notalist"\n')
    doc = Doctor()
    assert doc.check_config()
    msg = doc.findings[0].message
    assert "satellite without hub.url" in msg and "share port 17790" in msg and "agents.bad.command" in msg


def test_doctor_reports_missing_tokens(doctor_home):
    from orbital.cli.commands.doctor import Doctor
    doctor_home.mkdir(parents=True)
    (doctor_home / "config.toml").write_text('[system]\nrole = "hub"\n')
    (doctor_home / "secrets.env").write_text("ORBITAL_HUB_USERNAME=x\n")
    os.chmod(doctor_home / "secrets.env", 0o600)
    doc = Doctor()
    doc.check_secrets(load_config())
    assert doc.findings[0].level == "fail" and "ORBITAL_JOIN_TOKEN" in doc.findings[0].message


def test_doctor_tunnel_mode_does_not_require_tailscale(doctor_home, monkeypatch):
    from orbital.cli.commands.doctor import Doctor
    from orbital.core import net
    doctor_home.mkdir(parents=True)
    (doctor_home / "config.toml").write_text(
        '[system]\nrole = "hub"\n'
        '[network]\nmode = "tunnel"\npublic_url = "https://orbital.example.com"\n'
        '[hub]\nport = 17790\n'
    )
    monkeypatch.setattr(net, "tailscale_bin", lambda: None)
    monkeypatch.setattr(net, "tailscale_self", lambda: None)
    doc = Doctor()
    cfg = load_config()
    doc.cfg = cfg
    assert doc.check_config()
    assert all(f.level == "ok" for f in doc.findings if f.area == "config"), doc.findings
    doc.findings = []
    doc.check_tailscale(cfg)
    by_area = {f.area: f for f in doc.findings}
    assert by_area["tailscale"].level == "ok"
    assert "not required" in by_area["tailscale"].message
    assert by_area["network"].level in ("ok", "warn")  # warn if HTTPS probe fails offline


def test_doctor_tunnel_hub_without_public_url_fails_config(doctor_home):
    from orbital.cli.commands.doctor import Doctor
    doctor_home.mkdir(parents=True)
    (doctor_home / "config.toml").write_text('[system]\nrole = "hub"\n[network]\nmode = "tunnel"\n')
    doc = Doctor()
    doc.check_config()
    assert any("public_url" in f.message for f in doc.findings if f.level == "fail")


# ---------------------------------------------------------------- orbital test


def test_result_table_and_collector():
    from orbital.cli.commands.test import Collector, print_table

    class Rep:
        def __init__(self, nodeid, when, outcome, longrepr=None, duration=0.1):
            self.nodeid, self.when, self.duration, self.longrepr = nodeid, when, duration, longrepr
            self.skipped, self.failed, self.passed = outcome == "skipped", outcome == "failed", outcome == "passed"

    c = Collector()
    c.names = {"t::a": "hub: answers", "t::b": "browser: opens a tab", "t::c": "tracker: round trip"}
    c.order = ["t::a", "t::b", "t::c"]
    c.pytest_runtest_logreport(Rep("t::a", "call", "passed"))
    c.pytest_runtest_logreport(Rep("t::b", "setup", "skipped", ("f", 1, "Skipped: browser.enabled = false")))
    crash = type("R", (), {"reprcrash": type("C", (), {"message": "AssertionError: item not visible"})()})()
    c.pytest_runtest_logreport(Rep("t::c", "call", "failed", crash))
    rows = c.results()
    assert [r["outcome"] for r in rows] == ["passed", "skipped", "failed"]
    assert rows[1]["detail"] == "browser.enabled = false" and rows[2]["detail"] == "item not visible"
    out = io.StringIO()
    print_table(rows, out, color=False)
    text = out.getvalue()
    assert "PASS  hub: answers" in text and "FAIL  tracker: round trip" in text
    assert "1 passed, 1 failed, 1 skipped" in text
    json.dumps(rows)
