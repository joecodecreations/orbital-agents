"""`orbital desk`: an agent the Agent desk started reports back to it, and reads what the person adds.

    orbital desk note "Found the send log; counting today's rows" --progress 40
    orbital desk finding "37 emails went out today: 22 for the game, 15 for the site"
    orbital desk answer "37 emails went out today, 22 for the game and 15 for the site."
    orbital desk ask "Should I use the staging or the live account?" --wait 300
    orbital desk blocked "Needs the store account's two-factor code"
    orbital desk inbox [--wait 60]
    orbital desk answer --file report.md          (or - to read stdin)

The satellite sets ORBITAL_DESK_TASK, ORBITAL_DESK_TOKEN and ORBITAL_DESK_URL for every job the
desk starts; --task, --token and --url override them. `note` also moves the job's tracker item
(ORBITAL_TRACK_ID) with orbital-track, so one command covers both. An `answer`, `ask` or `blocked`
is read aloud to the person and raised as an alert. Every command prints what the person added
since the agent last looked: treat it as part of the brief.

Reporting never blocks the work: when the hub can't be reached it says so on stderr and exits 0.
`inbox` exits 1 then, and `ask` with no reply exits 0 and says to carry on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NAME = "desk"
HELP = "Report to the Agent desk from a job it started: note | finding | answer | ask | blocked | inbox"
VERBS = ("note", "finding", "answer", "ask", "blocked", "inbox")
KIND = {"note": "note", "finding": "finding", "answer": "answer", "ask": "question", "blocked": "blocked"}
MAX_CHARS = 4000
POLL = 110  # seconds per inbox request; the hub holds one for at most 120


def register(p):
    p.add_argument("verb", choices=VERBS)
    p.add_argument("text", nargs="*", help="what to report (or --file)")
    p.add_argument("--progress", help="note: how much of the whole job is done, 0-100")
    p.add_argument("--file", help="read the text from a file, or - for stdin")
    p.add_argument("--wait", type=float, default=None, help="ask: seconds to wait for a reply (default 300); inbox: seconds to wait for a message (default 0)")
    p.add_argument("--task", default=None, help="desk task id (default $ORBITAL_DESK_TASK)")
    p.add_argument("--token", default=None, help="the task's channel token (default $ORBITAL_DESK_TOKEN)")
    p.add_argument("--url", default=None, help="the hub (default $ORBITAL_DESK_URL)")
    p.add_argument("--json", action="store_true", help="print the hub's answer as JSON")


class Channel:
    def __init__(self, url: str, task: str, token: str) -> None:
        self.url, self.task, self.token = url.rstrip("/"), task, token

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 8.0) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.url}/v1/desk/tasks/{self.task}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - the hub's own URL
                return json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
                message = detail.get("error") or detail.get("detail") or ""
            except (ValueError, AttributeError):
                message = ""
            raise ChannelError(f"the hub said {exc.code}{': ' + str(message) if message else ''}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ChannelError(f"can't reach the hub at {self.url}: {getattr(exc, 'reason', exc)}") from exc


class ChannelError(Exception):
    pass


def _text(args) -> str:
    if args.file:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8", errors="replace")
    else:
        raw = " ".join(args.text)
    return raw.strip()


def _channel(args) -> Channel | None:
    url = args.url or os.environ.get("ORBITAL_DESK_URL") or ""
    task = args.task or os.environ.get("ORBITAL_DESK_TASK") or ""
    token = args.token or os.environ.get("ORBITAL_DESK_TOKEN") or ""
    if not url and task and token:
        try:
            from orbital.core.config import load_config
            url = load_config().hub_url()
        except Exception:  # noqa: BLE001 - no config: fall through to the message below
            url = ""
    return Channel(url, task, token) if url and task and token else None


def _show_inbox(messages: list[dict]) -> None:
    for message in messages or []:
        when = str(message.get("at") or "")[11:16]
        label = "Reply from the person" if message.get("kind") == "reply" else "New from the person"
        print(f"{label} (through the desk{', ' + when + ' UTC' if when else ''}), part of your brief now: {message.get('text')}")


def _track(text: str, progress: str | None) -> None:
    """Move the job's tracker item too, so the agent needs one command, not two."""
    item = os.environ.get("ORBITAL_TRACK_ID")
    if not item:
        return
    try:
        from orbital.satellite.agents import resolve_executable
        binary = resolve_executable("orbital-track")
    except Exception:  # noqa: BLE001
        binary = None
    if not binary:
        return
    argv = [binary, "update", item]
    if text:
        argv += ["--note", text[:1000]]
    if progress is not None:
        argv += ["--progress", str(progress)]
    try:
        subprocess.run(argv, capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def run(args) -> int:
    verb = args.verb
    text = _text(args) if verb != "inbox" else ""
    if verb != "inbox" and not text and not (verb == "note" and args.progress is not None):
        print(f'orbital desk {verb}: say what, e.g. orbital desk {verb} "..."', file=sys.stderr)
        return 2
    if verb == "note":
        _track(text, args.progress)
    channel = _channel(args)
    if channel is None:
        print("orbital desk: this job wasn't started by the desk (ORBITAL_DESK_TASK / ORBITAL_DESK_TOKEN aren't set); nothing sent.",
              file=sys.stderr)
        return 1 if verb == "inbox" else 0
    try:
        if verb == "inbox":
            answer = _inbox(channel, args.wait or 0)
            if args.json:
                print(json.dumps(answer, indent=2))
            elif answer.get("messages"):
                _show_inbox(answer["messages"])
            else:
                print("Nothing new from the person.")
            return 0
        body: dict = {"kind": KIND[verb], "text": text[:MAX_CHARS]}
        if args.progress is not None:
            body["progress"] = args.progress
        answer = channel.request("POST", "/report", body)
        if args.json:
            print(json.dumps(answer, indent=2))
        else:
            print(f"desk: {verb} sent to {channel.task}" + (f" ({answer['progress']}%)" if answer.get("progress") is not None and verb == "note" else ""))
            _show_inbox(answer.get("inbox") or [])
        if verb == "ask":
            return _await_reply(channel, 300 if args.wait is None else args.wait, bool(answer.get("inbox")))
        return 0
    except ChannelError as exc:
        print(f"orbital desk: {exc}. Carry on; this report wasn't delivered.", file=sys.stderr)
        return 1 if verb == "inbox" else 0


def _inbox(channel: Channel, wait: float) -> dict:
    wait = max(0.0, min(float(wait), 3600.0))
    deadline = time.monotonic() + wait
    while True:
        step = max(0.0, min(POLL, deadline - time.monotonic()))
        answer = channel.request("GET", f"/inbox?wait={step:.0f}", timeout=step + 15)
        if answer.get("messages") or step <= 0 or answer.get("status") not in (None, "running"):
            return answer
        if time.monotonic() >= deadline:
            return answer


def _await_reply(channel: Channel, wait: float, already: bool) -> int:
    if already or wait <= 0:
        return 0
    print(f"Waiting up to {int(wait)} s for the person's reply…", flush=True)
    try:
        answer = _inbox(channel, wait)
    except ChannelError as exc:
        print(f"orbital desk: {exc}. Carry on with your best call and say what you assumed.", file=sys.stderr)
        return 0
    if answer.get("messages"):
        _show_inbox(answer["messages"])
    else:
        print("No reply yet. Carry on with your best call and say in your report what you assumed; "
              "if the reply comes later, `orbital desk inbox` or your next desk command shows it.")
    return 0
