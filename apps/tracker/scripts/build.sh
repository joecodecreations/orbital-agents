#!/bin/bash
# Builds "build/Orbital Agents - Agent Satellite Tracker.app" (release) with the orbital-track CLI and the
# optional speech sidecar inside, and signs it: an "Apple Development" identity when the keychain has
# one (so the microphone permission survives rebuilds), otherwise ad-hoc.
#
#   scripts/build.sh
#
# Bundle id: <apple.bundle_prefix>.orbitalagents.tracker, read from $ORBITAL_HOME/config.resolved.json
# (default ~/.orbital-agents; com.example when there is none). Overrides: ORBITAL_TRACKER_BUNDLE_ID,
# CODESIGN_IDENTITY ("-" = ad-hoc).
set -euo pipefail
cd "$(dirname "$0")/.."
TRACKER="$PWD"
APP_NAME="Orbital Agents - Agent Satellite Tracker"
BUILD_DIR="$TRACKER/build"
APP="$BUILD_DIR/$APP_NAME.app"
HOME_DIR="${ORBITAL_HOME:-$HOME/.orbital-agents}"
RESOLVED="$HOME_DIR/config.resolved.json"

PREFIX=""
if [ -f "$RESOLVED" ]; then
  PREFIX="$(plutil -extract apple.bundle_prefix raw -o - "$RESOLVED" 2>/dev/null || true)"
fi
PREFIX="${PREFIX:-com.example}"
BUNDLE_ID="${ORBITAL_TRACKER_BUNDLE_ID:-$PREFIX.orbitalagents.tracker}"
if ! [[ "$BUNDLE_ID" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "build: bad bundle id '$BUNDLE_ID' (letters, digits, '.' and '-' only)" >&2
  exit 1
fi
VERSION="$(sed -nE 's/.*static let version = "([^"]+)".*/\1/p' Sources/OrbitalTrackerCore/Config.swift | head -1)"
BUILD_NUMBER="$(date -u +%Y%m%d%H%M)"

swift build -c release --product OrbitalTracker
swift build -c release --product orbital-track
BIN="$(swift build -c release --show-bin-path)"

ICON="$BUILD_DIR/AppIcon.icns"
SRC_ICON="../../docs/assets/icon-macos-1024.png"
if [ ! -f "$ICON" ] || [ "$SRC_ICON" -nt "$ICON" ]; then
  scripts/make-icon.sh "$ICON" >/dev/null
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN/OrbitalTracker" "$APP/Contents/MacOS/OrbitalTracker"
cp "$BIN/orbital-track" "$APP/Contents/MacOS/orbital-track"
sed -e "s|__BUNDLE_ID__|$BUNDLE_ID|g" -e "s|__VERSION__|$VERSION|g" -e "s|__BUILD__|$BUILD_NUMBER|g" \
  Resources/Info.plist.in > "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null
cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"
cp stt/crisper_server.py "$APP/Contents/Resources/crisper_server.py"
printf 'APPL????' > "$APP/Contents/PkgInfo"

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  IDENTITY="$CODESIGN_IDENTITY"
else
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | grep -m1 '"Apple Development' | sed -E 's/.*"(.*)"/\1/' || true)"
fi
IDENTITY="${IDENTITY:--}"
sign() {
  codesign --force --sign "$IDENTITY" --timestamp=none --identifier "$2" "$1" 2>/dev/null \
    || codesign --force --sign - --identifier "$2" "$1"
}
sign "$APP/Contents/MacOS/orbital-track" "$BUNDLE_ID.cli"
sign "$APP" "$BUNDLE_ID"
if [ "$IDENTITY" = "-" ]; then SIGNED="ad-hoc"; else SIGNED="Apple Development identity"; fi
echo "Built: $APP"
echo "  bundle id $BUNDLE_ID, version $VERSION ($BUILD_NUMBER), signed: $SIGNED"
