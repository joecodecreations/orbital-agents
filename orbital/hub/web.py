"""HTTP helpers every hub route shares: error bodies, JSON bodies, and the network allow list."""

from __future__ import annotations

import json
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from orbital.core.net import allowed
from orbital.hub.contract import error_body


def fail(
    message: str,
    status: int = 400,
    *,
    code: str | None = None,
    section_id: str | None = None,
    revision: int | None = None,
    include_revision: bool = False,
) -> JSONResponse:
    return JSONResponse(
        error_body(message, status, code=code, section_id=section_id, revision=revision, include_revision=include_revision),
        status_code=status,
    )


async def body_of(request: Request) -> dict:
    """The JSON object a request sent, or {} for no body, bad JSON, or a non-object."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def device_of(request: Request) -> str:
    return (request.headers.get("x-orbital-device") or "app")[:40]


class AllowNetworks:
    """ASGI middleware: answer only clients inside `networks` (loopback + Tailscale by default).

    Runs before routing and auth, for HTTP and websockets alike, so a request from anywhere
    else gets 403 (or a websocket close 4403) and nothing more.
    """

    def __init__(self, app, networks: Iterable[str], on_reject=None) -> None:
        self.app = app
        self.networks = list(networks)
        self.on_reject = on_reject

    async def __call__(self, scope, receive, send) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or (None, None)
        if allowed(client[0], self.networks):
            await self.app(scope, receive, send)
            return
        if self.on_reject is not None:
            self.on_reject(client[0], scope.get("path", ""))
        if kind == "websocket":
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 4403})
            return
        response = JSONResponse(error_body("this network may not use the hub", 403, code="forbidden"), status_code=403)
        await response(scope, receive, send)
