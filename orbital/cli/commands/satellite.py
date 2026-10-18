"""`orbital satellite`: this machine's satellite service.

    orbital satellite serve                      run it in the foreground (the service does this)
    orbital satellite status                     health, installed agents, running jobs, hub link
    orbital satellite jobs                       recent agent jobs on this machine
    orbital satellite run <agent> <prompt…>      start an agent here [--cwd DIR] [--title T] [--wait]
    orbital satellite find                       hubs on your tailnet
    orbital satellite join <hub_url> <token>     join a hub (the setup wizard does this for you)
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from orbital.core.config import load_config
from orbital.core.secrets import Secrets
from orbital.satellite.agents import configured_agents
from orbital.satellite.join import JoinError, join_hub, read_identity
from orbital.satellite.util import read_json

NAME = "satellite"
HELP = "This machine's satellite: serve | status | jobs | run <agent> <prompt> | find | join <hub_url> <token>"


def register(p):
    p.add_argument("action", nargs="?", default="status", choices=["serve", "status", "jobs", "run", "find", "join"])
    p.add_argument("args", nargs="*", help="join: <hub_url> <token>; run: <agent> <prompt…>")
    p.add_argument("--port", type=int, help="serve: override satellite.port")
    p.add_argument("--no-heartbeat", action="store_true", help="serve: do not talk to the hub")
    p.add_argument("--cwd", help="run: folder to run in (inside satellite.workdirs)")
    p.add_argument("--title", help="run: title shown in the tracker")
    p.add_argument("--wait", action="store_true", help="run: wait and print the output")
    p.add_argument("--no-track", action="store_true", help="run: do not report to the tracker")
    p.add_argument("--keep-role", action="store_true", help="join: do not switch this machine to the satellite role")
    p.add_argument("--json", action="store_true", help="print JSON")


def _local(cfg) -> tuple[str, dict]:
    port = int(cfg.get("satellite.port", 7704))
    token = Secrets(cfg.paths).get("ORBITAL_SATELLITE_TOKEN")
    return f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {token}"} if token else {}


def status(cfg, as_json: bool) -> int:
    base, headers = _local(cfg)
    out: dict = {"machine": cfg.machine_name, "url": base, "up": False}
    try:
        health = httpx.get(f"{base}/v1/health", timeout=2).json()
        out["up"] = health.get("service") == "orbital-satellite"
        out["health"] = health
    except (httpx.HTTPError, ValueError) as exc:
        out["error"] = exc.__class__.__name__
    out["agents"] = [a.public() for a in configured_agents(cfg).values()]
    out["workdirs"] = cfg.get("satellite.workdirs")
    identity = read_identity(cfg)
    out["hub"] = {"url": cfg.hub_url() or None, "satellite_id": identity.get("satellite_id"),
                  "joined_at": identity.get("joined_at"),
                  "last_heartbeat": read_json(cfg.paths.satellite_data / "heartbeat.json", None)}
    if out["up"]:
        try:
            out["jobs"] = httpx.get(f"{base}/v1/agents/jobs?limit=5", headers=headers, timeout=3).json().get("jobs")
        except (httpx.HTTPError, ValueError):
            out["jobs"] = None
    if as_json:
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["up"] else 1
    print(f"Satellite on {cfg.machine_name}: {'up' if out['up'] else 'DOWN'} at {base}"
          + ("" if out["up"] else f" ({out.get('error')}; start it with `orbital satellite serve`)"))
    if out["up"]:
        h = out["health"]
        print(f"  running {h.get('running', 0)}, queued {h.get('queued', 0)}, version {h.get('version')}")
    print("Agents")
    for agent in sorted(out["agents"], key=lambda a: a["name"]):
        print(f"  {agent['name']:<10} {'installed' if agent['available'] else 'not found':<10} {agent['path'] or agent['executable']}")
    print(f"Work folders  {', '.join(str(w) for w in out['workdirs'] or [])}")
    hub = out["hub"]
    if hub["satellite_id"]:
        beat = hub["last_heartbeat"] or {}
        state = "ok" if beat.get("ok") else (beat.get("detail") or "no heartbeat yet")
        print(f"Hub           {hub['url']} as {hub['satellite_id']} (last heartbeat {beat.get('at') or '-'}: {state})")
    else:
        print(f"Hub           not joined{' (' + hub['url'] + ')' if hub['url'] else ''}")
    for job in out.get("jobs") or []:
        print(f"  {job['id']}  {job['agent']:<8} {job['status']:<9} {job.get('title', '')[:50]}")
    return 0 if out["up"] else 1


def jobs(cfg, as_json: bool) -> int:
    base, headers = _local(cfg)
    try:
        rows = httpx.get(f"{base}/v1/agents/jobs?limit=30", headers=headers, timeout=3).json().get("jobs") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        print(f"satellite not answering at {base}: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no agent jobs yet")
    for job in rows:
        print(f"{job['id']}  {job['agent']:<8} {job['status']:<9} {str(job.get('created_at')):<20} {job.get('title', '')[:50]}")
    return 0


def run_agent(cfg, args) -> int:
    if len(args.args) < 2:
        print("usage: orbital satellite run <agent> <prompt…>", file=sys.stderr)
        return 2
    base, headers = _local(cfg)
    body = {"agent": args.args[0], "prompt": " ".join(args.args[1:]), "cwd": args.cwd, "title": args.title,
            "track": not args.no_track, "by": "cli"}
    try:
        response = httpx.post(f"{base}/v1/agents/run", json=body, headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        print(f"satellite not answering at {base}: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    if response.status_code != 200:
        print(f"refused ({response.status_code}): {response.json().get('detail')}", file=sys.stderr)
        return 1
    job_id = response.json()["job_id"]
    print(f"started {job_id}")
    if not args.wait:
        return 0
    while True:
        time.sleep(1)
        job = httpx.get(f"{base}/v1/agents/jobs/{job_id}", headers=headers, timeout=5).json()
        if job.get("status") in ("done", "failed", "cancelled"):
            log = httpx.get(f"{base}/v1/agents/jobs/{job_id}/log", headers=headers, timeout=10).json().get("log", "")
            print(log, end="" if log.endswith("\n") else "\n")
            print(f"{job_id}: {job['status']}" + (f" ({job.get('error')})" if job.get("error") else ""))
            return 0 if job["status"] == "done" else 1


def find(cfg, as_json: bool) -> int:
    from orbital.satellite.discover import find_hubs

    hubs = find_hubs(int(cfg.get("hub.port", 7700)))
    if as_json:
        print(json.dumps(hubs, indent=2))
        return 0
    if not hubs:
        print("no hub answered on your tailnet (is Tailscale up, and the hub running?)")
        return 1
    for hub in hubs:
        print(f"{hub['url']:<45} {hub.get('name') or ''} ({hub.get('machine')}, {hub.get('version')})")
    return 0


def join(cfg, args) -> int:
    if len(args.args) != 2:
        print("usage: orbital satellite join <hub_url> <join_token>", file=sys.stderr)
        return 2
    hub_url, token = args.args
    keep = args.keep_role or cfg.role == "hub"
    try:
        info = join_hub(hub_url, token, cfg, write_config=not keep, local=hub_url.startswith(("http://127.", "http://localhost")))
    except JoinError as exc:
        print(f"join failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"joined {info['hub_url']} as {info['satellite_id']}")
    print(f"agents offered: {', '.join(info['capabilities']['agents']) or 'none installed'}")
    if info.get("browser_token_changed") and cfg.get("browser.enabled"):
        # The bridge reads its token at start, so it must restart to accept the fleet's token.
        from orbital import services
        try:
            done = [o for o in services.restart(cfg, ["browser-bridge"]) if o.ok]
        except Exception:  # noqa: BLE001 (no service manager here)
            done = []
        if done:
            print("browser bridge restarted with the fleet's browser token")
        else:
            print("restart the browser bridge (if it is running) so it uses the fleet's browser token", file=sys.stderr)
    if keep:
        print("config.toml left as it was (this machine keeps its role)")
    else:
        print("config.toml now says role = satellite and hub.url; a running satellite service picks that up at its next heartbeat")
    return 0


def run(args) -> int:
    cfg = load_config()
    if args.action == "serve":
        from orbital.satellite.server import serve
        return serve(cfg, port=args.port, heartbeat=not args.no_heartbeat)
    if args.action == "status":
        return status(cfg, args.json)
    if args.action == "jobs":
        return jobs(cfg, args.json)
    if args.action == "run":
        return run_agent(cfg, args)
    if args.action == "find":
        return find(cfg, args.json)
    if args.action == "join":
        return join(cfg, args)
    return 2
