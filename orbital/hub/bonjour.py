"""Bonjour/mDNS service advertisement so iOS apps and satellites discover the hub on the network.

Registers `_orbital-hub._tcp` with the hub's port. On macOS uses the system's dns-sd; on Linux
falls back to the `zeroconf` package if installed. The hub calls `start()` at boot and `stop()`
at shutdown; both are safe to call when unavailable (they log a note and return).
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Any

from orbital.core import get_logger

SERVICE_TYPE = "_orbital-hub._tcp"
SERVICE_NAME = "Orbital Hub"


def tailscale_url(port: int) -> str:
    """The hub's address over Tailscale, which hub.allow_networks accepts (a LAN address it refuses)."""
    from orbital.core.net import tailscale_self

    me = tailscale_self()
    host = (me.dns if me and me.dns else "") or (me.ip if me and me.ip else "")
    return f"http://{host}:{port}" if host else ""


class BonjourAdvertiser:
    def __init__(self, port: int, hub_id: str = "", url: str = "") -> None:
        self.port = port
        self.hub_id = hub_id
        self.url = url
        self._proc: subprocess.Popen | None = None
        self._zc: Any = None
        self._info: Any = None

    def start(self) -> None:
        if sys.platform == "darwin":
            self._start_macos()
        else:
            self._start_zeroconf()

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
        if self._zc is not None:
            try:
                self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception:
                pass
            self._zc = None
            self._info = None

    def _txt(self) -> list[str]:
        return [f"{key}={value}" for key, value in (("hub_id", self.hub_id), ("url", self.url)) if value]

    def _start_macos(self) -> None:
        try:
            cmd = ["dns-sd", "-R", SERVICE_NAME, SERVICE_TYPE, "local", str(self.port), *self._txt()]
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            get_logger("hub").info("Bonjour: advertising on port %d", self.port)
        except (OSError, FileNotFoundError):
            get_logger("hub").info("Bonjour: dns-sd not available, skipping mDNS advertisement")

    def _start_zeroconf(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
            import socket
            self._zc = Zeroconf()
            self._info = ServiceInfo(
                f"{SERVICE_TYPE}.local.",
                f"{SERVICE_NAME}.{SERVICE_TYPE}.local.",
                addresses=[socket.inet_aton(socket.gethostbyname(socket.gethostname()))],
                port=self.port,
                properties=dict(entry.split("=", 1) for entry in self._txt()),
            )
            self._zc.register_service(self._info)
            get_logger("hub").info("Bonjour: advertising via zeroconf on port %d", self.port)
        except ImportError:
            get_logger("hub").info("Bonjour: zeroconf not installed and dns-sd not available, skipping mDNS")
        except Exception as exc:
            get_logger("hub").info("Bonjour: could not start: %s", exc)
