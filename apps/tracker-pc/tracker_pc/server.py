"""Loopback-only HTTP API on 127.0.0.1:<tracker.port> (CommandServer.swift + AppDelegate.handle).

Requests with an Origin header (browsers), a foreign Host header, or a POST body that isn't
Content-Type: application/json are refused. Bodies are limited to 16 MB; slow clients are cut off
after 10 s.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import VERSION
from .media import content_type
from .model import CommandError, command_from_route, dumps, evidence_level, normalize_writer, parse_command
from .store import StoreError

if TYPE_CHECKING:
    from .app import TrackerApp

MAX_REQUEST_BYTES = 16 << 20
REQUEST_TIMEOUT = 10.0
REASONS = {200: "OK", 206: "Partial Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 408: "Request Timeout", 413: "Payload Too Large",
           415: "Unsupported Media Type", 416: "Range Not Satisfiable", 500: "Internal Server Error"}


def guard_request(method: str, headers: dict[str, str]) -> tuple[int, dict] | None:
    """Blocks browsers (any Origin header) and DNS-rebinding hosts; only local tools get in."""
    if "origin" in headers:
        return 403, {"ok": False, "error": "browser requests are not accepted"}
    host = headers.get("host")
    if host is not None:
        host = host.lower()
        if host.startswith("["):
            name = host.split("]")[0] + "]"
        else:
            name = host.split(":")[0]
        if name not in ("127.0.0.1", "localhost", "[::1]", "::1"):
            return 403, {"ok": False, "error": "bad host"}
    if method == "POST" and "application/json" not in headers.get("content-type", "").lower():
        return 415, {"ok": False, "error": "send Content-Type: application/json"}
    return None


class _Handler(BaseHTTPRequestHandler):
    server_version = f"orbital-tracker/{VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    app: "TrackerApp"  # set on the subclass

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet; the app logs what matters
        return

    def _headers(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.headers.items()}

    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict[str, str] | None = None, head_only: bool = False) -> None:
        self.send_response(status, REASONS.get(status, "OK"))
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(body)
            except OSError:
                pass
        self.close_connection = True

    def _json(self, status: int, obj: Any) -> None:
        self._send(status, dumps(obj).encode())

    def _handle(self, method: str) -> None:
        headers = self._headers()
        try:
            length = max(int(headers.get("content-length") or 0), 0)
        except ValueError:
            length = 0
        if length > MAX_REQUEST_BYTES:
            self._json(413, {"ok": False, "error": "request too large"})
            return
        rejection = guard_request(method, headers)
        if rejection:
            self._json(*rejection)
            return
        try:
            body = self.rfile.read(length) if length else b""
        except (OSError, socket.timeout):
            self.close_connection = True
            return
        url = urlsplit(self.path)
        query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        try:
            self.route(method, url.path, query, body, headers)
        except Exception as exc:  # noqa: BLE001 - one bad request never takes the tracker down
            self.app.log.error("request failed", path=url.path, error=str(exc))
            self._json(500, {"ok": False, "error": "internal error"})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    # Routes

    def route(self, method: str, path: str, query: dict, body: bytes, headers: dict) -> None:
        app = self.app
        store = app.store
        parts = [p for p in path.split("/") if p]
        if not parts or parts[0] != "v1":
            self._json(404, {"ok": False, "error": "try /v1/health"})
            return
        route = parts[1] if len(parts) > 1 else ""

        if method == "GET" and route == "health":
            self._json(200, app.health())
        elif method == "GET" and route == "settings":
            app.reload_settings_if_changed()
            self._json(200, {"ok": True, "settings": app.settings.json()})
        elif method == "POST" and route == "settings":
            try:
                obj = json.loads(body or b"{}")
            except ValueError:
                obj = {}
            level = evidence_level(obj.get("evidence")) if isinstance(obj, dict) else None
            if level is None:
                self._json(400, {"ok": False, "error": "evidence is off, min1 or all"})
                return
            by = obj.get("by") if isinstance(obj.get("by"), str) else "cli"
            app.set_evidence(level, normalize_writer(by))
            self._json(200, {"ok": True, "settings": app.settings.json()})
        elif method == "GET" and route == "items":
            if len(parts) > 2:
                item = store.item(parts[2])
                if item is None:
                    self._json(404, {"ok": False, "error": "unknown id"})
                else:
                    self._send(200, dumps(item).encode())
                return
            self._send(200, dumps(store.snapshot(all_items="all" in query)).encode())
        elif method == "GET" and route == "media" and len(parts) > 3:
            self._media(parts[2], parts[3], headers, thumb=query.get("variant") == "thumb")
        elif method == "POST" and route in ("command", "start", "sub", "update", "done"):
            try:
                if route == "command":
                    try:
                        obj = json.loads(body)
                    except ValueError as exc:
                        raise CommandError(f"bad JSON: {exc}") from None
                    cmd = parse_command(obj)
                else:
                    cmd = command_from_route(route, parts[2] if len(parts) > 2 else None, body)
                item_id = store.apply(cmd)
                self._json(200, {"ok": True, "id": item_id})
            except (StoreError, CommandError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
        elif method == "POST" and route == "debug" and len(parts) > 2 and parts[2] == "ui":
            try:
                obj = json.loads(body or b"{}")
            except ValueError:
                obj = {}
            self._json(200, app.debug_ui(obj if isinstance(obj, dict) else {}))
        elif method == "POST" and route == "debug" and len(parts) > 2 and parts[2] == "retention":
            store.apply_retention()
            self._json(200, {"ok": True, "items": len(store.items)})
        else:
            self._json(404, {"ok": False, "error": f"unknown route {method} {path}"})

    def _media(self, item_id: str, media_id: str, headers: dict, thumb: bool = False) -> None:
        """The original file while it exists on this machine, else the archived JPEG. Supports
        Range requests so the panel's <video> can seek. `?variant=thumb` (a PC-only extension the
        panel uses) prefers the archived JPEG, so video bubbles get their frame sheet."""
        store = self.app.store
        ref = store.media_ref(item_id, media_id)
        resolved = store.resolve_media(ref, prefer_archive=thumb) if ref else None
        if resolved is None:
            self._json(404, {"ok": False, "error": "no such media"})
            return
        path, _ = resolved
        try:
            size = os.path.getsize(path)
        except OSError:
            self._json(404, {"ok": False, "error": "no such media"})
            return
        ctype = content_type(path)
        rng = headers.get("range", "")
        if rng.startswith("bytes=") and "," not in rng:
            start_s, _, end_s = rng[6:].partition("-")
            try:
                if start_s:
                    start = int(start_s)
                    end = min(int(end_s), size - 1) if end_s else size - 1
                else:
                    start = max(size - int(end_s), 0)
                    end = size - 1
            except ValueError:
                start, end = 0, -1
            if start > end or start >= size:
                self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
                return
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(end - start + 1)
            self._send(206, data, ctype, {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"})
            return
        if size > 256 << 20:
            self._json(413, {"ok": False, "error": "file too large; use a Range request"})
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # Windows: SO_REUSEADDR would let two trackers share the port


class CommandServer:
    def __init__(self, app: "TrackerApp", port: int, host: str = "127.0.0.1") -> None:
        handler = type("Handler", (_Handler,), {"app": app})
        self.httpd = _Server((host, port), handler)
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="tracker-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def inbox_scan(folder, failed_dir, on_command: Callable[[dict], None], on_bad: Callable[[str, str], None]) -> int:
    """Applies queued command files in order; unreadable ones move to failed/."""
    from .model import inbox_pending

    if not _owned_by_me(folder):
        return 0
    applied = 0
    for file in inbox_pending(folder):
        try:
            data = file.read_bytes()
        except OSError:
            continue
        try:
            cmd = parse_command(json.loads(data))
        except (ValueError, CommandError) as exc:
            try:
                failed_dir.mkdir(parents=True, exist_ok=True)
                os.replace(file, failed_dir / file.name)
            except OSError:
                try:
                    file.unlink()
                except OSError:
                    pass
            on_bad(file.name, str(exc))
            continue
        try:
            file.unlink()
        except OSError:
            pass
        on_command(cmd)
        applied += 1
    return applied


def _owned_by_me(folder) -> bool:
    """Only folders this user owns: another account could create the shared /tmp fallback first."""
    try:
        st = os.stat(folder)
    except OSError:
        return False
    if hasattr(os, "getuid"):
        return st.st_uid == os.getuid()
    return True  # Windows: the fallback lives in the user's own %TEMP%
