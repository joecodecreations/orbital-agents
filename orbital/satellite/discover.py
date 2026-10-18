"""Find Orbital Agents hubs on the tailnet.

    from orbital.satellite.discover import find_hubs
    hubs = find_hubs(7700)   # [{"url", "name", "machine", "hub_id", "version", "host", "ip", "dns", "os"}]

Each online Tailscale peer is asked `GET http://<dns or ip>:<port>/v1/hub/info` (1.5 s timeout,
all peers in parallel). Peers that answer `"service": "orbital-hub"` are hubs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import httpx

from orbital.core.net import Peer, tailscale_peers

PROBE_TIMEOUT = 1.5


def _probe(base: str, timeout: float) -> dict | None:
    try:
        response = httpx.get(f"{base}/v1/hub/info", timeout=timeout)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        info = response.json()
    except ValueError:
        return None
    return info if isinstance(info, dict) and info.get("service") == "orbital-hub" else None


def _probe_peer(peer: Peer, port: int, timeout: float) -> dict | None:
    for address in [a for a in (peer.dns, peer.ip) if a]:
        host = f"[{address}]" if ":" in address else address
        base = f"http://{host}:{port}"
        info = _probe(base, timeout)
        if info:
            return {
                "url": base,
                "name": info.get("name") or peer.name,
                "machine": info.get("machine") or peer.name,
                "hub_id": info.get("hub_id"),
                "version": info.get("version"),
                "host": peer.name,
                "dns": peer.dns,
                "ip": peer.ip,
                "os": peer.os,
                "is_self": peer.is_self,
            }
    return None


def find_hubs(port: int, peers: Iterable[Peer] | None = None, timeout: float = PROBE_TIMEOUT,
              include_self: bool = False) -> list[dict]:
    """Hubs among the online Tailscale peers (pass `peers` to probe a given list instead)."""
    candidates = list(peers) if peers is not None else tailscale_peers(include_self=include_self)
    candidates = [p for p in candidates if p.online and (p.dns or p.ip)]
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(32, len(candidates))) as pool:
        results = list(pool.map(lambda p: _probe_peer(p, int(port), timeout), candidates))
    hubs = [r for r in results if r]
    hubs.sort(key=lambda h: (str(h.get("name") or ""), str(h.get("url"))))
    return hubs
