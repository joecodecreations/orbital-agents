"""Shared bits for the example feeds: arguments, a small state file, fetching, pushing, the result line.

Every example feed works the same way:

    gather()  reads the source (this machine, a website, a public API) and returns plain data
    build()   turns that data into one orbital.section.v1 document (plus alerts), with no I/O
    main()    gather → build → push with HubClient, then prints one result line for the scheduler

Run any feed by hand with `--dry-run` to print the section instead of pushing it:

    python feeds/examples/machine_health.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx

USER_AGENT = "orbital-agents-example-feed/0.1"


def arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--dry-run", action="store_true", help="print the section JSON instead of pushing it")
    parser.add_argument("--home", help="ORBITAL_HOME to use (default ~/.orbital-agents)")
    return parser


def config(home: str | None = None):
    from orbital.core.config import load_config

    return load_config(home)


# --------------------------------------------------------------------------- state


def state_file(name: str, home: str | None = None) -> Path:
    """data/feeds/<name>.json under ORBITAL_HOME: history a feed keeps between runs (chart points)."""
    return config(home).paths.feeds_data / f"{name}.json"


def load_state(name: str, home: str | None = None) -> dict:
    try:
        data = json.loads(state_file(name, home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(name: str, data: dict, home: str | None = None) -> None:
    path = state_file(name, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- fetching


def http_client(timeout: float = 15.0) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT}
    return httpx.Client(timeout=timeout, follow_redirects=True, headers=headers)


def fetch_json(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = 15.0) -> Any:
    with http_client(timeout) as client:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


# --------------------------------------------------------------------------- pushing


def push(section: dict, alerts: list[dict] | None = None, *, dry_run: bool = False, client=None,
         home: str | None = None) -> dict:
    """PUT the section, then raise its alerts. Returns {"revision", "alerts"} (dry run: prints the JSON)."""
    alerts = alerts or []
    if dry_run:
        print(json.dumps({"section": section, "alerts": alerts}, indent=2))
        return {"revision": None, "alerts": 0}
    own = client is None
    if own:
        from orbital.feeds import HubClient

        client = HubClient.from_config(config(home))
    try:
        answer = client.put_section(section) or {}
        raised = 0
        for alert in alerts:
            client.alert(alert["title"], alert["body"], section_id=section["id"], level=alert.get("level", "info"),
                         **({"id": alert["id"]} if alert.get("id") else {}))
            raised += 1
        return {"revision": answer.get("revision"), "alerts": raised}
    finally:
        if own:
            client.close()


def result_line(feed: str, **values: Any) -> str:
    """`<feed> pushed key=value …`: the scheduler reads this to say ok / warn / error."""
    parts = " ".join(f"{k}={v}" for k, v in values.items())
    line = f"{feed} {parts}".strip()
    print(line, flush=True)
    return line


def run_feed(main: Callable[[list[str] | None], int], argv: list[str] | None = None) -> int:
    """Wrap main() so a failure prints a readable error line and exits non-zero."""
    try:
        return main(argv)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - the scheduler records this line as the error
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def local_time(ts: float, fmt: str = "%H:%M") -> str:
    return time.strftime(fmt, time.localtime(ts))
