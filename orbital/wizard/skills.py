"""Install the Orbital Agents skills and the always-on rule block into the AI agents' homes.

Skills: every `skills/<name>/` folder in the repo that has a SKILL.md is copied into each agent
home that exists, as `orbital-<name>` (names already starting with `orbital-` are kept):

    ~/.claude/skills/   ~/.codex/skills/   ~/.grok/skills/   ~/.gemini/config/skills/

Each copy carries a `.orbital-skill.json` marker; only marked folders are ever updated or removed.

Rules: the block from `skills/agent-rules.md`, fenced

    <!-- ORBITAL-AGENTS:BEGIN -->
    ...
    <!-- ORBITAL-AGENTS:END -->

goes at the end of ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, ~/.grok/AGENTS.md and ~/.gemini/GEMINI.md.
The first change to an existing file saves `<file>.orbital-backup`; text outside the fence is
never touched; uninstall removes exactly the fence. If a file already tells agents to report to
another tracker, the rule is not added there by default (agents would report twice).

When the repo has no `skills/` folders yet, the tracker's own templates in apps/tracker/integrations/
(SKILL.md → `orbital-tracker`, agent-rules.md) are used, and failing that a small built-in pair.

Placeholders in SKILL.md files and agent-rules.md, filled per agent home: {{AGENT}} / {{agent}}
(claude, codex, grok, gemini), {{AGENT_NAME}} (Claude, Codex, Grok, Gemini), {{machine}},
{{hub_url}}, {{orbital_home}}.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orbital import __version__
from orbital.core.paths import REPO_ROOT
from orbital.services.ops import Ops

BEGIN = "<!-- ORBITAL-AGENTS:BEGIN -->"
END = "<!-- ORBITAL-AGENTS:END -->"
BEGIN_RE = re.compile(r"<!--\s*ORBITAL-AGENTS:BEGIN\b[^>]*-->")
END_RE = re.compile(r"<!--\s*ORBITAL-AGENTS:END\s*-->")
MARKER = ".orbital-skill.json"
INTEGRATIONS = Path("apps/tracker/integrations")
TEXT_SUFFIXES = {".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".sh", ".py"}
AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "grok": "Grok", "gemini": "Gemini"}
BACKUP_SUFFIX = ".orbital-backup"

# agent → (home folder, skills folder inside it, always-on rules file inside it)
AGENTS: dict[str, tuple[str, str, str]] = {
    "claude": (".claude", "skills", "CLAUDE.md"),
    "codex": (".codex", "skills", "AGENTS.md"),
    "grok": (".grok", "skills", "AGENTS.md"),
    "gemini": (".gemini", "config/skills", "GEMINI.md"),
}

# Another tracker's rules: a fenced *TRACK*:BEGIN block, or a `<name>-track start|update|done|sub`
# command that isn't ours.
CONFLICT_PATTERNS = (
    re.compile(r"<!--\s*(?!ORBITAL-AGENTS:)[A-Z0-9_-]*TRACK[A-Z0-9_-]*:BEGIN", re.I),
    re.compile(r"\b(?!orbital-track\b)[a-z0-9][a-z0-9_]*-track(?:er)?\s+(?:start|update|done|sub)\b", re.I),
)

FALLBACK_SKILL = """---
name: orbital-agents
description: Use Orbital Agents from an AI agent - report work to the Agent Satellite Tracker with orbital-track, push data to the hub with orbital push, run agents on other machines with orbital satellites, and check health with orbital doctor.
---

# Orbital Agents

- Report work: `orbital-track start --agent <you> --title "2-5 words" --details "goal"` prints an ID;
  `orbital-track update <ID> --note "..."`; `orbital-track done <ID> --summary "..."`.
- Subagents (prompt contains `ORBITAL_TRACK_PARENT=<ID>`): `orbital-track sub <ID> --agent <you> --title "..."`.
- Health: `orbital doctor` (add `--fix` for safe repairs); services: `orbital services status`.
- Machines: `orbital satellites`; run an agent elsewhere through the hub.
- Tests: `orbital test --live` checks the running install end to end.
"""

FALLBACK_RULES = """## Orbital Agents: show what you're working on

You are **{{agent}}**. Report real work to the Agent Satellite Tracker with `orbital-track`
(skip one-shot questions):

1. Starting multi-step work: `orbital-track start --agent {{agent}} --title "<2-5 word title>" --details "<goal, project>"`.
   It prints an ID; remember it (shell variables don't survive between tool calls).
2. Milestones: `orbital-track update <ID> --note "<what just happened>"` (attach screenshots with `--media /abs/file.png`).
3. Finished or stopping: `orbital-track done <ID> --summary "<what changed, where, what's left>"`.
4. If your prompt contains `ORBITAL_TRACK_PARENT=<ID>`, you are a subagent: use
   `orbital-track sub <ID> --agent {{agent}} --title "<your piece>"` and `orbital-track done <SUB_ID>`, never `start`.
   When you start a subagent, add `ORBITAL_TRACK_PARENT=<your ID>` to its prompt.

Tracking never blocks work: if `orbital-track` is missing or fails, carry on.
"""


@dataclass
class AgentHome:
    agent: str
    root: Path
    skills_dir: Path
    rules_file: Path

    @property
    def exists(self) -> bool:
        return self.root.is_dir()


@dataclass
class SkillSource:
    name: str              # folder name in the repo
    installed_name: str    # folder name in the agent home
    path: Path | None      # None = built-in fallback
    files: dict[str, bytes] = field(default_factory=dict)

    def rendered(self, agent: str, values: dict[str, str] | None = None) -> dict[str, bytes]:
        """Files with the per-agent placeholders filled in (text files only)."""
        out: dict[str, bytes] = {}
        for rel, data in self.files.items():
            if Path(rel).suffix.lower() in TEXT_SUFFIXES:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    out[rel] = data
                    continue
                out[rel] = fill(text, agent, values).encode("utf-8")
            else:
                out[rel] = data
        return out

    def digest(self, agent: str, values: dict[str, str] | None = None) -> str:
        files = self.rendered(agent, values)
        h = hashlib.sha256()
        for rel in sorted(files):
            h.update(rel.encode())
            h.update(b"\0")
            h.update(files[rel])
            h.update(b"\0")
        return h.hexdigest()


@dataclass
class Event:
    agent: str
    level: str          # ok | warn | fail | info
    message: str


def fill(text: str, agent: str, values: dict[str, str] | None = None) -> str:
    body = text.replace("{{AGENT}}", agent).replace("{{agent}}", agent)
    body = body.replace("{{AGENT_NAME}}", AGENT_NAMES.get(agent, agent.capitalize()))
    for k, v in (values or {}).items():
        body = body.replace("{{" + k + "}}", v)
    return body


def installed_name(name: str) -> str:
    if name.startswith("orbital-"):
        return name
    if name == "orbital":
        return "orbital-agents"
    return f"orbital-{name}"


def agent_homes(home: Path | None = None, agents: list[str] | None = None) -> list[AgentHome]:
    home = Path(home) if home else Path.home()
    out = []
    for agent, (root, skills, rules) in AGENTS.items():
        if agents and agent not in agents:
            continue
        r = home / root
        out.append(AgentHome(agent, r, r / skills, r / rules))
    return out


def _rename_frontmatter(text: str, name: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    head, rest = text[:end], text[end:]
    head = re.sub(r"(?m)^name:\s*.*$", f"name: {name}", head, count=1)
    return head + rest


def _skill_from_dir(d: Path, name: str | None = None) -> SkillSource:
    target = installed_name(name or d.name)
    files: dict[str, bytes] = {}
    for f in sorted(d.rglob("*")):
        if not f.is_file() or f.name in (MARKER, ".DS_Store") or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(d).as_posix()
        data = f.read_bytes()
        if rel == "SKILL.md":
            data = _rename_frontmatter(data.decode("utf-8"), target).encode("utf-8")
        files[rel] = data
    return SkillSource(name or d.name, target, d, files)


def _frontmatter_name(text: str) -> str:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if m:
        n = re.search(r"(?m)^name:\s*([A-Za-z0-9_.-]+)\s*$", m.group(1))
        if n:
            return n.group(1)
    return ""


def source_skills(repo: Path = REPO_ROOT) -> list[SkillSource]:
    """Skill folders in the repo (`skills/<name>/SKILL.md`); without any, the tracker's integration
    SKILL.md; without that, a built-in one."""
    root = repo / "skills"
    out: list[SkillSource] = []
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
            out.append(_skill_from_dir(d))
    if out:
        return out
    integ = repo / INTEGRATIONS / "SKILL.md"
    if integ.is_file():
        text = integ.read_text(encoding="utf-8")
        name = installed_name(_frontmatter_name(text) or "orbital-tracker")
        return [SkillSource(name, name, integ.parent,
                            {"SKILL.md": _rename_frontmatter(text, name).encode("utf-8")})]
    return [SkillSource("orbital-agents", "orbital-agents", None, {"SKILL.md": FALLBACK_SKILL.encode()})]


def rules_template(repo: Path = REPO_ROOT) -> str:
    for f in (repo / "skills" / "agent-rules.md", repo / INTEGRATIONS / "agent-rules.md"):
        if f.is_file():
            return f.read_text(encoding="utf-8")
    return FALLBACK_RULES


def render_rules(template: str, agent: str, values: dict[str, str] | None = None) -> str:
    body = fill(template, agent, values).strip("\n")
    return f"{BEGIN}\n<!-- Managed by `orbital skills`. Edits inside this block are replaced on update; "\
           f"`orbital skills uninstall` removes it. -->\n{body}\n{END}"


# ----------------------------------------------------------------------- fence editing


def find_block(text: str) -> tuple[int, int] | None:
    b = BEGIN_RE.search(text)
    if not b:
        return None
    e = END_RE.search(text, b.end())
    if not e:
        return None
    return b.start(), e.end()


def has_block(text: str) -> bool:
    return find_block(text) is not None


def block_body(text: str) -> str | None:
    span = find_block(text)
    return text[span[0]:span[1]] if span else None


def insert_block(text: str, block: str) -> str:
    """Replace our fenced block in place, or append it (separated by one blank line)."""
    span = find_block(text)
    if span:
        return text[:span[0]] + block + text[span[1]:]
    if not text:
        return block + "\n"
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "\n" + block + "\n"


def remove_block(text: str) -> tuple[str, bool]:
    """Remove our block plus the newline after it and the blank line insert_block added before it."""
    span = find_block(text)
    if not span:
        return text, False
    start, end = span
    if text[end:end + 1] == "\n":
        end += 1
    before = text[:start]
    if before.endswith("\n\n"):
        before = before[:-1]
    elif before == "\n":
        before = ""
    return before + text[end:], True


def outside_block(text: str) -> str:
    span = find_block(text)
    return text if not span else text[:span[0]] + text[span[1]:]


def find_conflicts(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every other-tracker rule outside our block."""
    outside = outside_block(text)
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(outside.splitlines(), 1):
        if any(p.search(line) for p in CONFLICT_PATTERNS):
            hits.append((i, line.strip()[:120]))
    return hits


# ----------------------------------------------------------------------- installer


class Installer:
    def __init__(self, home: Path | None = None, repo: Path = REPO_ROOT, ops: Ops | None = None,
                 agents: list[str] | None = None, rules: str = "auto", force: bool = False,
                 values: dict[str, str] | None = None,
                 confirm_conflict: Callable[[AgentHome, list[tuple[int, str]]], bool] | None = None):
        if rules not in ("auto", "yes", "no"):
            raise ValueError("rules must be auto, yes or no")
        self.home = Path(home) if home else Path.home()
        self.repo = repo
        self.ops = ops or Ops()
        self.agents = agents
        self.rules = rules
        self.force = force
        self.values = values or {}
        self.confirm_conflict = confirm_conflict
        self.events: list[Event] = []

    def _event(self, agent: str, level: str, message: str) -> None:
        self.events.append(Event(agent, level, message))

    def homes(self) -> list[AgentHome]:
        return agent_homes(self.home, self.agents)

    # -- skills
    def _read_marker(self, folder: Path) -> dict | None:
        m = folder / MARKER
        if not m.is_file():
            return None
        try:
            return json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _install_skill(self, h: AgentHome, s: SkillSource) -> None:
        target = h.skills_dir / s.installed_name
        marker = self._read_marker(target) if target.exists() else None
        if target.exists() and marker is None and not self.force:
            self._event(h.agent, "warn", f"{target} exists and wasn't installed by Orbital Agents; left alone "
                                         "(use --force to replace it)")
            return
        files = s.rendered(h.agent, self.values)
        digest = s.digest(h.agent, self.values)
        if marker and marker.get("digest") == digest:
            self._event(h.agent, "ok", f"skill {s.installed_name}: up to date")
            return
        if target.exists():
            keep = set(files) | {MARKER}
            for f in sorted(target.rglob("*"), reverse=True):
                rel = f.relative_to(target).as_posix()
                if f.is_file() and rel not in keep:
                    self.ops.remove(f)
        for rel, data in files.items():
            self.ops.write(target / rel, data)
        source = (s.path.relative_to(self.repo).as_posix() if s.path and s.path.is_relative_to(self.repo)
                  else "built-in")
        meta = {"installed_by": "orbital skills", "version": __version__, "source": source, "digest": digest}
        self.ops.write(target / MARKER, json.dumps(meta, indent=2) + "\n")
        self._event(h.agent, "ok", f"skill {s.installed_name}: {'updated' if marker else 'installed'}")

    def _remove_stale_skills(self, h: AgentHome, keep: set[str]) -> None:
        if not h.skills_dir.is_dir():
            return
        for folder in sorted(h.skills_dir.glob("orbital-*")):
            if folder.is_dir() and folder.name not in keep and self._read_marker(folder) is not None:
                self.ops.rmtree(folder)
                self._event(h.agent, "info", f"skill {folder.name}: removed (no longer in the repo)")

    # -- rules
    def _backup(self, f: Path) -> None:
        b = f.with_name(f.name + BACKUP_SUFFIX)
        if f.is_file() and not b.exists():
            self.ops.copy(f, b)
            self._event("", "info", f"backed up {f} → {b.name}")

    def _install_rules(self, h: AgentHome) -> None:
        if self.rules == "no":
            self._event(h.agent, "info", "always-on rule: skipped (rules = no)")
            return
        text = h.rules_file.read_text(encoding="utf-8") if h.rules_file.is_file() else ""
        conflicts = find_conflicts(text)
        if conflicts:
            line_no, snippet = conflicts[0]
            install = self.rules == "yes"
            if not install and self.confirm_conflict:
                install = bool(self.confirm_conflict(h, conflicts))
            if not install:
                self._event(h.agent, "warn",
                            f"{h.rules_file} already tells agents to report to another tracker "
                            f"(line {line_no}: {snippet!r}); the always-on rule was NOT added, so agents "
                            f"don't report twice. Skills are installed. Add it anyway: "
                            f"`orbital skills install --rules yes`")
                return
            self._event(h.agent, "warn", f"{h.rules_file} also has another tracker rule (line {line_no}); "
                                         "agents may report twice")
        block = render_rules(rules_template(self.repo), h.agent, self.values)
        new = insert_block(text, block)
        if new == text:
            self._event(h.agent, "ok", f"always-on rule in {h.rules_file.name}: up to date")
            return
        self._backup(h.rules_file)
        self.ops.write(h.rules_file, new)
        self._event(h.agent, "ok", f"always-on rule in {h.rules_file}: {'updated' if has_block(text) else 'added'}")

    # -- public
    def install(self) -> list[Event]:
        skills = source_skills(self.repo)
        found = False
        for h in self.homes():
            if not h.exists:
                self._event(h.agent, "info", f"{h.root} not found: {h.agent} isn't set up here, skipped")
                continue
            found = True
            self.ops.mkdir(h.skills_dir)
            for s in skills:
                self._install_skill(h, s)
            self._remove_stale_skills(h, {s.installed_name for s in skills})
            self._install_rules(h)
        if not found:
            self._event("", "info", "no agent homes found (~/.claude, ~/.codex, ~/.grok, ~/.gemini)")
        return self.events

    def uninstall(self, rules_only: bool = False) -> list[Event]:
        for h in self.homes():
            if not h.exists:
                continue
            if not rules_only and h.skills_dir.is_dir():
                for folder in sorted(h.skills_dir.glob("orbital-*")):
                    if folder.is_dir() and self._read_marker(folder) is not None:
                        self.ops.rmtree(folder)
                        self._event(h.agent, "ok", f"skill {folder.name}: removed")
            if h.rules_file.is_file():
                text = h.rules_file.read_text(encoding="utf-8")
                new, removed = remove_block(text)
                if removed:
                    backup = h.rules_file.with_name(h.rules_file.name + BACKUP_SUFFIX)
                    if not new.strip() and not backup.exists():
                        self.ops.remove(h.rules_file)        # we created this file
                    else:
                        self.ops.write(h.rules_file, new)
                    self._event(h.agent, "ok", f"always-on rule removed from {h.rules_file}")
        return self.events

    def status(self) -> list[dict]:
        skills = source_skills(self.repo)
        rows = []
        for h in self.homes():
            row = {"agent": h.agent, "home": str(h.root), "present": h.exists, "skills": {}, "rules": "n/a",
                   "conflicts": []}
            if h.exists:
                for s in skills:
                    target = h.skills_dir / s.installed_name
                    m = self._read_marker(target) if target.exists() else None
                    if not target.exists():
                        row["skills"][s.installed_name] = "missing"
                    elif m is None:
                        row["skills"][s.installed_name] = "not ours"
                    else:
                        row["skills"][s.installed_name] = ("ok" if m.get("digest") == s.digest(h.agent, self.values)
                                                           else "outdated")
                text = h.rules_file.read_text(encoding="utf-8") if h.rules_file.is_file() else ""
                row["conflicts"] = find_conflicts(text)
                if has_block(text):
                    expected = render_rules(rules_template(self.repo), h.agent, self.values)
                    row["rules"] = "ok" if block_body(text) == expected else "outdated"
                else:
                    row["rules"] = "skipped (other tracker rule)" if row["conflicts"] else "missing"
            rows.append(row)
        return rows


def other_tracker_signals(home: Path | None = None, path_env: str | None = None) -> list[str]:
    """Signs that another agent tracker is set up on this machine: a `<name>-track` CLI on PATH
    (not orbital-track) or another tracker rule in an agent's rules file. Used to warn about double
    reporting and to suggest different tracker shortcuts."""
    import os

    signals: list[str] = []
    seen: set[str] = set()
    for d in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep):
        try:
            entries = list(Path(d).iterdir()) if d and Path(d).is_dir() else []
        except OSError:
            continue
        for f in entries:
            n = f.name
            if (n.endswith("-track") or n.endswith("-tracker")) and n not in ("orbital-track", "orbital-tracker") \
                    and n not in seen and os.access(f, os.X_OK):
                seen.add(n)
                signals.append(f"{n} on PATH ({f.parent})")
    for h in agent_homes(home):
        if h.rules_file.is_file():
            try:
                hits = find_conflicts(h.rules_file.read_text(encoding="utf-8"))
            except OSError:
                continue
            if hits:
                signals.append(f"another tracker rule in {h.rules_file} (line {hits[0][0]})")
    return signals
