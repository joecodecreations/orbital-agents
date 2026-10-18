"""`orbital services`: install, remove and control the login services on this machine."""

from __future__ import annotations

import json
import sys

from orbital.core.config import load_config
from orbital.services import SERVICE_NAMES, Ops
from orbital.wizard.ui import UI

NAME = "services"
HELP = "Install, remove, start, stop, restart and show the background services"


def register(p):
    p.add_argument("action", nargs="?", default="status",
                   choices=["install", "uninstall", "status", "restart", "start", "stop"])
    p.add_argument("names", nargs="*", metavar="name", help=f"service names ({', '.join(SERVICE_NAMES)}); default all")
    p.add_argument("--dry-run", action="store_true", help="print what would be done")
    p.add_argument("--json", action="store_true", help="status as JSON")
    p.add_argument("--no-health", action="store_true", help="status without the HTTP health checks")


def _print_outcomes(ui: UI, outcomes) -> int:
    rc = 0
    for o in outcomes:
        (ui.ok if o.ok else ui.fail)(f"{o.name:15} {o.message}")
        rc |= 0 if o.ok else 1
    if not outcomes:
        ui.info("nothing to do")
    return rc


def print_status(ui: UI, rows) -> int:
    bad = 0
    header = f"  {'service':15} {'wanted':7} {'loaded':7} {'state':22} {'pid':>7}  health"
    ui.line(ui.style(header, "bold"))
    for s in rows:
        if not s.wanted and not s.installed:
            ui.line(ui.style(f"  {s.name:15} {'no':7} {'-':7} {'(' + s.reason + ')':22}", "dim"))
            continue
        health = "-" if s.healthy is None else (s.health if s.healthy else ui.style(s.health, "red"))
        state = s.state or "-"
        if s.wanted and not s.current and s.installed:
            state += " (outdated)"
        line = (f"  {s.name:15} {'yes' if s.wanted else 'NO':7} {'yes' if s.loaded else 'no':7} "
                f"{state[:22]:22} {str(s.pid or '-'):>7}  {health}")
        if not s.ok:
            bad += 1
            line = ui.style(line, "yellow")
        ui.line(line)
    if bad:
        ui.line("")
        ui.warn("Fix: `orbital services install` (re)installs and loads them; `orbital doctor --fix` also restarts unhealthy ones.")
    return 1 if bad else 0


def run(args) -> int:
    cfg = load_config()
    ui = UI()
    ops = Ops(dry_run=args.dry_run, out=ui.line)
    names = list(args.names or [])
    from orbital import services as svc
    try:
        if args.action == "install":
            outcomes, notes = svc.install(cfg, ops=ops, names=names or None)
            rc = _print_outcomes(ui, outcomes)
            for n in notes:
                ui.warn(n)
            return rc
        if args.action == "uninstall":
            return _print_outcomes(ui, svc.uninstall(cfg, names or None, ops=ops))
        if args.action in ("restart", "start", "stop"):
            return _print_outcomes(ui, getattr(svc, args.action)(cfg, names or None, ops=ops))
        rows = svc.status(cfg, ops=ops, health=not args.no_health, names=names or None)
        if args.json:
            print(json.dumps([r.to_dict() for r in rows], indent=2))
            return 0 if all(r.ok for r in rows) else 1
        return print_status(ui, rows)
    except (RuntimeError, ValueError) as exc:
        print(f"orbital services: {exc}", file=sys.stderr)
        return 2
