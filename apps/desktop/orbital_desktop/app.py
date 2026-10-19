"""Start the desktop app: `python run.py` (or `python -m orbital_desktop`).

  python run.py                              open the window; signs in by itself when it can
  python run.py "orbital://connect?hub=…&pair=…"   pair with a link (also --pair-link LINK)
  python run.py --hub http://macbook.tailnet.ts.net:7700   pre-fill the hub address
  python run.py --browser                    no WebView available: serve the UI to your browser instead
  python run.py --sign-out                   forget the saved session and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from . import APP_NAME, __version__, storage
from .api import Api

# Also right under PyInstaller: the spec bundles ui/ next to the frozen package.
UI_DIR = Path(__file__).resolve().parent / "ui"


def _setup_logging(debug: bool) -> None:
    path = storage.config_dir() / "desktop.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("orbital_desktop")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(handler)
    # httpx logs request URLs only (never headers); keep it quiet unless debugging.
    logging.getLogger("httpx").setLevel(logging.DEBUG if debug else logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="orbital-desktop", description=f"{APP_NAME} desktop app {__version__}")
    p.add_argument("link", nargs="?", help="an orbital://connect?hub=…&pair=… pairing link")
    p.add_argument("--pair-link", help="pairing link from `orbital pair --link` on the hub")
    p.add_argument("--hub", help="pre-fill the hub address on the sign-in screen")
    p.add_argument("--no-auto", action="store_true", help="don't sign in automatically with this computer's hub")
    p.add_argument("--browser", action="store_true", help="serve the UI on 127.0.0.1 and open it in your web browser")
    p.add_argument("--port", type=int, default=0, help="--browser: port to serve on (default: any free port)")
    p.add_argument("--no-open", action="store_true", help="--browser: print the URL instead of opening it")
    p.add_argument("--route", default="", help="start on a screen, e.g. machine/<id> or job/<machine>/<job>")
    p.add_argument("--sign-out", action="store_true", help="forget the saved session and exit")
    p.add_argument("--debug", action="store_true", help="WebView dev tools and debug logging")
    p.add_argument("--size", default="1280x840", help="window size WIDTHxHEIGHT")
    p.add_argument("--position", default="", help="window position X,Y (screen pixels from the top left)")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.debug)
    if args.sign_out:
        settings = storage.load_settings()
        if settings.get("hub"):
            storage.clear_token(settings["hub"])
        print("Signed out: the saved session was removed.")
        return 0
    link = args.pair_link or (args.link if args.link and "://" in args.link else None)
    api = Api(pair_link=link, hub_hint=args.hub, no_auto=args.no_auto)
    route = args.route.strip("#/ ")
    try:
        if args.browser:
            return serve_browser(api, args.port, route, open_it=not args.no_open)
        return open_window(api, args, route)
    finally:
        api._shutdown()


def open_window(api: Api, args: argparse.Namespace, route: str) -> int:
    try:
        import webview
    except ImportError:
        print("pywebview isn't installed. Run: pip install -r requirements.txt  (or start with --browser)", file=sys.stderr)
        return 2
    try:
        width, height = (int(v) for v in args.size.lower().split("x", 1))
    except ValueError:
        width, height = 1280, 840
    try:
        x, y = (int(v) for v in args.position.split(",", 1)) if args.position else (None, None)
    except ValueError:
        x, y = None, None
    url = str(UI_DIR / "index.html")
    api._route = route  # the UI asks bootstrap() for its first screen
    focus = os.environ.get("ORBITAL_DESKTOP_NOFOCUS") != "1"
    webview.create_window(APP_NAME, url=url, js_api=api, width=width, height=height, x=x, y=y,
                          min_size=(760, 560), background_color="#0e1116", focus=focus)
    try:
        webview.start(debug=args.debug, private_mode=True)
    except Exception as exc:  # no GUI backend (e.g. Linux without GTK/Qt)
        print(f"Couldn't open a WebView window ({exc}). Try: python run.py --browser", file=sys.stderr)
        return 2
    return 0


def serve_browser(api: Api, port: int, route: str, open_it: bool = True) -> int:
    """Fallback without a WebView: the same UI in the default browser, bound to 127.0.0.1 only.

    Each run makes a random key; the page sends it on every /api call, and the Host header must be
    this loopback address (no DNS rebinding). The hub token still never leaves Python.
    """
    key = secrets.token_urlsafe(24)
    exposed = {name for name in dir(Api) if not name.startswith("_") and callable(getattr(Api, name))}

    class Handler(BaseHTTPRequestHandler):
        server_version = "OrbitalDesktop"

        def log_message(self, *a) -> None:  # quiet console
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = urlparse(self.path).path
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            root = UI_DIR.resolve()
            target = (root / rel).resolve()
            if root not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Orbital-Key", ""), key):
                return self._send(403, b'{"ok":false,"error":"forbidden"}', "application/json")
            name = urlparse(self.path).path.removeprefix("/api/")
            if name not in exposed:
                return self._send(404, b'{"ok":false,"error":"no such call"}', "application/json")
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
                params = json.loads(self.rfile.read(length) or b"[]")
                params = params if isinstance(params, list) else []
                out = getattr(api, name)(*params)
            except Exception as exc:
                out = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
            self._send(200, json.dumps(out).encode(), "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/?key={key}" + (f"#/{route}" if route else "")
    print(f"{APP_NAME} is running at {url}\nPress Ctrl+C to stop.", flush=True)
    if open_it:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
