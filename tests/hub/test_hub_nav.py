"""Navigation as data: extra bottom-bar items and the sidebar tree."""

from __future__ import annotations

from hub_helpers import AUTH, make_client

from orbital.cli.commands.nav import EXAMPLE


def test_default_then_save_then_reset(ports):
    client, hub = make_client()
    first = client.get("/v1/nav", headers=AUTH).json()
    assert first == {"nav": {"schema": "orbital.nav.v1", "tabs": [], "sidebar": None}, "default": True}
    saved = client.put("/v1/nav", headers=AUTH, json=EXAMPLE)
    assert saved.status_code == 200, saved.text
    got = client.get("/v1/nav", headers=AUTH).json()
    assert got["default"] is False
    assert [t["id"] for t in got["nav"]["tabs"]] == ["money", "marketing"]
    assert got["nav"]["tabs"][1]["children"][0]["target"] == {"kind": "section", "section": "social"}
    assert client.delete("/v1/nav", headers=AUTH).json()["default"] is True
    assert client.get("/v1/nav", headers=AUTH).json()["default"] is True


def test_needs_sign_in(ports):
    client, hub = make_client()
    assert client.get("/v1/nav").status_code == 401
    assert client.put("/v1/nav", json=EXAMPLE).status_code == 401


def test_bad_documents_are_refused(ports):
    client, hub = make_client()

    def put(doc):
        return client.put("/v1/nav", headers=AUTH, json=doc)

    assert put({"tabs": [{"id": "x", "title": "X"}]}).status_code == 400, "a leaf needs a target"
    assert put({"tabs": [{"id": "x", "title": "X", "target": {"kind": "view", "view": "nope"}}]}).status_code == 400
    assert put({"tabs": [{"id": "x", "title": "X", "target": {"kind": "link", "url": "javascript:alert(1)"}}]}).status_code == 400
    assert put({"tabs": [{"id": "x", "title": "X", "icon": "../etc", "target": {"kind": "view", "view": "home"}}]}).status_code == 400
    dup = {"id": "x", "title": "X", "target": {"kind": "view", "view": "home"}}
    assert put({"tabs": [dup, dup]}).status_code == 400
    deep: dict = {"id": "leaf", "title": "Leaf", "target": {"kind": "view", "view": "home"}}
    for level in range(8):
        deep = {"id": f"g{level}", "title": "Group", "children": [deep]}
    assert put({"sidebar": [deep]}).status_code == 400
    ok = put({"sidebar": [{"id": "g", "title": "Bucket", "icon": "/v1/media/abc123.png", "accent": "#33AAFF", "children": [dup]}]})
    assert ok.status_code == 200, ok.text
