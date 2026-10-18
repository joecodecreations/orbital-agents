#!/usr/bin/env bash
# Run the app in preview mode (the sample data in apps/orbital-app/Fixtures/preview, no hub) on an
# iPhone simulator and save screenshots of the main screens to docs/assets/screenshots/.
#
#   apps/orbital-app/scripts/screenshots.sh
#   apps/orbital-app/scripts/screenshots.sh --device "iPhone 16 Pro"
#   apps/orbital-app/scripts/screenshots.sh --out /tmp/shots
#
# A simulator this script boots is shut down when it finishes; one that was already running is left
# running (the app is only installed and launched on it).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$APP_DIR/../.." && pwd)"
DERIVED="${ORBITAL_DERIVED_DATA:-$APP_DIR/.derivedData}"
OUT="$REPO/docs/assets/screenshots"
DEVICE_QUERY="${ORBITAL_SIMULATOR:-}"
FIXTURES="$APP_DIR/Fixtures/preview"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device|-d) DEVICE_QUERY="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --help|-h) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

"$APP_DIR/scripts/build.sh"
APP="$DERIVED/Build/Products/Debug-iphonesimulator/OrbitalAgents.app"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Info.plist")"

read -r UDID STATE < <("$APP_DIR/scripts/simulator.py" "$DEVICE_QUERY")
cleanup() {
  xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1 || true
  if [[ "$STATE" != "Booted" ]]; then
    xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true
    echo "==> shut the simulator down"
  else
    xcrun simctl status_bar "$UDID" clear >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ "$STATE" != "Booted" ]]; then
  echo "==> booting $UDID"
  xcrun simctl boot "$UDID"
fi
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl ui "$UDID" appearance dark >/dev/null 2>&1 || true
xcrun simctl status_bar "$UDID" override --time "9:41" --dataNetwork wifi --wifiBars 3 --cellularBars 4 \
  --batteryState charged --batteryLevel 100 >/dev/null 2>&1 || true
xcrun simctl install "$UDID" "$APP"
mkdir -p "$OUT"

shot() {
  local name="$1" route="$2" wait="${3:-4}"
  xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1 || true
  SIMCTL_CHILD_ORBITAL_PREVIEW_DIR="$FIXTURES" SIMCTL_CHILD_ORBITAL_PREVIEW_ROUTE="$route" \
    xcrun simctl launch "$UDID" "$BUNDLE_ID" >/dev/null
  sleep "$wait"
  xcrun simctl io "$UDID" screenshot --type=png "$OUT/app-$name.png" >/dev/null 2>&1
  # 750 px wide is plenty for docs and keeps the repository small.
  sips --resampleWidth 750 "$OUT/app-$name.png" >/dev/null 2>&1 || true
  # Drop the alpha channel and recompress when Pillow is installed (about 40% smaller).
  python3 -c 'import sys; from PIL import Image; Image.open(sys.argv[1]).convert("RGB").save(sys.argv[1], optimize=True)' \
    "$OUT/app-$name.png" >/dev/null 2>&1 || true
  echo "==> $OUT/app-$name.png ($route)"
}

shot home "orbital://home" 6
shot section-charts "orbital://open?section=website-uptime"
shot agents "orbital://agents"
shot tracker "orbital://tracker" 8
shot alerts "orbital://alerts"
