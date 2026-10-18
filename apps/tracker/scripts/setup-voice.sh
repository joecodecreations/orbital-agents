#!/bin/bash
# Optional on-device speech for the tracker (only used when [tracker] voice = true).
#
# Creates the tracker's own Python env in $ORBITAL_HOME/voice/venv, installs CrisperWhisper 2.0 with
# PyTorch (Metal/MPS), copies the sidecar script next to it, and downloads the speech model so the
# first phrase works offline.
#
# Download sizes: Python packages about 1 GB on disk (PyTorch, transformers, crisperwhisper);
# the model nyralabs/CrisperWhisper2.0_medium is about 1.5 GB (Hugging Face cache, ~/.cache/huggingface).
# The model has its own license; read its model card before using it commercially.
#
#   scripts/setup-voice.sh            PYTHON=/path/to/python3 to pick the interpreter (3.10+)
set -euo pipefail
cd "$(dirname "$0")/.."
HOME_DIR="${ORBITAL_HOME:-$HOME/.orbital-agents}"
VOICE="$HOME_DIR/voice"
MODEL="${CRISPER_MODEL:-nyralabs/CrisperWhisper2.0_medium}"
PY="${PYTHON:-$(command -v python3.12 || command -v python3.11 || command -v python3)}"
"$PY" - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit(f"setup-voice: Python 3.10+ needed, found {sys.version.split()[0]}; set PYTHON=/path/to/python3")
PY
mkdir -p "$VOICE"
chmod 700 "$HOME_DIR"
"$PY" -m venv "$VOICE/venv"
"$VOICE/venv/bin/pip" install --upgrade pip
"$VOICE/venv/bin/pip" install "crisperwhisper[transformers]==2.0.1" numpy soundfile huggingface_hub
cp stt/crisper_server.py "$VOICE/crisper_server.py"
"$VOICE/venv/bin/python" - "$MODEL" <<'PY'
import sys
import torch
from huggingface_hub import snapshot_download

print("torch", torch.__version__, "| Metal (MPS) available:", torch.backends.mps.is_available())
print("model:", snapshot_download(sys.argv[1]))
PY
echo "Done. Turn voice on: set [tracker] voice = true in $HOME_DIR/config.toml, run 'orbital config --apply', then restart the tracker."
