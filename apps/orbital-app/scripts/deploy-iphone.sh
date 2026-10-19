#!/usr/bin/env bash
# Build Orbital Agents signed with your Apple Team ID and install it on a connected iPhone or iPad.
#
#   apps/orbital-app/scripts/deploy-iphone.sh
#   apps/orbital-app/scripts/deploy-iphone.sh --device "My iPhone"   by name or identifier
#   apps/orbital-app/scripts/deploy-iphone.sh --no-launch
#   apps/orbital-app/scripts/deploy-iphone.sh --no-install            build only
#   apps/orbital-app/scripts/deploy-iphone.sh --release
#
# Team ID, bundle prefix and app name come from ~/.orbital-agents/config.resolved.json
# (apple.team_id, apple.bundle_prefix, apple.app_name; `orbital setup` writes it) or from
# ORBITAL_TEAM_ID / ORBITAL_BUNDLE_PREFIX / ORBITAL_APP_NAME. The device is --device, else
# apple.ios_device (or ORBITAL_IOS_DEVICE), else the first connected iPhone.
# A free Apple account works: Xcode makes a 7-day profile. The phone must be in Developer Mode.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GEN="$APP_DIR/generate_xcodeproj.py"
DERIVED="${ORBITAL_DERIVED_DATA:-$APP_DIR/.derivedData-device}"
CONFIGURATION="Debug"
DEVICE_QUERY=""
LAUNCH=1
INSTALL=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device|-d) DEVICE_QUERY="${2:-}"; shift 2 ;;
    --no-launch) LAUNCH=0; shift ;;
    --no-install) INSTALL=0; LAUNCH=0; shift ;;
    --release) CONFIGURATION="Release"; shift ;;
    --help|-h) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$(python3 "$GEN" --get has_team)" ]]; then
  cat >&2 <<'MSG'
No Apple Team ID is set, so the app can't be signed for a device.
Find yours in Xcode → Settings → Accounts (10 letters and digits), then either
  run `orbital setup` and enter it, or
  ORBITAL_TEAM_ID=<your team id> ORBITAL_BUNDLE_PREFIX=com.yourname apps/orbital-app/scripts/deploy-iphone.sh
For a simulator build without signing: apps/orbital-app/scripts/build.sh
MSG
  exit 1
fi
BUNDLE_ID="$(python3 "$GEN" --get bundle_id)"
APP_NAME="$(python3 "$GEN" --get app_name)"
DEVICE_QUERY="${DEVICE_QUERY:-$(python3 "$GEN" --get ios_device)}"

python3 "$GEN"

echo "==> xcodebuild $CONFIGURATION for iOS ($BUNDLE_ID)"
xcodebuild \
  -project "$APP_DIR/OrbitalAgents.xcodeproj" \
  -scheme OrbitalAgents \
  -configuration "$CONFIGURATION" \
  -destination 'generic/platform=iOS' \
  -derivedDataPath "$DERIVED" \
  -allowProvisioningUpdates \
  build

APP="$DERIVED/Build/Products/${CONFIGURATION}-iphoneos/OrbitalAgents.app"
if [[ ! -d "$APP" ]]; then
  echo "build did not produce $APP" >&2
  exit 1
fi
echo "==> built $APP"
[[ "$INSTALL" == "1" ]] || exit 0

TMP_JSON="$(mktemp)"
trap 'rm -f "$TMP_JSON"' EXIT
xcrun devicectl list devices --json-output "$TMP_JSON" >/dev/null
DEVICE_ID="$(python3 - "$TMP_JSON" "$DEVICE_QUERY" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
query = sys.argv[2].strip().lower()
devices = data.get("result", {}).get("devices", data.get("devices", []))
rows = []
for d in devices:
    hw = d.get("hardwareProperties") or {}
    info = d.get("deviceProperties") or {}
    conn = d.get("connectionProperties") or {}
    name = info.get("name") or d.get("name") or ""
    ident = d.get("identifier") or hw.get("udid") or ""
    platform = (hw.get("platform") or "").lower()
    kind = (hw.get("deviceType") or hw.get("marketingName") or "").lower()
    if not ident or (platform and platform != "ios"):
        continue
    paired = conn.get("pairingState") == "paired"
    rows.append({"name": name, "id": ident, "udid": hw.get("udid") or "", "kind": kind, "paired": paired,
                 "connected": conn.get("tunnelState") == "connected" or bool(conn.get("transportType"))})
if query:
    rows = [r for r in rows if query in r["name"].lower() or query in (r["id"].lower(), r["udid"].lower())]
    if not rows:
        sys.exit(f"no paired device matches {query!r} (xcrun devicectl list devices)")
if not rows:
    sys.exit("no iPhone or iPad is paired with this Mac. Connect it by cable once and trust this Mac.")
rows.sort(key=lambda r: (not r["connected"], "iphone" not in r["kind"], r["name"].lower()))
print(rows[0]["id"])
print(f"device: {rows[0]['name']}", file=sys.stderr)
PY
)"

echo "==> install on $DEVICE_ID"
xcrun devicectl device install app --device "$DEVICE_ID" "$APP"
if [[ "$LAUNCH" == "1" ]]; then
  # On the hub, sign the app in by itself: a one-time orbital://connect link opened as it launches.
  ORBITAL_BIN="$(command -v orbital || true)"
  [[ -z "$ORBITAL_BIN" && -x "$APP_DIR/../../.venv/bin/orbital" ]] && ORBITAL_BIN="$APP_DIR/../../.venv/bin/orbital"
  PAIR_URL=""
  if [[ -n "$ORBITAL_BIN" ]]; then
    PAIR_URL="$("$ORBITAL_BIN" pair --link 2>/dev/null || true)"
  fi
  if [[ "$PAIR_URL" == orbital://* ]]; then
    echo "==> launching and signing the app in to the hub"
    xcrun devicectl device process launch --device "$DEVICE_ID" --terminate-existing --payload-url "$PAIR_URL" "$BUNDLE_ID" >/dev/null || true
  else
    xcrun devicectl device process launch --device "$DEVICE_ID" "$BUNDLE_ID" || true
  fi
fi
echo "==> installed $APP_NAME ($BUNDLE_ID)"
