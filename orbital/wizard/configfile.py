"""Write ~/.orbital-agents/config.toml from the wizard's answers: only values that differ from the
defaults, plus the few that must always be there, so the file stays short and readable."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

from orbital.core.config import DEFAULTS, to_toml

HEADER = (
    "# Orbital Agents settings (written by `orbital setup`; edit freely).\n"
    "# Only values that differ from the defaults are listed; every other setting uses the default\n"
    "# documented in config/orbital.example.toml. Secrets live in secrets.env, never here.\n"
    "# After editing: `orbital config --apply` and `orbital services restart`.\n\n"
)

ALWAYS = ("system.role", "system.name")
SATELLITE_ALWAYS = ("hub.url",)


def read_raw(path: Path) -> dict[str, Any]:
    """The file's own values (not merged with defaults). Raises tomllib.TOMLDecodeError if broken."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def diff(data: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key not in defaults:
            out[key] = copy.deepcopy(value)
            continue
        base = defaults[key]
        if isinstance(value, dict) and isinstance(base, dict):
            sub = diff(value, base)
            if sub:
                out[key] = sub
        elif value != base:
            out[key] = copy.deepcopy(value)
    return out


def get_dotted(data: dict, dotted: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_dotted(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def minimal(merged: dict[str, Any]) -> dict[str, Any]:
    """Non-default values of a fully merged config, plus the required keys."""
    out = diff(merged, DEFAULTS)
    required = list(ALWAYS)
    if get_dotted(merged, "system.role") == "satellite":
        required += SATELLITE_ALWAYS
    for key in required:
        value = get_dotted(merged, key)
        if value is not None and get_dotted(out, key) is None:
            set_dotted(out, key, value)
    # Put [system] first so the file reads top-down.
    ordered: dict[str, Any] = {}
    for key in ("system", "hub"):
        if key in out:
            ordered[key] = out.pop(key)
    ordered.update(out)
    if "system" in ordered:
        sysd = ordered["system"]
        ordered["system"] = {k: sysd[k] for k in ("name", "role", "machine_name") if k in sysd} | {
            k: v for k, v in sysd.items() if k not in ("name", "role", "machine_name")}
    return ordered


def render(merged: dict[str, Any]) -> str:
    return HEADER + to_toml(minimal(merged))
