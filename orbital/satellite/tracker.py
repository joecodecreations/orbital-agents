"""Report satellite jobs to the Agent Satellite Tracker.

Two ways, picked per machine:

1. **`orbital-track` CLI** (macOS with the tracker installed). It talks to the local tracker app,
   which on a satellite also relays to the hub itself:

       orbital-track start --agent <agent> --title <title> --details <markdown>   → prints the item id
       orbital-track done <id> --summary <markdown>

2. **Hub tracker relay**, when the CLI is not there (every Linux satellite, a Mac without the
   tracker app or with `tracker.enabled = false`), on a machine with `system.role = "satellite"`:

       POST <hub.url>/v1/tracker/command   Authorization: Bearer <ORBITAL_HUB_TOKEN>
       {"op": "start", "id": "<made here>", "agent", "machine", "title", "details", "sentAt"}
       {"op": "done",  "id": "<same>", "machine", "summary", "sentAt"}

   The same command JSON `orbital-track` sends (apps/tracker/Sources/OrbitalTrackerCore/Command.swift).
   Commands go out from one background thread in order, so a job never waits for the hub; while the
   hub is unreachable they wait in memory (at most 200) and are retried after a short cooldown.
   A command the hub rejects (bad token, bad id) is dropped. Problems are logged once, at info.

On the hub machine itself with no tracker app there is nothing to relay to, so reporting is skipped
silently. `ORBITAL_TRACK_BIN` overrides where the CLI is; set it to `off` to turn reporting off
entirely (the tests do).
"""

from __future__ import annotations

import collections
import os
import random
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from orbital.satellite.agents import child_env, resolve_executable
from orbital.satellite.util import NO_CONSOLE

ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # the tracker's own IDGen alphabet
RELAY_TIMEOUT = 4.0
RELAY_COOLDOWN = 30.0
RELAY_MAX_PENDING = 200


def new_item_id(length: int = 8) -> str:
    rng = random.SystemRandom()
    return "".join(rng.choice(ID_ALPHABET) for _ in range(length))


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HubTrackerRelay:
    """Sends tracker commands to the hub's `/v1/tracker/command`, in order, from a background thread."""

    def __init__(self, home, *, timeout: float = RELAY_TIMEOUT, cooldown: float = RELAY_COOLDOWN,
                 max_pending: int = RELAY_MAX_PENDING, log=None,
                 transport: Callable[[str, dict, str], int | None] | None = None):
        self.home = home
        self.timeout = timeout
        self.cooldown = cooldown
        self.log = log
        self.transport = transport or self._post
        self._pending: collections.deque[dict] = collections.deque(maxlen=max_pending)
        self._cond = threading.Condition()
        self._busy = False
        self._thread: threading.Thread | None = None
        self._said: set[str] = set()
        self.sent = 0
        self.dropped = 0

    # ---- where to send

    def target(self) -> tuple[str, str] | None:
        """(hub_url, hub_token) on a joined satellite; None anywhere else (the hub machine included)."""
        from orbital.core.config import load_config
        from orbital.core.secrets import Secrets

        cfg = load_config(self.home)
        if cfg.role != "satellite":
            return None
        url = cfg.hub_url()
        token = Secrets(cfg.paths).get("ORBITAL_HUB_TOKEN")
        if not url or not token:
            return None
        return url, token

    def available(self) -> bool:
        try:
            return self.target() is not None
        except Exception:  # noqa: BLE001 - a broken config.toml must not break a job
            return False

    # ---- sending

    def send(self, command: dict) -> None:
        with self._cond:
            if len(self._pending) == self._pending.maxlen:
                self.dropped += 1
                self._once("queue-full", "tracker relay queue is full; dropping the oldest command")
            self._pending.append(command)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker, name="tracker-relay", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until every queued command is out (or dropped). For tests and shutdown."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while (self._pending or self._busy) and time.monotonic() < deadline:
                self._cond.wait(timeout=0.05)
            return not self._pending and not self._busy

    def _once(self, key: str, msg: str, **fields) -> None:
        if not self.log:
            return
        if key in self._said:
            self.log.debug(msg, extra={"fields": fields})
        else:
            self._said.add(key)
            self.log.info(msg, extra={"fields": fields})

    def _post(self, url: str, command: dict, token: str) -> int | None:
        try:
            response = httpx.post(f"{url}/v1/tracker/command", json=command, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {token}", "User-Agent": "orbital-satellite"})
        except httpx.HTTPError:
            return None
        return response.status_code

    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self._pending:
                    self._cond.wait(timeout=60)
                    if not self._pending:
                        self._thread = None
                        return
                command = self._pending[0]
                self._busy = True
            try:
                delivered = self._deliver(command)
            except Exception as exc:  # noqa: BLE001 - never let the relay thread die
                self._once("crash", "tracker relay error", error=f"{type(exc).__name__}: {exc}")
                delivered = True  # drop it rather than loop on it
            with self._cond:
                if delivered and self._pending and self._pending[0] is command:
                    self._pending.popleft()
                self._busy = False
                self._cond.notify_all()
            if not delivered:
                time.sleep(self.cooldown)

    def _deliver(self, command: dict) -> bool:
        """True when the command is done with (sent or rejected); False to retry later."""
        target = self.target()
        if target is None:
            self.dropped += 1
            self._once("no-target", "tracker relay: not joined to a hub any more; dropping tracker commands")
            return True
        url, token = target
        status = self.transport(url, command, token)
        if status is None or status in (408, 429) or status >= 500:
            self._once("unreachable", "tracker relay: the hub's tracker is not answering; will retry",
                       status=status, hub=url)
            return False
        if 200 <= status < 300:
            self.sent += 1
            return True
        self.dropped += 1
        self._once(f"rejected-{status}", "tracker relay: the hub refused a tracker command", status=status)
        return True


class TrackerReporter:
    def __init__(self, bin_path: str | None = None, enabled: bool = True, timeout: float = 10.0, *,
                 cfg=None, relay: HubTrackerRelay | None = None, log=None):
        override = os.environ.get("ORBITAL_TRACK_BIN")
        if bin_path is None and override is not None:
            if override.strip().lower() in ("", "0", "off", "false", "none"):
                enabled = False
            else:
                bin_path = override
        self.enabled = enabled
        self.timeout = timeout
        self._bin = bin_path
        self.machine = cfg.machine_name if cfg is not None else None
        if relay is None and cfg is not None:
            relay = HubTrackerRelay(cfg.paths.home, log=log)
        self.relay = relay
        self._relayed: set[str] = set()
        self._lock = threading.Lock()

    @property
    def bin(self) -> str | None:
        if not self.enabled:
            return None
        if self._bin:
            return resolve_executable(self._bin)
        return resolve_executable("orbital-track")

    def _call(self, args: list[str]) -> str | None:
        exe = self.bin
        if not exe:
            return None
        try:
            proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=self.timeout,
                                  stdin=subprocess.DEVNULL, env=child_env(), creationflags=NO_CONSOLE)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _relay_ready(self) -> bool:
        return self.enabled and self.relay is not None and self.bin is None and self.relay.available()

    def _relay(self, command: dict) -> None:
        if self.machine:
            command["machine"] = self.machine
        command["sentAt"] = _stamp()
        self.relay.send(command)

    def _is_relayed(self, item_id: str, forget: bool = False) -> bool:
        with self._lock:
            known = item_id in self._relayed
            if known and forget:
                self._relayed.discard(item_id)
            return known

    def start(self, agent: str, title: str, details: str = "") -> str | None:
        if self._relay_ready():
            item_id = new_item_id()
            with self._lock:
                self._relayed.add(item_id)
            self._relay({"op": "start", "id": item_id, "agent": agent, "title": title[:120], "details": details[:2000]})
            return item_id
        out = self._call(["start", "--agent", agent, "--title", title[:120], "--details", details[:2000]])
        if not out:
            return None
        for line in reversed(out.strip().splitlines()):
            line = line.strip()
            if ID_RE.match(line):
                return line
        return None

    def update(self, item_id: str | None, note: str) -> bool:
        if not item_id:
            return False
        if self._is_relayed(item_id):
            self._relay({"op": "update", "id": item_id, "note": note[:2000]})
            return True
        return self._call(["update", item_id, "--note", note[:2000]]) is not None

    def done(self, item_id: str | None, summary: str) -> bool:
        if not item_id:
            return False
        if self._is_relayed(item_id, forget=True):
            self._relay({"op": "done", "id": item_id, "summary": summary[:4000]})
            return True
        return self._call(["done", item_id, "--summary", summary[:4000]]) is not None
