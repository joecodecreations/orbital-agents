"""orbital-track, the CLI agents report with: built from apps/tracker and run against a temp ORBITAL_HOME.

No tracker app runs during these tests, so commands land in the inbox (and, on a satellite, go to a
fake hub). Nothing touches ~/.orbital-agents or the real tracker ports.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from orbital.core.config import load_config

ROOT = Path(__file__).resolve().parents[2]
TRACKER = ROOT / "apps" / "tracker"
ICON = ROOT / "docs" / "assets" / "icon-128.png"

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swift") is None,
                                reason="the tracker CLI is a macOS Swift build")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def cli() -> Path:
    subprocess.run(["swift", "build", "--product", "orbital-track"], cwd=TRACKER, check=True,
                   capture_output=True, timeout=900)
    bin_dir = subprocess.run(["swift", "build", "--show-bin-path"], cwd=TRACKER, check=True,
                             capture_output=True, text=True, timeout=120).stdout.strip()
    exe = Path(bin_dir) / "orbital-track"
    assert exe.is_file()
    return exe


def make_home(tmp_path: Path, toml: str = "") -> Path:
    home = tmp_path / "orbital-home"
    home.mkdir()
    port = free_port()   # nothing listens here: the tracker app is "not running"
    (home / "config.toml").write_text(
        f'[system]\nmachine_name = "test-mac"\n\n[tracker]\nport = {port}\nstt_port = {free_port()}\n\n{toml}',
        encoding="utf-8")
    load_config(home).write_resolved()
    return home


def run(cli: Path, home: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith("ORBITAL_") and k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    clean["ORBITAL_HOME"] = str(home)
    clean.update(env or {})
    return subprocess.run([str(cli), *args], capture_output=True, text=True, env=clean, timeout=60,
                          cwd=cwd or home)


def inbox(home: Path) -> list[dict]:
    folder = home / "data" / "tracker" / "inbox"
    files = sorted(p for p in folder.glob("*.json")) if folder.is_dir() else []
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def test_help(cli, tmp_path):
    home = make_home(tmp_path)
    for args in (["--help"], ["help"], ["-h"]):
        out = run(cli, home, *args)
        assert out.returncode == 0, out.stderr
        assert "orbital-track start --agent" in out.stdout
        assert "ORBITAL_TRACK_PARENT" in out.stdout
    assert run(cli, home).returncode == 2          # no command: help on stdout, usage error
    bad = run(cli, home, "explode")
    assert bad.returncode == 2 and "unknown command" in bad.stderr


def test_offline_start_lands_in_the_inbox(cli, tmp_path):
    home = make_home(tmp_path)
    out = run(cli, home, "start", "--agent", "Claude Code", "--title", "Fix Login Redirect",
              "--details", "Login sends users to **/404**.")
    assert out.returncode == 0, out.stderr
    item_id = out.stdout.strip()
    assert len(item_id) == 8 and item_id.isalnum()
    assert "isn't running; queued" in out.stderr

    [cmd] = inbox(home)
    assert cmd["op"] == "start"
    assert cmd["id"] == item_id
    assert cmd["title"] == "Fix Login Redirect"
    assert cmd["agent"] == "claude"
    assert cmd["machine"] == "test-mac"
    assert cmd["details"].startswith("Login sends users")
    assert cmd["workspace"] == str(home.resolve()) or cmd["workspace"] == str(home)

    # Follow-ups queue behind it, in order.
    assert run(cli, home, "update", item_id, "--note", "found it", "--quiet").stdout.strip() == "ok"
    assert run(cli, home, "done", item_id, "--summary", "fixed").returncode == 0
    ops = [(c["op"], c["id"]) for c in inbox(home)]
    assert ops == [("start", item_id), ("update", item_id), ("done", item_id)]


def test_subagents_and_parent_env(cli, tmp_path):
    home = make_home(tmp_path)
    sub = run(cli, home, "sub", "parent01", "--agent", "gemini", "--title", "Audit Routes")
    assert sub.returncode == 0, sub.stderr
    # A subagent that inherited ORBITAL_TRACK_PARENT can't make a top-level item.
    start = run(cli, home, "start", "--agent", "claude", "--title", "Inner", env={"ORBITAL_TRACK_PARENT": "parent01"})
    assert start.returncode == 0
    cmds = inbox(home)
    assert [c["op"] for c in cmds] == ["sub", "sub"]
    assert all(c["parent"] == "parent01" for c in cmds)
    assert cmds[1]["id"] == start.stdout.strip()
    # sub without a parent is a usage error.
    assert run(cli, home, "sub", "--title", "x").returncode == 2


def test_bad_ids_are_refused(cli, tmp_path):
    home = make_home(tmp_path)
    out = run(cli, home, "done", "../../etc", "--summary", "x")
    assert out.returncode == 2
    assert inbox(home) == []


def test_disable_env_is_a_no_op(cli, tmp_path):
    home = make_home(tmp_path)
    out = run(cli, home, "start", "--title", "Hidden", env={"ORBITAL_TRACK_DISABLE": "1"})
    assert out.returncode == 0 and len(out.stdout.strip()) == 8
    assert inbox(home) == []


def test_media_is_archived_at_send_time(cli, tmp_path):
    home = make_home(tmp_path)
    shot = tmp_path / "shot.png"
    shutil.copy(ICON, shot)
    out = run(cli, home, "start", "--agent", "codex", "--title", "Checkout Page", "--media", str(shot),
              "--details", f"![after]({shot})")
    assert out.returncode == 0, out.stderr
    [cmd] = inbox(home)
    assert len(cmd["media"]) == 1                    # --media and the markdown image are the same file
    archived = cmd["media"][0]["archived"]
    assert archived["kind"] == "image" and archived["machine"] == "test-mac"
    assert archived["archivePath"].startswith("media/")
    assert (home / "data" / "tracker" / archived["archivePath"]).is_file()
    assert "inlineArchive" not in cmd["media"][0]   # only hub copies carry the image inline


def test_evidence_level_offline(cli, tmp_path):
    home = make_home(tmp_path)
    assert "OFF" in run(cli, home, "evidence").stdout
    out = run(cli, home, "evidence", "set", "min 1")
    assert out.returncode == 0 and "MIN 1" in out.stdout
    settings = json.loads((home / "data" / "tracker" / "settings.json").read_text(encoding="utf-8"))
    assert settings["evidence"] == "min1" and settings["updatedBy"] == "cli"
    as_json = json.loads(run(cli, home, "evidence", "--json").stdout)
    assert as_json["evidence"] == "min1" and as_json["label"] == "Min 1"
    # start reminds the agent on stderr; stdout stays just the id.
    start = run(cli, home, "start", "--title", "Visual Work")
    assert len(start.stdout.strip()) == 8 and "Screenshot evidence: MIN 1" in start.stderr
    assert run(cli, home, "evidence", "set", "sometimes").returncode == 2


def test_ping_and_list_offline(cli, tmp_path):
    home = make_home(tmp_path)
    ping = run(cli, home, "ping")
    assert ping.returncode == 1
    status = json.loads(ping.stdout)
    assert status["ok"] is False and status["machine"] == "test-mac" and status["role"] == "hub"
    items = home / "data" / "tracker" / "items.json"
    items.write_text(json.dumps([{"id": "abc12345", "title": "From Disk", "source": "agent", "agent": "grok",
                                  "machine": "test-mac", "status": "active"}]), encoding="utf-8")
    listing = run(cli, home, "list")
    assert listing.returncode == 0 and "abc12345" in listing.stdout and "From Disk" in listing.stdout


class FakeHub:
    """Records POST /v1/tracker/command calls like the hub relay endpoint."""

    def __init__(self, status: int = 200):
        self.requests: list[dict] = []
        self.status = status
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                hub.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                     "type": self.headers.get("Content-Type"), "body": json.loads(body or b"{}")})
                self.send_response(hub.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def satellite_home(tmp_path: Path, hub_port: int, token: str | None = "hub-token-for-tests") -> Path:
    home = make_home(tmp_path, f'[hub]\nurl = "http://127.0.0.1:{hub_port}"\n')
    toml = (home / "config.toml").read_text(encoding="utf-8").replace('machine_name = "test-mac"',
                                                                     'machine_name = "test-mac"\nrole = "satellite"')
    (home / "config.toml").write_text(toml, encoding="utf-8")
    load_config(home).write_resolved()
    if token:
        secrets = home / "secrets.env"
        secrets.write_text(f"ORBITAL_HUB_TOKEN={token}\n", encoding="utf-8")
        secrets.chmod(0o600)
    return home


def test_satellite_relays_to_the_hub(cli, tmp_path):
    hub = FakeHub()
    try:
        home = satellite_home(tmp_path, hub.port)
        shot = tmp_path / "shot.png"
        shutil.copy(ICON, shot)
        out = run(cli, home, "start", "--agent", "claude", "--title", "Remote Work", "--media", str(shot), "--quiet")
        assert out.returncode == 0, out.stderr
        item_id = out.stdout.strip()
        assert run(cli, home, "done", item_id, "--summary", "finished", "--quiet").returncode == 0

        assert [r["path"] for r in hub.requests] == ["/v1/tracker/command"] * 2
        first = hub.requests[0]
        assert first["auth"] == "Bearer hub-token-for-tests"
        assert first["type"] == "application/json"
        assert first["body"]["id"] == item_id and first["body"]["machine"] == "test-mac"
        inline = first["body"]["media"][0]["inlineArchive"]
        assert base64.b64decode(inline)[:3] == b"\xff\xd8\xff"          # the archived JPEG travels inline
        assert hub.requests[1]["body"]["op"] == "done"
        # This Mac's own tracker gets the commands too (queued: no app is running).
        assert [c["op"] for c in inbox(home)] == ["start", "done"]
        assert "inlineArchive" not in inbox(home)[0]["media"][0]
    finally:
        hub.close()


def test_satellite_queues_when_the_hub_is_down(cli, tmp_path):
    home = satellite_home(tmp_path, free_port())       # nothing listens on the hub port
    out = run(cli, home, "start", "--agent", "claude", "--title", "Offline Hub")
    assert out.returncode == 0 and len(out.stdout.strip()) == 8
    assert "hub" not in out.stderr.lower()              # hub problems are silent
    outbox = home / "data" / "tracker" / "hub-outbox"
    assert len(list(outbox.glob("*.json"))) == 1
    status = json.loads(run(cli, home, "ping").stdout)
    assert status["hub"]["queued"] == 1 and status["hub"]["token"] == "set"


def test_satellite_without_a_tracker_only_relays(cli, tmp_path):
    hub = FakeHub()
    try:
        home = satellite_home(tmp_path, hub.port)
        toml = (home / "config.toml").read_text(encoding="utf-8").replace("[tracker]\n", "[tracker]\nenabled = false\n")
        (home / "config.toml").write_text(toml, encoding="utf-8")
        load_config(home).write_resolved()
        out = run(cli, home, "start", "--agent", "codex", "--title", "Headless Box")
        assert out.returncode == 0
        assert len(hub.requests) == 1 and inbox(home) == []
    finally:
        hub.close()
