"""Every example feed: builds a section the hub accepts, pushes it, raises the right alerts."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import httpx
import pytest

from feeds_helpers import (ATOM, BLOCK_TYPES, EXAMPLES, RSS, FakeHub, all_blocks, feed, github_handler, mock_http,
                           validate, weather_payload)
from orbital.core.paths import REPO_ROOT
from orbital.scheduler.scheduler import load_registry


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


# --------------------------------------------------------------------------- machine health


def test_machine_health_pushes_a_valid_section(home, capsys):
    mod = feed("machine_health")
    hub = FakeHub()
    assert mod.main(["--home", str(home)], client=hub) == 0
    assert hub.sections and hub.sections[0]["id"] == "machine-health"
    section = hub.sections[0]
    validate(section)
    assert section["space"] == "home-lab" and section["space_title"] == "Home Lab"
    types = {blk["type"] for blk in section["blocks"]}
    assert {"metrics", "chart", "key_value", "series"} <= types
    line = last_line(capsys)
    assert line.startswith("machine-health pushed=1 machine=lab-box")
    # History grows run over run (the chart's points).
    mod.main(["--home", str(home)], client=hub)
    chart = next(blk for blk in hub.sections[1]["blocks"] if blk["id"] == "load-24h")
    assert len(chart["series"][0]["points"]) == 2


def test_machine_health_disk_alert(home):
    mod = feed("machine_health")
    sample = {"machine": "lab-box", "os": "Linux 6", "python": "3.12", "cpus": 4, "load": [0.5, 0.4, 0.3],
              "memory": {"total": 8e9, "used": 4e9, "percent": 50.0}, "uptime_s": 7200, "time": time.time(),
              "disks": [{"mount": "/", "total": 100e9, "used": 97e9, "free": 3e9, "percent": 97.0}]}
    state = mod.remember({}, sample)
    section, alerts = mod.build(sample, state)
    validate(section)
    assert section["blocks"][0]["type"] == "callout" and section["blocks"][0]["tone"] == "warning"
    assert alerts and alerts[0]["title"] == "Disk almost full on lab-box" and alerts[0]["level"] == "warning"
    assert alerts[0]["id"].startswith("disk-low-lab-box-root-")
    series = next(blk for blk in section["blocks"] if blk["type"] == "series")
    assert series["kind"] == "health" and series["product"] == "lab-box"


# --------------------------------------------------------------------------- website uptime


def test_website_uptime_alerts_on_down_and_back_up(home, monkeypatch, capsys):
    mod = feed("website_uptime")
    broken = {"down": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "flaky.example" and broken["down"]:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, text="ok")

    mock_http(monkeypatch, mod, handler)
    hub = FakeHub()
    args = ["--home", str(home), "--url", "https://good.example", "--url", "https://flaky.example"]
    assert mod.main(args, client=hub) == 0
    first = hub.sections[-1]
    validate(first)
    assert first["blocks"][0]["id"] == "down-now" and first["blocks"][0]["tone"] == "danger"
    assert [a["title"] for a in hub.alerts] == ["flaky.example is down"] and hub.alerts[0]["level"] == "error"
    assert "uptime pushed=1 sites=2 up=1 down=1 alerts=1" in last_line(capsys)
    broken["down"] = False
    mod.main(args, client=hub)
    assert hub.alerts[-1]["title"] == "flaky.example is back up"
    second = hub.sections[-1]
    validate(second)
    assert all(blk["id"] != "down-now" for blk in second["blocks"])
    timeline = next(blk for blk in second["blocks"] if blk["type"] == "timeline")
    assert len(timeline["items"]) == 2
    chart = next(blk for blk in second["blocks"] if blk["id"] == "latency")
    assert [s["name"] for s in chart["series"]] == ["good.example", "flaky.example"]
    mod.main(args, client=hub)
    assert len(hub.alerts) == 2, "no new alert while nothing changes"


# --------------------------------------------------------------------------- github


def test_github_repo_nodes_tabs_and_release_alert(home, monkeypatch, capsys):
    mod = feed("github_repo")
    tags = ["v1.0"]
    limited = {"on": False}
    mock_http(monkeypatch, mod, github_handler(tags, limited))
    hub = FakeHub()
    args = ["--home", str(home), "--repo", "example/widgets"]
    assert mod.main(args, client=hub) == 0
    section = hub.sections[-1]
    validate(section)
    assert section["layout"] == "sidebar" and section["space"] == "projects"
    node = section["nodes"][0]
    assert node["id"] == "example-widgets" and [t["id"] for t in node["tabs"]] == ["overview", "releases", "issues"]
    issues = next(blk for blk in node["tabs"][2]["blocks"] if blk["type"] == "table")
    assert [row[0] for row in issues["rows"]] == ["#9"], "pull requests are not issues"
    assert not hub.alerts, "the first run only learns the releases"
    assert "github pushed=1 repos=1 stars=1234 api=ok" in last_line(capsys)
    tags.insert(0, "v1.1")
    mod.main(args, client=hub)
    assert [a["title"] for a in hub.alerts] == ["example/widgets v1.1 released"]
    limited["on"] = True
    mod.main(args, client=hub)
    stale = hub.sections[-1]
    validate(stale)
    assert stale["nodes"][0]["tabs"][0]["blocks"][0]["id"] == "stale"
    out = capsys.readouterr()
    assert "api=unavailable" in out.out and "rate limit" in out.err


def test_github_repo_rejects_bad_names(home):
    assert feed("github_repo").main(["--home", str(home), "--repo", "not a repo"], client=FakeHub()) == 2


# --------------------------------------------------------------------------- weather


def test_weather_section_and_rain_alert(home, monkeypatch, capsys):
    mod = feed("weather")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=weather_payload(rain_tomorrow=90))

    mock_http(monkeypatch, mod, handler)
    hub = FakeHub()
    assert mod.main(["--home", str(home), "--place", "Testville", "--lat", "1.5", "--lon", "2.5", "--units", "imperial"], client=hub) == 0
    assert seen["latitude"] == "1.5" and seen["temperature_unit"] == "fahrenheit"
    section = hub.sections[-1]
    validate(section)
    assert section["title"] == "Weather · Testville" and section["space"] == "daily"
    assert section["icon"] == "cloud.fill"
    temp = next(blk for blk in section["blocks"] if blk["id"] == "temp-24h")
    assert len(temp["series"][0]["points"]) == 24 and temp["series"][0]["points"][0]["x"] == "09:00"
    week = next(blk for blk in section["blocks"] if blk["id"] == "week")
    assert len(week["rows"]) == 7 and week["rows"][1][1] == "Rain"
    assert hub.alerts[0]["title"] == "Rain likely tomorrow in Testville"
    assert hub.alerts[0]["id"] == "weather-testville-rain-2026-10-18"
    assert "weather pushed=1 temp=14.2 forecast=ok alerts=1" in last_line(capsys)


# --------------------------------------------------------------------------- rss


def test_rss_headlines_rss_atom_and_a_broken_feed(home, monkeypatch, capsys):
    mod = feed("rss_headlines")
    extra = {"items": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "news.example":
            body = RSS.replace("</channel>", extra["items"] + "</channel>")
            return httpx.Response(200, text=body)
        if request.url.host == "blog.example":
            return httpx.Response(200, text=ATOM)
        return httpx.Response(500, text="nope")

    mock_http(monkeypatch, mod, handler)
    hub = FakeHub()
    args = ["--home", str(home), "--feed", "https://news.example/rss", "--feed", "https://blog.example/atom",
            "--feed", "https://broken.example/feed"]
    assert mod.main(args, client=hub) == 0
    section = hub.sections[-1]
    validate(section)
    assert section["layout"] == "tabs" and [t["id"] for t in section["tabs"]] == ["all", "example-news", "example-blog"]
    everything = section["tabs"][0]["blocks"]
    assert everything[0]["type"] == "callout" and "broken.example" in everything[0]["text"]
    timeline = next(blk for blk in everything if blk["type"] == "timeline")
    assert [i["title"] for i in timeline["items"]] == ["Hello Atom", "Second [story]", "First story"]
    links = section["tabs"][1]["blocks"][0]["items"]
    assert links[1] == "[Second (story)](https://news.example/2)"
    out = capsys.readouterr()
    assert "rss pushed=1 feeds=2 items=3 new=0 errors=1" in out.out
    extra["items"] = "<item><title>Breaking</title><link>https://news.example/3</link><guid>n3</guid></item>"
    mod.main(args, client=hub)
    assert "new=1" in capsys.readouterr().out


def test_rss_parse_rejects_non_feeds():
    mod = feed("rss_headlines")
    with pytest.raises(ValueError):
        mod.parse("<html><body>hi</body></html>", "https://x.example")


# --------------------------------------------------------------------------- hello


def test_hello_uses_every_block_type_and_answers_taps(home, capsys):
    mod = feed("hello_blocks")
    hub = FakeHub(actions=[
        {"id": "a1", "type": "hello.ping", "item_id": "ping", "value": {}},
        {"id": "a2", "type": "task.set_done", "item_id": "tap-ping", "value": {"done": True}},
        {"id": "a3", "type": "task.add", "value": {"title": "Water the plants"}},
        {"id": "a4", "type": "garden.water", "value": {}},
    ])
    assert mod.main(["--home", str(home)], client=hub) == 0
    section = hub.sections[-1]
    validate(section)
    assert {blk["type"] for blk in all_blocks(section)} == BLOCK_TYPES
    assert section["space"] == "demo" and section["layout"] == "tabs"
    assert [r["status"] for r in hub.results] == ["applied", "applied", "applied", "failed"]
    tasks = next(blk for blk in all_blocks(section) if blk["type"] == "tasks")["items"]
    assert any(t["title"] == "Water the plants" for t in tasks)
    assert next(t for t in tasks if t["id"] == "tap-ping")["done"] is True
    ledger = next(blk for blk in all_blocks(section) if blk["type"] == "ledger")
    assert all(day["amount"] == 0 for day in ledger["days"]), "the demo never adds to real money totals"
    assert hub.uploads and next(blk for blk in all_blocks(section) if blk["type"] == "image")["src"] == "/v1/media/abc123"
    assert hub.patches[0]["tab_id"] == "numbers" and hub.patches[0]["block"]["id"] == "tiles"
    assert [a["title"] for a in hub.alerts] == ["Hello from Orbital Agents"]
    line = last_line(capsys)
    assert line.startswith("hello pushed=1") and "actions=3" in line
    mod.main(["--home", str(home)], client=hub)
    assert len(hub.alerts) == 1, "the welcome alert is sent once"
    assert len(hub.uploads) == 1, "the image is uploaded once and reused"


# --------------------------------------------------------------------------- scripts and timetable


@pytest.mark.parametrize("script", ["machine_health.py", "hello_blocks.py"])
def test_scripts_run_standalone_in_dry_run(home, script):
    proc = subprocess.run([sys.executable, str(EXAMPLES / script), "--dry-run", "--home", str(home)],
                          capture_output=True, text=True, timeout=60, cwd=str(home))
    assert proc.returncode == 0, proc.stderr
    body = proc.stdout[: proc.stdout.rindex("}") + 1]
    validate(json.loads(body)["section"])


def test_feed_failure_exits_non_zero_with_a_readable_line(home, monkeypatch, capsys):
    mod = feed("weather")
    kit = feed("feedkit")

    def boom(*a, **kw):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(kit, "fetch_json", boom)
    assert kit.run_feed(lambda argv: mod.main(["--home", str(home)], client=FakeHub())) == 1
    assert "ConnectError: no network" in capsys.readouterr().err


def test_jobs_example_is_a_valid_timetable(home):
    path = REPO_ROOT / "feeds" / "jobs.example.json"
    meta, jobs = load_registry(path, home)
    ids = {job.id for job in jobs}
    assert {"machine-health", "website-uptime", "github", "weather", "headlines", "hello"} <= ids
    for job in jobs:
        assert job.argv[0] == sys.executable
        script = REPO_ROOT / job.raw["argv"][1]
        assert script.is_file(), f"{job.id}: {script} is missing"
        assert job.cwd == str(REPO_ROOT)
        assert job.group in {g["id"] for g in meta["groups"]}
        assert job.timeout < job.default_every
    spaces = {g["id"] for g in meta["groups"]}
    assert {"home-lab", "daily", "projects", "demo"} <= spaces
