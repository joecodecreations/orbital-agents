"""`orbital satellites`: the machines joined to this hub.

    orbital satellites [list]          every satellite, online or offline, with its agents
    orbital satellites invite          open joining for 15 minutes: your other machines connect by themselves
                                       (--minutes N, --close, --token to print the join token instead)
    orbital satellites remove <id>     forget a machine (it can join again later)
    orbital satellites discover        find hubs on this Tailscale network

`list` and `remove` talk to the hub with the automation token. `invite` reads the hub's own
secrets, so it runs on the hub. `discover` probes Tailscale peers for a running hub.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

NAME = "satellites"
HELP = "Machines joined to this hub: list | invite | remove <id> | discover"


def register(p):
    p.add_argument("action", nargs="?", default="list", choices=["list", "invite", "remove", "discover"])
    p.add_argument("id", nargs="?", help="satellite id or machine name (remove)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--minutes", type=float, default=15, help="invite: how long joining stays open (default 15)")
    p.add_argument("--close", action="store_true", help="invite: close joining now")
    p.add_argument("--token", action="store_true", help="invite: print the join token and addresses instead")


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
        metrics_str = ""
        sm = row.get("system_metrics")
        if isinstance(sm, dict) and row.get("status") == "online":
            parts = []
            load = sm.get("load")
            if isinstance(load, list) and load:
                parts.append(f"load {load[0]:.1f}")
            mem = sm.get("memory")
            if isinstance(mem, dict) and mem.get("percent") is not None:
                parts.append(f"mem {mem['percent']:.0f}%")
            disks = sm.get("disks")
            if isinstance(disks, list) and disks:
                parts.append(f"disk {disks[0].get('percent', 0):.0f}%")
            if parts:
                metrics_str = f"  ({', '.join(parts)})"
        print(f"{mark} {(row.get('name') or row['id']).ljust(width)}  {row.get('status'):7}  {row.get('ip') or row.get('host') or '-':15}  "
              f"seen {_ago(row.get('last_seen'))}{running}  [{agents}{'; ' + ', '.join(extras) if extras else ''}]{metrics_str}  id {row['id']}")
    return 0


def _invite(args) -> int:
    if getattr(args, "token", False):
        return _invite_token(args)
    from orbital.feeds import HubClient, HubError

    try:
        with HubClient.from_config(startup_wait=0) as hub:
            if args.close:
                status = hub.request("DELETE", "/v1/satellites/invite")
            else:
                status = hub.request("POST", "/v1/satellites/invite", json_body={"minutes": args.minutes, "by": "cli"})
    except HubError as exc:
        print(f"orbital satellites invite: {exc.message}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(status, indent=2))
        return 0
    if not status.get("open"):
        print("Joining is closed. Machines that aren't joined yet wait until you open it again.")
        return 0
    minutes = round(status.get("seconds_left", 0) / 60)
    print(f"Joining is open for {minutes} minutes.")
    print("Any machine on your Tailscale account running the satellite connects by itself (within ~20 s).")
    print("New machine: clone the repo and run ./setup (Windows: setup.cmd), pick satellite; no token needed.")
    print("Watch them arrive: orbital satellites · close early: orbital satellites invite --close")
    return 0


def _invite_token(args) -> int:
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
    urls: list[str] = []
    external = cfg.external_hub_url()
    if external and not external.startswith("http://127.") and "://localhost" not in external:
        urls.append(external)
    me = tailscale_self()
    if cfg.network_mode == "tailscale":
        if me and me.dns:
            urls.append(f"http://{me.dns}:{port}")
        if me and me.ip:
            urls.append(f"http://{me.ip}:{port}")
        urls.append(f"http://{cfg.machine_name}:{port}")
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    urls = deduped
    if not urls:
        print("orbital satellites invite: no external hub URL yet "
              "(set network.public_url for tunnel mode, or start Tailscale).", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"join_token": token, "hub_urls": urls, "network_mode": cfg.network_mode}, indent=2))
        return 0
    print("To add a machine, run ./setup on it and choose satellite, or:")
    print(f"  orbital satellite join {urls[0]} {token}")
    print()
    print(f"Join token: {token}")
    print("Hub address" + ("es" if len(urls) > 1 else "") + ":")
    for url in urls:
        print(f"  {url}")
    if cfg.network_mode == "tunnel":
        print("(Tunnel mode: satellites and the phone use the HTTPS URL above.)")
    elif not me:
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


def _discover(args) -> int:
    from orbital.core.net import discover_hubs

    print("Scanning Tailscale peers for Orbital hubs…")
    hubs = discover_hubs()
    if args.json:
        print(json.dumps(hubs, indent=2))
        return 0
    if not hubs:
        print("No hubs found. Make sure the hub is running and this machine is on the same Tailscale network.")
        return 1
    for h in hubs:
        self_mark = " (this machine)" if h.get("is_self") else ""
        print(f"  ● {h['name']}{self_mark}  {h['url']}  hub_id={h.get('hub_id', '?')}")
    return 0


def run(args) -> int:
    return {"list": _list, "invite": _invite, "remove": _remove, "discover": _discover}[args.action](args)
