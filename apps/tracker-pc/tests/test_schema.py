"""items.json, settings.json, the inbox and the media layout match the Mac app byte for byte where
it matters (key names, optional fields left out, date format, Swift's pretty-print style), and the
Mac app's own readers (the Swift CLI, the hub's TrackerFeed) read what this tracker writes."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import SWIFT_CLI, run_cli
from tracker_pc import model
from tracker_pc.config import OrbitalConfig, TrackerPaths
from tracker_pc.jsonlog import JSONLineLogger
from tracker_pc.store import TrackerStore

ITEM_REQUIRED = {"id", "title", "details", "source", "colorIndex", "status", "createdAt", "updatedAt",
                 "dismissed", "processing", "notes", "media", "subagents"}
ITEM_OPTIONAL = {"transcript", "agent", "agentLabel", "machine", "workspace", "completedAt", "completedBy", "summary",
                 "progress", "usage"}
SUB_REQUIRED = {"id", "agent", "title", "details", "status", "createdAt", "updatedAt", "notes", "media"}
SUB_OPTIONAL = {"agentLabel", "machine", "completedAt", "summary", "progress", "usage"}
MEDIA_REQUIRED = {"id", "kind", "originalPath", "addedAt"}
MEDIA_OPTIONAL = {"archivePath", "caption", "originalBytes", "durationSeconds", "machine"}
DATE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def make_store(home: Path) -> TrackerStore:
    cfg = OrbitalConfig.load({"ORBITAL_HOME": str(home)})
    paths = TrackerPaths.from_config(cfg)
    paths.ensure_directories()
    return TrackerStore(cfg, paths, JSONLineLogger(paths.log_file), save_delay=0)


def populate(store: TrackerStore, png: Path) -> None:
    store.apply({"op": "start", "id": "item-1", "agent": "claude", "agentLabel": "Claude Code", "title": "Fix",
                 "details": "d", "workspace": "/w", "media": [{"path": str(png), "caption": "after"}]})
    store.apply({"op": "sub", "id": "sub-1", "parent": "item-1", "agent": "codex", "title": "S"})
    store.apply({"op": "update", "id": "item-1", "note": "n1"})
    store.apply({"op": "done", "id": "sub-1", "summary": "ok"})
    store.apply({"op": "done", "id": "item-1", "summary": "fin"})
    store.add_personal("Typed thing")
    store.wait_for_archives()
    store.save_now()


def check_keys(obj: dict, required: set, optional: set) -> None:
    keys = set(obj)
    assert required <= keys, required - keys
    assert keys <= required | optional, keys - required - optional
    assert all(v is not None for v in obj.values())  # Swift leaves nil optionals out


def test_items_json_shape_and_style(home, png):
    store = make_store(home)
    populate(store, png)
    text = (home / "data" / "tracker" / "items.json").read_text(encoding="utf-8")
    items = json.loads(text)
    assert [i["id"] for i in items][:1] == ["item-1"]
    for item in items:
        check_keys(item, ITEM_REQUIRED, ITEM_OPTIONAL)
        for d in ("createdAt", "updatedAt", "completedAt"):
            if d in item:
                assert DATE.match(item[d]), item[d]
        for sub in item["subagents"]:
            check_keys(sub, SUB_REQUIRED, SUB_OPTIONAL)
        for ref in item["media"]:
            check_keys(ref, MEDIA_REQUIRED, MEDIA_OPTIONAL)
        for note in item["notes"]:
            assert set(note) == {"at", "text"} and DATE.match(note["at"])
    first = items[0]
    assert first["status"] == "done" and first["completedBy"] == "claude" and first["source"] == "agent"
    assert first["media"][0]["caption"] == "after" and first["media"][0]["kind"] == "image"
    assert items[1]["source"] == "manual" and "agent" not in items[1]
    # Swift's JSONEncoder(.prettyPrinted, .sortedKeys, .withoutEscapingSlashes)
    assert text.startswith("[\n  {\n    \"agent\" : \"claude\",")
    assert "\\/" not in text
    assert text == model.dumps(items, pretty=True)
    assert not list((home / "data" / "tracker").glob(".*.tmp"))  # atomic writes leave nothing behind


def test_reads_swift_written_items(home):
    """Fractional seconds, Unix timestamps, missing fields and unknown keys all load, like Swift's decoder."""
    raw = [{"id": "old1", "createdAt": "2026-01-02T03:04:05.123Z", "status": "done", "extra": 1,
            "media": [{"id": "m", "kind": "video", "originalPath": "/x.mp4", "addedAt": 1767323045, "durationSeconds": 3}]},
           {"id": "old2", "title": "T", "createdAt": 1767323045000, "subagents": [{"id": "s"}], "dismissed": False}]
    (home / "data" / "tracker").mkdir(parents=True)
    (home / "data" / "tracker" / "items.json").write_text(json.dumps(raw))
    store = make_store(home)
    a, b = store.items
    assert a["title"] == "Untitled" and a["source"] == "manual" and a["createdAt"] == "2026-01-02T03:04:05Z"
    assert a["dismissed"] is True  # done items never come back onto the panel
    assert "extra" not in a and a["media"][0]["durationSeconds"] == 3.0 and a["media"][0]["addedAt"] == "2026-01-02T03:04:05Z"
    assert b["createdAt"] == "2026-01-02T03:04:05Z" and b["subagents"][0]["title"] == "Subtask"
    assert b["subagents"][0]["agent"] == "other" and b["status"] == "active"


def test_corrupt_items_json_is_backed_up(home):
    folder = home / "data" / "tracker"
    folder.mkdir(parents=True)
    (folder / "items.json").write_text("{not json")
    store = make_store(home)
    assert store.items == []
    assert list(folder.glob("items.corrupt-*.json"))


def test_settings_file_format(home):
    file = home / "settings.json"
    model.Settings("all", "2026-01-02T03:04:05Z", "tracker").save(file)
    assert file.read_text() == '{\n  "evidence" : "all",\n  "updatedAt" : "2026-01-02T03:04:05Z",\n  "updatedBy" : "tracker"\n}'
    file.write_text('{"evidence": "LOUD", "updatedBy": "OrbitalAgents-iOS"}')
    s = model.Settings.load(file)
    assert s.evidence == "off" and s.updated_by == "app"


def test_inbox_names_sort_in_write_order(tmp_path):
    names = [model.inbox_name() for _ in range(50)]
    assert all(re.match(r"^\d{16}-\d{4}-[a-z2-9]{4}\.json$", n) for n in names)
    for n in names:
        (tmp_path / n).write_text("{}")
    (tmp_path / "1700000000000-old.json").write_text("{}")  # a 13-digit (milliseconds) name sorts first
    pending = [p.name for p in model.inbox_pending(tmp_path)]
    assert pending[0] == "1700000000000-old.json" and pending[1:] == names


def test_inbox_files_are_applied_in_order(home, app):
    inbox = home / "data" / "tracker" / "inbox"
    for body in ({"op": "start", "id": "q1", "title": "Queued"}, {"op": "update", "id": "q1", "note": "later"},
                 {"op": "done", "id": "q1"}):
        model.inbox_write(json.dumps(body).encode(), inbox)
    (inbox / "9999999999999999-0001-bad.json").write_text("{broken")
    assert app.scan_inbox() == 3
    item = app.store.item("q1")
    assert item["status"] == "done" and item["notes"][0]["text"] == "later"
    assert (inbox / "failed" / "9999999999999999-0001-bad.json").exists()
    assert not list(inbox.glob("*.json"))


def test_archive_relative_path_layout():
    rel = model_path = __import__("tracker_pc.media", fromlist=["x"]).archive_relative_path("it/em", "m!d")
    assert re.match(r"^media/\d{4}-\d\d/it_em-m_d\.jpg$", rel), model_path


def test_agent_names_normalise_like_swift():
    cases = {"Claude Code": "claude", "anthropic": "claude", "codex": "chatgpt", "OpenAI": "chatgpt", "gpt-5": "chatgpt",
             "xai": "grok", "Grok CLI": "grok", "antigravity": "gemini", "agy": "gemini", "": "other", "Echo": "echo"}
    for raw, want in cases.items():
        assert model.normalize_agent(raw) == want, raw


def test_retention(home):
    store = make_store(home)
    old = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.items = [
        model.normalize_item({"id": "ancient", "status": "done", "createdAt": old, "completedAt": old}),
        model.normalize_item({"id": "old-active", "status": "active", "createdAt": old}),
        model.normalize_item({"id": "fresh", "status": "done", "createdAt": model.now_iso()}),
    ]
    media = home / "data" / "tracker" / "media" / "2020-01"
    media.mkdir(parents=True)
    stale = media / "x-y.jpg"
    stale.write_bytes(b"\xff\xd8\xff")
    past = time.time() - 90 * 86_400
    import os

    os.utime(stale, (past, past))
    store.items[2]["media"] = [{"id": "y", "kind": "image", "originalPath": "/gone.png", "addedAt": old,
                                "archivePath": "media/2020-01/x-y.jpg"}]
    store.apply_retention()
    assert [i["id"] for i in store.items] == ["old-active", "fresh"]
    assert not stale.exists() and "archivePath" not in store.items[1]["media"][0]


# ---- the Mac app's own readers -------------------------------------------------------------


def test_hub_tracker_feed_reads_our_files(home, app, png):
    """orbital/hub/tracker.py (TrackerFeed) is how the hub and the phone see this machine's tracker."""
    pytest.importorskip("fastapi")
    from orbital.hub.tracker import TrackerFeed

    root = home / "data" / "tracker"
    feed = TrackerFeed("secret", root, port=0, run_dir=home / "run")
    # Commands relayed by the hub go to the port in run/tracker.json.
    body = json.dumps({"op": "start", "id": "via-hub", "agent": "claude", "title": "From Phone",
                       "media": [{"path": str(png)}]}).encode()
    answer = feed.command(body)
    assert answer["ok"] and answer["delivered"] == "app" and answer["result"]["id"] == "via-hub"
    app.store.wait_for_archives()
    app.store.save_now()
    item = feed.get("via-hub")
    assert item is not None
    summary = feed.summary(item)
    assert summary["agent_name"] == "Claude" and summary["machine"] == "test-pc"
    ref = item["media"][0]
    found = feed.media_file("via-hub", ref["id"], "thumb")
    assert found is not None and found[0].suffix == ".jpg"
    # The phone's evidence toggle goes through the same API.
    assert feed.set_evidence("all", by="app")["evidence"] == "all"
    assert app.settings.evidence == "all" and app.settings.updated_by == "app"
    # And a relayed command while the app is closed waits in our inbox.
    app.stop()
    queued = feed.command(json.dumps({"op": "update", "id": "via-hub", "note": "queued"}).encode())
    assert queued["delivered"] == "inbox"


swift = pytest.mark.skipif(not SWIFT_CLI.is_file(), reason="the Swift orbital-track isn't built (apps/tracker: swift build)")


@swift
def test_swift_cli_decodes_our_items_json(home, png):
    """The Mac CLI decodes items.json with the Swift TrackedItem model: if any type were off, it would fail."""
    store = make_store(home)
    populate(store, png)
    out = run_cli(home, "list", "--all", "--json", exe=[str(SWIFT_CLI)])
    assert out.returncode == 0, out.stderr
    ours = (home / "data" / "tracker" / "items.json").read_text()
    assert json.loads(out.stdout) == json.loads(ours)
    # Swift re-encoding what it decoded gives the very same bytes we wrote.
    assert out.stdout == ours + "\n"


@swift
def test_swift_cli_against_our_server(home, app, png):
    """The real Swift orbital-track drives this server exactly as it drives the Mac app."""
    sw = [str(SWIFT_CLI)]
    ping = run_cli(home, "ping", exe=sw)
    assert ping.returncode == 0 and json.loads(ping.stdout)["tracker"]["service"] == "orbital-tracker"
    start = run_cli(home, "start", "--agent", "claude", "--title", "Swift Job", "--media", str(png), exe=sw)
    item_id = start.stdout.strip()
    assert start.returncode == 0 and model.valid_id(item_id), start.stderr
    sub = run_cli(home, "sub", item_id, "--agent", "codex", "--title", "Swift Sub", exe=sw).stdout.strip()
    assert run_cli(home, "update", item_id, "--note", "progress", exe=sw).returncode == 0
    assert run_cli(home, "done", sub, "--summary", "sub done", exe=sw).returncode == 0
    run_cli(home, "evidence", "set", "all", exe=sw)
    assert app.settings.evidence == "all" and app.settings.updated_by == "cli"
    assert run_cli(home, "done", item_id, "--summary", "all done", exe=sw).returncode == 0
    item = app.store.item(item_id)
    assert item["status"] == "done" and item["subagents"][0]["summary"] == "sub done"
    assert item["media"][0]["archivePath"].startswith("media/")  # archived by the Swift CLI, accepted here
    listing = run_cli(home, "list", "--all", exe=sw).stdout
    assert f"{item_id}  ✓ Claude: Swift Job" in listing


@swift
def test_python_cli_output_matches_swift_cli(home, app):
    """Same stdout for the commands whose output agents parse or read."""
    run_cli(home, "start", "--agent", "grok", "--title", "Both", "--id", "both-1")
    for args in (["evidence"], ["evidence", "--json"], ["list"], ["list", "--all", "--json"], ["help"]):
        py = run_cli(home, *args)
        sw = run_cli(home, *args, exe=[str(SWIFT_CLI)])
        assert (py.returncode, py.stdout) == (sw.returncode, sw.stdout), args
    for level in ("min1", "all"):
        py = run_cli(home, "evidence", "set", level)
        sw = run_cli(home, "evidence", "set", level, exe=[str(SWIFT_CLI)])
        assert py.stdout == sw.stdout
        py = run_cli(home, "start", "--title", "Remind", "--id", "rem-py")
        sw = run_cli(home, "start", "--title", "Remind", "--id", "rem-sw", exe=[str(SWIFT_CLI)])
        assert py.stderr.replace("rem-py", "ID") == sw.stderr.replace("rem-sw", "ID")
