"""Who may call the hub. Never log a secret value.

  App (iPhone / Mac)   POST /v1/login with ORBITAL_HUB_USERNAME / ORBITAL_HUB_PASSWORD → a session token
  Feeds, scripts, CLI  Authorization: Bearer $ORBITAL_AUTOMATION_TOKEN
  Satellites           Authorization: Bearer <hub_token issued at join> (satellites.py checks those)

Secrets come from ~/.orbital-agents/secrets.env (the environment wins, for tests and CI) and are
re-read when the file changes, so a new password needs no restart. Session tokens are stored
hashed, and every comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Request

from orbital.core.secrets import Secrets

SESSION_IDLE_DAYS = 90


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same(a: str | None, b: str | None) -> bool:
    """Constant-time equality. An empty value never matches."""
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def bearer(request) -> str | None:
    """The token on an HTTP request or a websocket: Authorization: Bearer, or ?token= on a socket."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip() or None
    alt = request.headers.get("x-orbital-token")
    if alt:
        return alt.strip()
    if request.scope.get("type") == "websocket":
        return request.query_params.get("token")
    return None


class SecretCache:
    """secrets.env, re-read only when it changes."""

    def __init__(self, store: Secrets) -> None:
        self.store = store
        self._mtime = -1.0
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str:
        if os.environ.get(key):
            return os.environ[key]
        try:
            mtime = self.store.file.stat().st_mtime
        except OSError:
            mtime = 0.0
        with self._lock:
            if mtime != self._mtime:
                self._data = self.store.all() if mtime else {}
                self._mtime = mtime
            return self._data.get(key, "")


class Sessions:
    """App sign-ins. The file holds sha256(token) → {username, created_at, last_used}."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)

    def issue(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock:
            data = self._read()
            data[digest(token)] = {"username": username, "created_at": now, "last_used": now}
            self._write(data)
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        key = digest(token)
        with self._lock:
            data = self._read()
            row = data.get(key)
            if not isinstance(row, dict):
                return False
            # Touch at most hourly so a busy app does not rewrite the file on every request.
            if time.time() - float(row.get("last_used") or 0) > 3600:
                row["last_used"] = int(time.time())
                self._write(data)
        return True

    def drop(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            data = self._read()
            if data.pop(digest(token), None) is not None:
                self._write(data)

    def trim(self, idle_days: int = SESSION_IDLE_DAYS) -> int:
        cutoff = time.time() - idle_days * 86400
        with self._lock:
            data = self._read()
            kept = {k: v for k, v in data.items() if isinstance(v, dict) and float(v.get("last_used") or 0) >= cutoff}
            if len(kept) != len(data):
                self._write(kept)
            return len(data) - len(kept)


class HubAuth:
    def __init__(self, secrets_store: Secrets, sessions: Sessions) -> None:
        self.secrets = SecretCache(secrets_store)
        self.sessions = sessions

    @property
    def automation_token(self) -> str:
        return self.secrets.get("ORBITAL_AUTOMATION_TOKEN")

    def is_automation(self, token: str | None) -> bool:
        return same(token, self.automation_token)

    def is_user(self, token: str | None) -> bool:
        """The app (a session) or a feed / the CLI (the automation token)."""
        if not token:
            return False
        return self.is_automation(token) or self.sessions.valid(token)

    def authorized(self, request: Request) -> bool:
        return self.is_user(bearer(request))

    def check_login(self, username: str, password: str) -> bool:
        want_user = self.secrets.get("ORBITAL_HUB_USERNAME")
        want_pass = self.secrets.get("ORBITAL_HUB_PASSWORD")
        # Both comparisons always run, so timing says nothing about which one failed.
        ok_user = same(username, want_user)
        ok_pass = same(password, want_pass)
        return ok_user and ok_pass

    def join_token_ok(self, token: str | None) -> bool:
        return same(token, self.secrets.get("ORBITAL_JOIN_TOKEN"))

    def get(self, key: str) -> str:
        return self.secrets.get(key)
