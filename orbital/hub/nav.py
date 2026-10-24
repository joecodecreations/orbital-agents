"""The apps' navigation, as data: extra bottom-bar items and the sidebar tree.

The five bottom tabs (Home, System, Agent, Tracker, Alerts) are always there. Everything else is this
document, so a layout changes without an app update:

    {"schema": "orbital.nav.v1",
     "tabs":    [item, ...],          extra bottom-bar items, after the fixed five
     "sidebar": [item, ...] | null}   the sidebar tree; null = the default (spaces, then their sections)

    item = {"id", "title", "icon"?, "accent"?, "target"?, "children"?}
      icon:     an SF Symbol name ("dollarsign.circle") or an uploaded image ("/v1/media/<id>")
      accent:   "#RRGGBB"
      target:   {"kind": "section", "section": id, "node"?: id, "tab"?: id}
                {"kind": "space", "space": id}
                {"kind": "view", "view": one of VIEWS}
                {"kind": "link", "url": "orbital://…" or "https://…"}
      children: items under it. An item with children and no target is a group (a bucket of anything).

    GET /v1/nav  → {"nav": doc, "default": bool}     PUT /v1/nav (the whole doc)     DELETE /v1/nav (back to default)
Stored in data/hub/nav.json. Every change is pushed to open apps as {"type": "nav", "nav": doc}.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from orbital.hub.web import body_of, fail

SCHEMA = "orbital.nav.v1"
VIEWS = ("home", "system", "satellites", "automations", "manage", "settings", "agent", "tracker", "alerts",
         "browse", "spaces", "money", "social", "analytics", "videos", "marketing")
MAX_TABS = 12
MAX_ITEMS = 400
MAX_DEPTH = 6
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
ICON = re.compile(r"^(?:[a-z0-9]+(?:\.[a-z0-9]+)*|/v1/media/[A-Za-z0-9._-]{1,128})$")
ACCENT = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT: dict = {"schema": SCHEMA, "tabs": [], "sidebar": None}


class NavError(ValueError):
    pass


def _target(raw: Any, where: str) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise NavError(f"{where}.target must be an object")
    kind = raw.get("kind")
    if kind == "section":
        out = {"kind": "section", "section": _id(raw.get("section"), f"{where}.target.section")}
        for key in ("node", "tab"):
            if raw.get(key) is not None:
                out[key] = _id(raw.get(key), f"{where}.target.{key}")
        return out
    if kind == "space":
        return {"kind": "space", "space": _id(raw.get("space"), f"{where}.target.space")}
    if kind == "view":
        view = raw.get("view")
        if view not in VIEWS:
            raise NavError(f"{where}.target.view must be one of {', '.join(VIEWS)}")
        return {"kind": "view", "view": view}
    if kind == "link":
        url = raw.get("url")
        if not isinstance(url, str) or len(url) > 2000 or not re.match(r"^(orbital|https)://", url):
            raise NavError(f"{where}.target.url must be an orbital:// or https:// link")
        return {"kind": "link", "url": url}
    raise NavError(f"{where}.target.kind must be section, space, view or link")


def _id(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not ID.match(raw):
        raise NavError(f"{where} must be an id (letters, digits, . _ : -; at most 64)")
    return raw


def _items(raw: Any, where: str, depth: int, count: list[int]) -> list[dict]:
    if not isinstance(raw, list):
        raise NavError(f"{where} must be a list")
    if depth > MAX_DEPTH:
        raise NavError(f"{where} nests deeper than {MAX_DEPTH} levels")
    out: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(raw):
        here = f"{where}[{i}]"
        if not isinstance(row, dict):
            raise NavError(f"{here} must be an object")
        count[0] += 1
        if count[0] > MAX_ITEMS:
            raise NavError(f"more than {MAX_ITEMS} items")
        ident = _id(row.get("id"), f"{here}.id")
        if ident in seen:
            raise NavError(f"{here}.id {ident} is used twice in the same list")
        seen.add(ident)
        title = row.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 60:
            raise NavError(f"{here}.title is required (at most 60 characters)")
        item: dict = {"id": ident, "title": title.strip()}
        icon = row.get("icon")
        if icon is not None:
            if not isinstance(icon, str) or not ICON.match(icon):
                raise NavError(f"{here}.icon must be an SF Symbol name or /v1/media/<id>")
            item["icon"] = icon
        accent = row.get("accent")
        if accent is not None:
            if not isinstance(accent, str) or not ACCENT.match(accent):
                raise NavError(f"{here}.accent must look like #RRGGBB")
            item["accent"] = accent
        target = _target(row.get("target"), here)
        children = row.get("children")
        if target is None and children is None:
            raise NavError(f"{here} needs a target or children")
        if target is not None:
            item["target"] = target
        if children is not None:
            item["children"] = _items(children, f"{here}.children", depth + 1, count)
        out.append(item)
    return out


def validate(doc: Any) -> dict:
    if not isinstance(doc, dict):
        raise NavError("the navigation document must be an object")
    if doc.get("schema", SCHEMA) != SCHEMA:
        raise NavError(f"schema must be {SCHEMA}")
    count = [0]
    tabs = _items(doc.get("tabs") or [], "tabs", 1, count)
    if len(tabs) > MAX_TABS:
        raise NavError(f"at most {MAX_TABS} extra tabs")
    sidebar = doc.get("sidebar")
    return {"schema": SCHEMA, "tabs": tabs, "sidebar": None if sidebar is None else _items(sidebar, "sidebar", 1, count)}


class NavStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[dict, bool]:
        try:
            return validate(json.loads(self.path.read_text(encoding="utf-8"))), False
        except (OSError, json.JSONDecodeError, NavError):
            return dict(DEFAULT), True

    def save(self, doc: dict) -> dict:
        clean = validate(doc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        return clean

    def reset(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def add_routes(app, hub) -> None:
    nav = NavStore(hub.paths.hub_data / "nav.json")

    @app.get("/v1/nav")
    async def nav_get(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        doc, default = nav.load()
        return JSONResponse({"nav": doc, "default": default})

    @app.put("/v1/nav")
    async def nav_put(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        body = await body_of(request)
        try:
            doc = nav.save(body.get("nav", body))
        except NavError as exc:
            return fail(str(exc), 400)
        await hub.live.broadcast({"type": "nav", "nav": doc})
        return JSONResponse({"ok": True, "nav": doc})

    @app.delete("/v1/nav")
    async def nav_reset(request: Request) -> JSONResponse:
        denied = hub.require(request)
        if denied:
            return denied
        nav.reset()
        await hub.live.broadcast({"type": "nav", "nav": dict(DEFAULT)})
        return JSONResponse({"ok": True, "nav": dict(DEFAULT), "default": True})
