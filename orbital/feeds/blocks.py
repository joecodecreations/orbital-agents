"""Builders for sections and the blocks inside them. Each returns a plain dict the hub accepts.

    from orbital.feeds import blocks as b

    page = b.section("website", "Website uptime", [
        b.metrics("now", [b.metric("Status", "Up", caption="checked 2 min ago"),
                          b.metric("Response", "182 ms", delta="-12 ms", direction="down")]),
        b.chart("latency", [b.chart_series("p95", [("2026-01-01", 210), ("2026-01-02", 182)])],
                title="Response time", y_label="ms"),
        b.table("checks", ["When", "Result"], [["09:00", "200 OK"], ["09:05", "200 OK"]], title="Latest checks"),
        b.buttons("actions", [b.button("Open site", url="https://example.com"),
                              b.button("Re-check now", post="site.recheck", id="recheck")]),
    ], icon="globe", space="web")

Block type names (schema/orbital.section.v1.schema.json): heading, paragraph (text), bullets (list),
callout, metrics, key_value, table, timeline, divider, chart, image, hero, buttons, progress, status,
links, tasks, ledger, product, series. Nothing here fetches or estimates: pass only figures your
source returned.
"""

from __future__ import annotations

import builtins
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

_SLUG = re.compile(r"[^a-z0-9]+")
_list = builtins.list


def slug(raw: str, limit: int = 64) -> str:
    """A valid id from any text: 'My Server #2' → 'my-server-2'."""
    text = _SLUG.sub("-", (raw or "").lower()).strip("-") or "item"
    return text[:limit].strip("-") or "item"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(block: dict) -> dict:
    """Drop the optional fields left as None."""
    return {k: v for k, v in block.items() if v is not None}


# Sections and pages


def section(
    id: str,
    title: str,
    blocks: Sequence[dict] = (),
    *,
    icon: str = "square.grid.2x2",
    order: int = 100,
    layout: str | None = None,
    subtitle: str | None = None,
    summary: str | None = None,
    accent: str | None = None,
    icon_image: str | None = None,
    tabs: Sequence[dict] = (),
    nodes: Sequence[dict] = (),
    space: str | None = None,
    space_title: str | None = None,
    space_icon: str | None = None,
) -> dict:
    """One page in the app. layout: page (blocks), tabs (tabs), or sidebar (nodes); guessed when omitted."""
    if layout is None:
        layout = "sidebar" if nodes else "tabs" if tabs else "page"
    return _clean({
        "schema": "orbital.section.v1",
        "id": id,
        "title": title,
        "subtitle": subtitle,
        "icon": icon,
        "icon_image": icon_image,
        "order": order,
        "accent": accent,
        "layout": layout,
        "summary": summary,
        "space": space,
        "space_title": space_title,
        "space_icon": space_icon,
        "blocks": _list(blocks),
        "tabs": _list(tabs),
        "nodes": _list(nodes),
    })


def tab(id: str, title: str, blocks: Sequence[dict] = (), *, icon: str | None = None) -> dict:
    return _clean({"id": id, "title": title, "icon": icon, "blocks": _list(blocks)})


def node(id: str, title: str, blocks: Sequence[dict] = (), *, order: int = 10, tabs: Sequence[dict] = (),
         subtitle: str | None = None, icon: str | None = None, icon_image: str | None = None) -> dict:
    """One entry in a sidebar section (a server, a repo, a site). With tabs, its layout is tabs."""
    return _clean({
        "id": id, "title": title, "subtitle": subtitle, "icon": icon, "icon_image": icon_image, "order": order,
        "layout": "tabs" if tabs else "page", "blocks": _list(blocks), "tabs": _list(tabs),
    })


# Text


def heading(id: str, text: str, level: int = 2) -> dict:
    return {"type": "heading", "id": id, "text": text, "level": level}


def text(id: str, text: str) -> dict:
    """A paragraph of markdown (emphasis, links, inline code)."""
    return {"type": "paragraph", "id": id, "text": text}


paragraph = text


def list(id: str, items: Iterable[str], *, title: str | None = None, ordered: bool = False) -> dict:  # noqa: A001
    """A short bulleted (or numbered) list."""
    return _clean({"type": "bullets", "id": id, "title": title, "ordered": ordered, "items": [str(i) for i in items]})


bullets = list


def callout(id: str, text: str, *, tone: str = "info", title: str | None = None) -> dict:
    """One notice. tone: info, success, warning, danger."""
    return _clean({"type": "callout", "id": id, "tone": tone, "title": title, "text": text})


def divider(id: str) -> dict:
    return {"type": "divider", "id": id}


# Numbers


def metric(label: str, value: Any, *, id: str | None = None, delta: str | None = None,
           direction: str | None = None, caption: str | None = None) -> dict:
    """One tile for metrics(). value is shown as given (format it yourself). direction: up, down, flat."""
    return _clean({"id": id, "label": label, "value": str(value), "delta": delta, "direction": direction, "caption": caption})


def metrics(id: str, items: Iterable[dict] | dict, *, title: str | None = None) -> dict:
    """A row of large figures. items: metric(...) tiles (or one tile)."""
    tiles = [items] if isinstance(items, Mapping) else _list(items)
    return _clean({"type": "metrics", "id": id, "title": title, "items": tiles})


def key_value(id: str, items: Mapping[str, Any] | Iterable[tuple[str, Any]], *, title: str | None = None) -> dict:
    """Label / value rows, from a dict or (label, value) pairs."""
    pairs = items.items() if isinstance(items, Mapping) else items
    return _clean({"type": "key_value", "id": id, "title": title,
                   "items": [{"label": str(k), "value": str(v)} for k, v in pairs]})


def table(id: str, columns: Sequence[str], rows: Iterable[Sequence[Any]], *, title: str | None = None,
          limit: int | None = None) -> dict:
    """Columns and rows of strings. limit shows that many rows, then 'Show all'."""
    return _clean({"type": "table", "id": id, "title": title, "columns": [str(c) for c in columns],
                   "rows": [[("" if cell is None else str(cell)) for cell in row] for row in rows], "limit": limit})


def chart_series(name: str, points: Iterable[Any], *, color: str | None = None) -> dict:
    """One line / bar group. points: (x, y) pairs or {"x", "y"} dicts."""
    out = []
    for point in points:
        if isinstance(point, Mapping):
            out.append(dict(point))
        else:
            x, y = point[0], point[1]
            out.append({"x": x, "y": y})
    return _clean({"name": name, "color": color, "points": out})


def chart(id: str, series: Iterable[dict] | dict, *, kind: str = "line", title: str | None = None,
          caption: str | None = None, x_label: str | None = None, y_label: str | None = None,
          y_format: str | None = None) -> dict:
    """kind: line, area, bar, scatter. y_format: number, currency, percent (0.12 = 12%), compact."""
    lines = [series] if isinstance(series, Mapping) else _list(series)
    return _clean({"type": "chart", "id": id, "chart": kind, "title": title, "caption": caption,
                   "x_label": x_label, "y_label": y_label, "y_format": y_format, "series": lines})


def timeline_item(at: str, title: str, *, id: str | None = None, detail: str | None = None, tone: str | None = None) -> dict:
    return _clean({"id": id, "at": at, "title": title, "detail": detail, "tone": tone})


def timeline(id: str, items: Iterable[dict], *, title: str | None = None) -> dict:
    """Dated events. `at` is display text (not parsed)."""
    return _clean({"type": "timeline", "id": id, "title": title, "items": _list(items)})


# Pictures


def image(id: str, src: str, alt: str, *, caption: str | None = None, aspect: str | None = None) -> dict:
    """src: an https URL or a /v1/media/ path from HubClient.upload_media(). aspect: 16:9, 4:3, 1:1, 3:2."""
    return _clean({"type": "image", "id": id, "src": src, "alt": alt, "caption": caption, "aspect": aspect})


media = image


def hero(id: str, title: str, *, eyebrow: str | None = None, subtitle: str | None = None,
         image_src: str | None = None, image_alt: str | None = None, actions: Sequence[dict] = ()) -> dict:
    img = {"src": image_src, "alt": image_alt or title} if image_src else None
    return _clean({"type": "hero", "id": id, "eyebrow": eyebrow, "title": title, "subtitle": subtitle,
                   "image": img, "actions": _list(actions) or None})


# Buttons and actions


def button(label: str, *, id: str | None = None, style: str = "primary", url: str | None = None,
           open: Mapping[str, str] | None = None, agent: str | None = None, post: str | None = None,  # noqa: A002
           value: Mapping[str, Any] | None = None, confirm: str | None = None,
           run_agent: str | Mapping[str, str] | None = None, machine: str | None = None, agent_name: str | None = None,
           cwd: str | None = None, title: str | None = None) -> dict:
    """A tap that opens url, opens a page (open={"section_id", "node_id"?, "tab_id"?}), prefills the
    Desk with a prompt (agent=...), posts an action back to your feed (post="server.restart"),
    or opens Run agent filled in (run_agent="the prompt", machine=, agent_name=, cwd=, title=; or
    run_agent={...} with those keys). Run agent waits for the person to start it.

    A posted action reaches HubClient.get_actions() with item_id = this button's id, and the button
    shows as pending until your next push.
    """
    action = None
    if post:
        action = _clean({"type": "post", "action": post, "value": dict(value or {}), "confirm": confirm})
    elif run_agent is not None:
        fields = dict(run_agent) if isinstance(run_agent, Mapping) else {"prompt": run_agent}
        for key, given in (("machine", machine), ("agent", agent_name), ("cwd", cwd), ("title", title)):
            if given is not None:
                fields[key] = given
        action = _clean({"type": "run_agent", **fields})
    elif agent:
        action = {"type": "agent", "prompt": agent}
    elif open:
        action = {"type": "open", **dict(open)}
    return _clean({"id": id or slug(label, 40), "label": label, "style": style, "url": url, "action": action})


def buttons(id: str, items: Iterable[dict]) -> dict:
    """Up to six buttons."""
    return {"type": "buttons", "id": id, "items": _list(items)}


actions = buttons


def progress_item(label: str, value: float, *, id: str | None = None, max: float | None = None,  # noqa: A002
                  display: str | None = None, caption: str | None = None, tone: str | None = None) -> dict:
    """One bar: value out of max (100 when omitted, shown as a percent). display replaces the shown number."""
    return _clean({"id": id or slug(label), "label": label, "value": value, "max": max, "display": display,
                   "caption": caption, "tone": tone})


def progress(id: str, items: Iterable[dict], *, title: str | None = None) -> dict:
    """Labelled bars: disk use, a goal, a backup."""
    return _clean({"type": "progress", "id": id, "title": title, "items": _list(items)})


def status_item(label: str, status: str, *, id: str | None = None, detail: str | None = None,
                since: str | None = None, url: str | None = None) -> dict:
    """One row. status: up, down, degraded, unknown (or ok, online, failed, slow, ...). since: ISO 8601 time."""
    return _clean({"id": id or slug(label), "label": label, "status": status, "detail": detail, "since": since, "url": url})


def status(id: str, items: Iterable[dict], *, title: str | None = None) -> dict:
    """Up / down rows. Home lists the block when a row is down or degraded."""
    return _clean({"type": "status", "id": id, "title": title, "items": _list(items)})


def link(label: str, url: str, *, detail: str | None = None, icon: str | None = None) -> dict:
    """One place to go. icon is an SF Symbol name."""
    return _clean({"label": label, "url": url, "detail": detail, "icon": icon})


def links(id: str, items: Iterable[dict], *, title: str | None = None) -> dict:
    return _clean({"type": "links", "id": id, "title": title, "items": _list(items)})


def task(id: str, title: str, *, done: bool = False, due: str | None = None, note: str | None = None,
         group: str | None = None, status: str | None = None, tags: Sequence[str] = (), can_toggle: bool = True) -> dict:
    """One to-do. due is YYYY-MM-DD. status: ready, waiting, blocked, contingent, later, done."""
    return _clean({"id": id, "title": title, "done": done, "due": due, "note": note, "group": group, "status": status,
                   "tags": _list(tags) or None, "can_toggle": can_toggle})


def tasks(id: str, items: Iterable[dict], *, title: str | None = None, add_label: str | None = None,
          empty_text: str | None = None) -> dict:
    """A checklist people tick in the app (task.set_done) and add to (task.add, when add_label is set)."""
    return _clean({"type": "tasks", "id": id, "title": title, "items": _list(items), "empty_text": empty_text,
                   "add": {"label": add_label} if add_label else None})


# Structured blocks the app also adds up across sections


def ledger_day(date: str, amount: float, *, count: int | None = None, net: float | None = None, **tags: str) -> dict:
    """One day of money. tags: product, category, source, space."""
    return _clean({"date": date, "amount": amount, "count": count, "net": net, **tags})


def ledger(id: str, days: Iterable[dict], *, title: str | None = None, currency: str = "USD", source: str | None = None,
           space: str | None = None, entries: Iterable[dict] = (), connected: bool = True, note: str | None = None,
           as_of: str | None = None, company: str | None = None) -> dict:
    """Money for one source (sales, donations, subscriptions): daily rows plus recent entries.
    space groups it with other money (company is accepted as the older name)."""
    return _clean({"type": "ledger", "id": id, "title": title, "currency": currency, "source": source, "space": space or company,
                   "connected": connected, "note": note, "as_of": as_of or now_iso(), "days": _list(days),
                   "entries": _list(entries)})


def series_metric(id: str, label: str, points: Iterable[Any], *, format: str | None = None,  # noqa: A002
                  platform: str | None = None, stat: str | None = None, group: str | None = None) -> dict:
    """One daily line for series(). points: (YYYY-MM-DD, y) pairs. format: number, usd, percent, duration."""
    rows = [dict(p) if isinstance(p, Mapping) else {"x": p[0], "y": p[1]} for p in points]
    return _clean({"id": id, "label": label, "format": format, "platform": platform, "stat": stat, "group": group, "points": rows})


def series(id: str, metrics: Iterable[dict], *, kind: str, product: str, space: str | None = None, title: str | None = None,
           caption: str | None = None, site: str | None = None, window: int | None = None, company: str | None = None) -> dict:
    """Daily numbers for one product. kind: social, site, usage, players, search, health, money, other.
    space groups it with other numbers (company is accepted as the older name)."""
    return _clean({"type": "series", "id": id, "kind": kind, "space": space or company, "product": product, "title": title,
                   "caption": caption, "site": site, "window": window, "as_of": now_iso(), "metrics": _list(metrics)})


def product(id: str = "product", *, kind: str = "app", headline: str | None = None, tone: str | None = None,
            space: str | None = None, metrics: Iterable[dict] = (), releases: Iterable[dict] = (),
            ratings: Iterable[dict] = (), links: Iterable[dict] = (), company: str | None = None) -> dict:
    """The status card for one app, game, site, or service (kind). Home shows one tile per product block."""
    return _clean({"type": "product", "id": id, "kind": kind, "headline": headline, "tone": tone, "space": space or company,
                   "checked_at": now_iso(), "metrics": _list(metrics) or None, "releases": _list(releases) or None,
                   "ratings": _list(ratings) or None, "links": _list(links) or None})
