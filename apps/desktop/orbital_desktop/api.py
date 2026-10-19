"""The API the UI calls: `window.pywebview.api.<method>()` in the app, POST /api/<method> in --browser mode.

Every method returns a JSON-able dict: {"ok": true, ...} or {"ok": false, "error": "...", "status": n}.
Live events (websocket /v1/live, connection state) are queued here; the UI long-polls next_events().
The session token stays in this object (and the OS keyring); it is never returned to the UI.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import queue
import threading
import time
from urllib.parse import quote

from . import storage
from .hub import HubClient, HubError, discover, local_auto_pair, login, normalize_hub, pair, parse_pair_link

log = logging.getLogger("orbital_desktop")
MAX_LOG_CHARS = 200_000
MAX_MEDIA_CACHE = 40_000_000     # data: URLs kept in memory, oldest dropped first


def _q(part: str) -> str:
    return quote(str(part), safe="")


def safe(fn):
    """Turn exceptions into {"ok": false, "error": ...} so the UI has one error path."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            out = fn(self, *args, **kwargs)
            if isinstance(out, dict):
                return {"ok": True, **out}
            return {"ok": True, "result": out}
        except HubError as exc:
            if exc.status == 401 and self._client is not None and fn.__name__ not in ("sign_in_password", "sign_in_link"):
                # The session was revoked on the hub: drop it and send the UI back to sign-in.
                self._forget_session()
                self._emit({"type": "signed_out", "reason": "The hub ended this session. Sign in again."})
            return {"ok": False, "error": str(exc), "status": exc.status, "code": exc.code}
        except Exception as exc:  # never let a UI call crash the bridge
            log.exception("api %s failed", fn.__name__)
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    return wrapper


class Api:
    def __init__(self, pair_link: str | None = None, hub_hint: str | None = None, no_auto: bool = False) -> None:
        self._pending_link = pair_link
        self._hub_hint = hub_hint
        self._no_auto = no_auto
        self._client: HubClient | None = None
        self._username: str | None = None
        self._hub_id: str | None = None
        self._events: queue.Queue = queue.Queue()
        self._live: LiveSocket | None = None
        self._lock = threading.RLock()
        self._route = ""
        self._media: dict[str, str] = {}
        self._media_bytes = 0
        self._token_store = ""

    # Session plumbing (not exposed: leading underscore)

    def _emit(self, event: dict) -> None:
        self._events.put(event)

    def _start_session(self, hub: str, token: str, username: str | None, hub_id: str | None, method: str) -> dict:
        with self._lock:
            self._stop_live()
            if self._client is not None:
                self._client.close()
            self._client = HubClient(hub, token)
            self._username = username
            self._hub_id = hub_id
            where = storage.save_token(hub, token)
            self._token_store = where
            settings = storage.load_settings()
            settings.update({"hub": hub, "username": username, "hub_id": hub_id, "signed_in_with": method})
            storage.save_settings(settings)
            self._live = LiveSocket(self._client, self._emit)
            self._live.start()
            log.info("signed in to %s via %s (token in %s)", hub, method, where)
            return self._session(method=method, token_store=where)

    def _session(self, **extra) -> dict:
        return {"signed_in": True, "hub": self._client.hub if self._client else None,
                "username": self._username, "hub_id": self._hub_id, "route": self._route, **extra}

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _forget_session(self) -> None:
        with self._lock:
            self._stop_live()
            if self._client is not None:
                storage.clear_token(self._client.hub)
                self._client.close()
            self._client = None

    def _need(self) -> HubClient:
        if self._client is None:
            raise HubError("Not signed in", 401, "unauthorized")
        return self._client

    def _shutdown(self) -> None:
        self._stop_live()

    # Sign-in

    @safe
    def bootstrap(self) -> dict:
        """Called once by the UI: pairing link → stored session → local auto-pair → sign-in form."""
        settings = storage.load_settings()
        error = None
        if self._pending_link:
            link, self._pending_link = self._pending_link, None
            try:
                hub, code = parse_pair_link(link)
                answer = pair(hub, code)
                return self._start_session(hub, answer["token"], answer.get("username"), answer.get("hub_id"), "pairing link")
            except HubError as exc:
                error = str(exc)
        hub = settings.get("hub")
        if hub and not error:
            token = storage.load_token(hub)
            if token:
                client = HubClient(hub, token)
                try:
                    client.get("/v1/satellites")
                    client.close()
                    return self._start_session(hub, token, settings.get("username"), settings.get("hub_id"), "saved session")
                except HubError as exc:
                    client.close()
                    if exc.status == 401:
                        storage.clear_token(hub)
                    else:
                        # Hub unreachable right now: keep the session and let live/poll retry.
                        out = self._start_session(hub, token, settings.get("username"), settings.get("hub_id"), "saved session")
                        out["warning"] = str(exc)
                        return out
        if not self._no_auto and not error:
            try:
                answer = local_auto_pair()
            except HubError as exc:
                answer, error = None, f"Automatic sign-in on this computer failed: {exc}"
            if answer:
                return self._start_session(answer["hub"], answer["token"], answer.get("username"), answer.get("hub_id"), "this computer's hub")
        return {"signed_in": False, "hub": self._hub_hint or settings.get("hub") or "",
                "username": settings.get("username") or "", "error": error}

    @safe
    def discover(self) -> dict:
        return {"hubs": discover()}

    @safe
    def sign_in_link(self, link: str) -> dict:
        hub, code = parse_pair_link(link)
        answer = pair(hub, code)
        return self._start_session(hub, answer["token"], answer.get("username"), answer.get("hub_id"), "pairing link")

    @safe
    def sign_in_password(self, hub: str, username: str, password: str) -> dict:
        hub = normalize_hub(hub)
        if not username or not password:
            raise HubError("Enter the username and password")
        answer = login(hub, username.strip(), password)
        password = None  # noqa: F841 — not kept
        return self._start_session(hub, answer["token"], answer.get("username") or username.strip(), answer.get("hub_id"), "password")

    @safe
    def sign_in_local(self) -> dict:
        answer = local_auto_pair()
        if not answer:
            raise HubError("No hub on this computer, or its secrets.env isn't readable by you")
        return self._start_session(answer["hub"], answer["token"], answer.get("username"), answer.get("hub_id"), "this computer's hub")

    @safe
    def sign_out(self) -> dict:
        client = self._client
        if client is not None:
            client.logout()
        self._forget_session()
        return {"signed_in": False}

    @safe
    def session(self) -> dict:
        if self._client is None:
            return {"signed_in": False}
        return self._session(live=bool(self._live and self._live.connected))

    # Live events

    def next_events(self, timeout: float = 25) -> dict:
        """Block up to `timeout` s for live events; returns {"events": [...]} (possibly empty)."""
        events = []
        try:
            events.append(self._events.get(timeout=max(0.1, min(float(timeout or 25), 55))))
            while len(events) < 50:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return {"ok": True, "events": events}

    # Data

    @safe
    def hub_info(self) -> dict:
        return {"info": self._need().get("/v1/hub/info")}

    @safe
    def satellites(self) -> dict:
        data = self._need().get("/v1/satellites")
        if data.get("hub_id"):
            self._hub_id = data["hub_id"]
        return {"satellites": data.get("satellites") or [], "hub_id": data.get("hub_id"), "fetched_at": time.time()}

    @safe
    def jobs(self) -> dict:
        data = self._need().get("/v1/agents/jobs")
        return {"jobs": data.get("jobs") or [], "sources": data.get("satellites") or []}

    @safe
    def machine_jobs(self, satellite_id: str) -> dict:
        data = self._need().get(f"/v1/satellites/{_q(satellite_id)}/agents/jobs")
        return {"jobs": data.get("jobs") or []}

    @safe
    def job(self, satellite_id: str, job_id: str) -> dict:
        return {"job": self._need().get(f"/v1/satellites/{_q(satellite_id)}/agents/jobs/{_q(job_id)}")}

    @safe
    def job_log(self, satellite_id: str, job_id: str) -> dict:
        data = self._need().get(f"/v1/satellites/{_q(satellite_id)}/agents/jobs/{_q(job_id)}/log")
        text = str(data.get("log") or "")
        clipped = len(text) > MAX_LOG_CHARS
        return {"log": text[-MAX_LOG_CHARS:], "bytes": data.get("bytes"),
                "truncated": bool(data.get("truncated")) or clipped, "status": data.get("status")}

    @safe
    def cancel_job(self, satellite_id: str, job_id: str) -> dict:
        return {"result": self._need().post(f"/v1/satellites/{_q(satellite_id)}/agents/jobs/{_q(job_id)}/cancel")}

    @safe
    def run_agent(self, satellite_id: str, agent: str, prompt: str, cwd: str = "", title: str = "") -> dict:
        if not (prompt or "").strip():
            raise HubError("Write a prompt for the agent")
        body = {"prompt": prompt, "by": "desktop"}
        for key, value in (("agent", agent), ("cwd", cwd), ("title", title)):
            if value and str(value).strip():
                body[key] = str(value).strip()
        answer = self._need().post(f"/v1/satellites/{_q(satellite_id)}/agents/run", body, timeout=25)
        return {"job": answer}

    @safe
    def ping(self, satellite_id: str) -> dict:
        return {"ping": self._need().get(f"/v1/satellites/{_q(satellite_id)}/ping")}

    # Navigation, spaces and sections (the same payloads the iPhone/Mac app reads)

    @safe
    def nav(self) -> dict:
        """GET /v1/nav → {nav: orbital.nav.v1 doc, default: bool}."""
        data = self._need().get("/v1/nav")
        return {"nav": data.get("nav") or {"schema": "orbital.nav.v1", "tabs": [], "sidebar": None},
                "default": bool(data.get("default"))}

    @safe
    def reset_nav(self) -> dict:
        """DELETE /v1/nav: back to the default layout (every app updates over the live socket)."""
        return {"result": self._need().delete("/v1/nav")}

    @safe
    def open_url(self, url: str) -> dict:
        """Open an https:// link in the system browser (pywebview would open it inside the app)."""
        import webbrowser

        url = str(url or "")
        if not url.startswith("https://"):
            raise HubError("Only https:// links open in the browser")
        webbrowser.open(url)
        return {}

    @safe
    def catalog(self) -> dict:
        """GET /v1/catalog → {sections: [...], spaces: [...]} (declared spaces first, with aliases)."""
        data = self._need().get("/v1/catalog")
        return {"sections": data.get("sections") or [], "spaces": data.get("spaces") or []}

    @safe
    def section(self, section_id: str) -> dict:
        return {"section": self._need().get(f"/v1/sections/{_q(section_id)}")}

    @safe
    def section_action(self, section_id: str, body: dict) -> dict:
        """A button or to-do on a section page: POST /v1/sections/<id>/actions."""
        if not isinstance(body, dict):
            raise HubError("Bad action")
        return {"result": self._need().post(f"/v1/sections/{_q(section_id)}/actions", body, timeout=30)}

    @safe
    def media(self, path: str) -> dict:
        """An uploaded image (/v1/media/<id>, /v1/tracker/media/...) as a data: URL; the page can't send the token."""
        path = str(path or "")
        if not (path.startswith("/v1/media/") or path.startswith("/v1/tracker/media/")):
            raise HubError("Not a hub media path")
        with self._lock:
            hit = self._media.get(path)
        if hit:
            return {"url": hit}
        mime, raw = self._need().raw(path)
        url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        with self._lock:
            self._media[path] = url
            self._media_bytes += len(url)
            while self._media_bytes > MAX_MEDIA_CACHE and self._media:
                old = next(iter(self._media))
                self._media_bytes -= len(self._media.pop(old))
        return {"url": url}

    # System: automations (the scheduler's feeds), hub details, tracker, the Agent desk

    @safe
    def feeds(self) -> dict:
        data = self._need().get("/v1/jobs")
        return {k: data.get(k) for k in ("scheduler", "groups", "jobs", "lanes", "disk", "batch", "server_time")}

    @safe
    def run_feed(self, job_id: str) -> dict:
        return {"result": self._need().post(f"/v1/jobs/{_q(job_id)}/run")}

    @safe
    def run_all_feeds(self, group: str = "") -> dict:
        return {"result": self._need().post("/v1/jobs/run-all", {"group": group} if group else {})}

    @safe
    def feed_log(self, job_id: str) -> dict:
        return {"log": self._need().text(f"/v1/jobs/{_q(job_id)}/log")[-MAX_LOG_CHARS:]}

    @safe
    def tracker(self) -> dict:
        data = self._need().get("/v1/tracker/items")
        try:
            settings = self._need().get("/v1/tracker/settings")
        except HubError:
            settings = {}
        return {"items": data.get("items") or [], "settings": settings}

    @safe
    def tracker_item(self, item_id: str) -> dict:
        return {"item": self._need().get(f"/v1/tracker/items/{_q(item_id)}")}

    @safe
    def desk(self) -> dict:
        data = self._need().get("/v1/desk")
        return {"messages": (data.get("messages") or [])[-200:], "model": data.get("model"),
                "tasks": data.get("tasks") or [], "available": data.get("available", True)}

    @safe
    def desk_say(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise HubError("Write a message")
        # The hub streams NDJSON while the model answers; the reply also arrives as a live desk_message.
        raw = self._need().post("/v1/desk/say", {"text": text}, timeout=180)
        events = []
        for line in (raw if isinstance(raw, str) else json.dumps(raw)).splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return {"events": events[-50:]}

    @safe
    def app_info(self) -> dict:
        import platform

        from . import __version__
        return {"version": __version__, "platform": platform.platform(terse=True), "python": platform.python_version(),
                "token_store": {"keyring": "the system keychain", "file": "a private file"}.get(self._token_store, "–"),
                "data_dir": str(storage.config_dir())}

    @safe
    def alerts(self, status: str = "pending") -> dict:
        data = self._need().get("/v1/alerts", status=status if status in ("pending", "all") else "pending", limit=200)
        return {"alerts": data.get("alerts") or []}

    @safe
    def ack_alerts(self, ids: list) -> dict:
        ids = [str(i) for i in (ids or [])][:200]
        return {"result": self._need().post("/v1/alerts/ack", {"ids": ids})}


class LiveSocket(threading.Thread):
    """GET /v1/live with the Bearer token; reconnects with backoff; forwards every message as an event."""

    def __init__(self, client: HubClient, emit) -> None:
        super().__init__(name="orbital-live", daemon=True)
        self._client = client
        self._emit = emit
        self._stop = threading.Event()
        self._ws = None
        self.connected = False

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _state(self, connected: bool, detail: str = "") -> None:
        if connected != self.connected or detail:
            self.connected = connected
            self._emit({"type": "live", "connected": connected, "detail": detail})

    def run(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            self._emit({"type": "live", "connected": False, "detail": "websockets package missing; polling only"})
            return
        delay = 1.0
        while not self._stop.is_set():
            try:
                with connect(self._client.ws_url, additional_headers=self._client.auth_header(),
                             open_timeout=8, close_timeout=2, ping_interval=25, max_size=8 * 1024 * 1024) as ws:
                    self._ws = ws
                    self._state(True)
                    delay = 1.0
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=30)
                        except TimeoutError:
                            ws.send("ping")
                            continue
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(message, dict) and message.get("type") not in ("ping", None):
                            self._emit(message)
            except Exception as exc:
                if self._stop.is_set():
                    break
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                if code == 4401:
                    self._state(False, "The hub refused the live connection (session ended)")
                    self._emit({"type": "signed_out", "reason": "The hub ended this session. Sign in again."})
                    return
                self._state(False, f"Live updates reconnecting ({exc.__class__.__name__})")
            finally:
                self._ws = None
            self._stop.wait(delay)
            delay = min(delay * 2, 30.0)
        self.connected = False
