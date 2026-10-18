"""Helpers for the browser controller tests: a fake extension poller, a fake hub, small utilities."""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import httpx

REPO = Path(__file__).resolve().parents[2]
EXTENSION_DIR = REPO / "browser-extension"
PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
SECRET_ENV = ("ORBITAL_BROWSER_TOKEN", "ORBITAL_AUTOMATION_TOKEN", "ORBITAL_HUB_TOKEN", "ORBITAL_JOIN_TOKEN")


@contextlib.contextmanager
def refused_port():
    """A port held for the whole block where nothing answers HTTP: every connection is reset at once.
    (A bound socket that does not listen would make macOS drop connections until they time out.)"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    sock.settimeout(0.2)
    done = threading.Event()

    def reset_all():
        while not done.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conn.close()

    t = threading.Thread(target=reset_all, daemon=True)
    t.start()
    try:
        yield sock.getsockname()[1]
    finally:
        done.set()
        t.join(2)
        sock.close()


def bound_socket() -> socket.socket:
    """A listening socket on a free port, to hand to a server (no gap for another process to take it)."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    return sock






def write_config(home: Path, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")






class FakeExtension(threading.Thread):
    """Polls /ext/commands like the extension and answers with handlers (action -> fn(params))."""

    def __init__(self, url: str, handlers: dict[str, Callable[[dict], Any]] | None = None,
                 delay: float = 0.0, instance: str = "fake1"):
        super().__init__(daemon=True)
        self.url = url
        self.delay = delay
        self.instance = instance
        self.received: list[dict] = []
        self.polls = 0                       # completed polls, a deterministic clock for tests
        self.ready = threading.Event()       # set after the first poll the bridge answered
        self.stopped = threading.Event()
        self.handlers = {
            "ping": lambda p: {"pong": True},
            "status": lambda p: {"version": "0.1.0", "build": "test", "sessions": [{"session": "main"}], "actions": ["ping"]},
            "list_tabs": lambda p: [{"id": 7, "url": "https://example.com/", "title": "Example", "active": True, "session": "main"}],
            "navigate": lambda p: {"tabId": 7, "url": p.get("url"), "title": "Example", "session": p.get("session", "main"), "complete": True},
            "eval": lambda p: {"value": f"value of {p.get('code')}", "via": "main"},
            "screenshot": lambda p: {"dataUrl": "data:image/png;base64," + PNG_1PX, "via": "cdp"},
            "click": lambda p: {"tag": "BUTTON", "text": "Go", "selector": p.get("selector")},
            "type": lambda p: {"typed": len(p.get("text", "")), "value": p.get("text"), "human": bool(p.get("human"))},
            "wait_for": lambda p: {"found": "text", "text": p.get("text")},
            "set_capture": lambda p: {"capturing": bool(p.get("enabled"))},
            "upload_file": self._upload,
            "automation_window": lambda p: {"op": p.get("op"), "sessions": []},
            "hotkey": lambda p: {"key": p.get("key")},
            "text": lambda p: {"text": "Hello page"},
            "boom": self._boom,
        }
        if handlers:
            self.handlers.update(handlers)

    @staticmethod
    def _boom(params):
        raise RuntimeError("the page said no")

    @staticmethod
    def _upload(params):
        paths = params.get("paths") or [params.get("path")]
        missing = [p for p in paths if not Path(p).is_file()]
        if missing:
            raise RuntimeError(f"no such file: {missing[0]}")
        return {"ok": True, "mode": "direct", "files": paths}

    def run(self) -> None:
        with httpx.Client(timeout=10) as http:
            while not self.stopped.is_set():
                try:
                    r = http.get(f"{self.url}/ext/commands", params={"wait_ms": 300, "instance": self.instance,
                                                                       "version": "0.1.0", "build": "test", "capturing": 0})
                    cmds = r.json().get("commands", [])
                except (httpx.HTTPError, ValueError):
                    self.stopped.wait(0.1)
                    continue
                self.polls += 1
                self.ready.set()
                for cmd in cmds:
                    self.received.append(cmd)
                    threading.Thread(target=self._answer, args=(http, cmd), daemon=True).start()

    def _answer(self, http, cmd) -> None:
        if self.delay:
            time.sleep(self.delay)   # a deliberately slow extension
        handler = self.handlers.get(cmd["action"])
        try:
            if handler is None:
                raise RuntimeError(f"unknown action: {cmd['action']}")
            out = {"id": cmd["id"], "ok": True, "action": cmd["action"], "result": handler(cmd.get("params") or {}), "ms": 1}
        except Exception as exc:  # noqa: BLE001
            out = {"id": cmd["id"], "ok": False, "action": cmd["action"], "error": str(exc), "ms": 1}
        try:
            httpx.post(f"{self.url}/ext/results", json=out, timeout=10)
        except httpx.HTTPError:
            pass

    def stop(self) -> None:
        self.stopped.set()




def wait_until(fn: Callable[[], Any], timeout: float = 15.0, interval: float = 0.05):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


class FakeHub:
    """Just enough of the hub for browser nodes: GET /v1/satellites and the browser relay, which
    forwards like the real hub: POST <bridge>/command {action: <command>, params[, timeout]}."""

    def __init__(self, satellites: list[dict], token: str, browser_token: str = ""):
        self.satellites = satellites
        self.token = token
        self.browser_token = browser_token
        self.relayed: list[tuple[str, str, dict]] = []
        hub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authed(self):
                return self.headers.get("Authorization") == f"Bearer {hub.token}"

            def do_GET(self):
                if self.path == "/v1/health":
                    return self._send(200, {"ok": True})
                if not self._authed():
                    return self._send(401, {"detail": "unauthorized"})
                if self.path == "/v1/satellites":
                    return self._send(200, {"satellites": hub.satellites})
                self._send(404, {"detail": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self._authed():
                    return self._send(401, {"detail": "unauthorized"})
                parts = self.path.strip("/").split("/")
                # /v1/satellites/{id}/browser/{command}
                if len(parts) == 5 and parts[:2] == ["v1", "satellites"] and parts[3] == "browser":
                    sat = next((s for s in hub.satellites if s.get("satellite_id") == parts[2]), None)
                    if not sat:
                        return self._send(404, {"detail": "no such satellite"})
                    hub.relayed.append((parts[2], parts[4], body))
                    port = sat["capabilities"]["browser_port"]
                    headers = {"Authorization": f"Bearer {hub.browser_token}"} if hub.browser_token else {}
                    # Same forwarding as orbital/hub/satellites.py: flat params (+ "_timeout") or wrapped.
                    params = dict(body["params"]) if isinstance(body.get("params"), dict) else dict(body)
                    wait = body.get("timeout") if isinstance(body.get("params"), dict) else params.pop("_timeout", None)
                    forward = {"action": parts[4], "params": params}
                    if wait:
                        forward["timeout"] = wait
                    r = httpx.post(f"http://{sat['ip']}:{port}/command", json=forward, headers=headers, timeout=60)
                    if r.status_code >= 400:
                        return self._send(r.status_code, {"ok": False, "error": r.json().get("error")})
                    return self._send(200, r.json())
                self._send(404, {"detail": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        wait_until(self._up)

    def _up(self) -> bool:
        try:
            return httpx.get(self.url + "/v1/health", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def close(self):
        self.server.shutdown()
        self.server.server_close()
