"""The desk channel: agents the desk starts report progress, findings, answers and questions, and
read what the person adds while they run. The live state the desk model sees carries all of it."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from contextlib import redirect_stderr, redirect_stdout

from hub_helpers import AUTH, Server, fake_satellite, make_client, write_config
from orbital.cli.commands import desk as desk_cli
from orbital.hub.desk_engines import LiveDiff, live_blocks, turn_prompt
from orbital.hub.desk_tasks import TaskRunner
from test_hub_desk import FakeModel, lines

TASK = '>>> {"type":"task","project":"","title":"Count sent emails","brief":"Count the emails the newsletter tool sent today.","effort":"quick"}\n'


def start_task(client, hub, reply: str = TASK, text: str = "How many emails went out today?") -> dict:
    hub.desk._fixed_provider = FakeModel(f"On it, checking now.\n{reply}")
    rows = lines(client.post("/v1/desk/say", headers=AUTH, json={"text": text}))
    assert any(r["type"] == "event" and r["message"]["kind"] == "task_started" for r in rows), rows
    return hub.desk.runner.get("t1")


def channel(task: dict) -> dict:
    return {"Authorization": f"Bearer {task['channel_token']}"}


def test_the_agent_gets_its_own_channel_and_nobody_else_can_use_it(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port)
        client, hub = make_client()
        with client:
            task = start_task(client, hub)
            run = state["runs"][0]
            assert run["desk"] == {"task": "t1", "token": task["channel_token"]} and len(task["channel_token"]) >= 24
            assert "orbital desk answer" in run["prompt"] and "Effort: quick" in run["prompt"]
            public = client.get("/v1/desk/tasks/t1", headers=AUTH).json()
            assert "channel_token" not in json.dumps(public), "the token never reaches the apps"
            assert "channel_token" not in json.dumps(client.get("/v1/desk", headers=AUTH).json())

            body = {"kind": "note", "text": "Found the send log.", "progress": 30}
            assert client.post("/v1/desk/tasks/t1/report", json=body).status_code == 401
            assert client.post("/v1/desk/tasks/t1/report", json=body, headers={"Authorization": "Bearer wrong-token-0123456789"}).status_code == 401
            # Another task's token doesn't open this one.
            hub.desk._fixed_provider = FakeModel(TASK.replace("Count sent emails", "Second job"))
            client.post("/v1/desk/say", headers=AUTH, json={"text": "and another"})
            other = hub.desk.runner.get("t2")
            assert client.post("/v1/desk/tasks/t1/report", json=body, headers=channel(other)).status_code == 401
            assert client.post("/v1/desk/tasks/t1/report", json={"kind": "shout", "text": "x"}, headers=channel(task)).status_code == 400

            res = client.post("/v1/desk/tasks/t1/report", json=body, headers=channel(task))
            assert res.status_code == 200 and res.json()["progress"] == 30 and res.json()["inbox"] == []
            public = client.get("/v1/desk/tasks/t1", headers=AUTH).json()
            assert public["progress"] == 30 and public["last_note"] == "Found the send log."
            assert public["notes"][-1]["text"] == "Found the send log."


def test_an_answer_is_spoken_at_once_and_the_finish_does_not_repeat_it(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port)
        client, hub = make_client()
        with client:
            task = start_task(client, hub)
            client.post("/v1/desk/tasks/t1/report", headers=channel(task),
                        json={"kind": "finding", "text": "22 emails for the game newsletter, sent 09:10"})
            res = client.post("/v1/desk/tasks/t1/report", headers=channel(task),
                              json={"kind": "answer", "text": "**37 emails** went out today: 22 for the game and 15 for the site. Details in [the log](https://example.com/log)."})
            assert res.status_code == 200
            thread = client.get("/v1/desk/messages", headers=AUTH).json()["messages"]
            answer = next(m for m in thread if m.get("kind") == "task_answer")
            assert answer["speak"].startswith("37 emails went out today") and "https" not in answer["speak"] and "*" not in answer["speak"]
            alerts = client.get("/v1/alerts", headers=AUTH).json()["alerts"]
            assert any(a["title"] == "Answer: Count sent emails" for a in alerts)
            # The same answer again is not news.
            client.post("/v1/desk/tasks/t1/report", headers=channel(task), json={"kind": "answer", "text": "**37 emails** went out today: 22 for the game and 15 for the site. Details in [the log](https://example.com/log)."})
            assert sum(1 for m in client.get("/v1/desk/messages", headers=AUTH).json()["messages"] if m.get("kind") == "task_answer") == 1

            prompt = hub.desk.system_prompt()
            assert "answer so far: **37 emails** went out today" in prompt and "found so far: 22 emails for the game" in prompt

            state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": "37 emails went out today.\n\nDetails: 22 game, 15 site."})
            client.portal.call(hub.desk.runner.tick)
            done = next(m for m in client.get("/v1/desk/messages", headers=AUTH).json()["messages"] if m.get("kind") == "task_done")
            assert "speak" not in done, "the answer was already read out"
            assert next(a for a in client.get("/v1/alerts", headers=AUTH).json()["alerts"] if a["id"] == "desk-t1-1")["interruption_level"] == "passive"


def test_a_question_reaches_the_person_and_their_reply_reaches_the_running_agent(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port)
        client, hub = make_client()
        with client:
            task = start_task(client, hub)
            client.post("/v1/desk/tasks/t1/report", headers=channel(task), json={"kind": "ask", "text": "Count the test sends too?"})
            asked = next(m for m in client.get("/v1/desk/messages", headers=AUTH).json()["messages"] if m.get("kind") == "task_question")
            assert asked["speak"] == "The Count sent emails agent asks: Count the test sends too?"
            assert 'ASKS THE USER: "Count the test sends too?"' in hub.desk.system_prompt()

            hub.desk._fixed_provider = FakeModel('Got it, I told it to skip those.\n>>> {"type":"followup","task":"t1","message":"No, skip the test sends."}\n')
            rows = lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "No, skip the test ones"}))
            event = next(r["message"] for r in rows if r["type"] == "event")
            assert event["kind"] == "task_followup" and event["speak"] == "Passed that to the Count sent emails agent."
            assert client.get("/v1/desk/tasks/t1", headers=AUTH).json()["inbox_unread"] == 1
            # The app can look without taking it from the agent.
            assert client.get("/v1/desk/tasks/t1/inbox", headers=AUTH).json()["messages"][0]["delivered_at"] is None

            res = client.post("/v1/desk/tasks/t1/report", headers=channel(task), json={"kind": "note", "text": "Counting.", "progress": 60})
            inbox = res.json()["inbox"]
            assert [m["text"] for m in inbox] == ["No, skip the test sends."] and inbox[0]["kind"] == "reply"
            assert hub.desk.runner.get("t1")["queue"] == [], "read by the agent, so it won't start another run"
            assert hub.desk.runner.get("t1")["question"] is None
            assert client.get("/v1/desk/tasks/t1/inbox", headers=channel(task)).json()["messages"] == []

            state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": "35 emails went out, test sends skipped."})
            client.portal.call(hub.desk.runner.tick)
            assert len(state["runs"]) == 1 and hub.desk.runner.get("t1")["status"] == "done"


def test_an_unread_followup_still_starts_the_next_run(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port)
        client, hub = make_client()
        with client:
            start_task(client, hub)
            client.portal.call(hub.desk.runner.followup, "t1", "Also count the bounces.")
            state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": "37 went out."})
            client.portal.call(hub.desk.runner.tick)
            assert len(state["runs"]) == 2 and "Also count the bounces." in state["runs"][1]["prompt"]
            assert hub.desk.runner.get("t1")["inbox"][0]["delivered_at"], "the new run's prompt carried it"
            # The new run is a new satellite job with its own tracker item; the desk follows that one now.
            client.portal.call(hub.desk.runner.tick)
            task = hub.desk.runner.get("t1")
            assert task["tracker_id"] == "trj2" and set(task["tracker_ids"]) == {"trj1", "trj2"}
            assert hub.desk.task_for_tracker("trj1")["id"] == "t1" and {"trj1", "trj2"} <= hub.desk.tracker_ids()


def test_the_live_state_carries_full_reports_and_live_tracker_progress(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port)
        client, hub = make_client()
        with client:
            start_task(client, hub)
            task = hub.desk.runner.get("t1")
            # orbital-track update on the job's tracker item reaches the desk while it runs.
            items = {"trj1": {"id": "trj1", "status": "active", "progress": 55, "updatedAt": "2999-01-01T00:00:00Z",
                              "notes": [{"at": "2999-01-01T00:00:00Z", "text": "Read the send log for today."}]}}
            hub.desk.runner.tracker = items.get
            client.portal.call(hub.desk.runner.tick)
            assert task["progress"] == 55 and task["last_note"] == "Read the send log for today."
            assert "[running, 55%" in hub.desk.system_prompt() and "latest notes: Read the send log for today." in hub.desk.system_prompt()

            # A long report: the finish reads the job's whole log, not the last 4,000 characters.
            draft = "Here is the newsletter draft that would go out.\n\nSubject: Spring update\n\n" + ("Body line of the draft. " * 300) + "\n\nSign-off line."
            state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": draft[-4000:]})
            state["logs"] = {"j1": draft}
            client.portal.call(hub.desk.runner.tick)
            assert task["summary"].startswith("Here is the newsletter draft") and task["summary"].endswith("Sign-off line.")
            prompt = hub.desk.system_prompt()
            assert "    Subject: Spring update" in prompt, "a fresh report is in the live state in full"
            assert "Here is the newsletter draft" in prompt
            done = next(m for m in client.get("/v1/desk/messages", headers=AUTH).json()["messages"] if m.get("kind") == "task_done")
            assert "newsletter draft" in done["text"], "the spoken result starts at the report's start, not mid-word"


def test_the_inbox_waits_for_a_message():
    async def noop(_):
        return None

    async def go(tmp):
        from pathlib import Path
        runner = TaskRunner(Path(tmp), satellite_url=lambda: "x", satellite_token=lambda: "", agent=lambda: "claude", on_change=noop, on_finish=noop)
        task = {"id": "t1", "status": "running", "queue": [], "inbox": []}
        runner.tasks.append(task)
        waiting = asyncio.create_task(runner.wait_inbox(task, 5))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        task["queue"].append("Use the staging account.")
        runner._post_inbox(task, "Use the staging account.", "reply")
        got = await asyncio.wait_for(waiting, 2)
        assert [m["text"] for m in got] == ["Use the staging account."] and task["queue"] == []
        assert await runner.wait_inbox(task, 0) == []

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(go(tmp))


def test_a_warm_session_is_sent_only_what_changed():
    first = "Now: Monday 09:00 (UTC).\nUnread alerts (2), newest first:\n- Disk full (just now)\n- Site down (5 min ago)\nDesk tasks running or queued: none."
    second = "Now: Monday 09:01 (UTC).\nUnread alerts (2), newest first:\n- Disk full (just now)\n- Site down (5 min ago)\nDesk tasks running or queued (progress):\n- t1 [running, 40%] Fix tests\n  latest notes: two left"
    assert [k for k, _ in live_blocks(second)] == ["now", "unread alerts", "desk tasks running or queued"]
    diff = LiveDiff()
    full = diff.render(first)
    assert full.startswith("Live state right now") and "Site down" in full
    changed = diff.render(second)
    assert "Site down" not in changed and "Now: Monday 09:01" in changed and "t1 [running, 40%] Fix tests" in changed and "two left" in changed
    assert "(nothing changed)" in diff.render(second.replace("09:01", "09:01"))
    gone = diff.render("Now: Monday 09:02 (UTC).")
    assert "No longer in the live state: unread alerts; desk tasks running or queued." in gone
    msgs = [{"role": "user", "content": "hi"}]
    assert "Live state right now" in turn_prompt(first, msgs, True, diff), "a new session gets everything again"


def test_the_cli_reports_and_prints_what_the_person_added(home, monkeypatch):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        ports = write_config(home, satellite_port=sat.port)
        client, hub = make_client()
        with client:
            task = start_task(client, hub)
        with Server(client.app, port=ports["hub"]) as hub_server:
            monkeypatch.setenv("ORBITAL_DESK_TASK", "t1")
            monkeypatch.setenv("ORBITAL_DESK_TOKEN", task["channel_token"])
            monkeypatch.setenv("ORBITAL_DESK_URL", hub_server.url)
            monkeypatch.delenv("ORBITAL_TRACK_ID", raising=False)

            def cli(*argv: str) -> tuple[int, str, str]:
                parser = argparse.ArgumentParser()
                desk_cli.register(parser)
                args = parser.parse_args(list(argv))
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = desk_cli.run(args)
                return code, out.getvalue(), err.getvalue()

            code, out, _ = cli("note", "Found", "the", "log", "--progress", "40")
            assert code == 0 and "note sent to t1 (40%)" in out
            hub.desk.runner.get("t1")["queue"].append("Skip the test sends.")
            hub.desk.runner._post_inbox(hub.desk.runner.get("t1"), "Skip the test sends.")
            code, out, _ = cli("finding", "22 for the game")
            assert code == 0 and "New from the person" in out and "Skip the test sends." in out
            code, out, _ = cli("inbox")
            assert code == 0 and "Nothing new" in out
            code, out, _ = cli("answer", "37 emails went out today.")
            assert code == 0 and hub.desk.runner.get("t1")["answer"] == "37 emails went out today."
            code, out, _ = cli("ask", "Include bounces?", "--wait", "0")
            assert code == 0 and hub.desk.runner.get("t1")["question"] == "Include bounces?"

            monkeypatch.setenv("ORBITAL_DESK_TOKEN", "not-the-token-0123456789")
            code, _, err = cli("note", "x")
            assert code == 0 and "wasn't delivered" in err, "a failed report never fails the agent"
            code, _, err = cli("inbox")
            assert code == 1

    monkeypatch.delenv("ORBITAL_DESK_TASK")
    code, _, err = cli("answer", "x")
    assert code == 0 and "wasn't started by the desk" in err
