"""`orbital satellites`: the machines joined to this hub.

    orbital satellites [list]          every satellite, online or offline, with its agents
    orbital satellites invite          the join token and the hub address(es) a new machine needs
    orbital satellites remove <id>     forget a machine (it can join again later)

`list` and `remove` talk to the hub with the automation token. `invite` reads the hub's own
secrets, so it runs on the hub.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

NAME = "satellites"
HELP = "Machines joined to this hub: list | invite | remove <id>"


def register(p):
    p.add_argument("action", nargs="?", default="list", choices=["list", "invite", "remove"])
    p.add_argument("id", nargs="?", help="satellite id or machine name (remove)")
    p.add_argument("--json", action="store_true", help="print JSON")


def _ago(stamp: str | None) -> str:
    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 90:
        return f"{int(seconds)} s ago"
    if seconds < 5400:
        return f"{int(seconds // 60)} min ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def _list(args) -> int:
    from orbital.feeds import HubClient, HubError

    try:
        with HubClient.from_config(startup_wait=0) as hub:
            rows = hub.satellites()
    except HubError as exc:
        print(f"orbital satellites: {exc.message}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No satellites yet. On another machine run ./setup (or: orbital satellite join <hub> <token>).")
        print("The token: orbital satellites invite")
        return 0
    width = max(len(r.get("name") or r["id"]) for r in rows)
    for row in rows:
        caps = row.get("capabilities") or {}
        extras = [name for name, on in (("browser", caps.get("browser")), ("tracker", caps.get("tracker"))) if on]
        agents = ", ".join(caps.get("agents") or []) or "no agents"
        mark = "●" if row.get("status") == "online" else "○"
        running = f", {row['running_jobs']} running" if row.get("running_jobs") else ""
        print(f"{mark} {(row.get('name') or row['id']).ljust(width)}  {row.get('status'):7}  {row.get('ip') or row.get('host') or '-':15}  "
              f"seen {_ago(row.get('last_seen'))}{running}  [{agents}{'; ' + ', '.join(extras) if extras else ''}]  id {row['id']}")
    return 0


def _invite(args) -> int:
    from orbital.core.config import load_config
    from orbital.core.net import tailscale_self
    from orbital.core.secrets import Secrets

    cfg = load_config()
    if cfg.role != "hub":
        print("orbital satellites invite: run this on the hub (this machine is a satellite).", file=sys.stderr)
        return 1
    store = Secrets(cfg.paths)
    token = store.get("ORBITAL_JOIN_TOKEN") or store.ensure_token("ORBITAL_JOIN_TOKEN")
    port = int(cfg.get("hub.port", 7700))
    urls = []
    me = tailscale_self()
    if me and me.dns:
        urls.append(f"http://{me.dns}:{port}")
    if me and me.ip:
        urls.append(f"http://{me.ip}:{port}")
    urls.append(f"http://{cfg.machine_name}:{port}")
    if args.json:
        print(json.dumps({"join_token": token, "hub_urls": urls}, indent=2))
        return 0
    print("To add a machine, run ./setup on it and choose satellite, or:")
    print(f"  orbital satellite join {urls[0]} {token}")
    print()
    print(f"Join token: {token}")
    print("Hub address" + ("es" if len(urls) > 1 else "") + ":")
    for url in urls:
        print(f"  {url}")
    if not me:
        print("(Tailscale isn't running here, so other machines may not reach these addresses.)")
    return 0


def _remove(args) -> int:
    from orbital.feeds import HubClient, HubError

    if not args.id:
        print("orbital satellites remove: which one? (orbital satellites lists the ids)", file=sys.stderr)
        return 2
    try:
        with HubClient.from_config(startup_wait=0) as hub:
            answer = hub.remove_satellite(args.id)
    except HubError as exc:
        print(f"orbital satellites: {exc.message}", file=sys.stderr)
        return 1
    print(json.dumps(answer) if args.json else f"removed {answer.get('id')}")
    return 0


def run(args) -> int:
    return {"list": _list, "invite": _invite, "remove": _remove}[args.action](args)
