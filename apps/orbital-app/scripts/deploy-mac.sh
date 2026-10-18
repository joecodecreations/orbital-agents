#!/usr/bin/env bash
# Build the Mac version of Orbital Agents (the same target, as Mac Catalyst) and install it as
# /Applications/<app name>.app.
#
#   apps/orbital-app/scripts/deploy-mac.sh               build, install, reopen it in the background if it was running
#   apps/orbital-app/scripts/deploy-mac.sh --open        build, install, and open it
#   apps/orbital-app/scripts/deploy-mac.sh --no-install  build only
#   apps/orbital-app/scripts/deploy-mac.sh --debug       a Debug build instead of Release
#
# Signing uses the same Team ID and bundle id as the iPhone app (see deploy-iphone.sh). Without a
# Team ID it is signed to run locally only. ORBITAL_MAC_APP_DIR installs somewhere other than /Applications.
# An app already at that path with a different bundle id is never replaced.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GEN="$APP_DIR/generate_xcodeproj.py"
DERIVED="${ORBITAL_DERIVED_DATA:-$APP_DIR/.derivedData-mac}"
CONFIGURATION="Release"
DEST_DIR="${ORBITAL_MAC_APP_DIR:-/Applications}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
INSTALL=1
OPEN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --open) OPEN=1; shift ;;
    --no-install) INSTALL=0; shift ;;
    --debug) CONFIGURATION="Debug"; shift ;;
    --help|-h) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

BUNDLE_ID="$(python3 "$GEN" --get bundle_id)"
APP_NAME="$(python3 "$GEN" --get app_name)"
SIGNING=(-allowProvisioningUpdates)
if [[ -z "$(python3 "$GEN" --get has_team)" ]]; then
  echo "note: no Apple Team ID set; signing to run on this Mac only (push notifications need a Team ID)." >&2
  SIGNING=(CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= PROVISIONING_PROFILE_SPECIFIER=)
fi

python3 "$GEN"

# The build number is the build time, so About shows which build is installed.
BUILD_STAMP="$(date +%Y%m%d.%H%M)"

echo "==> xcodebuild $CONFIGURATION (Mac Catalyst, $BUNDLE_ID)"
xcodebuild \
  -project "$APP_DIR/OrbitalAgents.xcodeproj" \
  -scheme OrbitalAgents \
  -configuration "$CONFIGURATION" \
  -destination 'generic/platform=macOS,variant=Mac Catalyst' \
  -derivedDataPath "$DERIVED" \
  "${SIGNING[@]}" \
  CURRENT_PROJECT_VERSION="$BUILD_STAMP" \
  build

APP="$DERIVED/Build/Products/${CONFIGURATION}-maccatalyst/OrbitalAgents.app"
if [[ ! -d "$APP" ]]; then
  echo "build did not produce $APP" >&2
  exit 1
fi
echo "==> built $APP"
[[ "$INSTALL" == "1" ]] || exit 0

DEST="$DEST_DIR/${APP_NAME}.app"
if [[ -d "$DEST" ]]; then
  existing="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DEST/Contents/Info.plist" 2>/dev/null || true)"
  if [[ -n "$existing" && "$existing" != "$BUNDLE_ID" ]]; then
    echo "$DEST is a different app ($existing), so it was left alone. Set apple.app_name to another name, or ORBITAL_MAC_APP_DIR." >&2
    exit 1
  fi
fi

WAS_RUNNING=0
BINARY="$DEST/Contents/MacOS/OrbitalAgents"
if pgrep -f "^$BINARY" >/dev/null 2>&1; then
  WAS_RUNNING=1
  pkill -TERM -f "^$BINARY" || true
  for _ in $(seq 1 50); do
    pgrep -f "^$BINARY" >/dev/null 2>&1 || break
    sleep 0.1
  done
  pkill -KILL -f "^$BINARY" 2>/dev/null || true
fi

echo "==> install $DEST"
rm -rf "$DEST"
ditto "$APP" "$DEST"
# orbital:// links and Spotlight should open the installed copy, not the build folder.
"$LSREGISTER" -u "$APP" >/dev/null 2>&1 || true
"$LSREGISTER" -f "$DEST" >/dev/null 2>&1 || true

if [[ "$OPEN" == "1" ]]; then
  open "$DEST"
elif [[ "$WAS_RUNNING" == "1" ]]; then
  # Reopen in the background so an install doesn't take focus.
  open -g "$DEST"
fi
echo "==> installed $APP_NAME for Mac, build $BUILD_STAMP"
