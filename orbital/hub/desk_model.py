"""The fast model behind the Desk: xAI's chat API, streamed.

Needs XAI_API_KEY in secrets.env and desk.provider = "xai" (desk.model picks the model). Without a
key the desk says plainly that it is not configured; real work still goes to agents through the
satellite service either way. The model yields plain text; the desk splits it into the spoken
reply and `>>>` action lines.
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx

XAI_URL = "https://api.x.ai/v1"
DEFAULT_XAI_MODEL = "grok-4-fast-non-reasoning"


class ModelError(RuntimeError):
    pass


class XAIProvider:
    name = "xai"

    def __init__(self, api_key: str, model: str = DEFAULT_XAI_MODEL, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.model = model or DEFAULT_XAI_MODEL
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._warm_at = 0.0

    @property
    def label(self) -> str:
        return self.model

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=XAI_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120),
                transport=self._transport,
            )
        return self._client

    async def warm(self) -> None:
        """Open TLS to the API ahead of the first word. Costs no tokens."""
        if time.time() - self._warm_at < 45:
            return
        self._warm_at = time.time()
        try:
            await self.client().get("/models", timeout=5)
        except httpx.HTTPError:
            pass

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "temperature": 0.4,
            "max_tokens": 900,
        }
        self._warm_at = time.time()
        try:
            async with self.client().stream("POST", "/chat/completions", json=body) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise ModelError(f"xAI {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield text
        except httpx.HTTPError as exc:
            raise ModelError(f"xAI unreachable: {exc.__class__.__name__}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
