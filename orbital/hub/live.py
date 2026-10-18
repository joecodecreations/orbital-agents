"""WebSocket fan-out: section revisions, alerts, tracker, desk and satellite events, to every open app."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class LiveHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self.clients:
            return
        payload = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def ping_forever(self) -> None:
        while True:
            await asyncio.sleep(20)
            await self.broadcast({"type": "ping"})
