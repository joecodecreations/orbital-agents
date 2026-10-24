"""The Desk: not configured, a turn with actions, workers through the local satellite, echo drops."""

from __future__ import annotations

import json

from hub_helpers import AUTH, SATELLITE, Server, fake_satellite, make_client, write_config
from orbital.hub.desk import ReplySplitter


class FakeModel:
    """Streams a scripted reply in small chunks, like the real API."""

    name = "fake"
    label = "fake-model"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seen: list[tuple[str, list]] = []

    async def stream(self, system, messages):
        self.seen.append((system, messages))
        text = self.replies.pop(0) if self.replies else "ok"
        for i in range(0, len(text), 7):
            yield text[i : i + 7]

    async def warm(self):
        return None

    async def close(self):
        return None


def lines(res) -> list[dict]:
    return [json.loads(line) for line in res.text.splitlines() if line.strip()]


def test_splitter_streams_the_reply_and_holds_back_actions():
    splitter = ReplySplitter()
    out = "".join(splitter.feed(c) for c in ["On it, starting ", "an agent.\n>", '>> {"type":"task","title":"X"', "}\n"])
    assert out == "On it, starting an agent."
    reply, actions = splitter.finish()
    assert reply == "On it, starting an agent."
    assert actions == [{"type": "task", "title": "X"}]


def test_splitter_catches_an_action_on_the_same_line_as_the_reply():
    splitter = ReplySplitter()
    chunks = ["On it. I'm sending the build", " for review. >", '>> {"type":"task","title":"Submit"', ',"effort":"high"} >>> {"type":"todo","title":"Check"}']
    out = "".join(splitter.feed(c) for c in chunks)
    assert out == "On it. I'm sending the build for review."
    reply, actions = splitter.finish()
    assert reply == "On it. I'm sending the build for review."
    assert actions == [{"type": "task", "title": "Submit", "effort": "high"}, {"type": "todo", "title": "Check"}]
    plain = ReplySplitter()
    assert plain.feed("Five > three, and 2 >> 1.") == "Five > three, and 2 >> 1."
    assert plain.finish() == ("Five > three, and 2 >> 1.", [])


def test_without_a_key_the_desk_uses_the_subscription_engines(ports):
    client, hub = make_client()
    snap = client.get("/v1/desk", headers=AUTH).json()
    assert snap["model"]["configured"] is True and snap["model"]["provider"] == "subscription"
    from orbital.hub.desk_engines import GrokSessionProvider
    assert isinstance(hub.desk.provider.primary, GrokSessionProvider), "the Grok subscription answers first"
    assert client.post("/v1/desk/say", headers=AUTH, json={"text": ""}).status_code == 400
    assert client.get("/v1/desk").status_code == 401


def test_turned_off_the_desk_says_so(ports, monkeypatch):
    client, hub = make_client()
    real = hub.cfg.get
    monkeypatch.setattr(hub.cfg, "get", lambda key, default=None: "off" if key == "desk.provider" else real(key, default))
    snap = client.get("/v1/desk", headers=AUTH).json()
    assert snap["model"]["configured"] is False
    res = client.post("/v1/desk/say", headers=AUTH, json={"text": "What's running?"})
    assert [row["type"] for row in lines(res)] == ["user", "event", "done"]
    assert lines(res)[1]["message"]["kind"] == "not_configured"


def test_a_key_puts_the_xai_api_first(ports, monkeypatch):
    client, hub = make_client()
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    model = client.get("/v1/desk", headers=AUTH).json()["model"]
    assert model["configured"] is True
    from orbital.hub.desk_engines import XAIProvider
    assert isinstance(hub.desk.provider.primary, XAIProvider)


def test_a_turn_starts_a_worker_on_the_local_satellite_and_reports_back(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port, extra='''
[desk]
worker_agent = "codex"

[[desk.projects]]
name = "Website"
path = "~/code/website"
about = "the public site"
''')
        client, hub = make_client()
        model = FakeModel('On it, I will get an agent on the website.\n>>> {"type":"task","project":"website","title":"Add dark mode","brief":"Add a dark theme to the site."}\n')
        hub.desk._fixed_provider = model
        with client:
            res = client.post("/v1/desk/say", headers=AUTH, json={"text": "Add dark mode to the website", "client_id": "c1"})
            rows = lines(res)
            kinds = [row["type"] for row in rows]
            assert kinds[:2] == ["user", "start"] and "said" in kinds and kinds[-1] == "done"
            spoken = "".join(row["text"] for row in rows if row["type"] == "delta")
            assert spoken == "On it, I will get an agent on the website."
            event = next(row["message"] for row in rows if row["type"] == "event")
            assert event["kind"] == "task_started" and event["task_id"] == "t1"
            assert "Website" in model.seen[0][0], "the project list from config is in the prompt"
            run = state["runs"][0]
            assert run["agent"] == "codex" and run["cwd"] == "~/code/website" and run["track"] is True and run["by"] == "desk"
            assert "Add a dark theme" in run["prompt"]
            assert state["auth"][-1] == f"Bearer {SATELLITE}"

            again = client.post("/v1/desk/say", headers=AUTH, json={"text": "Add dark mode to the website", "client_id": "c1"})
            assert lines(again)[0]["duplicate"] is True, "a retried client_id is not run twice"

            state["jobs"]["j1"].update({"status": "done", "exit_code": 0, "output_tail": "Dark mode is live. I added a toggle in the header."})
            client.portal.call(hub.desk.runner.tick)
            task = client.get("/v1/desk/tasks/t1", headers=AUTH).json()
            assert task["status"] == "done" and task["tracker_id"] == "trj1"
            thread = client.get("/v1/desk/messages", headers=AUTH).json()["messages"]
            done = [m for m in thread if m.get("kind") == "task_done"]
            assert done and done[0]["speak"].startswith("Add dark mode is done. Dark mode is live.")
            alerts = client.get("/v1/alerts", headers=AUTH).json()["alerts"]
            assert alerts[0]["id"] == "desk-t1-1" and alerts[0]["launch_url"] == "orbital://agent?task=t1"
            assert client.get("/v1/desk", headers=AUTH).json()["tasks"][0]["status"] == "done"


def test_unknown_project_and_busy_satellite(home):
    sat_app, state = fake_satellite()
    with Server(sat_app) as sat:
        write_config(home, satellite_port=sat.port, extra='''
[[desk.projects]]
name = "Website"
path = "~/code/website"
''')
        client, hub = make_client()
        hub.desk._fixed_provider = FakeModel(
            'Which project?\n>>> {"type":"task","project":"garden","title":"Plan beds","brief":"x"}\n',
            'Saved.\n>>> {"type":"task","project":"","title":"Clean up","brief":"Tidy the repo."}\n',
        )
        with client:
            first = lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "plan the garden"}))
            assert next(r["message"] for r in first if r["type"] == "event")["kind"] == "error"
            state["busy"] = True
            second = lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "clean up"}))
            event = next(r["message"] for r in second if r["type"] == "event")
            assert event["kind"] == "task_started" and "Saved" in event["text"]
            task = hub.desk.runner.get("t1")
            assert task["status"] == "queued" and task["waiting"]
            state["busy"] = False
            client.portal.call(hub.desk.runner.tick)
            assert hub.desk.runner.get("t1")["status"] == "running"
            assert "cwd" not in state["runs"][-1], "no project: the satellite's default folder"
            job_id = hub.desk.runner.get("t1")["job_id"]
            stopped = client.post("/v1/desk/tasks/t1/cancel", headers=AUTH).json()
            assert stopped["status"] == "stopped" and state["cancelled"] == [job_id]


def test_voice_echo_of_the_last_reply_is_dropped(ports):
    client, hub = make_client()
    hub.desk._fixed_provider = FakeModel("Sure, I can check the backup status on the server for you right now.")
    with client:
        lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "Can you check the backups?", "source": "voice"}))
        echo = lines(client.post("/v1/desk/say", headers=AUTH, json={
            "text": "I can check the backup status on the server for you right now", "source": "voice"}))
        assert echo[0]["type"] == "echo"
        typed = lines(client.post("/v1/desk/say", headers=AUTH, json={
            "text": "I can check the backup status on the server for you right now", "source": "text"}))
        assert typed[0]["type"] == "user", "typed messages are never dropped"


def test_clear_keeps_the_thread_and_carries_notes(ports):
    client, hub = make_client()
    hub.desk._fixed_provider = FakeModel("Noted.", "- Prefers short answers")
    with client:
        lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "Keep answers short please"}))
        marker = client.post("/v1/desk/clear", headers=AUTH).json()["message"]
        assert marker["kind"] == "context" and marker["carry"] == "- Prefers short answers"
        assert "Prefers short answers" in hub.desk.system_prompt()
        assert hub.desk.history(marker["id"]) == [{"role": "user", "content": "(continue)"}]
