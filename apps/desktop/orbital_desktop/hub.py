"""Talking to the Orbital hub: sign-in (pairing link, local auto-pair, password), discovery, calls.

Secrets rule: the automation token read for local auto-pairing and any password typed into the
form are used for one request and dropped. Neither is logged, returned to the UI or stored.
Only the session token the hub issues is kept (storage.save_token).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from . import APP_NAME, __version__

DEFAULT_PORT = 7700
LOCAL_HUB = f"http://127.0.0.1:{DEFAULT_PORT}"
USER_AGENT = f"OrbitalAgentsDesktop/{__version__} ({sys.platform})"
HEADERS = {"User-Agent": USER_AGENT, "X-Orbital-Device": APP_NAME}


class HubError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def normalize_hub(url: str) -> str:
    """'macbook.tailnet.ts.net' → 'http://macbook.tailnet.ts.net:7700'; strips paths and slashes."""
    url = (url or "").strip()
    if not url:
        raise HubError("Enter the hub address")
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HubError(f"Not a hub address: {url}")
    netloc = parsed.netloc
    if parsed.port is None and parsed.scheme == "http":
        netloc = f"{netloc}:{DEFAULT_PORT}"
    return f"{parsed.scheme}://{netloc}"


def parse_pair_link(link: str) -> tuple[str, str]:
    """orbital://connect?hub=<url>&pair=<code> (or any URL with those two params) → (hub, code)."""
    text = (link or "").strip()
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    hub = (query.get("hub") or [""])[0]
    code = (query.get("pair") or query.get("code") or [""])[0]
    if not hub or not code:
        raise HubError("That isn't a pairing link. Run `orbital pair --link` on the hub and paste the orbital://connect?... link it prints.")
    return normalize_hub(hub), code.strip()


def orbital_home() -> Path:
    return Path(os.environ.get("ORBITAL_HOME") or Path.home() / ".orbital-agents").expanduser()


def _read_secret(name: str) -> str | None:
    """One value from ~/.orbital-agents/secrets.env, or None. Never logged or printed."""
    path = orbital_home() / "secrets.env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().removeprefix("export ").strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


def local_hub_url() -> str:
    """This machine's own hub, when it runs one (from config.resolved.json), else the default."""
    try:
        data = json.loads((orbital_home() / "config.resolved.json").read_text(encoding="utf-8"))
        url = data.get("hub_url")
        if isinstance(url, str) and url.startswith("http"):
            parsed = urlparse(url)
            if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
                return url.rstrip("/")
        port = (data.get("hub") or {}).get("port")
        if isinstance(port, int):
            return f"http://127.0.0.1:{port}"
    except (OSError, ValueError, AttributeError):
        pass
    return LOCAL_HUB


def health(hub: str, timeout: float = 3.0) -> dict | None:
    try:
        r = httpx.get(f"{hub}/v1/health", timeout=timeout, headers=HEADERS)
        data = r.json() if r.status_code == 200 else None
        if isinstance(data, dict) and data.get("service") in ("orbital-hub", "orbital-agents"):
            return data
    except (httpx.HTTPError, ValueError):
        pass
    return None


def _answer(r: httpx.Response):
    try:
        data = r.json()
    except ValueError:
        data = None
    if r.status_code >= 400:
        message = None
        if isinstance(data, dict):
            message = data.get("error") or data.get("detail") or data.get("message")
            if isinstance(message, dict):
                message = message.get("message")
        code = data.get("code") if isinstance(data, dict) else None
        raise HubError(str(message or f"hub answered HTTP {r.status_code}"), r.status_code, code)
    return data if data is not None else r.text


def pair(hub: str, code: str) -> dict:
    """POST /v1/pair {code} → {token, username, hub_id}."""
    try:
        r = httpx.post(f"{hub}/v1/pair", json={"code": code}, timeout=10, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise HubError(f"Can't reach the hub at {hub}: {exc.__class__.__name__}") from None
    return _answer(r)


def login(hub: str, username: str, password: str) -> dict:
    try:
        r = httpx.post(f"{hub}/v1/login", json={"username": username, "password": password},
                       timeout=10, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise HubError(f"Can't reach the hub at {hub}: {exc.__class__.__name__}") from None
    if r.status_code == 401:
        raise HubError("Wrong username or password", 401, "bad_login")
    return _answer(r)


def local_auto_pair() -> dict | None:
    """On the hub machine itself: mint a pairing code with the automation token, then pair.

    Returns {hub, token, username, hub_id} or None when this machine can't (no secrets.env,
    no hub answering locally). The automation token stays in this function's local scope.
    """
    hub = local_hub_url()
    if not health(hub, timeout=2):
        return None
    automation = _read_secret("ORBITAL_AUTOMATION_TOKEN")
    if not automation:
        return None
    try:
        r = httpx.post(f"{hub}/v1/pair/new", json={}, timeout=8,
                       headers={**HEADERS, "Authorization": f"Bearer {automation}"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    code = (r.json() or {}).get("code")
    if not code:
        return None
    answer = pair(hub, code)
    return {"hub": hub, **answer}


def discover(timeout: float = 25.0) -> list[dict]:
    """Hubs for the address field: `orbital satellites discover --json` (Tailscale peers), plus localhost."""
    found: dict[str, dict] = {}
    local = local_hub_url()
    data = health(local, timeout=1.5)
    if data:
        found[local] = {"name": "This computer", "url": local, "hub_id": data.get("hub_id", ""), "is_self": True}
    exe = shutil.which("orbital")
    if exe:
        try:
            out = subprocess.run([exe, "satellites", "discover", "--json"], capture_output=True, text=True,
                                 timeout=timeout, **_no_window())
            text = out.stdout
            start = text.find("[")
            hubs = json.loads(text[start:]) if start >= 0 else []
            for h in hubs if isinstance(hubs, list) else []:
                if isinstance(h, dict) and h.get("url"):
                    url = normalize_hub(h["url"])
                    if any(v.get("hub_id") and v.get("hub_id") == h.get("hub_id") for v in found.values()) and h.get("is_self"):
                        continue
                    found.setdefault(url, {"name": h.get("name") or url, "url": url,
                                           "hub_id": h.get("hub_id", ""), "is_self": bool(h.get("is_self"))})
        except (OSError, subprocess.TimeoutExpired, ValueError, HubError):
            pass
    return list(found.values())


def _no_window() -> dict:
    """Don't flash a console window on Windows when running the orbital CLI."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


class HubClient:
    """Signed-in calls. One client per (hub, token); thread-safe (httpx.Client is)."""

    def __init__(self, hub: str, token: str) -> None:
        self.hub = hub
        self._client = httpx.Client(
            base_url=hub, timeout=httpx.Timeout(15, connect=6),
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
        )
        self._token = token

    @property
    def ws_url(self) -> str:
        return ("wss://" if self.hub.startswith("https://") else "ws://") + self.hub.split("://", 1)[1] + "/v1/live"

    def auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, **kw):
        try:
            r = self._client.request(method, path, **kw)
        except httpx.TimeoutException:
            raise HubError("The hub didn't answer in time") from None
        except httpx.HTTPError as exc:
            raise HubError(f"Can't reach the hub ({exc.__class__.__name__})") from None
        return _answer(r)

    def get(self, path: str, **params):
        return self._call("GET", path, params=params or None)

    def post(self, path: str, body: dict | None = None, timeout: float | None = None):
        kw = {"json": body or {}}
        if timeout:
            kw["timeout"] = timeout
        return self._call("POST", path, **kw)

    def delete(self, path: str):
        return self._call("DELETE", path)

    def text(self, path: str, **params) -> str:
        try:
            r = self._client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise HubError(f"Can't reach the hub ({exc.__class__.__name__})") from None
        if r.status_code >= 400:
            _answer(r)
        return r.text

    def raw(self, path: str) -> tuple[str, bytes]:
        """(content type, bytes) of a binary resource such as /v1/media/<id>."""
        try:
            r = self._client.get(path)
        except httpx.HTTPError as exc:
            raise HubError(f"Can't reach the hub ({exc.__class__.__name__})") from None
        if r.status_code >= 400:
            _answer(r)
        mime = (r.headers.get("content-type") or "application/octet-stream").split(";", 1)[0].strip()
        return mime, r.content

    def logout(self) -> None:
        try:
            self._client.post("/v1/logout", json={}, timeout=4)
        except httpx.HTTPError:
            pass
