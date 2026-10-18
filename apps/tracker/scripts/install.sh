#!/bin/bash
# Installs the tracker app and puts the orbital-track CLI on PATH.
#
#   scripts/install.sh [--dest DIR] [--bin-dir DIR] [--no-build] [--open]
#
#   --dest DIR      where the .app goes (default /Applications)
#   --bin-dir DIR   where the orbital-track link goes (default ~/.local/bin)
#   --no-build      install the existing build/ copy instead of running scripts/build.sh
#   --open          start the tracker afterwards (it is restarted anyway if it was running)
#
# Starting at login is the services installer's job (`orbital services install`, LaunchAgent
# io.orbitalagents.tracker running <app>/Contents/MacOS/OrbitalTracker).
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="/Applications"
BIN_DIR="$HOME/.local/bin"
BUILD=1
OPEN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:?--dest needs a folder}"; shift 2 ;;
    --dest=*) DEST="${1#*=}"; shift ;;
    --bin-dir) BIN_DIR="${2:?--bin-dir needs a folder}"; shift 2 ;;
    --bin-dir=*) BIN_DIR="${1#*=}"; shift ;;
    --no-build) BUILD=0; shift ;;
    --open) OPEN=1; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install: unknown option $1" >&2; exit 2 ;;
  esac
done

APP_NAME="Orbital Agents - Agent Satellite Tracker.app"
LABEL="io.orbitalagents.tracker"
if [ "$BUILD" = 1 ]; then scripts/build.sh; fi
[ -d "build/$APP_NAME" ] || { echo "install: build/$APP_NAME is missing; run scripts/build.sh" >&2; exit 1; }

TARGET="$DEST/$APP_NAME"
EXE="$TARGET/Contents/MacOS/OrbitalTracker"
WAS_RUNNING=0
if pgrep -f "$EXE" >/dev/null 2>&1; then
  WAS_RUNNING=1
  pkill -TERM -f "$EXE" || true          # the tracker saves and exits 0 on SIGTERM
  for _ in $(seq 1 50); do pgrep -f "$EXE" >/dev/null 2>&1 || break; sleep 0.1; done
fi

mkdir -p "$DEST"
rm -rf "$TARGET.new"
cp -R "build/$APP_NAME" "$TARGET.new"
rm -rf "$TARGET"
mv "$TARGET.new" "$TARGET"
echo "App: $TARGET"

mkdir -p "$BIN_DIR"
ln -sf "$TARGET/Contents/MacOS/orbital-track" "$BIN_DIR/orbital-track"
echo "CLI: $BIN_DIR/orbital-track -> $TARGET/Contents/MacOS/orbital-track"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "  (add $BIN_DIR to PATH so agents find orbital-track)" ;; esac

if [ "$OPEN" = 1 ] || [ "$WAS_RUNNING" = 1 ]; then
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
  else
    open "$TARGET"
  fi
  RESOLVED="${ORBITAL_HOME:-$HOME/.orbital-agents}/config.resolved.json"
  HOTKEYS="$(plutil -extract tracker.hotkeys raw -o - "$RESOLVED" 2>/dev/null || echo true)"
  MODS="$(plutil -extract tracker.hotkey_modifiers raw -o - "$RESOLVED" 2>/dev/null || echo ctrl+option)"
  if [ "$HOTKEYS" = "false" ]; then
    echo "Started. Global shortcuts are off (tracker.hotkeys = false)."
  else
    echo "Started. Shortcuts: $MODS+T show/hide · $MODS+N type an item · $MODS+M mic (when voice is on)"
  fi
fi
