"""Satellites: the machines that run agents for the hub, and the relay to them.

A satellite joins once with the join token (X-Orbital-Join-Token = ORBITAL_JOIN_TOKEN) and sends
its own satellite token, which the hub uses for every call back to it. The hub answers with a
hub_token the satellite uses from then on (heartbeat, tracker relay). It is stored hashed.
A satellite that misses 3 heartbeats is offline. Joining again from the same machine keeps its id.

Everything the apps and the CLI do on another machine goes through here: start an agent, read a
job and its log, cancel it, and drive that machine's Chrome through its browser bridge.

Who may call what:
  join                      X-Orbital-Join-Token (ORBITAL_JOIN_TOKEN), once per machine
  heartbeat                 that satellite's hub_token only
  list, ping, agent relay,  an app session, the automation token, or any joined satellite's hub_token,
  jobs, browser relay       so `orbital agents` and `orbital browser` work from every machine
  remove                    an app session or the automation token (never a satellite)
A hub_token never reads or writes sections, actions, media, the desk or sync; besides the routes
above it may only post alerts (stamped with its machine) and use the tracker relay and settings.

satellites.json (mode 0600, under data/hub):
  {"satellites": [{id, name, machine, host, ip, port, os, version, capabilities, satellite_token,
                   hub_token_hash, heartbeat_seconds, joined_at, last_seen, running_jobs, jobs_at}]}
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from orbital.core import get_logger
from orbital.core.limits import MAX_PROMPT_CHARS
from orbital.core.net import allowed
from orbital.core.secrets import Secrets
from orbital.hub.auth import digest, same
from orbital.hub.web import body_of, fail

MISSED_BEATS = 3
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
COMMAND = re.compile(r"^[a-z][a-z0-9_.-]{0,40}$")
MAX_JOBS_CACHED = 50
PUBLIC_KEYS = ("id", "name", "machine", "host", "ip", "port", "os", "version", "capabilities", "joined_at", "last_seen", "heartbeat_seconds")


def _iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return out[:48].strip("-") or "machine"


def _url_host(host: str) -> str:
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host


class JoinError(ValueError):
    pass


def _capabilities(raw: object) -> dict:
    caps = raw if isinstance(raw, dict) else {}
    agents = caps.get("agents") or []
    if not isinstance(agents, list) or len(agents) > 40 or any(not isinstance(a, str) or not a or len(a) > 40 for a in agents):
        raise JoinError("capabilities.agents must be a list of agent names")
    port = caps.get("browser_port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536):
        raise JoinError("capabilities.browser_port must be a port number")
    return {
        "agents": list(dict.fromkeys(agents)),
        "browser": bool(caps.get("browser")),
        "browser_port": port,
        "tracker": bool(caps.get("tracker")),
    }


class Registry:
    def __init__(self, path: Path, *, heartbeat_seconds: int = 60, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self.clock = clock
        self._lock = threading.RLock()

    # Storage

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = data.get("satellites") if isinstance(data, dict) else None
        return [row for row in rows or [] if isinstance(row, dict) and row.get("id")]

    def _save(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"satellites": rows}, fh, indent=2)
        os.replace(tmp, self.path)

    def all(self) -> list[dict]:
        with self._lock:
            return self._load()

    # Join and heartbeat

    def join(self, body: dict, client_ip: str | None, networks: list[str]) -> tuple[dict, str]:
        """Add or refresh one machine. Returns (record, hub_token). Raises JoinError on a bad body."""
        machine = str(body.get("machine") or body.get("name") or "").strip()
        if not machine or len(machine) > 64:
            raise JoinError("machine is required (at most 64 characters)")
        name = str(body.get("name") or machine).strip()[:64]
        token = body.get("satellite_token")
        if not isinstance(token, str) or len(token) < 16 or len(token) > 256:
            raise JoinError("satellite_token is required (16–256 characters)")
        port = body.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise JoinError("port must be the satellite service's port number")
        # Where the hub calls back: the address it reported, else the one this request came from.
        # Either way it has to be inside the allow list, so a join can't point the relay elsewhere.
        ip = str(body.get("ip") or client_ip or "").strip()
        if not allowed(ip, networks):
            raise JoinError(f"ip {ip or '(none)'} is not inside hub.allow_networks")
        record_in = {
            "name": name,
            "machine": machine,
            "host": str(body.get("host") or "")[:255],
            "ip": ip,
            "port": port,
            "os": str(body.get("os") or "")[:40],
            "version": str(body.get("version") or "")[:40],
            "capabilities": _capabilities(body.get("capabilities")),
            "heartbeat_seconds": _beat(body.get("heartbeat_seconds"), self.heartbeat_seconds),
        }
        hub_token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            rows = self._load()
            existing = next((row for row in rows if row.get("machine", "").lower() == machine.lower()), None)
            if existing is None:
                ident = _slug(machine)
                taken = {row["id"] for row in rows}
                base, n = ident, 2
                while ident in taken:
                    ident, n = f"{base}-{n}", n + 1
                existing = {"id": ident, "joined_at": _iso(now), "running_jobs": []}
                rows.append(existing)
            existing.update(record_in)
            existing["satellite_token"] = token
            existing["hub_token_hash"] = digest(hub_token)
            existing["last_seen"] = now
            self._save(rows)
            return dict(existing), hub_token

    def by_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        want = digest(token)
        for row in self.all():
            if same(row.get("hub_token_hash"), want):
                return row
        return None

    def heartbeat(self, token: str | None, body: dict) -> dict | None:
        """A beat from the satellite holding `token`. None when the token is unknown (removed: join again)."""
        if not token:
            return None
        want = digest(token)
        with self._lock:
            rows = self._load()
            row = next((r for r in rows if same(r.get("hub_token_hash"), want)), None)
            if row is None:
                return None
            claimed = body.get("satellite_id")
            if claimed and claimed != row["id"]:
                return None
            row["last_seen"] = self.clock()
            if "capabilities" in body:
                try:
                    row["capabilities"] = _capabilities(body.get("capabilities"))
                except JoinError:
                    pass
            jobs = body.get("running_jobs")
            if isinstance(jobs, list):
                row["running_jobs"] = [job for job in jobs if isinstance(job, dict)][:MAX_JOBS_CACHED]
                row["jobs_at"] = row["last_seen"]
            if body.get("version"):
                row["version"] = str(body["version"])[:40]
            if body.get("heartbeat_seconds") is not None:
                row["heartbeat_seconds"] = _beat(body.get("heartbeat_seconds"), row.get("heartbeat_seconds") or self.heartbeat_seconds)
            self._save(rows)
            return dict(row)

    def remember_jobs(self, ident: str, jobs: list[dict]) -> None:
        with self._lock:
            rows = self._load()
            row = next((r for r in rows if r["id"] == ident), None)
            if row is None:
                return
            row["running_jobs"] = [job for job in jobs if isinstance(job, dict)][:MAX_JOBS_CACHED]
            row["jobs_at"] = self.clock()
            self._save(rows)

    # Reading

    def get(self, ident: str) -> dict | None:
        """By id, then by machine or display name (case-insensitive), so `--on my-laptop` works."""
        rows = self.all()
        found = next((row for row in rows if row["id"] == ident), None)
        if found is None:
            low = (ident or "").lower()
            found = next((row for row in rows if low in (str(row.get("machine", "")).lower(), str(row.get("name", "")).lower())), None)
        return found

    def remove(self, ident: str) -> dict | None:
        with self._lock:
            rows = self._load()
            row = self.get(ident)
            if row is None:
                return None
            self._save([r for r in rows if r["id"] != row["id"]])
            return row

    def status(self, row: dict) -> str:
        beat = int(row.get("heartbeat_seconds") or self.heartbeat_seconds)
        last = float(row.get("last_seen") or 0)
        return "online" if last and self.clock() - last <= MISSED_BEATS * beat else "offline"

    def public(self, row: dict) -> dict:
        out = {key: row.get(key) for key in PUBLIC_KEYS}
        out["last_seen"] = _iso(row.get("last_seen"))
        out["status"] = self.status(row)
        out["running_jobs"] = len([j for j in row.get("running_jobs") or [] if j.get("status") in ("queued", "running")])
        return out

    def listing(self) -> list[dict]:
        rows = [self.public(row) for row in self.all()]
        rows.sort(key=lambda r: (r["status"] != "online", r["name"].lower()))
        return rows

    @staticmethod
    def base_url(row: dict) -> str:
        return f"http://{_url_host(row.get('ip') or row.get('host') or '127.0.0.1')}:{int(row['port'])}"

    @staticmethod
    def browser_url(row: dict) -> str | None:
        port = (row.get("capabilities") or {}).get("browser_port")
        if not port:
            return None
        return f"http://{_url_host(row.get('ip') or row.get('host') or '127.0.0.1')}:{int(port)}"


def _beat(raw: object, default: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return int(default)
    return max(1, min(int(raw), 3600))


class RelayError(Exception):
    def __init__(self, status: int, message: str, body: Any = None, upstream: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.upstream = upstream  # the status the machine answered, when it answered


async def call(url: str, method: str, path: str, token: str | None, *, json_body: Any = None, timeout: float = 10.0) -> Any:
    """One request to a satellite or a browser bridge. Returns the JSON answer; raises RelayError."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # A client per call: requests can come from different event loops (tests, threads).
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 4.0))) as client:
            response = await client.request(method, url + path, headers=headers, json=json_body)
    except httpx.HTTPError as exc:
        raise RelayError(502, f"no answer from {url}: {exc.__class__.__name__}") from exc
    try:
        body = response.json() if response.content else {}
    except (json.JSONDecodeError, ValueError):
        body = {"error": response.text[:500]}
    message = (body.get("error") or body.get("detail")) if isinstance(body, dict) else None
    if response.status_code == 401:
        # Never pass a satellite's 401 to the app: it would look like the app's own sign-in failed.
        raise RelayError(502, "the machine refused the hub's token; join it again (orbital satellites invite)", body, 401)
    if response.status_code == 403:
        # A folder outside satellite.workdirs, or the hub outside the machine's allow list: the request's fault.
        raise RelayError(400, str(message or "the machine refused the request"), body, 403)
    if response.status_code >= 400:
        raise RelayError(response.status_code, str(message or f"HTTP {response.status_code}"), body, response.status_code)
    return body


def _jobs_of(answer: Any) -> list[dict]:
    rows = answer.get("jobs") if isinstance(answer, dict) else answer
    return [row for row in rows or [] if isinstance(row, dict)] if isinstance(rows, list) else []


def add_routes(app, hub) -> None:
    registry: Registry = hub.satellites
    log = lambda: get_logger("hub")  # noqa: E731

    def relay_fail(exc: RelayError) -> JSONResponse:
        if exc.status >= 500:
            return fail(str(exc), exc.status, code="unavailable")
        out = fail(str(exc), exc.status)
        if isinstance(exc.body, dict):
            body = json.loads(out.body)
            body["satellite"] = exc.body
            return JSONResponse(body, status_code=exc.status)
        return out

    def find(ident: str) -> dict | JSONResponse:
        row = registry.get(ident)
        if row is None:
            return fail(f"no satellite {ident}; GET /v1/satellites lists them", 404)
        return row

    @app.post("/v1/satellites/join")
    async def satellites_join(request: Request) -> JSONResponse:
        if not hub.auth.join_token_ok(request.headers.get("x-orbital-join-token")):
            log().warning("satellite join refused: bad join token", extra={"fields": {"ip": hub.client_ip(request)}})
            return fail("bad or missing X-Orbital-Join-Token", 401, code="unauthorized")
        body = await body_of(request)
        try:
            row, hub_token = registry.join(body, hub.client_ip(request), hub.networks)
        except JoinError as exc:
            return fail(str(exc), 400)
        log().info("satellite joined", extra={"fields": {"satellite": row["id"], "machine": row["machine"], "ip": row["ip"]}})
        await hub.live.broadcast({"type": "satellites", "satellites": registry.listing()})
        # One browser token for the whole fleet: the satellite's bridge takes the hub's, so the
        # browser relay (which calls every bridge with the hub's token) works after a normal setup.
        browser_token = hub.auth.get("ORBITAL_BROWSER_TOKEN") or Secrets(hub.paths).ensure_token("ORBITAL_BROWSER_TOKEN")
        return JSONResponse({"ok": True, "satellite_id": row["id"], "hub_token": hub_token, "hub_id": hub.hub_id,
                             "heartbeat_seconds": row["heartbeat_seconds"], "browser_token": browser_token})

    @app.post("/v1/satellites/heartbeat")
    async def satellites_heartbeat(request: Request) -> JSONResponse:
        body = await body_of(request)
        before = registry.by_token(hub.token(request))
        was = registry.status(before) if before else None
        row = registry.heartbeat(hub.token(request), body)
        if row is None:
            return fail("unknown satellite: join again", 401, code="unauthorized")
        if was != "online":
            log().info("satellite online", extra={"fields": {"satellite": row["id"]}})
            await hub.live.broadcast({"type": "satellites", "satellites": registry.listing()})
        return JSONResponse({"ok": True, "satellite_id": row["id"], "hub_id": hub.hub_id})

    @app.get("/v1/satellites")
    async def satellites_list(request: Request) -> JSONResponse:
        denied = hub.require_machine(request)
        if denied:
            return denied
        return JSONResponse({"satellites": registry.listing(), "hub_id": hub.hub_id})

    @app.get("/v1/satellites/{ident}")
    async def satellites_one(ident: str, request: Request) -> JSONResponse:
        denied = hub.require_machine(request)
        if denied:
            return denied
        row = find(ident)
        if isinstance(row, JSONResponse):
            return row
        return JSONResponse(registry.public(row))

    @app.delete("/v1/satellites/{ident}")
    async def satellites_remove(ident: str, request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        row = registry.remove(ident)
        if row is None:
            return fail(f"no satellite {ident}", 404)
        log().info("satellite removed", extra={"fields": {"satellite": row["id"]}})
        await hub.live.broadcast({"type": "satellites", "satellites": registry.listing()})
        return JSONResponse({"ok": True, "id": row["id"]})

    @app.get("/v1/satellites/{ident}/ping")
    async def satellites_ping(ident: str, request: Request) -> JSONResponse:
        denied = hub.require_machine(request)
        if denied:
            return denied
        row = find(ident)
        if isinstance(row, JSONResponse):
            return row
        started = time.monotonic()
        try:
            answer = await call(registry.base_url(row), "GET", "/v1/health", None, timeout=4)
        except RelayError as exc:
            return relay_fail(exc)
        return JSONResponse({"ok": True, "satellite_id": row["id"], "ms": int((time.monotonic() - started) * 1000), "health": answer})

    @app.post("/v1/satellites/{ident}/agents/run")
    async def satellites_run(ident: str, request: Request) -> JSONResponse:
        denied = hub.require_machine(request)
        if denied:
            return denied
        row = find(ident)
        if isinstance(row, JSONResponse):
            return row
        body = await body_of(request)
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return fail("prompt is required", 400)
        if len(prompt) > MAX_PROMPT_CHARS:
            return fail(f"prompt is longer than {MAX_PROMPT_CHARS:,} characters", 413)
        # No agent named: the machine uses its own satellite.default_agent.
        agent = str(body["agent"]) if body.get("agent") else None
        offered = (row.get("capabilities") or {}).get("agents") or []
        if agent and offered and agent not in offered:
            return fail(f"agent {agent} isn't available on {row['name']}; it has {', '.join(offered)}", 400)
        forward: dict = {"prompt": prompt}
        if agent:
            forward["agent"] = agent
        for key in ("cwd", "title"):
            if isinstance(body.get(key), str) and body[key].strip():
                forward[key] = body[key].strip()[:1000]
        if isinstance(body.get("track"), bool):
            forward["track"] = body["track"]
        forward["by"] = str(body.get("by") or "hub")[:40]
        try:
            answer = await call(registry.base_url(row), "POST", "/v1/agents/run", row.get("satellite_token"), json_body=forward, timeout=15)
        except RelayError as exc:
            return relay_fail(exc)
        job_id = answer.get("job_id") if isinstance(answer, dict) else None
        log().info("agent started", extra={"fields": {"satellite": row["id"], "agent": agent, "job": job_id}})
        result = {**(answer if isinstance(answer, dict) else {}), "job_id": job_id, "satellite_id": row["id"], "machine": row["machine"]}
        await hub.live.broadcast({"type": "agent_job", "job": {"id": job_id, "satellite_id": row["id"], "agent": result.get("agent") or agent,
                                                               "title": forward.get("title"), "status": result.get("status", "queued")}})
        return JSONResponse(result)

    @app.get("/v1/satellites/{ident}/agents/jobs")
    async def satellites_jobs(ident: str, request: Request) -> JSONResponse:
        denied = hub.require_machine(request)
        if denied:
            return denied
        row = find(ident)
        if isinstance(row, JSONResponse):
            return row
        try:
            answer = await call(registry.base_url(row), "GET", "/v1/agents/jobs", row.get("satellite_token"), timeout=6)
        except RelayError as exc:
            return relay_fail(exc)
        jobs = _jobs_of(answer)
        registry.remember_jobs(row["id"], jobs)
        return JSONResponse({"jobs": [{**job, "satellite_id": row["id"], "machine": row["machine"]} for job in jobs]})

    async def job_call(ident: str, job_id: str, method: str, suffix: str, request: Request) -> JSONResponse:
        denied = hub.require_machine(request)
        if denied:
            return denied
        if not JOB_ID.match(job_id):
            return fail("invalid job id", 400)
        row = find(ident)
        if isinstance(row, JSONResponse):
            return row
        try:
            answer = await call(registry.base_url(row), method, f"/v1/agents/jobs/{job_id}{suffix}", row.get("satellite_token"), timeout=8)
        except RelayError as exc:
            return relay_fail(exc)
        out = answer if isinstance(answer, dict) else {"result": answer}
        return JSONResponse({**out, "satellite_id": row["id"]})

    @app.get("/v1/satellites/{ident}/agents/jobs/{job_id}")
    async def satellites_job(ident: str, job_id: str, request: Request) -> JSONResponse:
        return await job_call(ident, job_id, "GET", "", request)

    @app.get("/v1/satellites/{ident}/agents/jobs/{job_id}/log")
    async def satellites_job_log(ident: str, job_id: str, request: Request, format: str = "json"):  # noqa: A002
        """{job_id, status, log, bytes, truncated}, or the log alone with ?format=text."""
        answer = await job_call(ident, job_id, "GET", "/log", request)
        if format != "text" or answer.status_code != 200:
            return answer
        return PlainTextResponse(str(json.loads(answer.body).get("log") or ""))

    @app.post("/v1/satellites/{ident}/agents/jobs/{job_id}/cancel")
    async def satellites_job_cancel(ident: str, job_id: str, request: Request) -> JSONResponse:
        return await job_call(ident, job_id, "POST", "/cancel", request)

    @app.get("/v1/agents/jobs")
    async def agents_jobs(request: Request) -> JSONResponse:
        """Every satellite's jobs: read live where the machine answers, from its last heartbeat where not."""
        denied = hub.require_machine(request)
        if denied:
            return denied
        rows = registry.all()

        async def one(row: dict) -> tuple[dict, list[dict], str]:
            if registry.status(row) == "online":
                try:
                    answer = await call(registry.base_url(row), "GET", "/v1/agents/jobs", row.get("satellite_token"), timeout=3)
                    jobs = _jobs_of(answer)
                    registry.remember_jobs(row["id"], jobs)
                    return row, jobs, "live"
                except RelayError:
                    pass
            cached = row.get("running_jobs") or []
            return row, cached, "cached" if cached else "none"

        results = await asyncio.gather(*(one(row) for row in rows))
        jobs: list[dict] = []
        sources = []
        for row, rows_jobs, source in results:
            sources.append({"id": row["id"], "machine": row["machine"], "status": registry.status(row), "source": source})
            jobs.extend({**job, "satellite_id": row["id"], "machine": row["machine"]} for job in rows_jobs)
        jobs.sort(key=lambda job: str(job.get("started_at") or job.get("created_at") or ""), reverse=True)
        return JSONResponse({"jobs": jobs, "satellites": sources})

    @app.post("/v1/satellites/{ident}/browser/{command}")
    async def satellites_browser(ident: str, command: str, request: Request) -> JSONResponse:
        """Drive that machine's Chrome: POST <bridge>/command {action, params} with the browser token."""
        denied = hub.require_machine(request)
        if denied:
            return denied
        if not COMMAND.match(command):
            return fail("invalid browser command", 400)
        row = find(ident)
        if isinstance(row, JSONResponse):
            return row
        url = registry.browser_url(row)
        if not (row.get("capabilities") or {}).get("browser") or url is None:
            return fail(f"{row['name']} has no browser bridge open to the hub (browser.bind = \"tailscale\" on that machine)", 400)
        body = await body_of(request)
        # The params come wrapped ({"params": {...}, "timeout"?}) or flat (the browser client's form,
        # with the bridge timeout as "_timeout").
        params = dict(body["params"]) if isinstance(body.get("params"), dict) else dict(body)
        wait = body.get("timeout") if isinstance(body.get("params"), dict) else params.pop("_timeout", None)
        token = hub.auth.get("ORBITAL_BROWSER_TOKEN")
        if not token:
            return fail("ORBITAL_BROWSER_TOKEN is not set in the hub's secrets.env", 503, code="not_configured")
        forward: dict = {"action": command, "params": params}
        timeout = 30.0
        if isinstance(wait, (int, float)) and not isinstance(wait, bool) and wait > 0:
            forward["timeout"] = wait
            timeout = float(wait) + 5
        try:
            answer = await call(url, "POST", "/command", token, json_body=forward, timeout=min(timeout, 125))
        except RelayError as exc:
            if exc.upstream == 401:
                return fail(f"browser token mismatch — run `orbital satellite join` again on {row['machine']}", 502, code="unavailable")
            return relay_fail(exc)
        return JSONResponse(answer if isinstance(answer, dict) else {"result": answer})


async def watch(hub, interval: float = 10.0) -> None:
    """Tell open apps when a satellite goes offline (no beat for 3 periods) or comes back."""
    seen = {row["id"]: hub.satellites.status(row) for row in hub.satellites.all()}
    while True:
        await asyncio.sleep(interval)
        try:
            now = {row["id"]: hub.satellites.status(row) for row in hub.satellites.all()}
            if now != seen:
                for ident, state in now.items():
                    if seen.get(ident) != state:
                        get_logger("hub").info(f"satellite {state}", extra={"fields": {"satellite": ident}})
                seen = now
                await hub.live.broadcast({"type": "satellites", "satellites": hub.satellites.listing()})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let the watcher die
            get_logger("hub").warning("satellite watch error", extra={"fields": {"error": str(exc)[:200]}})
