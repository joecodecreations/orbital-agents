"""`orbital push`: send a section, an ops batch, or an alert to the hub with the automation token.

    orbital push section.json                       PUT the section (its "id", or --section)
    orbital push ops.json --section home-server     POST {"ops": [...]} to that section
    cat section.json | orbital push -               read from stdin
    orbital push section.json --validate            check it, write nothing, show warnings
    orbital push --alert "Backup failed" --body "Exit code 2" [--section home-server] [--level warning]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NAME = "push"
HELP = "Push a section or ops batch (file or -), or an alert (--alert TITLE --body TEXT)"


def register(p):
    p.add_argument("file", nargs="?", help="section or ops JSON file, or - for stdin")
    p.add_argument("--section", help="section id (default: the document's id)")
    p.add_argument("--validate", action="store_true", help="check only; the hub writes nothing")
    p.add_argument("--alert", metavar="TITLE", help="raise an alert instead of pushing a file")
    p.add_argument("--body", default="", help="alert body")
    p.add_argument("--level", default="info", choices=["passive", "info", "warning", "error", "critical"], help="alert level")
    p.add_argument("--id", dest="alert_id", help="alert id (default: same title and body on the same day count once)")
    p.add_argument("--json", action="store_true", help="print the hub's answer as JSON")


def _read(source: str) -> object:
    raw = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text(encoding="utf-8")
    return json.loads(raw)


def run(args) -> int:
    from orbital.feeds import HubClient, HubError

    if not args.alert and not args.file:
        print("orbital push: give a JSON file (or -), or --alert TITLE --body TEXT", file=sys.stderr)
        return 2
    try:
        hub = HubClient.from_config(startup_wait=0)
    except HubError as exc:
        print(f"orbital push: {exc.message}", file=sys.stderr)
        return 1
    try:
        if args.alert:
            alert_id = hub.alert(args.alert, args.body, section_id=args.section, level=args.level, id=args.alert_id)
            print(json.dumps({"ok": True, "alert_id": alert_id}) if args.json else f"alert {alert_id}")
            return 0
        try:
            doc = _read(args.file)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"orbital push: can't read {args.file}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(doc, dict):
            print("orbital push: the file must hold one JSON object", file=sys.stderr)
            return 2
        section_id = args.section or doc.get("section_id") or (doc.get("id") if "ops" not in doc else None)
        if not section_id:
            print("orbital push: which section? add --section ID", file=sys.stderr)
            return 2
        if "ops" in doc:
            if args.validate:
                answer = hub.validate(section_id, doc.get("ops") or [], alerts=doc.get("alerts"), if_revision=doc.get("if_revision"))
            else:
                answer = hub.patch_section(section_id, doc.get("ops") or [], alerts=doc.get("alerts"), if_revision=doc.get("if_revision"))
        elif args.validate:
            answer = hub.validate(section_id, section=doc)
        else:
            answer = hub.put_section(doc, section_id)
    except HubError as exc:
        print(f"orbital push: {exc}", file=sys.stderr)
        return 1
    finally:
        hub.close()
    if args.json:
        print(json.dumps(answer, indent=2))
    elif args.validate:
        warnings = answer.get("warnings") or []
        print(f"{section_id}: ok" + (f", would write (revision {answer.get('revision')})" if answer.get("would_write") else ""))
        for warning in warnings:
            print(f"  warning: {warning.get('hint')}")
    else:
        revision = answer.get("revision")
        if revision is None and isinstance(answer.get("section"), dict):
            revision = answer["section"].get("revision")
        print(f"{section_id}: revision {revision}")
    return 0
