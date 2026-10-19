"""Heartbeat: tell the hub every `satellite.heartbeat_seconds` that this satellite is alive.

    POST <hub>/v1/satellites/heartbeat   Authorization: Bearer <ORBITAL_HUB_TOKEN>
    {"satellite_id", "capabilities": {...}, "running_jobs": [<job summaries, running and queued>]}

On the hub machine (`system.role = "hub"`) the satellite first joins the local hub by itself, with
the join token from secrets.env, and joins again if the hub forgets it. A satellite the hub no
longer knows logs a warning (at most hourly) until `orbital satellite join` is run again.
The last result is kept in data/satellite/heartbeat.json for `orbital satellite status`.
"""

from __future__ import annotations

import threading
import time

import httpx

from orbital import __version__
from orbital.core.config import Config, load_config
from orbital.core.secrets import Secrets
from orbital.satellite.join import (HUB_TOKEN_KEY, JOIN_TOKEN_KEY, JoinClosed, JoinError, capabilities, clear_identity,
                                    browser_status, join_hub, read_identity, system_disk, system_metrics)
from orbital.satellite.util import atomic_write_json, iso, now

COMPLAIN_EVERY = 3600.0
AUTO_JOIN_EVERY = 20.0
JOIN_RETRY = 10.0
JOIN_GRACE = 120.0


def heartbeat_path(cfg: Config):
    return cfg.paths.satellite_data / "heartbeat.json"


def beat_once(cfg: Config, manager=None, timeout: float = 10.0) -> dict:
    """One heartbeat. Returns {"ok", "status", "detail", "hub", "at"}; never raises."""
    hub = cfg.hub_url()
    result: dict = {"ok": False, "status": None, "detail": "", "hub": hub, "at": iso(now())}
    if not hub:
        result["detail"] = "no hub configured (hub.url is empty)"
        return result
    identity = read_identity(cfg)
    token = Secrets(cfg.paths).get(HUB_TOKEN_KEY)
    satellite_id = identity.get("satellite_id")
    if not token or not satellite_id:
        result["detail"] = "not joined to a hub yet"
        return result
    disk_paths = [system_disk(), str(cfg.paths.home)]
    body = {
        "satellite_id": satellite_id,
        "capabilities": capabilities(cfg),
        "running_jobs": manager.running_summary() if manager is not None else [],
        "version": __version__,
        "heartbeat_seconds": max(1, int(float(cfg.get("satellite.heartbeat_seconds", 60) or 60))),
        "system_metrics": system_metrics(disk_paths),
        "browser_status": browser_status(cfg, local=bool(identity.get("local"))),
    }
    try:
        response = httpx.post(f"{hub}/v1/satellites/heartbeat", json=body,
                              headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    except httpx.HTTPError as exc:
        result["detail"] = f"hub unreachable: {exc.__class__.__name__}"
        return result
    result["status"] = response.status_code
    result["ok"] = response.status_code == 200
    if not result["ok"]:
        try:
            result["detail"] = str(response.json().get("detail") or "")[:200]
        except ValueError:
            result["detail"] = response.text[:200]
    return result


class Heartbeat(threading.Thread):
    def __init__(self, cfg: Config, manager=None, log=None, interval: float | None = None):
        super().__init__(name="satellite-heartbeat", daemon=True)
        self.home = cfg.paths.home
        self.manager = manager
        self.log = log
        self.interval = interval
        self._halt = threading.Event()
        self._last_complaint = 0.0
        self._join_failing_since: float | None = None
        self._next_auto_join = 0.0
        self._said_waiting = 0.0
        self.last: dict = {}
        self.beats = 0

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                wait = self.tick()
            except Exception as exc:  # the loop must survive anything
                if self.log:
                    self.log.warning("heartbeat failed", extra={"fields": {"error": f"{type(exc).__name__}: {exc}"}})
                wait = JOIN_RETRY
            self._halt.wait(wait)

    def _complain(self, msg: str, **fields) -> None:
        if self.log and time.monotonic() - self._last_complaint > COMPLAIN_EVERY:
            self._last_complaint = time.monotonic()
            self.log.warning(msg, extra={"fields": fields})

    def ensure_local_join(self, cfg: Config) -> bool:
        """Hub machine: make sure our own satellite is joined to the local hub."""
        local = cfg.hub_url()
        identity = read_identity(cfg)
        store = Secrets(cfg.paths)
        if identity.get("satellite_id") and store.get(HUB_TOKEN_KEY) and identity.get("hub_url") == local:
            return True
        join_token = store.get(JOIN_TOKEN_KEY)
        if not join_token:
            self._complain("no ORBITAL_JOIN_TOKEN in secrets.env; the hub's own satellite cannot join it")
            return False
        try:
            info = join_hub(local, join_token, cfg, write_config=False, local=True)
        except JoinError as exc:
            # At login the satellite often starts before the hub listens: only a lasting failure is a problem.
            first = self._join_failing_since = self._join_failing_since or time.monotonic()
            if time.monotonic() - first > JOIN_GRACE:
                self._complain("joining the local hub failed; will retry", error=str(exc))
            elif self.log:
                self.log.info("local hub not answering yet; will join when it does", extra={"fields": {"error": str(exc)[:200]}})
            return False
        self._join_failing_since = None
        if self.log:
            self.log.info("joined the local hub", extra={"fields": {"satellite_id": info["satellite_id"]}})
        return True

    def auto_join(self, cfg: Config) -> bool:
        """A satellite that isn't joined: find the hub (hub.url, else on the tailnet) and join. Without the
        join token the hub lets it in while its join window is open (`orbital satellites invite`)."""
        if time.monotonic() < self._next_auto_join:
            return False
        self._next_auto_join = time.monotonic() + AUTO_JOIN_EVERY
        url = cfg.hub_url()
        if not url:
            from orbital.core.net import discover_hubs
            found = [h for h in discover_hubs(port=int(cfg.get("hub.port", 7700) or 7700)) if not h.get("is_self")]
            if not found:
                return False
            url = found[0]["url"]
        try:
            info = join_hub(url, "", cfg, write_config=True)
        except JoinClosed:
            if self.log and time.monotonic() - self._said_waiting > COMPLAIN_EVERY:
                self._said_waiting = time.monotonic()
                self.log.info("found the hub; waiting for it to open joining (run `orbital satellites invite` there)",
                              extra={"fields": {"hub": url}})
            return False
        except JoinError as exc:
            self._complain("joining the hub failed; will retry", hub=url, error=str(exc)[:200])
            return False
        if self.log:
            self.log.info("joined the hub", extra={"fields": {"hub": url, "satellite_id": info["satellite_id"]}})
        return True

    def tick(self) -> float:
        cfg = load_config(self.home)
        interval = float(self.interval or cfg.get("satellite.heartbeat_seconds", 60) or 60)
        if cfg.role == "hub" and not self.ensure_local_join(cfg):
            return min(interval, JOIN_RETRY)
        if cfg.role != "hub" and not (read_identity(cfg).get("satellite_id") and Secrets(cfg.paths).get(HUB_TOKEN_KEY)):
            if not self.auto_join(cfg):
                return min(interval, AUTO_JOIN_EVERY)
            cfg = load_config(self.home)
        result = beat_once(cfg, self.manager)
        self.last = result
        self.beats += 1
        try:
            atomic_write_json(heartbeat_path(cfg), result)
        except OSError:
            pass
        if result["status"] in (401, 403, 404):
            if cfg.role == "hub":
                clear_identity(cfg)          # join again on the next tick
                return min(interval, JOIN_RETRY)
            # The hub forgot this machine: join again (by itself with the saved token, or in the next join window).
            clear_identity(cfg)
            self._complain("the hub no longer knew this satellite; joining it again", status=result["status"])
            return min(interval, AUTO_JOIN_EVERY)
        elif not result["ok"] and result["detail"] not in ("not joined to a hub yet", "no hub configured (hub.url is empty)"):
            self._complain("heartbeat to the hub failed", detail=result["detail"], status=result["status"])
        return interval
