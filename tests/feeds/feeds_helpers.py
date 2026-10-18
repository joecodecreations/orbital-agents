"""Helpers for the example-feed tests: import a feed by name, a fake HubClient, canned HTTP."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Callable

import httpx

from orbital.core.paths import REPO_ROOT

EXAMPLES = REPO_ROOT / "feeds" / "examples"
SECRET_ENV = ("ORBITAL_SATELLITE_TOKEN", "ORBITAL_HUB_TOKEN", "ORBITAL_JOIN_TOKEN", "ORBITAL_AUTOMATION_TOKEN",
              "ORBITAL_HUB_USERNAME", "ORBITAL_HUB_PASSWORD", "ORBITAL_BROWSER_TOKEN", "ORBITAL_HUB_URL",
              "GITHUB_TOKEN")
BLOCK_TYPES = {"heading", "paragraph", "bullets", "callout", "metrics", "key_value", "table", "timeline", "divider",
               "chart", "image", "hero", "buttons", "tasks", "ledger", "product", "series"}


def feed(name: str):
    if str(EXAMPLES) not in sys.path:
        sys.path.insert(0, str(EXAMPLES))
    return importlib.import_module(name)


class FakeHub:
    """Records what a feed sends. get_actions() hands out the queued taps once."""

    def __init__(self, actions: list[dict] | None = None):
        self.sections: list[dict] = []
        self.alerts: list[dict] = []
        self.patches: list[dict] = []
        self.results: list[dict] = []
        self.uploads: list[str] = []
        self.actions = list(actions or [])
        self.closed = False

    def put_section(self, section, section_id=None):
        self.sections.append(json.loads(json.dumps(section)))
        return {"ok": True, "revision": len(self.sections)}

    def upsert_block(self, section_id, block, *, node_id=None, tab_id=None):
        self.patches.append({"section": section_id, "block": block, "node_id": node_id, "tab_id": tab_id})
        return {"ok": True}

    def patch_section(self, section_id, ops, **kw):
        self.patches.append({"section": section_id, "ops": ops})
        return {"ok": True}

    def alert(self, title, body, section_id=None, level="info", **kw):
        self.alerts.append({"title": title, "body": body, "section_id": section_id, "level": level, **kw})
        return kw.get("id") or f"a{len(self.alerts)}"

    def upload_media(self, path, content_type=None):
        self.uploads.append(str(path))
        return {"id": "abc123", "url": "/v1/media/abc123", "content_type": "image/png"}

    def get_actions(self, section_id, status="pending", limit=100):
        out, self.actions = self.actions, []
        return out

    def post_action_result(self, section_id, action_id, status="applied", message=None):
        self.results.append({"id": action_id, "status": status, "message": message})
        return {"ok": True}

    def close(self):
        self.closed = True


def all_blocks(section: dict) -> list[dict]:
    out = list(section.get("blocks") or [])
    for tab in section.get("tabs") or []:
        out += tab.get("blocks") or []
    for node in section.get("nodes") or []:
        out += node.get("blocks") or []
        for tab in node.get("tabs") or []:
            out += tab.get("blocks") or []
    return out


def validate(section: dict) -> None:
    """The hub's own validator, when the hub package is present."""
    try:
        from orbital.hub.ops import validate_section
    except ImportError:  # pragma: no cover - the hub ships in the same repo
        import pytest
        pytest.skip("orbital.hub is not available")
    validate_section(json.loads(json.dumps(section)))


def mock_http(monkeypatch, module, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Point a feed's feedkit.http_client at canned responses."""
    kit = feed("feedkit")

    def client(timeout: float = 15.0):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout, follow_redirects=True,
                            headers={"User-Agent": kit.USER_AGENT})

    monkeypatch.setattr(kit, "http_client", client)


# --------------------------------------------------------------------------- canned sources


def github_handler(release_tags: list[str], limited: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if limited.get("on"):
            return httpx.Response(403, json={"message": "rate limit"}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"})
        path = request.url.path
        if path == "/repos/example/widgets":
            return httpx.Response(200, json={"name": "widgets", "description": "Widgets for everyone", "html_url": "https://github.com/example/widgets",
                                             "homepage": "https://widgets.example", "stargazers_count": 1234, "forks_count": 56,
                                             "subscribers_count": 7, "open_issues_count": 8, "language": "Python",
                                             "license": {"spdx_id": "MIT"}, "default_branch": "main", "pushed_at": "2026-10-01T10:00:00Z"})
        if path == "/repos/example/widgets/releases":
            return httpx.Response(200, json=[{"name": f"Widgets {t}", "tag_name": t, "html_url": f"https://github.com/example/widgets/releases/{t}",
                                              "published_at": "2026-10-01T10:00:00Z", "prerelease": False} for t in release_tags])
        if path == "/repos/example/widgets/issues":
            return httpx.Response(200, json=[{"number": 9, "title": "A bug", "html_url": "https://github.com/example/widgets/issues/9",
                                              "created_at": "2026-10-02T10:00:00Z", "comments": 2},
                                             {"number": 10, "title": "A PR", "pull_request": {}, "created_at": "2026-10-02T10:00:00Z"}])
        return httpx.Response(404, json={"message": "Not Found"})
    return handler


def weather_payload(rain_tomorrow: int) -> dict:
    hours = [f"2026-10-17T{h:02d}:00" for h in range(24)] + [f"2026-10-18T{h:02d}:00" for h in range(24)]
    days = [f"2026-10-{d}" for d in range(17, 24)]
    return {
        "timezone": "Europe/London",
        "current": {"time": "2026-10-17T09:00", "temperature_2m": 14.2, "relative_humidity_2m": 70,
                    "apparent_temperature": 13.1, "weather_code": 3, "wind_speed_10m": 12.0},
        "hourly": {"time": hours, "temperature_2m": [10 + (i % 8) for i in range(48)],
                   "precipitation_probability": [i % 100 for i in range(48)]},
        "daily": {"time": days, "weather_code": [3, 63, 0, 1, 2, 95, 71], "temperature_2m_max": [16, 15, 17, 18, 14, 13, 9],
                  "temperature_2m_min": [8, 9, 7, 6, 5, 4, 1], "precipitation_probability_max": [20, rain_tomorrow, 5, 0, 10, 60, 40],
                  "sunrise": [f"{d}T07:25" for d in days], "sunset": [f"{d}T18:05" for d in days]},
    }


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example News</title><link>https://news.example</link>
<item><title>First story</title><link>https://news.example/1</link><guid>n1</guid><pubDate>Fri, 16 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Second [story]</title><link>https://news.example/2</link><guid>n2</guid><pubDate>Fri, 16 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Example Blog</title>
<link href="https://blog.example/"/><entry><title>Hello Atom</title><link href="https://blog.example/hello"/><id>tag:blog,1</id>
<updated>2026-10-16T12:00:00Z</updated></entry></feed>"""
