"""Orbital Agents Browser Controller bridge: `python -m orbital.browser.server`.

The bridge sits between agents and the Chrome extension on the same machine. Agents POST a
command; the bridge queues it; the extension long-polls the queue over loopback, runs the
command in Chrome and posts the result back; the agent's request returns that result.

    agent / CLI / hub relay ──HTTP──▶ bridge ◀──long-poll── extension (127.0.0.1 only)

Agent API (Bearer ORBITAL_BROWSER_TOKEN for every caller that is not this machine):
    GET  /health                        no auth: {"ok","service","extension_connected","version","machine"}
    GET  /status                        bridge + extension status
    POST /command {action, params, timeout?, wait?}   run one extension action, return its result
    POST /<action>  (body = params)     same, e.g. POST /navigate {"url": "..."}
    GET  /result/<id>                   result of a command sent with wait=false
    GET  /network?host=&path=&limit=&api_only=   captured traffic (newest first)
    GET  /endpoints                     API endpoint catalog built from the capture
    GET  /events?limit=                 raw captured events
    POST /export   POST /clear          write captures/ files; empty the buffers
    POST /stage?name=<file>             upload a file (raw body, or JSON {name, content_base64})
                                        into the staged folder so `upload_file` can use it
    GET  /files?path=   POST /files/read {path}   list / fetch files in ~/Downloads or staged/
    POST /purge_staged {max_age_s?}     delete staged files

The bridge's own endpoints are also actions on /command (BRIDGE_ACTIONS: bridge_status, network,
endpoints, events, export, clear, stage, files, files_read, purge_staged, health). The hub relay
POST /v1/satellites/{id}/browser/<command> always forwards to POST /command {action: <command>, params},
so those work through the hub too.

Extension protocol (loopback only): GET /ext/commands?wait_ms=, POST /ext/results, POST /ext/events.

Binding: browser.bind = "loopback" → 127.0.0.1 only. "tailscale" → 127.0.0.1 and this machine's
Tailscale IP (retried every 30 s until Tailscale is up). Callers outside loopback (and, with
tailscale, the Tailscale ranges) are refused before anything else. Browser pages are refused
too: a request carrying a web Origin, or a loopback request whose Host is not a loopback name
(DNS rebinding), gets 403.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from orbital import __version__
from orbital.browser.capture import EventLog, build_endpoint_catalog, filter_network, redact
from orbital.core import net
from orbital.core.config import Config, load_config
from orbital.core.logs import get_logger
from orbital.core.secrets import Secrets

SERVICE = "orbital-browser-bridge"
VERSION = __version__
ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
BRIDGE_ROUTES = {"health", "status", "command", "result", "network", "endpoints", "events", "export",
                 "clear", "stage", "files", "purge_staged"}
# Answered by the bridge itself, also through POST /command {action, params}. That is how the hub
# relay (which always forwards to /command) reaches them. "status" there is the extension's own status.
BRIDGE_ACTIONS = ("health", "bridge_status", "network", "endpoints", "events", "export", "clear", "stage",
                  "files", "files_read", "purge_staged")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXT_CONNECTED_S = 30.0          # extension counts as connected if it polled this recently
MAX_LONG_POLL_MS = 25_000
MAX_JSON_BYTES = 64 * 1024 * 1024   # results carry screenshots
RESULT_TTL_S = 600
MAX_STORED_RESULTS = 500


def bind_addresses(bind: str, tailscale_ip: str | None) -> list[str]:
    """Where the bridge listens: loopback always, plus the Tailscale IP when bind = tailscale."""
    addrs = ["127.0.0.1"]
    if bind == "tailscale" and tailscale_ip:
        addrs.append(tailscale_ip)
    return addrs


def allowed_networks(bind: str) -> tuple[str, ...]:
    """Who may connect at all: loopback, plus the Tailscale ranges when bind = tailscale."""
    nets = tuple(net.LOOPBACK_NETS)
    if bind == "tailscale":
        nets += tuple(net.TAILSCALE_NETS)
    return nets


def bearer_token(headers) -> str:
    auth = (headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def token_matches(given: str, expected: str) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def host_name(host_header: str) -> str:
    """'127.0.0.1:7703' -> '127.0.0.1'; '[::1]:7703' -> '::1'."""
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:].split("]", 1)[0]
    if h.count(":") == 1:
        return h.split(":", 1)[0]
    return h


@dataclass
class BridgeSettings:
    port: int = 7703
    bind: str = "loopback"
    token: str = ""
    data_dir: Path = field(default_factory=lambda: Path("."))
    run_dir: Path = field(default_factory=lambda: Path("."))
    home: Path | None = None
    machine: str = ""
    max_events: int = 20_000
    events_log_mb: float = 50.0
    max_stage_mb: float = 500.0
    staged_max_mb: float = 2000.0
    staged_max_age_hours: float = 24.0
    capture_secrets: bool = False
    command_timeout: float = 60.0
    max_command_timeout: float = 600.0
    read_roots: list[Path] = field(default_factory=list)
    # Callers in these networks are "this machine" and need no token. Tests narrow it.
    local_networks: tuple[str, ...] = tuple(net.LOOPBACK_NETS)
    write_run_file: bool = True

    @classmethod
    def from_config(cls, cfg: Config, *, port: int | None = None, bind: str | None = None) -> "BridgeSettings":
        g = cfg.get
        data_dir = cfg.paths.browser_data
        roots = [Path(p).expanduser() for p in (g("browser.read_roots") or ["~/Downloads"])]
        return cls(
            port=int(port if port is not None else g("browser.bridge_port", 7703)),
            bind=str(bind or g("browser.bind", "loopback")).lower(),
            token=Secrets(cfg.paths).get("ORBITAL_BROWSER_TOKEN"),
            data_dir=data_dir,
            run_dir=cfg.paths.run,
            home=cfg.paths.home,
            machine=cfg.machine_name,
            max_events=int(g("browser.max_events", 20_000)),
            events_log_mb=float(g("browser.events_log_mb", 50)),
            max_stage_mb=float(g("browser.max_stage_mb", 500)),
            staged_max_mb=float(g("browser.staged_max_mb", 2000)),
            staged_max_age_hours=float(g("browser.staged_max_age_hours", 24)),
            capture_secrets=bool(g("browser.capture_secrets", False)),
            command_timeout=float(g("browser.command_timeout", 60)),
            read_roots=roots,
        )

    @property
    def staged_dir(self) -> Path:
        return self.data_dir / "staged"

    @property
    def captures_dir(self) -> Path:
        return self.data_dir / "captures"


class HttpError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"ok": False, "error": message, **extra}


class BridgeState:
    """Command queue, results, captured events and extension bookkeeping. Thread-safe."""

    def __init__(self, settings: BridgeSettings, log):
        self.settings = settings
        self.log = log
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.queue: deque[dict] = deque()
        self.pending: dict[str, dict] = {}      # id -> {action, enqueued_at, delivered_at, waiting}
        self.results: dict[str, dict] = {}      # id -> {result, at}
        self.events: deque[dict] = deque(maxlen=max(100, settings.max_events))
        self.event_log = EventLog(settings.captures_dir / "events.jsonl", int(settings.events_log_mb * 1024 * 1024))
        self.started = time.time()
        self.last_poll: float | None = None
        self.polling_now = 0
        self.ext_info: dict[str, Any] = {}
        self.instances: dict[str, float] = {}
        self.was_connected = False
        self.counters = {"commands": 0, "commands_failed": 0, "commands_timed_out": 0, "events_in": 0, "staged_files": 0}

    # ── extension side ──
    def extension_connected(self) -> bool:
        with self.lock:
            if self.polling_now > 0:
                return True
            return self.last_poll is not None and time.time() - self.last_poll < EXT_CONNECTED_S

    def note_poll(self, info: dict[str, str]) -> None:
        with self.lock:
            now = time.time()
            if not self.was_connected:
                self.log.info("extension connected", extra={"fields": {"version": info.get("version"), "build": info.get("build")}})
            self.was_connected = True
            self.last_poll = now
            inst = (info.get("instance") or "")[:16]
            if inst:
                self.instances[inst] = now
            for k in ("version", "build", "capturing"):
                if info.get(k) is not None:
                    self.ext_info[k] = info[k]

    def take_commands(self, wait_ms: int) -> list[dict]:
        deadline = time.time() + min(max(wait_ms, 0), MAX_LONG_POLL_MS) / 1000.0
        with self.cv:
            self.polling_now += 1
            try:
                while not self.queue:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    self.cv.wait(timeout=min(remaining, 1.0))
                out = []
                while self.queue and len(out) < 10:
                    cmd = self.queue.popleft()
                    meta = self.pending.get(cmd["id"])
                    if meta is not None:
                        meta["delivered_at"] = time.time()
                    out.append(cmd)
                self.last_poll = time.time()
                return out
            finally:
                self.polling_now -= 1

    def put_result(self, body: dict) -> None:
        cid = str(body.get("id") or "")
        if not cid:
            return
        with self.cv:
            meta = self.pending.get(cid)
            if meta is not None and meta.get("waiting"):
                meta["result"] = body
            else:
                self.pending.pop(cid, None)
                self.results[cid] = {"result": body, "at": time.time()}
                self._trim_results()
            self.cv.notify_all()

    def _trim_results(self) -> None:
        cutoff = time.time() - RESULT_TTL_S
        for k in [k for k, v in self.results.items() if v["at"] < cutoff]:
            self.results.pop(k, None)
        while len(self.results) > MAX_STORED_RESULTS:
            self.results.pop(next(iter(self.results)), None)

    def add_events(self, events: list) -> int:
        keep = self.settings.capture_secrets
        clean = [redact(e, keep_secrets=keep) for e in events if isinstance(e, dict)]
        with self.lock:
            self.events.extend(clean)
            self.counters["events_in"] += len(clean)
        try:
            self.event_log.write(clean)
        except OSError as exc:
            self.log.warning("capture log write failed", extra={"fields": {"error": str(exc)}})
        return len(clean)

    # ── agent side ──
    def enqueue(self, action: str, params: dict, cid: str | None = None, waiting: bool = True) -> str:
        cid = cid or uuid.uuid4().hex[:16]
        cmd = {"id": cid, "action": action, "params": params}
        with self.cv:
            self.pending[cid] = {"action": action, "enqueued_at": time.time(), "delivered_at": None, "waiting": waiting}
            self.queue.append(cmd)
            self.counters["commands"] += 1
            self.cv.notify_all()
        return cid

    def wait_result(self, cid: str, timeout: float) -> tuple[str, dict | None]:
        """('ok', result) | ('undelivered', None) | ('timeout', None)."""
        deadline = time.time() + timeout
        with self.cv:
            while True:
                meta = self.pending.get(cid)
                if meta is None:
                    return "timeout", None
                if "result" in meta:
                    self.pending.pop(cid, None)
                    return "ok", meta["result"]
                remaining = deadline - time.time()
                if remaining <= 0:
                    if meta["delivered_at"] is None:
                        # never picked up: drop it so it cannot run later, by surprise
                        self.pending.pop(cid, None)
                        try:
                            self.queue.remove(next(c for c in self.queue if c["id"] == cid))
                        except (StopIteration, ValueError):
                            pass
                        return "undelivered", None
                    meta["waiting"] = False   # a late result goes to /result/<id>
                    return "timeout", None
                self.cv.wait(timeout=min(remaining, 1.0))

    def lookup_result(self, cid: str) -> tuple[str, dict | None]:
        with self.lock:
            self._trim_results()
            if cid in self.results:
                return "done", self.results[cid]["result"]
            if cid in self.pending:
                return "pending", None
            return "unknown", None

    def clear(self) -> None:
        with self.cv:
            self.events.clear()
            self.results.clear()
            for cmd in list(self.queue):
                self.pending.pop(cmd["id"], None)
            self.queue.clear()
            self.cv.notify_all()

    def status(self) -> dict:
        with self.lock:
            now = time.time()
            recent = [i for i, t in self.instances.items() if now - t < 60]
            ext = {
                "connected": self.extension_connected(),
                "last_poll_age_s": None if self.last_poll is None else round(now - self.last_poll, 1),
                "version": self.ext_info.get("version"),
                "build": self.ext_info.get("build"),
                "capturing": self.ext_info.get("capturing") in ("1", "true", True),
                "instances": len(recent),
            }
            if len(recent) > 1:
                ext["warning"] = ("more than one browser profile polls this bridge; commands go to whichever "
                                  "asks first. Give each profile its own bridge port.")
            in_flight = sum(1 for m in self.pending.values() if m["delivered_at"] is not None)
            return {
                "extension": ext,
                "queue": {"waiting": len(self.queue), "in_flight": in_flight, "stored_results": len(self.results)},
                "capture": {"events": len(self.events), "events_in": self.counters["events_in"],
                            "log_file": str(self.event_log.path), "log_bytes": self.event_log.size(),
                            "secrets_redacted": not self.settings.capture_secrets},
                "counters": dict(self.counters),
                "uptime_s": round(now - self.started, 1),
            }


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64
    bridge_log = None

    def handle_error(self, request, client_address) -> None:
        """A client that hangs up (a reloaded extension, a killed agent) is normal: stay quiet."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        if self.bridge_log is not None:
            self.bridge_log.exception("connection error")
        else:
            super().handle_error(request, client_address)


class _Server6(_Server):
    address_family = socket.AF_INET6


class BridgeServer:
    """The running bridge. `start()` binds and serves in background threads; `stop()` ends it."""

    def __init__(self, settings: BridgeSettings, log=None):
        self.settings = settings
        self.log = log or get_logger("browser-bridge", home=str(settings.home) if settings.home else None)
        self.state = BridgeState(settings, self.log)
        self.servers: dict[str, ThreadingHTTPServer] = {}
        self.threads: list[threading.Thread] = []
        self.port = settings.port
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ── lifecycle ──
    def start(self) -> "BridgeServer":
        s = self.settings
        for d in (s.data_dir, s.staged_dir, s.captures_dir, s.run_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._listen("127.0.0.1")
        if s.bind == "tailscale":
            if not s.token:
                self.log.warning("browser.bind is tailscale but ORBITAL_BROWSER_TOKEN is not set; calls from other machines will be refused")
            if not self._try_tailscale():
                t = threading.Thread(target=self._tailscale_retry, name="bridge-tailscale", daemon=True)
                t.start()
                self.threads.append(t)
        elif s.bind != "loopback":
            self.log.warning("unknown browser.bind; using loopback", extra={"fields": {"bind": s.bind}})
        self._write_run_file()
        self.log.info("bridge started", extra={"fields": {"port": self.port, "bind": s.bind, "listening": self.listening(),
                                                          "token_set": bool(s.token), "version": VERSION}})
        return self

    def _listen(self, host: str) -> None:
        cls = _Server6 if ":" in host else _Server
        handler = _make_handler(self)
        httpd = cls((host, self.port), handler)
        httpd.bridge_log = self.log
        if self.port == 0:
            self.port = httpd.server_address[1]
        with self._lock:
            self.servers[host] = httpd
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, name=f"bridge-{host}", daemon=True)
        t.start()
        self.threads.append(t)

    def _try_tailscale(self) -> bool:
        ip = net.tailscale_ip()
        if not ip:
            self.log.warning("Tailscale address not available yet; serving loopback only, retrying every 30 s")
            return False
        if ip in self.servers:
            return True
        try:
            self._listen(ip)
        except OSError as exc:
            self.log.warning("could not listen on the Tailscale address", extra={"fields": {"ip": ip, "error": str(exc)}})
            return False
        self.log.info("listening on Tailscale", extra={"fields": {"ip": ip, "port": self.port}})
        self._write_run_file()
        return True

    def _tailscale_retry(self) -> None:
        while not self._stop.wait(30):
            if self._try_tailscale():
                return

    def listening(self) -> list[str]:
        with self._lock:
            return [f"[{h}]:{self.port}" if ":" in h else f"{h}:{self.port}" for h in self.servers]

    def bound_hosts(self) -> set[str]:
        with self._lock:
            return set(self.servers)

    def _run_file(self) -> Path:
        return self.settings.run_dir / "bridge.json"

    def _write_run_file(self) -> None:
        if not self.settings.write_run_file:
            return
        data = {"service": SERVICE, "port": self.port, "pid": os.getpid(), "bind": self.settings.bind,
                "listening": self.listening(), "version": VERSION, "started": int(self.state.started)}
        path = self._run_file()
        tmp = path.with_name(f".bridge.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            servers = list(self.servers.values())
            self.servers.clear()
        for httpd in servers:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001 - best effort on shutdown
                pass
        with self.state.cv:
            self.state.cv.notify_all()
        if self.settings.write_run_file:
            try:
                data = json.loads(self._run_file().read_text(encoding="utf-8"))
                if data.get("pid") == os.getpid():
                    self._run_file().unlink()
            except (OSError, ValueError):
                pass
        self.log.info("bridge stopped", extra={"fields": {"port": self.port}})

    def serve_forever(self) -> None:
        self.start()
        try:
            while not self._stop.wait(1.0):
                pass
        finally:
            self.stop()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ── access control ──
    def check_access(self, client_ip: str, path: str, headers) -> bool:
        """Raise HttpError if this request may not proceed. Returns True for a 'local' caller."""
        s = self.settings
        if not net.allowed(client_ip, allowed_networks(s.bind)):
            self.log.warning("refused caller outside the allow list", extra={"fields": {"ip": client_ip, "path": path}})
            raise HttpError(403, "not allowed from this address")
        origin = (headers.get("Origin") or "").strip()
        if origin and origin != "null" and not origin.startswith(("chrome-extension://", "moz-extension://")):
            self.log.warning("refused a request from a web page", extra={"fields": {"ip": client_ip, "path": path}})
            raise HttpError(403, "web pages may not call the browser bridge")
        if path == "/health":
            return True
        if path.startswith("/ext/"):
            # The extension always runs on this machine and talks to 127.0.0.1.
            if not net.allowed(client_ip, net.LOOPBACK_NETS):
                raise HttpError(403, "the extension endpoints are for this machine only")
            self._check_host(headers, path)
            return True
        local = net.allowed(client_ip, s.local_networks)
        token_ok = token_matches(bearer_token(headers), s.token)
        if local:
            if not token_ok:
                self._check_host(headers, path)
            return True
        if not s.token:
            raise HttpError(401, "this bridge has no ORBITAL_BROWSER_TOKEN set, so it refuses calls from other machines")
        if not token_ok:
            self.log.warning("refused a remote call without a valid token", extra={"fields": {"ip": client_ip, "path": path}})
            raise HttpError(401, "missing or wrong bridge token (Authorization: Bearer ...)")
        return False

    def _check_host(self, headers, path: str) -> None:
        """A loopback request must name a loopback host: a web page that rebinds its own DNS name
        to 127.0.0.1 sends its own name in Host, and is refused here."""
        host = host_name(headers.get("Host") or "")
        if host and host not in LOOPBACK_HOSTS and host not in self.bound_hosts():
            self.log.warning("refused a loopback request with a foreign Host header", extra={"fields": {"path": path}})
            raise HttpError(403, "unexpected Host header")

    # ── command execution ──
    def run_command(self, action: str, params: dict, *, timeout: float | None = None, wait: bool = True,
                    caller: str = "", local: bool = True) -> tuple[int, dict]:
        action = str(action or "").strip()
        if not ACTION_RE.match(action):
            raise HttpError(400, "action must be a lower-case name like navigate or list_tabs")
        if not isinstance(params, dict):
            raise HttpError(400, "params must be a JSON object")
        if action in BRIDGE_ACTIONS:
            return self.bridge_action(action, params)
        s = self.settings
        t = s.command_timeout if timeout is None else float(timeout)
        t = max(0.5, min(t, s.max_command_timeout))
        started = time.time()
        cid = self.state.enqueue(action, params, waiting=wait)
        if not wait:
            return 202, {"ok": True, "id": cid, "action": action, "queued": True}
        outcome, result = self.state.wait_result(cid, t)
        ms = int((time.time() - started) * 1000)
        fields = {"action": action, "id": cid, "ms": ms, "remote": not local, "caller": caller}
        if outcome == "ok" and result is not None:
            ok = bool(result.get("ok"))
            if not ok:
                self.state.counters["commands_failed"] += 1
            self.log.info("command", extra={"fields": {**fields, "ok": ok}})
            body = {"ok": ok, "id": cid, "action": result.get("action") or action, "ms": result.get("ms", ms)}
            if ok:
                body["result"] = result.get("result")
            else:
                body["error"] = str(result.get("error") or "command failed")
            return 200, body
        self.state.counters["commands_timed_out"] += 1
        if outcome == "undelivered":
            connected = self.state.extension_connected()
            msg = ("the extension did not pick up the command in time" if connected else
                   "the browser extension is not connected to this bridge (is Chrome open with the "
                   "Orbital Agents Browser Controller loaded, and set to this port?)")
            self.log.warning("command not delivered", extra={"fields": {**fields, "extension_connected": connected}})
            return 503, {"ok": False, "id": cid, "action": action, "error": msg, "extension_connected": connected}
        self.log.warning("command timed out", extra={"fields": fields})
        return 504, {"ok": False, "id": cid, "action": action,
                     "error": f"no result within {t:g} s; the command may still finish (GET /result/{cid})"}

    # ── answered by the bridge itself ──
    def bridge_action(self, name: str, args: dict) -> tuple[int, dict]:
        table: dict[str, Callable[[dict], tuple[int, dict]]] = {
            "health": lambda a: (200, self.health_payload()),
            "bridge_status": lambda a: (200, self.status_payload()),
            "network": self.network_payload,
            "endpoints": self.endpoints_payload,
            "events": self.events_payload,
            "export": lambda a: (200, self.export_payload()),
            "clear": self.clear_payload,
            "stage": self.stage_base64,
            "files": self.list_files,
            "files_read": self.read_file,
            "purge_staged": self.purge_payload,
        }
        return table[name](args or {})

    def health_payload(self) -> dict:
        return {"ok": True, "service": SERVICE, "extension_connected": self.state.extension_connected(),
                "version": VERSION, "machine": self.settings.machine}

    def status_payload(self) -> dict:
        s = self.settings
        return {
            "ok": True,
            "service": SERVICE,
            "version": VERSION,
            "machine": s.machine,
            "port": self.port,
            "bind": s.bind,
            "listening": self.listening(),
            "token_set": bool(s.token),
            "data_dir": str(s.data_dir),
            "staged": self.staged_summary(),
            **self.state.status(),
        }

    def _events(self) -> list[dict]:
        with self.state.lock:
            return list(self.state.events)

    def network_payload(self, args: dict) -> tuple[int, dict]:
        limit = _int(args.get("limit"), 200, 1, 5000)
        api_only = str(args.get("api_only", "0")).lower() in ("1", "true", "yes")
        found = filter_network(self._events(), args.get("host") or None, args.get("path") or None, limit, api_only)
        return 200, {"ok": True, "count": len(found), "events": found}

    def endpoints_payload(self, args: dict) -> tuple[int, dict]:
        events = self._events()
        items = build_endpoint_catalog(events, limit=_int(args.get("limit"), 500, 1, 5000))
        host = str(args.get("host") or "").lower()
        if host:
            items = [i for i in items if host in i["host"].lower()]
        return 200, {"ok": True, "count": len(items), "endpoints": items, "events_seen": len(events)}

    def events_payload(self, args: dict) -> tuple[int, dict]:
        limit = _int(args.get("limit"), 100, 1, 20000)
        events = self._events()[-limit:]
        return 200, {"ok": True, "count": len(events), "events": events}

    def export_payload(self) -> dict:
        events = self._events()
        catalog = build_endpoint_catalog(events)
        out_dir = self.settings.captures_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        full = out_dir / "full_export.json"
        eps = out_dir / "endpoints_catalog.json"
        full.write_text(json.dumps({"exported_at": stamp, "event_count": len(events), "endpoints": catalog,
                                    "events": events}, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
        eps.write_text(json.dumps({"built_at": stamp, "endpoints": catalog}, ensure_ascii=False, indent=1,
                                  default=str) + "\n", encoding="utf-8")
        return {"ok": True, "events": len(events), "endpoints": len(catalog), "path": str(full), "catalog": str(eps)}

    def clear_payload(self, args: dict) -> tuple[int, dict]:
        self.state.clear()
        if str(args.get("log", "")).lower() in ("1", "true", "yes"):
            self.state.event_log.clear()
        return 200, {"ok": True}

    def purge_payload(self, args: dict) -> tuple[int, dict]:
        age = args.get("max_age_s")
        return 200, {"ok": True, "removed": self.purge_staged(max_age_s=float(age) if age is not None else 0.0)}

    def staged_path(self, name: str) -> Path:
        self.purge_staged()
        self.settings.staged_dir.mkdir(parents=True, exist_ok=True)
        return self.settings.staged_dir / f"{uuid.uuid4().hex[:12]}_{safe_filename(name)}"

    def stage_done(self, dest: Path, size: int) -> tuple[int, dict]:
        with self.state.lock:
            self.state.counters["staged_files"] += 1
        self.log.info("file staged", extra={"fields": {"bytes": size}})
        return 200, {"ok": True, "path": str(dest.resolve()), "bytes": size, "name": dest.name.split("_", 1)[1],
                     "machine": self.settings.machine}

    def stage_base64(self, args: dict) -> tuple[int, dict]:
        s = self.settings
        b64 = args.get("content_base64")
        if not isinstance(b64, str) or not b64:
            raise HttpError(400, "content_base64 required")
        try:
            raw = base64.b64decode(b64, validate=False)
        except ValueError:
            raise HttpError(400, "content_base64 is not valid base64") from None
        if len(raw) > s.max_stage_mb * 1024 * 1024:
            raise HttpError(413, f"file over {s.max_stage_mb:g} MB")
        if not raw:
            raise HttpError(400, "empty file")
        dest = self.staged_path(str(args.get("name") or "file.bin"))
        dest.write_bytes(raw)
        return self.stage_done(dest, len(raw))

    def list_files(self, args: dict) -> tuple[int, dict]:
        target = self.path_allowed(str(args.get("path") or self.settings.staged_dir))
        if not target.is_dir():
            raise HttpError(404, "not a folder: " + str(target))
        limit = _int(args.get("limit"), 50, 1, 500)
        rows = []
        for p in sorted(target.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if p.name.startswith("."):
                continue
            try:
                stt = p.stat()
            except OSError:
                continue
            rows.append({"name": p.name, "path": str(p), "is_dir": p.is_dir(),
                         "bytes": stt.st_size if p.is_file() else 0, "mtime": stt.st_mtime})
            if len(rows) >= limit:
                break
        return 200, {"ok": True, "path": str(target), "count": len(rows), "entries": rows, "machine": self.settings.machine}

    def read_file(self, args: dict) -> tuple[int, dict]:
        target = self.path_allowed(str(args.get("path") or ""))
        if not target.is_file():
            raise HttpError(404, "not a file: " + str(target))
        if target.stat().st_size > self.settings.max_stage_mb * 1024 * 1024:
            raise HttpError(413, f"file over {self.settings.max_stage_mb:g} MB")
        raw = target.read_bytes()
        return 200, {"ok": True, "path": str(target), "name": target.name, "bytes": len(raw),
                     "content_base64": base64.b64encode(raw).decode("ascii"), "machine": self.settings.machine}

    # ── staging and files ──
    def purge_staged(self, max_age_s: float | None = None) -> int:
        s = self.settings
        age = s.staged_max_age_hours * 3600 if max_age_s is None else max_age_s
        now = time.time()
        files = []
        removed = 0
        try:
            entries = list(s.staged_dir.iterdir())
        except OSError:
            return 0
        for p in entries:
            try:
                st = p.stat()
            except OSError:
                continue
            if not p.is_file():
                continue
            if now - st.st_mtime >= age:
                p.unlink(missing_ok=True)
                removed += 1
            else:
                files.append((st.st_mtime, st.st_size, p))
        cap = int(s.staged_max_mb * 1024 * 1024)
        total = sum(f[1] for f in files)
        for mtime, size, p in sorted(files):
            if total <= cap:
                break
            p.unlink(missing_ok=True)
            total -= size
            removed += 1
        return removed

    def staged_summary(self) -> dict:
        files = [p for p in self.settings.staged_dir.glob("*") if p.is_file()]
        return {"dir": str(self.settings.staged_dir), "files": len(files), "bytes": sum(p.stat().st_size for p in files)}

    def read_roots(self) -> list[Path]:
        return [self.settings.staged_dir] + list(self.settings.read_roots)

    def path_allowed(self, raw: str) -> Path:
        target = Path(str(raw or "")).expanduser().resolve()
        for root in self.read_roots():
            try:
                root = root.expanduser().resolve()
            except OSError:
                continue
            if target == root or root in target.parents:
                return target
        raise HttpError(403, "path not allowed; readable folders: " + ", ".join(str(r) for r in self.read_roots()))


def safe_filename(name: str) -> str:
    base = Path(unquote(name or "file.bin")).name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "file.bin"
    return base[:160]


def _make_handler(bridge: BridgeServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "orbital-browser-bridge"
        sys_version = ""

        def log_message(self, fmt: str, *args) -> None:  # quiet: the bridge logs its own lines
            pass

        # ── plumbing ──
        def _send(self, status: int, payload: Any) -> None:
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _body_bytes(self, limit: int, sink: Callable[[bytes], None] | None = None) -> bytes:
            """Read the request body (Content-Length or chunked). With a sink, stream to it."""
            self._body_done = True
            chunks: list[bytes] = []
            total = 0

            def take(data: bytes) -> None:
                nonlocal total
                total += len(data)
                if total > limit:
                    self.close_connection = True
                    raise HttpError(413, f"request body over {limit // (1024 * 1024)} MB")
                if sink:
                    sink(data)
                else:
                    chunks.append(data)

            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                while True:
                    line = self.rfile.readline(1024)
                    size = int(line.split(b";", 1)[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                            pass
                        break
                    remaining = size
                    while remaining:
                        data = self.rfile.read(min(remaining, 1 << 20))
                        if not data:
                            raise HttpError(400, "truncated body")
                        take(data)
                        remaining -= len(data)
                    self.rfile.readline(8)
            else:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise HttpError(400, "bad Content-Length") from None
                if length > limit:
                    self.close_connection = True
                    raise HttpError(413, f"request body over {limit // (1024 * 1024)} MB")
                remaining = length
                while remaining:
                    data = self.rfile.read(min(remaining, 1 << 20))
                    if not data:
                        raise HttpError(400, "truncated body")
                    take(data)
                    remaining -= len(data)
            return b"".join(chunks)

        def _json(self, limit: int = MAX_JSON_BYTES) -> dict:
            raw = self._body_bytes(limit)
            if not raw.strip():
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise HttpError(400, "body is not valid JSON") from None
            if not isinstance(data, dict):
                raise HttpError(400, "body must be a JSON object")
            return data

        def _client_ip(self) -> str:
            return (self.client_address or ("",))[0]

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            self._body_done = False
            try:
                local = bridge.check_access(self._client_ip(), path, self.headers)
                status, payload = self._route(method, path, query, local)
            except HttpError as exc:
                status, payload = exc.status, exc.payload
                if method == "POST" and not self._body_done:
                    # the body was never read: close rather than leave it in the stream
                    self.close_connection = True
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001 - never kill the server thread
                bridge.log.exception("request failed", extra={"fields": {"path": path}})
                status, payload = 500, {"ok": False, "error": f"bridge error: {type(exc).__name__}"}
            try:
                self._send(status, payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_OPTIONS(self) -> None:  # noqa: N802 - no CORS: browsers pages get nothing
            self._send(403, {"ok": False, "error": "web pages may not call the browser bridge"})

        # ── routes ──
        def _route(self, method: str, path: str, query: dict, local: bool) -> tuple[int, Any]:
            parts = [p for p in path.split("/") if p]
            head = parts[0] if parts else ""

            if path == "/health":
                return 200, bridge.health_payload()

            if head == "ext":
                return self._ext_route(method, path, query)

            if path == "/stage" and method == "POST":
                if "application/json" in (self.headers.get("Content-Type") or "").lower():
                    limit = int(bridge.settings.max_stage_mb * 1024 * 1024 * 1.4) + 4096
                    return bridge.stage_base64({**query, **self._json(limit=limit)})
                return self._stage_stream(query)

            body: dict = self._json() if method == "POST" else {}
            args = {**query, **body}

            if path == "/status":
                return 200, bridge.status_payload()

            if path == "/command" and method == "POST":
                params = body.get("params", body.get("args", {})) or {}
                return bridge.run_command(body.get("action", ""), params, timeout=body.get("timeout"),
                                          wait=body.get("wait", True) is not False, caller=self._client_ip(), local=local)

            if head == "command" and len(parts) == 2 and method == "POST":
                return self._routed_command(parts[1], body, query, local)

            if head == "result":
                cid = parts[1] if len(parts) > 1 else str(args.get("id") or "")
                found, result = bridge.state.lookup_result(cid)
                if found == "done":
                    return 200, result
                if found == "pending":
                    return 202, {"ok": True, "id": cid, "pending": True}
                return 404, {"ok": False, "id": cid, "error": "no such command (results are kept 10 minutes)"}

            if path == "/network":
                return bridge.network_payload(args)
            if path == "/endpoints":
                return bridge.endpoints_payload(args)
            if path == "/events":
                return bridge.events_payload(args)
            if path == "/files":
                return bridge.list_files(args)
            if method == "POST":
                if path == "/export":
                    return 200, bridge.export_payload()
                if path == "/clear":
                    return bridge.clear_payload(args)
                if path == "/purge_staged":
                    return bridge.purge_payload(args)
                if path == "/files/read":
                    return bridge.read_file(args)
                # POST /<action> with the params as the body.
                if len(parts) == 1 and head not in BRIDGE_ROUTES:
                    return self._routed_command(head, body, query, local)

            raise HttpError(404, f"no route {method} {path}")

        def _routed_command(self, action: str, body: dict, query: dict, local: bool) -> tuple[int, dict]:
            params = dict(body)
            timeout = params.pop("_timeout", None) or query.get("timeout")
            wait = params.pop("_wait", None)
            if wait is None:
                wait = query.get("wait", True)
            wait = str(wait).lower() not in ("0", "false", "no")
            return bridge.run_command(action, params, timeout=float(timeout) if timeout else None, wait=wait,
                                      caller=self._client_ip(), local=local)

        def _ext_route(self, method: str, path: str, query: dict) -> tuple[int, Any]:
            st = bridge.state
            if path == "/ext/commands" and method == "GET":
                st.note_poll(query)
                cmds = st.take_commands(_int(query.get("wait_ms"), 0, 0, MAX_LONG_POLL_MS))
                return 200, {"commands": cmds}
            if path == "/ext/results" and method == "POST":
                st.put_result(self._json())
                return 200, {"ok": True}
            if path == "/ext/events" and method == "POST":
                events = self._json().get("events") or []
                if not isinstance(events, list):
                    raise HttpError(400, "events must be a list")
                return 200, {"ok": True, "accepted": st.add_events(events)}
            raise HttpError(404, f"no route {method} {path}")

        def _stage_stream(self, query: dict) -> tuple[int, dict]:
            """POST /stage?name=<file> with the raw file as the body (Content-Length or chunked)."""
            limit = int(bridge.settings.max_stage_mb * 1024 * 1024)
            dest = bridge.staged_path(str(query.get("name") or "file.bin"))
            tmp = dest.with_name("." + dest.name + ".part")
            try:
                with tmp.open("wb") as fh:
                    self._body_bytes(limit, sink=fh.write)
                size = tmp.stat().st_size
                if size == 0:
                    raise HttpError(400, "empty file")
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
            return bridge.stage_done(dest, size)

    return Handler


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m orbital.browser.server",
                                 description="Orbital Agents Browser Controller bridge (Chrome extension <-> agents).")
    ap.add_argument("--port", type=int, default=None, help="override browser.bridge_port")
    ap.add_argument("--bind", choices=["loopback", "tailscale"], default=None, help="override browser.bind")
    ap.add_argument("--home", default=None, help="ORBITAL_HOME to use (default ~/.orbital-agents)")
    args = ap.parse_args(argv)
    if args.home:
        os.environ["ORBITAL_HOME"] = args.home
    cfg = load_config(args.home)
    cfg.paths.ensure()
    if not cfg.get("browser.enabled", True):
        print("browser.enabled is false in config.toml; not starting the bridge")
        return 0
    settings = BridgeSettings.from_config(cfg, port=args.port, bind=args.bind)
    log = get_logger("browser-bridge", home=str(cfg.paths.home))
    bridge = BridgeServer(settings, log=log)

    def _term(signum, frame):  # noqa: ARG001
        bridge._stop.set()

    previous = signal.signal(signal.SIGTERM, _term)
    try:
        try:
            bridge.start()
        except OSError as exc:
            log.error("could not start the bridge", extra={"fields": {"port": settings.port, "error": str(exc)}})
            print(f"could not start the bridge on port {settings.port}: {exc}", file=sys.stderr)
            return 1
        print(f"Orbital Agents Browser Controller bridge on {', '.join(bridge.listening())} (bind={settings.bind})", flush=True)
        try:
            while not bridge._stop.wait(1.0):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            bridge.stop()
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
