"""Browser nodes: which machines' Chrome you can drive.

A node is `local` (the bridge on this machine, http://127.0.0.1:<browser.bridge_port>) or any
satellite joined to the hub whose capabilities say `browser: true`. The list of satellites comes
from the hub: `GET <hub>/v1/satellites` with the automation token from secrets.env (on a
satellite without that token, the hub token it got at join is tried).

A remote node is reached either
  - directly: http://<satellite ip>:<capabilities.browser_port> with ORBITAL_BROWSER_TOKEN
    (that machine runs its bridge with browser.bind = "tailscale"), or
  - through the hub: POST <hub>/v1/satellites/<id>/browser/<command>, which forwards the
    call to that machine's bridge with the browser token.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from orbital.core.config import Config, load_config
from orbital.core.secrets import Secrets

LOCAL_NAMES = {"", "local", "this", "localhost", "self"}


class NodeError(RuntimeError):
    pass


@dataclass
class Node:
    name: str
    kind: str                      # "local" | "satellite"
    url: str                       # the bridge, reached directly
    satellite_id: str | None = None
    machine: str = ""
    status: str = ""               # hub's view: online | offline ("" for local)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        """True when the bridge is on this machine (local files can be used as they are)."""
        if self.kind == "local":
            return True
        if self.kind == "custom":
            return (urlparse(self.url).hostname or "") in ("127.0.0.1", "localhost", "::1")
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "url": self.url, "satellite_id": self.satellite_id,
                "machine": self.machine, "status": self.status}


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def _url_host(host: str) -> str:
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host


def local_node(cfg: Config) -> Node:
    port = int(cfg.get("browser.bridge_port", 7703))
    return Node(name="local", kind="local", url=f"http://127.0.0.1:{port}", machine=cfg.machine_name)


def hub_auth(cfg: Config, secrets: Secrets | None = None) -> tuple[str, str]:
    """(hub base URL, bearer token) for reading the satellite list and using the relay."""
    secrets = secrets or Secrets(cfg.paths)
    token = secrets.get("ORBITAL_AUTOMATION_TOKEN") or secrets.get("ORBITAL_HUB_TOKEN")
    return cfg.hub_url(), token


def fetch_satellites(hub_url: str, token: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    if not hub_url:
        raise NodeError("no hub configured (hub.url in config.toml) so only the local browser is available")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.get(f"{hub_url.rstrip('/')}/v1/satellites", headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise NodeError(f"could not reach the hub at {hub_url}: {exc}") from exc
    if r.status_code in (401, 403):
        raise NodeError("the hub refused the satellite list (check ORBITAL_AUTOMATION_TOKEN in secrets.env)")
    if r.status_code >= 400:
        raise NodeError(f"the hub answered HTTP {r.status_code} for /v1/satellites")
    try:
        data = r.json()
    except ValueError as exc:
        raise NodeError("the hub's satellite list is not JSON") from exc
    if isinstance(data, dict):
        data = data.get("satellites") or data.get("items") or []
    return [s for s in data if isinstance(s, dict)]


def satellite_node(sat: dict[str, Any]) -> Node | None:
    caps = sat.get("capabilities") or {}
    if not caps.get("browser"):
        return None
    sid = str(sat.get("satellite_id") or sat.get("id") or "")
    # The machine name is what people type after --node; the display name may have spaces.
    name = str(sat.get("machine") or sat.get("name") or sid)
    host = str(sat.get("ip") or sat.get("host") or "")
    if _loopback(host) and sat.get("host"):
        # The hub's own satellite joins over loopback; from any other machine use its Tailscale name.
        host = str(sat["host"])
    port = int(caps.get("browser_port") or 7703)
    return Node(name=name, kind="satellite", url=f"http://{_url_host(host)}:{port}" if host else "",
                satellite_id=sid or None, machine=str(sat.get("machine") or name),
                status=str(sat.get("status") or ""), raw=sat)


def _is_self(node: Node, cfg: Config) -> bool:
    me = cfg.machine_name.lower()
    return bool(me) and me in {node.machine.lower(), node.name.lower()}


def list_nodes(cfg: Config | None = None, *, remote: bool = True, timeout: float = 5.0) -> tuple[list[Node], str | None]:
    """[local, satellites with a browser...] and an error message when the hub could not be read."""
    cfg = cfg or load_config()
    nodes = [local_node(cfg)]
    if not remote:
        return nodes, None
    hub_url, token = hub_auth(cfg)
    try:
        sats = fetch_satellites(hub_url, token, timeout=timeout)
    except NodeError as exc:
        return nodes, str(exc)
    for sat in sats:
        node = satellite_node(sat)
        if node and not _is_self(node, cfg):
            nodes.append(node)
    return nodes, None


def resolve_node(name: str | None, cfg: Config | None = None, *, timeout: float = 5.0) -> Node:
    """`local` (or this machine's name) or a satellite matched by name, machine, id or host."""
    cfg = cfg or load_config()
    want = (name or "").strip().lower()
    if want in LOCAL_NAMES or want == cfg.machine_name.lower():
        return local_node(cfg)
    hub_url, token = hub_auth(cfg)
    sats = fetch_satellites(hub_url, token, timeout=timeout)
    for sat in sats:
        keys = {str(sat.get(k) or "").lower() for k in ("name", "machine", "satellite_id", "id", "host", "ip")}
        if want not in keys:
            continue
        node = satellite_node(sat)
        if node is None:
            raise NodeError(f"{name} has no browser bridge (capabilities.browser is false); "
                            "set browser.enabled = true there and run the bridge")
        if _is_self(node, cfg):
            return local_node(cfg)
        return node
    known = ", ".join(sorted({str(s.get("name") or s.get("machine") or "?") for s in sats})) or "none"
    raise NodeError(f"no satellite named {name!r} (known: {known})")
