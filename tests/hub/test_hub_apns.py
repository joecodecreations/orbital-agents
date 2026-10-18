"""Direct Apple push with a mocked HTTP/2 client and a throwaway key made here."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time

import httpx
import pytest

# The push extras (pip install 'orbital-agents[push]'); without them these tests are skipped.
pytest.importorskip("cryptography")
pytest.importorskip("h2")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

from hub_helpers import AUTH, make_client, write_config  # noqa: E402
from orbital.core.config import load_config  # noqa: E402
from orbital.hub import apns as apns_module  # noqa: E402
from orbital.hub.apns import APNs, payload, provider_token, should_push  # noqa: E402
from orbital.hub.store import Store  # noqa: E402

KEY_ID = "KEY0123456"
TEAM = "TEAM012345"
PHONE = "a1" * 32
MAC = "b2" * 32


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


class Apple:
    """Stands in for api.(sandbox.)push.apple.com: records requests, answers from a script."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else (200, None)
        if isinstance(answer, Exception):
            raise answer
        status, reason = answer
        return httpx.Response(status, json={"reason": reason} if reason else None)

    def factory(self):
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(self))


def b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


@pytest.fixture
def key(home):
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    (home / "apns.p8").write_bytes(pem)
    return private


def configure(home, monkeypatch, *, enabled: bool = True, environment: str = "development") -> None:
    write_config(home, extra=f'\n[notifications]\napns = {"true" if enabled else "false"}\napns_environment = "{environment}"\n'
                             '\n[apple]\nbundle_prefix = "com.example.test"\n')
    monkeypatch.setenv("APNS_KEY_ID", KEY_ID)
    monkeypatch.setenv("APNS_TEAM_ID", TEAM)


def make_apns(apple: Apple, clock=None, devices=(PHONE, MAC)) -> tuple[APNs, Store]:
    cfg = load_config()
    store = Store(cfg.paths.ensure().hub_data)
    for n, token in enumerate(devices):
        store.save_device({"device_id": f"d{n}", "platform": "ios", "apns_token": token})
    service = APNs(cfg, lambda k: os.environ.get(k, ""), store, clock=clock or Clock(), client_factory=apple.factory())
    return service, store


ALERT = {"schema": "orbital.alert.v1", "id": "backup-failed", "kind": "event", "section_id": "home-server",
         "title": "Backup failed", "body": "Exit code 2.", "interruption_level": "timeSensitive"}


def test_provider_token_shape_and_signature(key):
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    token = provider_token(pem, KEY_ID, TEAM, 1_700_000_000)
    header, claims, signature = token.split(".")
    assert json.loads(b64decode(header)) == {"alg": "ES256", "kid": KEY_ID}
    assert json.loads(b64decode(claims)) == {"iss": TEAM, "iat": 1_700_000_000}
    raw = b64decode(signature)
    assert len(raw) == 64, "JWS wants raw r||s"
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    rsa_like = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with pytest.raises(ValueError):
        provider_token(rsa_like, KEY_ID, TEAM, 1)


def test_provider_token_is_cached_about_fifty_minutes(home, key, monkeypatch):
    configure(home, monkeypatch)
    clock = Clock()
    service, _ = make_apns(Apple(), clock)
    first = service.token()
    clock.now += 49 * 60
    assert service.token() == first
    clock.now += 2 * 60
    assert service.token() != first


@pytest.mark.parametrize("environment,host", [("development", "api.sandbox.push.apple.com"), ("production", "api.push.apple.com")])
def test_sends_to_every_device_on_the_right_host(home, key, monkeypatch, environment, host):
    configure(home, monkeypatch, environment=environment)
    apple = Apple()
    service, _ = make_apns(apple)
    assert service.status() == {"enabled": True, "configured": True, "environment": environment, "healthy": True, "error": None}
    result = asyncio.run(service.send_alerts([ALERT]))
    assert result == {"sent": 2, "removed": 0, "failed": 0, "skipped": 0}
    assert [r.url.host for r in apple.requests] == [host, host]
    assert sorted(r.url.path for r in apple.requests) == sorted([f"/3/device/{PHONE}", f"/3/device/{MAC}"])
    sent = apple.requests[0]
    assert sent.headers["apns-topic"] == "com.example.test.orbitalagents"
    assert sent.headers["apns-push-type"] == "alert" and sent.headers["apns-priority"] == "10"
    assert sent.headers["authorization"].startswith("bearer ") and sent.headers["authorization"].count(".") == 2
    body = json.loads(sent.content)
    assert body["aps"]["alert"] == {"title": "Backup failed", "body": "Exit code 2."}
    assert body["aps"]["sound"] == "default" and body["aps"]["thread-id"] == "home-server"
    assert body["orbital"] == {"alert_id": "backup-failed", "section_id": "home-server"}


def test_dead_tokens_are_removed(home, key, monkeypatch):
    configure(home, monkeypatch)
    apple = Apple((410, "Unregistered"), (400, "BadDeviceToken"))
    service, store = make_apns(apple)
    assert asyncio.run(service.send_alerts([ALERT]))["removed"] == 2
    assert [row["apns_token"] for row in store.load_devices()] == [None, None]
    assert service.devices() == []
    other = Apple((400, "BadTopic"))
    service, store = make_apns(other, devices=(PHONE,))
    assert asyncio.run(service.send_alerts([ALERT]))["failed"] == 1
    phone = next(row for row in store.load_devices() if row["device_id"] == "d0")
    assert phone["apns_token"] == PHONE, "only a dead-token answer removes it"


def test_a_403_marks_push_unhealthy_and_stops_sending(home, key, monkeypatch):
    configure(home, monkeypatch)
    clock = Clock()
    apple = Apple((403, "InvalidProviderToken"))
    service, _ = make_apns(apple, clock)
    assert asyncio.run(service.send_alerts([ALERT]))["failed"] == 1
    status = service.status()
    assert status["healthy"] is False and "APNS_KEY_ID" in status["error"]
    assert asyncio.run(service.send_alerts([ALERT]))["skipped"] == 1
    assert len(apple.requests) == 1
    clock.now += apns_module.COOLDOWN + 1
    assert asyncio.run(service.send_alerts([ALERT]))["sent"] == 2, "tried again after the cooldown"


def test_a_network_error_is_retried_once(home, key, monkeypatch):
    configure(home, monkeypatch)
    monkeypatch.setattr(apns_module, "RETRY_DELAY", 0)
    apple = Apple(httpx.ConnectError("down"), (200, None), httpx.ConnectError("down"), httpx.ConnectError("down"))
    service, _ = make_apns(apple)
    assert asyncio.run(service.send_alerts([ALERT])) == {"sent": 1, "removed": 0, "failed": 1, "skipped": 0}
    assert len(apple.requests) == 4


def test_no_send_when_disabled_or_missing_anything(home, key, monkeypatch):
    configure(home, monkeypatch, enabled=False)
    apple = Apple()
    service, _ = make_apns(apple)
    assert service.status()["enabled"] is False and service.status()["configured"] is True
    assert asyncio.run(service.send_alerts([ALERT]))["skipped"] == 1
    configure(home, monkeypatch)
    monkeypatch.delenv("APNS_TEAM_ID")
    service, _ = make_apns(apple)
    status = service.status()
    assert status["enabled"] is False and status["configured"] is False
    assert asyncio.run(service.send_alerts([ALERT]))["skipped"] == 1
    monkeypatch.setenv("APNS_TEAM_ID", TEAM)
    monkeypatch.setattr(apns_module, "extras_missing", lambda: "pip install 'orbital-agents[push]'")
    service, _ = make_apns(apple)
    assert service.status()["enabled"] is False
    assert "push extras" in " ".join(service.missing())
    assert apple.requests == []
    service.status()
    lines = [json.loads(line)["msg"] for line in (home / "logs" / "hub.log").read_text().splitlines()]
    missing_lines = [line for line in lines if line.startswith("direct Apple push is off")]
    assert missing_lines and len(set(missing_lines)) == len(missing_lines), "one line per distinct reason, not one per call"


def test_which_alerts_push_and_how(home):
    now = time.time()
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))
    assert should_push(ALERT, now)
    assert not should_push({**ALERT, "deliver_at": future}, now), "the app schedules future reminders itself"
    assert not should_push({**ALERT, "kind": "reminder", "repeats": {"hour": 9, "minute": 0}}, now)
    assert not should_push({**ALERT, "retracted": True}, now)
    quiet = payload({**ALERT, "interruption_level": "passive", "node_id": "nas", "launch_url": "orbital://open?section=home-server"})
    assert "sound" not in quiet["aps"] and quiet["aps"]["interruption-level"] == "passive"
    assert quiet["orbital"]["node_id"] == "nas" and quiet["orbital"]["launch_url"].startswith("orbital://")
    long = payload({**ALERT, "body": "x" * 5000})
    assert len(json.dumps(long)) < 4096


def test_logs_mask_tokens_and_never_hold_the_key_or_jwt(home, key, monkeypatch):
    configure(home, monkeypatch)
    apple = Apple((410, "Unregistered"), (403, "InvalidProviderToken"))
    service, _ = make_apns(apple)
    jwt = service.token()
    asyncio.run(service.send_alerts([ALERT]))
    log = (home / "logs" / "hub.log").read_text()
    assert "apns token removed" in log
    assert PHONE not in log and MAC not in log, "device tokens are masked"
    assert PHONE[:3] + "…" in log
    assert jwt not in log and jwt.split(".")[2] not in log
    assert "PRIVATE KEY" not in log


def test_new_alerts_go_out_in_the_background_through_the_api(home, key, monkeypatch):
    configure(home, monkeypatch, environment="production")
    client, hub = make_client()
    apple = Apple()
    hub.apns.client_factory = apple.factory()
    assert client.get("/v1/health").json()["apns"] == {"enabled": True, "configured": True, "environment": "production"}
    with client:
        reg = client.post("/v1/devices", headers=AUTH, json={"device_id": "phone", "platform": "ios", "apns_token": PHONE})
        assert reg.json()["apns"]["enabled"] is True
        res = client.post("/v1/alerts", headers=AUTH, json={"alerts": [ALERT]})
        assert res.status_code == 200
        deadline = time.monotonic() + 5
        while not apple.requests and time.monotonic() < deadline:
            client.portal.call(asyncio.sleep, 0.02)
        assert len(apple.requests) == 1
        assert apple.requests[0].url.host == "api.push.apple.com"
        again = client.post("/v1/alerts", headers=AUTH, json={"alerts": [ALERT]})
        assert again.status_code == 200
        client.portal.call(asyncio.sleep, 0.1)
        assert len(apple.requests) == 1, "a duplicate alert id is not pushed again"
