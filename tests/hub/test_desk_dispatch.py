"""The Agent tab as a dispatcher: the project catalog, which agent on which machine, and handing a job
to the next agent when its engine runs out."""

from __future__ import annotations

import asyncio
import json

from hub_helpers import JOIN, SATELLITE, make_client

from orbital.hub import desk_tasks
from orbital.hub.desk import Projects
from orbital.hub.desk_tasks import TaskRunner


def join(client, machine: str, agents: list[str], port: int = 17777, token: str = SATELLITE) -> dict:
    body = {"name": machine, "machine": machine, "host": f"{machine}.example", "ip": "127.0.0.1", "port": port,
            "os": "macos", "satellite_token": token, "version": "0.1.0",
            "capabilities": {"agents": agents, "browser": False, "tracker": False}}
    res = client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_the_catalog_lists_project_folders_with_curated_names(tmp_path):
    root = tmp_path / "projects"
    for name in ("alpha", "beta", "secret"):
        (root / name).mkdir(parents=True)
    (root / "alpha" / "README.md").write_text("# Alpha\n\nThe alpha app for tracking things.\n", encoding="utf-8")
    (root / "beta" / "AGENTS.md").write_text("# Beta tools\nScripts.\n", encoding="utf-8")
    curated = tmp_path / "projects.json"
    curated.write_text(json.dumps({"projects": {"alpha": {"name": "Alpha App", "aliases": ["the alpha"], "kind": "ios"}},
                                   "hidden": ["secret"]}), encoding="utf-8")
    Projects._cache.clear()
    projects = Projects([], root, curated)
    assert [r["name"] for r in projects.rows] == ["Alpha App", "beta"], "curated first, hidden left out"
    assert projects.resolve("the alpha")["path"] == str(root / "alpha")
    assert projects.resolve("~/projects/beta")["name"] == "beta"
    assert projects.resolve("secret") is None
    block = projects.prompt_block()
    assert "Alpha App" in block and "[ios]" in block and "Beta tools" in block
    configured = Projects([{"name": "Site", "path": "~/code/site"}], root, curated)
    assert [r["name"] for r in configured.rows] == ["Site"], "config.toml projects win over the catalog"


def test_workers_go_to_installed_agents_and_skip_engines_that_are_out(ports):
    client, hub = make_client()
    join(client, "test-hub", ["claude", "codex", "echo"])
    desk = hub.desk
    task = {"agent_wanted": None, "machine": None, "engines_down": []}
    assert desk.choose_worker(task) == {"agent": "claude", "satellite": None}, "grok isn't installed here"
    desk.provider.health.fail("claude", "402 Payment Required: you've hit your usage limit")
    assert desk.choose_worker(task)["agent"] == "codex", "a Claude that ran out is skipped"
    assert "codex" in desk.machines_block() and "claude (out right now)" in desk.machines_block()
    assert "echo" not in desk.machines_block()
    wanted = desk.choose_worker({**task, "agent_wanted": "gemini"})
    assert "isn't installed" in wanted["wait"]
    assert desk.choose_worker({**task, "engines_down": ["codex"]})["wait"].startswith("No agent on test-hub")


def test_a_named_machine_gets_the_job(ports):
    client, hub = make_client()
    join(client, "test-hub", ["claude"])
    join(client, "laptop", ["grok", "claude"], port=18888, token="laptop-satellite-token-000")
    desk = hub.desk
    pick = desk.choose_worker({"agent_wanted": None, "machine": "Laptop", "engines_down": []})
    assert pick == {"agent": "grok", "satellite": "laptop"}
    url, token = desk.locate_machine("laptop")
    assert url == "http://127.0.0.1:18888" and token == "laptop-satellite-token-000"
    assert "no machine called" in desk.choose_worker({"agent_wanted": None, "machine": "nas", "engines_down": []})["wait"].lower()


def test_a_job_its_engine_cut_off_moves_to_the_next_agent(tmp_path, monkeypatch):
    started: list[dict] = []

    async def fake_call(url, method, path, token, *, json_body=None, timeout=10.0):
        started.append({"url": url, "path": path, **(json_body or {})})
        return {"job_id": f"j{len(started)}"}

    monkeypatch.setattr(desk_tasks, "call", fake_call)
    down: list[str] = []

    def choose(task):
        for agent in ("grok", "claude"):
            if agent not in (task.get("engines_down") or []):
                return {"agent": agent, "satellite": None}
        return {"wait": "nothing can run"}

    def engine_down(agent, text):
        if "usage limit" in text:
            down.append(agent)
            return True
        return False

    async def noop(_):
        return None

    runner = TaskRunner(tmp_path, satellite_url=lambda: "http://127.0.0.1:1", satellite_token=lambda: "tok",
                        agent=lambda: "claude", on_change=noop, on_finish=noop, choose=choose, engine_down=engine_down)

    async def go():
        task = await runner.create(None, None, "Fix login", "Fix the login bug.", "fix the login bug", effort="xhigh")
        assert task["agent"] == "grok" and task["status"] == "running"
        assert "Effort: extra high" in started[0]["prompt"]
        await runner._finish(task, {"status": "failed", "output_tail": "Error: you've hit your usage limit"})
        return task

    task = asyncio.run(go())
    assert down == ["grok"]
    assert task["status"] == "running" and task["agent"] == "claude" and task["engines_down"] == ["grok"]
    assert task["runs"] == 2 and started[1]["agent"] == "claude"
    assert "An earlier run already worked on this" in started[1]["prompt"]


def test_an_ordinary_failure_is_not_handed_on(tmp_path, monkeypatch):
    async def fake_call(url, method, path, token, *, json_body=None, timeout=10.0):
        return {"job_id": "j1"}

    monkeypatch.setattr(desk_tasks, "call", fake_call)

    async def noop(_):
        return None

    runner = TaskRunner(tmp_path, satellite_url=lambda: "http://127.0.0.1:1", satellite_token=lambda: "tok",
                        agent=lambda: "claude", on_change=noop, on_finish=noop,
                        choose=lambda task: {"agent": "grok", "satellite": None}, engine_down=lambda agent, text: False)

    async def go():
        task = await runner.create(None, None, "Build", "Build it.", "build it")
        await runner._finish(task, {"status": "failed", "exit_code": 1, "output_tail": "tests failed"})
        return task

    task = asyncio.run(go())
    assert task["status"] == "failed" and task["engines_down"] == []


def test_the_report_is_the_workers_closing_message_not_its_progress_chatter():
    from orbital.hub.desk_tasks import final_report

    streamed = ("I'll look into it. Starting the tracker.I'll refine the count, then finish."
                "**There are 130 Swift files.**\n\n### Details\n- 89 in the app")
    assert final_report(streamed).startswith("**There are 130 Swift files.**")
    clean = "The site is fixed. It deploys on the next push."
    assert final_report(clean) == clean


def test_each_worker_is_told_to_do_only_its_own_part(tmp_path):
    async def noop(_):
        return None

    runner = TaskRunner(tmp_path, satellite_url=lambda: "x", satellite_token=lambda: "", agent=lambda: "claude",
                        on_change=noop, on_finish=noop)
    prompt = runner._prompt({"id": "t1", "title": "Count files", "cwd": None, "origin": "count files and size my downloads",
                             "brief": "Count the Swift files.", "effort": "medium", "runs": 0, "queue": []})
    assert "Your job is only the brief below" in prompt and "other agents may be doing its other parts" in prompt
    assert "Effort: medium" in prompt
