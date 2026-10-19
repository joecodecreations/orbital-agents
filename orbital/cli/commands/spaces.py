"""`orbital spaces`: the hub's own spaces, which file sections (and old company names) into spaces.

    orbital spaces                  the merged list the apps draw, then what the hub declares
    orbital spaces set <file|->     replace the declared spaces with a JSON file ({"spaces": [...]})
    orbital spaces clear            remove them (sections fall back to their own `space`)

A declared space: {"id": "acme-games", "title": "Acme Games", "icon": "gamecontroller.fill",
"sections": ["acme-games", "roblox"], "aliases": ["Acme Games LLC"]}. A section listed here stays in
that space even when its feed rewrites it.
"""

from __future__ import annotations

import json
import sys

NAME = "spaces"
HELP = "The hub's own spaces: show | set <file> | clear"


def register(p):
    p.add_argument("action", nargs="?", default="show", choices=["show", "set", "clear"])
    p.add_argument("file", nargs="?", help="JSON file for set (- reads stdin)")
    p.add_argument("--json", action="store_true", help="print JSON")


def run(args) -> int:
    from orbital.feeds import HubClient, HubError

    try:
        with HubClient.from_config(startup_wait=0) as hub:
            if args.action == "show":
                answer = hub.request("GET", "/v1/spaces")
                if args.json:
                    print(json.dumps(answer, indent=2))
                    return 0
                declared = {s["id"] for s in answer.get("declared") or []}
                for space in answer.get("spaces") or []:
                    mark = "*" if space["id"] in declared else " "
                    print(f"{mark} {space['title']:24} {space['id']:20} {', '.join(space.get('sections') or []) or '-'}")
                print("\n* declared by the hub (orbital spaces set); the rest come from each section's own space")
            elif args.action == "clear":
                hub.request("PUT", "/v1/spaces", json_body={"spaces": []})
                print("declared spaces removed")
            else:
                if not args.file:
                    print("orbital spaces set: give a JSON file (or - for stdin)", file=sys.stderr)
                    return 2
                text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
                answer = hub.request("PUT", "/v1/spaces", json_body=json.loads(text))
                print(f"saved {len(answer.get('declared') or [])} spaces")
    except HubError as exc:
        print(f"orbital spaces: {exc.message}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"orbital spaces: {exc}", file=sys.stderr)
        return 1
    return 0
