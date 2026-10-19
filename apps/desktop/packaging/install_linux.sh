#!/usr/bin/env bash
# Install the Orbital Agents desktop app for this Linux user:
#   - a virtualenv in apps/desktop/.venv (with system site-packages, so the distro's GTK/WebKit bindings work)
#   - ~/.local/bin/orbital-agents-desktop launcher
#   - an app-menu entry and the orbital:// link handler (pairing links open the app)
# Usage: packaging/install_linux.sh [--qt] [--no-url-handler]
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
QT=0
URL=1
for arg in "$@"; do
  case "$arg" in
    --qt) QT=1 ;;
    --no-url-handler) URL=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ is required" >&2; exit 1; }

if [ "$QT" = 0 ] && ! "$PY" -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null \
                 && ! "$PY" -c 'import gi; gi.require_version("WebKit2", "4.0")' 2>/dev/null; then
  echo "GTK WebKit bindings not found. Either install them, e.g."
  echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-webkit2-4.1"
  echo "  Fedora:        sudo dnf install python3-gobject webkit2gtk4.1"
  echo "  Arch:          sudo pacman -S python-gobject webkit2gtk-4.1"
  echo "or re-run with --qt to use Qt from pip instead."
  exit 1
fi

"$PY" -m venv --system-site-packages "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip >/dev/null
"$HERE/.venv/bin/pip" install -r "$HERE/requirements.txt"
if [ "$QT" = 1 ]; then
  "$HERE/.venv/bin/pip" install "pywebview[qt]"
fi

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
cat > "$BIN/orbital-agents-desktop" <<EOF
#!/usr/bin/env bash
exec "$HERE/.venv/bin/python" "$HERE/run.py" "\$@"
EOF
chmod +x "$BIN/orbital-agents-desktop"

ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
APPS="$HOME/.local/share/applications"
mkdir -p "$ICONS" "$APPS"
cp "$HERE/packaging/icon.svg" "$ICONS/orbital-agents.svg"
sed -e "s|^Exec=.*|Exec=$BIN/orbital-agents-desktop %u|" \
    -e "s|^Icon=.*|Icon=$ICONS/orbital-agents.svg|" \
    "$HERE/packaging/orbital-agents.desktop" > "$APPS/orbital-agents.desktop"
if [ "$URL" = 0 ]; then
  sed -i '/^MimeType=/d' "$APPS/orbital-agents.desktop"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
if [ "$URL" = 1 ] && command -v xdg-mime >/dev/null; then
  xdg-mime default orbital-agents.desktop x-scheme-handler/orbital
fi

echo "Installed. Start it from the app menu (Orbital Agents) or run: orbital-agents-desktop"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH." ;; esac
