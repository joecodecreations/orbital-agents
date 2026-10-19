"""orbital_track.py: the cross-platform CLI agents report with. Same subcommands, flags, output
and exit codes as apps/tracker/Sources/orbital-track (see tests/tracker/test_cli.py for the Mac one)."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import cli_env, free_port, run_cli, write_config
from tracker_pc import model


def items(home: Path) -> list[dict]:
    return json.loads((home / "data" / "tracker" / "items.json").read_text())


def test_start_prints_only_the_id(home, app):
    out = run_cli(home, "start", "--agent", "Claude Code", "--title", "Fix Login", "--details", "md", "--label", "Claude Code")
    assert out.returncode == 0
    item_id = out.stdout.strip()
    assert model.valid_id(item_id) and out.stdout == item_id + "\n"
    item = app.store.item(item_id)
    assert item["agent"] == "claude" and item["agentLabel"] == "Claude Code" and item["workspace"] == str(home)
    assert item["machine"] == "test-pc"


def test_full_flow_with_media_files_and_stdin(home, app, png, tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("Done.")
    item_id = run_cli(home, "start", "--agent", "gemini", "--title", "Feed", "--media", str(png),
                      "--details", f"see ![after]({png})").stdout.strip()
    assert run_cli(home, "note", item_id, "quick", "note").returncode == 0
    assert run_cli(home, "update", item_id, "--details", "-", stdin="from stdin").returncode == 0
    assert run_cli(home, "update", item_id, "--details", "more", "--append").returncode == 0
    sub = run_cli(home, "sub", item_id, "--agent", "codex", "--title", "Helper").stdout.strip()
    assert run_cli(home, "done", sub, "--summary", "sub ok").stdout == "ok\n"
    assert run_cli(home, "complete", item_id, "--summary-file", str(summary)).stdout == "ok\n"
    item = app.store.item(item_id)
    assert item["status"] == "done" and item["details"] == "from stdin\n\nmore"
    assert [n["text"] for n in item["notes"]] == ["quick note"]
    assert item["summary"].startswith("Done.")
    assert len(item["media"]) == 1  # the same file via --media and markdown is kept once
    assert item["media"][0]["archivePath"].startswith("media/") and item["media"][0]["machine"] == "test-pc"
    assert item["subagents"][0]["agent"] == "chatgpt" and item["subagents"][0]["summary"] == "sub ok"


def test_title_from_trailing_words_and_alias(home, app):
    item_id = run_cli(home, "add", "Quick", "Title", "Here").stdout.strip()
    assert app.store.item(item_id)["title"] == "Quick Title Here"


def test_usage_errors_exit_2(home, app):
    assert run_cli(home).returncode == 2  # no command: help, exit 2
    assert run_cli(home, "help").returncode == 0
    assert run_cli(home, "--help").returncode == 0
    for args in (["start"], ["start", "--title", "t", "--id", "x"], ["update"], ["done", "bad/id"],
                 ["sub", "--title", "t"], ["frobnicate"], ["start", "--title"], ["evidence", "set", "loud"]):
        out = run_cli(home, *args)
        assert out.returncode == 2 and out.stderr.startswith("orbital-track: "), args


def test_parent_env_turns_start_into_sub(home, app):
    parent = run_cli(home, "start", "--title", "Parent").stdout.strip()
    out = run_cli(home, "start", "--title", "Child", env=cli_env(home, ORBITAL_TRACK_PARENT=parent))
    child = out.stdout.strip()
    assert app.store.item(child) is None
    assert app.store.item(parent)["subagents"][0]["id"] == child


def test_disable_env_is_a_noop(home, app):
    env = cli_env(home, ORBITAL_TRACK_DISABLE="1")
    out = run_cli(home, "start", "--title", "x", env=env)
    assert out.returncode == 0 and model.valid_id(out.stdout.strip())
    assert run_cli(home, "done", "whatever", env=env).stdout == "ok\n"
    assert app.store.items == []


def test_rejected_command_warns_but_exits_0(home, app):
    out = run_cli(home, "update", "unknown-id", "--note", "x")
    assert out.returncode == 0 and out.stdout == "ok\n" and "rejected the command (400)" in out.stderr
    assert run_cli(home, "update", "unknown-id", "--note", "x", "--quiet").stderr == ""


def test_queues_in_the_inbox_when_the_app_is_down(home):
    out = run_cli(home, "start", "--title", "Offline")
    assert out.returncode == 0 and "queued in" in out.stderr
    queued = list((home / "data" / "tracker" / "inbox").glob("*.json"))
    assert len(queued) == 1
    cmd = json.loads(queued[0].read_text())
    assert cmd["op"] == "start" and cmd["title"] == "Offline" and cmd["id"] == out.stdout.strip()
    assert model.parse_command(cmd)["sentAt"].endswith("Z")


def test_list_and_ping_without_the_app(home):
    assert run_cli(home, "list").returncode == 1  # no items.json yet
    folder = home / "data" / "tracker"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "items.json").write_text(json.dumps([{"id": "aa", "title": "Saved", "source": "agent", "agent": "grok",
                                                    "status": "active", "machine": "pc"}]))
    out = run_cli(home, "list")
    assert out.stdout == "aa  • Grok: Saved  @pc\n" and "not running" in out.stderr
    ping = run_cli(home, "ping")
    assert ping.returncode == 1 and json.loads(ping.stdout)["tracker"]["ok"] is False


def test_evidence_commands(home, app):
    assert run_cli(home, "evidence").stdout.startswith("Screenshot evidence: OFF.")
    out = run_cli(home, "evidence", "set", "Min 1")
    assert out.stdout.startswith("Screenshot evidence: MIN 1")
    assert app.settings.evidence == "min1" and app.settings.updated_by == "cli"
    as_json = json.loads(run_cli(home, "evidence", "--json").stdout)
    assert as_json["evidence"] == "min1" and as_json["label"] == "Min 1"
    started = run_cli(home, "start", "--title", "Visual")
    item_id = started.stdout.strip()
    assert f"orbital-track update {item_id} --media /abs/after.png" in started.stderr  # reminder on stderr only
    done = run_cli(home, "done", item_id)
    assert "has NO screenshots or videos" in done.stderr and done.stdout == "ok\n"
    app.stop()
    # App closed: `evidence set` writes settings.json itself.
    run_cli(home, "evidence", "all")
    on_disk = json.loads((home / "data" / "tracker" / "settings.json").read_text())
    assert on_disk["evidence"] == "all" and on_disk["updatedBy"] == "cli"


class FakeHub(BaseHTTPRequestHandler):
    received: list = []
    settings_posts: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        record = {"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(body)}
        (FakeHub.received if self.path.endswith("/command") else FakeHub.settings_posts).append(record)
        self._reply({"ok": True})

    def do_GET(self):
        self._reply({"ok": True, "settings": {"evidence": "all", "updatedAt": "2099-01-01T00:00:00Z", "updatedBy": "app"}})

    def _reply(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def hub():
    FakeHub.received, FakeHub.settings_posts = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_satellite_relays_to_the_hub(home, hub, png):
    write_config(home, free_port(), machine="laptop", extra={"system": {"role": "satellite"}, "hub_url": hub})
    env = cli_env(home, ORBITAL_HUB_TOKEN="test-token")
    out = run_cli(home, "start", "--agent", "claude", "--title", "Remote Work", "--media", str(png), env=env)
    item_id = out.stdout.strip()
    assert out.returncode == 0 and model.valid_id(item_id)
    # The local tracker isn't running: queued locally AND sent to the hub with the archive inline.
    sent = FakeHub.received[-1]
    assert sent["auth"] == "Bearer test-token" and sent["body"]["machine"] == "laptop" and sent["body"]["id"] == item_id
    media = sent["body"]["media"][0]
    assert base64.b64decode(media["inlineArchive"])[:3] == b"\xff\xd8\xff"
    assert media["archived"]["machine"] == "laptop"
    # The hub's newer evidence level wins (the fake hub says "all", changed in 2099).
    assert "Screenshot evidence: ALL" in out.stderr
    run_cli(home, "evidence", "set", "min1", env=env)
    assert FakeHub.settings_posts[-1]["body"] == {"by": "cli", "evidence": "min1"}
    ping = json.loads(run_cli(home, "ping", env=env).stdout)
    assert ping["hub"]["token"] == "set" and ping["hub"]["url"] == hub


def test_satellite_queues_for_a_hub_that_is_down(home):
    write_config(home, free_port(), machine="laptop",
                 extra={"system": {"role": "satellite"}, "hub_url": f"http://127.0.0.1:{free_port()}"})
    env = cli_env(home, ORBITAL_HUB_TOKEN="t")
    run_cli(home, "start", "--title", "One", env=env)
    run_cli(home, "start", "--title", "Two", env=env)
    outbox = home / "data" / "tracker" / "hub-outbox"
    assert len(list(outbox.glob("*.json"))) == 2 and (outbox / ".hub-down").exists()
