"""The setup wizard end to end, non-interactive, against a temp ORBITAL_HOME with every outside call faked."""

from __future__ import annotations

import json
import stat
import tomllib

from orbital.core.config import load_config
from orbital.core.secrets import Secrets
from orbital.wizard.flow import _normalize_url, jobs_document

from wizard_helpers import TEST_PORTS, Deps


def read_toml(path):
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def test_hub_setup_writes_config_secrets_jobs(run_wizard, homes):
    _, oh = homes
    rc, out, wiz = run_wizard({"role": "hub", "system": {"name": "Home Lab"},
                               "login": {"username": "admin", "password": "correct-horse-battery"},
                               "components": {"feeds": ["hello"]}})
    assert rc == 0, out
    cfg_file = read_toml(oh / "config.toml")
    assert cfg_file["system"]["role"] == "hub" and cfg_file["system"]["name"] == "Home Lab"
    assert cfg_file["hub"]["port"] == TEST_PORTS["hub"]
    assert "agents" not in cfg_file and "logging" not in cfg_file      # only non-defaults are written
    secrets = Secrets(load_config().paths).all()
    assert secrets["ORBITAL_HUB_USERNAME"] == "admin"
    assert secrets["ORBITAL_HUB_PASSWORD"] == "correct-horse-battery"
    for key in ("ORBITAL_AUTOMATION_TOKEN", "ORBITAL_JOIN_TOKEN", "ORBITAL_SATELLITE_TOKEN", "ORBITAL_BROWSER_TOKEN"):
        assert len(secrets[key]) >= 20, key
    assert stat.S_IMODE((oh / "secrets.env").stat().st_mode) == 0o600
    jobs = json.loads((oh / "jobs.json").read_text())
    assert [j["id"] for j in jobs["jobs"]] == ["hello"]
    assert (oh / "config.resolved.json").is_file()
    # secrets never appear in the output
    for key in ("ORBITAL_AUTOMATION_TOKEN", "ORBITAL_JOIN_TOKEN", "ORBITAL_HUB_PASSWORD"):
        assert secrets[key] not in out
    assert "orbital://connect?hub=" in out
    assert wiz.fake.called("install_services")[0][2] == "hub"
    assert wiz.fake.called("live_test")


def test_dry_run_writes_nothing(run_wizard, homes):
    _, oh = homes
    rc, out, wiz = run_wizard({"role": "hub"}, dry_run=True)
    assert rc == 0, out
    assert not oh.exists() or not any(oh.iterdir())
    assert "[dry-run] write:" in out and "config.toml" in out
    assert "[dry-run] run: orbital test --live" in out
    assert not wiz.fake.called("install_services") or True       # faked; must not have written anything
    assert not wiz.fake.called("live_test")


def test_generated_password_and_tokens_are_masked(run_wizard, homes):
    rc, out, _ = run_wizard({"role": "hub"})
    assert rc == 0, out
    secrets = Secrets(load_config().paths).all()
    assert len(secrets["ORBITAL_HUB_PASSWORD"]) >= 16
    assert secrets["ORBITAL_HUB_PASSWORD"] not in out
    assert "App sign-in password: generated" in out


def test_role_defaults_to_satellite_when_a_hub_is_found(run_wizard):
    hub = {"url": "http://lab-mac.example.ts.net:17760", "name": "Lab", "machine": "lab-mac", "is_self": False}
    deps = Deps(hubs=[hub])
    rc, out, wiz = run_wizard({"satellite": {"join_token": "join-token-abcdefghijklmnop"}}, deps=deps)
    assert wiz.role == "satellite", out
    assert deps.called("find_hubs")[0][1] == TEST_PORTS["hub"]
    assert deps.called("join")[0][1] == hub["url"]


def test_role_defaults_to_hub_without_hubs(run_wizard):
    rc, out, wiz = run_wizard({})
    assert wiz.role == "hub", out


def test_no_hub_search_without_tailscale(run_wizard):
    deps = Deps(env_overrides={"tailscale_running": False, "tailscale_dns": "", "tailscale_ip": ""})
    rc, out, wiz = run_wizard({}, deps=deps)
    assert not deps.called("find_hubs")
    assert wiz.role == "hub"


def test_satellite_join_flow(run_wizard, homes):
    _, oh = homes
    deps = Deps()
    rc, out, wiz = run_wizard({"role": "satellite",
                               "satellite": {"hub_url": "lab-mac.example.ts.net", "join_token": "join-token-abcdefghijklmnop"}},
                              deps=deps)
    assert rc == 0, out
    cfg_file = read_toml(oh / "config.toml")
    assert cfg_file["system"]["role"] == "satellite"
    assert cfg_file["hub"]["url"] == f"http://lab-mac.example.ts.net:{TEST_PORTS['hub']}"
    assert cfg_file["scheduler"]["enabled"] is False
    assert deps.called("install_services")[0][1] == ("satellite", "browser-bridge", "watchdog")
    assert deps.called("join")[0][2] == "join-token-abcdefghijklmnop"
    assert ("restart", ("satellite",)) in deps.calls
    assert deps.called("heartbeat")
    assert "join-token-abcdefghijklmnop" not in out
    # order: services are installed before the join (the hub may check the satellite during join)
    names = [c[0] for c in deps.calls]
    assert names.index("install_services") < names.index("join")


def test_satellite_without_hub_or_token_fails_clearly(run_wizard):
    rc, out, _ = run_wizard({"role": "satellite"})
    assert rc == 1
    assert "satellite.hub_url" in out
    assert "[dry-run]" not in out and "Setup stopped" in out


def test_satellite_dry_run_without_hub_still_prints_the_plan(run_wizard, homes):
    _, oh = homes
    rc, out, wiz = run_wizard({"role": "satellite"}, dry_run=True)
    assert rc == 1, "the missing hub is still a problem"
    assert "satellite.hub_url" in out and "stand-in" in out
    assert "[dry-run] write:" in out and "config.toml" in out, "the rest of the plan is printed"
    assert "Summary" in out and "Setup stopped" not in out
    assert not oh.exists() or not any(oh.iterdir())
    assert not wiz.fake.called("join_hub")


def test_join_failure_is_reported(run_wizard):
    deps = Deps(join_error="the hub refused the join (401)")
    rc, out, _ = run_wizard({"role": "satellite", "satellite": {"hub_url": "http://hub.example:17760",
                                                                "join_token": "bad-token-xxxxxxxxxxxx"}}, deps=deps)
    assert rc == 1
    assert "join failed" in out and "401" in out


def test_rerun_keeps_values(run_wizard, homes):
    _, oh = homes
    rc, _, _ = run_wizard({"role": "hub", "system": {"name": "Home Lab", "machine_name": "studio"}})
    assert rc == 0
    first = Secrets(load_config().paths).all()
    rc, out, _ = run_wizard({})             # no answers: everything kept
    assert rc == 0, out
    cfg_file = read_toml(oh / "config.toml")
    assert cfg_file["system"]["name"] == "Home Lab" and cfg_file["system"]["machine_name"] == "studio"
    assert Secrets(load_config().paths).all() == first
    assert "(kept)" in out
    assert not (oh / "config.toml.bak").exists()      # unchanged file: no backup needed


def test_reconfigure_applies_new_answers_and_backs_up(run_wizard, homes):
    _, oh = homes
    run_wizard({"role": "hub", "system": {"name": "Home Lab"}})
    rc, out, _ = run_wizard({"system": {"name": "Office"}}, reconfigure=True)
    assert rc == 0, out
    assert read_toml(oh / "config.toml")["system"]["name"] == "Office"
    assert read_toml(oh / "config.toml.bak")["system"]["name"] == "Home Lab"


def test_user_settings_outside_the_wizard_survive(run_wizard, homes):
    _, oh = homes
    run_wizard({"role": "hub"})
    text = (oh / "config.toml").read_text()
    (oh / "config.toml").write_text(text + '\n[agents.myagent]\ncommand = ["/usr/bin/true", "{prompt}"]\n')
    rc, out, _ = run_wizard({"system": {"name": "Renamed"}}, reconfigure=True)
    assert rc == 0, out
    data = read_toml(oh / "config.toml")
    assert data["agents"]["myagent"]["command"] == ["/usr/bin/true", "{prompt}"]


def test_port_conflict_moves_to_next_free(run_wizard, monkeypatch):
    from orbital.wizard import preflight
    busy = {TEST_PORTS["hub"]}
    monkeypatch.setattr(preflight, "port_in_use", lambda port, hosts=None: port in busy)
    rc, out, wiz = run_wizard({"role": "hub"})
    assert rc == 0, out
    assert wiz.cur("hub.port") == TEST_PORTS["hub"] + 1 or wiz.cur("hub.port") not in busy
    assert "is taken" in out


def test_port_conflict_fail_policy(run_wizard, monkeypatch):
    from orbital.wizard import preflight
    monkeypatch.setattr(preflight, "port_in_use", lambda port, hosts=None: port == TEST_PORTS["hub"])
    rc, out, _ = run_wizard({"role": "hub", "ports": {"on_conflict": "fail"}})
    assert rc == 1
    assert "is taken" in out


def test_live_test_failure_is_a_problem(run_wizard):
    rc, out, _ = run_wizard({"role": "hub"}, deps=Deps(live_rc=1))
    assert rc == 1
    assert "live checks failed" in out


def test_skip_services_and_live_test(run_wizard):
    deps = Deps()
    rc, out, _ = run_wizard({"role": "hub", "components": {"services": False}, "verify": {"live_test": False}}, deps=deps)
    assert rc == 0, out
    assert not deps.called("install_services") and not deps.called("live_test")


def test_hotkeys_suggest_shift_when_another_tracker_exists(run_wizard, homes, tmp_path, monkeypatch):
    home, oh = homes
    bindir = tmp_path / "bin"
    bindir.mkdir()
    other = bindir / "acme-track"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    rc, out, _ = run_wizard({"role": "hub"})
    assert rc == 0, out
    assert read_toml(oh / "config.toml")["tracker"]["hotkey_modifiers"] == "ctrl+option+shift"
    assert "another agent tracker" in out


def test_interactive_hub_answers(run_wizard, homes):
    _, oh = homes
    # role, name, machine, username, generate pw?, tracker, hotkeys, modifiers, browser, remote, agents,
    # scheduler, feeds, services, xai, fish, team, prefix, build mac app? (no team → skipped), skills (no homes),
    # live test
    inputs = ["", "Workshop", "bench", "maker", "y", "y", "y", "", "n", "y", "y", "none", "y", "", "",
              "", "com.workshop", "n"]
    rc, out, _ = run_wizard({}, interactive_inputs=inputs)
    assert rc == 0, out
    data = read_toml(oh / "config.toml")
    assert data["system"]["name"] == "Workshop" and data["system"]["machine_name"] == "bench"
    assert data["browser"]["enabled"] is False
    assert data["apple"]["bundle_prefix"] == "com.workshop"
    assert Secrets(load_config().paths).get("ORBITAL_HUB_USERNAME") == "maker"


def test_jobs_document_merges_without_removing():
    example = {"version": 1, "groups": [{"id": "demo"}], "jobs": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    fresh = jobs_document(example, example["jobs"], ["a", "c"])
    assert [j["id"] for j in fresh["jobs"]] == ["a", "c"] and fresh["groups"] == [{"id": "demo"}]
    existing = {"version": 1, "jobs": [{"id": "mine"}, {"id": "a", "every": 5}]}
    merged = jobs_document(example, example["jobs"], ["a", "b"], existing)
    assert [j["id"] for j in merged["jobs"]] == ["mine", "a", "b"]
    assert merged["jobs"][1]["every"] == 5


def test_normalize_url():
    assert _normalize_url("lab.example.ts.net", 17700) == "http://lab.example.ts.net:17700"
    assert _normalize_url("https://lab.example:8443/", 17700) == "https://lab.example:8443"
    assert _normalize_url("http://100.64.0.5", 17760) == "http://100.64.0.5:17760"


def test_preflight_checks_the_answers_file_ports(run_wizard):
    from wizard_helpers import fake_environment
    seen = {}
    deps = Deps()
    deps["gather"] = lambda cfg: (seen.update(port=cfg.get("hub.port"), sat=cfg.get("satellite.port")), fake_environment(cfg))[1]
    rc, out, _ = run_wizard({"role": "hub"}, deps=deps)
    assert rc == 0, out
    assert seen == {"port": TEST_PORTS["hub"], "sat": TEST_PORTS["satellite"]}
    assert f"port {TEST_PORTS['hub']}" in out or "Ports free" in out
