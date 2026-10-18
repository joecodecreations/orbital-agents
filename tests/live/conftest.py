"""Operational checks against the installed system (`orbital test --live`).

These run only when ORBITAL_LIVE=1 (set by `orbital test --live`); a plain `pytest` skips them.
They use this machine's real config and secrets (ORBITAL_HOME) and the ports in its config.toml,
clean up what they create (a test section, alert, tracker item, browser tab), and skip with a
reason when a component is turned off here.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from orbital.core.config import load_config
from orbital.core.secrets import Secrets

LIVE = os.environ.get("ORBITAL_LIVE") == "1"


@pytest.fixture(autouse=True)
def _live_only():
    if not LIVE:
        pytest.skip("live check: run `orbital test --live` against an installed system")


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def secrets(cfg):
    return Secrets(cfg.paths)


class Hub:
    """Tiny HTTP helper for the hub with a bearer token."""

    def __init__(self, url: str, token: str = ""):
        self.url = url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, *, token: str | None = None, timeout: float = 10.0, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        tok = self.token if token is None else token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        return httpx.request(method, f"{self.url}{path}", headers=headers, timeout=timeout, **kw)

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)


@pytest.fixture(scope="session")
def hub(cfg, secrets) -> Hub:
    url = cfg.hub_url()
    if not url:
        pytest.skip("no hub configured (hub.url is empty on this satellite)")
    token = secrets.get("ORBITAL_AUTOMATION_TOKEN") if cfg.role == "hub" else ""
    return Hub(url, token)


@pytest.fixture
def hub_role(cfg):
    if cfg.role != "hub":
        pytest.skip("hub-only check: this machine is a satellite (run it on the hub)")


@pytest.fixture
def automation(hub_role, hub):
    if not hub.token:
        pytest.fail("ORBITAL_AUTOMATION_TOKEN is missing from secrets.env (re-run ./setup)")
    return hub


@pytest.fixture
def nonce() -> str:
    return f"orbital-live-{uuid.uuid4().hex[:10]}"
