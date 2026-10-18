#!/usr/bin/env bash
# Build Orbital Agents without signing, for the iOS Simulator and/or Mac Catalyst. Anyone can run this:
# no Apple account, Team ID or device needed.
#
#   apps/orbital-app/scripts/build.sh            iOS Simulator (Debug)
#   apps/orbital-app/scripts/build.sh --mac      Mac Catalyst (Debug)
#   apps/orbital-app/scripts/build.sh --all      both
#   apps/orbital-app/scripts/build.sh --release  Release instead of Debug (no preview mode in Release)
#
# The Xcode project is generated first (generate_xcodeproj.py). Build output goes to
# apps/orbital-app/.derivedData (git-ignored).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DERIVED="${ORBITAL_DERIVED_DATA:-$APP_DIR/.derivedData}"
CONFIGURATION="Debug"
TARGETS=("ios")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mac) TARGETS=("mac"); shift ;;
    --all) TARGETS=("ios" "mac"); shift ;;
    --release) CONFIGURATION="Release"; shift ;;
    --help|-h) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

python3 "$APP_DIR/generate_xcodeproj.py"

for target in "${TARGETS[@]}"; do
  if [[ "$target" == "ios" ]]; then
    destination="generic/platform=iOS Simulator"
    products="$DERIVED/Build/Products/${CONFIGURATION}-iphonesimulator"
  else
    destination="generic/platform=macOS,variant=Mac Catalyst"
    products="$DERIVED/Build/Products/${CONFIGURATION}-maccatalyst"
  fi
  echo "==> xcodebuild $CONFIGURATION ($destination), unsigned"
  xcodebuild \
    -project "$APP_DIR/OrbitalAgents.xcodeproj" \
    -scheme OrbitalAgents \
    -configuration "$CONFIGURATION" \
    -destination "$destination" \
    -derivedDataPath "$DERIVED" \
    CODE_SIGNING_ALLOWED=NO \
    build | grep -E "error:|warning: .*OrbitalAgents/|BUILD (SUCCEEDED|FAILED)" || true
  if [[ ! -d "$products/OrbitalAgents.app" ]]; then
    echo "build failed: no $products/OrbitalAgents.app (run xcodebuild without the filter above for the full log)" >&2
    exit 1
  fi
  echo "==> built $products/OrbitalAgents.app"
done
