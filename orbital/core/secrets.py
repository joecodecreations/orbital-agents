"""~/.orbital-agents/secrets.env: passwords, tokens and API keys. Mode 0600, never committed, never logged.

Keys used across the system (the setup wizard creates the ones marked *):
  ORBITAL_HUB_USERNAME *        app sign-in
  ORBITAL_HUB_PASSWORD *        app sign-in
  ORBITAL_AUTOMATION_TOKEN *    feeds/scripts pushing data to the hub (Bearer)
  ORBITAL_JOIN_TOKEN *          hub only: satellites present it once to join
  ORBITAL_HUB_TOKEN             satellite only: issued by the hub at join, used for satellite → hub calls
  ORBITAL_SATELLITE_TOKEN *     this machine's satellite service; the hub uses it to start agents here
  ORBITAL_BROWSER_TOKEN *       browser bridge token for calls from other machines
  XAI_API_KEY                   optional: Desk voice replies
  FISH_API_KEY                  optional: spoken replies
  APNS_KEY_ID, APNS_TEAM_ID     optional: direct Apple push (plus apns.p8 next to this file)
"""

from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

from orbital.core.paths import Paths


def _parse(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip("'").strip('"')
    return out


class Secrets:
    def __init__(self, paths: Paths | None = None):
        self.paths = paths or Paths.resolve()
        self.file = self.paths.secrets_file

    def all(self) -> dict[str, str]:
        return _parse(self.file)

    def get(self, key: str, default: str = "") -> str:
        """Environment wins (handy for CI and tests), then the file."""
        return os.environ.get(key) or self.all().get(key, default)

    def set(self, key: str, value: str) -> None:
        data = self.all()
        data[key] = value
        self.paths.home.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        body = "# Orbital Agents secrets. Mode 0600. Do not commit or share.\n"
        body += "".join(f"{k}={v}\n" for k, v in sorted(data.items()))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, self.file)
        os.chmod(self.file, 0o600)

    def ensure_token(self, key: str, nbytes: int = 24) -> str:
        current = self.all().get(key)
        if current:
            return current
        token = new_token(nbytes)
        self.set(key, token)
        return token


def new_token(nbytes: int = 24) -> str:
    return _secrets.token_urlsafe(nbytes)


def mask(value: str) -> str:
    """For logs and status output: never print a whole secret."""
    if not value:
        return "(not set)"
    return value[:3] + "…" + f"({len(value)} chars)"
