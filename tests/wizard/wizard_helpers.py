"""Shared fakes for the wizard tests (imported by conftest.py and the test modules)."""

from __future__ import annotations

import json

from orbital.core.secrets import Secrets
from orbital.services import Outcome
from orbital.services.ops import Result
from orbital.wizard import preflight

SAFE_ENV = ("ORBITAL_JOIN_TOKEN", "ORBITAL_HUB_PASSWORD", "ORBITAL_AUTOMATION_TOKEN", "XAI_API_KEY",
            "FISH_API_KEY", "XDG_CONFIG_HOME", "ORBITAL_LIVE", "ORBITAL_HUB_TOKEN", "ORBITAL_SATELLITE_TOKEN",
            "ORBITAL_BROWSER_TOKEN")

TEST_PORTS = {"hub": 17760, "tracker": 17761, "stt": 17762, "browser": 17763, "satellite": 17764}


class FakeExec:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout=60, **kw):
        self.calls.append(list(argv))
        return Result(0, "", "")


class FakeStream:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, log_file=None, cwd=None, env=None, progress=None):
        self.calls.append(list(argv))
        return Result(0, "built", "")


def fake_environment(cfg, **kw) -> preflight.Environment:
    env = preflight.Environment(
        os="macos", os_version="15.0", python="3.13.0", python_ok=True,
        tailscale_installed=True, tailscale_running=True, tailscale_name="lab-mac",
        tailscale_dns="lab-mac.example.ts.net", tailscale_ip="100.64.0.10",
        xcode="Xcode 16.0", command_line_tools=True, swift="swift 6.0",
        chrome="/Applications/Google Chrome.app",
        agents={"claude": "/usr/local/bin/claude", "codex": "", "grok": "", "gemini": ""},
    )
    for key, dotted in preflight.PORT_KEYS.items():
        # the real check (tests monkeypatch preflight.port_in_use to make a port busy), never a real probe
        env.ports[key] = preflight.check_port(key, int(cfg.get(dotted)), probe=lambda port: "")
    for k, v in kw.items():
        setattr(env, k, v)
    return env


class Deps(dict):
    """Fakes for the wizard's outside calls; records what was called."""

    def __init__(self, hubs=None, env_overrides=None, live_rc=0, join_error=None):
        super().__init__()
        self.calls: list[tuple] = []
        self.exec = FakeExec()
        self["find_hubs"] = lambda port: (self.calls.append(("find_hubs", port)), list(hubs or []))[1]
        self["hub_info"] = lambda url: {"service": "orbital-hub", "name": "Lab", "machine": "lab-mac", "version": "0.1.0"}
        self["heartbeat"] = lambda url, token, sid: (self.calls.append(("heartbeat", url, sid)), (True, "the hub knows this machine"))[1]
        self["install_services"] = self._install
        self["restart_services"] = lambda cfg, names, ops: self.calls.append(("restart", tuple(names)))
        self["wait_healthy"] = lambda cfg, timeout: {"hub": (True, "HTTP 200")}
        self["live_test"] = lambda env: (self.calls.append(("live_test", dict(env))), live_rc)[1]
        self["teams"] = lambda: []
        self["devices"] = lambda: []
        self["gather"] = lambda cfg: fake_environment(cfg, **(env_overrides or {}))
        self["tracker_app"] = lambda: None
        self["exec"] = self.exec
        self.join_error = join_error
        self["join_hub"] = self._join

    def _install(self, cfg, ops):
        from orbital.services.spec import enabled_names
        names = [n for n in enabled_names(cfg) if n != "tracker"]
        self.calls.append(("install_services", tuple(names), cfg.role))
        return [Outcome(n, True, "installed and loaded") for n in names], []

    def _join(self, url, token, cfg):
        self.calls.append(("join", url, token))
        if self.join_error:
            raise RuntimeError(self.join_error)
        store = Secrets(cfg.paths)
        store.ensure_token("ORBITAL_SATELLITE_TOKEN")
        store.set("ORBITAL_HUB_TOKEN", "hub-token-from-join-0123456789")
        cfg.paths.satellite_data.mkdir(parents=True, exist_ok=True)
        (cfg.paths.satellite_data / "identity.json").write_text(json.dumps({"satellite_id": "sat-1", "hub_url": url}))
        return {"satellite_id": "sat-1", "hub_url": url}

    def called(self, name: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == name]


def answers_with_ports(extra: dict | None = None) -> dict:
    data = {"ports": dict(TEST_PORTS), "apple": {"build_tracker": False, "build_mac_app": False},
            "verify": {"wait_seconds": 1}}
    for table, values in (extra or {}).items():
        if isinstance(values, dict):
            data.setdefault(table, {}).update(values)
        else:
            data[table] = values
    return data
