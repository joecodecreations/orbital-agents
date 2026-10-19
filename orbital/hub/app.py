"""The Orbital hub: the data API the apps read, the satellite registry and relay, the tracker view
and relay, and the Desk (the app's voice and text tab). Contract: docs/ARCHITECTURE.md.

    from orbital.hub import create_app
    app = create_app()            # settings from ~/.orbital-agents/config.toml, data in data/hub

Every request, HTTP or websocket, is first checked against hub.allow_networks (loopback and
Tailscale by default). Then: GET /v1/health and GET /v1/hub/info are open; satellites use their
join token or hub_token; everything else needs an app session or the automation token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile as StarletteUpload

from orbital import __version__
from orbital.core import Config, get_logger, load_config
from orbital.core.secrets import Secrets
from orbital.hub import desk as desk_module
from orbital.hub import satellites as satellites_module
from orbital.hub import speech as speech_module
from orbital.hub import tracker as tracker_module
from orbital.hub.actions import ActionStore, validate_action
from orbital.hub.apns import APNs
from orbital.hub.auth import HubAuth, Sessions, bearer
from orbital.hub.contract import build_contract, render_warnings
from orbital.hub.desk import Desk
from orbital.hub.jobs import Jobs
from orbital.hub.live import LiveHub
from orbital.hub.ops import OpsError, validate_section
from orbital.hub.pulse import PulseStore
from orbital.hub.satellites import Registry
from orbital.hub.speech import FishVoices, SpeechModels
from orbital.hub.store import Store, now_iso
from orbital.hub.tracker import TrackerEvents, TrackerFeed
from orbital.hub.web import AllowNetworks, body_of, device_of, fail

ALLOWED_MEDIA = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAINTENANCE_EVERY = 24 * 3600
APNS_TOKEN = re.compile(r"^[0-9A-Fa-f]{32,200}$")


def _identity(path: Path) -> dict:
    """This hub's id (for discovery) and the key that signs tracker media URLs. Made once, kept."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("hub_id") and data.get("media_key"):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    data = {"hub_id": f"hub-{secrets.token_hex(6)}", "media_key": secrets.token_urlsafe(32), "created_at": now_iso()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    return data


class Hub:
    """Everything the routes share. Feature modules get this object and read what they need."""

    def __init__(self, cfg: Config, *, background: bool = True, clock=time.time) -> None:
        self.cfg = cfg
        self.paths = cfg.paths.ensure()
        self.paths.hub_data.mkdir(parents=True, exist_ok=True)
        self.background = background
        self.networks = [str(n) for n in cfg.get("hub.allow_networks") or []]
        self.max_media = int(cfg.get("hub.max_media_mb", 8) or 8) * 1024 * 1024
        self.log = get_logger("hub", home=str(self.paths.home))
        identity = _identity(self.paths.hub_data / "identity.json")
        self.hub_id: str = identity["hub_id"]
        self.auth = HubAuth(Secrets(self.paths), Sessions(self.paths.hub_data / "sessions.json"))
        self.store = Store(self.paths.hub_data)
        self.pulses = PulseStore(self.paths.hub_data / "pulse.json")
        self.actions = ActionStore(self.paths.hub_data)
        self.jobs = Jobs(self.paths.scheduler_data)
        self.live = LiveHub()
        self.tracker = TrackerFeed(identity["media_key"], self.paths.tracker_data,
                                   port=int(cfg.get("tracker.port", 7701) or 0), run_dir=self.paths.run)
        self.satellites = Registry(self.paths.hub_data / "satellites.json",
                                   heartbeat_seconds=int(cfg.get("satellite.heartbeat_seconds", 60) or 60), clock=clock)
        self.join_window = satellites_module.JoinWindow(self.paths.hub_data / "join_window.json", clock=clock)
        self.voices = FishVoices(self.auth.get("FISH_API_KEY"), str(cfg.get("speech.fish_model") or ""),
                                 speech_module.parse_voices(cfg.get("speech.voices")))
        self.speech_models = SpeechModels(self.paths.hub_data)
        self.apns = APNs(cfg, self.auth.get, self.store, clock=clock)
        self.desk: Desk | None = None
        self.desk = Desk(self)
        self.tracker_events = TrackerEvents(self)
        self._rejected: dict[str, float] = {}
        self._background: set[asyncio.Task] = set()

    # Requests

    @staticmethod
    def token(request) -> str | None:
        return bearer(request)

    @staticmethod
    def client_ip(request) -> str | None:
        return request.client.host if request.client else None

    def require(self, request: Request) -> JSONResponse | None:
        if self.auth.authorized(request):
            return None
        return fail("unauthorized", 401, code="unauthorized")

    def require_machine(self, request: Request) -> JSONResponse | None:
        """The app, the automation token, or a joined satellite's hub_token: for the machine list and the
        agent and browser relays, so `orbital agents` / `orbital browser` work from any joined machine."""
        if self.auth.authorized(request) or self.satellites.by_token(self.token(request)) is not None:
            return None
        return fail("unauthorized", 401, code="unauthorized")

    def rejected(self, ip: str | None, path: str) -> None:
        # One log line per address per minute, so a scanner can't fill the log.
        key = ip or "?"
        if time.time() - self._rejected.get(key, 0) > 60:
            self._rejected[key] = time.time()
            self.log.warning("request from a network outside hub.allow_networks", extra={"fields": {"ip": key, "path": path[:80]}})

    async def raise_alerts(self, alerts: list[dict]) -> list[str]:
        """Store alerts the hub itself raises (desk, tracker) and push the new ones."""
        try:
            ids, fresh = self.store.add_alerts(alerts)
        except OpsError as exc:
            self.log.warning("hub alert rejected", extra={"fields": {"error": str(exc)}})
            return []
        await self.notify(fresh)
        return ids

    async def notify(self, fresh: list[dict]) -> None:
        """New alerts: to every open app now, and to closed ones through Apple push in the background."""
        if not fresh:
            return
        await self.live.broadcast({"type": "alerts", "alerts": fresh})
        if self.apns.status()["enabled"]:
            self.spawn(self.apns.send_alerts(fresh))

    def spawn(self, coro) -> asyncio.Task:
        """A background task the hub keeps a reference to (and cancels at shutdown)."""
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def push_status(self) -> dict:
        state = self.apns.status()
        return {"enabled": state["enabled"], "configured": state["configured"], "environment": state["environment"]}

    def info(self) -> dict:
        return {
            "service": "orbital-hub",
            "name": self.cfg.get("system.name", "Orbital Agents"),
            "version": __version__,
            "hub_id": self.hub_id,
            "machine": self.cfg.machine_name,
            "apns": self.push_status(),
        }


def create_app(cfg: Config | None = None, *, background: bool = True, clock=time.time) -> FastAPI:
    cfg = cfg or load_config()
    hub = Hub(cfg, background=background, clock=clock)
    store, live = hub.store, hub.live

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.apns.status()  # one INFO line when push is wanted but something is missing
        from orbital.hub.bonjour import BonjourAdvertiser, tailscale_url
        port = int(cfg.get("hub.port", 7700) or 7700)
        url = await asyncio.to_thread(tailscale_url, port) if hub.background else ""
        bonjour = BonjourAdvertiser(port=port, hub_id=hub.hub_id, url=url)
        if hub.background:
            bonjour.start()
        tasks = [asyncio.create_task(live.ping_forever()), asyncio.create_task(satellites_module.watch(hub))]
        if hub.background:
            tasks += [
                asyncio.create_task(hub.desk.runner.run_forever()),
                asyncio.create_task(hub.desk.housekeeping()),
                asyncio.create_task(hub.tracker.watch(hub.tracker_events.changed, on_settings=hub.tracker_events.settings_changed)),
                asyncio.create_task(_maintenance_forever(cfg)),
            ]
        hub.log.info("hub started", extra={"fields": {"hub_id": hub.hub_id, "version": __version__}})
        try:
            yield
        finally:
            tasks += list(hub._background)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await hub.desk.close()
            await hub.voices.close()
            bonjour.stop()
            hub.log.info("hub stopped")

    app = FastAPI(title="Orbital hub", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(AllowNetworks, networks=hub.networks, on_reject=hub.rejected)
    app.state.hub = hub
    require = hub.require

    @app.exception_handler(Exception)
    async def crashed(request: Request, exc: Exception) -> JSONResponse:
        # The traceback goes to the hub log (no request body: it may hold a secret); the caller gets the usual error shape.
        hub.log.error("unhandled error", exc_info=exc, extra={"fields": {"path": request.url.path, "method": request.method}})
        return fail("the hub hit an internal error; see `orbital logs hub`", 500, code="rejected")

    def fail_ops(exc: OpsError, section_id: str | None = None) -> JSONResponse:
        revision = None
        include = exc.status == 409
        if include and section_id:
            current = store.get(section_id)
            revision = None if current is None else current.get("revision")
        return fail(str(exc), exc.status, section_id=section_id, revision=revision, include_revision=include)

    def ops_body(body: object, section_id: str) -> JSONResponse | tuple:
        if not isinstance(body, dict):
            return fail("body must be an object", 400, section_id=section_id)
        if body.get("schema") not in (None, "orbital.ops.v1"):
            return fail("schema must be orbital.ops.v1", 400, code="bad_schema", section_id=section_id)
        ops = body.get("ops")
        if not isinstance(ops, list):
            return fail("ops must be an array", 400, section_id=section_id)
        alerts = body.get("alerts") or []
        if not isinstance(alerts, list):
            return fail("alerts must be an array", 400, section_id=section_id)
        return ops, alerts, body.get("if_revision")

    async def json_or_fail(request: Request, section_id: str | None = None):
        try:
            return await request.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return fail("body must be JSON", 400, section_id=section_id)

    # Open routes

    @app.get("/v1/health")
    async def health() -> dict:
        return {"ok": True, "service": "orbital-hub", "version": __version__, "hub_id": hub.hub_id, "contract": "/v1/contract", "apns": hub.push_status()}

    @app.get("/v1/hub/info")
    async def hub_info() -> dict:
        return hub.info()

    @app.post("/v1/login")
    async def login(request: Request) -> JSONResponse:
        body = await body_of(request)
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not hub.auth.check_login(username, password):
            hub.log.info("sign-in refused", extra={"fields": {"ip": hub.client_ip(request)}})
            return fail("unauthorized", 401, code="bad_login")
        token = hub.auth.sessions.issue(username)
        hub.log.info("signed in", extra={"fields": {"ip": hub.client_ip(request), "device": device_of(request)}})
        return JSONResponse({"ok": True, "token": token, "username": username, "hub_id": hub.hub_id})

    @app.post("/v1/pair")
    async def pair(request: Request) -> JSONResponse:
        """Sign an app in with a one-time code from `orbital pair` (the orbital://connect link carries it)."""
        body = await body_of(request)
        username = hub.auth.pair_codes.redeem(str(body.get("code") or ""))
        if not username:
            hub.log.info("pairing refused", extra={"fields": {"ip": hub.client_ip(request)}})
            return fail("that pairing code is unknown, used or expired; run `orbital pair` again", 401, code="bad_login")
        token = hub.auth.sessions.issue(username)
        hub.log.info("paired", extra={"fields": {"ip": hub.client_ip(request), "device": device_of(request)}})
        return JSONResponse({"ok": True, "token": token, "username": username, "hub_id": hub.hub_id})

    @app.post("/v1/pair/new")
    async def pair_new(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        username = hub.auth.get("ORBITAL_HUB_USERNAME")
        if not username:
            return fail("ORBITAL_HUB_USERNAME is not set in secrets.env", 503, code="not_configured")
        code, expires = hub.auth.pair_codes.new(username)
        url = await asyncio.to_thread(cfg.external_hub_url)
        from urllib.parse import urlencode
        link = "orbital://connect?" + urlencode({"hub": url, "pair": code}) if url else ""
        return JSONResponse({"ok": True, "code": code, "hub": url, "link": link,
                             "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))})

    # Signed-in routes

    @app.post("/v1/logout")
    async def logout(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        hub.auth.sessions.drop(hub.token(request))
        return JSONResponse({"ok": True})

    @app.get("/v1/contract")
    async def contract(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = build_contract(store.catalog(), int(cfg.get("hub.max_media_mb", 8) or 8))
        body["apns"] = hub.push_status()
        return JSONResponse(body)

    @app.get("/v1/catalog")
    async def catalog(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        return JSONResponse(store.catalog())

    @app.get("/v1/sections/{section_id}")
    async def get_section(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        doc = store.get(section_id)
        if doc is None:
            return fail("not found", 404, section_id=section_id)
        return JSONResponse(doc)

    @app.put("/v1/sections/{section_id}")
    async def put_section(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = await json_or_fail(request, section_id)
        if isinstance(body, JSONResponse):
            return body
        if not isinstance(body, dict):
            return fail("section must be an object", 400, section_id=section_id)
        try:
            saved = store.put(section_id, body)
        except OpsError as exc:
            return fail_ops(exc, section_id)
        await live.broadcast({"type": "section", "section": saved})
        await live.broadcast({"type": "catalog"})
        return JSONResponse(saved)

    @app.delete("/v1/sections/{section_id}")
    async def delete_section(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        try:
            store.delete(section_id)
        except OpsError as exc:
            return fail_ops(exc, section_id)
        await live.broadcast({"type": "section_deleted", "id": section_id})
        await live.broadcast({"type": "catalog"})
        return JSONResponse({"ok": True, "id": section_id})

    @app.post("/v1/sections/{section_id}/ops")
    async def post_ops(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = await json_or_fail(request, section_id)
        if isinstance(body, JSONResponse):
            return body
        parsed = ops_body(body, section_id)
        if isinstance(parsed, JSONResponse):
            return parsed
        ops, alerts, if_revision = parsed
        try:
            result = store.apply_batch(section_id, ops, alerts, if_revision)
        except OpsError as exc:
            return fail_ops(exc, section_id)
        if result["deleted"]:
            await live.broadcast({"type": "section_deleted", "id": section_id})
            await live.broadcast({"type": "catalog"})
        elif result["mutated"] and result["section"] is not None:
            await live.broadcast({"type": "section", "section": result["section"]})
            await live.broadcast({"type": "catalog"})
        await hub.notify(result["fresh_alerts"])
        section = result["section"]
        return JSONResponse({
            "ok": True,
            "revision": None if section is None else section.get("revision"),
            "section": section,
            "alert_ids": result["alert_ids"],
        })

    @app.post("/v1/sections/{section_id}/validate")
    async def validate_section_request(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = await json_or_fail(request, section_id)
        if isinstance(body, JSONResponse):
            return body
        if not isinstance(body, dict):
            return fail("body must be an object", 400, section_id=section_id)
        try:
            if "section" in body and "ops" not in body:
                document = body["section"]
                if not isinstance(document, dict):
                    return fail("section must be an object", 400, section_id=section_id)
                document = dict(document)
                document["id"] = section_id
                for key in ("blocks", "tabs", "nodes"):
                    document.setdefault(key, [])
                validate_section(document)
                return JSONResponse({"ok": True, "would_write": False, "revision": None, "warnings": render_warnings(document)})
            parsed = ops_body(body, section_id)
            if isinstance(parsed, JSONResponse):
                return parsed
            ops, alerts, if_revision = parsed
            checked = store.validate_batch(section_id, ops, alerts, if_revision)
        except OpsError as exc:
            return fail_ops(exc, section_id)
        return JSONResponse({
            "ok": True,
            "would_write": checked["would_write"],
            "would_delete": checked["would_delete"],
            "revision": checked["revision"],
            "warnings": render_warnings(checked["document"]),
        })

    @app.post("/v1/pulse")
    async def post_pulse(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = await json_or_fail(request)
        if isinstance(body, JSONResponse):
            return body
        if not isinstance(body, dict):
            return fail("body must be an object", 400)
        if body.get("schema") not in (None, "orbital.pulse.v1"):
            return fail("schema must be orbital.pulse.v1", 400, code="bad_schema")
        items = body.get("items") or []
        remove = body.get("remove") or []
        if not isinstance(items, list) or not isinstance(remove, list):
            return fail("items and remove must be arrays", 400)
        known = {row["id"] for row in store.catalog()["sections"]}
        try:
            saved, removed = hub.pulses.apply(items, remove, known)
        except OpsError as exc:
            return fail_ops(exc)
        if saved or removed:
            await live.broadcast({"type": "pulse", "items": saved, "removed": removed})
        return JSONResponse({"ok": True, "keys": [item["key"] for item in saved], "removed": removed})

    @app.get("/v1/pulse")
    async def get_pulse(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        return JSONResponse({"items": hub.pulses.all()})

    @app.get("/v1/alerts")
    async def get_alerts(request: Request, status: str = "pending", limit: int = 200) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        if status not in {"pending", "all"}:
            return fail("status must be pending or all", 400)
        return JSONResponse({"alerts": store.query_alerts(status, max(1, min(limit, 500)))})

    @app.post("/v1/alerts")
    async def post_alerts(request: Request) -> JSONResponse:
        """Alerts from feeds, the CLI and the app; also from a joined satellite (its watchdog) with its hub_token."""
        satellite = None
        if not hub.auth.authorized(request):
            satellite = hub.satellites.by_token(hub.token(request))
            if satellite is None:
                return fail("unauthorized", 401, code="unauthorized")
        body = await body_of(request)
        alerts = body.get("alerts")
        if not isinstance(alerts, list):
            return fail("alerts must be an array", 400)
        if satellite is not None:
            alerts = [stamp_machine(alert, satellite) if isinstance(alert, dict) else alert for alert in alerts]
        try:
            ids, fresh = store.add_alerts(alerts)
        except OpsError as exc:
            return fail_ops(exc)
        await hub.notify(fresh)
        return JSONResponse({"ok": True, "alert_ids": ids})

    def id_list(body: dict) -> list[str] | None:
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            return None
        return ids

    @app.post("/v1/alerts/ack")
    async def ack_alerts(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        ids = id_list(await body_of(request))
        if ids is None:
            return fail("ids must be an array of strings", 400)
        done = store.ack(ids)
        if done:
            # Cleared on one device: every other device drops the row, the badge, and the notification.
            await live.broadcast({"type": "alerts_acked", "ids": done})
        return JSONResponse({"ok": True, "acked": len(done), "ids": done})

    @app.post("/v1/alerts/retract")
    async def retract_alerts(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        ids = id_list(await body_of(request))
        if ids is None:
            return fail("ids must be an array of strings", 400)
        done = store.retract(ids)
        if done:
            await live.broadcast({"type": "alerts_retracted", "ids": done})
        return JSONResponse({"ok": True, "retracted": done})

    @app.get("/v1/prefs")
    async def get_prefs(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        return JSONResponse({"prefs": store.load_prefs()})

    @app.post("/v1/prefs")
    async def post_prefs(request: Request) -> JSONResponse:
        """{"prefs": {"monthly_goal": 5000}}: settings every app shares. Null removes a key."""
        denied = require(request)
        if denied:
            return denied
        changes = (await body_of(request)).get("prefs")
        if not isinstance(changes, dict) or not changes:
            return fail("prefs must be a non-empty object", 400)
        try:
            prefs = store.merge_prefs(changes)
        except OpsError as exc:
            return fail_ops(exc)
        await live.broadcast({"type": "prefs", "prefs": prefs})
        return JSONResponse({"ok": True, "prefs": prefs})

    @app.get("/v1/spaces")
    async def get_spaces(request: Request) -> JSONResponse:
        """The hub's own spaces (spaces.json) and the merged list the apps draw."""
        denied = require(request)
        if denied:
            return denied
        return JSONResponse({"declared": store.declared_spaces(), "spaces": store.catalog()["spaces"]})

    @app.put("/v1/spaces")
    async def put_spaces(request: Request) -> JSONResponse:
        """{"spaces": [{id, title, icon?, accent?, order?, sections: [...], aliases: [...]}]}; [] removes them."""
        denied = require(request)
        if denied:
            return denied
        try:
            declared = store.save_spaces(await body_of(request))
        except OpsError as exc:
            return fail_ops(exc)
        await live.broadcast({"type": "catalog"})
        return JSONResponse({"ok": True, "declared": declared, "spaces": store.catalog()["spaces"]})

    @app.post("/v1/sections/{section_id}/actions")
    async def post_action(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        if store.get(section_id) is None:
            return fail(f"unknown section {section_id}", 404, section_id=section_id)
        body = await json_or_fail(request, section_id)
        if isinstance(body, JSONResponse):
            return body
        try:
            action = validate_action(body)
            stored = hub.actions.add(section_id, action, device=request.headers.get("x-orbital-device"))
            saved = store.patch_for_action(section_id, stored)
        except OpsError as exc:
            return fail_ops(exc, section_id)
        if saved is not None:
            await live.broadcast({"type": "section", "section": saved})
            await live.broadcast({"type": "catalog"})
        await live.broadcast({"type": "action", "action": stored})
        section = saved or store.get(section_id)
        return JSONResponse({
            "ok": True,
            "action": stored,
            "revision": None if section is None else section.get("revision"),
            "section": section,
        })

    @app.get("/v1/sections/{section_id}/actions")
    async def get_actions(section_id: str, request: Request, status: str = "pending", limit: int = 100) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        if status not in {"pending", "all"}:
            return fail("status must be pending or all", 400, section_id=section_id)
        try:
            rows = hub.actions.query(section_id, status, max(1, min(limit, 500)))
        except OpsError as exc:
            return fail_ops(exc, section_id)
        return JSONResponse({"actions": rows})

    @app.post("/v1/sections/{section_id}/actions/results")
    async def post_action_results(section_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        results = (await body_of(request)).get("results")
        try:
            changed = hub.actions.resolve(section_id, results)
        except OpsError as exc:
            return fail_ops(exc, section_id)
        for row in changed:
            await live.broadcast({"type": "action_result", "action": row})
        return JSONResponse({"ok": True, "resolved": [row["id"] for row in changed]})

    @app.post("/v1/media")
    async def upload_media(request: Request) -> JSONResponse:
        """multipart/form-data with one `file` field. Auth is checked before the body is read."""
        denied = require(request)
        if denied:
            return denied
        try:
            form = await request.form(max_part_size=hub.max_media + 1024 * 1024)
        except Exception:
            return fail("send the image as multipart/form-data with a file field", 400)
        try:
            file = form.get("file")
            if not isinstance(file, StarletteUpload):
                return fail("send the image as multipart/form-data with a file field", 400)
            content_type = (file.content_type or "").split(";")[0].strip().lower()
            if content_type not in ALLOWED_MEDIA:
                return fail("media must be jpeg, png, webp, or gif", 400)
            data = await file.read(hub.max_media + 1)
        finally:
            await form.close()
        if len(data) > hub.max_media:
            return fail(f"media is larger than {hub.max_media // (1024 * 1024)} MB", 413)
        if not data:
            return fail("media is empty", 400)
        return JSONResponse(store.save_media(data, content_type, file.filename or "image"))

    @app.get("/v1/media/{media_id}")
    async def get_media(media_id: str, request: Request):
        denied = require(request)
        if denied:
            return denied
        found = store.media_file(media_id)
        if found is None:
            return fail("not found", 404)
        path, content_type = found
        return FileResponse(path, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/v1/devices")
    async def register_device(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = await body_of(request)
        if not body.get("device_id"):
            return fail("device_id is required", 400)
        token = body.get("apns_token")
        if token is not None and token != "" and (not isinstance(token, str) or not APNS_TOKEN.match(token)):
            return fail("apns_token must be the device token as hex", 400)
        platform = str(body.get("platform") or "").lower()
        store.save_device({
            "device_id": str(body["device_id"])[:128],
            "name": str(body.get("name") or "")[:80],
            "platform": platform if platform in ("ios", "mac") else platform[:20],
            "apns_token": token.lower() if token else None,
        })
        return JSONResponse({"ok": True, "apns": hub.push_status()})

    @app.get("/v1/sync")
    async def sync(request: Request, known: str = "") -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        revisions: dict[str, int] = {}
        for piece in known.split(","):
            piece = piece.strip()
            if not piece or ":" not in piece:
                continue
            ident, rev = piece.rsplit(":", 1)
            try:
                revisions[ident] = int(rev)
            except ValueError:
                continue
        changed = [doc for doc in store.list_docs() if revisions.get(doc["id"]) is None or int(doc.get("revision") or 0) > revisions[doc["id"]]]
        return JSONResponse({
            "catalog": store.catalog(),
            "sections": changed,
            "alerts": store.query_alerts("pending", 200),
            "pulse": hub.pulses.all(),
            "retracted": store.recently_retracted(),
            "acked": store.acked_ids(),
            "prefs": store.load_prefs(),
            "jobs": hub.jobs.snapshot(),
            "satellites": hub.satellites.listing(),
            "hub": hub.info(),
            "server_time": now_iso(),
        })

    # Feed schedules and run buttons (the scheduler's state in data/scheduler).

    @app.get("/v1/jobs")
    async def get_jobs(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        return JSONResponse(hub.jobs.snapshot())

    @app.post("/v1/jobs/notify")
    async def jobs_notify(request: Request) -> JSONResponse:
        """The scheduler changed its picture: push it to every open app."""
        if not hub.auth.is_automation(hub.token(request)):
            return fail("unauthorized", 401, code="unauthorized")
        await live.broadcast({"type": "jobs", "jobs": hub.jobs.snapshot()})
        return JSONResponse({"ok": True})

    @app.post("/v1/jobs/run-all")
    async def jobs_run_all(request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        body = await body_of(request)
        try:
            if body.get("section_id"):
                result = hub.jobs.run_for_page(str(body["section_id"]), body.get("node_id"), device_of(request))
            else:
                result = hub.jobs.run_all(body.get("group"), device_of(request))
        except OpsError as exc:
            return fail_ops(exc)
        return JSONResponse(result)

    @app.post("/v1/jobs/{job_id}/run")
    async def jobs_run(job_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        try:
            return JSONResponse(hub.jobs.run([job_id], device_of(request)))
        except OpsError as exc:
            return fail_ops(exc)

    @app.post("/v1/jobs/{job_id}/cancel")
    async def jobs_cancel(job_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        try:
            return JSONResponse(hub.jobs.cancel(job_id, device_of(request)))
        except OpsError as exc:
            return fail_ops(exc)

    @app.post("/v1/jobs/{job_id}/settings")
    async def jobs_settings(job_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        try:
            return JSONResponse(hub.jobs.settings(job_id, await body_of(request), device_of(request)))
        except OpsError as exc:
            return fail_ops(exc)

    @app.get("/v1/jobs/{job_id}/log")
    async def jobs_log(job_id: str, request: Request) -> JSONResponse:
        denied = require(request)
        if denied:
            return denied
        try:
            return JSONResponse(hub.jobs.log(job_id))
        except OpsError as exc:
            return fail_ops(exc)

    @app.websocket("/v1/live")
    async def live_socket(websocket: WebSocket) -> None:
        if not hub.auth.is_user(bearer(websocket)):
            await websocket.close(code=4401)
            return
        await live.connect(websocket)
        await websocket.send_json({"type": "hello", "server_time": now_iso(), "hub_id": hub.hub_id})
        try:
            while True:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json({"type": "ping"})
        except Exception:
            live.disconnect(websocket)

    tracker_module.add_routes(app, hub)
    satellites_module.add_routes(app, hub)
    desk_module.add_routes(app, hub)
    speech_module.add_routes(app, hub)
    from orbital.hub import nav as nav_module
    from orbital.hub import usage as usage_module
    nav_module.add_routes(app, hub)
    usage_module.add_routes(app, hub)
    return app


def stamp_machine(alert: dict, satellite: dict) -> dict:
    """An alert a satellite sent: say which machine, and keep its id apart from other machines' ids."""
    name = str(satellite.get("name") or satellite.get("machine") or satellite["id"])
    names = {name.lower(), str(satellite.get("machine") or "").lower(), satellite["id"].lower()} - {""}
    out = dict(alert)
    out["machine"] = satellite.get("machine") or name
    out["source"] = out.get("source") or "satellite"
    title = str(out.get("title") or "")
    if title and not any(n in title.lower() for n in names):
        out["title"] = f"{name}: {title}"
    ident = out.get("id")
    if isinstance(ident, str) and ident and not ident.startswith(f"{satellite['id']}-"):
        out["id"] = f"{satellite['id']}-{ident}"[:128]
    info = out.get("user_info") if isinstance(out.get("user_info"), dict) else {}
    out["user_info"] = {**info, "machine": out["machine"], "satellite_id": satellite["id"]}
    return out


async def _maintenance_forever(cfg: Config) -> None:
    from orbital.hub.maintenance import trim

    while True:
        try:
            await asyncio.to_thread(trim, cfg)
        except Exception as exc:  # never let it stop the hub
            get_logger("hub").warning("retention trim failed", extra={"fields": {"error": str(exc)[:200]}})
        await asyncio.sleep(MAINTENANCE_EVERY)
