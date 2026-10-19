"""Blocking HTTP for the CLI and the satellite → hub relay (HubRelay.swift).

Satellites send every orbital-track command to the hub as well: POST <hub_url>/v1/tracker/command
with Authorization: Bearer <ORBITAL_HUB_TOKEN> and the same JSON. Nothing here ever blocks an
agent: commands that can't be delivered wait in hub-outbox/ (oldest first) and go out with the
next command once the hub answers. After a failure the hub is skipped for 60 s.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import VERSION
from .config import OrbitalConfig, TrackerPaths, secret
from .model import Settings, dumps, inbox_pending, inbox_write

# Loopback and tailnet traffic never goes through a proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, url: str, body: bytes | None = None, timeout: float = 2.0,
         bearer: str | None = None) -> tuple[int, bytes] | None:
    """(status, body), or None when nothing answered."""
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    req.add_header("User-Agent", f"orbital-track/{VERSION}")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        except Exception:  # noqa: BLE001
            return exc.code, b""
    except (OSError, ValueError):
        return None


def prune_files(folder: Path, older_than: float, recursive: bool = False) -> list[Path]:
    """Deletes plain files (optionally one level of sub-folders, removed when empty) modified before
    `older_than` (a Unix time)."""
    removed: list[Path] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return removed
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if not recursive:
                    continue
                removed += prune_files(Path(entry.path), older_than)
                if not os.listdir(entry.path):
                    os.rmdir(entry.path)
            elif entry.stat(follow_symlinks=False).st_mtime < older_than:
                os.remove(entry.path)
                removed.append(Path(entry.path))
        except OSError:
            continue
    return removed


class HubRelay:
    timeout = 2.5
    cooldown = 60.0
    max_queued = 500
    max_age = 7 * 86_400

    def __init__(self, hub_url: str, token: str | None, outbox: Path) -> None:
        self.hub_url = hub_url.strip().strip("/").strip()
        self.token = token
        self.outbox = outbox
        self.transport = http  # injected in tests

    @classmethod
    def from_config(cls, cfg: OrbitalConfig, paths: TrackerPaths, env: dict[str, str] | None = None) -> "HubRelay | None":
        if not cfg.relays_to_hub:
            return None
        return cls(cfg.hub_url, secret("ORBITAL_HUB_TOKEN", cfg.secrets_file, env), paths.hub_outbox_dir)

    @property
    def command_url(self) -> str:
        return self.hub_url + "/v1/tracker/command"

    @property
    def settings_url(self) -> str:
        return self.hub_url + "/v1/tracker/settings"

    @property
    def down_marker(self) -> Path:
        return self.outbox / ".hub-down"

    def in_cooldown(self, now: float | None = None) -> bool:
        try:
            modified = self.down_marker.stat().st_mtime
        except OSError:
            return False
        return (now or time.time()) - modified < self.cooldown

    def _mark_down(self) -> None:
        try:
            self.outbox.mkdir(parents=True, exist_ok=True)
            self.down_marker.write_bytes(b"")
            os.utime(self.down_marker)
        except OSError:
            pass

    def _mark_up(self) -> None:
        try:
            self.down_marker.unlink()
        except OSError:
            pass

    @property
    def queued(self) -> list[Path]:
        return inbox_pending(self.outbox)

    def relay(self, body: bytes) -> str:
        """Sends `body` after anything already queued: 'sent', 'queued', 'dropped' or 'skipped'."""
        self.trim()
        if self.in_cooldown() or self.token is None or not self.flush():
            return "queued" if self._enqueue(body) else "skipped"
        result = self._post(body)
        if result == "delivered":
            return "sent"
        if result == "rejected":
            return "dropped"
        return "queued" if self._enqueue(body) else "skipped"

    def flush(self, limit: int = 50) -> bool:
        """Sends queued commands oldest first; False when the hub stopped answering."""
        if self.token is None or self.in_cooldown():
            return not self.queued
        for file in self.queued[:limit]:
            try:
                data = file.read_bytes()
            except OSError:
                try:
                    file.unlink()
                except OSError:
                    pass
                continue
            if self._post(data) == "unreachable":
                return False
            try:
                file.unlink()  # delivered, or rejected (retrying would never succeed)
            except OSError:
                pass
        return not self.queued

    def _post(self, body: bytes) -> str:
        reply = self.transport("POST", self.command_url, body, self.timeout, self.token)
        if reply is None:
            self._mark_down()
            return "unreachable"
        self._mark_up()
        status = reply[0]
        if 200 <= status < 300:
            return "delivered"
        if status in (408, 429) or status >= 500:
            self._mark_down()
            return "unreachable"
        return "rejected"

    def _enqueue(self, body: bytes) -> bool:
        try:
            inbox_write(body, self.outbox)
            return True
        except OSError:
            return False

    def trim(self) -> None:
        prune_files(self.outbox, time.time() - self.max_age)
        files = self.queued
        for f in files[: max(0, len(files) - self.max_queued)]:
            try:
                f.unlink()
            except OSError:
                pass

    def fetch_settings(self) -> Settings | None:
        """The hub's evidence level (None when unreachable or not allowed)."""
        if not self.token or self.in_cooldown():
            return None
        reply = self.transport("GET", self.settings_url, None, min(self.timeout, 1.5), self.token)
        if reply is None:
            self._mark_down()
            return None
        if not 200 <= reply[0] < 300:
            return None
        return Settings.from_reply(reply[1])

    def push_settings(self, settings: Settings, by: str) -> bool:
        """Tells the hub about a level changed on this machine. Best effort."""
        if not self.token or self.in_cooldown():
            return False
        body = dumps({"evidence": settings.evidence, "by": by}).encode()
        reply = self.transport("POST", self.settings_url, body, min(self.timeout, 1.5), self.token)
        if reply is None:
            self._mark_down()
            return False
        return 200 <= reply[0] < 300
