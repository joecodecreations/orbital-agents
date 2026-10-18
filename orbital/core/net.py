"""Network helpers shared by every server: who may connect, and what Tailscale can see."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
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
    for cand in ("tailscale", "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
                 "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        found = shutil.which(cand) if "/" not in cand else (cand if shutil.which(cand) else None)
        if found:
            return found
    return None


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
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=timeout)
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
