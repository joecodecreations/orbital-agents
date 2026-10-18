#!/usr/bin/env python3
"""Pick an iOS simulator: prints "<udid> <state>".

    simulator.py                  an iPhone that is shut down (so the one you are using is left alone), else any iPhone
    simulator.py "iPhone 16 Pro"  the first available simulator whose name or UDID contains the text
"""
import json
import subprocess
import sys

query = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
data = json.loads(subprocess.run(["xcrun", "simctl", "list", "devices", "available", "--json"],
                                 capture_output=True, text=True, check=True).stdout)
rows = []
for runtime, devices in data.get("devices", {}).items():
    if "iOS" not in runtime:
        continue
    version = tuple(int(p) for p in runtime.rsplit("iOS-", 1)[-1].split("-") if p.isdigit())
    for d in devices:
        rows.append((d["name"], d["udid"], d.get("state", ""), version))
if query:
    rows = [r for r in rows if query in r[0].lower() or query == r[1].lower()]
    if not rows:
        sys.exit(f"no available simulator matches {query!r} (xcrun simctl list devices available)")
    rows.sort(key=lambda r: (r[0].lower() != query and r[1].lower() != query, tuple(-v for v in r[3])))
else:
    rows = [r for r in rows if r[0].startswith("iPhone")]
    if not rows:
        sys.exit("no iPhone simulator installed (Xcode → Settings → Components)")
    # Prefer one that is not running: a booted simulator is probably someone's.
    rows.sort(key=lambda r: (r[2] == "Booted", "Pro" not in r[0], "Max" in r[0], tuple(-v for v in r[3]), r[0]))
name, udid, state, _ = rows[0]
print(f"{udid} {state or 'Shutdown'}")
print(f"simulator: {name}", file=sys.stderr)
