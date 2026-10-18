"""The example feeds pushing into the real hub through the real SDK (network sources mocked)."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from feeds_helpers import RSS, feed, github_handler, mock_http, weather_payload
from orbital.core.config import load_config
from orbital.core.secrets import Secrets

hub_module = pytest.importorskip("orbital.hub")
AUTOMATION = "feeds-automation-token-0123456789"


def free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if not 7700 <= port <= 7704:
            return port


class Hub:
    def __init__(self, home):
        self.port = free_port()
        (home / "config.toml").write_text(f'[system]\nmachine_name = "lab-box"\n[hub]\nport = {self.port}\n'
                                          '[tracker]\nenabled = false\n', encoding="utf-8")
        cfg = load_config(home)
        Secrets(cfg.paths).set("ORBITAL_AUTOMATION_TOKEN", AUTOMATION)
        app = hub_module.create_app(cfg, background=False)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None,
                                                    access_log=False, lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def get(self, path):
        return httpx.get(f"{self.url}{path}", headers={"Authorization": f"Bearer {AUTOMATION}"}, timeout=10)


def test_every_example_feed_lands_in_the_hub(home, monkeypatch):
    def web(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(200, json=weather_payload(rain_tomorrow=10))
        if request.url.host == "api.github.com":
            return github_handler(["v1.0"], {})(request)
        if request.url.host == "news.example":
            return httpx.Response(200, text=RSS)
        return httpx.Response(200, text="ok")

    for name in ("website_uptime", "github_repo", "weather", "rss_headlines"):
        mock_http(monkeypatch, feed(name), web)
    with Hub(home) as hub:
        assert feed("machine_health").main(["--home", str(home)]) == 0
        assert feed("website_uptime").main(["--home", str(home), "--url", "https://site.example"]) == 0
        assert feed("github_repo").main(["--home", str(home), "--repo", "example/widgets"]) == 0
        assert feed("weather").main(["--home", str(home)]) == 0
        assert feed("rss_headlines").main(["--home", str(home), "--feed", "https://news.example/rss"]) == 0
        assert feed("hello_blocks").main(["--home", str(home)]) == 0
        for section_id in ("machine-health", "website-uptime", "github", "weather", "headlines", "hello"):
            response = hub.get(f"/v1/sections/{section_id}")
            assert response.status_code == 200, (section_id, response.text)
            assert response.json()["id"] == section_id
        hello = hub.get("/v1/sections/hello").json()
        image = next(blk for tab in hello["tabs"] for blk in tab["blocks"] if blk["type"] == "image")
        assert image["src"].startswith("/v1/media/")
        numbers = next(tab for tab in hello["tabs"] if tab["id"] == "numbers")
        tiles = next(blk for blk in numbers["blocks"] if blk["id"] == "tiles")
        assert tiles["items"][0]["caption"].startswith("updated in place"), "patch_section reached the hub"
        catalog = hub.get("/v1/catalog").json()
        spaces = {s["id"]: s for s in catalog.get("spaces") or []}
        if spaces:  # the catalog groups sections into spaces (the app's home tiles)
            assert {"machine-health", "website-uptime"} <= set(spaces["home-lab"]["sections"])
            assert spaces["home-lab"]["title"] == "Home Lab"
        # A tap on the Ping button reaches the feed on its next run and is answered.
        tap = httpx.post(f"{hub.url}/v1/sections/hello/actions", headers={"Authorization": f"Bearer {AUTOMATION}"},
                         json={"schema": "orbital.action.v1", "type": "hello.ping", "item_id": "ping",
                               "block_id": "buttons", "tab_id": "actions", "value": {}})
        assert tap.status_code in (200, 201), tap.text
        assert feed("hello_blocks").main(["--home", str(home)]) == 0
        pending = hub.get("/v1/sections/hello/actions?status=pending").json().get("actions") or []
        assert not pending, "the feed answered the tap"
