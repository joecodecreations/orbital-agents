"""The bridge methods the new screens call, against a fake HubClient (no network, no keychain)."""

from __future__ import annotations

import pytest

from orbital_desktop import api as api_mod
from orbital_desktop.api import Api
from orbital_desktop.hub import HubError


class FakeClient:
    hub = "http://hub.test:7700"

    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(("GET", path))
        if path == "/v1/nav":
            return {"nav": {"schema": "orbital.nav.v1", "tabs": [{"id": "t", "title": "T", "target": {"kind": "view", "view": "home"}}], "sidebar": None}, "default": False}
        if path == "/v1/catalog":
            return {"sections": [{"id": "a"}], "spaces": [{"id": "s", "title": "S", "aliases": ["S LLC"]}]}
        if path.startswith("/v1/sections/"):
            return {"id": path.rsplit("/", 1)[-1], "blocks": []}
        if path == "/v1/jobs":
            return {"scheduler": {"up": True}, "jobs": [{"id": "weather"}], "groups": [], "extra": "dropped"}
        if path == "/v1/tracker/items":
            return {"items": [{"id": "x", "status": "active"}]}
        if path == "/v1/tracker/settings":
            raise HubError("no tracker", 404)
        if path == "/v1/desk":
            return {"messages": [{"id": "m", "role": "user", "text": "hi"}], "model": {"model": "m1"}, "tasks": []}
        raise AssertionError(path)

    def post(self, path, body=None, timeout=None):
        self.calls.append(("POST", path, body))
        if path == "/v1/desk/say":
            return '{"type": "delta", "text": "he"}\n{"type": "done", "message": {"text": "hello"}}\nnot json\n'
        return {"ok": True}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {"ok": True, "default": True}

    def raw(self, path):
        self.calls.append(("RAW", path))
        return "image/png", b"\x89PNG"


@pytest.fixture
def api():
    a = Api(no_auto=True)
    a._client = FakeClient()
    return a


def test_nav_catalog_section(api):
    nav = api.nav()
    assert nav["ok"] and nav["nav"]["tabs"][0]["id"] == "t" and nav["default"] is False
    cat = api.catalog()
    assert cat["spaces"][0]["aliases"] == ["S LLC"]
    assert api.section("a b")["section"]["id"] == "a%20b"     # ids are URL-quoted


def test_feeds_tracker_desk(api):
    feeds = api.feeds()
    assert feeds["jobs"] == [{"id": "weather"}] and "extra" not in feeds
    tr = api.tracker()
    assert tr["items"][0]["id"] == "x" and tr["settings"] == {}
    assert api.desk()["model"] == {"model": "m1"}
    said = api.desk_say("  hello ")
    assert said["ok"] and [e["type"] for e in said["events"]] == ["delta", "done"]
    assert ("POST", "/v1/desk/say", {"text": "hello"}) in api._client.calls
    assert api.desk_say("  ")["ok"] is False


def test_actions_and_writes(api):
    assert api.run_feed("weather")["ok"] and ("POST", "/v1/jobs/weather/run", None) in api._client.calls
    assert api.run_all_feeds("daily")["ok"] and ("POST", "/v1/jobs/run-all", {"group": "daily"}) in api._client.calls
    assert api.reset_nav()["ok"] and ("DELETE", "/v1/nav") in api._client.calls
    body = {"type": "task.set_done", "item_id": "x", "value": {"done": True}}
    assert api.section_action("todo", body)["ok"]
    assert api.section_action("todo", "nope")["ok"] is False


def test_media_is_cached_and_limited_to_hub_paths(api, monkeypatch):
    first = api.media("/v1/media/abc")
    assert first["url"].startswith("data:image/png;base64,")
    api.media("/v1/media/abc")
    assert [c for c in api._client.calls if c[0] == "RAW"] == [("RAW", "/v1/media/abc")]
    assert api.media("https://example.com/x.png")["ok"] is False
    monkeypatch.setattr(api_mod, "MAX_MEDIA_CACHE", 10)
    api.media("/v1/media/def")
    assert len(api._media) <= 1


def test_open_url_only_https(api, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert api.open_url("https://example.com")["ok"] and opened == ["https://example.com"]
    assert api.open_url("file:///etc/passwd")["ok"] is False and len(opened) == 1


def test_not_signed_in(api):
    api._client = None
    assert api.catalog() == {"ok": False, "error": "Not signed in", "status": 401, "code": "unauthorized"}
