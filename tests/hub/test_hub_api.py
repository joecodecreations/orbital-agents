"""The data API: sign-in, sections and ops, validation, alerts, actions, pulse, media, sync, jobs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from hub_helpers import AUTH, make_client, section_body


@pytest.fixture
def api(ports):
    client, hub = make_client()
    yield client
    client.close()


def server_section(**extra) -> dict:
    return section_body(
        "home-server",
        title="Home server",
        layout="sidebar",
        nodes=[
            {"id": "nas", "title": "NAS", "order": 10, "layout": "tabs", "blocks": [], "tabs": [
                {"id": "overview", "title": "Overview", "blocks": [{"type": "paragraph", "id": "p", "text": "Healthy."}]},
                {"id": "disks", "title": "Disks", "blocks": []},
            ]},
            {"id": "router", "title": "Router", "order": 20, "layout": "page", "blocks": [], "tabs": []},
        ],
        **extra,
    )


def put(api, doc: dict):
    res = api.put(f"/v1/sections/{doc['id']}", headers=AUTH, json=doc)
    assert res.status_code == 200, res.text
    return res.json()


def ops(api, section_id: str, *items, **extra):
    return api.post(f"/v1/sections/{section_id}/ops", headers=AUTH, json={"schema": "orbital.ops.v1", "ops": list(items), **extra})


def test_health_and_info_are_open_everything_else_needs_auth(api):
    health = api.get("/v1/health")
    assert health.status_code == 200
    assert health.json()["service"] == "orbital-hub"
    info = api.get("/v1/hub/info").json()
    assert info["service"] == "orbital-hub"
    assert info["machine"] == "test-hub"
    assert info["hub_id"].startswith("hub-")
    assert api.get("/v1/hub/info").json()["hub_id"] == info["hub_id"], "the hub id is stable"
    for path in ("/v1/catalog", "/v1/sections/home-server", "/v1/contract", "/v1/alerts", "/v1/sync", "/v1/satellites"):
        res = api.get(path)
        assert res.status_code == 401, path
        assert res.json()["code"] == "unauthorized"
        assert res.json()["heal"]


def test_login_session_and_logout(api):
    bad = api.post("/v1/login", json={"username": "owner", "password": "nope"})
    assert bad.status_code == 401
    assert bad.json()["code"] == "bad_login"
    assert api.post("/v1/login", json={"username": "someone", "password": "correct horse"}).status_code == 401
    good = api.post("/v1/login", json={"username": "owner", "password": "correct horse"})
    assert good.status_code == 200
    token = good.json()["token"]
    session = {"Authorization": f"Bearer {token}"}
    assert api.get("/v1/catalog", headers=session).status_code == 200
    stored = (Path(api.app.state.hub.paths.hub_data) / "sessions.json").read_text()
    assert token not in stored, "sessions are stored hashed"
    assert api.post("/v1/logout", headers=session).status_code == 200
    assert api.get("/v1/catalog", headers=session).status_code == 401


def test_login_needs_credentials_configured(api, monkeypatch):
    monkeypatch.delenv("ORBITAL_HUB_PASSWORD")
    assert api.post("/v1/login", json={"username": "owner", "password": ""}).status_code == 401


def test_put_replaces_one_section_only_and_catalog_hides_blocks(api):
    alpha = section_body("alpha", title="Alpha", blocks=[{"type": "paragraph", "id": "p", "text": "one"}])
    beta = section_body("beta", title="Beta", order=11)
    put(api, alpha)
    put(api, beta)
    before = api.get("/v1/sections/beta", headers=AUTH).json()
    alpha["blocks"] = [{"type": "paragraph", "id": "p", "text": "two"}]
    assert put(api, alpha)["revision"] == 2
    after = api.get("/v1/sections/beta", headers=AUTH).json()
    assert (before["title"], before["revision"]) == (after["title"], after["revision"])
    assert api.get("/v1/sections/alpha", headers=AUTH).json()["blocks"][0]["text"] == "two"
    rows = api.get("/v1/catalog", headers=AUTH).json()["sections"]
    assert [row["id"] for row in rows] == ["alpha", "beta"]
    assert "blocks" not in rows[0] and "nodes" not in rows[0]


def test_spaces_group_sections(api):
    put(api, section_body("nas", space="home-lab", space_title="Home lab", space_icon="house"))
    put(api, section_body("router", space="home-lab", order=20))
    put(api, section_body("weather"))
    catalog = api.get("/v1/catalog", headers=AUTH).json()
    assert catalog["spaces"] == [{"id": "home-lab", "title": "Home lab", "icon": "house", "sections": ["nas", "router"]}]
    bad = api.put("/v1/sections/x", headers=AUTH, json=section_body("x", space="Not A Slug"))
    assert bad.status_code == 400
    moved = ops(api, "weather", {"op": "merge_section", "value": {"space": "outdoors"}})
    assert moved.status_code == 200, moved.text
    assert {s["id"] for s in api.get("/v1/catalog", headers=AUTH).json()["spaces"]} == {"home-lab", "outdoors"}


def test_upsert_node_keeps_siblings_and_blocks_are_addressed_by_id(api):
    put(api, server_section())
    res = ops(api, "home-server", {"op": "upsert_node", "node": {"id": "printer", "title": "Printer", "order": 30, "layout": "page"}})
    assert res.status_code == 200, res.text
    assert [n["id"] for n in res.json()["section"]["nodes"]] == ["nas", "router", "printer"]
    chart = {"type": "chart", "id": "temps", "chart": "line", "series": [{"name": "disk", "points": [{"x": "09:00", "y": 41}]}]}
    res = ops(api, "home-server", {"op": "upsert_block", "node_id": "nas", "tab_id": "disks", "block": chart})
    assert res.status_code == 200, res.text
    nas = next(n for n in res.json()["section"]["nodes"] if n["id"] == "nas")
    assert [b["id"] for b in nas["tabs"][1]["blocks"]] == ["temps"]
    res = ops(api, "home-server", {"op": "delete_block", "node_id": "nas", "tab_id": "disks", "block_id": "temps"})
    nas = next(n for n in res.json()["section"]["nodes"] if n["id"] == "nas")
    assert nas["tabs"][1]["blocks"] == []
    top = ops(api, "home-server", {"op": "upsert_block", "block": chart})
    assert top.status_code == 200, "no node_id: the section's own blocks, drawn above the sidebar"
    assert [b["id"] for b in top.json()["section"]["blocks"]] == ["temps"]
    wrong = ops(api, "home-server", {"op": "upsert_block", "tab_id": "disks", "block": chart})
    assert wrong.status_code == 400
    assert "node_id is required" in wrong.json()["error"]


def test_delete_node_and_reorder(api):
    body = section_body("reorder-me", layout="sidebar", nodes=[
        {"id": "a", "title": "A", "order": 1, "layout": "page", "blocks": [], "tabs": []},
        {"id": "b", "title": "B", "order": 2, "layout": "page", "blocks": [], "tabs": []},
        {"id": "c", "title": "C", "order": 3, "layout": "page", "blocks": [], "tabs": []},
    ])
    put(api, body)
    moved = ops(api, "reorder-me", {"op": "reorder_nodes", "ids": ["c", "a", "b"]})
    assert {n["id"]: n["order"] for n in moved.json()["section"]["nodes"]} == {"c": 10, "a": 20, "b": 30}
    gone = ops(api, "reorder-me", {"op": "delete_node", "node_id": "b"})
    assert [n["id"] for n in gone.json()["section"]["nodes"]] == ["c", "a"]
    deleted = ops(api, "reorder-me", {"op": "delete_section"})
    assert deleted.status_code == 200
    assert api.get("/v1/sections/reorder-me", headers=AUTH).status_code == 404


def test_revision_conflict_leaves_the_section_alone(api):
    current = put(api, server_section())["revision"]
    res = ops(api, "home-server", {"op": "merge_section", "value": {"title": "Should not stick"}}, if_revision=current + 9)
    assert res.status_code == 409
    assert res.json()["revision"] == current
    assert res.json()["code"] == "revision_conflict"
    again = api.get("/v1/sections/home-server", headers=AUTH).json()
    assert again["revision"] == current
    assert again["title"] != "Should not stick"


def test_invalid_ids_and_bad_bodies(api):
    res = api.put("/v1/sections/Bad_ID", headers=AUTH, json=section_body("Bad_ID"))
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_value"
    assert res.json()["contract"] == "/v1/contract"
    assert res.json()["detail"] == res.json()["error"]
    put(api, server_section())
    nope = ops(api, "home-server", {"op": "nope"})
    assert nope.json()["code"] == "unknown_op"
    schema = api.post("/v1/sections/home-server/ops", headers=AUTH, json={"schema": "other.v1", "ops": []})
    assert schema.json()["code"] == "bad_schema"
    garbage = api.post("/v1/sections/home-server/ops", headers={**AUTH, "Content-Type": "application/json"}, content=b"{not json")
    assert garbage.status_code == 400
    missing = ops(api, "no-such-page", {"op": "merge_section", "value": {"title": "x"}})
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_validate_does_not_write_and_warns_on_unrendered_types(api):
    before = put(api, server_section())["revision"]
    bad = api.post("/v1/sections/home-server/validate", headers=AUTH, json={"schema": "orbital.ops.v1", "ops": [{"op": "nope"}]})
    assert bad.status_code == 400
    assert bad.json()["code"] == "unknown_op"
    warned = api.post("/v1/sections/home-server/validate", headers=AUTH,
                      json={"section": section_body("ignored", blocks=[{"type": "sparkle", "id": "s"}])})
    assert warned.status_code == 200, warned.text
    assert warned.json()["would_write"] is False
    assert warned.json()["warnings"][0]["type"] == "sparkle"
    checked = api.post("/v1/sections/home-server/validate", headers=AUTH,
                       json={"schema": "orbital.ops.v1", "ops": [{"op": "merge_section", "value": {"summary": "ok"}}]})
    assert checked.json()["would_write"] is True
    assert api.get("/v1/sections/home-server", headers=AUTH).json()["revision"] == before


def test_contract_lists_blocks_actions_and_limits(api, ports):
    body = api.get("/v1/contract", headers=AUTH).json()
    assert body["schema"] == "orbital.contract.v1"
    for name in ("heading", "chart", "metrics", "buttons", "product", "ledger", "tasks", "series", "insight_cards", "videos"):
        assert name in body["app_renders"]
    assert "product" in body["aggregated_blocks"]
    assert body["versions"]["section"] == "orbital.section.v1"
    assert body["limits"]["media_bytes"] == 8 * 1024 * 1024
    assert "merge_node" in body["ops"]
    assert body["actions"]["types"]


def test_structured_block_errors_name_the_field(api):
    put(api, server_section())

    def push(block: dict):
        return ops(api, "home-server", {"op": "upsert_block", "node_id": "router", "block": block})

    bad_status = push({"type": "product", "id": "product", "releases": [{"platform": "ios", "status": "shipped"}]})
    assert bad_status.status_code == 400
    assert "releases[0].status" in bad_status.json()["error"]
    string_amount = push({"type": "ledger", "id": "ledger", "days": [{"date": "2026-10-01", "amount": "12.50"}]})
    assert "days[0].amount" in string_amount.json()["error"]
    bad_date = push({"type": "ledger", "id": "ledger", "days": [{"date": "10/01/2026", "amount": 1}]})
    assert "YYYY-MM-DD" in bad_date.json()["error"]
    nan = api.post("/v1/sections/home-server/ops", headers={**AUTH, "Content-Type": "application/json"}, content=(
        '{"schema": "orbital.ops.v1", "ops": [{"op": "upsert_block", "node_id": "router", '
        '"block": {"type": "ledger", "id": "ledger", "days": [{"date": "2026-10-01", "amount": NaN}]}}]}'))
    assert nan.status_code == 400, nan.text
    assert "finite" in nan.json()["error"]
    assert api.get("/v1/sync", headers=AUTH).status_code == 200
    twice = push({"type": "product", "id": "product", "releases": [
        {"id": "web", "platform": "web", "status": "live"}, {"id": "web", "platform": "web", "status": "pending"}]})
    assert "repeats" in twice.json()["error"]
    no_product = push({"type": "series", "id": "visits", "kind": "site", "company": "Personal", "metrics": [{"id": "v", "label": "Visits", "points": []}]})
    assert "product" in no_product.json()["error"]
    twice_day = push({"type": "series", "id": "visits", "kind": "site", "company": "Personal", "product": "Blog",
                      "metrics": [{"id": "v", "label": "Visits", "points": [{"x": "2026-10-05", "y": 1}, {"x": "2026-10-05", "y": 2}]}]})
    assert "repeats" in twice_day.json()["error"]
    bad_icon = ops(api, "home-server", {"op": "merge_node", "node_id": "router", "value": {"icon_image": "/home/someone/icon.png"}})
    assert "icon_image" in bad_icon.json()["error"]
    unknown_key = ops(api, "home-server", {"op": "merge_node", "node_id": "router", "value": {"tabs": []}})
    assert "merge_node cannot set tabs" in unknown_key.json()["error"]
    bad_tap = push({"type": "buttons", "id": "b", "items": [{"label": "Go", "style": "primary", "action": {"type": "post", "action": "NotDotted"}}]})
    assert bad_tap.status_code == 400
    assert "dotted" in bad_tap.json()["error"]
    good = push({"type": "buttons", "id": "b", "items": [{"id": "go", "label": "Go", "style": "primary", "action": {"type": "post", "action": "router.reboot"}}]})
    assert good.status_code == 200, good.text


def test_alerts_dedupe_ack_and_retract_revive(api):
    put(api, server_section())
    before = api.get("/v1/sections/home-server", headers=AUTH).json()["revision"]
    alert = {"schema": "orbital.alert.v1", "id": "disk-80", "kind": "event", "title": "Disk", "body": "81% full"}
    first = ops(api, "home-server", alerts=[alert])
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == before, "alerts alone do not bump the revision"
    assert api.post("/v1/alerts", headers=AUTH, json={"alerts": [alert]}).status_code == 200
    pending = api.get("/v1/alerts?status=pending", headers=AUTH).json()["alerts"]
    assert [row["id"] for row in pending].count("disk-80") == 1
    ack = api.post("/v1/alerts/ack", headers=AUTH, json={"ids": ["disk-80"]})
    assert ack.json()["ids"] == ["disk-80"]
    assert api.get("/v1/alerts?status=pending", headers=AUTH).json()["alerts"] == []
    assert "disk-80" in api.get("/v1/sync", headers=AUTH).json()["acked"]
    retract = api.post("/v1/alerts/retract", headers=AUTH, json={"ids": ["disk-80"]})
    assert retract.json()["retracted"] == ["disk-80"]
    assert "disk-80" in api.get("/v1/sync", headers=AUTH).json()["retracted"]
    again = api.post("/v1/alerts", headers=AUTH, json={"alerts": [alert]})
    assert again.status_code == 200
    assert [row["id"] for row in api.get("/v1/alerts", headers=AUTH).json()["alerts"]] == ["disk-80"], "a retracted alert sent again is new"
    bad = api.post("/v1/alerts", headers=AUTH, json={"alerts": [{**alert, "id": "x", "kind": "shout"}]})
    assert bad.status_code == 400


def test_sync_returns_newer_sections_only(api):
    put(api, server_section())
    put(api, section_body("weather"))
    catalog = api.get("/v1/catalog", headers=AUTH).json()["sections"]
    known = ",".join(f"{row['id']}:{row['revision']}" for row in catalog)
    quiet = api.get(f"/v1/sync?known={known}", headers=AUTH).json()
    assert quiet["sections"] == []
    assert quiet["hub"]["service"] == "orbital-hub"
    assert "satellites" in quiet and "jobs" in quiet
    ops(api, "weather", {"op": "merge_section", "value": {"summary": "Rain later"}})
    changed = api.get(f"/v1/sync?known={known}", headers=AUTH).json()["sections"]
    assert [doc["id"] for doc in changed] == ["weather"]


def test_prefs_are_shared(api):
    res = api.post("/v1/prefs", headers=AUTH, json={"prefs": {"monthly_goal": 6500, "home.space": "home-lab"}})
    assert res.status_code == 200, res.text
    assert api.get("/v1/prefs", headers=AUTH).json()["prefs"] == {"monthly_goal": 6500, "home.space": "home-lab"}
    api.post("/v1/prefs", headers=AUTH, json={"prefs": {"monthly_goal": None}})
    assert api.get("/v1/prefs", headers=AUTH).json()["prefs"] == {"home.space": "home-lab"}
    assert api.post("/v1/prefs", headers=AUTH, json={"prefs": {"Bad Key": 1}}).status_code == 400
    assert api.post("/v1/prefs", headers=AUTH, json={"prefs": {"list": [1]}}).status_code == 400


def tasks_section() -> dict:
    return section_body("chores", layout="sidebar", nodes=[
        {"id": "this-week", "title": "This week", "order": 10, "layout": "page", "tabs": [], "blocks": [
            {"type": "tasks", "id": "open", "add": {"label": "Add", "groups": [{"id": "house", "label": "House"}]}, "items": [
                {"id": "gutters", "title": "Clean the gutters", "done": False, "due": "2026-11-18", "group": "house"}]},
            {"type": "buttons", "id": "actions", "items": [
                {"id": "water", "label": "Water the garden", "style": "primary",
                 "action": {"type": "post", "action": "garden.water", "value": {"minutes": 10}}}]},
        ]},
        {"id": "everything", "title": "Everything", "order": 20, "layout": "page", "tabs": [], "blocks": [
            {"type": "tasks", "id": "open", "items": [{"id": "gutters", "title": "Clean the gutters"}]}]},
    ])


def test_action_patches_every_copy_then_owner_resolves(api):
    put(api, tasks_section())
    before = api.get("/v1/sections/chores", headers=AUTH).json()["revision"]
    res = api.post("/v1/sections/chores/actions", headers={**AUTH, "X-Orbital-Device": "phone"}, json={
        "schema": "orbital.action.v1", "type": "task.set_done", "item_id": "gutters",
        "block_id": "open", "node_id": "this-week", "value": {"done": True}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["revision"] == before + 1
    copies = [item for node in body["section"]["nodes"] for block in node["blocks"]
              if block["type"] == "tasks" for item in block["items"] if item["id"] == "gutters"]
    assert len(copies) == 2 and all(item["done"] and item["pending"] for item in copies)
    assert body["action"]["device"] == "phone"
    pending = api.get("/v1/sections/chores/actions", headers=AUTH).json()["actions"]
    assert [row["item_id"] for row in pending] == ["gutters"]
    done = api.post("/v1/sections/chores/actions/results", headers=AUTH,
                    json={"results": [{"id": pending[0]["id"], "status": "applied", "message": "written"}]})
    assert done.status_code == 200, done.text
    assert api.get("/v1/sections/chores/actions", headers=AUTH).json()["actions"] == []
    everything = api.get("/v1/sections/chores/actions?status=all", headers=AUTH).json()["actions"]
    assert everything[0]["status"] == "applied"
    unknown = api.post("/v1/sections/chores/actions/results", headers=AUTH, json={"results": [{"id": "act-nope", "status": "applied"}]})
    assert unknown.status_code == 404


def test_add_and_button_actions(api):
    put(api, tasks_section())
    added = api.post("/v1/sections/chores/actions", headers=AUTH, json={
        "type": "task.add", "block_id": "open", "node_id": "this-week",
        "value": {"title": "Fix the fence", "due": "2026-11-01", "group": "house", "remind_days": [1, 3, 3]}})
    assert added.status_code == 200, added.text
    row = added.json()["section"]["nodes"][0]["blocks"][0]["items"][0]
    assert row["title"] == "Fix the fence" and row["pending"] and row["group"] == "house"
    assert added.json()["action"]["value"]["remind_days"] == [3, 1]
    bad = api.post("/v1/sections/chores/actions", headers=AUTH, json={"type": "task.add", "value": {"due": "soon"}})
    assert bad.status_code == 400
    tap = api.post("/v1/sections/chores/actions", headers=AUTH, json={
        "type": "garden.water", "item_id": "water", "block_id": "actions", "node_id": "this-week", "value": {"minutes": 10}, "source": "button"})
    assert tap.status_code == 200, tap.text
    button = tap.json()["section"]["nodes"][0]["blocks"][1]["items"][0]
    assert button["pending"] is True
    queued = api.get("/v1/sections/chores/actions", headers=AUTH).json()["actions"]
    assert queued[-1]["type"] == "garden.water" and queued[-1]["value"] == {"minutes": 10}
    free_form = api.post("/v1/sections/chores/actions", headers=AUTH, json={"type": "lights.dim", "value": {"level": 30}})
    assert free_form.status_code == 200 and free_form.json()["action"]["value"] == {"level": 30}
    not_dotted = api.post("/v1/sections/chores/actions", headers=AUTH, json={"type": "dim", "value": {}})
    assert not_dotted.status_code == 400
    assert api.post("/v1/sections/nope/actions", headers=AUTH, json={"type": "a.b", "value": {}}).status_code == 404


def test_insight_cards_render_check_and_patch(api):
    card = {"id": "slow-site", "kind": "uptime", "severity": "high", "title": "The site got slow", "actions": [
        {"id": "investigate", "kind": "agent", "label": "Investigate", "brief": {"prompt": "Find why p95 doubled"}}]}
    put(api, section_body("insights", blocks=[{"type": "insight_cards", "id": "cards", "cards": [card]}]))
    res = api.post("/v1/sections/insights/actions", headers=AUTH, json={
        "type": "insight.agent.approve", "item_id": "slow-site", "value": {"action_id": "investigate"}})
    assert res.status_code == 200, res.text
    patched = res.json()["section"]["blocks"][0]["cards"][0]
    assert patched["state"] == "pending" and patched["actions"][0]["state"] == "pending"
    snooze = api.post("/v1/sections/insights/actions", headers=AUTH, json={"type": "insight.snooze", "item_id": "slow-site", "value": {"days": 99}})
    assert snooze.status_code == 400
    bad_card = ops(api, "insights", {"op": "upsert_block", "block": {"type": "insight_cards", "id": "cards", "cards": [{**card, "severity": "meh"}]}})
    assert "severity" in bad_card.json()["error"]


def test_pulse_keeps_newest_per_key_and_does_not_bump_revision(api):
    put(api, server_section())
    before = api.get("/v1/sections/home-server", headers=AUTH).json()["revision"]
    item = {"key": "home-server.cpu", "section_id": "home-server", "node_id": "nas", "label": "CPU now", "value": 3,
            "ttl_seconds": 180, "series": [{"x": "2026-10-05T10:00:00Z", "y": 1}]}
    assert api.post("/v1/pulse", headers=AUTH, json={"schema": "orbital.pulse.v1", "items": [item]}).status_code == 200
    assert api.post("/v1/pulse", headers=AUTH, json={"items": [{**item, "value": 5}]}).status_code == 200
    rows = api.get("/v1/pulse", headers=AUTH).json()["items"]
    assert [(row["key"], row["value"]) for row in rows] == [("home-server.cpu", 5)]
    assert api.get("/v1/sync", headers=AUTH).json()["pulse"][0]["value"] == 5
    assert api.get("/v1/sections/home-server", headers=AUTH).json()["revision"] == before
    gone = api.post("/v1/pulse", headers=AUTH, json={"remove": ["home-server.cpu"]})
    assert gone.json()["removed"] == ["home-server.cpu"]
    assert api.post("/v1/pulse", headers=AUTH, json={"items": [{**item, "section_id": "nope"}]}).status_code == 404
    assert "finite" in api.post("/v1/pulse", headers=AUTH, json={"items": [{**item, "value": "3"}]}).json()["error"]
    assert api.post("/v1/pulse", json={"items": [item]}).status_code == 401


def test_media_is_content_addressed(api):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    first = api.post("/v1/media", headers=AUTH, files={"file": ("a.png", png, "image/png")})
    second = api.post("/v1/media", headers=AUTH, files={"file": ("b.png", png, "image/png")})
    assert first.status_code == 200, first.text
    assert first.json()["url"] == second.json()["url"]
    assert api.get(first.json()["url"], headers=AUTH).content == png
    assert api.get(first.json()["url"]).status_code == 401
    assert api.get("/v1/media/../../etc", headers=AUTH).status_code == 404
    wrong = api.post("/v1/media", headers=AUTH, files={"file": ("a.txt", b"hello", "text/plain")})
    assert wrong.status_code == 400


def test_media_size_limit_comes_from_config(home):
    from hub_helpers import write_config

    write_config(home, extra="")
    (home / "config.toml").write_text((home / "config.toml").read_text().replace("[hub]\n", "[hub]\nmax_media_mb = 1\n"))
    client, hub = make_client()
    assert hub.max_media == 1024 * 1024
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024)
    res = client.post("/v1/media", headers=AUTH, files={"file": ("big.png", big, "image/png")})
    assert res.status_code == 413
    assert res.json()["code"] == "too_large"
    ok = client.post("/v1/media", headers=AUTH, files={"file": ("small.png", big[:2000], "image/png")})
    assert ok.status_code == 200
    assert client.get("/v1/contract", headers=AUTH).json()["limits"]["media_bytes"] == 1024 * 1024


def test_devices_register(api):
    token = "ab" * 32
    res = api.post("/v1/devices", headers=AUTH, json={"device_id": "phone-1", "name": "Phone", "apns_token": token.upper(), "platform": "iOS"})
    assert res.status_code == 200
    assert res.json()["apns"] == {"enabled": False, "configured": False, "environment": "development"}
    devices = json.loads((api.app.state.hub.paths.hub_data / "devices.json").read_text())
    assert devices[0]["device_id"] == "phone-1" and devices[0]["last_seen"]
    assert devices[0]["apns_token"] == token and devices[0]["platform"] == "ios"
    moved = api.post("/v1/devices", headers=AUTH, json={"device_id": "phone-2", "apns_token": token, "platform": "ios"})
    assert moved.status_code == 200
    devices = {row["device_id"]: row for row in json.loads((api.app.state.hub.paths.hub_data / "devices.json").read_text())}
    assert devices["phone-1"]["apns_token"] is None and devices["phone-2"]["apns_token"] == token, "a token belongs to one device"
    assert api.post("/v1/devices", headers=AUTH, json={"device_id": "mac", "platform": "mac"}).status_code == 200
    assert api.post("/v1/devices", headers=AUTH, json={"device_id": "x", "apns_token": "not hex!"}).status_code == 400
    assert api.post("/v1/devices", headers=AUTH, json={}).status_code == 400
    assert api.get("/v1/health").json()["apns"]["enabled"] is False
    assert api.get("/v1/hub/info").json()["apns"]["environment"] == "development"
    assert api.get("/v1/contract", headers=AUTH).json()["apns"]["configured"] is False


def write_scheduler_state(hub, heartbeat_age: float = 1.0) -> None:
    beat = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - heartbeat_age))
    hub.paths.scheduler_data.mkdir(parents=True, exist_ok=True)
    (hub.paths.scheduler_data / "state.json").write_text(json.dumps({"snapshot": {
        "schema": "orbital.jobs.v1",
        "scheduler": {"heartbeat_at": beat, "every_choices": [300, 900]},
        "groups": [{"id": "web", "title": "Web"}],
        "lanes": [],
        "jobs": [{"id": "uptime", "group": "web", "targets": [{"section": "website"}]},
                 {"id": "weather", "group": "outdoors", "targets": [{"section": "weather", "node": "today"}]}],
        "batch": None,
    }}))
    (hub.paths.scheduler_data / "runs").mkdir(exist_ok=True)
    (hub.paths.scheduler_data / "runs" / "uptime.log").write_text("checked 3 sites\nall up\n")


def test_jobs_snapshot_and_run_requests(api):
    hub = api.app.state.hub
    write_scheduler_state(hub)
    snap = api.get("/v1/jobs", headers=AUTH).json()
    assert snap["scheduler"]["up"] is True
    assert [job["id"] for job in snap["jobs"]] == ["uptime", "weather"]
    assert api.post("/v1/jobs/uptime/run", headers={**AUTH, "X-Orbital-Device": "mac"}).json()["queued"] == ["uptime"]
    assert api.post("/v1/jobs/nope/run", headers=AUTH).status_code == 404
    assert api.post("/v1/jobs/run-all", headers=AUTH, json={"section_id": "weather"}).json()["queued"] == ["weather"]
    assert api.post("/v1/jobs/run-all", headers=AUTH, json={"group": "web"}).json()["queued"] == ["uptime"]
    assert api.post("/v1/jobs/uptime/settings", headers=AUTH, json={"every_s": 900}).status_code == 200
    assert api.post("/v1/jobs/uptime/settings", headers=AUTH, json={"every_s": 7}).status_code == 400
    assert api.post("/v1/jobs/uptime/cancel", headers=AUTH).status_code == 200
    assert api.get("/v1/jobs/uptime/log", headers=AUTH).json()["lines"] == ["checked 3 sites", "all up"]
    requests = sorted((hub.paths.scheduler_data / "requests").glob("*.json"))
    kinds = [json.loads(path.read_text())["type"] for path in requests]
    assert sorted(kinds) == ["cancel", "run", "run", "run_all", "settings"]
    assert api.post("/v1/jobs/notify").status_code == 401
    assert api.post("/v1/jobs/notify", headers=AUTH).status_code == 200


def test_jobs_scheduler_down_when_heartbeat_old(api):
    write_scheduler_state(api.app.state.hub, heartbeat_age=600)
    assert api.get("/v1/jobs", headers=AUTH).json()["scheduler"]["up"] is False


def test_live_socket_needs_a_token_and_pushes_changes(ports):
    from starlette.websockets import WebSocketDisconnect

    client, hub = make_client()
    with client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/v1/live") as ws:
                ws.receive_json()
        assert refused.value.code == 4401
        with client.websocket_connect("/v1/live?token=" + AUTH["Authorization"].split()[1]) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["hub_id"] == hub.hub_id
            put(client, section_body("weather"))
            frames = [ws.receive_json(), ws.receive_json()]
            assert frames[0]["type"] == "section" and frames[0]["section"]["id"] == "weather"
            assert frames[1]["type"] == "catalog"
            ws.send_text("ping")
            assert ws.receive_json()["type"] == "ping"


def test_schema_files_are_orbital_and_their_examples_are_accepted(api):
    root = Path(__file__).resolve().parents[2] / "schema"
    names = sorted(path.name for path in root.glob("*.json"))
    assert names == ["orbital.action.v1.schema.json", "orbital.alert.v1.schema.json", "orbital.ops.v1.schema.json",
                     "orbital.pulse.v1.schema.json", "orbital.section.v1.schema.json"]
    schemas = {name.split(".")[1]: json.loads((root / name).read_text()) for name in names}
    for kind, schema in schemas.items():
        assert schema["properties"]["schema"] == {"const": f"orbital.{kind}.v1"}
    section = schemas["section"]["examples"][0]
    put(api, section)
    batch = schemas["ops"]["examples"][0]
    assert api.post(f"/v1/sections/{section['id']}/ops", headers=AUTH, json=batch).status_code == 200
    alert = schemas["alert"]["examples"][0]
    assert api.post("/v1/alerts", headers=AUTH, json={"alerts": [alert]}).status_code == 200
    for action in schemas["action"]["examples"]:
        assert api.post(f"/v1/sections/{section['id']}/actions", headers=AUTH, json=action).status_code == 200
    assert api.post("/v1/pulse", headers=AUTH, json=schemas["pulse"]["examples"][0]).status_code == 200


def test_progress_status_links_and_run_agent_buttons(api):
    page = section_body("lab", blocks=[
        {"type": "status", "id": "services", "title": "Services", "items": [
            {"id": "web", "label": "Web", "status": "up", "detail": "41 ms", "since": "2026-01-02T03:04:05Z", "url": "https://web.example.com"},
            {"id": "dns", "label": "DNS", "status": "Degraded"},
            {"id": "new", "label": "New box"}]},
        {"type": "progress", "id": "volumes", "items": [
            {"id": "ssd", "label": "SSD", "value": 38, "caption": "190 GB of 500 GB"},
            {"id": "backup", "label": "Backup", "value": 5.1, "max": 8, "display": "5.1 / 8 TB", "tone": "warning"}]},
        {"type": "links", "id": "links", "items": [
            {"label": "Router", "url": "https://router.example.com", "icon": "wifi.router"},
            {"url": "ssh://nas.example.com"}, {"label": "Open the runbook", "url": "orbital://open?section=runbooks"}]},
        {"type": "buttons", "id": "actions", "items": [
            {"id": "fix", "label": "Fix it", "style": "primary", "action": {
                "type": "run_agent", "machine": "lab-box", "agent": "claude", "cwd": "~/srv", "title": "Fix DNS", "prompt": "Find out why DNS is slow."}},
            {"id": "blank", "label": "Run an agent", "style": "secondary", "action": {"type": "run"}},
            {"id": "ask", "label": "Ask", "style": "link", "action": {"type": "desk", "prompt": "Is the lab healthy?"}}]},
    ])
    assert api.post("/v1/sections/lab/validate", headers=AUTH, json={"section": page}).json()["warnings"] == []
    put(api, page)

    def bad(block: dict) -> str:
        res = ops(api, "lab", {"op": "upsert_block", "block": block})
        assert res.status_code == 400, res.text
        return res.json()["error"]

    assert "status must be a status word" in bad({"type": "status", "id": "s", "items": [{"label": "Web", "status": "purple"}]})
    assert "label" in bad({"type": "status", "id": "s", "items": [{"status": "up"}]})
    assert "since" in bad({"type": "status", "id": "s", "items": [{"label": "Web", "since": "yesterday"}]})
    assert "value" in bad({"type": "progress", "id": "p", "items": [{"label": "Disk", "value": "38%"}]})
    assert "max must be more than 0" in bad({"type": "progress", "id": "p", "items": [{"label": "Disk", "value": 1, "max": 0}]})
    assert "tone" in bad({"type": "progress", "id": "p", "items": [{"label": "Disk", "value": 1, "tone": "red"}]})
    assert "url" in bad({"type": "links", "id": "l", "items": [{"label": "No url"}]})
    assert "url" in bad({"type": "links", "id": "l", "items": [{"url": "javascript:alert(1)"}]})
    assert "repeats" in bad({"type": "links", "id": "l", "items": [{"url": "https://a.example.com"}, {"url": "https://a.example.com"}]})
    assert "machine" in bad({"type": "buttons", "id": "b", "items": [{"label": "Go", "action": {"type": "run_agent", "machine": "x" * 65}}]})
    assert "prompt" in bad({"type": "buttons", "id": "b", "items": [{"label": "Go", "action": {"type": "run_agent", "prompt": "x" * 100_001}}]})
    assert "type must be one of" in bad({"type": "buttons", "id": "b", "items": [{"label": "Go", "action": {"type": "grok", "prompt": "x"}}]})
    contract = api.get("/v1/contract", headers=AUTH).json()
    assert {"progress", "status", "links"} <= set(contract["app_renders"])
    assert "status" in contract["aggregated_blocks"]
    videos = next(row["summary"] for row in contract["block_types"] if row["type"] == "videos")
    assert "platform" not in videos.lower() and "post" not in videos.lower()


def test_space_is_the_name_company_still_works(api):
    put(api, section_body("shop", blocks=[
        {"type": "series", "id": "visits", "kind": "site", "space": "Shop", "product": "Store",
         "metrics": [{"id": "v", "label": "Visits", "points": [{"x": "2026-01-01", "y": 3}]}]},
        {"type": "series", "id": "old", "kind": "site", "company": "Shop", "product": "Store",
         "metrics": [{"id": "v", "label": "Visits", "points": []}]},
        {"type": "series", "id": "none", "kind": "usage", "product": "Store", "metrics": [{"id": "v", "label": "Users", "points": []}]},
        {"type": "ledger", "id": "ledger", "space": "Shop", "days": [{"date": "2026-01-01", "amount": 12, "space": "Shop"}]},
        {"type": "product", "id": "product", "kind": "site", "space": "Shop"},
        {"type": "videos", "id": "clips", "space": "Shop", "product": "Store", "items": []},
    ]))
    long_space = ops(api, "shop", {"op": "upsert_block", "block": {"type": "product", "id": "product", "space": "x" * 81}})
    assert long_space.status_code == 400 and "space" in long_space.json()["error"]


def test_top_level_blocks_ride_along_with_tabs_and_sidebar_layouts(api):
    for layout, extra in (("tabs", {"tabs": [{"id": "one", "title": "One", "blocks": []}]}),
                          ("sidebar", {"nodes": [{"id": "n", "title": "N", "order": 1, "layout": "page", "blocks": [], "tabs": []}]})):
        page = section_body(f"top-{layout}", layout=layout, blocks=[{"type": "callout", "id": "banner", "tone": "warning", "text": "Maintenance tonight"}], **extra)
        saved = put(api, page)
        assert saved["blocks"][0]["id"] == "banner", layout
        moved = ops(api, f"top-{layout}", {"op": "upsert_block", "block": {"type": "paragraph", "id": "note", "text": "Back at 6."}})
        assert [b["id"] for b in moved.json()["section"]["blocks"]] == ["banner", "note"], layout
        gone = ops(api, f"top-{layout}", {"op": "delete_block", "block_id": "banner"})
        assert [b["id"] for b in gone.json()["section"]["blocks"]] == ["note"], layout
