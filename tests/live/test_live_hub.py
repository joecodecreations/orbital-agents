"""Live: the hub answers, auth works, and data goes in and out."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


def test_hub_health(hub):
    """hub: /v1/health answers"""
    r = hub.get("/v1/health", token="")
    assert r.status_code == 200, f"{hub.url}/v1/health → HTTP {r.status_code}"
    assert r.json().get("service") == "orbital-hub"


def test_hub_info(hub):
    """hub: /v1/hub/info identifies the hub"""
    r = hub.get("/v1/hub/info", token="")
    assert r.status_code == 200
    info = r.json()
    assert info.get("service") == "orbital-hub" and info.get("hub_id"), info


def test_hub_login(hub_role, hub, secrets):
    """hub: sign in with the configured username and password"""
    user, password = secrets.get("ORBITAL_HUB_USERNAME"), secrets.get("ORBITAL_HUB_PASSWORD")
    assert user and password, "ORBITAL_HUB_USERNAME / ORBITAL_HUB_PASSWORD missing from secrets.env"
    r = hub.post("/v1/login", token="", json={"username": user, "password": password})
    assert r.status_code == 200, f"login → HTTP {r.status_code}"
    session = r.json().get("token")
    assert session
    r = hub.get("/v1/catalog", token=session)
    assert r.status_code == 200, f"catalog with the session token → HTTP {r.status_code}"
    hub.post("/v1/logout", token=session)


def test_hub_rejects_bad_login(hub_role, hub):
    """hub: a wrong password is refused"""
    r = hub.post("/v1/login", token="", json={"username": "nobody", "password": "wrong-password-xyz"})
    assert r.status_code == 401


def test_automation_token(automation):
    """hub: the automation token is accepted"""
    r = automation.get("/v1/catalog")
    assert r.status_code == 200, f"/v1/catalog with ORBITAL_AUTOMATION_TOKEN → HTTP {r.status_code}"


def test_section_round_trip(automation, nonce):
    """hub: push a test section, read it back, delete it"""
    section = {
        "schema": "orbital.section.v1",
        "title": "Live test",
        "icon": "checkmark.seal",
        "order": 999,
        "layout": "page",
        "blocks": [{"type": "paragraph", "id": "note", "text": f"Created by orbital test --live ({nonce})."}],
        "tabs": [],
        "nodes": [],
    }
    r = automation.put(f"/v1/sections/{nonce}", json=section)
    assert r.status_code == 200, f"PUT section → HTTP {r.status_code}: {r.text[:200]}"
    try:
        r = automation.get(f"/v1/sections/{nonce}")
        assert r.status_code == 200
        doc = r.json()
        assert doc.get("title") == "Live test"
        assert nonce in str(doc.get("blocks"))
    finally:
        d = automation.delete(f"/v1/sections/{nonce}")
    assert d.status_code == 200, f"DELETE section → HTTP {d.status_code}"
    assert automation.get(f"/v1/sections/{nonce}").status_code == 404


def test_alert_round_trip(automation, nonce):
    """hub: raise a (silent) test alert and retract it"""
    alert = {"id": nonce, "kind": "event", "title": "Orbital Agents live test",
             "body": "Raised and retracted by orbital test --live.", "interruption_level": "passive"}
    r = automation.post("/v1/alerts", json={"alerts": [alert]})
    assert r.status_code == 200, f"POST alert → HTTP {r.status_code}: {r.text[:200]}"
    assert nonce in (r.json().get("alert_ids") or [])
    r = automation.post("/v1/alerts/retract", json={"ids": [nonce]})
    assert r.status_code == 200
    assert nonce in (r.json().get("retracted") or [])
