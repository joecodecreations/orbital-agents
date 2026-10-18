"""Direct Apple push: new alerts reach a phone or Mac whose app is closed. Optional, off by default.

On when all of these hold:
  notifications.apns = true                  config.toml (notifications.apns_environment picks the host:
                                             development → api.sandbox.push.apple.com, production → api.push.apple.com)
  APNS_KEY_ID, APNS_TEAM_ID                  secrets.env
  ~/.orbital-agents/apns.p8                  the APNs auth key from the Apple Developer site
  pip install 'orbital-agents[push]'         cryptography (ES256) and h2 (HTTP/2)

The topic is <apple.bundle_prefix>.orbitalagents. Each device the app registered with an
apns_token (POST /v1/devices) gets every new alert the hub stores, except reminders the app
schedules itself (deliver_at in the future, repeats). Passive alerts go out quietly, as they do
on the device. Sending runs in the background and never holds up the request that raised the alert.

Apple's answers: 200 sent; 410, or 400 BadDeviceToken / Unregistered, removes that token;
403 (a bad key or team id) stops sending for COOLDOWN and says so in status(); a network error is
retried once. Without the key or the extras nothing changes: alerts still reach open apps over the
live socket and their next sync, and one INFO line says what is missing. Keys, provider tokens
and device tokens are never logged (tokens are masked). Never Firebase or any other relay.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from orbital.core import get_logger
from orbital.core.secrets import mask

HOSTS = {"development": "api.sandbox.push.apple.com", "production": "api.push.apple.com"}
JWT_TTL = 50 * 60          # Apple accepts a provider token for an hour; refresh before that
COOLDOWN = 15 * 60         # after a 403, wait this long before trying again
RETRY_DELAY = 1.0
REMOVE_REASONS = {"BadDeviceToken", "Unregistered"}
BODY_LIMIT = 1500          # characters; the whole payload must stay under 4 KB


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def provider_token(key_pem: bytes, key_id: str, team_id: str, issued_at: int) -> str:
    """The ES256 JWT Apple wants as `authorization: bearer …`: header {alg, kid}, claims {iss, iat}."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("apns.p8 is not a P-256 key")
    header = _b64(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iss": team_id, "iat": int(issued_at)}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    # JWS wants the raw 64-byte r||s, not the DER the library returns.
    return f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def extras_missing() -> str | None:
    """None when the push extras import, else what to install."""
    try:
        import cryptography  # noqa: F401
        import h2  # noqa: F401
    except ImportError:
        return "pip install 'orbital-agents[push]'"
    return None


def should_push(alert: dict, now: float | None = None) -> bool:
    """New alerts the device would show now. Future and repeating reminders the app schedules itself."""
    if alert.get("retracted") or alert.get("acked") or alert.get("repeats"):
        return False
    when = alert.get("deliver_at")
    if when:
        try:
            at = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError:
            return False
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at.timestamp() > (now or time.time()) + 30:
            return False
    return True


def payload(alert: dict) -> dict:
    """The aps dictionary plus `orbital` (alert id, section, node, tab, launch_url) for the deep link."""
    level = alert.get("interruption_level") or ("active" if alert.get("kind") == "reminder" else "timeSensitive")
    body = str(alert.get("body") or "")
    if len(body) > BODY_LIMIT:
        body = body[: BODY_LIMIT - 1] + "…"
    content: dict = {"title": str(alert.get("title") or "")[:200], "body": body}
    if alert.get("subtitle"):
        content["subtitle"] = str(alert["subtitle"])[:200]
    aps: dict = {
        "alert": content,
        "thread-id": str(alert.get("section_id") or alert.get("thread_id") or "orbital"),
        "interruption-level": level,
    }
    if level != "passive" and alert.get("sound") != "none":
        aps["sound"] = "default"
    if isinstance(alert.get("relevance_score"), (int, float)):
        aps["relevance-score"] = alert["relevance_score"]
    if alert.get("category_id"):
        aps["category"] = str(alert["category_id"])
    custom = {"alert_id": alert.get("id"), "section_id": alert.get("section_id")}
    for key in ("node_id", "tab_id", "launch_url"):
        if alert.get(key):
            custom[key] = alert[key]
    return {"aps": aps, "orbital": custom}


class APNs:
    def __init__(self, cfg, secret: Callable[[str], str], store, *, clock: Callable[[], float] = time.time,
                 client_factory: Callable[[], Any] | None = None) -> None:
        self.cfg = cfg
        self.secret = secret
        self.store = store
        self.clock = clock
        self.client_factory = client_factory or self._client
        self.key_path = cfg.paths.home / "apns.p8"
        self._jwt: tuple[str, float, str] | None = None   # (token, issued_at, key fingerprint)
        self._down_until = 0.0
        self._error: str | None = None
        self._logged: str | None = None

    # Settings

    @property
    def environment(self) -> str:
        env = str(self.cfg.get("notifications.apns_environment", "development") or "development").lower()
        return env if env in HOSTS else "development"

    @property
    def host(self) -> str:
        return HOSTS[self.environment]

    @property
    def topic(self) -> str:
        return f"{self.cfg.get('apple.bundle_prefix', 'com.example')}.orbitalagents"

    def missing(self) -> list[str]:
        """What stands between this hub and push, in words for the log."""
        out = []
        if not self.key_path.is_file():
            out.append(f"the APNs key {self.key_path.name} in ORBITAL_HOME")
        for key in ("APNS_KEY_ID", "APNS_TEAM_ID"):
            if not self.secret(key):
                out.append(f"{key} in secrets.env")
        extras = extras_missing()
        if extras:
            out.append(f"the push extras ({extras})")
        return out

    def status(self) -> dict:
        """{enabled, configured, environment, healthy, error}: the app registers for push when enabled."""
        wanted = bool(self.cfg.get("notifications.apns", False))
        missing = self.missing()
        if wanted and missing:
            line = "direct Apple push is off: missing " + ", ".join(missing)
            if line != self._logged:
                self._logged = line
                get_logger("hub").info(line)
        healthy = self.clock() >= self._down_until
        return {
            "enabled": wanted and not missing,
            "configured": not missing,
            "environment": self.environment,
            "healthy": healthy,
            "error": None if healthy else self._error,
        }

    # Provider token

    def token(self) -> str:
        key_pem = self.key_path.read_bytes()
        fingerprint = hashlib.sha256(key_pem).hexdigest()
        now = self.clock()
        if self._jwt and now - self._jwt[1] < JWT_TTL and self._jwt[2] == fingerprint:
            return self._jwt[0]
        jwt = provider_token(key_pem, self.secret("APNS_KEY_ID"), self.secret("APNS_TEAM_ID"), int(now))
        self._jwt = (jwt, now, fingerprint)
        return jwt

    # Sending

    @staticmethod
    def _client():
        import httpx

        return httpx.AsyncClient(http2=True, timeout=httpx.Timeout(15.0, connect=5.0))

    def devices(self) -> list[dict]:
        """Registered devices with a push token, one row per token."""
        seen: set[str] = set()
        out = []
        for row in self.store.load_devices():
            token = row.get("apns_token")
            if isinstance(token, str) and token and token not in seen:
                seen.add(token)
                out.append(row)
        return out

    async def send_alerts(self, alerts: list[dict]) -> dict:
        """Push each alert that should notify to every registered device. Returns counts; never raises."""
        result = {"sent": 0, "removed": 0, "failed": 0, "skipped": 0}
        wanted = [alert for alert in alerts if should_push(alert)]
        result["skipped"] = len(alerts) - len(wanted)
        if not wanted:
            return result
        if not self.status()["enabled"] or self.clock() < self._down_until:
            result["skipped"] = len(alerts)
            return result
        devices = self.devices()
        if not devices:
            return result
        try:
            jwt = self.token()
        except Exception as exc:  # a broken key file: say so, keep the hub running
            self._mark_down(f"the APNs key could not be used: {exc.__class__.__name__}")
            get_logger("hub").warning("apns key unusable", extra={"fields": {"error": exc.__class__.__name__}})
            result["failed"] = len(wanted) * len(devices)
            return result
        client = self.client_factory()
        try:
            for alert in wanted:
                message = payload(alert)
                body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                headers = {
                    "authorization": f"bearer {jwt}",
                    "apns-topic": self.topic,
                    "apns-push-type": "alert",
                    "apns-priority": "5" if message["aps"]["interruption-level"] == "passive" else "10",
                    "apns-collapse-id": str(alert.get("id"))[:64],
                }
                for device in devices:
                    result[await self._send_one(client, device["apns_token"], headers, body, str(alert.get("id")))] += 1
                    if self.clock() < self._down_until:
                        return result
        finally:
            await client.aclose()
        return result

    async def _send_one(self, client, device_token: str, headers: dict, body: bytes, alert_id: str) -> str:
        log = get_logger("hub")
        url = f"https://{self.host}/3/device/{device_token}"
        response = None
        for attempt in (1, 2):
            try:
                response = await client.post(url, headers=headers, content=body)
                break
            except Exception as exc:  # network trouble: one more try after a short wait
                if attempt == 2:
                    log.warning("apns send failed", extra={"fields": {"device": mask(device_token), "alert": alert_id,
                                                                     "error": exc.__class__.__name__}})
                    return "failed"
                await asyncio.sleep(RETRY_DELAY)
        status = response.status_code
        reason = _reason(response)
        if status == 200:
            return "sent"
        if status == 410 or (status == 400 and reason in REMOVE_REASONS):
            self.store.drop_apns_token(device_token)
            log.info("apns token removed", extra={"fields": {"device": mask(device_token), "reason": reason or str(status)}})
            return "removed"
        if status == 403:
            self._jwt = None
            self._mark_down(f"Apple refused the provider token ({reason or 'HTTP 403'}); check APNS_KEY_ID, APNS_TEAM_ID and apns.p8")
            log.warning("apns refused the provider token", extra={"fields": {"reason": reason or "403"}})
            return "failed"
        log.warning("apns rejected a push", extra={"fields": {"status": status, "reason": reason, "device": mask(device_token), "alert": alert_id}})
        return "failed"

    def _mark_down(self, error: str) -> None:
        self._error = error
        self._down_until = self.clock() + COOLDOWN


def _reason(response) -> str | None:
    try:
        data = response.json()
    except Exception:
        return None
    return data.get("reason") if isinstance(data, dict) else None
