"""`orbital nav`: the apps' bottom-bar items and sidebar tree (orbital.nav.v1), changed without an app update.

    orbital nav                 print the current document
    orbital nav set <file|->    replace it with a JSON file (or stdin)
    orbital nav reset           back to the default (no extra tabs; sidebar = spaces and their sections)
    orbital nav example         print an example to start from

The format is described in orbital/hub/nav.py and docs/APP.md.
"""

from __future__ import annotations

import json
import sys

NAME = "nav"
HELP = "The apps' custom bottom-bar items and sidebar tree: show | set <file> | reset | example"

EXAMPLE = {
    "schema": "orbital.nav.v1",
    "tabs": [
        {"id": "money", "title": "Money", "icon": "dollarsign.circle.fill", "target": {"kind": "view", "view": "spaces"}},
        {"id": "marketing", "title": "Marketing", "icon": "megaphone.fill", "children": [
            {"id": "social", "title": "Social", "icon": "person.2.fill", "target": {"kind": "section", "section": "social"}},
            {"id": "videos", "title": "Videos", "icon": "play.rectangle.fill", "target": {"kind": "section", "section": "videos"}},
        ]},
    ],
    "sidebar": [
        {"id": "work", "title": "Work", "icon": "briefcase.fill", "children": [
            {"id": "site", "title": "Website uptime", "target": {"kind": "section", "section": "website-uptime"}},
            {"id": "lab", "title": "Home Lab", "target": {"kind": "space", "space": "home-lab"}},
        ]},
        {"id": "everything", "title": "All pages", "icon": "square.grid.2x2", "target": {"kind": "view", "view": "browse"}},
    ],
}


def register(p):
    p.add_argument("action", nargs="?", default="show", choices=["show", "set", "reset", "example"])
    p.add_argument("file", nargs="?", help="JSON file for set (- reads stdin)")


def run(args) -> int:
    if args.action == "example":
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    from orbital.feeds import HubClient, HubError

    try:
        with HubClient.from_config(startup_wait=0) as hub:
            if args.action == "show":
                print(json.dumps(hub.request("GET", "/v1/nav"), indent=2))
            elif args.action == "reset":
                hub.request("DELETE", "/v1/nav")
                print("navigation reset to the default")
            else:
                if not args.file:
                    print("orbital nav set: give a JSON file (or - for stdin)", file=sys.stderr)
                    return 2
                text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
                answer = hub.request("PUT", "/v1/nav", json_body=json.loads(text))
                nav = answer.get("nav") or {}
                print(f"saved: {len(nav.get('tabs') or [])} extra tabs, "
                      f"sidebar {'default' if nav.get('sidebar') is None else str(len(nav['sidebar'])) + ' items'}")
    except HubError as exc:
        print(f"orbital nav: {exc.message}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"orbital nav: {exc}", file=sys.stderr)
        return 1
    return 0
