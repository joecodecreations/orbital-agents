"""Linux: systemd user units in ~/.config/systemd/user/orbital-<name>.service (+ orbital-watchdog.timer).

User units stop when you log out unless lingering is on: `loginctl enable-linger $USER`
(the installer prints this hint; it never changes it for you).
"""

from __future__ import annotations

import getpass
import os
import re
import shlex
from pathlib import Path

from orbital.services.ops import Ops
from orbital.services.spec import UNIT_PREFIX, ServiceSpec


def systemctl() -> str:
    return os.environ.get("ORBITAL_SYSTEMCTL", "systemctl")


def _quote_env(key: str, value: str) -> str:
    # systemd Environment= takes "KEY=value" with C-style quoting
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{key}={escaped}"'


def _exec_line(argv: list[str]) -> str:
    # systemd splits ExecStart like a shell, but `%` must be doubled
    return " ".join(shlex.quote(a).replace("%", "%%") for a in argv)


def render_service(spec: ServiceSpec) -> str:
    lines = [
        "# Managed by `orbital services` (Orbital Agents). Reinstall with `orbital services install`.",
        "[Unit]",
        f"Description={spec.description or spec.name}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
    ]
    if spec.kind == "interval":
        lines.append("Type=oneshot")
    else:
        lines.append("Type=simple")
    lines.append(f"ExecStart={_exec_line(spec.program)}")
    lines.append(f"WorkingDirectory={spec.working_dir}")
    for key, value in spec.env.items():
        lines.append(_quote_env(key, value))
    if spec.kind != "interval":
        lines.append("Restart=always")
        lines.append("RestartSec=10")
    lines.append(f"StandardOutput=append:{spec.log_path}")
    lines.append(f"StandardError=append:{spec.log_path}")
    if spec.kind != "interval":
        lines += ["", "[Install]", "WantedBy=default.target"]
    return "\n".join(lines) + "\n"


def render_timer(spec: ServiceSpec) -> str:
    return "\n".join([
        "# Managed by `orbital services` (Orbital Agents).",
        "[Unit]",
        f"Description={spec.description or spec.name} (timer)",
        "",
        "[Timer]",
        "OnBootSec=60s",
        f"OnUnitActiveSec={int(spec.interval)}s",
        f"Unit={spec.unit}.service",
        "",
        "[Install]",
        "WantedBy=timers.target",
    ]) + "\n"


def parse_show(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class SystemdBackend:
    kind = "systemd"

    def __init__(self, ops: Ops, home: Path | None = None):
        self.ops = ops
        self.home = Path(home) if home else Path.home()
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg and home is None else self.home / ".config"
        self.dir = base / "systemd" / "user"
        self._reload_needed = False

    def service_file(self, name: str) -> Path:
        return self.dir / f"{UNIT_PREFIX}{name}.service"

    def timer_file(self, name: str) -> Path:
        return self.dir / f"{UNIT_PREFIX}{name}.timer"

    def file(self, name: str) -> Path:
        return self.service_file(name)

    def files(self, name: str) -> list[Path]:
        return [self.service_file(name), self.timer_file(name)]

    def render(self, spec: ServiceSpec) -> dict[Path, str]:
        out = {self.service_file(spec.name): render_service(spec)}
        if spec.kind == "interval":
            out[self.timer_file(spec.name)] = render_timer(spec)
        return out

    def installed_names(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return [p.name[len(UNIT_PREFIX):-len(".service")] for p in sorted(self.dir.glob(f"{UNIT_PREFIX}*.service"))]

    def is_current(self, spec: ServiceSpec) -> bool:
        for path, text in self.render(spec).items():
            if not path.is_file():
                return False
            strip = lambda t: re.sub(r'^Environment="PATH=.*$', "", t, flags=re.M)  # noqa: E731
            if strip(path.read_text(encoding="utf-8")) != strip(text):
                return False
        return True

    def _sc(self, *args: str, mutate: bool = True):
        return self.ops.run([systemctl(), "--user", *args], mutate=mutate, timeout=60)

    def _primary(self, name: str) -> str:
        """The unit to enable/start: the timer for interval services."""
        if self.timer_file(name).is_file():
            return f"{UNIT_PREFIX}{name}.timer"
        return f"{UNIT_PREFIX}{name}.service"

    def install(self, spec: ServiceSpec, restart: bool = False) -> tuple[bool, str]:
        self.ops.mkdir(spec.log_path.parent)
        changed = False
        for path, text in self.render(spec).items():
            changed |= self.ops.write(path, text)
        if changed:
            r = self._sc("daemon-reload")
            if not r.ok:
                return False, (r.stderr or r.stdout).strip() or "systemctl daemon-reload failed"
        unit = f"{spec.unit}.timer" if spec.kind == "interval" else f"{spec.unit}.service"
        r = self._sc("enable", unit)
        if not r.ok:
            return False, (r.stderr or r.stdout).strip() or f"systemctl enable {unit} failed"
        verb = "restart" if (changed or restart) else "start"
        r = self._sc(verb, unit)
        if not r.ok:
            return False, (r.stderr or r.stdout).strip() or f"systemctl {verb} {unit} failed"
        return True, "installed and started" if changed else ("restarted" if restart else "unchanged, started")

    def uninstall(self, name: str) -> tuple[bool, str]:
        units = [f"{UNIT_PREFIX}{name}.timer"] if self.timer_file(name).is_file() else []
        units.append(f"{UNIT_PREFIX}{name}.service")
        if self.service_file(name).is_file():
            self._sc("disable", "--now", *units)
        removed = False
        for f in self.files(name):
            removed |= self.ops.remove(f)
        if removed:
            self._sc("daemon-reload")
        return True, "removed" if removed else "not installed"

    def status(self, name: str) -> dict:
        info = {"installed": self.service_file(name).is_file(), "loaded": False, "running": False,
                "pid": None, "last_exit": None, "state": "not installed"}
        if not info["installed"]:
            return info
        r = self._sc("show", f"{UNIT_PREFIX}{name}.service", "-p", "LoadState", "-p", "ActiveState",
                     "-p", "SubState", "-p", "MainPID", "-p", "ExecMainStatus", mutate=False)
        if not r.ok:
            info["state"] = "unknown (systemctl failed)"
            return info
        s = parse_show(r.stdout)
        info["loaded"] = s.get("LoadState") == "loaded"
        pid = int(s.get("MainPID") or 0)
        info["pid"] = pid or None
        try:
            info["last_exit"] = int(s.get("ExecMainStatus", ""))
        except ValueError:
            info["last_exit"] = None
        active = s.get("ActiveState", "")
        info["running"] = active == "active" and s.get("SubState") in ("running", "exited", "start")
        info["state"] = f"{active}/{s.get('SubState', '')}".strip("/")
        if self.timer_file(name).is_file():
            t = self._sc("show", f"{UNIT_PREFIX}{name}.timer", "-p", "ActiveState", mutate=False)
            timer_active = parse_show(t.stdout).get("ActiveState") == "active" if t.ok else False
            info["running"] = timer_active
            info["state"] = f"timer {'active' if timer_active else 'inactive'}, last run {info['state']}"
        return info

    def restart(self, name: str) -> tuple[bool, str]:
        if not self.service_file(name).is_file():
            return False, "not installed"
        r = self._sc("restart", f"{UNIT_PREFIX}{name}.service")
        return (r.ok, "restarted" if r.ok else (r.stderr or r.stdout).strip())

    def start(self, name: str) -> tuple[bool, str]:
        if not self.service_file(name).is_file():
            return False, "not installed"
        r = self._sc("start", self._primary(name))
        return (r.ok, "started" if r.ok else (r.stderr or r.stdout).strip())

    def stop(self, name: str) -> tuple[bool, str]:
        if not self.service_file(name).is_file():
            return True, "not installed"
        units = [f"{UNIT_PREFIX}{name}.timer"] if self.timer_file(name).is_file() else []
        r = self._sc("stop", *units, f"{UNIT_PREFIX}{name}.service")
        return (r.ok, "stopped" if r.ok else (r.stderr or r.stdout).strip())

    def linger_enabled(self) -> bool | None:
        user = getpass.getuser()
        r = self.ops.run(["loginctl", "show-user", user, "-p", "Linger"], mutate=False, timeout=10)
        if not r.ok:
            return None
        return parse_show(r.stdout).get("Linger") == "yes"

    def finish(self) -> list[str]:
        notes = []
        if self.linger_enabled() is False:
            notes.append("User services stop when you log out. Keep them running with: "
                         "loginctl enable-linger $USER")
        return notes
