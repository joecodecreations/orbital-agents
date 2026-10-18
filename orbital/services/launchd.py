"""macOS: LaunchAgents in ~/Library/LaunchAgents/io.orbitalagents.<name>.plist, managed with
`launchctl bootstrap / bootout / kickstart` in the user's gui/<uid> domain."""

from __future__ import annotations

import os
import plistlib
import re
import time
from pathlib import Path

from orbital.services.ops import Ops
from orbital.services.spec import LABEL_PREFIX, ServiceSpec


def launchctl() -> str:
    return os.environ.get("ORBITAL_LAUNCHCTL", "launchctl")


def plist_dict(spec: ServiceSpec) -> dict:
    d: dict = {
        "Label": spec.label,
        "ProgramArguments": list(spec.program),
        "EnvironmentVariables": dict(spec.env),
        "WorkingDirectory": str(spec.working_dir),
        "StandardOutPath": str(spec.log_path),
        "StandardErrorPath": str(spec.log_path),
        "RunAtLoad": True,
    }
    if spec.kind == "interval":
        d["StartInterval"] = int(spec.interval)
    elif spec.kind == "app":
        d["KeepAlive"] = {"SuccessfulExit": False}
        d["LimitLoadToSessionType"] = "Aqua"
        d["ProcessType"] = "Interactive"
        d["ThrottleInterval"] = 10
    else:
        d["KeepAlive"] = True
        d["ThrottleInterval"] = 10
    return d


def render(spec: ServiceSpec) -> bytes:
    return plistlib.dumps(plist_dict(spec), sort_keys=True)


def _without_path(data: dict) -> dict:
    out = dict(data)
    env = dict(out.get("EnvironmentVariables") or {})
    env.pop("PATH", None)
    out["EnvironmentVariables"] = env
    return out


def same_except_path(a: bytes, b: bytes) -> bool:
    """Plists equal apart from PATH (which depends on the shell that rendered them)."""
    try:
        return _without_path(plistlib.loads(a)) == _without_path(plistlib.loads(b))
    except Exception:
        return False


def parse_print(text: str) -> dict:
    """Pick state, pid and last exit code out of `launchctl print` (top-level keys only)."""
    out: dict = {"state": "", "pid": None, "last_exit": None}
    for line in text.splitlines():
        if not line.startswith("\t") or line.startswith("\t\t"):
            continue
        m = re.match(r"^\tstate = (.+)$", line)
        if m and not out["state"]:
            out["state"] = m.group(1).strip()
        m = re.match(r"^\tpid = (\d+)$", line)
        if m and out["pid"] is None:
            out["pid"] = int(m.group(1))
        m = re.match(r"^\tlast exit code = (.+)$", line)
        if m and out["last_exit"] is None:
            raw = m.group(1).strip()
            num = re.match(r"^-?\d+", raw)
            out["last_exit"] = int(num.group(0)) if num else raw
    return out


class LaunchdBackend:
    kind = "launchd"

    def __init__(self, ops: Ops, home: Path | None = None, uid: int | None = None):
        self.ops = ops
        self.home = Path(home) if home else Path.home()
        self.uid = os.getuid() if uid is None else uid
        self.dir = self.home / "Library" / "LaunchAgents"

    # -- names and files
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def target(self, name: str) -> str:
        return f"{self.domain()}/{LABEL_PREFIX}{name}"

    def file(self, name: str) -> Path:
        return self.dir / f"{LABEL_PREFIX}{name}.plist"

    def files(self, name: str) -> list[Path]:
        return [self.file(name)]

    def render(self, spec: ServiceSpec) -> dict[Path, bytes]:
        return {self.file(spec.name): render(spec)}

    def installed_names(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.glob(f"{LABEL_PREFIX}*.plist")):
            out.append(p.name[len(LABEL_PREFIX):-len(".plist")])
        return out

    def is_current(self, spec: ServiceSpec) -> bool:
        f = self.file(spec.name)
        if not f.is_file():
            return False
        return same_except_path(f.read_bytes(), render(spec))

    # -- launchctl
    def _lc(self, *args: str, mutate: bool = True):
        return self.ops.run([launchctl(), *args], mutate=mutate, timeout=30)

    def loaded(self, name: str) -> bool:
        return self._lc("print", self.target(name), mutate=False).ok

    def _bootstrap(self, name: str) -> tuple[bool, str]:
        self._lc("enable", self.target(name))
        last = ""
        for attempt in range(4):
            r = self._lc("bootstrap", self.domain(), str(self.file(name)))
            if r.ok:
                return True, "loaded"
            last = (r.stderr or r.stdout).strip()
            # 5 / 37 = already loaded or still unloading after a bootout: wait and retry.
            if self.ops.dry_run:
                break
            time.sleep(0.5 * (attempt + 1))
            if self.loaded(name):
                return True, "loaded"
        return False, last or "launchctl bootstrap failed"

    def _bootout(self, name: str) -> None:
        self._lc("bootout", self.target(name))
        if self.ops.dry_run:
            return
        for _ in range(10):
            if not self.loaded(name):
                return
            time.sleep(0.3)

    def install(self, spec: ServiceSpec, restart: bool = False) -> tuple[bool, str]:
        self.ops.mkdir(spec.log_path.parent)
        changed = False
        for path, data in self.render(spec).items():
            changed |= self.ops.write(path, data)
        is_loaded = self.loaded(spec.name)
        if is_loaded and not (changed or restart):
            return True, "unchanged, loaded"
        if is_loaded:
            self._bootout(spec.name)
        ok, msg = self._bootstrap(spec.name)
        if not ok:
            return False, msg
        if changed:
            return True, "updated and reloaded" if is_loaded else "installed and loaded"
        return True, "reloaded" if is_loaded else "loaded"

    def uninstall(self, name: str) -> tuple[bool, str]:
        if self.loaded(name):
            self._bootout(name)
        removed = self.ops.remove(self.file(name))
        return True, "removed" if removed else "not installed"

    def status(self, name: str) -> dict:
        info = {"installed": self.file(name).is_file(), "loaded": False, "running": False,
                "pid": None, "last_exit": None, "state": "not installed"}
        r = self._lc("print", self.target(name), mutate=False)
        if r.ok:
            parsed = parse_print(r.stdout)
            info.update(parsed)
            info["loaded"] = True
            info["running"] = parsed.get("state") == "running" or bool(parsed.get("pid"))
            info["state"] = parsed.get("state") or "loaded"
        elif info["installed"]:
            info["state"] = "not loaded"
        return info

    def restart(self, name: str) -> tuple[bool, str]:
        if not self.file(name).is_file():
            return False, "not installed"
        if not self.loaded(name):
            return self._bootstrap(name)
        r = self._lc("kickstart", "-k", self.target(name))
        return (r.ok, "restarted" if r.ok else (r.stderr or r.stdout).strip())

    def start(self, name: str) -> tuple[bool, str]:
        if not self.file(name).is_file():
            return False, "not installed"
        if not self.loaded(name):
            return self._bootstrap(name)
        r = self._lc("kickstart", self.target(name))
        return (r.ok, "started" if r.ok else (r.stderr or r.stdout).strip())

    def stop(self, name: str) -> tuple[bool, str]:
        if not self.loaded(name):
            return True, "not running"
        self._bootout(name)
        return True, "stopped (starts again at next login; `orbital services start` to start now)"

    def finish(self) -> list[str]:
        return []
