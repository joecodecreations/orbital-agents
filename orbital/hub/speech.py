"""Optional speech for the Desk: Fish Audio voices and on-device speech-to-text models.

Voices (FISH_API_KEY in secrets.env): the app sends a reply's text and a Fish voice id and plays
the audio that comes back, so the app needs no Fish key of its own and the key never leaves the hub.
Without the key, /v1/speech/status says so and the app uses its built-in voice. The voices the app
offers come from [speech.voices] in config.toml (name, Fish reference id, language, default); a
request with no voice id gets the default voice for its language.

Speech-to-text models: each model folder in data/hub/stt-models/<id>/ has a manifest.json listing
every file with its size and sha256. The app lists them, downloads the files once (resuming with
Range requests), checks the hashes, and runs the model itself. Only files a manifest lists are served.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from orbital.core import get_logger
from orbital.hub.web import body_of, fail

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
# Streamed clips are raw 16-bit little-endian mono PCM at this rate (the app resamples).
STREAM_SAMPLE_RATE = 24000


def fish_body(text: str, reference_id: str | None, speed: float | None) -> dict:
    """One Fish request.

    The app plays the finished MP3, so a low-latency 100-character chunk
    does not start the voice any sooner. It does split one sentence into
    pieces that Fish levels separately, which is heard as the volume stepping
    up and down. One 300-character chunk at normal latency stays level.
    ``normalize`` here is text (numbers and dates), not audio gain.
    """
    rate = 1.05 if speed is None else max(0.5, min(2.0, float(speed)))
    body: dict = {
        "text": text[:4000],
        "format": "mp3",
        "mp3_bitrate": 128,
        "normalize": True,
        "latency": "normal",
        "chunk_length": 300,
        "prosody": {"speed": rate, "volume": 0, "normalize_loudness": True},
    }
    if reference_id:
        body["reference_id"] = reference_id.strip()
    return body


def fish_stream_body(text: str, reference_id: str | None, speed: float | None) -> dict:
    """One Fish request whose audio the app plays while it arrives.

    Raw PCM needs no decoder in the app, and "balanced" latency sends the
    first audio in ~0.6 s instead of ~2 s ("normal" holds the clip back until
    most of it is made). One 300-character chunk still covers a whole
    sentence, so the volume stays level inside it.
    """
    body = fish_body(text, reference_id, speed)
    body.pop("mp3_bitrate", None)
    body.update(format="pcm", sample_rate=STREAM_SAMPLE_RATE, latency="balanced")
    return body


class FishError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_voices(table) -> list[dict]:
    """[speech.voices] as a list: {"name", "id", "language", "default"}.

    Each entry is a table named after the voice (`[speech.voices.Ada]` with `id`, optional
    `language` like "en" or "pt", optional `default = true`), or just `Ada = "<reference id>"`.
    Entries without an id are skipped.
    """
    voices: list[dict] = []
    if not isinstance(table, dict):
        return voices
    for name, entry in table.items():
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict):
            continue
        voice_id = str(entry.get("id") or entry.get("reference_id") or "").strip()
        if not voice_id or any(v["id"] == voice_id for v in voices):
            continue
        language = str(entry.get("language") or "").strip().lower() or None
        voices.append({"name": str(entry.get("name") or name).strip() or voice_id, "id": voice_id,
                       "language": language, "default": entry.get("default") is True})
    return voices


class FishVoices:
    def __init__(self, api_key: str, model: str, voices: list[dict] | None = None) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model or "s2.1-pro-free"
        self.voices = list(voices or [])
        self._client: httpx.AsyncClient | None = None

    def default_voice(self, language: str | None = None) -> str | None:
        """The voice for a request with no voice id: the language's default, else its first voice.

        English (and no language) falls back to the overall default, then the first voice. Another
        language with no voice of its own gets None, so Fish picks rather than an English voice.
        """
        lang = (language or "en").strip().lower()[:2] or "en"
        same = [v for v in self.voices if (v["language"] or "en")[:2] == lang]
        if same:
            return next((v["id"] for v in same if v["default"]), same[0]["id"])
        if lang != "en":
            return None
        marked = [v for v in self.voices if v["default"]]
        return (marked or self.voices or [{"id": None}])[0]["id"]

    def defaults(self) -> dict[str, str]:
        """Default voice id per language, for the app's pickers."""
        langs = sorted({(v["language"] or "en")[:2] for v in self.voices} | ({"en"} if self.voices else set()))
        return {lang: vid for lang in langs if (vid := self.default_voice(lang))}

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def refresh(self, api_key: str) -> None:
        """secrets.env changed: use the new key from the next clip on."""
        self.api_key = (api_key or "").strip()

    def _http(self) -> httpx.AsyncClient:
        # One keep-alive connection pool: later clips skip the TLS handshake.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self, model: str | None) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "model": (model or self.model).strip() or self.model,
        }

    async def speak_stream(self, text: str, reference_id: str | None, speed: float | None, model: str | None) -> AsyncIterator[bytes]:
        """PCM chunks as Fish makes them. Raises FishError before the first byte if Fish refuses."""
        if not self.configured:
            raise FishError(503, "FISH_API_KEY is not set in secrets.env")
        spoken = " ".join((text or "").split())
        if not spoken:
            raise FishError(400, "nothing to speak")
        client = self._http()
        request = client.build_request("POST", FISH_TTS_URL, headers=self._headers(model), json=fish_stream_body(spoken, reference_id, speed))
        r = await client.send(request, stream=True)
        if r.status_code >= 400:
            detail = (await r.aread()).decode("utf-8", "replace")[:200]
            await r.aclose()
            get_logger("hub").warning("fish tts stream failed", extra={"fields": {"status": r.status_code, "detail": detail[:160]}})
            raise FishError(502, f"Fish {r.status_code}: {detail[:160]}")

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await r.aclose()

        return chunks()

    async def speak(self, text: str, reference_id: str | None, speed: float | None, model: str | None) -> bytes:
        if not self.configured:
            raise FishError(503, "FISH_API_KEY is not set in secrets.env")
        spoken = " ".join((text or "").split())
        if not spoken:
            raise FishError(400, "nothing to speak")
        body = fish_body(spoken, reference_id, speed)
        headers = self._headers(model)
        client = self._http()
        r = await client.post(FISH_TTS_URL, headers=headers, json=body)
        if r.status_code >= 400 and r.status_code not in (401, 402, 403) and body.get("latency") != "balanced":
            # A plan that rejects the stable latency still gets a clip.
            body["latency"] = "balanced"
            r = await client.post(FISH_TTS_URL, headers=headers, json=body)
        if r.status_code >= 400:
            get_logger("hub").warning("fish tts failed", extra={"fields": {"status": r.status_code, "detail": r.text[:160]}})
            raise FishError(502, f"Fish {r.status_code}: {r.text[:160]}")
        if not r.content:
            raise FishError(502, "Fish returned no audio")
        return r.content


SUMMARY_KEYS = ("id", "name", "source", "license", "version", "total_bytes", "int8", "requires", "created", "files")


class SpeechModels:
    def __init__(self, root: Path) -> None:
        self.dir = root / "stt-models"
        self._cache: dict[str, tuple[float, dict]] = {}

    def _manifest(self, model_id: str) -> dict | None:
        if not model_id or "/" in model_id or model_id.startswith("."):
            return None
        path = self.dir / model_id / "manifest.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(model_id)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        self._cache[model_id] = (mtime, manifest)
        return manifest

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        models = []
        for folder in sorted(self.dir.iterdir()):
            manifest = self._manifest(folder.name) if folder.is_dir() else None
            if manifest:
                models.append({k: manifest[k] for k in SUMMARY_KEYS if k in manifest})
        return models

    def file(self, model_id: str, rel: str) -> Path | None:
        manifest = self._manifest(model_id)
        if manifest is None or not any(f.get("path") == rel for f in manifest.get("files", [])):
            return None
        base = (self.dir / model_id).resolve()
        target = (base / rel).resolve()
        if base not in target.parents or not target.is_file():
            return None
        return target


def add_routes(app, hub) -> None:
    voices: FishVoices = hub.voices
    models: SpeechModels = hub.speech_models

    @app.get("/v1/stt/models")
    async def stt_models(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        return JSONResponse({"models": models.list()})

    @app.get("/v1/stt/models/{model_id}/files/{rel:path}")
    async def stt_model_file(model_id: str, rel: str, request: Request):
        denied = hub.require(request)
        if denied:
            return denied
        path = models.file(model_id, rel)
        if path is None:
            return fail("no such model file", 404)
        # Range requests let the app resume a large download.
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/v1/speech/status")
    async def speech_status(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        voices.refresh(hub.auth.get("FISH_API_KEY"))
        return JSONResponse({
            "tts": voices.configured,
            "provider_tts": "fish" if voices.configured else "none",
            "voices": [{"id": v["id"], "name": v["name"], "language": v["language"], "default": v["default"]}
                       for v in voices.voices],
            "default_voice": voices.default_voice(),
            "defaults": voices.defaults(),
            "model": voices.model,
            "note": None if voices.configured else "Add FISH_API_KEY to secrets.env for spoken replies.",
        })

    def _speech_args(body: dict):
        speed = body.get("speed")
        reference_id = str(body.get("reference_id") or "").strip()
        return (
            str(body["text"]),
            reference_id or voices.default_voice(str(body.get("language") or "") or None),
            float(speed) if isinstance(speed, (int, float)) and not isinstance(speed, bool) else None,
            str(body.get("model") or "") or None,
        )

    @app.post("/v1/speech/tts")
    async def speech_tts(request: Request):
        denied = hub.require(request)
        if denied:
            return denied
        body = await body_of(request)
        if not str(body.get("text") or "").strip():
            return fail("text is required", 400)
        voices.refresh(hub.auth.get("FISH_API_KEY"))
        try:
            audio = await voices.speak(*_speech_args(body))
        except FishError as exc:
            return fail(str(exc), exc.status, code="not_configured" if exc.status == 503 else "unavailable")
        except httpx.HTTPError as exc:
            return fail(f"Fish unreachable: {exc.__class__.__name__}", 502, code="unavailable")
        return Response(content=audio, media_type="audio/mpeg")

    @app.post("/v1/speech/tts/stream")
    async def speech_tts_stream(request: Request):
        """The same clip as raw PCM, sent while Fish makes it, so the app starts talking in about a second."""
        denied = hub.require(request)
        if denied:
            return denied
        body = await body_of(request)
        if not str(body.get("text") or "").strip():
            return fail("text is required", 400)
        voices.refresh(hub.auth.get("FISH_API_KEY"))
        try:
            chunks = await voices.speak_stream(*_speech_args(body))
        except FishError as exc:
            return fail(str(exc), exc.status, code="not_configured" if exc.status == 503 else "unavailable")
        except httpx.HTTPError as exc:
            return fail(f"Fish unreachable: {exc.__class__.__name__}", 502, code="unavailable")
        return StreamingResponse(
            chunks,
            media_type="audio/pcm",
            headers={"Cache-Control": "no-store", "X-Sample-Rate": str(STREAM_SAMPLE_RATE), "X-Channels": "1"},
        )

    @app.post("/v1/desk/voice-log")
    async def desk_voice_log(request: Request) -> JSONResponse:
        """The app's voice diagnostics, kept in logs/voice.log (rolled by size like every log)."""
        denied = hub.require(request)
        if denied:
            return denied
        body = await body_of(request)
        lines = [str(line)[:600] for line in (body.get("lines") or [])[:200]]
        device = str(body.get("device") or "")[:40]
        voice_log = get_logger("voice")
        for line in lines:
            voice_log.info(line, extra={"fields": {"device": device}})
        return JSONResponse({"ok": True, "lines": len(lines)})
