"""The floating panel: a frameless, always-on-top, transparent pywebview window
(WebView2 on Windows, GTK/Qt WebKit on Linux, WebKit on macOS).

The page never talks to the HTTP API with fetch(): that API refuses browser requests (any Origin
header). It reads state through the pywebview JS bridge (`Api` below); only <img>/<video> load
media from GET /v1/media/<item>/<media>, which plain media requests are allowed to do.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import PRODUCT_NAME, VERSION
from ..config import platform_name
from ..model import dumps, evidence_level, valid_id, write_atomic

if TYPE_CHECKING:
    from ..app import TrackerApp

HERE = Path(__file__).resolve().parent
WIDTH = 500
MIN_HEIGHT = 64


class Api:
    """Called from JavaScript as window.pywebview.api.<name>(...). Runs on pywebview's threads."""

    def __init__(self, app: "TrackerApp", panel: "Panel") -> None:
        self._app = app
        self._panel = panel
        self._icons: dict[str, str | None] = {}

    def poll(self, version: str) -> dict | None:
        """The whole state when anything changed since `version`, else None."""
        app = self._app
        current = f"{app.store.version}:{app.settings_version}"
        if version == current:
            return None
        toast = None
        if app.toast and time.time() - app.toast[1] < 4:
            toast = {"text": app.toast[0], "at": app.toast[1], "age": time.time() - app.toast[1]}
        return {
            "version": current,
            "items": app.store.snapshot(all_items=True),
            "settings": app.settings.json(),
            "toast": toast,
            "machine": app.cfg.machine_name,
            "role": app.cfg.role,
            "port": app.port,
            "platform": platform_name(),
            "hubUrl": app.cfg.hub_url,
            "relay": app.cfg.relays_to_hub,
            "dataDir": str(app.paths.root),
            "version_app": VERSION,
            "prefs": self._panel.prefs,
        }

    def icon(self, agent: str) -> str | None:
        """data: URL of <tracker data>/icons/<agent>.png|jpg when someone dropped a logo there."""
        if not valid_id(agent):
            return None
        if agent in self._icons:
            return self._icons[agent]
        found = None
        for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("svg", "image/svg+xml")):
            path = self._app.paths.icons_dir / f"{agent}.{ext}"
            if path.is_file() and path.stat().st_size < 1 << 20:
                found = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
                break
        self._icons[agent] = found
        return found

    def set_evidence(self, level: str) -> bool:
        lvl = evidence_level(level)
        if lvl:
            self._app.set_evidence(lvl, "tracker")
        return bool(lvl)

    def close_item(self, item_id: str) -> None:
        self._app.store.user_close(item_id)

    def mark_done(self, item_id: str) -> None:
        self._app.store.mark_done(item_id)

    def add_item(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text:
            return None
        title = text if len(text.split()) <= 5 else " ".join(text.split()[:5]) + "…"
        return self._app.store.add_personal(title, text)

    def fit(self, height: float) -> None:
        self._panel.fit(height)

    def save_prefs(self, prefs: dict) -> None:
        self._panel.save_prefs(prefs)

    def open_path(self, which: str) -> None:
        paths = self._app.paths
        target = {"data": paths.root, "log": paths.log_file if paths.log_file.exists() else paths.logs_dir}.get(which)
        if target is not None:
            _open(str(target))

    def open_url(self, url: str) -> None:
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            import webbrowser

            webbrowser.open(url)

    def open_media(self, owner: str, media_id: str) -> None:
        """Opens the original (or the archive) in the system viewer."""
        ref = self._app.store.media_ref(owner, media_id)
        resolved = self._app.store.resolve_media(ref) if ref else None
        if resolved:
            _open(str(resolved[0]))

    def hide(self) -> None:
        self._panel.hide()

    def quit(self) -> None:
        self._panel.quit()


def _open(target: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError:
        pass


class Panel:
    def __init__(self, app: "TrackerApp") -> None:
        self.app = app
        self.window: Any = None
        self.prefs_file = app.paths.root / "panel-pc.json"
        try:
            self.prefs: dict = json.loads(self.prefs_file.read_text(encoding="utf-8"))
            if not isinstance(self.prefs, dict):
                self.prefs = {}
        except (OSError, ValueError):
            self.prefs = {}
        self._height = 0
        self._lock = threading.Lock()

    def save_prefs(self, prefs: dict) -> None:
        if not isinstance(prefs, dict):
            return
        with self._lock:
            self.prefs.update({k: v for k, v in prefs.items() if k in ("filter", "pageSize", "x", "y", "range")})
            try:
                write_atomic(self.prefs_file, dumps(self.prefs, pretty=True))
            except OSError:
                pass

    def fit(self, height: float) -> None:
        if self.window is None:
            return
        try:
            screen_h = _screen_height()
            h = int(max(MIN_HEIGHT, min(float(height), screen_h * 0.88)))
        except (TypeError, ValueError):
            return
        if abs(h - self._height) < 2:
            return
        self._height = h
        try:
            self.window.resize(WIDTH, h)
        except Exception:  # noqa: BLE001
            pass

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()

    def quit(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001
                pass

    def geometry(self) -> dict:
        w = self.window
        try:
            return {"x": w.x, "y": w.y, "width": w.width, "height": w.height}
        except Exception:  # noqa: BLE001
            return {}

    def debug(self, obj: dict) -> dict:
        """POST /v1/debug/ui: {"view": "active|history", "select": id, "expand": id, "range": "week",
        "filter": "all|ai|mine", "query": "text", "page": 0, "pageSize": 10, "menu": "evidence|more|",
        "typing": true, "lightbox": "<owner>/<media>", "showPanel": true}."""
        if self.window is None:
            return {"ui": False}
        if obj.get("showPanel"):
            self.window.show()
        move = obj.get("move")
        if isinstance(move, list) and len(move) == 2 and all(isinstance(v, (int, float)) for v in move):
            self.window.move(int(move[0]), int(move[1]))
        payload = {k: v for k, v in obj.items() if k not in ("toast", "move", "showPanel")}
        result = None
        if payload:
            try:
                result = self.window.evaluate_js(f"window.trackerDebug({json.dumps(payload)})")
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc)}
        out = {"ui": True, "panel": self.geometry(), "pid": os.getpid()}
        if result is not None:
            out["page"] = result
        return out


def _screen_height() -> float:
    try:
        import webview

        screens = webview.screens
        if screens:
            return float(screens[0].height)
    except Exception:  # noqa: BLE001
        pass
    return 900.0


def _float_on_all_spaces(window: Any) -> None:
    """macOS only (where this port runs for testing): like the Swift panel, show on every Space and
    over full-screen apps without activating. Windows and Linux need nothing here: on_top is enough."""
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper

        def apply() -> None:
            try:
                ns = window.native
                if not hasattr(ns, "setCollectionBehavior_"):
                    ns = ns.window()
                # canJoinAllSpaces | fullScreenAuxiliary | ignoresCycle
                ns.setCollectionBehavior_((1 << 0) | (1 << 8) | (1 << 6))
                ns.setHidesOnDeactivate_(False)
                ns.orderFrontRegardless()
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"tracker: couldn't pin the panel to all Spaces: {exc}\n")

        AppHelper.callAfter(apply)
    except Exception:  # noqa: BLE001
        pass


def _accepts_focus() -> bool:
    """Windows and Linux: yes, or the "+" box couldn't take typing (pywebview can't switch it later);
    the panel only takes focus once, when it opens at login. macOS (a test platform for this port):
    no, so test runs never steal focus. ORBITAL_TRACKER_FOCUS=0/1 overrides."""
    raw = os.environ.get("ORBITAL_TRACKER_FOCUS")
    if raw in ("0", "1"):
        return raw == "1"
    return sys.platform != "darwin"


def _start_position(prefs: dict, webview: Any) -> tuple[int, int]:
    """Where the panel goes: the remembered spot when it is still on a screen, else the top-right
    corner of the first screen. A position off every screen would put the window nowhere (and
    pywebview's macOS backend crashes on it), so it is never trusted blindly."""
    try:
        screens = [(int(s.x), int(s.y), int(s.width), int(s.height)) for s in webview.screens]
    except Exception:  # noqa: BLE001
        screens = []
    first = screens[0] if screens else (0, 0, 1440, 900)
    default = (max(first[0] + first[2] - WIDTH - 16, first[0]), first[1] + 36)
    x, y = prefs.get("x"), prefs.get("y")
    # macOS is only a test platform here, and pywebview reports moves there in mixed coordinates.
    if sys.platform == "darwin" or not isinstance(x, int) or not isinstance(y, int):
        return default
    for sx, sy, sw, sh in screens:
        if sx - 20 <= x <= sx + sw - 60 and sy - 20 <= y <= sy + sh - 40:
            return x, y
    return default


def run_panel(app: "TrackerApp") -> None:
    """Creates the window and runs the GUI loop on this (the main) thread."""
    import webview

    panel = Panel(app)
    app.ui = panel
    api = Api(app, panel)
    x, y = _start_position(panel.prefs, webview)
    html = (HERE / "panel.html").read_text(encoding="utf-8")
    html = html.replace("/*__CSS__*/", (HERE / "panel.css").read_text(encoding="utf-8"))
    html = html.replace("/*__JS__*/", (HERE / "panel.js").read_text(encoding="utf-8"))
    window = webview.create_window(
        PRODUCT_NAME, html=html, js_api=api, width=WIDTH, height=120, x=x, y=y,
        resizable=False, min_size=(200, 40), text_select=True, frameless=True, easy_drag=False, on_top=True, transparent=True,
        background_color="#26242a", focus=_accepts_focus(), shadow=False,
    )
    panel.window = window

    def on_moved(*args: Any) -> None:
        try:
            panel.save_prefs({"x": int(window.x), "y": int(window.y)})
        except Exception:  # noqa: BLE001
            pass

    try:
        window.events.moved += on_moved
        window.events.shown += lambda *a: _float_on_all_spaces(window)
    except Exception:  # noqa: BLE001
        pass
    gui = os.environ.get("ORBITAL_TRACKER_GUI") or None  # e.g. "qt" or "gtk" on Linux
    webview.start(gui=gui, debug=os.environ.get("ORBITAL_TRACKER_DEVTOOLS") == "1")
