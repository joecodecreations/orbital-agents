"""Which agent CLIs this machine has, and how to start one.

Agents come from `[agents.<name>]` in config.toml: `command = ["claude", "-p", "{prompt}"]`.
`{prompt}` and `{cwd}` are filled in per argument; no shell is ever involved. When a command has no
`{prompt}`, the prompt goes to the agent on stdin instead.

An agent is "available" only when its executable resolves. Services started by launchd or systemd
get a short PATH, so besides PATH we also look in the usual install folders (Homebrew, /usr/local,
~/.local/bin, ~/.npm-global/bin, nvm's node folders, bun, cargo).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from orbital.core.config import Config

COMMON_BIN_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.cargo/bin",
    "~/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _version_key(path: str) -> tuple:
    # ~/.nvm/versions/node/v20.11.1/bin → (20, 11, 1)
    match = re.search(r"/v(\d+)\.(\d+)\.(\d+)/", path)
    return tuple(int(x) for x in match.groups()) if match else (0, 0, 0)


def nvm_bin_dirs() -> list[str]:
    pattern = os.path.expanduser("~/.nvm/versions/node/*/bin")
    return sorted(glob.glob(pattern), key=_version_key, reverse=True)


def search_dirs(env_path: str | None = None) -> list[str]:
    """PATH first, then the common install folders, without duplicates."""
    raw = env_path if env_path is not None else os.environ.get("PATH", "")
    out: list[str] = []
    for part in [p for p in raw.split(os.pathsep) if p] + [os.path.expanduser(d) for d in COMMON_BIN_DIRS] + nvm_bin_dirs():
        if part not in out:
            out.append(part)
    return out


def search_path(env_path: str | None = None) -> str:
    return os.pathsep.join(search_dirs(env_path))


def resolve_executable(name: str, env_path: str | None = None) -> str | None:
    """Full path of an executable, or None. Absolute and ~ paths are checked as given."""
    if not name:
        return None
    if name.startswith("~"):
        name = os.path.expanduser(name)
    if os.sep in name:
        return name if os.path.isfile(name) and os.access(name, os.X_OK) else None
    return shutil.which(name, path=search_path(env_path))


@dataclass
class Agent:
    name: str
    command: list[str]
    path: str | None          # resolved executable, None when not installed

    @property
    def available(self) -> bool:
        return self.path is not None

    def public(self) -> dict:
        return {"name": self.name, "available": self.available, "path": self.path,
                "executable": self.command[0] if self.command else ""}


def configured_agents(cfg: Config) -> dict[str, Agent]:
    out: dict[str, Agent] = {}
    for name, spec in (cfg.get("agents") or {}).items():
        if not isinstance(spec, dict) or not NAME_RE.match(str(name)):
            continue
        command = spec.get("command")
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            continue
        if spec.get("enabled") is False:
            continue
        out[name] = Agent(name=name, command=list(command), path=resolve_executable(command[0]))
    return out


def available_agents(cfg: Config) -> list[str]:
    return sorted(name for name, agent in configured_agents(cfg).items() if agent.available)


def build_argv(agent: Agent, prompt: str, cwd: str) -> tuple[list[str], bool]:
    """(argv, prompt_on_stdin). argv[0] is the resolved executable."""
    if not agent.path:
        raise ValueError(f"agent {agent.name} is not installed")
    uses_prompt = any("{prompt}" in part for part in agent.command)
    argv = [agent.path] + [part.replace("{prompt}", prompt).replace("{cwd}", cwd) for part in agent.command[1:]]
    return argv, not uses_prompt


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for an agent process: ours, with the wider PATH so node-based CLIs find node."""
    env = dict(os.environ)
    env["PATH"] = search_path(env.get("PATH", ""))
    env.setdefault("HOME", str(Path.home()))
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env
