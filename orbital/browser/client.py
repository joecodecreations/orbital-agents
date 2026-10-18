"""Python API for the Orbital Agents Browser Controller.

    from orbital.browser import BrowserClient

    b = BrowserClient()                       # this machine's Chrome
    b.navigate("https://example.com")
    print(b.eval("document.title"))
    b.screenshot("page.png")

    remote = BrowserClient(node="studio-mac") # a joined satellite with a browser
    remote.upload("input[type=file]", "report.pdf")   # the file is staged on that machine first

Every page action goes to the agent session's own Chrome window (session "main" unless you pass
`session=`), so it never lands in the window you are using. `call(action, **params)` runs any
extension action by name (see `status()["extension"]` or the README for the list).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from orbital.browser.nodes import Node, NodeError, hub_auth, resolve_node
from orbital.core.config import Config, load_config
from orbital.core.secrets import Secrets

# Bridge endpoint -> the action name the bridge answers through POST /command (the hub relay's path).
_HUB_ACTIONS = {"status": "bridge_status", "health": "health", "network": "network", "endpoints": "endpoints",
                "events": "events", "export": "export", "clear": "clear", "stage": "stage", "files": "files",
                "files/read": "files_read", "purge_staged": "purge_staged"}


class BrowserError(RuntimeError):
    """A browser command failed, or the bridge could not be reached."""

    def __init__(self, message: str, status: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class BrowserClient:
    """Drive Chrome on one node. `node` is "local" or a satellite's name, machine or id.

    via: "auto" (direct to the remote bridge when ORBITAL_BROWSER_TOKEN is set, else through the
    hub), "direct" or "hub". Local nodes are always direct.
    """

    def __init__(self, node: str | Node = "local", *, session: str | None = None, timeout: float = 60.0,
                 via: str = "auto", token: str | None = None, url: str | None = None,
                 home: str | None = None, config: Config | None = None):
        self.cfg = config or load_config(home)
        self.secrets = Secrets(self.cfg.paths)
        if url:
            self.node = Node(name="custom", kind="custom", url=url.rstrip("/"))
        elif isinstance(node, Node):
            self.node = node
        else:
            try:
                self.node = resolve_node(node, self.cfg)
            except NodeError as exc:
                raise BrowserError(str(exc)) from exc
        if via not in ("auto", "direct", "hub"):
            raise ValueError("via must be auto, direct or hub")
        self.via = via
        self.session = session
        self.timeout = float(timeout)
        self.token = token if token is not None else self.secrets.get("ORBITAL_BROWSER_TOKEN")
        self._route: str | None = None

    def __repr__(self) -> str:
        return f"BrowserClient(node={self.node.name!r}, url={self.node.url!r})"

    # ── transport ─────────────────────────────────────────────────

    def _routes(self) -> list[str]:
        if self.node.kind in ("local", "custom"):
            return ["direct"]
        if self._route:
            return [self._route]
        if self.via == "direct":
            return ["direct"]
        if self.via == "hub":
            return ["hub"]
        return ["direct", "hub"] if (self.token and self.node.url) else ["hub"]

    def _direct(self, method: str, endpoint: str, *, body: dict | None, query: dict | None,
                content: Any, headers: dict | None, timeout: float) -> httpx.Response:
        h = {"Accept": "application/json", **(headers or {})}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        url = f"{self.node.url}/{endpoint.lstrip('/')}"
        if content is not None:
            return httpx.request(method, url, params=query, content=content, headers=h, timeout=timeout)
        if method == "GET":
            return httpx.get(url, params=query, headers=h, timeout=timeout)
        return httpx.request(method, url, params=query, json=body or {}, headers=h, timeout=timeout)

    def _hub(self, endpoint: str, *, body: dict | None, query: dict | None, timeout: float) -> httpx.Response:
        hub_url, hub_token = hub_auth(self.cfg, self.secrets)
        if not hub_url or not self.node.satellite_id:
            raise BrowserError("no hub relay available for this node")
        if endpoint.strip("/") == "command":
            # The relay is per action: /v1/satellites/<id>/browser/<action> with the params as body.
            body = body or {}
            command = str(body.get("action") or "")
            payload = dict(body.get("params") or {})
            if body.get("timeout") is not None:
                payload["_timeout"] = body["timeout"]
        else:
            # The bridge's own endpoints are actions too (the hub always forwards to /command).
            command = _HUB_ACTIONS.get(endpoint.strip("/"), endpoint.strip("/").replace("/", "_"))
            payload = {**(query or {}), **(body or {})}
        h = {"Accept": "application/json"}
        if hub_token:
            h["Authorization"] = f"Bearer {hub_token}"
        url = f"{hub_url.rstrip('/')}/v1/satellites/{self.node.satellite_id}/browser/{command}"
        return httpx.post(url, json=payload, headers=h, timeout=timeout)

    def _request(self, method: str, endpoint: str, *, body: dict | None = None, query: dict | None = None,
                 content: Any = None, headers: dict | None = None, timeout: float | None = None) -> dict:
        timeout = timeout or self.timeout + 15
        errors = []
        for route in self._routes():
            try:
                if route == "direct":
                    r = self._direct(method, endpoint, body=body, query=query, content=content, headers=headers, timeout=timeout)
                else:
                    if content is not None:
                        raise BrowserError("raw uploads cannot go through the hub relay; use via='direct'")
                    r = self._hub(endpoint, body=body, query=query, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise BrowserError(f"the bridge did not answer within {timeout:g} s ({self.node.name})") from exc
            except httpx.HTTPError as exc:
                errors.append(f"{route}: {exc}")
                continue
            if route == "direct" and r.status_code in (401, 403) and len(self._routes()) > 1:
                errors.append(f"direct: HTTP {r.status_code}")
                continue
            self._route = route if self.node.kind == "satellite" else None
            return self._decode(r)
        where = self.node.url or self.node.name
        hint = " Is the bridge running? Start it with `orbital browser serve`." if self.node.is_local else ""
        raise BrowserError(f"could not reach the browser bridge on {self.node.name} ({where}): " + "; ".join(errors) + hint)

    @staticmethod
    def _decode(r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            raise BrowserError(f"HTTP {r.status_code}: {r.text[:300]}", status=r.status_code) from None
        if not isinstance(data, dict):
            data = {"ok": r.status_code < 400, "result": data}
        if r.status_code >= 400 or data.get("ok") is False:
            raise BrowserError(str(data.get("error") or data.get("detail") or f"HTTP {r.status_code}"),
                               status=r.status_code, payload=data)
        return data

    # ── generic ───────────────────────────────────────────────────

    def call(self, action: str, timeout: float | None = None, **params: Any) -> Any:
        """Run one extension action and return its result (raises BrowserError when it fails)."""
        params = {k: v for k, v in params.items() if v is not None}
        if self.session and "session" not in params and "tabId" not in params:
            params["session"] = self.session
        t = float(timeout if timeout is not None else self.timeout)
        data = self._request("POST", "/command", body={"action": action, "params": params, "timeout": t},
                             timeout=t + 15)
        return data.get("result")

    command = call

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=10)

    def status(self, *, extension: bool = True) -> dict:
        """Bridge status, plus the extension's own status when it is connected."""
        data = self._request("GET", "/status", timeout=15)
        if extension and (data.get("extension") or {}).get("connected"):
            try:
                data["extension_status"] = self.call("status", timeout=5)
            except BrowserError as exc:
                data["extension_status"] = {"error": str(exc)}
        return data

    # ── tabs and pages ────────────────────────────────────────────

    def tabs(self) -> list[dict]:
        return self.call("list_tabs") or []

    def navigate(self, url: str, *, new_tab: bool = False, tab_id: int | None = None, session: str | None = None,
                 wait: bool = True, timeout: float | None = None) -> dict:
        t = timeout or max(self.timeout, 45)
        return self.call("navigate", url=url, new_tab=new_tab or None, tabId=tab_id, session=session,
                         wait=None if wait else False, timeoutMs=int(t * 1000) - 2000, timeout=t)

    def reload(self, **params: Any) -> dict:
        return self.call("reload", **params)

    def close_tab(self, tab_id: int | None = None, *, force: bool = False) -> dict:
        return self.call("close_tab", tabId=tab_id, force=force or None)

    def click(self, selector: str, *, index: int = 0, trusted: bool = False, session: str | None = None) -> dict:
        return self.call("click", selector=selector, index=index or None, trusted=trusted or None, session=session)

    def click_text(self, text: str, *, exact: bool = False) -> dict:
        return self.call("click_text", text=text, exact=exact or None)

    def type(self, selector: str, text: str, *, clear: bool = False, human: bool = False,
             session: str | None = None) -> dict:
        t = max(self.timeout, len(text) * 0.25 + 30) if human else None
        return self.call("type", selector=selector, text=text, clear=clear or None, human=human or None,
                         session=session, timeout=t)

    def press(self, key: str, **params: Any) -> dict:
        return self.call("hotkey", key=key, **params)

    def eval(self, js: str, *, world: str | None = None) -> Any:
        result = self.call("eval", code=js, world=world)
        return (result or {}).get("value") if isinstance(result, dict) else result

    def text(self, selector: str | None = None, max_len: int | None = None) -> str:
        return (self.call("text", selector=selector, maxLen=max_len) or {}).get("text", "")

    def dom(self, selector: str | None = None, max_len: int | None = None) -> dict:
        return self.call("dom", selector=selector, maxLen=max_len)

    def links(self) -> list[dict]:
        return self.call("links") or []

    def screenshot(self, out: str | Path | None = None, *, full_page: bool = False) -> bytes:
        """PNG bytes of the session's tab; also written to `out` when given."""
        result = self.call("screenshot", fullPage=full_page or None, timeout=max(self.timeout, 60))
        data_url = (result or {}).get("dataUrl") or ""
        if "," not in data_url:
            raise BrowserError("the extension returned no image")
        png = base64.b64decode(data_url.split(",", 1)[1])
        if out:
            path = Path(out).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
        return png

    def wait_for(self, text: str | None = None, *, selector: str | None = None, url: str | None = None,
                 timeout: float = 15.0, gone: bool = False) -> dict:
        return self.call("wait_for", text=text, selector=selector, url=url, gone=gone or None,
                         timeoutMs=int(timeout * 1000), timeout=timeout + 10)

    def window(self, op: str = "status", **params: Any) -> dict:
        return self.call("automation_window", op=op, **params)

    def capture(self, on: bool = True) -> dict:
        return self.call("set_capture", enabled=bool(on))

    # ── network capture (answered by the bridge) ───────────────────

    def network(self, host: str | None = None, *, path: str | None = None, limit: int = 200,
                api_only: bool = False) -> list[dict]:
        q = {"limit": limit, "api_only": 1 if api_only else 0}
        if host:
            q["host"] = host
        if path:
            q["path"] = path
        return self._request("GET", "/network", query=q).get("events", [])

    def endpoints(self, host: str | None = None) -> list[dict]:
        q = {"host": host} if host else None
        return self._request("GET", "/endpoints", query=q).get("endpoints", [])

    def events(self, limit: int = 100) -> list[dict]:
        return self._request("GET", "/events", query={"limit": limit}).get("events", [])

    def export(self) -> dict:
        return self._request("POST", "/export")

    def clear(self) -> dict:
        return self._request("POST", "/clear")

    # ── files ─────────────────────────────────────────────────────

    def stage(self, file: str | Path) -> str:
        """Copy a local file to the node's staged folder; returns the path there."""
        p = Path(file).expanduser().resolve()
        if not p.is_file():
            raise BrowserError(f"file not found: {p}")
        size = p.stat().st_size
        t = max(self.timeout, 60 + size / (2 * 1024 * 1024))
        if "direct" in self._routes():
            try:
                with p.open("rb") as fh:
                    data = self._request("POST", "/stage", query={"name": p.name}, content=fh,
                                         headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
                                         timeout=t)
                return data["path"]
            except BrowserError:
                if self.node.kind != "satellite" or self.via == "direct":
                    raise
                self._route = "hub"
        body = {"name": p.name, "content_base64": base64.b64encode(p.read_bytes()).decode("ascii")}
        return self._request("POST", "/stage", body=body, timeout=t)["path"]

    def upload(self, selector: str | None, file: str | Path | list, *, stage: bool | None = None,
               click_labels: list[str] | None = None, click_selector: str | None = None, timeout: float | None = None) -> dict:
        """Put file(s) into a file input. Remote nodes get the file staged on that machine first."""
        files = file if isinstance(file, (list, tuple)) else [file]
        local_paths = [Path(f).expanduser().resolve() for f in files]
        for p in local_paths:
            if not p.is_file():
                raise BrowserError(f"file not found: {p}")
        must_stage = stage if stage is not None else not self.node.is_local
        paths = [self.stage(p) for p in local_paths] if must_stage else [str(p) for p in local_paths]
        return self.call("upload_file", paths=paths, selector=selector or None, clickLabels=click_labels or None,
                         clickSelector=click_selector, timeout=timeout or max(self.timeout, 90))

    def files(self, path: str | None = None, limit: int = 50) -> dict:
        q = {"limit": limit}
        if path:
            q["path"] = path
        return self._request("GET", "/files", query=q)

    def pull(self, remote_path: str, dest: str | Path) -> Path:
        """Copy a file from the node (its ~/Downloads or staged folder) to `dest` here."""
        data = self._request("POST", "/files/read", body={"path": remote_path}, timeout=max(self.timeout, 120))
        out = Path(dest).expanduser()
        if out.is_dir():
            out = out / data.get("name", "download.bin")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(data.get("content_base64") or ""))
        return out


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
