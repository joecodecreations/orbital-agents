"""The setup wizard: `orbital setup` (and `./setup`, which installs Python bits and then runs this).

Hub:       preflight → role → this system → components → keys → Apple apps → agent skills → install → verify
Satellite: preflight → role → hub → components → apps → agent skills → install (+ join) → verify

Every side effect goes through `Ops`, so --dry-run prints the whole plan and changes nothing.
Every question can be answered by an answers file (orbital/wizard/answers.py) or by --yes.
Re-running keeps the current settings (questions already answered are skipped and shown as
"kept"); --reconfigure asks them again with the current values as defaults.
"""

from __future__ import annotations

import getpass
import json
import logging
import re
import shutil
import socket
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from orbital import __version__
from orbital.core.config import DEFAULTS, load_config
from orbital.core.paths import REPO_ROOT, Paths
from orbital.core.secrets import Secrets, mask, new_token
from orbital.services.ops import Ops, Result, default_exec
from orbital.wizard import apps as apps_mod
from orbital.wizard import configfile, preflight
from orbital.wizard.answers import Answers
from orbital.wizard.ui import UI, Abort

HUB_TOKENS = ("ORBITAL_AUTOMATION_TOKEN", "ORBITAL_JOIN_TOKEN")
TEAM_RE = re.compile(r"^[A-Z0-9]{10}$")
BUNDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(\.[A-Za-z0-9-]+)+$")


def _default_deps() -> dict[str, Callable]:
    """Functions the wizard calls that live in other packages (swapped for fakes in tests)."""

    def find_hubs(port: int) -> list[dict]:
        try:
            from orbital.satellite.discover import find_hubs as fh
        except ImportError:
            return []
        return fh(port)

    def join_hub(url: str, token: str, cfg) -> dict:
        from orbital.satellite.join import join_hub as jh
        return jh(url, token, cfg)

    def hub_info(url: str) -> dict | None:
        import urllib.request
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/v1/hub/info", timeout=3) as r:  # noqa: S310
                data = json.loads(r.read(8192).decode("utf-8", "replace"))
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("service") == "orbital-hub" else None

    def heartbeat(url: str, hub_token: str, satellite_id: str) -> tuple[bool, str]:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"{url.rstrip('/')}/v1/satellites/heartbeat", method="POST",
                                     data=json.dumps({"satellite_id": satellite_id}).encode(),
                                     headers={"Authorization": f"Bearer {hub_token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
                data = json.loads(r.read().decode())
            return bool(data.get("ok")), "the hub knows this machine"
        except urllib.error.HTTPError as exc:
            return False, f"the hub answered HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"no answer from the hub ({exc.__class__.__name__})"

    def install_services(cfg, ops):
        from orbital import services
        return services.install(cfg, ops=ops)

    def restart_services(cfg, names, ops):
        from orbital import services
        return services.restart(cfg, names, ops=ops)

    def wait_healthy(cfg, timeout):
        from orbital import services
        return services.wait_healthy(cfg, timeout=timeout)

    def live_test(env_extra: dict[str, str]) -> int:
        from orbital.cli.commands.test import run_suite
        return run_suite(live=True, unit=False, env_extra=env_extra)

    def teams():
        from orbital.wizard.apple import detect_teams
        return detect_teams()

    def devices():
        from orbital.wizard.apple import list_devices
        return list_devices()

    def gather(cfg):
        return preflight.gather(cfg)

    def tracker_app():
        from orbital.services.spec import find_tracker_app
        return find_tracker_app()

    return {
        "find_hubs": find_hubs, "join_hub": join_hub, "hub_info": hub_info, "heartbeat": heartbeat,
        "install_services": install_services, "restart_services": restart_services,
        "wait_healthy": wait_healthy, "live_test": live_test, "teams": teams, "devices": devices,
        "gather": gather, "tracker_app": tracker_app, "exec": default_exec,
    }


@dataclass
class Options:
    role: str | None = None
    answers: Answers = field(default_factory=Answers)
    yes: bool = False
    dry_run: bool = False
    reconfigure: bool = False
    user_home: Path | None = None          # where ~/.claude etc. live (tests: a temp dir)
    platform: str | None = None


def load_example_jobs(repo: Path = REPO_ROOT) -> tuple[dict, list[dict]]:
    f = repo / "feeds" / "jobs.example.json"
    if not f.is_file():
        return {}, []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, []
    if isinstance(data, list):
        return {"jobs": data}, [j for j in data if isinstance(j, dict)]
    jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    return data, jobs


def job_id(job: dict) -> str:
    return str(job.get("id") or job.get("name") or "")


def jobs_document(example: dict, jobs: list[dict], selected: list[str], existing: dict | None = None) -> dict:
    """jobs.json content: the example's settings + the chosen jobs. An existing file is kept and only
    gets the newly chosen jobs it doesn't have yet."""
    chosen = [j for j in jobs if job_id(j) in selected]
    if existing:
        out = dict(existing)
        have = {job_id(j) for j in out.get("jobs") or [] if isinstance(j, dict)}
        out["jobs"] = list(out.get("jobs") or []) + [j for j in chosen if job_id(j) not in have]
        for key in ("groups", "lanes"):
            if key in example and key not in out:
                out[key] = example[key]
        return out
    out = {k: v for k, v in example.items() if k != "jobs"}
    out["jobs"] = chosen
    return out


class Wizard:
    def __init__(self, opts: Options, ui: UI | None = None, ops: Ops | None = None,
                 deps: dict[str, Callable] | None = None, repo: Path = REPO_ROOT):
        self.opts = opts
        self.a = opts.answers
        self.ui = ui or UI(interactive=False if opts.yes else None)
        if opts.yes:
            self.ui.interactive = False
        self.ops = ops or Ops(dry_run=opts.dry_run, out=self.ui.line)
        self.deps = _default_deps()
        self.deps.update(deps or {})
        self.repo = repo
        self.paths = Paths.resolve()
        self.platform = opts.platform or ("macos" if sys.platform == "darwin" else
                                          "linux" if sys.platform.startswith("linux") else sys.platform)
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.secret_changes: dict[str, str] = {}
        self.generated: list[str] = []
        self.log = self._logger()
        self.raw: dict = {}
        self.env: preflight.Environment | None = None
        self.hubs: list[dict] = []
        self.role = "hub"
        self.extras: dict[str, Any] = {}
        self.existing = False
        self.merged: dict = configfile.merge(DEFAULTS, {})
        self.secrets_now: dict[str, str] = {}
        self.total = 9
        self.step_no = 0

    # ------------------------------------------------------------------ plumbing
    def _logger(self) -> logging.Logger:
        if self.opts.dry_run:
            lg = logging.getLogger("orbital.wizard.dry-run")
            lg.addHandler(logging.NullHandler())
            lg.propagate = False
            return lg
        try:
            from orbital.core.logs import get_logger
            return get_logger("wizard", echo=False)
        except Exception:  # noqa: BLE001 - logging must never stop setup
            lg = logging.getLogger("orbital.wizard.fallback")
            lg.addHandler(logging.NullHandler())
            return lg

    def _log(self, msg: str, **fields) -> None:
        self.log.info(msg, extra={"fields": fields} if fields else None)

    @property
    def keep(self) -> bool:
        """Re-run without --reconfigure: values already in config.toml are kept, not asked."""
        return self.existing and not self.opts.reconfigure

    def cur(self, dotted: str, default: Any = None) -> Any:
        return configfile.get_dotted(self.merged, dotted, default)

    def put(self, dotted: str, value: Any) -> None:
        configfile.set_dotted(self.merged, dotted, value)

    def preset(self, answer_key: str | None, config_key: str | None = None) -> tuple[Any, str]:
        """(value, why) when the question is already answered, else (None, '')."""
        if answer_key and self.a.has(answer_key):
            return self.a.get(answer_key), self.a.why(answer_key)
        if config_key and self.keep:
            return self.cur(config_key), "kept"
        return None, ""

    def secret(self, key: str) -> str:
        if key in self.secret_changes:
            return self.secret_changes[key]
        return self.secrets_now.get(key, "")

    def set_secret(self, key: str, value: str) -> None:
        if value and value != self.secrets_now.get(key, ""):
            self.secret_changes[key] = value

    def ensure_token(self, key: str) -> str:
        current = self.secret(key)
        if current:
            return current
        token = new_token()
        self.secret_changes[key] = token
        self.generated.append(key)
        return token

    # ------------------------------------------------------------------ entry
    def run(self) -> int:
        ui = self.ui
        ui.banner(f"Orbital Agents setup  v{__version__}",
                  "hub, satellites, tracker, apps and browser controller" + ("   [dry run: nothing is changed]" if self.opts.dry_run else ""))
        if self.a.source:
            ui.info(f"answers file: {self.a.source}")
            for k in self.a.unknown_keys():
                ui.warn(f"answers file: unknown key {k!r} (ignored; see config/answers.example.toml)")
        if not self.ui.interactive:
            ui.info("non-interactive: using answers, kept values and defaults")
        try:
            if not self._load_current():
                return 2
            self._log("setup started", dry_run=self.opts.dry_run, existing=self.existing,
                      reconfigure=self.opts.reconfigure)
            self.total = 9
            self.step_no = 0
            self.step_preflight()
            self.step_role()
            if self.role == "hub":
                self.step_hub_system()
                self.step_components()
                self.step_keys()
                self.step_apple()
                self.step_skills()
                self.step_install()
                self.step_verify()
            else:
                self.step_satellite_hub()
                self.step_components()
                self.step_apple()
                self.step_skills()
                self.step_install()
                self.step_join()
                self.step_verify()
            self.summary()
        except Abort:
            ui.line("")
            for p in dict.fromkeys(self.problems):
                ui.fail(p)
            ui.warn("Setup stopped. Nothing after the last finished step was changed; run it again any time.")
            self._log("setup aborted", problems=len(self.problems))
            return 1 if self.problems else 130
        self._log("setup finished", problems=len(self.problems))
        return 1 if self.problems else 0

    def _would(self, done: str) -> str:
        return f"would be {done}" if self.opts.dry_run else done

    def step(self, title: str) -> None:
        self.step_no += 1
        self.ui.step(self.step_no, self.total, title)

    def _load_current(self) -> bool:
        self.existing = self.paths.config_file.is_file()
        try:
            self.raw = configfile.read_raw(self.paths.config_file)
        except tomllib.TOMLDecodeError as exc:
            self.ui.fail(f"{self.paths.config_file} can't be read: {exc}")
            self.ui.hint("Fix the file, or move it aside (it will be recreated): "
                         f"mv {self.paths.config_file} {self.paths.config_file}.broken")
            return False
        self.merged = configfile.merge(DEFAULTS, self.raw)
        self.secrets_now = Secrets(self.paths).all()
        if self.existing:
            role = self.cur("system.role")
            self.ui.info(f"existing setup found ({role}) in {self.paths.home}"
                         + ("; asking everything again (--reconfigure)" if self.opts.reconfigure else
                            "; keeping your answers (use --reconfigure to change them)"))
        return True

    # ------------------------------------------------------------------ 1. preflight
    def step_preflight(self) -> None:
        self.step("Checking this machine")
        cfg = self._cfg()
        # Check the ports this run will use: the answers file's, else the current or default ones.
        data = json.loads(json.dumps(cfg.data))
        for key, dotted in preflight.PORT_KEYS.items():
            given = self.a.get(f"ports.{key}")
            if given not in (None, ""):
                section, name = dotted.split(".", 1)
                data.setdefault(section, {})[name] = int(given)
        cfg.data = data
        self.env = self.deps["gather"](cfg)
        blocking = preflight.report(self.ui, self.env)
        if blocking:
            raise Abort()
        if not self.env.tailscale_running and self.ui.interactive:
            if not self.ui.ask_bool("Continue without Tailscale for now?", True):
                raise Abort()

    # ------------------------------------------------------------------ 2. role
    def step_role(self) -> None:
        self.step("Role")
        forced = self.opts.role or (self.a.get("role") if self.a.get("role") in ("hub", "satellite") else None)
        port = int(self.a.get("ports.hub") or self.cur("hub.port") or 7700)
        if not forced and not (self.keep and self.cur("system.role")):
            if self.env and self.env.tailscale_running:
                self.ui.info(f"looking for hubs on your tailnet (port {port})…")
                try:
                    self.hubs = [h for h in self.deps["find_hubs"](port) if not h.get("is_self")]
                except Exception as exc:  # noqa: BLE001
                    self.ui.warn(f"couldn't search the tailnet: {exc}")
                    self.hubs = []
                for h in self.hubs:
                    self.ui.ok(f"found hub {h.get('name') or '?'} on {h.get('machine') or h.get('host')}: {h.get('url')}")
                if not self.hubs:
                    self.ui.info("no hub found on the tailnet")
            else:
                self.ui.info("Tailscale isn't running, so no hub search")
        suggested = "satellite" if self.hubs else "hub"
        if self.existing:
            suggested = self.cur("system.role", suggested)
        if forced:
            role = forced
            self.ui._chosen("This machine is", role, "--role" if self.opts.role else self.a.why("role"))
        else:
            preset, why = (self.cur("system.role"), "kept") if self.keep else (None, "")
            role = self.ui.ask_choice("This machine is", [
                ("hub", "the hub: the primary machine (runs the hub, scheduler and app data)"),
                ("satellite", "a satellite: joins a hub" + (" found on your tailnet" if self.hubs else "")),
            ], suggested, preset=preset, why=why)
        if role == "hub" and self.hubs:
            self.ui.warn("another hub is already on your tailnet; that's fine, but each satellite joins one hub")
        previous = self.raw.get("system", {}).get("role") if self.raw else None
        if previous and previous != role:
            self.ui.warn(f"this machine was set up as a {previous}; it becomes a {role} "
                         f"(its {previous}-only services are removed)")
        self.role = role
        self.put("system.role", role)
        self._log("role chosen", role=role, hubs_found=len(self.hubs))

    # ------------------------------------------------------------------ 3a. hub system + login
    def step_hub_system(self) -> None:
        self.step("This system")
        ui = self.ui
        v, why = self.preset("system.name", "system.name")
        self.put("system.name", ui.ask("System name (shown in the apps)", self.cur("system.name") or "Orbital Agents",
                                       preset=v, why=why))
        self._ask_machine_name()
        v, why = self.preset("login.username")
        current_user = self.secret("ORBITAL_HUB_USERNAME")
        if v is None and self.keep and current_user:
            v, why = current_user, "kept"
        username = ui.ask("App sign-in username", current_user or _local_user(), preset=v, why=why,
                          validate=lambda s: None if re.match(r"^[A-Za-z0-9._@-]{1,64}$", s or "") else
                          "letters, digits and . _ @ - only")
        self.set_secret("ORBITAL_HUB_USERNAME", username)
        current_pw = self.secret("ORBITAL_HUB_PASSWORD")
        given, why = self.preset("login.password")
        if given:
            password = str(given)
            ui._chosen("App sign-in password", mask(password), why)
        elif current_pw and (self.keep or not ui.interactive):
            password = current_pw
            ui._chosen("App sign-in password", mask(password), "kept")
        elif ui.interactive:
            gen = ui.ask_bool("Generate a strong password for the app sign-in?", not current_pw)
            if gen:
                password = new_token(18)
                self.generated.append("ORBITAL_HUB_PASSWORD")
            else:
                password = ui.ask_secret("Password (8+ characters)", current=current_pw, allow_empty=False)
                while len(password) < 8:
                    ui.warn("at least 8 characters, please")
                    password = ui.ask_secret("Password (8+ characters)", allow_empty=False)
        else:
            if self.a.get("login.generate_password", True) is False:
                self.problems.append("no app password: set login.password or ORBITAL_HUB_PASSWORD")
                password = ""
            else:
                password = new_token(18)
                self.generated.append("ORBITAL_HUB_PASSWORD")
                ui._chosen("App sign-in password", "generated", "default")
        if password:
            self.set_secret("ORBITAL_HUB_PASSWORD", password)

    def _ask_machine_name(self) -> None:
        host = socket.gethostname().split(".")[0]
        v, why = self.preset("system.machine_name", "system.machine_name")
        if v == "" and why == "kept":
            v = host
        # The Tailscale name is usually more telling than a generic hostname like "Mac".
        ts_name = ""
        if self.env and (self.env.tailscale_dns or self.env.tailscale_name):
            ts_name = (self.env.tailscale_dns or "").split(".")[0] or self.env.tailscale_name
        suggestion = _machine_slug(self.cur("system.machine_name") or ts_name or host) or "machine"
        name = self.ui.ask("This machine's name", suggestion, preset=v, why=why,
                           validate=lambda s: None if re.match(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$", s or "") else
                           "letters, digits, spaces and . _ - (up to 64)")
        self.put("system.machine_name", "" if name == host else name)

    # ------------------------------------------------------------------ 3b. satellite: which hub
    def step_satellite_hub(self) -> None:
        self.step("Hub")
        ui = self.ui
        self._ask_machine_name()
        given, why = self.preset("satellite.hub_url", "hub.url")
        url = str(given or "")
        stand_in = False
        if not url:
            options = [(h["url"], f"{h.get('name') or 'hub'} on {h.get('machine') or h.get('host')}  ({h['url']})")
                       for h in self.hubs]
            if len(options) == 1 and not ui.interactive:
                url = options[0][0]
                ui._chosen("Hub", url, "the only hub found")
            elif options:
                options.append(("other", "another hub (type its address)"))
                url = ui.ask_choice("Which hub", options, options[0][0])
            if not url or url == "other":
                if not ui.interactive:
                    ui.fail("no hub found on the tailnet and no satellite.hub_url given")
                    self.problems.append("no hub to join: set satellite.hub_url in the answers file "
                                         "(or check that the hub is running and on Tailscale)")
                    if not self.opts.dry_run:
                        raise Abort()
                    # A dry run still prints the rest of the plan, with a stand-in for the hub's address.
                    ui.hint("dry run: the rest of the plan uses a stand-in address for the hub")
                    url, stand_in = "http://your-hub", True
                else:
                    url = ui.ask("Hub address (e.g. http://my-mac.your-tailnet.ts.net:7700)", "",
                                 validate=lambda s: None if s.strip() else "the hub's address, please")
        url = _normalize_url(url, int(self.a.get("ports.hub") or self.cur("hub.port") or 7700))
        info = None if stand_in else self.deps["hub_info"](url)
        if info:
            ui.ok(f"hub answers: {info.get('name') or 'hub'} on {info.get('machine') or '?'} (v{info.get('version', '?')})")
        elif not stand_in:
            ui.warn(f"no Orbital Agents hub answers at {url} right now")
            ui.hint("Check that the hub is running (`orbital services status` on it) and both machines are on Tailscale.")
            if ui.interactive and not ui.ask_bool("Continue anyway?", False):
                raise Abort()
            self.notes.append(f"the hub at {url} didn't answer during setup")
        self.put("hub.url", url)
        # Join token
        identity = self._identity()
        already = bool(self.secret("ORBITAL_HUB_TOKEN")) and identity.get("hub_url") == url
        self.extras["already_joined"] = already and self.keep
        tok, why = self.preset("satellite.join_token")
        if self.extras["already_joined"] and not tok:
            ui._chosen("Join token", "not needed", f"already joined as {identity.get('satellite_id')}")
            self.extras["join_token"] = ""
            return
        token = ui.ask_secret("Join token (the hub's setup printed it; `orbital satellites invite` shows it again)",
                              preset=tok, why=why, allow_empty=False) if (tok or ui.interactive) else ""
        if not token:
            self.problems.append("no join token: pass satellite.join_token in the answers file or set ORBITAL_JOIN_TOKEN")
        self.extras["join_token"] = token

    def _identity(self) -> dict:
        f = self.paths.satellite_data / "identity.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ 4. components (+ ports)
    def step_components(self) -> None:
        self.step("Components")
        ui = self.ui
        env = self.env
        hub = self.role == "hub"
        if self.platform == "macos":
            v, why = self.preset("components.tracker", "tracker.enabled")
            self.put("tracker.enabled", ui.ask_bool("Agent Satellite Tracker (floating panel on this Mac)",
                                                    bool(self.cur("tracker.enabled", True)), preset=v, why=why))
            if self.cur("tracker.enabled"):
                self._ask_hotkeys()
        else:
            if self.cur("tracker.enabled", True):
                ui.info("Agent Satellite Tracker: macOS only (agent jobs on this machine don't show in the tracker)")
            self.put("tracker.enabled", False)
        has_chrome = bool(env and env.chrome)
        v, why = self.preset("components.browser", "browser.enabled")
        browser = ui.ask_bool("Browser Controller (agents can drive Chrome on this machine)",
                              bool(self.cur("browser.enabled", True)) and has_chrome, preset=v, why=why)
        self.put("browser.enabled", browser)
        if browser:
            v, why = self.preset("components.browser_remote")
            if v is None and self.keep:
                v, why = self.cur("browser.bind") == "tailscale", "kept"
            default_remote = (self.cur("browser.bind") == "tailscale") if self.existing else (not hub)
            remote = ui.ask_bool("Let your other machines drive this Chrome over Tailscale", default_remote,
                                 preset=v, why=why)
            self.put("browser.bind", "tailscale" if remote else "loopback")
        v, why = self.preset("components.agents", "satellite.enabled")
        self.put("satellite.enabled", ui.ask_bool("Run agents on this machine when asked (claude, codex, …)",
                                                  bool(self.cur("satellite.enabled", True)), preset=v, why=why))
        if hub:
            v, why = self.preset("components.scheduler", "scheduler.enabled")
            sched = ui.ask_bool("Scheduler (runs data feeds and pushes them to the app)",
                                bool(self.cur("scheduler.enabled", True)), preset=v, why=why)
            self.put("scheduler.enabled", sched)
            if sched:
                self._ask_feeds()
        else:
            self.put("scheduler.enabled", False)
        v, why = self.preset("components.services")
        self.extras["services"] = ui.ask_bool("Install login services (start at login, restart on crash)", True,
                                              preset=v, why=why if v is not None else "")
        self._resolve_ports()

    def _ask_hotkeys(self) -> None:
        from orbital.wizard.skills import other_tracker_signals
        ui = self.ui
        signals = other_tracker_signals(self.opts.user_home)
        self.extras["other_tracker"] = signals
        v, why = self.preset("tracker.hotkeys", "tracker.hotkeys")
        on = ui.ask_bool("Tracker keyboard shortcuts (show/hide, type an item, mic)", bool(self.cur("tracker.hotkeys", True)),
                         preset=v, why=why)
        self.put("tracker.hotkeys", on)
        if not on:
            return
        current = str(self.cur("tracker.hotkey_modifiers") or "ctrl+option")
        suggestion = current
        if signals and current == "ctrl+option" and not self.raw.get("tracker", {}).get("hotkey_modifiers"):
            suggestion = "ctrl+option+shift"
            ui.warn("another agent tracker seems to be set up here (" + "; ".join(signals[:2]) + ")")
            ui.hint("Its shortcuts may already use ctrl+option, so ctrl+option+shift is suggested for this one.")
        v, why = self.preset("tracker.hotkey_modifiers", "tracker.hotkey_modifiers" if "hotkey_modifiers" in self.raw.get("tracker", {}) else None)
        mods = ui.ask("Shortcut modifiers (ctrl, option, shift, cmd joined with +)", suggestion, preset=v, why=why,
                      validate=_check_modifiers)
        self.put("tracker.hotkey_modifiers", mods.lower().replace(" ", ""))

    def _ask_feeds(self) -> None:
        example, jobs = load_example_jobs(self.repo)
        self.extras["jobs_example"] = (example, jobs)
        exists = self.paths.jobs_file.is_file()
        if not jobs:
            self.ui.info("no example feeds in this checkout (feeds/jobs.example.json)")
            self.extras["feeds"] = []
            return
        v, why = self.preset("components.feeds")
        if v is None and exists and self.keep:
            self.ui._chosen("Example feeds", "your jobs.json is kept", "kept")
            self.extras["feeds"] = None
            return
        defaults = [job_id(j) for j in jobs if j.get("enabled", True) is not False and j.get("default", True) is not False]
        options = [(job_id(j), f"{j.get('name') or job_id(j)}" + (f": {j['description']}" if j.get("description") else ""))
                   for j in jobs]
        self.extras["feeds"] = self.ui.ask_multi("Example feeds to schedule", options, defaults, preset=v, why=why)

    def _resolve_ports(self) -> None:
        needed = []
        if self.role == "hub":
            needed.append("hub")
        if self.cur("satellite.enabled"):
            needed.append("satellite")
        if self.cur("browser.enabled"):
            needed.append("browser")
        if self.cur("tracker.enabled") and self.platform == "macos":
            needed.append("tracker")
            if self.cur("tracker.voice"):
                needed.append("stt")
        policy = str(self.a.get("ports.on_conflict") or "next-free")
        taken = {int(self.cur(k)) for k in preflight.PORT_KEYS.values()}
        for key in needed:
            dotted = preflight.PORT_KEYS[key]
            given = self.a.get(f"ports.{key}")
            if given is not None:
                self.put(dotted, int(given))
            port = int(self.cur(dotted))
            known = self.env.ports.get(key) if self.env else None
            state = known if (known and known.port == port) else preflight.check_port(key, port)
            if not state.conflict:
                continue
            label = preflight.PORT_LABELS[key]
            alt = preflight.next_free_port(port, taken)
            if self.ui.interactive:
                new = self.ui.ask_int(f"Port {port} ({label}) is used by another program. Port to use instead", alt)
            elif policy == "keep":
                self.ui.warn(f"port {port} ({label}) is taken; keeping it (ports.on_conflict = keep)")
                self.problems.append(f"port {port} ({label}) is used by another program")
                continue
            elif policy == "fail":
                self.ui.fail(f"port {port} ({label}) is taken (ports.on_conflict = fail)")
                self.problems.append(f"port {port} ({label}) is used by another program")
                raise Abort()
            else:
                new = alt
                self.ui._chosen(f"Port for {label} ({port} is taken)", str(new), "next free port")
            taken.add(new)
            self.put(dotted, int(new))

    # ------------------------------------------------------------------ 5. keys
    def step_keys(self) -> None:
        self.step("Keys and tokens")
        ui = self.ui
        ui.hint("Optional API keys. They go to secrets.env (mode 0600), never into config.toml or logs.")
        for key, label in (("XAI_API_KEY", "xAI API key (Desk voice replies)"),
                           ("FISH_API_KEY", "Fish Audio API key (spoken replies)")):
            v, why = self.preset(f"keys.{key}")
            current = self.secret(key)
            if v is None and current and self.keep:
                v, why = current, "kept"
            value = ui.ask_secret(f"{label}, optional", current=current, preset=v, why=why)
            if value:
                self.set_secret(key, value)
        for key in self._token_keys():
            had = bool(self.secret(key))
            value = self.ensure_token(key)
            ui.info(f"{key}: {mask(value)} ({'kept' if had else 'will be generated' if self.opts.dry_run else 'generated'})")

    def _token_keys(self) -> list[str]:
        keys = list(HUB_TOKENS) if self.role == "hub" else []
        if self.cur("satellite.enabled"):
            keys.append("ORBITAL_SATELLITE_TOKEN")
        if self.cur("browser.enabled"):
            keys.append("ORBITAL_BROWSER_TOKEN")
        return keys

    # ------------------------------------------------------------------ 6. apple
    def step_apple(self) -> None:
        self.step("Apps")
        ui = self.ui
        env = self.env
        self.extras.setdefault("builds", [])
        if self.platform != "macos":
            ui.info("the tracker and the Mac/iPhone apps are built on a Mac; nothing to build here")
            return
        has_swift = bool(env and (env.swift or env.command_line_tools or env.xcode))
        has_xcode = bool(env and env.xcode)
        tracker_installed = self.deps["tracker_app"]() is not None
        if self.cur("tracker.enabled"):
            v, why = self.preset("apple.build_tracker")
            default = has_swift and (not tracker_installed or self.opts.reconfigure)
            if not has_swift:
                ui.warn("the tracker needs the Swift toolchain: run `xcode-select --install`, then re-run setup")
            if tracker_installed and v is None:
                ui.info("the tracker app is already installed")
            if ui.ask_bool("Build and install the tracker app now", default, preset=v, why=why):
                self.extras["builds"].append("tracker")
        if self.role != "hub":
            if not self.cur("tracker.enabled"):
                ui.info("nothing to build on this satellite (the tracker is off; the Mac/iPhone apps are built on the hub)")
            return
        # Team ID / bundle prefix / device: the Mac and iPhone app need them for signing.
        teams = self.deps["teams"]() if has_xcode else []
        current_team = str(self.cur("apple.team_id") or "")
        default_team = current_team or (teams[0].team_id if teams else "")
        if teams and not current_team:
            ui.ok("Apple Development certificate found: team " + ", ".join(t.team_id for t in teams))
        v, why = self.preset("apple.team_id", "apple.team_id" if current_team else None)
        team = ui.ask("Apple Developer Team ID (empty = skip the Mac/iPhone apps for now)", default_team, preset=v,
                      why=why, validate=lambda s: None if (not s or TEAM_RE.match(s)) else "10 letters/digits, e.g. from developer.apple.com → Membership")
        self.put("apple.team_id", team)
        current_prefix = str(self.cur("apple.bundle_prefix") or "com.example")
        suggestion = current_prefix if current_prefix != "com.example" else _suggest_prefix()
        v, why = self.preset("apple.bundle_prefix", "apple.bundle_prefix" if current_prefix != "com.example" else None)
        prefix = ui.ask("Bundle id prefix (your reverse domain)", suggestion, preset=v, why=why,
                        validate=lambda s: None if BUNDLE_RE.match(s or "") else "like com.yourname")
        self.put("apple.bundle_prefix", prefix)
        if not team or not has_xcode:
            if not has_xcode:
                ui.info("full Xcode isn't installed, so the Mac/iPhone apps are skipped (install Xcode, then re-run setup)")
            return
        mac_app = Path("/Applications") / f"{self.cur('apple.app_name') or 'Orbital Agents'}.app"
        v, why = self.preset("apple.build_mac_app")
        if ui.ask_bool("Build and install the Orbital Agents Mac app", not mac_app.exists() or self.opts.reconfigure,
                       preset=v, why=why):
            self.extras["builds"].append("mac")
        devices = self.deps["devices"]()
        connected = [d for d in devices if d.connected]
        want_device = str(self.a.get("apple.ios_device") or self.cur("apple.ios_device") or "")
        if connected:
            names = [d.name for d in connected]
            default_dev = want_device if want_device in names else names[0]
            if len(names) > 1:
                device = ui.ask_choice("iPhone to install to", [(n, n) for n in names], default_dev,
                                       preset=self.a.get("apple.ios_device"))
            else:
                device = default_dev
                ui.ok(f"iPhone connected: {device}")
            self.put("apple.ios_device", device)
        else:
            ui.info("no iPhone connected (plug it in and trust this Mac to install the app; re-run setup later)")
            device = ""
        v = self.a.get("apple.build_iphone_app")
        if device:
            if v is not None and str(v).lower() == "auto":
                build = True
                ui._chosen(f"Build and install the iPhone app on {device}", "yes", "auto: a device is connected")
            else:
                build = ui.ask_bool(f"Build and install the iPhone app on {device}", not self.keep,
                                    preset=v, why="answers file")
            if build:
                self.extras["builds"].append("iphone")
        elif v is not None and str(v).lower() not in ("auto", "false", "no", "0"):
            self.notes.append("iPhone app not installed: no iPhone connected (connect it and re-run setup)")
        self.extras["device"] = device

    # ------------------------------------------------------------------ 7. skills
    def step_skills(self) -> None:
        self.step("Agent skills")
        from orbital.wizard.skills import agent_homes
        ui = self.ui
        homes = [h for h in agent_homes(self.opts.user_home) if h.exists]
        if not homes:
            ui.info("no agent homes here (~/.claude, ~/.codex, ~/.grok, ~/.gemini): nothing to install")
            self.extras["skills"] = False
            return
        ui.info("agent homes found: " + ", ".join(h.agent for h in homes))
        v, why = self.preset("skills.install")
        self.extras["skills"] = ui.ask_bool("Install the Orbital Agents skills + always-on rule for these agents",
                                            True, preset=v, why=why)
        agents = self.a.get("skills.agents")
        if isinstance(agents, str):
            agents = None if agents.strip().lower() == "all" else [x.strip() for x in agents.replace(",", " ").split()]
        self.extras["skills_agents"] = agents
        rules = str(self.a.get("skills.rules") or "auto").lower()
        if rules in ("true", "install"):
            rules = "yes"
        if rules in ("false", "skip"):
            rules = "no"
        self.extras["skills_rules"] = rules if rules in ("auto", "yes", "no") else "auto"

    # ------------------------------------------------------------------ 8. install
    def _cfg(self):
        cfg = load_config(self.paths.home)
        cfg.data = self.merged
        return cfg

    def step_install(self) -> None:
        self.step("Installing")
        ui, ops = self.ui, self.ops
        if not self.opts.dry_run:
            self.paths.ensure()
        # config.toml
        text = configfile.render(self.merged)
        if self.paths.config_file.is_file() and self.paths.config_file.read_text(encoding="utf-8") != text:
            backup = self.paths.config_file.with_name("config.toml.bak")
            ops.copy(self.paths.config_file, backup)
        if ops.write(self.paths.config_file, text, mode=0o644):
            ui.ok(f"settings {self._would('written')}: {self.paths.config_file}")
        else:
            ui.ok(f"settings unchanged: {self.paths.config_file}")
        # secrets.env
        if self.role == "satellite":
            for key in self._token_keys():
                self.ensure_token(key)
        if self.secret_changes:
            ops.note(f"update secrets.env: {', '.join(sorted(self.secret_changes))} (values not shown)")
            if not self.opts.dry_run:
                store = Secrets(self.paths)
                for k, v in self.secret_changes.items():
                    store.set(k, v)
            ui.ok(f"secrets {self._would('saved')}: {self.paths.secrets_file} ({', '.join(sorted(self.secret_changes))})")
            self._log("secrets updated", keys=sorted(self.secret_changes))
        # jobs.json
        feeds = self.extras.get("feeds")
        if self.role == "hub" and self.cur("scheduler.enabled") and feeds is not None and self.extras.get("jobs_example"):
            example, jobs = self.extras["jobs_example"]
            existing = None
            if self.paths.jobs_file.is_file():
                try:
                    existing = json.loads(self.paths.jobs_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    ui.warn(f"{self.paths.jobs_file} isn't valid JSON; left as it is")
                    existing = {"_broken": True}
            if not (existing or {}).get("_broken"):
                doc = jobs_document(example, jobs, feeds, existing)
                if ops.write(self.paths.jobs_file, json.dumps(doc, indent=2) + "\n"):
                    ui.ok(f"feeds {self._would('scheduled')}: {', '.join(feeds) or 'none'} ({self.paths.jobs_file})")
        # config.resolved.json
        ops.note(f"write {self.paths.resolved_file}")
        if not self.opts.dry_run:
            load_config(self.paths.home).write_resolved()
        cfg = load_config(self.paths.home) if not self.opts.dry_run else self._cfg()
        # apps (tracker first: its login item needs the installed app)
        for name in self.extras.get("builds", []):
            if name == "tracker":
                r = apps_mod.build_tracker(ui, ops, cfg, self.repo)
            elif name == "mac":
                r = apps_mod.deploy_mac_app(ui, ops, cfg, self.repo)
            else:
                r = apps_mod.deploy_iphone_app(ui, ops, cfg, self.extras.get("device", ""), self.repo)
            self._report_build(r)
        # services
        if self.extras.get("services", True):
            try:
                outcomes, notes = self.deps["install_services"](cfg, ops)
            except Exception as exc:  # noqa: BLE001
                ui.fail(f"services: {exc}")
                self.problems.append(f"services: {exc}")
                outcomes, notes = [], []
            for o in outcomes:
                message = "would be installed and started" if (self.opts.dry_run and o.ok) else o.message
                (ui.ok if o.ok else ui.fail)(f"service {o.name}: {message}")
                if not o.ok:
                    self.problems.append(f"service {o.name}: {o.message}")
            for n in notes:
                ui.warn(n)
                self.notes.append(n)
            self.extras["service_names"] = [o.name for o in outcomes
                                            if o.ok and not o.message.startswith(("removed", "not installed"))]
        else:
            ui.info("login services skipped (components.services = false); start them with `orbital services install`")
        # skills
        if self.extras.get("skills"):
            self._install_skills(cfg)
        # browser extension
        if self.cur("browser.enabled"):
            self._extension(cfg)

    def _report_build(self, r) -> None:
        if r.skipped:
            self.ui.info(f"{r.name}: skipped ({r.message})")
            if "not found" in r.message:
                self.notes.append(f"{r.name}: {r.message}")
        elif r.ok:
            self.ui.ok(f"{r.name}: {r.message}")
        else:
            self.ui.fail(f"{r.name}: {r.message}")
            for line in r.log.splitlines()[1:]:
                self.ui.hint(line)
            self.problems.append(f"{r.name}: {r.message} (log: {r.log.splitlines()[0]})")

    def step_join(self) -> None:
        self.step("Joining the hub")
        self._join(load_config(self.paths.home) if not self.opts.dry_run else self._cfg())

    def _join(self, cfg) -> None:
        ui = self.ui
        url = self.cur("hub.url")
        if self.extras.get("already_joined"):
            ident = self._identity()
            ok, detail = self.deps["heartbeat"](url, self.secret("ORBITAL_HUB_TOKEN"), ident.get("satellite_id", ""))
            (ui.ok if ok else ui.warn)(f"already joined {url}: {detail}")
            if not ok:
                self.problems.append("the hub doesn't accept this machine's token any more: re-run with --reconfigure "
                                     "and a fresh join token")
            return
        token = self.extras.get("join_token")
        if not token:
            ui.fail("not joined: no join token")
            return
        self.ops.note(f"join the hub at {url} (POST /v1/satellites/join)")
        if self.opts.dry_run:
            return
        try:
            info = self.deps["join_hub"](url, token, cfg)
        except Exception as exc:  # noqa: BLE001 (JoinError and network errors)
            ui.fail(f"join failed: {exc}")
            self.problems.append(f"join failed: {exc}")
            return
        sid = info.get("satellite_id", "?")
        ui.ok(f"joined {url} as {sid}")
        self._log("joined hub", satellite_id=sid)
        self.extras["satellite_id"] = sid
        # The satellite service started before it had a hub token, and a running browser bridge still
        # holds the old browser token when the hub just handed out the fleet's: restart them to pick both up.
        restart = []
        if self.cur("satellite.enabled"):
            restart.append("satellite")
        if info.get("browser_token_changed") and self.cur("browser.enabled"):
            restart.append("browser-bridge")
        if restart and self.extras.get("services", True):
            try:
                self.deps["restart_services"](load_config(self.paths.home), restart, self.ops)
            except Exception as exc:  # noqa: BLE001
                ui.warn(f"couldn't restart {', '.join(restart)}: {exc}")
        ok, detail = self.deps["heartbeat"](url, Secrets(self.paths).get("ORBITAL_HUB_TOKEN"), sid)
        (ui.ok if ok else ui.fail)(f"hub check: {detail}")
        if not ok:
            self.problems.append(f"the hub doesn't list this machine yet ({detail})")

    def _install_skills(self, cfg) -> None:
        from orbital.wizard.skills import Installer
        ui = self.ui

        def confirm(home, conflicts) -> bool:
            if not ui.interactive or self.extras.get("skills_rules") != "auto":
                return False
            line_no, snippet = conflicts[0]
            ui.warn(f"{home.rules_file} already tells agents to report to another tracker (line {line_no}): {snippet}")
            return ui.ask_bool("Add the Orbital Agents always-on rule there too? (agents would report twice)", False)

        inst = Installer(home=self.opts.user_home, repo=self.repo, ops=self.ops,
                         agents=self.extras.get("skills_agents"), rules=self.extras.get("skills_rules", "auto"),
                         values=rule_values(cfg), confirm_conflict=confirm)
        for e in inst.install():
            prefix = f"{e.agent}: " if e.agent else ""
            ui.status(e.level, prefix + e.message)
            if e.level == "warn":
                self.notes.append(prefix + e.message)

    def _extension(self, cfg) -> None:
        ui = self.ui
        connected = _extension_connected(cfg)
        if connected:
            ui.ok("Browser Controller extension is connected")
            return
        v, why = self.preset("browser.open_extensions_page")
        open_it = ui.ask_bool("Open chrome://extensions and the extension folder now", True, preset=v, why=why) \
            if self.env and self.env.chrome else False
        if open_it:
            apps_mod.open_extension_page(self.ops, self.repo)
        ui.line("")
        ui.line(ui.style("  Load the Browser Controller extension (Chrome can't load it automatically):", "bold"))
        for i, s in enumerate(apps_mod.extension_steps(cfg, self.repo, opened=open_it), 1):
            ui.line(f"    {i}. {s}")
        self.notes.append("load the Browser Controller extension in Chrome (steps above), then `orbital browser status`")
        if ui.interactive and open_it and not self.opts.dry_run:
            ui.pause("Press Enter when the extension is loaded (or to skip)")

    # ------------------------------------------------------------------ 9. verify
    def step_verify(self) -> None:
        self.step("Checking that it works")
        ui = self.ui
        v, why = self.preset("verify.live_test")
        run = ui.ask_bool("Run the live checks now (orbital test --live)", True, preset=v, why=why)
        if not run:
            ui.info("skipped; run `orbital test --live` any time")
            return
        if self.opts.dry_run:
            self.ops.note("run: orbital test --live")
            return
        if not self.extras.get("services", True):
            ui.info("services weren't installed, so the live checks would only report them down; skipped")
            return
        cfg = load_config(self.paths.home)
        wait = float(self.a.get("verify.wait_seconds") or 25)
        ui.info(f"waiting up to {wait:.0f}s for the services to answer…")
        health = self.deps["wait_healthy"](cfg, wait)
        for name, (ok, detail) in health.items():
            (ui.ok if ok else ui.warn)(f"{name}: {detail}")
        rc = self.deps["live_test"]({"ORBITAL_LIVE_ALLOW_NO_EXTENSION": "1"})
        if rc != 0:
            self.problems.append("some live checks failed (see the table above); `orbital doctor` explains fixes")
        else:
            ui.ok("all live checks passed")

    # ------------------------------------------------------------------ summary
    def summary(self) -> None:
        ui = self.ui
        cfg = self._cfg() if self.opts.dry_run else load_config(self.paths.home)
        ui.line("")
        ui.banner("Summary" + (" (dry run: nothing was changed)" if self.opts.dry_run else ""))
        rows: list[tuple[str, str]] = [("Role", f"{self.role} ({cfg.machine_name})"), ("Settings", str(self.paths.config_file))]
        ts_dns = self.env.tailscale_dns if self.env else ""
        if self.role == "hub":
            port = cfg.get("hub.port")
            rows.append(("Hub (this machine)", f"http://127.0.0.1:{port}"))
            public = f"http://{ts_dns}:{port}" if ts_dns else ""
            if public:
                rows.append(("Hub over Tailscale", public))
            rows.append(("App sign-in", f"username {self.secret('ORBITAL_HUB_USERNAME') or '?'}; password in "
                                        f"{self.paths.secrets_file} (ORBITAL_HUB_PASSWORD)"))
            jt = self.secret("ORBITAL_JOIN_TOKEN")
            rows.append(("Join token", f"{mask(jt)}  (show it: orbital satellites invite)"))
            link_url = public or f"http://127.0.0.1:{port}"
            link = "orbital://connect?hub=" + urllib.parse.quote(link_url, safe="")
            rows.append(("Connect the app", link))
        else:
            rows.append(("Hub", str(cfg.get("hub.url"))))
            if self.extras.get("satellite_id"):
                rows.append(("Satellite id", str(self.extras["satellite_id"])))
        names = self.extras.get("service_names")
        if names:
            rows.append(("Services", ", ".join(names)))
        rows.append(("Logs", str(self.paths.logs)))
        ui.table(rows)
        if self.role == "hub":
            self._qr(next(v for k, v in rows if k == "Connect the app"))
        if self.generated:
            ui.line("")
            ui.info("generated: " + ", ".join(sorted(set(self.generated))) + f" (in {self.paths.secrets_file})")
        ui.line("")
        ui.line(ui.style("  Next steps", "bold"))
        steps = []
        if self.role == "hub":
            steps.append("On each other machine: clone this repo and run ./setup; it finds this hub. Paste the join token.")
            steps.append("iPhone/Mac app: open Orbital Agents, scan the QR code (or open the link) and sign in.")
        else:
            steps.append("On the hub: `orbital satellites` lists this machine; run an agent here from the app.")
        if self.cur("browser.enabled"):
            steps.append("Load the Browser Controller extension (steps above) if you haven't yet.")
        steps.append("Any time: `orbital doctor` (add --fix), `orbital services status`, `orbital test --live`.")
        for i, s in enumerate(steps, 1):
            ui.line(f"    {i}. {s}")
        if self.notes:
            ui.line("")
            ui.line(ui.style("  Notes", "bold"))
            for n in dict.fromkeys(self.notes):
                ui.warn(n)
        if self.problems:
            ui.line("")
            ui.line(ui.style("  Needs attention", "bold", "red"))
            for p in dict.fromkeys(self.problems):
                ui.fail(p)
        else:
            ui.line("")
            ui.ok("setup finished" + (" (dry run)" if self.opts.dry_run else ""))

    def _qr(self, link: str) -> None:
        exe = (self.env.qrencode if self.env else "") or shutil.which("qrencode")
        if not exe or not self.ui._isatty():
            if not exe:
                self.ui.hint("(install qrencode to show a QR code here: brew install qrencode)")
            return
        r: Result = self.deps["exec"]([exe, "-t", "ANSIUTF8", "-m", "1", link], timeout=10)
        if r.ok and r.stdout:
            self.ui.line("")
            for line in r.stdout.splitlines():
                self.ui.line("  " + line)


def rule_values(cfg) -> dict[str, str]:
    return {"machine": cfg.machine_name, "hub_url": cfg.hub_url() or "", "orbital_home": str(cfg.paths.home)}


def _extension_connected(cfg) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.get('browser.bridge_port')}/status", timeout=1.5) as r:  # noqa: S310
            data = json.loads(r.read(8192).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return False
    return _status_connected(data)


def _status_connected(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for key in ("extension_connected", "connected", "extension"):
        v = data.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, dict) and isinstance(v.get("connected"), bool):
            return v["connected"]
    return False


def _normalize_url(url: str, default_port: int) -> str:
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.port is None and parsed.hostname:
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        url = f"{parsed.scheme}://{host}:{default_port}{parsed.path}"
    return url


def _machine_slug(name: str) -> str:
    """A name that passes the machine-name check: unusual characters become '-'."""
    name = name.replace("’", "").replace("'", "")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-_")
    return name[:64]


def _check_modifiers(value: str) -> str | None:
    parts = [p for p in (value or "").lower().replace(" ", "").split("+") if p]
    allowed = {"ctrl", "control", "option", "alt", "shift", "cmd", "command"}
    if not parts or any(p not in allowed for p in parts):
        return "use ctrl, option, shift and cmd joined with +, e.g. ctrl+option+shift"
    if not any(p in ("ctrl", "control", "option", "alt", "cmd", "command") for p in parts):
        return "include at least one of ctrl, option or cmd"
    return None


def _local_user() -> str:
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001
        name = "admin"
    return re.sub(r"[^A-Za-z0-9._@-]", "", name) or "admin"


def _suggest_prefix() -> str:
    user = re.sub(r"[^a-z0-9]", "", _local_user().lower()) or "yourname"
    return f"com.{user}"


def run_wizard(opts: Options, **kw) -> int:
    return Wizard(opts, **kw).run()
