"""Progress bars: the shared rule (ShownProgress in apps/tracker/.../Models.swift), how the store keeps
`progress`, and the CLI's --progress on start/sub plus its nudge on updates without one."""

from __future__ import annotations

import json
import http.client

import pytest

from conftest import run_cli
from tracker_pc import model
from tracker_pc.ui import with_shown_progress


def shown(node: dict, subs: list[dict] | None = None) -> tuple[int, str, str] | None:
    p = model.shown_progress(node, subs)
    return None if p is None else (p["percent"], p["source"], p["label"])


# The rule

def test_done_is_100_whatever_else_is_set():
    assert shown({"status": "done", "progress": 20, "details": "- [ ] a"}) == (100, "done", "100%")


def test_reported_progress_wins_and_is_clamped():
    assert shown({"status": "active", "progress": 40, "details": "- [x] a\n- [ ] b"}) == (40, "reported", "40%")
    assert shown({"status": "active", "progress": 140}) == (100, "reported", "100%")
    assert shown({"status": "active", "progress": -5}) == (0, "reported", "0%")
    assert shown({"status": "active", "progress": 0}) == (0, "reported", "0%")


def test_checklist_estimate():
    details = "Plan\n- [x] one\n  * [X] two\n+ [ ] three\n\t- [ ] four\n-[x] not a box\n- x [ ] nor this\n1. [x] nor this"
    assert model.checklist(details) == (2, 4)
    assert shown({"status": "active", "details": details}) == (50, "checklist", "~50%")
    assert shown({"status": "active", "details": "- [x] a\n- [x] b\n- [ ] c"}) == (67, "checklist", "~67%")
    # all boxes ticked but not marked done: an estimate stops at 99
    assert shown({"status": "active", "details": "- [x] a\n- [x] b"}) == (99, "checklist", "~99%")
    assert model.checklist("no boxes here\n- plain bullet") is None


def test_subagent_average():
    subs = [{"status": "done", "progress": 10}, {"status": "active", "progress": 50}, {"status": "active"}]
    assert shown({"status": "active", "details": "no boxes"}, subs) == (50, "subagents", "~50%")
    assert shown({"status": "active"}, [{"status": "done"}]) == (99, "subagents", "~99%")
    # half rounds up like Swift's .rounded(), not Python's banker's rounding
    assert shown({"status": "active"}, [{"status": "active", "progress": 1}, {"status": "active", "progress": 0}]) == (1, "subagents", "~1%")


def test_nothing_to_show():
    assert model.shown_progress({"status": "active", "details": ""}, []) is None
    assert model.shown_progress({"status": "active"}) is None


def test_caption_and_estimate_flags():
    p = model.shown_progress({"status": "active", "details": "- [ ] a"})
    assert p["estimate"] is True and p["caption"] == "estimated from the task list"
    p = model.shown_progress({"status": "active", "progress": 5})
    assert p["estimate"] is False and p["caption"] == "reported by the agent"


def test_panel_snapshot_gets_shown_for_items_and_subagents():
    items = with_shown_progress([{"id": "a", "status": "active", "details": "",
                                  "subagents": [{"id": "s", "status": "active", "progress": 30, "details": ""}]}])
    assert items[0]["shown"]["label"] == "~30%" and items[0]["subagents"][0]["shown"]["label"] == "30%"


def test_items_json_keeps_progress():
    item = model.normalize_item({"id": "a", "progress": 55.4, "subagents": [{"id": "s", "progress": 120}]})
    assert item["progress"] == 55 and item["subagents"][0]["progress"] == 100
    assert "progress" not in model.normalize_item({"id": "b", "progress": "lots"})


# The store

def test_store_start_sub_update_done(app):
    store = app.store
    store.apply({"op": "start", "id": "p-1", "title": "Job", "progress": 12})
    store.apply({"op": "sub", "id": "s-1", "parent": "p-1", "title": "Helper", "progress": 33})
    store.apply({"op": "sub", "id": "s-2", "parent": "p-1", "title": "Other"})
    assert store.item("p-1")["progress"] == 12
    subs = store.item("p-1")["subagents"]
    assert subs[0]["progress"] == 33 and "progress" not in subs[1]
    store.apply({"op": "update", "id": "s-1", "progress": 70})
    store.apply({"op": "done", "id": "s-2"})
    subs = store.item("p-1")["subagents"]
    assert subs[0]["progress"] == 70 and subs[1]["progress"] == 100  # done sets 100 even when never reported
    store.apply({"op": "start", "id": "p-2", "title": "No figure"})
    store.apply({"op": "done", "id": "p-2"})
    assert store.item("p-2")["progress"] == 100


def test_progress_over_http(app):
    def post(path: str, body: dict) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
        conn.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        out = json.loads(resp.read())
        conn.close()
        return out

    assert post("/v1/command", {"op": "start", "id": "h-1", "title": "T", "progress": 25})["ok"]
    assert app.store.item("h-1")["progress"] == 25
    assert post("/v1/update/h-1", {"progress": 60})["ok"]
    assert app.store.item("h-1")["progress"] == 60
    bad = post("/v1/update/h-1", {"progress": "half"})
    assert bad["ok"] is False and "progress" in bad["error"]


# The CLI

NUDGE = "orbital-track: add --progress <0-100> to every update so the tracker's bar moves (e.g. --progress 40).\n"


def test_cli_start_and_sub_take_progress(home, app):
    out = run_cli(home, "start", "--title", "With bar", "--progress", "15%")
    item_id = out.stdout.strip()
    assert out.returncode == 0 and app.store.item(item_id)["progress"] == 15
    sub = run_cli(home, "sub", item_id, "--title", "Helper", "--progress", "42.5").stdout.strip()
    assert app.store.item(item_id)["subagents"][0]["id"] == sub
    assert app.store.item(item_id)["subagents"][0]["progress"] == 43
    for bad in ("120", "-1", "lots"):
        res = run_cli(home, "start", "--title", "x", "--progress", bad)
        assert res.returncode == 2 and "--progress takes a percent from 0 to 100" in res.stderr


def test_cli_nudges_updates_without_progress(home, app):
    item_id = run_cli(home, "start", "--title", "Nudge").stdout.strip()
    out = run_cli(home, "update", item_id, "--note", "step")
    assert out.returncode == 0 and out.stdout == "ok\n" and NUDGE in out.stderr
    out = run_cli(home, "note", item_id, "another", "step")
    assert out.returncode == 0 and NUDGE in out.stderr
    out = run_cli(home, "update", item_id, "--note", "step", "--progress", "40")
    assert out.returncode == 0 and out.stdout == "ok\n" and NUDGE not in out.stderr
    assert app.store.item(item_id)["progress"] == 40
    out = run_cli(home, "update", item_id, "--note", "quiet", "--quiet")
    assert out.stdout == "ok\n" and out.stderr == ""
    out = run_cli(home, "done", item_id, "--summary", "fin")
    assert out.stdout == "ok\n" and NUDGE not in out.stderr
    assert app.store.item(item_id)["progress"] == 100


def test_help_marks_progress_required(home):
    out = run_cli(home, "help")
    assert "--progress is REQUIRED at every update" in out.stdout
    assert '[--details "markdown"] [--progress 0-100]' in out.stdout


@pytest.mark.parametrize("raw,expected", [("0", 0), ("100", 100), ("99.5", 100), ("7%", 7), (" 8 ", 8)])
def test_cli_progress_parsing(home, app, raw, expected):
    item_id = run_cli(home, "start", "--title", "Parse").stdout.strip()
    assert run_cli(home, "update", item_id, "--progress", raw).returncode == 0
    assert app.store.item(item_id)["progress"] == expected
