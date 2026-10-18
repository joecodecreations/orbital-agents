"""`orbital browser`: drive Chrome through the Orbital Agents Browser Controller.

    orbital browser serve                         run the bridge here (foreground)
    orbital browser status                        bridge + extension health
    orbital browser nodes                         this machine + satellites with a browser
    orbital browser [--node NAME] tabs
    orbital browser navigate https://example.com
    orbital browser click "button[type=submit]"
    orbital browser type "input[name=q]" "hello" [--human] [--clear]
    orbital browser eval "document.title"
    orbital browser screenshot --out page.png
    orbital browser network --host api.example.com
    orbital browser endpoints
    orbital browser upload "input[type=file]" ./photo.jpg
    orbital browser stage ./video.mp4             copy a file to the node (prints its path there)
    orbital browser command <action> '<json params>'   any extension action

Page commands act in the agent session's own Chrome window (`--session NAME`, default "main").
Results print as JSON. Exit codes: 0 ok, 1 the command failed, 3 the bridge could not be reached.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NAME = "browser"
HELP = "Drive Chrome here or on another machine (Orbital Agents Browser Controller)"

EXIT_FAILED = 1
EXIT_UNREACHABLE = 3


def _common(p: argparse.ArgumentParser, top: bool) -> None:
    d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
    p.add_argument("--node", default=d("local"), help="local (default) or a satellite name, machine or id")
    p.add_argument("--via", choices=["auto", "direct", "hub"], default=d("auto"),
                   help="how to reach a remote node: direct to its bridge (needs ORBITAL_BROWSER_TOKEN), through the hub, or auto")
    p.add_argument("--session", default=d(None), help="agent session (its own Chrome window); default main")
    p.add_argument("--timeout", type=float, default=d(60.0), help="seconds to wait for a command (default 60)")
    p.add_argument("--json", action="store_true", default=d(False), help="print raw JSON for status and nodes")


def register(p: argparse.ArgumentParser) -> None:
    _common(p, top=True)
    sub = p.add_subparsers(dest="browser_cmd", metavar="<subcommand>")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text, description=help_text)
        _common(sp, top=False)
        return sp

    sp = add("serve", "run the bridge in the foreground (what the background service runs)")
    sp.add_argument("--port", type=int, default=None, help="override browser.bridge_port")
    sp.add_argument("--bind", choices=["loopback", "tailscale"], default=None, help="override browser.bind")

    add("status", "bridge and extension status")
    add("nodes", "browser nodes: this machine and satellites with a browser")
    add("tabs", "list open tabs")

    sp = add("navigate", "open a URL in the session's tab")
    sp.add_argument("url")
    sp.add_argument("--new-tab", action="store_true", help="open a new tab instead of reusing the session's tab")
    sp.add_argument("--no-wait", action="store_true", help="return without waiting for the page to load")
    sp.add_argument("--tab-id", type=int, default=None)

    sp = add("click", "click the element matching a CSS selector")
    sp.add_argument("selector")
    sp.add_argument("--index", type=int, default=0, help="which match (default the first)")
    sp.add_argument("--trusted", action="store_true", help="a real mouse click through the debugger")

    sp = add("click-text", "click the visible button or link with this text")
    sp.add_argument("text")
    sp.add_argument("--exact", action="store_true")

    sp = add("type", "type text into the field matching a CSS selector")
    sp.add_argument("selector")
    sp.add_argument("text")
    sp.add_argument("--clear", action="store_true", help="empty the field first")
    sp.add_argument("--human", action="store_true", help="type one key at a time with natural pauses")

    sp = add("press", "press a key or combination, e.g. Enter, Escape, Meta+Enter")
    sp.add_argument("key")

    sp = add("eval", "evaluate JavaScript in the page and print the value")
    sp.add_argument("js")
    sp.add_argument("--world", choices=["MAIN", "ISOLATED", "CDP"], default=None)

    sp = add("text", "the page's visible text")
    sp.add_argument("selector", nargs="?", default=None)
    sp.add_argument("--max-len", type=int, default=None)

    sp = add("dom", "the page's HTML")
    sp.add_argument("--selector", default=None)
    sp.add_argument("--out", default=None, help="write the HTML to this file")
    sp.add_argument("--max-len", type=int, default=None)

    add("links", "every link on the page")

    sp = add("screenshot", "PNG screenshot of the session's tab")
    sp.add_argument("--out", default="screenshot.png", help="file to write (default screenshot.png)")
    sp.add_argument("--full-page", action="store_true")

    sp = add("network", "captured network traffic, newest first (turn capture on first)")
    sp.add_argument("--host", default=None)
    sp.add_argument("--path", default=None)
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--api-only", action="store_true")

    sp = add("endpoints", "API endpoint catalog built from the capture")
    sp.add_argument("--host", default=None)

    sp = add("capture", "turn network capture on or off")
    sp.add_argument("state", choices=["on", "off"])

    sp = add("upload", "put a file into a page's file input (staged on remote nodes)")
    sp.add_argument("selector", help='file input selector, e.g. "input[type=file]"')
    sp.add_argument("file", nargs="+")
    sp.add_argument("--label", action="append", default=None, help="text of the page's upload button (uses the real file chooser)")
    sp.add_argument("--click", default=None, help="CSS selector of the page's upload button")
    sp.add_argument("--stage", action="store_true", help="stage the file first even on this machine")

    sp = add("stage", "copy a file to the node's staged folder and print its path there")
    sp.add_argument("file")

    sp = add("wait-for", "wait for text, a selector or a URL")
    sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--selector", default=None)
    sp.add_argument("--url", default=None)
    sp.add_argument("--wait", type=float, default=15.0, help="seconds to wait (default 15)")
    sp.add_argument("--gone", action="store_true", help="wait for it to disappear")

    sp = add("window", "the session's automation window: status, open, focus, release, close, on, off")
    sp.add_argument("op", nargs="?", default="status", choices=["status", "open", "focus", "release", "close", "on", "off", "adopt"])
    sp.add_argument("--window-id", type=int, default=None)

    sp = add("command", "run any extension action: command ACTION '{\"param\": 1}'")
    sp.add_argument("action")
    sp.add_argument("params", nargs="?", default="{}", help="JSON object of parameters")

    sp = add("files", "list files in the node's ~/Downloads or staged folder")
    sp.add_argument("path", nargs="?", default=None)

    sp = add("pull", "copy a file from the node's ~/Downloads or staged folder to here")
    sp.add_argument("remote")
    sp.add_argument("--out", default=".", help="local file or folder (default: current folder)")

    add("export", "write the capture to files on the node")
    add("clear", "empty the capture buffer and the command queue")


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _print_status(data: dict) -> None:
    ext = data.get("extension") or {}
    print(f"bridge      {data.get('service')} {data.get('version')} on {data.get('machine')}")
    print(f"listening   {', '.join(data.get('listening') or [])}  (bind={data.get('bind')}, token {'set' if data.get('token_set') else 'not set'})")
    if ext.get("connected"):
        print(f"extension   connected (v{ext.get('version')}, build {ext.get('build')}, last poll {ext.get('last_poll_age_s')} s ago)")
    else:
        age = ext.get("last_poll_age_s")
        print("extension   NOT connected" + (f" (last poll {age} s ago)" if age is not None else " (never polled)"))
        print("            Load browser-extension/ in Chrome (chrome://extensions, Developer mode, Load unpacked)")
        print(f"            and set its port to {data.get('port')}.")
    if ext.get("warning"):
        print(f"warning     {ext['warning']}")
    cap = data.get("capture") or {}
    print(f"capture     {'on' if ext.get('capturing') else 'off'}, {cap.get('events', 0)} events buffered")
    q = data.get("queue") or {}
    print(f"queue       {q.get('waiting', 0)} waiting, {q.get('in_flight', 0)} running")
    es = data.get("extension_status") or {}
    if es and not es.get("error"):
        sessions = es.get("sessions") or []
        print(f"windows     {len(sessions)} agent session(s): " + (", ".join(s.get("session", "?") for s in sessions) or "none"))


def _client(args):
    from orbital.browser import BrowserClient

    return BrowserClient(getattr(args, "node", "local"), via=getattr(args, "via", "auto"),
                         session=getattr(args, "session", None), timeout=getattr(args, "timeout", 60.0))


def _nodes(args) -> int:
    from orbital.browser import BrowserClient, BrowserError
    from orbital.browser.nodes import list_nodes
    from orbital.core.config import load_config

    cfg = load_config()
    nodes, err = list_nodes(cfg)
    rows = []
    for node in nodes:
        row = node.to_dict()
        try:
            h = BrowserClient(node, via=args.via, config=cfg, timeout=5).health()
            row["bridge"] = "up"
            row["extension_connected"] = bool(h.get("extension_connected"))
        except BrowserError as exc:
            row["bridge"] = "down"
            row["extension_connected"] = False
            row["error"] = str(exc)
        rows.append(row)
    if args.json:
        _print({"nodes": rows, "hub_error": err})
        return 0
    print(f"{'NODE':18} {'KIND':10} {'HUB':8} {'BRIDGE':7} {'EXTENSION':10} URL")
    for r in rows:
        ext = "connected" if r["extension_connected"] else "-"
        print(f"{r['name'][:18]:18} {r['kind']:10} {r['status'] or '-':8} {r['bridge']:7} {ext:10} {r['url']}")
    if err:
        print(f"\n(remote nodes unavailable: {err})")
    return 0


def run(args) -> int:
    cmd = getattr(args, "browser_cmd", None)
    if not cmd:
        print("usage: orbital browser [--node NAME] <subcommand> ...  (orbital browser -h for the list)")
        return 2
    if cmd == "serve":
        from orbital.browser.server import main as serve_main

        argv = []
        if args.port:
            argv += ["--port", str(args.port)]
        if args.bind:
            argv += ["--bind", args.bind]
        return serve_main(argv)

    from orbital.browser import BrowserError

    try:
        if cmd == "nodes":
            return _nodes(args)
        b = _client(args)
        if cmd == "status":
            data = b.status()
            if args.json:
                _print(data)
            else:
                print(f"node        {b.node.name} ({b.node.url})")
                _print_status(data)
            return 0
        if cmd == "tabs":
            tabs = b.tabs()
            if args.json:
                _print(tabs)
            else:
                for t in tabs:
                    tag = f"[{t.get('session')}]" if t.get("session") else ""
                    print(f"{t.get('id'):>6} {'*' if t.get('active') else ' '} {tag:10} {(t.get('title') or '')[:50]:50} {t.get('url')}")
            return 0
        if cmd == "navigate":
            _print(b.navigate(args.url, new_tab=args.new_tab, tab_id=args.tab_id, wait=not args.no_wait))
        elif cmd == "click":
            _print(b.click(args.selector, index=args.index, trusted=args.trusted))
        elif cmd == "click-text":
            _print(b.click_text(args.text, exact=args.exact))
        elif cmd == "type":
            _print(b.type(args.selector, args.text, clear=args.clear, human=args.human))
        elif cmd == "press":
            _print(b.press(args.key))
        elif cmd == "eval":
            _print(b.eval(args.js, world=args.world))
        elif cmd == "text":
            print(b.text(args.selector, args.max_len))
        elif cmd == "dom":
            d = b.dom(args.selector, args.max_len)
            if args.out:
                Path(args.out).write_text(d.get("html", ""), encoding="utf-8")
                _print({k: v for k, v in d.items() if k != "html"} | {"written": str(Path(args.out).resolve())})
            else:
                print(d.get("html", ""))
        elif cmd == "links":
            _print(b.links())
        elif cmd == "screenshot":
            png = b.screenshot(args.out, full_page=args.full_page)
            _print({"ok": True, "file": str(Path(args.out).resolve()), "bytes": len(png)})
        elif cmd == "network":
            _print(b.network(args.host, path=args.path, limit=args.limit, api_only=args.api_only))
        elif cmd == "endpoints":
            items = b.endpoints(args.host)
            if args.json:
                _print(items)
            else:
                for e in items:
                    print(f"{e['count']:>5}  {e['method']:6} {e['host']}{e['path_key']}  {e.get('statuses')}")
                print(f"{len(items)} endpoints")
        elif cmd == "capture":
            _print(b.capture(args.state == "on"))
        elif cmd == "upload":
            _print(b.upload(args.selector, args.file, stage=True if args.stage else None,
                            click_labels=args.label, click_selector=args.click))
        elif cmd == "stage":
            _print({"ok": True, "node": b.node.name, "path": b.stage(args.file)})
        elif cmd == "wait-for":
            _print(b.wait_for(args.text, selector=args.selector, url=args.url, timeout=args.wait, gone=args.gone))
        elif cmd == "window":
            _print(b.window(args.op, windowId=args.window_id))
        elif cmd == "command":
            try:
                params = json.loads(args.params or "{}")
            except ValueError:
                print("params must be a JSON object, e.g. '{\"selector\": \"#go\"}'", file=sys.stderr)
                return 2
            if not isinstance(params, dict):
                print("params must be a JSON object", file=sys.stderr)
                return 2
            _print(b.call(args.action, **params))
        elif cmd == "files":
            _print(b.files(args.path))
        elif cmd == "pull":
            _print({"ok": True, "file": str(b.pull(args.remote, args.out).resolve())})
        elif cmd == "export":
            _print(b.export())
        elif cmd == "clear":
            _print(b.clear())
        else:
            print(f"unknown subcommand {cmd}", file=sys.stderr)
            return 2
        return 0
    except BrowserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        unreachable = exc.status is None and "could not reach" in str(exc)
        return EXIT_UNREACHABLE if unreachable else EXIT_FAILED
