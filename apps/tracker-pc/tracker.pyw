"""Start the Agent Satellite Tracker (Windows / Linux).

    pythonw tracker.pyw          Windows: no console window (Startup folder / Task Scheduler)
    python3 tracker.pyw          Linux (XDG autostart / systemd --user)
    python3 tracker.pyw --headless   the API, inbox and items.json without a window

Same as `python -m tracker_pc` run from this folder.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracker_pc.app import main  # noqa: E402

sys.exit(main())
