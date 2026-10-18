"""Feed SDK: push sections, alerts and live numbers to the hub, and answer the buttons people tap.

    from orbital.feeds import HubClient, blocks

    hub = HubClient.from_config()                     # hub URL from config.toml, ORBITAL_AUTOMATION_TOKEN
    hub.put_section(blocks.section("home-server", "Home server", [
        blocks.metrics("vitals", [blocks.metric("CPU", "12%"), blocks.metric("Disk free", "412 GB")]),
        blocks.chart("load", [blocks.chart_series("Load", [("09:00", 0.4), ("10:00", 0.9)])], title="Load"),
    ], icon="server.rack"))
    hub.alert("Backup failed", "The nightly backup exited with code 2.", section_id="home-server", level="warning")

    for action in hub.get_actions("home-server"):     # taps waiting for this feed
        ...                                           # do it against your own source
        hub.post_action_result("home-server", action["id"], "applied", "Restarted.")

Stdlib + httpx only. Every error from the hub raises HubError with the hub's code, hint and heal
steps. A connection refused at start-up (the hub not listening yet) is retried for a few seconds.
Also used by the `orbital` CLI (push, satellites, agents) and the watchdog.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from orbital.feeds import blocks

__all__ = ["HubClient", "HubError", "blocks", "conflict_retry_revision"]

# These rewrite a whole section. A 409 must be read by the caller, not replayed.
CONFLICT_RETRY_BLOCKED = {"replace_section", "delete_section"}
LEVELS = {
    "passive": "passive",
    "info": "active",
    "active": "active",
    "success": "active",
    "warning": "timeSensitive",
    "error": "timeSensitive",
    "timesensitive": "timeSensitive",
    "critical": "critical",
}
_SLUG = re.compile(r"[^a-z0-9]+")


class HubError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        self.payload: dict = {}
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            self.payload = parsed
        self.code = str(self.payload.get("code") or f"http_{status}")
        self.hint = str(self.payload.get("hint") or "")
        heal = self.payload.get("heal")
        self.heal = [str(step) for step in heal] if isinstance(heal, list) else []
        self.revision = self.payload.get("revision")
        self.section_id = self.payload.get("section_id")
        self.message = str(self.payload.get("error") or self.payload.get("detail") or (body or "")[:500])
        lines = [f"HTTP {status} {self.code}: {self.message}"]
        if self.hint and self.hint != self.message:
            lines.append(f"hint: {self.hint}")
        if self.revision is not None:
            lines.append(f"revision: {self.revision}")
        lines.extend(f"heal: {step}" for step in self.heal)
        super().__init__("\n".join(lines))


def conflict_retry_revision(ops: list | None, error: HubError) -> int | None:
    """Revision to retry once, or None when the caller must fix the payload."""
    if error.status != 409 or not isinstance(error.revision, int):
        return None
    for op in ops or []:
        if not isinstance(op, dict) or op.get("op") in CONFLICT_RETRY_BLOCKED:
            return None
    return error.revision


def _slug(text: str, limit: int = 40) -> str:
    return (_SLUG.sub("-", (text or "").lower()).strip("-") or "feed")[:limit].strip("-") or "feed"


class HubClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0, startup_wait: float | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        wait = os.environ.get("ORBITAL_STARTUP_WAIT", "10") if startup_wait is None else startup_wait
        self.startup_wait = float(wait)
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)), transport=transport)

    @classmethod
    def from_config(cls, cfg: Any = None, *, token: str | None = None, **kwargs: Any) -> "HubClient":
        """The hub this machine talks to (itself on the hub, hub.url on a satellite) with the automation token.

        ORBITAL_HUB_URL and ORBITAL_AUTOMATION_TOKEN in the environment override both. A satellite
        without the automation token falls back to its ORBITAL_HUB_TOKEN, which the hub accepts for
        the machine list and the agent and browser relays (not for sections or alerts).
        """
        from orbital.core.config import load_config
        from orbital.core.secrets import Secrets

        cfg = cfg or load_config()
        url = os.environ.get("ORBITAL_HUB_URL") or cfg.hub_url()
        if not url:
            raise HubError(0, json.dumps({"error": "no hub address: set hub.url in config.toml (satellites) or run ./setup", "code": "not_configured"}))
        store = Secrets(cfg.paths)
        token = token or store.get("ORBITAL_AUTOMATION_TOKEN") or store.get("ORBITAL_HUB_TOKEN")
        if not token:
            raise HubError(0, json.dumps({"error": "ORBITAL_AUTOMATION_TOKEN is not set on this machine (see ~/.orbital-agents/secrets.env)", "code": "not_configured"}))
        return cls(url, token, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Transport

    def request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
                files: dict | None = None, auth: bool = True, timeout: float | None = None) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        deadline = time.monotonic() + self.startup_wait
        delay = 1.0
        while True:
            try:
                response = self._client.request(method, path, json=json_body, params=params, files=files, headers=headers,
                                                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
                break
            except httpx.ConnectError as exc:
                # At login every feed starts at once, often before the hub listens. Nothing was sent: safe to retry.
                if time.monotonic() + delay > deadline:
                    raise HubError(0, json.dumps({"error": f"could not reach the hub at {self.base_url}: {exc.__class__.__name__}", "code": "unavailable"})) from exc
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
            except httpx.HTTPError as exc:
                raise HubError(0, json.dumps({"error": f"hub request failed: {exc.__class__.__name__}", "code": "unavailable"})) from exc
        if response.status_code >= 400:
            raise HubError(response.status_code, response.text)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    # Reading

    def health(self) -> dict:
        return self.request("GET", "/v1/health", auth=False)

    def info(self) -> dict:
        return self.request("GET", "/v1/hub/info", auth=False)

    def contract(self) -> dict:
        return self.request("GET", "/v1/contract")

    def catalog(self) -> dict:
        return self.request("GET", "/v1/catalog")

    def section(self, section_id: str) -> dict:
        return self.request("GET", f"/v1/sections/{section_id}")

    # Writing sections

    def put_section(self, section: dict, section_id: str | None = None) -> dict:
        """Create or replace one whole section (its id comes from the document unless given)."""
        ident = section_id or section.get("id")
        if not ident:
            raise ValueError("the section needs an id")
        return self.request("PUT", f"/v1/sections/{ident}", json_body=section)

    def patch_section(self, section_id: str, ops: list[dict], *, alerts: list[dict] | None = None,
                      if_revision: int | None = None, _retry: bool = False) -> dict:
        """Apply id-addressed ops (upsert_block, merge_section, …). A 409 on safe ops is retried once."""
        body: dict = {"schema": "orbital.ops.v1", "ops": ops or []}
        if alerts:
            body["alerts"] = alerts
        if if_revision is not None:
            body["if_revision"] = if_revision
        try:
            return self.request("POST", f"/v1/sections/{section_id}/ops", json_body=body)
        except HubError as exc:
            revision = None if _retry else conflict_retry_revision(ops, exc)
            if revision is None:
                raise
            return self.patch_section(section_id, ops, alerts=alerts, if_revision=revision, _retry=True)

    def upsert_block(self, section_id: str, block: dict, *, node_id: str | None = None, tab_id: str | None = None) -> dict:
        op: dict = {"op": "upsert_block", "block": block}
        if node_id:
            op["node_id"] = node_id
        if tab_id:
            op["tab_id"] = tab_id
        return self.patch_section(section_id, [op])

    def delete_section(self, section_id: str) -> dict:
        return self.request("DELETE", f"/v1/sections/{section_id}")

    def validate(self, section_id: str, ops: list | None = None, *, section: dict | None = None,
                 alerts: list | None = None, if_revision: int | None = None) -> dict:
        """Check a section document or an ops batch without writing. Returns warnings too."""
        if section is not None:
            body: dict = {"section": section}
        else:
            body = {"schema": "orbital.ops.v1", "ops": ops or []}
            if alerts:
                body["alerts"] = alerts
            if if_revision is not None:
                body["if_revision"] = if_revision
        return self.request("POST", f"/v1/sections/{section_id}/validate", json_body=body)

    # Alerts

    def alert(self, title: str, body: str, section_id: str | None = None, level: str = "info", *,
              id: str | None = None, kind: str = "event", subtitle: str | None = None, node_id: str | None = None,
              tab_id: str | None = None, launch_url: str | None = None, thread_id: str | None = None,
              deliver_at: str | None = None, **extra: Any) -> str:
        """Raise one notification. Returns its id.

        level: passive | info | warning | error | critical (mapped to the iOS interruption level).
        Without an id, the same title and body on the same day count as one alert, so a feed that
        runs every few minutes does not notify again and again.
        """
        if id is None:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            digest = hashlib.sha1(f"{section_id}|{title}|{body}".encode()).hexdigest()[:12]
            id = f"{_slug(section_id or 'feed')}-{day}-{digest}"
        alert: dict = {
            "schema": "orbital.alert.v1",
            "id": id,
            "kind": kind,
            "title": title,
            "body": body,
            "interruption_level": LEVELS.get(str(level).lower(), "active"),
        }
        for key, value in (("section_id", section_id), ("subtitle", subtitle), ("node_id", node_id), ("tab_id", tab_id),
                           ("launch_url", launch_url), ("thread_id", thread_id or section_id), ("deliver_at", deliver_at)):
            if value is not None:
                alert[key] = value
        alert.update(extra)
        answer = self.request("POST", "/v1/alerts", json_body={"alerts": [alert]})
        ids = answer.get("alert_ids") or [id]
        return ids[0]

    def alerts(self, alerts: list[dict]) -> dict:
        """Post orbital.alert.v1 objects as they are."""
        return self.request("POST", "/v1/alerts", json_body={"alerts": alerts})

    def retract(self, ids: list[str]) -> dict:
        """Cancel alerts the apps scheduled but should no longer fire."""
        return self.request("POST", "/v1/alerts/retract", json_body={"ids": ids})

    def ack(self, ids: list[str]) -> dict:
        return self.request("POST", "/v1/alerts/ack", json_body={"ids": ids})

    # Media and live numbers

    def upload_media(self, path: str | Path, content_type: str | None = None) -> dict:
        """Upload an image (jpeg, png, webp, gif). Returns {id, url, content_type}; url is /v1/media/<hash>."""
        source = Path(path).expanduser()
        kind = content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        with source.open("rb") as fh:
            return self.request("POST", "/v1/media", files={"file": (source.name, fh, kind)})

    def pulse(self, items: list[dict], remove: list[str] | None = None) -> dict:
        """Post live values (no revision bump, no notification). Each item: key, section_id, label, value…"""
        body: dict = {"schema": "orbital.pulse.v1", "items": items}
        if remove:
            body["remove"] = remove
        return self.request("POST", "/v1/pulse", json_body=body)

    # Actions: taps from the apps, waiting for this feed

    def get_actions(self, section_id: str, status: str = "pending", limit: int = 100) -> list[dict]:
        answer = self.request("GET", f"/v1/sections/{section_id}/actions", params={"status": status, "limit": limit})
        return answer.get("actions") or []

    def post_action_result(self, section_id: str, action_id: str, status: str = "applied", message: str | None = None) -> dict:
        """Answer one action: applied or failed, with an optional message the app shows."""
        result: dict = {"id": action_id, "status": status}
        if message is not None:
            result["message"] = message[:500]
        return self.resolve_actions(section_id, [result])

    def resolve_actions(self, section_id: str, results: list[dict]) -> dict:
        return self.request("POST", f"/v1/sections/{section_id}/actions/results", json_body={"results": results})

    # Satellites and agents (used by the CLI; any script may use them too)

    def satellites(self) -> list[dict]:
        return self.request("GET", "/v1/satellites").get("satellites") or []

    def remove_satellite(self, satellite: str) -> dict:
        return self.request("DELETE", f"/v1/satellites/{satellite}")

    def run_agent(self, satellite: str, prompt: str, agent: str | None = None, *, cwd: str | None = None,
                  title: str | None = None, track: bool | None = None) -> dict:
        body: dict = {"prompt": prompt, "by": "cli"}
        for key, value in (("agent", agent), ("cwd", cwd), ("title", title), ("track", track)):
            if value is not None:
                body[key] = value
        return self.request("POST", f"/v1/satellites/{satellite}/agents/run", json_body=body)

    def agent_jobs(self, satellite: str | None = None) -> dict:
        if satellite:
            return self.request("GET", f"/v1/satellites/{satellite}/agents/jobs")
        return self.request("GET", "/v1/agents/jobs")

    def agent_job(self, satellite: str, job_id: str) -> dict:
        return self.request("GET", f"/v1/satellites/{satellite}/agents/jobs/{job_id}")

    def agent_log(self, satellite: str, job_id: str) -> dict:
        return self.request("GET", f"/v1/satellites/{satellite}/agents/jobs/{job_id}/log")

    def cancel_agent(self, satellite: str, job_id: str) -> dict:
        return self.request("POST", f"/v1/satellites/{satellite}/agents/jobs/{job_id}/cancel")

    def browser(self, satellite: str, command: str, **params: Any) -> dict:
        return self.request("POST", f"/v1/satellites/{satellite}/browser/{command}", json_body={"params": params}, timeout=130)

    def tracker_command(self, command: dict) -> dict:
        return self.request("POST", "/v1/tracker/command", json_body=command)
