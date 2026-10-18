#!/usr/bin/env bash
# Run the unit tests (OrbitalAgentsTests) on an iOS Simulator, unsigned.
#
#   apps/orbital-app/scripts/test.sh                        first available iPhone simulator
#   apps/orbital-app/scripts/test.sh --device "iPhone 16"   a simulator by name or UDID
#   apps/orbital-app/scripts/test.sh --mac                  on this Mac, as Mac Catalyst
#
# A simulator this script boots is shut down again afterwards; one that was already running is left alone.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DERIVED="${ORBITAL_DERIVED_DATA:-$APP_DIR/.derivedData}"
DEVICE_QUERY="${ORBITAL_SIMULATOR:-}"
MAC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device|-d) DEVICE_QUERY="${2:-}"; shift 2 ;;
    --mac) MAC=1; shift ;;
    --help|-h) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

python3 "$APP_DIR/generate_xcodeproj.py"

if [[ "$MAC" == "1" ]]; then
  exec xcodebuild -project "$APP_DIR/OrbitalAgents.xcodeproj" -scheme OrbitalAgents \
    -destination 'platform=macOS,variant=Mac Catalyst' -derivedDataPath "$DERIVED" \
    CODE_SIGNING_ALLOWED=NO test
fi

read -r UDID STATE < <("$APP_DIR/scripts/simulator.py" "$DEVICE_QUERY")
echo "==> simulator $UDID ($STATE)"
cleanup() {
  if [[ "$STATE" != "Booted" ]]; then
    xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

xcodebuild -project "$APP_DIR/OrbitalAgents.xcodeproj" -scheme OrbitalAgents \
  -destination "platform=iOS Simulator,id=$UDID" -derivedDataPath "$DERIVED" \
  CODE_SIGNING_ALLOWED=NO test
