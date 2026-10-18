"""Skills + always-on rule installer, against temp agent homes (never the real ~/.claude etc.)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orbital.services.ops import Ops
from orbital.wizard import skills as sk


def make_repo(tmp_path: Path, rules: str = "Rules for {{AGENT_NAME}}: use `orbital-track start --agent {{AGENT}}`.\n") -> Path:
    repo = tmp_path / "repo"
    (repo / "skills" / "tracker").mkdir(parents=True)
    (repo / "skills" / "tracker" / "SKILL.md").write_text("---\nname: tracker\ndescription: x\n---\n\nI am {{AGENT}}.\n")
    (repo / "skills" / "tracker" / "notes.txt").write_text("extra file\n")
    (repo / "skills" / "orbital-browser").mkdir()
    (repo / "skills" / "orbital-browser" / "SKILL.md").write_text("---\nname: orbital-browser\n---\nbrowse\n")
    (repo / "skills" / "agent-rules.md").write_text(rules)
    return repo


def make_home(tmp_path: Path, agents=("claude", "codex")) -> Path:
    home = tmp_path / "home"
    for a in agents:
        (home / sk.AGENTS[a][0]).mkdir(parents=True)
    return home


def inst(home, repo, **kw):
    return sk.Installer(home=home, repo=repo, ops=Ops(out=lambda line: None), **kw)


def test_installs_skills_into_existing_homes_only(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path)
    events = inst(home, repo).install()
    claude = home / ".claude" / "skills"
    assert sorted(p.name for p in claude.iterdir()) == ["orbital-browser", "orbital-tracker"]
    text = (claude / "orbital-tracker" / "SKILL.md").read_text()
    assert "name: orbital-tracker" in text and "I am claude." in text
    assert "I am codex." in (home / ".codex" / "skills" / "orbital-tracker" / "SKILL.md").read_text()
    assert (claude / "orbital-tracker" / "notes.txt").is_file()
    assert json.loads((claude / "orbital-tracker" / sk.MARKER).read_text())["source"] == "skills/tracker"
    assert not (home / ".grok").exists() and not (home / ".gemini").exists()
    assert any("grok isn't set up" in e.message for e in events)


def test_gemini_uses_config_skills(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("gemini",))
    inst(home, repo).install()
    assert (home / ".gemini" / "config" / "skills" / "orbital-tracker" / "SKILL.md").is_file()
    assert "Rules for Gemini" in (home / ".gemini" / "GEMINI.md").read_text()


def test_rule_block_added_with_backup_and_text_kept(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    rules = home / ".claude" / "CLAUDE.md"
    original = "# My own rules\n\nBe concise.\n"
    rules.write_text(original)
    inst(home, repo).install()
    text = rules.read_text()
    assert text.startswith(original)
    assert text.count(sk.BEGIN) == 1 and text.count(sk.END) == 1
    assert "Rules for Claude: use `orbital-track start --agent claude`." in text
    backup = rules.with_name("CLAUDE.md" + sk.BACKUP_SUFFIX)
    assert backup.read_text() == original
    # update in place: one block, backup untouched
    (repo / "skills" / "agent-rules.md").write_text("New rules for {{AGENT}}.\n")
    inst(home, repo).install()
    text = rules.read_text()
    assert text.count(sk.BEGIN) == 1 and "New rules for claude." in text and "Rules for Claude" not in text
    assert text.startswith(original) and backup.read_text() == original


def test_user_edits_after_the_block_survive(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    rules = home / ".claude" / "CLAUDE.md"
    rules.write_text("top\n")
    inst(home, repo).install()
    rules.write_text(rules.read_text() + "\nbottom line I added later\n")
    (repo / "skills" / "agent-rules.md").write_text("v2\n")
    inst(home, repo).install()
    text = rules.read_text()
    assert text.startswith("top\n") and text.endswith("bottom line I added later\n") and "v2" in text


def test_uninstall_restores_exactly(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    rules = home / ".claude" / "CLAUDE.md"
    original = "# Mine\n\nkeep this\n"
    rules.write_text(original)
    inst(home, repo).install()
    inst(home, repo).uninstall()
    assert rules.read_text() == original
    assert not list((home / ".claude" / "skills").glob("orbital-*"))


def test_uninstall_deletes_a_rules_file_we_created(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("codex",))
    inst(home, repo).install()
    agents_md = home / ".codex" / "AGENTS.md"
    assert agents_md.is_file()
    inst(home, repo).uninstall()
    assert not agents_md.exists()


def test_uninstall_rules_only_keeps_skills(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    inst(home, repo).install()
    inst(home, repo).uninstall(rules_only=True)
    assert (home / ".claude" / "skills" / "orbital-tracker").is_dir()
    assert not (home / ".claude" / "CLAUDE.md").exists()


def test_other_tracker_rule_blocks_the_rule_by_default(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    rules = home / ".claude" / "CLAUDE.md"
    original = "Report work with: acme-track start --agent claude --title X\n"
    rules.write_text(original)
    events = inst(home, repo).install()
    assert rules.read_text() == original                      # untouched
    assert (home / ".claude" / "skills" / "orbital-tracker").is_dir()   # skills still installed
    assert any(e.level == "warn" and "another tracker" in e.message for e in events)
    assert not rules.with_name("CLAUDE.md" + sk.BACKUP_SUFFIX).exists()
    # --rules yes installs it anyway
    inst(home, repo, rules="yes").install()
    assert sk.BEGIN in rules.read_text()


def test_fenced_tracker_block_counts_as_conflict():
    text = "x\n<!-- ACME-TRACKER:BEGIN -->\nrules\n<!-- ACME-TRACKER:END -->\n"
    assert sk.find_conflicts(text)
    assert not sk.find_conflicts("orbital-track start --agent claude\n")
    ours = sk.insert_block("", sk.render_rules("orbital-track start --agent {{AGENT}}", "claude"))
    assert not sk.find_conflicts(ours)


def test_confirm_callback_can_allow_the_rule(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    rules = home / ".claude" / "CLAUDE.md"
    rules.write_text("acme-track update ID --note x\n")
    seen = []
    inst(home, repo, confirm_conflict=lambda h, c: (seen.append(c), True)[1]).install()
    assert seen and sk.BEGIN in rules.read_text()


def test_rules_no_skips_the_block(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    inst(home, repo, rules="no").install()
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert (home / ".claude" / "skills" / "orbital-tracker").is_dir()


def test_foreign_skill_folder_left_alone(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    foreign = home / ".claude" / "skills" / "orbital-tracker"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("someone else's\n")
    events = inst(home, repo).install()
    assert (foreign / "SKILL.md").read_text() == "someone else's\n"
    assert any("wasn't installed by Orbital Agents" in e.message for e in events)
    inst(home, repo, force=True).install()
    assert "I am claude." in (foreign / "SKILL.md").read_text()


def test_update_removes_stale_files_and_skills(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    inst(home, repo).install()
    (repo / "skills" / "tracker" / "notes.txt").unlink()
    import shutil
    shutil.rmtree(repo / "skills" / "orbital-browser")
    inst(home, repo).install()
    base = home / ".claude" / "skills"
    assert not (base / "orbital-tracker" / "notes.txt").exists()
    assert not (base / "orbital-browser").exists()


def test_status_reports_ok_outdated_and_conflicts(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude", "codex"))
    inst(home, repo).install()
    (home / ".codex" / "AGENTS.md").write_text("nothing\n")
    (home / ".codex" / "AGENTS.md").write_text("acme-track done X\n")
    rows = {r["agent"]: r for r in inst(home, repo).status()}
    assert rows["claude"]["skills"] == {"orbital-browser": "ok", "orbital-tracker": "ok"}
    assert rows["claude"]["rules"] == "ok"
    assert rows["codex"]["rules"].startswith("skipped") and rows["codex"]["conflicts"]
    assert rows["grok"]["present"] is False
    (repo / "skills" / "tracker" / "SKILL.md").write_text("---\nname: tracker\n---\nchanged\n")
    rows = {r["agent"]: r for r in inst(home, repo).status()}
    assert rows["claude"]["skills"]["orbital-tracker"] == "outdated"


def test_dry_run_installs_nothing(tmp_path):
    repo, home = make_repo(tmp_path), make_home(tmp_path, ("claude",))
    lines = []
    sk.Installer(home=home, repo=repo, ops=Ops(dry_run=True, out=lines.append)).install()
    assert not (home / ".claude" / "skills").exists()
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert any("CLAUDE.md" in line for line in lines)


def test_fallback_to_tracker_integrations_then_builtin(tmp_path):
    repo = tmp_path / "repo"
    integ = repo / "apps" / "tracker" / "integrations"
    integ.mkdir(parents=True)
    (integ / "SKILL.md").write_text("---\nname: orbital-tracker\n---\nYou are {{AGENT_NAME}}.\n")
    (integ / "agent-rules.md").write_text("integration rules {{AGENT}}\n")
    srcs = sk.source_skills(repo)
    assert [s.installed_name for s in srcs] == ["orbital-tracker"]
    assert sk.rules_template(repo) == "integration rules {{AGENT}}\n"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert [s.installed_name for s in sk.source_skills(empty)] == ["orbital-agents"]
    assert "orbital-track start" in sk.rules_template(empty)


def test_other_tracker_signals(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("acme-track", "orbital-track"):
        f = bindir / name
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
    home = make_home(tmp_path, ("claude",))
    signals = sk.other_tracker_signals(home, path_env=str(bindir))
    assert len(signals) == 1 and "acme-track" in signals[0]
    (home / ".claude" / "CLAUDE.md").write_text("acme-track start --agent claude\n")
    assert len(sk.other_tracker_signals(home, path_env="")) == 1


def test_installer_rejects_bad_rules_mode():
    with pytest.raises(ValueError):
        sk.Installer(rules="maybe")


def test_cli_status_with_fake_home(tmp_path, monkeypatch, capsys):
    from orbital.cli.main import main
    monkeypatch.setenv("ORBITAL_HOME", str(tmp_path / "oh"))
    home = make_home(tmp_path, ("claude",))
    rc = main(["skills", "status", "--json", "--user-home", str(home)])
    rows = json.loads(capsys.readouterr().out)
    assert rc == 0 and rows[0]["agent"] == "claude" and rows[0]["present"] is True
    assert os.path.isdir(home / ".claude")
