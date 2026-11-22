"""The desk answering from data itself: lookups (pages, reports, files), a typed turn that looks something
up and answers in the same turn, a promise with no action behind it, and live voice through a fake
OpenAI Realtime socket."""

from __future__ import annotations

import asyncio
import base64
import json
import time

from hub_helpers import AUTH, make_client
from test_hub_desk import FakeModel, lines

from orbital.feeds import blocks as b
from orbital.hub import desk_realtime


def make_projects(home):
    root = home / "projects"
    notes = root / "alpha-client"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "meeting-notes.md").write_text(
        "# Alpha client\n\nKickoff went fine.\n\nThe launch is moved to Tuesday because the payment page failed review.\n",
        encoding="utf-8",
    )
    (notes / ".env").write_text("TOKEN=nope\n", encoding="utf-8")
    (notes / "api-secret.txt").write_text("nope\n", encoding="utf-8")
    (root / "beta").mkdir(exist_ok=True)
    return root


def test_lookups_read_pages_reports_and_files_but_never_secrets(home, ports):
    client, hub = make_client()
    root = make_projects(home)
    hub.store.put("website-uptime", b.section("website-uptime", "Website Uptime", [
        b.metrics("now", [b.metric("Response", "182 ms")])], icon="globe", summary="All sites up"))
    lookup = hub.desk.lookup
    assert "Response: 182 ms" in lookup.page("website uptime")
    assert "Website Uptime" in lookup.search("response time uptime")

    listing = lookup.list_folder("alpha-client")
    assert "meeting-notes.md" in listing and ".env" not in listing and "api-secret" not in listing
    text = asyncio.run(lookup.read_file("alpha-client/meeting-notes.md"))
    assert "moved to Tuesday" in text
    assert "won't read" in asyncio.run(lookup.read_file(str(root / "alpha-client" / "api-secret.txt")))
    assert "hidden" in asyncio.run(lookup.read_file(str(root / "alpha-client" / ".env")))
    assert "outside the folders" in asyncio.run(lookup.read_file("/etc/hosts"))
    found = asyncio.run(lookup.find_files("meeting notes"))
    assert "meeting-notes.md" in found
    recent = asyncio.run(lookup.find_files("", "alpha-client"))
    assert "meeting-notes.md" in recent
    assert "meeting-notes.md" in lookup.list_folder("Alpha Client"), "spoken names match dashed folders"
    assert "meeting-notes.md" in lookup.list_folder("the alpha client folder")
    assert hub.desk.projects().resolve("alpha client")["name"] == "alpha-client"


def test_a_typed_turn_looks_it_up_and_answers_in_the_same_turn(home, ports):
    client, hub = make_client()
    make_projects(home)
    model = FakeModel(
        'Checking the notes.\n>>> {"type":"lookup","tool":"read_file","path":"alpha-client/meeting-notes.md"}',
        "The launch moved to Tuesday because the payment page failed review.",
    )
    hub.desk._fixed_provider = model
    res = client.post("/v1/desk/say", headers=AUTH, json={"text": "Why did the alpha launch move?"})
    rows = lines(res)
    replies = [r["message"] for r in rows if r["type"] == "reply"]
    assert [r["text"] for r in replies] == ["Checking the notes.", "The launch moved to Tuesday because the payment page failed review."]
    assert len({r["id"] for r in replies}) == 2 and [r["type"] for r in rows].count("start") == 2
    assert replies[0]["did"] == ["read meeting-notes.md"]
    second_prompt = model.seen[1][1][-1]["content"]
    assert second_prompt.startswith("(Lookup results") and "moved to Tuesday" in second_prompt


def test_a_promise_without_an_action_is_acted_on(home, ports):
    client, hub = make_client()
    hub.store.put("book-sales", b.section("book-sales", "Book Sales", [
        b.paragraph("p", "41 paperbacks sold this month")], icon="book", summary="41 sold this month"))
    model = FakeModel("I'm pulling your book sales now.", "You sold 41 paperbacks this month.")
    hub.desk._fixed_provider = model
    rows = lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "How many paperbacks sold this month?"}))
    replies = [r["message"] for r in rows if r["type"] == "reply"]
    assert replies[0]["promised"] is True and replies[-1]["text"] == "You sold 41 paperbacks this month."
    assert "41 paperbacks" in model.seen[1][1][-1]["content"], "the desk searched for it instead of doing nothing"

    model = FakeModel("Let me check on that.", "I'm sending someone to find it.")
    hub.desk._fixed_provider = model
    lines(client.post("/v1/desk/say", headers=AUTH, json={"text": "Where is the signed lease for the garage?"}))
    task = hub.desk.runner.tasks[-1]
    assert task["effort"] == "quick" and "signed lease" in task["brief"], "a second empty promise starts an agent"


class FakeOpenAI:
    """Enough of a Realtime socket: answers session.update, records what the bridge sends."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg["type"] == "session.update" and "audio" in msg["session"]:
            await self.inbox.put({"type": "session.updated", "session": {}})

    async def recv(self):
        item = await self.inbox.get()
        return json.dumps(item)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return json.dumps(item)

    async def close(self) -> None:
        await self.inbox.put(None)

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m["type"] == kind]


class FakePhone:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.events: list[dict] = []
        self.audio: list[bytes] = []

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, text: str) -> None:
        self.events.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    def kinds(self) -> list[str]:
        return [e["type"] for e in self.events]


async def wait_for(check, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


def test_live_voice_relays_audio_runs_tools_and_reports_agents(home, ports):
    client, hub = make_client()
    make_projects(home)
    desk = hub.desk
    oa, phone = FakeOpenAI(), FakePhone()

    async def connect(model):
        assert model == "gpt-realtime-2.1"
        return oa

    async def call() -> None:
        bridge = desk_realtime.RealtimeBridge(desk, phone, "test-key", connect=connect)
        running = asyncio.create_task(bridge.run())
        await wait_for(lambda: "ready" in phone.kinds())
        session = oa.of("session.update")[0]["session"]
        assert session["audio"]["output"]["voice"] == "marin"
        assert session["audio"]["input"]["turn_detection"]["interrupt_response"] is True
        assert {t["name"] for t in session["tools"]} >= {"find_files", "read_file", "start_agent", "live_status"}
        assert "Before your first tool call in a turn, say a few words" in session["instructions"]

        await phone.incoming.put({"type": "websocket.receive", "bytes": b"\x01\x00" * 480})
        await wait_for(lambda: oa.of("input_audio_buffer.append"))
        assert base64.b64decode(oa.of("input_audio_buffer.append")[0]["audio"]) == b"\x01\x00" * 480

        # The user asks; the voice says a few words and calls a tool in the same response.
        for event in (
            {"type": "input_audio_buffer.speech_started", "item_id": "u1"},
            {"type": "input_audio_buffer.speech_stopped", "item_id": "u1"},
            {"type": "input_audio_buffer.committed", "item_id": "u1"},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Why did the alpha launch move?"},
            {"type": "response.created", "response": {"id": "r1"}},
            {"type": "response.output_audio.delta", "item_id": "a1", "delta": base64.b64encode(b"\x02\x00" * 240).decode()},
            {"type": "response.output_audio_transcript.delta", "item_id": "a1", "delta": "Checking on that."},
            {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "c1", "name": "read_file",
                                                           "arguments": json.dumps({"path": "alpha-client/meeting-notes.md", "query": "", "offset": 0})}},
            {"type": "response.done", "response": {"id": "r1", "status": "completed", "output": [
                {"type": "message", "id": "a1"}, {"type": "function_call", "call_id": "c1"}]}},
        ):
            await oa.inbox.put(event)
        await wait_for(lambda: oa.of("response.create"))
        output = oa.of("conversation.item.create")[0]["item"]
        assert output["type"] == "function_call_output" and output["call_id"] == "c1" and "moved to Tuesday" in output["output"]
        assert phone.audio == [b"\x02\x00" * 240]
        assert {"speech_started", "audio_start", "assistant_delta", "user_final", "tool"} <= set(phone.kinds())

        # Barge-in: the phone stopped playback after 120 ms; the hub truncates that reply.
        await phone.incoming.put({"type": "websocket.receive", "text": json.dumps({"type": "truncate", "item_id": "a1", "audio_end_ms": 120})})
        await wait_for(lambda: oa.of("conversation.item.truncate"))
        assert oa.of("conversation.item.truncate")[0] == {"type": "conversation.item.truncate", "item_id": "a1", "content_index": 0, "audio_end_ms": 120}

        await oa.inbox.put({"type": "response.created", "response": {"id": "r2"}})
        await oa.inbox.put({"type": "response.output_audio_transcript.done", "item_id": "a2", "transcript": "It moved to Tuesday; the payment page failed review."})
        await oa.inbox.put({"type": "response.done", "response": {"id": "r2", "status": "completed", "output": [{"type": "message", "id": "a2"}]}})
        await wait_for(lambda: any(m.get("text", "").startswith("It moved to Tuesday") for m in desk.messages))

        # An agent answers while the call is open: the voice is told at once and speaks it.
        before = len(oa.of("response.create"))
        desk._notify_voice("[agent update] Agent t1 “Store check” answered: the listing is live.")
        await wait_for(lambda: len(oa.of("response.create")) > before)
        update = [m for m in oa.of("conversation.item.create") if m["item"].get("role") == "system"][-1]
        assert "the listing is live" in update["item"]["content"][0]["text"]

        # Its own words coming back through a speakerphone are not the user: dropped, and any reply stopped.
        await oa.inbox.put({"type": "response.created", "response": {"id": "r3"}})
        await oa.inbox.put({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u9", "transcript": "the payment page failed review"})
        await wait_for(lambda: oa.of("conversation.item.delete"))
        assert oa.of("conversation.item.delete")[0]["item_id"] == "u9" and oa.of("response.cancel")
        assert "echo" in phone.kinds()
        await oa.inbox.put({"type": "response.done", "response": {"id": "r3", "status": "cancelled", "output": []}})
        bridge_echo = desk_realtime.RealtimeBridge(desk, FakePhone(), "k")
        bridge_echo.spoken = [(time.time(), "Nice. If you're back at your phone, tell me what you need.")]
        bridge_echo.over_voice = {"u10", "u11"}
        assert bridge_echo._is_echo("Nice.", "u10"), "a short echo heard over its voice is dropped"
        assert not bridge_echo._is_echo("Stop.", "u11"), "a real cut-in is kept"
        assert not bridge_echo._is_echo("Nice.", "u12"), "a short word said after it finished is kept"

        # Tracker news reaches the call only when the phone's "say when something finishes" is on.
        before = len(oa.of("response.create"))
        desk._notify_voice("[tracker update] Claude finished “Fix tests”: all green.", "tracker")
        await asyncio.sleep(0.3)
        assert len(oa.of("response.create")) == before, "tracker news is off until the phone asks for it"
        await phone.incoming.put({"type": "websocket.receive", "text": json.dumps({"type": "prefs", "announce_tracker": True})})
        await asyncio.sleep(0.1)
        desk._notify_voice("[tracker update] Claude finished “Fix tests”: all green.", "tracker")
        await wait_for(lambda: len(oa.of("response.create")) > before)
        news = [m for m in oa.of("conversation.item.create") if m["item"].get("role") == "system"][-1]["item"]["content"][0]["text"]
        assert "Fix tests" in news and "by the way" in news

        await phone.incoming.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(running, 3)

    asyncio.run(call())
    said = [(m["role"], m.get("kind"), m.get("text")) for m in desk.messages]
    assert ("user", None, "Why did the alpha launch move?") in said
    assert ("assistant", None, "Checking on that.") in said
    assert ("event", "lookup", "Read meeting-notes.md") in said
    assert not any(t == "the payment page failed review" for _, _, t in said), "an echo never lands in the thread"
    assert desk.voice_sessions == 0 and not desk.voice_listeners
    assert any("Why did the alpha launch move?" in line for line in desk.recent_turns()), "typed turns see what was said by voice"


def test_a_reply_reported_twice_is_saved_once(home, ports):
    client, hub = make_client()
    bridge = desk_realtime.RealtimeBridge(hub.desk, FakePhone(), "k")
    reply = {"id": "r1", "status": "completed", "output": [{"type": "message", "id": "a1", "content": [{"transcript": "Night falls."}]}]}
    assert bridge._spoken(reply) == [("a1", "Night falls.")]
    assert bridge._spoken({**reply, "status": "cancelled"}) == [], "the same item again is not a second message"


def test_live_voice_without_a_key_says_how_to_turn_it_on(home, ports):
    client, hub = make_client()
    with client.websocket_connect("/v1/desk/realtime?token=" + AUTH["Authorization"].split()[1]) as ws:
        first = json.loads(ws.receive_text())
    assert first["type"] == "error" and first["fatal"] is True and "OPENAI_API_KEY" in first["message"]
    assert client.get("/v1/desk/realtime", headers=AUTH).json()["available"] is False
