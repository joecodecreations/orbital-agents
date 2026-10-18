"""Satellite service: starts agent CLIs on this machine when the hub (or the local CLI) asks.

Every machine runs one, the hub included, so "run an agent on machine X" works the same everywhere.

    python -m orbital.satellite          # serve (launchd / systemd run this)
    orbital satellite serve | status | join <hub_url> <token>

Pieces:
    agents.py     which agent CLIs are installed and how their argv is built
    jobs.py       job queue, concurrency limit, timeouts, output capture, job records
    server.py     the FastAPI app (API in docs/ARCHITECTURE.md, "Satellite service API") and serve()
    heartbeat.py  heartbeat to the hub; the hub's own satellite joins the local hub automatically
    join.py       join_hub(): pair this machine with a hub (the setup wizard calls it)
    discover.py   find_hubs(): look for hubs among Tailscale peers (the setup wizard calls it)
    tracker.py    reports jobs to the Agent Satellite Tracker through the orbital-track CLI
"""

from orbital.satellite.discover import find_hubs
from orbital.satellite.join import join_hub

__all__ = ["find_hubs", "join_hub"]
