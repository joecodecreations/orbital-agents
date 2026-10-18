"""`orbital setup`: the setup wizard (./setup runs this after preparing Python)."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

NAME = "setup"
HELP = "Set up this machine as the hub or a satellite (interactive, or --answers file --yes)"


def register(p):
    p.add_argument("--role", choices=["hub", "satellite"], help="skip the question: this machine's role")
    p.add_argument("--answers", metavar="FILE", help="answers TOML (see config/answers.example.toml)")
    p.add_argument("--yes", "-y", action="store_true", help="never ask: use answers, kept values and defaults")
    p.add_argument("--dry-run", action="store_true", help="print every action; change nothing")
    p.add_argument("--reconfigure", action="store_true", help="ask every question again (current values are the defaults)")


def run(args) -> int:
    from orbital.wizard.answers import Answers
    from orbital.wizard.flow import Options, run_wizard

    try:
        answers = Answers.load(args.answers)
    except FileNotFoundError:
        print(f"orbital setup: answers file not found: {args.answers}", file=sys.stderr)
        return 2
    except tomllib.TOMLDecodeError as exc:
        print(f"orbital setup: {args.answers} is not valid TOML: {exc}", file=sys.stderr)
        return 2
    opts = Options(role=args.role, answers=answers, yes=args.yes, dry_run=args.dry_run,
                   reconfigure=args.reconfigure, user_home=Path.home())
    return run_wizard(opts)
