"""Where everything lives on disk.

One folder per install, `~/.orbital-agents` unless ORBITAL_HOME says otherwise (tests point it at a
temp dir). The repo itself holds only code; config, secrets, data and logs live here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def resolve(cls, home: str | os.PathLike | None = None) -> "Paths":
        raw = home or os.environ.get("ORBITAL_HOME") or "~/.orbital-agents"
        return cls(Path(raw).expanduser().resolve())

    # Settings
    @property
    def config_file(self) -> Path: return self.home / "config.toml"
    @property
    def secrets_file(self) -> Path: return self.home / "secrets.env"
    @property
    def resolved_file(self) -> Path: return self.home / "config.resolved.json"
    @property
    def jobs_file(self) -> Path: return self.home / "jobs.json"

    # Data, one folder per component
    @property
    def data(self) -> Path: return self.home / "data"
    @property
    def hub_data(self) -> Path: return self.data / "hub"
    @property
    def tracker_data(self) -> Path: return self.data / "tracker"
    @property
    def scheduler_data(self) -> Path: return self.data / "scheduler"
    @property
    def satellite_data(self) -> Path: return self.data / "satellite"
    @property
    def browser_data(self) -> Path: return self.data / "browser"
    @property
    def feeds_data(self) -> Path: return self.data / "feeds"

    # Logs and runtime state
    @property
    def logs(self) -> Path: return self.home / "logs"
    @property
    def run(self) -> Path: return self.home / "run"

    def log_file(self, component: str) -> Path:
        return self.logs / f"{component}.log"

    def ensure(self) -> "Paths":
        for d in (self.home, self.data, self.hub_data, self.tracker_data, self.scheduler_data,
                  self.satellite_data, self.browser_data, self.feeds_data, self.logs, self.run):
            d.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        return self


def paths(home: str | os.PathLike | None = None) -> Paths:
    return Paths.resolve(home)
