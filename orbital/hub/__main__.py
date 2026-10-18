"""`python -m orbital.hub`: serve the hub on hub.host:hub.port and write run/hub.json.

run/hub.json holds {"port", "pid", "hub_id", "started_at"} while the hub runs, so the CLI, the
watchdog and the apps' tooling can find it; it is removed on a clean exit.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys

from orbital.core import load_config
from orbital.hub.store import now_iso


def _write_run_file(cfg, port: int, hub_id: str) -> None:
    cfg.paths.ensure()
    target = cfg.paths.run / "hub.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "pid": os.getpid(), "hub_id": hub_id, "started_at": now_iso()}) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _remove_run_file(cfg) -> None:
    target = cfg.paths.run / "hub.json"
    try:
        if json.loads(target.read_text(encoding="utf-8")).get("pid") == os.getpid():
            target.unlink()
    except (OSError, ValueError):
        pass


def _port_taken(host: str, port: int) -> OSError | None:
    """The bind error when something already listens on host:port, else None."""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            return exc
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orbital.hub", description="Run the Orbital hub.")
    parser.add_argument("--host", help="override hub.host")
    parser.add_argument("--port", type=int, help="override hub.port")
    parser.add_argument("--home", help="use another ORBITAL_HOME")
    args = parser.parse_args(argv)
    if args.home:
        os.environ["ORBITAL_HOME"] = args.home

    import uvicorn

    from orbital.hub.app import create_app

    cfg = load_config()
    host = args.host or str(cfg.get("hub.host", "0.0.0.0"))
    port = int(args.port or cfg.get("hub.port", 7700))
    # A second copy (the login service is already running) stops here, before it starts the hub's
    # background tasks or replaces the running copy's run/hub.json.
    taken = _port_taken(host, port)
    if taken is not None:
        print(f"hub: cannot listen on {host}:{port}: {taken} "
              "(is the hub service already running? `orbital services status`)", file=sys.stderr)
        return 1
    app = create_app(cfg)
    _write_run_file(cfg, port, app.state.hub.hub_id)
    # uvicorn shuts down on SIGTERM / SIGINT, then raises the signal again with the handler that was
    # there before it started. Make that handler an exit, so the finally below still runs.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))
    try:
        # Access logs off: the hub writes its own JSON log, and request lines would carry tokens in ?token=.
        uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False, proxy_headers=False)
    finally:
        _remove_run_file(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
