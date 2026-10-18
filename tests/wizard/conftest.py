"""Fixtures for the wizard tests: temp HOME / ORBITAL_HOME, a non-interactive UI that records its
output, fake commands, and fakes for everything the wizard calls in other packages (hub discovery,
join, services, live tests, Apple tooling). Nothing touches the real machine."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from wizard_helpers import SAFE_ENV, Deps, FakeStream, answers_with_ports

from orbital.core import logs
from orbital.core.paths import REPO_ROOT
from orbital.services.ops import Ops
from orbital.wizard import preflight
from orbital.wizard.answers import Answers
from orbital.wizard.flow import Options, Wizard
from orbital.wizard.ui import UI

@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    orbital_home = tmp_path / "orbital-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORBITAL_HOME", str(orbital_home))
    monkeypatch.setenv("ORBITAL_LAUNCHCTL", "/usr/bin/false")
    monkeypatch.setenv("ORBITAL_SYSTEMCTL", "/usr/bin/false")
    monkeypatch.setenv("ORBITAL_TRACKER_APP", str(tmp_path / "no-tracker.app"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    for key in SAFE_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(preflight, "port_in_use", lambda port, hosts=None: False)
    monkeypatch.setattr(preflight, "probe_orbital", lambda port, timeout=0.8: "")
    logs.reset_for_tests()
    yield home, orbital_home
    logs.reset_for_tests()


@pytest.fixture
def run_wizard(homes):
    """run_wizard(answers=dict, deps=Deps(), **opts) → (exit code, output text, wizard)."""
    home, _ = homes

    def run(answers: dict | None = None, deps: Deps | None = None, user_home: Path | None = None,
            interactive_inputs: list[str] | None = None, **opts):
        out = io.StringIO()
        if interactive_inputs is not None:
            feed = iter(interactive_inputs)
            ui = UI(out=out, interactive=True, color=False, input_fn=lambda prompt: next(feed),
                    secret_fn=lambda prompt: next(feed))
        else:
            ui = UI(out=out, interactive=False, color=False)
        deps = deps or Deps()
        options = Options(answers=Answers(answers_with_ports(answers), env={}), user_home=user_home or home,
                          platform="macos", **opts)
        ops = Ops(dry_run=options.dry_run, out=ui.line, exec_fn=deps.exec, stream_fn=FakeStream())
        wiz = Wizard(options, ui=ui, ops=ops, deps=deps, repo=REPO_ROOT)
        wiz.fake = deps
        rc = wiz.run()
        return rc, out.getvalue(), wiz

    return run
