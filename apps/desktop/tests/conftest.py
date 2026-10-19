"""Desktop app tests: run with `.venv/bin/pytest apps/desktop/tests -q`. No hub, no window, no keychain."""

from __future__ import annotations

import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))
