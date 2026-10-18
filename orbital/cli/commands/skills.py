"""`orbital skills`: install, update, remove or show the agent skills and the always-on rule block."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orbital.core.config import load_config
from orbital.services.ops import Ops
from orbital.wizard.skills import AGENTS, Installer
from orbital.wizard.ui import UI

NAME = "skills"
HELP = "Install / uninstall / show the Orbital Agents skills and rules for claude, codex, grok, gemini"


def register(p):
    p.add_argument("action", nargs="?", default="status", choices=["install", "uninstall", "status"])
    p.add_argument("--agent", action="append", choices=sorted(AGENTS), help="only this agent home (repeatable)")
    p.add_argument("--rules", choices=["auto", "yes", "no"], default="auto",
                   help="always-on rule block: auto skips files that already have another tracker rule (default)")
    p.add_argument("--rules-only", action="store_true", help="uninstall: remove only the rule blocks, keep the skills")
    p.add_argument("--force", action="store_true", help="replace orbital-* skill folders that weren't installed by us")
    p.add_argument("--dry-run", action="store_true", help="print what would change")
    p.add_argument("--json", action="store_true", help="status as JSON")
    p.add_argument("--user-home", help=argparse.SUPPRESS, default=None)   # tests: a fake home folder


def run(args) -> int:
    from orbital.wizard.flow import rule_values

    ui = UI()
    cfg = load_config()
    ops = Ops(dry_run=args.dry_run, out=ui.line)
    home = Path(args.user_home).expanduser() if args.user_home else None
    inst = Installer(home=home, ops=ops, agents=args.agent, rules=args.rules, force=args.force,
                     values=rule_values(cfg))
    if args.action == "status":
        rows = inst.status()
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
            return 0
        bad = 0
        for r in rows:
            if not r["present"]:
                ui.line(ui.style(f"  {r['agent']:7} {r['home']} not found", "dim"))
                continue
            skills = ", ".join(f"{k} {v}" for k, v in r["skills"].items())
            level = "ok" if all(v == "ok" for v in r["skills"].values()) and r["rules"] in ("ok", "skipped (other tracker rule)") else "warn"
            bad += level != "ok"
            ui.status(level, f"{r['agent']:7} skills: {skills or 'none'}; rule: {r['rules']}")
            for line_no, snippet in r["conflicts"][:1]:
                ui.hint(f"another tracker rule at line {line_no}: {snippet}")
        if bad:
            ui.hint("Update with: orbital skills install")
        return 0
    events = inst.install() if args.action == "install" else inst.uninstall(rules_only=args.rules_only)
    for e in events:
        ui.status(e.level, (f"{e.agent}: " if e.agent else "") + e.message)
    return 1 if any(e.level == "fail" for e in events) else 0
