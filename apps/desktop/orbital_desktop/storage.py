"""Where the desktop app keeps its settings and its session token.

settings.json (hub URL, username, hub_id) lives in the per-user config folder:
  Windows  %APPDATA%\\OrbitalAgents
  macOS    ~/Library/Application Support/OrbitalAgentsDesktop
  Linux    $XDG_CONFIG_HOME/orbital-agents-desktop  (~/.config/...)

The session token goes into the OS keyring (Windows Credential Manager, macOS Keychain,
Secret Service on Linux). When no keyring backend works it falls back to a token file with
mode 0600 in the same folder. Nothing else secret is ever stored.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from . import APP_ID

KEYRING_SERVICE = APP_ID
# A keyring call can wait on the user (macOS asks whether a rebuilt app may read the Keychain item;
# Secret Service may ask to unlock). Give them time to answer, but never hang the app forever.
KEYRING_TIMEOUT = 60.0


def _bounded(fn, *args):
    """Run a keyring call in a worker thread; raise TimeoutError when the user never answers."""
    box: dict = {}

    def work() -> None:
        try:
            box["value"] = fn(*args)
        except Exception as exc:  # surfaced to the caller below
            box["error"] = exc

    worker = threading.Thread(target=work, name="orbital-keyring", daemon=True)
    worker.start()
    worker.join(KEYRING_TIMEOUT)
    if worker.is_alive():
        raise TimeoutError("keyring did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def config_dir() -> Path:
    override = os.environ.get("ORBITAL_DESKTOP_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "OrbitalAgents"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "OrbitalAgentsDesktop"
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "orbital-agents-desktop"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings_path() -> Path:
    return config_dir() / "settings.json"


def _token_path() -> Path:
    return config_dir() / "session.token"


def load_settings() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    path = _settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _keyring():
    if os.environ.get("ORBITAL_DESKTOP_NO_KEYRING"):
        return None
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def save_token(hub: str, token: str) -> str:
    """Store the session token; returns where it went ("keyring" or "file")."""
    kr = _keyring()
    if kr is not None:
        try:
            _bounded(kr.set_password, KEYRING_SERVICE, hub, token)
            _token_path().unlink(missing_ok=True)
            return "keyring"
        except Exception:
            pass
    path = _token_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"hub": hub, "token": token}, fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return "file"


def load_token(hub: str) -> str | None:
    kr = _keyring()
    if kr is not None:
        try:
            token = _bounded(kr.get_password, KEYRING_SERVICE, hub)
            if token:
                return token
        except Exception:
            pass
    try:
        data = json.loads(_token_path().read_text(encoding="utf-8"))
        if data.get("hub") == hub and data.get("token"):
            return str(data["token"])
    except (OSError, ValueError, AttributeError):
        pass
    return None


def clear_token(hub: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            _bounded(kr.delete_password, KEYRING_SERVICE, hub)
        except Exception:
            pass
    _token_path().unlink(missing_ok=True)
