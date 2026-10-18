#!/bin/bash
# Builds AppIcon.icns (16 to 1024 px, @1x and @2x) from the project icon.
#   scripts/make-icon.sh [OUT.icns]      default: build/AppIcon.icns
# Source: docs/assets/icon-macos-1024.png at the repo root (override with ICON_SRC=...).
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${ICON_SRC:-../../docs/assets/icon-macos-1024.png}"
OUT="${1:-build/AppIcon.icns}"
[ -f "$SRC" ] || { echo "make-icon: $SRC not found" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SET="$TMP/AppIcon.iconset"
mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -s format png -z "$s" "$s" "$SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -s format png -z "$((s * 2))" "$((s * 2))" "$SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
mkdir -p "$(dirname "$OUT")"
iconutil -c icns "$SET" -o "$OUT"
echo "Icon: $OUT"
