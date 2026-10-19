"""Network helpers shared by every server: who may connect, and what Tailscale can see."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable

TAILSCALE_NETS = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")
LOOPBACK_NETS = ("127.0.0.0/8", "::1/128")


def allowed(client_ip: str | None, networks: Iterable[str]) -> bool:
    """True when client_ip falls inside one of `networks` (CIDR strings)."""
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    for net in networks:
        try:
            if ip in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def tailscale_bin() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles") or "C:\\Program Files"
        candidates = [os.path.join(program_files, "Tailscale", "tailscale.exe")]
    else:
        candidates = ["/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
                      "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]
    return next((cand for cand in candidates if shutil.which(cand)), None)


@dataclass
class Peer:
    name: str          # short host name
    dns: str           # MagicDNS name without the trailing dot
    ip: str            # first Tailscale IP
    os: str
    online: bool
    is_self: bool = False


def tailscale_status(timeout: float = 5.0) -> dict | None:
    exe = tailscale_bin()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def _peer(node: dict, is_self: bool) -> Peer:
    ips = node.get("TailscaleIPs") or [""]
    return Peer(
        name=node.get("HostName", ""),
        dns=(node.get("DNSName") or "").rstrip("."),
        ip=ips[0],
        os=node.get("OS", ""),
        online=bool(node.get("Online", is_self)),
        is_self=is_self,
    )


def tailscale_peers(include_self: bool = False) -> list[Peer]:
    status = tailscale_status()
    if not status:
        return []
    peers = [_peer(p, False) for p in (status.get("Peer") or {}).values()]
    if include_self and status.get("Self"):
        peers.insert(0, _peer(status["Self"], True))
    return peers


def tailscale_self() -> Peer | None:
    status = tailscale_status()
    if not status or not status.get("Self"):
        return None
    return _peer(status["Self"], True)


def tailscale_ip() -> str | None:
    me = tailscale_self()
    return me.ip if me and me.ip else None


def discover_hubs(port: int = 7700, timeout: float = 2.0) -> list[dict]:
    """Probe Tailscale peers for an Orbital hub on `port`. Returns [{name, dns, ip, url, hub_id}]."""
    import concurrent.futures
    import urllib.request

    peers = tailscale_peers(include_self=True)
    if not peers:
        return []

    def probe(peer: Peer) -> dict | None:
        for addr in (peer.dns, peer.ip):
            if not addr:
                continue
            url = f"http://{addr}:{port}"
            try:
                req = urllib.request.Request(f"{url}/v1/health", method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read())
                    if isinstance(data, dict) and data.get("service") in ("orbital-hub", "orbital-agents"):
                        return {"name": peer.name, "dns": peer.dns, "ip": peer.ip,
                                "url": url, "hub_id": data.get("hub_id", ""), "is_self": peer.is_self}
            except Exception:
                continue
        return None

    found: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(peers), 10)) as pool:
        for result in pool.map(probe, peers):
            if result:
                found.append(result)
    found.sort(key=lambda h: (not h.get("is_self", False), h["name"].lower()))
    return found


def own_tailnet_peer(client_ip: str | None) -> Peer | None:
    """The tailnet device at client_ip when it belongs to the same Tailscale user as this machine
    (devices shared in from other accounts don't count), else None."""
    if not client_ip:
        return None
    status = tailscale_status()
    if not status or not status.get("Self"):
        return None
    owner = status["Self"].get("UserID")
    want = client_ip.split("%")[0]
    for node in (status.get("Peer") or {}).values():
        if want in (node.get("TailscaleIPs") or []):
            if owner is not None and node.get("UserID") == owner:
                return _peer(node, False)
            return None
    return None
