# PyInstaller spec: a self-contained Orbital Agents desktop app (no Python needed on the target).
# Build on the OS you are targeting (PyInstaller doesn't cross-compile), from apps/desktop:
#   pip install -r requirements.txt pyinstaller
#   pyinstaller packaging/orbital_desktop.spec --noconfirm
# Output: dist/OrbitalAgents/ (Windows: OrbitalAgents.exe; Linux: OrbitalAgents; macOS: OrbitalAgents.app)
# -*- mode: python -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
hidden = collect_submodules("keyring.backends") + collect_submodules("websockets")
if sys.platform == "win32":
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]
elif sys.platform == "darwin":
    hidden += ["webview.platforms.cocoa"]
else:
    hidden += ["webview.platforms.gtk", "webview.platforms.qt"]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "orbital_desktop" / "ui"), "orbital_desktop/ui")],
    hiddenimports=hidden,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
icon = ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.icns")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="OrbitalAgents",
    console=False,
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="OrbitalAgents")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="OrbitalAgents.app", bundle_identifier="net.orbitalagents.desktop",
                 icon=str(icon) if icon.exists() else None)
