"""The satellite HTTP service (base `http://<machine>:<satellite.port>`, default port 7704).

    GET  /v1/health                     no auth: {"service": "orbital-satellite", "machine", "version", "agents": [...]}
    GET  /v1/agents                     every configured agent and whether it is installed here
    POST /v1/agents/run                 {agent, prompt, cwd?, title?, track?: true} → {job_id, status}
    GET  /v1/agents/jobs                {"jobs": [...]} newest first (?limit=, ?status=)
    GET  /v1/agents/jobs/{id}           one job, with output_tail
    GET  /v1/agents/jobs/{id}/log       {"job_id", "status", "log", "bytes", "truncated"} (?format=text for plain text)
    POST /v1/agents/jobs/{id}/cancel    stop a running job or drop a queued one

Everything but /v1/health needs `Authorization: Bearer <ORBITAL_SATELLITE_TOKEN>`. Every request,
health included, must come from loopback or Tailscale (`satellite.allow_networks`).

Binding follows `satellite.bind`: "tailscale" listens on this machine's Tailscale IP and on
127.0.0.1; "loopback" on 127.0.0.1 only. When Tailscale is not up yet the service starts on
loopback and rebinds by itself once the Tailscale address appears.
"""

from __future__ import annotations

import hmac
import os
import signal
import socket
import sys
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from orbital import __version__
from orbital.core import net
from orbital.core.limits import MAX_PROMPT_CHARS
from orbital.core.config import Config, load_config
from orbital.core.logs import get_logger
from orbital.core.secrets import Secrets
from orbital.satellite.agents import available_agents, configured_agents
from orbital.satellite.jobs import JobError, JobManager
from orbital.satellite.util import atomic_write_json, iso, now, read_json

TOKEN_KEY = "ORBITAL_SATELLITE_TOKEN"
REBIND_CHECK_SECONDS = 30.0


class RunRequest(BaseModel):
    agent: str | None = None
    prompt: str = Field(default="", max_length=MAX_PROMPT_CHARS)
    cwd: str | None = None
    title: str | None = None
    track: bool = True
    by: str | None = None


def allow_networks(cfg: Config) -> list[str]:
    nets = cfg.get("satellite.allow_networks")
    if isinstance(nets, list) and nets:
        return [str(n) for n in nets]
    return list(net.LOOPBACK_NETS + net.TAILSCALE_NETS)


def satellite_token(cfg: Config) -> str:
    store = Secrets(cfg.paths)
    return store.get(TOKEN_KEY) or store.ensure_token(TOKEN_KEY)


def error_response(status: int, message: str, headers: dict | None = None) -> JSONResponse:
    """Errors carry both `error` (what the hub relay reads) and `detail` (FastAPI's usual key)."""
    return JSONResponse({"ok": False, "error": message, "detail": message}, status_code=status, headers=headers)


def create_app(cfg: Config, manager: JobManager, token: str | None = None) -> FastAPI:
    token = token if token is not None else satellite_token(cfg)
    networks = allow_networks(cfg)
    app = FastAPI(title="Orbital Agents satellite", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    app.state.manager = manager

    @app.middleware("http")
    async def guard(request: Request, call_next):
        client = request.client.host if request.client else None
        if not net.allowed(client, networks):
            return error_response(403, "this address may not use the satellite")
        if request.url.path != "/v1/health":
            header = request.headers.get("authorization", "")
            given = header[7:].strip() if header.lower().startswith("bearer ") else ""
            if not token or not given or not hmac.compare_digest(given.encode(), token.encode()):
                return error_response(401, "missing or wrong satellite token", {"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def bad_body(request: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        if where == "prompt" and first.get("type") == "string_too_long":
            # The same answer the hub and the job runner give for a prompt over the shared limit.
            return error_response(413, f"prompt is longer than {MAX_PROMPT_CHARS:,} characters")
        return error_response(422, f"{where or 'body'}: {first.get('msg', 'invalid')}")

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        counts = manager.counts()
        return {
            "ok": True,
            "service": "orbital-satellite",
            "machine": cfg.machine_name,
            "version": __version__,
            "agents": available_agents(cfg),
            "running": counts["running"],
            "queued": counts["queued"],
            "time": iso(now()),
        }

    @app.get("/v1/agents")
    def agents() -> dict[str, Any]:
        return {"machine": cfg.machine_name,
                "default_agent": cfg.get("satellite.default_agent"),
                "workdirs": [str(p) for p in manager.workdirs()],
                "agents": [a.public() for a in sorted(configured_agents(cfg).values(), key=lambda a: a.name)]}

    @app.post("/v1/agents/run")
    def run(body: RunRequest) -> dict[str, Any]:
        try:
            job = manager.submit(agent=body.agent, prompt=body.prompt, cwd=body.cwd, title=body.title,
                                 track=body.track, by=body.by or "api")
        except JobError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        return {"job_id": job["id"], "status": job["status"], "agent": job["agent"], "machine": cfg.machine_name}

    @app.get("/v1/agents/jobs")
    def jobs(limit: int = 100, status: str | None = None) -> dict[str, Any]:
        return {"machine": cfg.machine_name, "jobs": manager.list(limit=min(max(1, limit), 1000), status=status)}

    @app.get("/v1/agents/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        record = manager.get(job_id)
        if record is None:
            raise HTTPException(404, f"no job {job_id}")
        return record

    @app.get("/v1/agents/jobs/{job_id}/log")
    def job_log(job_id: str, format: str = "json"):
        text = manager.log_text(job_id)
        if text is None:
            raise HTTPException(404, f"no job {job_id}")
        if format == "text":
            return PlainTextResponse(text)
        record = manager.get(job_id, with_output=False) or {}
        return {"job_id": job_id, "status": record.get("status"), "log": text,
                "bytes": record.get("output_bytes"), "truncated": record.get("output_truncated")}

    @app.post("/v1/agents/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        record = manager.cancel(job_id)
        if record is None:
            raise HTTPException(404, f"no job {job_id}")
        return record

    return app


# --------------------------------------------------------------------------- serving


def bind_hosts(cfg: Config) -> tuple[list[str], bool]:
    """(hosts, waiting_for_tailscale)."""
    hosts = ["127.0.0.1"]
    waiting = False
    if str(cfg.get("satellite.bind", "tailscale")).lower() == "tailscale":
        ip = net.tailscale_ip()
        if ip:
            hosts.append(ip)
        else:
            waiting = True
    return hosts, waiting


def bind_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


def _run_file(cfg: Config):
    return cfg.paths.run / "satellite.json"


def _remove_run_file(cfg: Config) -> None:
    """Removes run/satellite.json only when this process wrote it (never another copy's)."""
    path = _run_file(cfg)
    data = read_json(path, None)
    if isinstance(data, dict) and data.get("pid") == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def serve(cfg: Config | None = None, port: int | None = None, heartbeat: bool = True,
          stop: threading.Event | None = None) -> int:
    """Run the satellite until SIGTERM/SIGINT (or until `stop` is set). Returns the exit code."""
    cfg = cfg or load_config()
    cfg.paths.ensure()
    log = get_logger("satellite", home=str(cfg.paths.home))
    if not cfg.get("satellite.enabled", True):
        log.warning("satellite.enabled is false; not starting")
        print("satellite.enabled is false in config.toml; not starting", file=sys.stderr)
        return 0
    port = int(port if port is not None else cfg.get("satellite.port", 7704))
    # Take the loopback port before anything else. A second copy started by mistake (the login
    # service is already running) must stop here: before it marks the running copy's jobs as
    # interrupted and stops their processes, heartbeats, or removes the running copy's run file.
    try:
        first: socket.socket | None = bind_socket("127.0.0.1", port)
    except OSError as exc:
        log.error("could not listen", extra={"fields": {"host": "127.0.0.1", "port": port, "error": str(exc)}})
        print(f"satellite: cannot listen on 127.0.0.1:{port}: {exc} "
              "(is the satellite service already running? `orbital services status`)", file=sys.stderr)
        return 1
    if threading.current_thread() is threading.main_thread():
        # uvicorn re-raises SIGTERM once it has stopped; make that an exit that runs our cleanup.
        signal.signal(signal.SIGTERM, _exit_on_signal)
    manager = JobManager(cfg, log=log)
    app = create_app(cfg, manager)
    beat = None
    if heartbeat:
        from orbital.satellite.heartbeat import Heartbeat
        beat = Heartbeat(cfg, manager, log=log)
        beat.start()
    code = 0
    current: dict = {}
    if stop is not None:
        def _stopper() -> None:
            stop.wait()
            if current.get("server") is not None:
                current["server"].should_exit = True
        threading.Thread(target=_stopper, daemon=True).start()
    try:
        while stop is None or not stop.is_set():
            hosts, waiting = bind_hosts(cfg)
            sockets = []
            for host in hosts:
                if host == "127.0.0.1" and first is not None:
                    sockets.append(first)      # bound above, before anything else started
                    first = None
                    continue
                try:
                    sockets.append(bind_socket(host, port))
                except OSError as exc:
                    log.error("could not listen", extra={"fields": {"host": host, "port": port, "error": str(exc)}})
                    if host == "127.0.0.1":
                        for s in sockets:
                            s.close()
                        print(f"satellite: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
                        return 1
            bound = [s.getsockname()[0] for s in sockets]
            atomic_write_json(_run_file(cfg), {"pid": os.getpid(), "port": port, "hosts": bound,
                                               "started_at": iso(now()), "version": __version__})
            log.info("satellite listening", extra={"fields": {"hosts": bound, "port": port, "agents": available_agents(cfg)}})
            server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
            current["server"] = server
            if stop is not None and stop.is_set():
                server.should_exit = True
            rebind = threading.Event()
            if waiting:
                threading.Thread(target=_watch_tailscale, args=(server, rebind, log), daemon=True).start()
            server.run(sockets=sockets)
            for s in sockets:
                try:
                    s.close()
                except OSError:
                    pass
            if rebind.is_set() and not (stop is not None and stop.is_set()):
                log.info("Tailscale is up; listening on its address too")
                continue
            break
    finally:
        if first is not None:
            first.close()
        if beat:
            beat.stop()
        manager.shutdown(timeout=10)
        _remove_run_file(cfg)
        log.info("satellite stopped")
    return code


def _exit_on_signal(signum, frame):  # noqa: ARG001
    raise SystemExit(0)


def _watch_tailscale(server: uvicorn.Server, rebind: threading.Event, log) -> None:
    while not server.should_exit:
        time.sleep(REBIND_CHECK_SECONDS)
        if server.should_exit:
            return
        if net.tailscale_ip():
            rebind.set()
            server.should_exit = True
            return


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m orbital.satellite", description="Orbital Agents satellite service")
    parser.add_argument("--port", type=int, help="override satellite.port")
    parser.add_argument("--no-heartbeat", action="store_true", help="do not talk to the hub")
    args = parser.parse_args(argv)
    return serve(port=args.port, heartbeat=not args.no_heartbeat)
