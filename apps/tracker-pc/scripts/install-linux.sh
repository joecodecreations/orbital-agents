#!/bin/sh
# Install the Agent Satellite Tracker on Linux (also works on macOS for testing, without autostart).
#
#   scripts/install-linux.sh                         venv + deps + orbital-track on PATH + XDG autostart
#   scripts/install-linux.sh --autostart systemd     a systemd --user service instead (starts with the session)
#   scripts/install-linux.sh --autostart headless    systemd --user, no window (servers, no desktop)
#   scripts/install-linux.sh --autostart none        no autostart
#   scripts/install-linux.sh --uninstall             remove autostart, the PATH link and the install marker
#
# Env: PYTHON=/usr/bin/python3 to pick the interpreter; ORBITAL_HOME for a non-default home.
set -eu
DIR=$(cd "$(dirname "$0")/.." && pwd)
MODE=xdg
UNINSTALL=0
while [ $# -gt 0 ]; do
  case $1 in
    --autostart) MODE=$2; shift 2 ;;
    --autostart=*) MODE=${1#*=}; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
BIN="$HOME/.local/bin"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
DESKTOP="$CONF/autostart/orbital-agents-tracker.desktop"
UNIT="$CONF/systemd/user/orbital-agents-tracker.service"

if [ "$UNINSTALL" = 1 ]; then
  if command -v systemctl >/dev/null 2>&1 && [ -f "$UNIT" ]; then
    systemctl --user disable --now orbital-agents-tracker.service || true
    rm -f "$UNIT"; systemctl --user daemon-reload || true
  fi
  rm -f "$DESKTOP"
  [ -L "$BIN/orbital-track" ] && [ "$(readlink "$BIN/orbital-track")" = "$DIR/orbital-track" ] && rm -f "$BIN/orbital-track"
  [ -x "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" "$DIR/tracker.pyw" --remove-marker || true
  echo "Removed the tracker's autostart, PATH link and install marker (data in ~/.orbital-agents/data/tracker is kept)."
  exit 0
fi

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || { echo "python3 not found; install Python 3.10+ first" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ is required" >&2; exit 1; }

# --system-site-packages: GTK's python3-gi comes from the distribution, not pip.
if [ ! -x "$DIR/.venv/bin/python" ]; then
  "$PY" -m venv --system-site-packages "$DIR/.venv"
fi
VPY="$DIR/.venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r "$DIR/requirements.txt"
if [ "$MODE" != headless ] && [ "$(uname)" = Linux ]; then
  if ! "$VPY" -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null \
     && ! "$VPY" -c 'import gi; gi.require_version("WebKit2", "4.0")' 2>/dev/null \
     && ! "$VPY" -c 'import qtpy' 2>/dev/null; then
    echo "note: no GUI backend for pywebview yet. Install one of:" >&2
    echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-webkit2-4.1" >&2
    echo "  Fedora:        sudo dnf install python3-gobject webkit2gtk4.1" >&2
    echo "  or Qt:         $VPY -m pip install 'pywebview[qt]'" >&2
    echo "Without one the tracker runs headless (API + items.json, no window)." >&2
  fi
fi

mkdir -p "$BIN"
ln -sf "$DIR/orbital-track" "$BIN/orbital-track"
chmod +x "$DIR/orbital-track"
"$VPY" "$DIR/tracker.pyw" --install-marker >/dev/null

case $MODE in
  xdg)
    mkdir -p "$(dirname "$DESKTOP")"
    sed -e "s#@PYTHON@#$VPY#g" -e "s#@DIR@#$DIR#g" "$DIR/autostart/orbital-agents-tracker.desktop" > "$DESKTOP"
    echo "Autostart: $DESKTOP (starts at your next login)."
    ;;
  systemd|headless)
    ARGS=""; TARGET=graphical-session.target
    if [ "$MODE" = headless ]; then ARGS="--headless"; TARGET=default.target; fi
    mkdir -p "$(dirname "$UNIT")"
    sed -e "s#@PYTHON@#$VPY#g" -e "s#@DIR@#$DIR#g" -e "s#@ARGS@#$ARGS#g" -e "s#@TARGET@#$TARGET#g" \
      "$DIR/autostart/orbital-agents-tracker.service" > "$UNIT"
    if [ "$MODE" = headless ]; then
      sed -i -e '/^After=graphical-session.target/d' -e '/^PartOf=graphical-session.target/d' "$UNIT"
    fi
    systemctl --user daemon-reload
    systemctl --user enable --now orbital-agents-tracker.service
    echo "Autostart: systemd --user service orbital-agents-tracker ($MODE)."
    [ "$MODE" = headless ] && echo "Tip: 'loginctl enable-linger $USER' keeps it running without a login session."
    ;;
  none) ;;
  *) echo "unknown --autostart value: $MODE (xdg, systemd, headless, none)" >&2; exit 2 ;;
esac

echo "Installed. Start it now:  $VPY $DIR/tracker.pyw &"
echo "Check it:                 orbital-track ping"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Add $BIN to PATH so agents find orbital-track." ;; esac
