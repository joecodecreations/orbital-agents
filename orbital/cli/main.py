"""`orbital` command line.

Every file in orbital/cli/commands/ is a subcommand. A command module provides:

    NAME = "status"                     # the subcommand
    HELP = "One-line description"
    def register(parser): ...           # add arguments to its argparse subparser
    def run(args) -> int: ...           # exit code

Add a command by adding a file; nothing else needs to change.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys

from orbital import __version__
from orbital.cli import commands as commands_pkg


def _load_commands() -> list:
    mods = []
    for info in pkgutil.iter_modules(commands_pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"orbital.cli.commands.{info.name}")
        if hasattr(mod, "NAME") and hasattr(mod, "run"):
            mods.append(mod)
    return sorted(mods, key=lambda m: m.NAME)


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    parser = argparse.ArgumentParser(prog="orbital", description="Orbital Agents: hub, satellites, tracker, apps and browser controller.")
    parser.add_argument("--version", action="version", version=f"orbital {__version__}")
    parser.add_argument("--home", help="use another ORBITAL_HOME (default ~/.orbital-agents)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    table = {}
    for mod in _load_commands():
        p = sub.add_parser(mod.NAME, help=getattr(mod, "HELP", ""), description=getattr(mod, "HELP", ""))
        if hasattr(mod, "register"):
            mod.register(p)
        table[mod.NAME] = mod
    return parser, table


def main(argv: list[str] | None = None) -> int:
    parser, table = build_parser()
    args = parser.parse_args(argv)
    if args.home:
        import os
        os.environ["ORBITAL_HOME"] = args.home
    if not args.command:
        parser.print_help()
        return 0
    try:
        return int(table[args.command].run(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
