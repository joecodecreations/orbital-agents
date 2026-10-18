"""Build and install the apps: the tracker (macOS), the Orbital Agents Mac app and iPhone app, and
the steps to load the browser extension (Chrome doesn't allow loading it automatically).

The build scripts live with each app; this module only calls them with the right environment:

    apps/tracker/scripts/build.sh, install.sh
    apps/orbital-app/scripts/deploy-mac.sh
    apps/orbital-app/scripts/deploy-iphone.sh
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from orbital.core.config import Config
from orbital.core.paths import REPO_ROOT
from orbital.services.ops import Ops

TRACKER_BUILD = Path("apps/tracker/scripts/build.sh")
TRACKER_INSTALL = Path("apps/tracker/scripts/install.sh")
MAC_DEPLOY = Path("apps/orbital-app/scripts/deploy-mac.sh")
IPHONE_DEPLOY = Path("apps/orbital-app/scripts/deploy-iphone.sh")
EXTENSION_DIR = Path("browser-extension")


@dataclass
class BuildResult:
    name: str
    ok: bool
    skipped: bool = False
    message: str = ""
    log: str = ""


def build_env(cfg: Config, device: str = "") -> dict[str, str]:
    """Environment for the build scripts. They also read $ORBITAL_HOME/config.resolved.json."""
    env = {
        "ORBITAL_HOME": str(cfg.paths.home),
        "ORBITAL_REPO": str(REPO_ROOT),
        "ORBITAL_APP_NAME": str(cfg.get("apple.app_name") or "Orbital Agents"),
        "ORBITAL_BUNDLE_PREFIX": str(cfg.get("apple.bundle_prefix") or "com.example"),
    }
    team = str(cfg.get("apple.team_id") or "")
    if team:
        env["ORBITAL_TEAM_ID"] = team
        env["DEVELOPMENT_TEAM"] = team
    dev = device or str(cfg.get("apple.ios_device") or "")
    if dev:
        env["ORBITAL_IOS_DEVICE"] = dev
    return env


class _Progress:
    """One updating status line on a terminal; nothing otherwise (the log file has everything)."""

    def __init__(self, ui, label: str):
        self.ui, self.label = ui, label
        self.tty = ui._isatty()
        self.width = shutil.get_terminal_size((100, 20)).columns - 8

    def __call__(self, line: str) -> None:
        if not self.tty or not line.strip():
            return
        text = f"{self.label}: {line.strip()}"[: self.width]
        self.ui.out.write("\r\033[2K    " + self.ui.style(text, "dim"))
        self.ui.out.flush()

    def done(self) -> None:
        if self.tty:
            self.ui.out.write("\r\033[2K")
            self.ui.out.flush()


def run_script(ui, ops: Ops, cfg: Config, name: str, script: Path, args: list[str] | None = None,
               env: dict[str, str] | None = None, repo: Path = REPO_ROOT) -> BuildResult:
    path = repo / script
    if not path.is_file():
        return BuildResult(name, False, skipped=True, message=f"{script} not found in this checkout")
    log = cfg.paths.logs / f"setup-{name.replace(' ', '-')}.log"
    ui.info(f"{name}: running {script}" + ("" if ops.dry_run else f"  (log: {log})"))
    progress = _Progress(ui, name)
    r = ops.run_logged(["bash", str(path), *(args or [])], log, cwd=repo, env=env, progress=progress)
    progress.done()
    if r.ok:
        return BuildResult(name, True, message="done", log=str(log))
    tail = "\n".join(r.stdout.splitlines()[-15:])
    return BuildResult(name, False, message=f"{script} failed (exit {r.returncode})", log=str(log) + "\n" + tail)


def build_tracker(ui, ops: Ops, cfg: Config, repo: Path = REPO_ROOT) -> BuildResult:
    if sys.platform != "darwin":
        return BuildResult("tracker", True, skipped=True, message="macOS only")
    env = build_env(cfg)
    r = run_script(ui, ops, cfg, "tracker build", TRACKER_BUILD, env=env, repo=repo)
    if not r.ok:
        return r
    return run_script(ui, ops, cfg, "tracker install", TRACKER_INSTALL, args=["--no-build"], env=env, repo=repo)


def deploy_mac_app(ui, ops: Ops, cfg: Config, repo: Path = REPO_ROOT) -> BuildResult:
    if sys.platform != "darwin":
        return BuildResult("mac app", True, skipped=True, message="macOS only")
    return run_script(ui, ops, cfg, "mac app", MAC_DEPLOY, env=build_env(cfg), repo=repo)


def deploy_iphone_app(ui, ops: Ops, cfg: Config, device: str, repo: Path = REPO_ROOT) -> BuildResult:
    if sys.platform != "darwin":
        return BuildResult("iphone app", True, skipped=True, message="macOS only")
    args = ["--device", device] if device else []
    return run_script(ui, ops, cfg, "iphone app", IPHONE_DEPLOY, args=args, env=build_env(cfg, device), repo=repo)


def extension_steps(cfg: Config, repo: Path = REPO_ROOT, opened: bool = True) -> list[str]:
    """The manual steps to load the extension. `opened`: setup just opened chrome://extensions and the folder."""
    folder = repo / EXTENSION_DIR
    port = int(cfg.get("browser.bridge_port", 7703))
    mac = sys.platform == "darwin"
    manager = "Finder" if mac else "your file manager"
    # open_extension_page opens chrome://extensions only on macOS, and the folder where there is a desktop.
    page_opened = opened and mac
    folder_shown = opened and (mac or bool(shutil.which("xdg-open") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))))
    steps = [
        "In Chrome, go to chrome://extensions" + (" (setup opened it for you)." if page_opened else "."),
        "Turn on \"Developer mode\" (switch at the top right).",
        "Click \"Load unpacked\" (top left).",
        f"Choose this folder{f' (setup showed it in {manager})' if folder_shown else ''}: {folder}",
        "Click the puzzle icon in the toolbar and pin \"Orbital Agents Browser Controller\".",
    ]
    if port != 7703:
        steps.append(f"Open the extension's options and set the bridge port to {port}.")
    steps.append("Check: `orbital browser status` shows the extension as connected.")
    return steps


def open_extension_page(ops: Ops, repo: Path = REPO_ROOT) -> None:
    folder = repo / EXTENSION_DIR
    if sys.platform == "darwin":
        ops.run(["open", "-a", "Google Chrome", "chrome://extensions"])
        target = folder / "manifest.json"
        ops.run(["open", "-R", str(target if target.exists() else folder)])
        return
    # Linux: starting Chrome from here would block until it quits, so only the folder is opened.
    if shutil.which("xdg-open") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        ops.run(["xdg-open", str(folder)])
