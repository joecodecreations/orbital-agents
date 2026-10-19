"""System → Settings → Agent: the chat AI, its model, how work is handed out, and plain-word routing rules."""

from __future__ import annotations

from hub_helpers import AUTH, JOIN, SATELLITE, make_client


def test_defaults_then_save_rules_and_engine(ports):
    client, hub = make_client()
    client.post("/v1/satellites/join", headers={"X-Orbital-Join-Token": JOIN}, json={
        "name": "test-hub", "machine": "test-hub", "ip": "127.0.0.1", "port": 17777, "os": "macos",
        "satellite_token": SATELLITE, "capabilities": {"agents": ["grok", "claude", "echo"]}})
    got = client.get("/v1/desk/settings", headers=AUTH).json()
    assert got["settings"] == {"chat": "auto", "model": "", "dispatch": "parallel", "rules": []}
    assert {c["id"] for c in got["options"]["chats"]} == {"auto", "grok", "claude", "xai", "ondevice"}
    assert got["options"]["agents"] == ["grok", "claude"], "echo is a test agent, never offered"
    assert got["options"]["machines"][0]["name"] == "test-hub"

    body = {"chat": "claude", "model": "sonnet", "dispatch": "single",
            "rules": [{"text": "Always route iOS builds to test-hub with claude"}, {"text": "  "}, "Use grok for research"]}
    saved = client.put("/v1/desk/settings", headers=AUTH, json=body)
    assert saved.status_code == 200, saved.text
    settings = saved.json()["settings"]
    assert settings["chat"] == "claude" and settings["model"] == "sonnet" and settings["dispatch"] == "single"
    assert [r["text"] for r in settings["rules"]] == ["Always route iOS builds to test-hub with claude", "Use grok for research"]

    from orbital.hub.desk_engines import ClaudeSessionProvider
    assert isinstance(hub.desk.provider.primary, ClaudeSessionProvider), "the chosen chat AI answers first"
    prompt = hub.desk.system_prompt()
    assert "Always route iOS builds to test-hub with claude" in prompt and "Dispatch mode: one agent" in prompt

    assert client.put("/v1/desk/settings", headers=AUTH, json={"rules": []}).json()["settings"]["chat"] == "claude", \
        "only the keys sent change"


def test_bad_settings_are_refused(ports):
    client, hub = make_client()
    assert client.put("/v1/desk/settings", headers=AUTH, json={"chat": "gpt"}).status_code == 400
    assert client.put("/v1/desk/settings", headers=AUTH, json={"dispatch": "sometimes"}).status_code == 400
    assert client.put("/v1/desk/settings", headers=AUTH, json={"model": "bad model; rm"}).status_code == 400
    assert client.put("/v1/desk/settings", headers=AUTH, json={"rules": ["x" * 401]}).status_code == 400
    assert client.get("/v1/desk/settings").status_code == 401


def test_talk_only_starts_no_agents(ports):
    import asyncio

    client, hub = make_client()
    client.put("/v1/desk/settings", headers=AUTH, json={"dispatch": "off"})
    assert "Dispatch mode: talk only" in hub.desk.system_prompt()
    event, did = asyncio.run(hub.desk._run_action({"type": "task", "title": "X", "brief": "do x"}, "do x"))
    assert event is None and did is None
    assert hub.desk.runner.tasks == []
