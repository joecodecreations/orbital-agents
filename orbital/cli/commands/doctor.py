"""`orbital doctor`: find what's wrong on this machine and say how to fix it; `--fix` does the safe fixes.

Checks: config.toml, config.resolved.json freshness, secrets (file mode, required tokens), Tailscale,
ports, every service (installed, up to date, running, healthy), hub reachable (satellite) / hub
healthy (hub), this satellite visible on the hub, satellites online (hub), browser extension
connected, log folder size, agent CLIs on the PATH the services use, agent skills.

Safe fixes (--fix): rewrite config.resolved.json, chmod secrets.env to 0600, (re)install service
files that are missing or outdated, restart services that are loaded but unhealthy.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable

from orbital.core.config import DEFAULTS, Config, load_config
from orbital.core.paths import Paths
from orbital.core.secrets import Secrets
from orbital.services.ops import Ops
from orbital.wizard.ui import UI

NAME = "doctor"
HELP = "Diagnose this machine and print fixes; --fix applies the safe ones"

ROLE_TOKENS = {
    "hub": ["ORBITAL_HUB_USERNAME", "ORBITAL_HUB_PASSWORD", "ORBITAL_AUTOMATION_TOKEN", "ORBITAL_JOIN_TOKEN"],
    "satellite": ["ORBITAL_HUB_TOKEN"],
}


def register(p):
    p.add_argument("--fix", action="store_true", help="apply the safe fixes, then check again")
    p.add_argument("--json", action="store_true", help="results as JSON")
    p.add_argument("--dry-run", action="store_true", help="with --fix: print the fixes instead of doing them")


@dataclass
class Finding:
    area: str
    level: str                  # ok | warn | fail | info
    message: str
    fix: str = ""               # what to do (shown to the person)
    fixer: Callable[[], str] | None = field(default=None, repr=False)   # safe automatic fix
    fixed: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("fixer", None)
        d["auto_fix"] = self.fixer is not None
        return d


def _get_json(url: str, *, token: str = "", method: str = "GET", body: dict | None = None,
              timeout: float = 3.0) -> tuple[int, dict | None, str]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (configured hub/loopback URLs)
            raw = r.read(65536).decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw), ""
            except ValueError:
                return r.status, None, ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, None, f"no answer ({getattr(exc, 'reason', exc)})"


class Doctor:
    def __init__(self, ops: Ops | None = None, platform: str | None = None):
        self.ops = ops or Ops()
        self.platform = platform
        self.findings: list[Finding] = []
        self.paths = Paths.resolve()
        self.cfg: Config | None = None

    def add(self, area: str, level: str, message: str, fix: str = "", fixer=None) -> Finding:
        f = Finding(area, level, message, fix, fixer)
        self.findings.append(f)
        return f

    # ------------------------------------------------------------------ checks
    def run(self) -> list[Finding]:
        self.findings = []
        if not self.check_config():
            return self.findings
        cfg = self.cfg
        self.check_resolved(cfg)
        self.check_secrets(cfg)
        self.check_tailscale(cfg)
        statuses = self.check_services(cfg)
        self.check_ports(cfg, statuses)
        self.check_hub(cfg)
        self.check_browser(cfg)
        self.check_logs(cfg)
        self.check_agent_path(cfg)
        self.check_skills(cfg)
        return self.findings

    def check_config(self) -> bool:
        f = self.paths.config_file
        if not f.is_file():
            self.add("config", "fail", f"no settings file at {f}", "run ./setup (or `orbital setup`)")
            return False
        try:
            with f.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            self.add("config", "fail", f"{f} is not valid TOML: {exc}", "fix the line it names, or re-run ./setup")
            return False
        cfg = load_config()
        self.cfg = cfg
        problems = []
        if cfg.role not in ("hub", "satellite"):
            problems.append(f"system.role is {cfg.role!r} (hub or satellite)")
        unknown = [k for k in raw if k not in DEFAULTS]
        ports = {}
        for key in ("hub.port", "tracker.port", "tracker.stt_port", "browser.bridge_port", "satellite.port"):
            v = cfg.get(key)
            if not isinstance(v, int) or not 1 <= v <= 65535:
                problems.append(f"{key} = {v!r} is not a port number")
            else:
                ports.setdefault(v, []).append(key)
        for port, keys in ports.items():
            if len(keys) > 1:
                problems.append(f"{' and '.join(keys)} share port {port}")
        if cfg.role == "satellite" and not cfg.get("hub.url"):
            problems.append("satellite without hub.url")
        for key, allowed in (("browser.bind", ("loopback", "tailscale")), ("satellite.bind", ("loopback", "tailscale")),
                             ("logging.level", ("DEBUG", "INFO", "WARNING", "ERROR"))):
            if str(cfg.get(key)).upper() not in [a.upper() for a in allowed]:
                problems.append(f"{key} = {cfg.get(key)!r} (one of {', '.join(allowed)})")
        for name, agent in (cfg.get("agents") or {}).items():
            cmd = (agent or {}).get("command") if isinstance(agent, dict) else None
            if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
                problems.append(f"agents.{name}.command must be a list of strings")
        if problems:
            self.add("config", "fail", "; ".join(problems), f"edit {f}, then `orbital config --apply`")
        else:
            self.add("config", "ok", f"{f.name} OK ({cfg.role}, machine {cfg.machine_name})")
        if unknown:
            self.add("config", "warn", f"unknown sections in config.toml: {', '.join(unknown)} (ignored)")
        return True

    def check_resolved(self, cfg: Config) -> None:
        f = cfg.paths.resolved_file
        fixer = lambda: (self._do(f"write {f}", lambda: cfg.write_resolved()), "rewritten")[1]  # noqa: E731
        if not f.is_file():
            self.add("config", "fail", f"{f.name} is missing (the Swift apps read it)", "orbital config --apply", fixer)
            return
        try:
            current = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            self.add("config", "fail", f"{f.name} is not valid JSON", "orbital config --apply", fixer)
            return
        if current != json.loads(json.dumps(cfg.resolved())):
            self.add("config", "warn", f"{f.name} is out of date with config.toml", "orbital config --apply", fixer)
        else:
            self.add("config", "ok", f"{f.name} up to date")

    def check_secrets(self, cfg: Config) -> None:
        f = cfg.paths.secrets_file
        if not f.is_file():
            self.add("secrets", "fail", f"no {f}", "re-run ./setup to create the tokens")
            return
        mode = stat.S_IMODE(f.stat().st_mode)
        if mode & 0o077:
            self.add("secrets", "fail", f"{f.name} is readable by others (mode {oct(mode)})", f"chmod 600 {f}",
                     lambda: (self._do(f"chmod 600 {f}", lambda: os.chmod(f, 0o600)), "chmod 600")[1])
        have = Secrets(cfg.paths).all()
        need = list(ROLE_TOKENS.get(cfg.role, []))
        if cfg.get("satellite.enabled"):
            need.append("ORBITAL_SATELLITE_TOKEN")
        if cfg.get("browser.enabled") and cfg.get("browser.bind") == "tailscale":
            need.append("ORBITAL_BROWSER_TOKEN")
        missing = [k for k in need if not have.get(k)]
        if missing:
            how = "join the hub again: ./setup --reconfigure" if "ORBITAL_HUB_TOKEN" in missing else "./setup --reconfigure"
            self.add("secrets", "fail", "missing: " + ", ".join(missing), how)
        else:
            self.add("secrets", "ok", f"{f.name}: {len(have)} keys, mode 0600" if not mode & 0o077 else f"{len(have)} keys")

    def check_tailscale(self, cfg: Config) -> None:
        from orbital.core import net
        if not net.tailscale_bin():
            level = "fail" if cfg.role == "satellite" else "warn"
            self.add("tailscale", level, "Tailscale isn't installed", "install it from https://tailscale.com/download and log in")
            return
        status = net.tailscale_status()
        state = (status or {}).get("BackendState")
        me = net.tailscale_self()
        if state != "Running" or not me:
            self.add("tailscale", "fail" if cfg.role == "satellite" else "warn",
                     f"Tailscale is not connected (state {state or 'unknown'})", "open Tailscale and log in (`tailscale up`)")
            return
        self.add("tailscale", "ok", f"{me.dns or me.name} ({me.ip})")

    def check_services(self, cfg: Config):
        from orbital import services
        from orbital.services.spec import tracker_missing
        try:
            rows = services.status(cfg, platform=self.platform)
        except RuntimeError as exc:
            self.add("services", "warn", str(exc))
            return []

        def reinstall():
            outcomes, _ = services.install(cfg, ops=self.ops, platform=self.platform)
            bad = [o.name for o in outcomes if not o.ok]
            return "reinstalled" + (f" (failed: {', '.join(bad)})" if bad else "")

        for s in rows:
            if not s.wanted:
                if s.installed:
                    self.add("services", "warn", f"{s.name}: installed but not wanted here ({s.reason})",
                             "orbital services install (removes it)", reinstall)
                continue
            if s.name == "tracker" and tracker_missing(cfg, self.platform):
                self.add("services", "warn", "tracker: the app isn't installed",
                         "apps/tracker/scripts/build.sh && apps/tracker/scripts/install.sh --no-build && orbital services install")
                continue
            if not s.installed:
                self.add("services", "fail", f"{s.name}: not installed", "orbital services install", reinstall)
            elif not s.current:
                self.add("services", "warn", f"{s.name}: service file is outdated", "orbital services install", reinstall)
            elif not s.loaded:
                self.add("services", "fail", f"{s.name}: installed but not loaded", f"orbital services start {s.name}",
                         lambda n=s.name: self._outcome(services.start(cfg, [n], ops=self.ops, platform=self.platform)))
            elif not s.running:
                last = f", last exit {s.last_exit}" if s.last_exit not in (None, 0, "") else ""
                self.add("services", "fail", f"{s.name}: not running ({s.state}{last})",
                         f"orbital services restart {s.name}; look at `orbital logs {s.name}` and {cfg.paths.logs}/{s.name}.stderr.log",
                         lambda n=s.name: self._outcome(services.restart(cfg, [n], ops=self.ops, platform=self.platform)))
            elif s.healthy is False:
                self.add("services", "fail", f"{s.name}: running but unhealthy ({s.health})",
                         f"orbital services restart {s.name}",
                         lambda n=s.name: self._outcome(services.restart(cfg, [n], ops=self.ops, platform=self.platform)))
            else:
                self.add("services", "ok", f"{s.name}: running" + (f", pid {s.pid}" if s.pid else "")
                         + (f", {s.health}" if s.health else ""))
        return rows

    def _outcome(self, outcomes) -> str:
        return "; ".join(f"{o.name} {o.message}" for o in outcomes) or "nothing to do"

    def check_ports(self, cfg: Config, statuses) -> None:
        from orbital.wizard.preflight import PORT_KEYS, PORT_LABELS, check_port
        running = {s.name for s in statuses if s.running}
        owner = {"hub": "hub", "tracker": "tracker", "stt": "tracker", "browser": "browser-bridge", "satellite": "satellite"}
        wanted = {s.name for s in statuses if s.wanted}
        for key, dotted in PORT_KEYS.items():
            svc = owner[key]
            if svc not in wanted or svc in running or (key == "stt" and not cfg.get("tracker.voice")):
                continue
            ps = check_port(key, int(cfg.get(dotted)))
            if ps.conflict:
                self.add("ports", "fail", f"port {ps.port} ({PORT_LABELS[key]}) is used by another program",
                         f"set another {dotted} in config.toml (or stop that program), then `orbital services restart`")

    def check_hub(self, cfg: Config) -> None:
        store = Secrets(cfg.paths)
        if cfg.role == "satellite":
            url = cfg.hub_url()
            code, info, err = _get_json(f"{url}/v1/hub/info", timeout=4)
            if code != 200 or not info or info.get("service") != "orbital-hub":
                self.add("hub", "fail", f"the hub at {url} doesn't answer ({err or code})",
                         "check the hub machine (`orbital services status` there) and that both are on Tailscale")
                return
            self.add("hub", "ok", f"hub reachable: {info.get('name')} on {info.get('machine')}")
            ident = {}
            try:
                ident = json.loads((cfg.paths.satellite_data / "identity.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            token = store.get("ORBITAL_HUB_TOKEN")
            if not token:
                return
            code, data, err = _get_json(f"{url}/v1/satellites/heartbeat", token=token, method="POST",
                                        body={"satellite_id": ident.get("satellite_id")}, timeout=5)
            if code == 200:
                self.add("hub", "ok", f"this machine is listed on the hub ({ident.get('satellite_id') or 'joined'})")
            elif code == 401:
                self.add("hub", "fail", "the hub doesn't know this machine any more (removed, or the hub was reset)",
                         "join again: ./setup --reconfigure (with a join token from `orbital satellites invite` on the hub)")
            else:
                self.add("hub", "warn", f"heartbeat check failed ({err or code})")
            return
        token = store.get("ORBITAL_AUTOMATION_TOKEN")
        code, data, err = _get_json(f"{cfg.hub_url()}/v1/satellites", token=token, timeout=4)
        if code == 0:
            return       # the hub service row already reports it down
        if code in (401, 403):
            self.add("hub", "fail", "the hub refuses ORBITAL_AUTOMATION_TOKEN", "restart the hub after changing secrets: orbital services restart hub")
            return
        sats = (data or {}).get("satellites") or []
        offline = [s.get("name") or s.get("id") for s in sats if s.get("status") != "online"]
        me = cfg.machine_name.lower()
        if cfg.get("satellite.enabled") and not any(str(s.get("machine", "")).lower() == me for s in sats):
            self.add("hub", "warn", "this hub's own satellite hasn't joined yet", "orbital services restart satellite")
        if offline:
            self.add("hub", "warn", f"satellites offline: {', '.join(map(str, offline))}",
                     "on each: `orbital doctor` (is it awake, on Tailscale, services running?)")
        else:
            self.add("hub", "ok", f"{len(sats)} satellite{'s' if len(sats) != 1 else ''} online")

    def check_browser(self, cfg: Config) -> None:
        if not cfg.get("browser.enabled"):
            return
        code, data, err = _get_json(f"http://127.0.0.1:{cfg.get('browser.bridge_port')}/status", timeout=3)
        if code != 200 or not isinstance(data, dict):
            return       # the service row reports the bridge itself
        ext = data.get("extension") if isinstance(data.get("extension"), dict) else {}
        connected = bool(ext.get("connected") or data.get("extension_connected"))
        if connected:
            self.add("browser", "ok", "Browser Controller extension connected")
        else:
            self.add("browser", "warn", "the Browser Controller extension isn't connected",
                     "Chrome → chrome://extensions → Developer mode → Load unpacked → the repo's browser-extension/ folder"
                     + (f"; set its port to {cfg.get('browser.bridge_port')}" if cfg.get("browser.bridge_port") != 7703 else ""))

    def check_logs(self, cfg: Config) -> None:
        logs = cfg.paths.logs
        if not logs.is_dir():
            return
        max_bytes = int(cfg.get("logging.max_bytes"))
        files = [p for p in logs.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        big = [p.name for p in files if p.stat().st_size > max_bytes * 1.1]
        budget = max_bytes * (int(cfg.get("logging.backups")) + 1) * 12
        if big:
            self.add("logs", "warn", f"over logging.max_bytes: {', '.join(big)}",
                     "the watchdog trims these; check `orbital services status watchdog`")
        if total > budget:
            self.add("logs", "warn", f"log folder is {total / 1e6:.0f} MB", f"delete old files in {logs} or lower logging.max_bytes")
        if not big and total <= budget:
            self.add("logs", "ok", f"{len(files)} files, {total / 1e6:.1f} MB")

    def check_agent_path(self, cfg: Config) -> None:
        from orbital.services import installed_path
        from orbital.services.spec import service_path
        # What the installed services really use; before install, what install would write.
        svc_path = installed_path(self.platform) or service_path()
        missing, only_user = [], []
        for name, agent in sorted((cfg.get("agents") or {}).items()):
            cmd = (agent or {}).get("command") or []
            if not cmd:
                continue
            exe = cmd[0]
            in_svc = shutil.which(exe, path=svc_path) if "/" not in exe else (exe if os.access(exe, os.X_OK) else None)
            in_user = shutil.which(exe)
            if in_svc:
                continue
            if in_user:
                only_user.append(f"{name} ({in_user})")
            elif name in ("claude", "codex", "grok", "gemini", "echo"):
                missing.append(name)
        if only_user:
            self.add("agents", "warn", "on your PATH but not the services': " + ", ".join(only_user),
                     "orbital services install (recomputes the services' PATH)",
                     lambda: self._reinstall_services(cfg))
        found = [n for n in ("claude", "codex", "grok", "gemini") if n not in missing]
        self.add("agents", "ok" if found else "warn",
                 ("agents available to services: " + ", ".join(found)) if found else "no agent CLIs found (claude, codex, grok, gemini)",
                 "" if found else "install one (e.g. Claude Code), then `orbital services install`")

    def _reinstall_services(self, cfg: Config) -> str:
        from orbital import services
        outcomes, _ = services.install(cfg, ops=self.ops, platform=self.platform)
        return self._outcome(outcomes)

    def check_skills(self, cfg: Config) -> None:
        from orbital.wizard.flow import rule_values
        from orbital.wizard.skills import Installer
        rows = [r for r in Installer(values=rule_values(cfg)).status() if r["present"]]
        if not rows:
            return
        outdated = [r["agent"] for r in rows if any(v in ("outdated", "missing") for v in r["skills"].values())
                    or r["rules"] == "outdated"]
        if outdated:
            self.add("skills", "warn", "agent skills missing or outdated for: " + ", ".join(outdated), "orbital skills install")
        else:
            self.add("skills", "ok", "agent skills installed for " + ", ".join(r["agent"] for r in rows))

    # ------------------------------------------------------------------ fixing
    def _do(self, what: str, fn) -> None:
        self.ops.note(what)
        if not self.ops.dry_run:
            fn()

    def fix(self) -> list[Finding]:
        done = []
        seen: set[str] = set()
        order = {"config": 0, "secrets": 1, "services": 2, "agents": 3}
        for f in sorted(self.findings, key=lambda x: order.get(x.area, 9)):
            if not f.fixer or f.level == "ok":
                continue
            key = f.fix if f.fix.startswith("orbital services install") else f.message
            if key in seen:
                f.fixed = "(same fix as above)"
                continue
            seen.add(key)
            try:
                f.fixed = (f.fixer() or "done") + (" (dry run)" if self.ops.dry_run else "")
            except Exception as exc:  # noqa: BLE001
                f.fixed = f"fix failed: {exc}"
            done.append(f)
        return done


def print_findings(ui: UI, findings: list[Finding]) -> None:
    area = None
    for f in findings:
        if f.area != area:
            area = f.area
            ui.line(ui.style(f"  {area}", "bold"))
        ui.status(f.level, f.message)
        if f.level != "ok" and f.fix:
            ui.hint(("fix (--fix does it): " if f.fixer else "fix: ") + f.fix)


def run(args) -> int:
    ui = UI()
    ops = Ops(dry_run=args.dry_run, out=ui.line)
    doc = Doctor(ops=ops)
    findings = doc.run()
    if args.fix:
        fixes = doc.fix()
        if not args.json:
            if fixes:
                ui.line(ui.style("  Fixing", "bold"))
                for f in fixes:
                    ui.ok(f"{f.message}: {f.fixed}")
            else:
                ui.info("no automatic fixes to apply")
        if fixes and not args.dry_run:
            import time
            time.sleep(2.0)       # give restarted services a moment before checking again
            findings = doc.run()
    if args.json:
        print(json.dumps({"ok": not any(f.level == "fail" for f in findings),
                          "findings": [f.to_dict() for f in findings]}, indent=2))
    else:
        ui.line("")
        print_findings(ui, findings)
        fails = sum(f.level == "fail" for f in findings)
        warns = sum(f.level == "warn" for f in findings)
        ui.line("")
        if fails or warns:
            auto = any(f.fixer for f in findings if f.level != "ok")
            ui.line(f"  {fails} problem{'s' if fails != 1 else ''}, {warns} warning{'s' if warns != 1 else ''}"
                    + ("; `orbital doctor --fix` applies the safe fixes" if auto and not args.fix else ""))
        else:
            ui.ok("everything looks healthy")
    return 1 if any(f.level == "fail" for f in findings) else 0
