"""Finding the agent CLIs (grok, claude) from a service started with a bare PATH."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

HOME = Path.home()
EXTRA_PATH = [str(HOME / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def tool_env() -> dict[str, str]:
    """launchd and systemd start the hub with a bare PATH; the CLIs live in ~/.local/bin and Homebrew."""
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for extra in EXTRA_PATH:
        if extra not in parts:
            parts.append(extra)
    env["PATH"] = os.pathsep.join(parts)
    return env


def which(name: str) -> str | None:
    return shutil.which(name, path=tool_env()["PATH"])
