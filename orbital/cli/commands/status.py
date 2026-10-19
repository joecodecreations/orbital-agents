"""`orbital status`: one screen with this machine's role, the hub, every local service, and recent problems."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime

import httpx

from orbital import __version__
from orbital.core.config import load_config
from orbital.core.secrets import Secrets
from orbital.satellite.join import read_identity
from orbital.satellite.util import parse_iso, read_json
from orbital.scheduler.watchdog import checks_for, probe

NAME = "status"
HELP = "One screen: role, machine, hub, each local service up/down, satellites, recent errors"


def register(p):
    p.add_argument("--json", action="store_true", help="print the same picture as JSON")


def _hub_info(url: str) -> tuple[bool, dict | str]:
    try:
        response = httpx.get(f"{url}/v1/hub/info", timeout=2.0)
    except httpx.HTTPError as exc:
        return False, exc.__class__.__name__
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    try:
        return True, response.json()
    except ValueError:
        return False, "not JSON"


def _satellites(url: str, token: str) -> dict | None:
    if not token:
        return None
    try:
        response = httpx.get(f"{url}/v1/satellites", headers={"Authorization": f"Bearer {token}"}, timeout=3.0)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    rows = body.get("satellites") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return None
    online = sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "online")
    return {"total": len(rows), "online": online}


def _ago(stamp: str | None) -> str:
    ts = parse_iso(stamp)
    if ts is None:
        return "never"
    seconds = max(0, int(time.time() - ts))
    if seconds < 120:
        return f"{seconds} s ago"
    if seconds < 7200:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def gather() -> dict:
    cfg = load_config()
    secrets = Secrets(cfg.paths)
    hub_url = cfg.hub_url()
    external = cfg.external_hub_url()
    picture: dict = {
        "version": __version__,
        "machine": cfg.machine_name,
        "role": cfg.role,
        "network_mode": cfg.network_mode,
        "home": str(cfg.paths.home),
        "hub": {"url": hub_url or None, "external_url": external or None, "reachable": False},
        "services": [],
        "satellites": None,
        "watchdog": None,
        "problems": [],
    }
    if hub_url:
        ok, info = _hub_info(hub_url)
        picture["hub"]["reachable"] = ok
        picture["hub"]["detail"] = info if ok else str(info)
        if ok and cfg.role == "hub":
            picture["satellites"] = _satellites(hub_url, secrets.get("ORBITAL_AUTOMATION_TOKEN"))
    identity = read_identity(cfg)
    if identity:
        picture["hub"]["satellite_id"] = identity.get("satellite_id")
        beat = read_json(cfg.paths.satellite_data / "heartbeat.json", {}) or {}
        picture["hub"]["last_heartbeat"] = {"at": beat.get("at"), "ok": beat.get("ok"), "detail": beat.get("detail")}
    watchdog_state = read_json(cfg.paths.run / "watchdog.json", {}) or {}
    for check in checks_for(cfg):
        healthy, detail = probe(check, timeout=1.5, tries=1)
        row = {"name": check.name, "port": check.port, "up": healthy, "detail": detail}
        record = (watchdog_state.get("services") or {}).get(check.name) or {}
        if record.get("status") == "not installed" and not healthy:
            row["detail"] = "not installed"
        if check.name == "satellite" and healthy:
            try:
                row["agents"] = httpx.get(check.target, timeout=1.5).json().get("agents")
            except (httpx.HTTPError, ValueError):
                pass
        if check.name == "scheduler":
            snap = (read_json(cfg.paths.scheduler_data / "state.json", {}) or {}).get("snapshot") or {}
            row["jobs"] = len(snap.get("jobs") or [])
        picture["services"].append(row)
    if watchdog_state:
        restarts = sum(len([t for t in (r.get("restarts") or []) if time.time() - t < 86400])
                       for r in (watchdog_state.get("services") or {}).values() if isinstance(r, dict))
        picture["watchdog"] = {"checked_at": watchdog_state.get("checked_at"), "restarts_24h": restarts}
    try:
        from orbital.cli.commands.logs import recent_problems
        for entry in recent_problems(cfg.paths.logs, limit=5):
            picture["problems"].append({"ts": entry.get("ts"), "component": entry.get("component"),
                                        "level": entry.get("level"), "msg": entry.get("msg")})
    except Exception:  # noqa: BLE001 - status must always print
        pass
    return picture


def run(args) -> int:
    picture = gather()
    if args.json:
        print(json.dumps(picture, indent=2, default=str))
        return 0
    tty = sys.stdout.isatty()
    green, red, dim, reset = ("\033[32m", "\033[31m", "\033[2m", "\033[0m") if tty else ("", "", "", "")
    print(f"Orbital Agents {picture['version']} · {picture['machine']} · role {picture['role']} · {picture.get('network_mode', 'tailscale')}")
    print(f"{dim}home {picture['home']}{reset}")
    hub = picture["hub"]
    if hub["url"]:
        state = f"{green}reachable{reset}" if hub["reachable"] else f"{red}unreachable{reset} ({hub.get('detail')})"
        print(f"Hub        {hub['url']}  {state}")
        if hub.get("external_url") and hub["external_url"] != hub["url"]:
            print(f"External   {hub['external_url']}")
    else:
        print(f"Hub        {red}not configured{reset} (hub.url is empty; run `orbital satellite join`)")
        if hub.get("external_url"):
            print(f"External   {hub['external_url']}")
    if hub.get("last_heartbeat"):
        beat = hub["last_heartbeat"]
        ok = f"{green}ok{reset}" if beat.get("ok") else f"{red}{beat.get('detail') or 'failed'}{reset}"
        print(f"Heartbeat  {_ago(beat.get('at'))}, {ok}")
    print("Services")
    for row in picture["services"]:
        port = f":{row['port']}" if row.get("port") else ""
        mark = f"{green}up  {reset}" if row["up"] else f"{red}down{reset}"
        extra = row["detail"]
        if row.get("agents") is not None:
            extra += f", agents: {', '.join(row['agents']) or 'none'}"
        if row.get("jobs") is not None:
            extra += f", {row['jobs']} feeds"
        print(f"  {row['name']:<15} {port:<7} {mark}  {dim}{extra}{reset}")
    if picture["satellites"] is not None:
        sats = picture["satellites"]
        print(f"Satellites {sats['total']} joined, {sats['online']} online")
    if picture["watchdog"]:
        wd = picture["watchdog"]
        print(f"Watchdog   last pass {_ago(wd.get('checked_at'))}, {wd['restarts_24h']} restarts in 24 h")
    if picture["problems"]:
        print("Recent problems (24 h)")
        for p in picture["problems"]:
            ts = parse_iso((p.get("ts") or "")[:19] + "Z") if p.get("ts") else None
            when = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "     -     "
            print(f"  {when}  {str(p.get('component')):<14} {str(p.get('level')):<7} {p.get('msg')}")
    return 0
