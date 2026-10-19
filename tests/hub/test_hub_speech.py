"""Speech: the voices the app offers, the default voice for a request with none, on-device model files."""

from __future__ import annotations

import json

import pytest

from hub_helpers import AUTH, make_client, write_config
from orbital.hub import speech
from orbital.hub.speech import FishVoices, parse_voices

VOICES = """
[speech]
fish_model = "s2.1-pro-free"

[speech.voices.Ada]
id = "voice-ada"
language = "en"
default = true

[speech.voices.Ben]
id = "voice-ben"

[speech.voices.Lia]
id = "voice-lia"
language = "pt"
default = true
"""


def test_parse_voices_accepts_tables_and_plain_ids():
    voices = parse_voices({"Ada": {"id": "a", "language": "EN", "default": True}, "Ben": "b", "Dup": "b", "Empty": {}})
    assert voices == [
        {"name": "Ada", "id": "a", "language": "en", "default": True},
        {"name": "Ben", "id": "b", "language": None, "default": False},
    ]
    assert parse_voices(None) == []


def test_default_voice_per_language():
    fish = FishVoices("", "", parse_voices({"Ben": "b", "Ada": {"id": "a", "default": True}, "Lia": {"id": "l", "language": "pt"}}))
    assert fish.default_voice() == "a"
    assert fish.default_voice("en-US") == "a"
    assert fish.default_voice("pt") == "l"
    assert fish.default_voice("fr") is None
    assert fish.defaults() == {"en": "a", "pt": "l"}
    assert FishVoices("", "").default_voice() is None


@pytest.fixture
def api(home):
    write_config(home, extra=VOICES)
    client, hub = make_client()
    yield client, hub
    client.close()


def test_status_lists_voices_and_defaults(api, monkeypatch):
    client, _ = api
    body = client.get("/v1/speech/status", headers=AUTH).json()
    assert body["tts"] is False and body["provider_tts"] == "none"
    assert [v["name"] for v in body["voices"]] == ["Ada", "Ben", "Lia"]
    assert body["default_voice"] == "voice-ada"
    assert body["defaults"] == {"en": "voice-ada", "pt": "voice-lia"}
    assert body["model"] == "s2.1-pro-free"

    monkeypatch.setenv("FISH_API_KEY", "test-fish-key")
    body = client.get("/v1/speech/status", headers=AUTH).json()
    assert body["tts"] is True and body["provider_tts"] == "fish" and body["note"] is None


def test_tts_without_a_voice_uses_the_language_default(api, monkeypatch):
    client, hub = api
    monkeypatch.setenv("FISH_API_KEY", "test-fish-key")
    seen = []

    async def fake_speak(text, reference_id, speed, model):
        seen.append(reference_id)
        return b"ID3audio"

    monkeypatch.setattr(hub.voices, "speak", fake_speak)
    for body in ({"text": "hi"}, {"text": "oi", "language": "pt"}, {"text": "hey", "reference_id": "voice-ben"}):
        res = client.post("/v1/speech/tts", headers=AUTH, json=body)
        assert res.status_code == 200 and res.content == b"ID3audio"
        assert res.headers["content-type"] == "audio/mpeg"
    assert seen == ["voice-ada", "voice-lia", "voice-ben"]


def test_tts_without_key_says_not_configured(api):
    client, _ = api
    res = client.post("/v1/speech/tts", headers=AUTH, json={"text": "hello"})
    assert res.status_code == 503


def test_model_files_only_listed_ones(home, api):
    client, hub = api
    folder = hub.paths.hub_data / "stt-models" / "tiny"
    folder.mkdir(parents=True)
    (folder / "a.bin").write_bytes(b"abc")
    (folder / "secret.bin").write_bytes(b"no")
    (folder / "manifest.json").write_text(json.dumps({"id": "tiny", "name": "Tiny", "total_bytes": 3, "files": [{"path": "a.bin", "size": 3}]}))
    models = client.get("/v1/stt/models", headers=AUTH).json()["models"]
    assert [m["id"] for m in models] == ["tiny"]
    assert client.get("/v1/stt/models/tiny/files/a.bin", headers=AUTH).content == b"abc"
    assert client.get("/v1/stt/models/tiny/files/secret.bin", headers=AUTH).status_code == 404
    assert speech.STREAM_SAMPLE_RATE == 24000
