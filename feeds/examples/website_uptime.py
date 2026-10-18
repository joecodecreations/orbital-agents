"""Website uptime: is each URL answering, and how fast.

Shows: a status table, a latency chart with one line per site (kept across runs), an incident
timeline, a danger callout while something is down, and an alert when a site goes down or comes
back. Lives in the "Home Lab" space. No API keys.

    python feeds/examples/website_uptime.py --url https://example.com --url https://example.org [--dry-run]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import feedkit  # noqa: E402

from orbital.feeds import blocks as b  # noqa: E402

FEED = "website-uptime"
KEEP_CHECKS = 288
KEEP_INCIDENTS = 30
COLORS = ["#4F8EF7", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#EC4899"]


def host_of(url: str) -> str:
    return urlparse(url).netloc or url


def check(url: str, timeout: float = 10.0, client: httpx.Client | None = None) -> dict:
    """One GET. ok when it answers below HTTP 400 in time."""
    own = client is None
    client = client or feedkit.http_client(timeout)
    started = time.monotonic()
    try:
        response = client.get(url)
        ms = round((time.monotonic() - started) * 1000)
        return {"url": url, "ok": response.status_code < 400, "code": response.status_code, "ms": ms,
                "detail": f"HTTP {response.status_code}", "t": time.time()}
    except httpx.TimeoutException:
        return {"url": url, "ok": False, "code": None, "ms": None, "detail": f"no answer in {timeout:g} s", "t": time.time()}
    except httpx.HTTPError as exc:
        return {"url": url, "ok": False, "code": None, "ms": None, "detail": exc.__class__.__name__, "t": time.time()}
    finally:
        if own:
            client.close()


def gather(urls: list[str], timeout: float) -> list[dict]:
    with feedkit.http_client(timeout) as client:
        return [check(url, timeout, client) for url in urls]


def remember(state: dict, results: list[dict]) -> tuple[dict, list[dict]]:
    """Record each check; return the status changes (incidents) this run found."""
    sites = state.setdefault("sites", {})
    changes = []
    for result in results:
        site = sites.setdefault(result["url"], {"history": [], "up": None, "since": result["t"]})
        if site["up"] is not result["ok"]:
            if site["up"] is not None or not result["ok"]:
                changes.append({"url": result["url"], "ok": result["ok"], "detail": result["detail"], "t": result["t"],
                                "first": site["up"] is None})
            site["up"] = result["ok"]
            site["since"] = result["t"]
        site["history"].append({"t": result["t"], "ok": result["ok"], "ms": result["ms"], "code": result["code"]})
        del site["history"][:-KEEP_CHECKS]
    incidents = state.setdefault("incidents", [])
    incidents.extend(changes)
    del incidents[:-KEEP_INCIDENTS]
    for url in list(sites):
        if url not in {r["url"] for r in results}:
            del sites[url]
    return state, changes


def build(results: list[dict], state: dict, changes: list[dict], *, section_id: str = FEED,
          title: str = "Website uptime") -> tuple[dict, list[dict]]:
    now = time.time()
    sites = state.get("sites") or {}
    up = [r for r in results if r["ok"]]
    down = [r for r in results if not r["ok"]]
    timed = [r["ms"] for r in up if r["ms"] is not None]
    avg = round(sum(timed) / len(timed)) if timed else None
    slowest = max(up, key=lambda r: r["ms"] or 0) if up else None

    rows = []
    for r in results:
        site = sites.get(r["url"]) or {}
        history = site.get("history") or []
        day = [h for h in history if now - h["t"] <= 86400]
        uptime = f"{100 * sum(1 for h in day if h['ok']) / len(day):.1f}%" if day else "-"
        since = feedkit.ago(now - site.get("since", now))
        rows.append([host_of(r["url"]), "Up" if r["ok"] else "Down", r["code"] or r["detail"],
                     f"{r['ms']} ms" if r["ms"] is not None else "-", since, uptime])

    page: list[dict] = []
    if down:
        page.append(b.callout("down-now", ", ".join(f"{host_of(r['url'])} ({r['detail']})" for r in down),
                              tone="danger", title=f"{len(down)} of {len(results)} down"))
    tiles = [b.metric("Sites up", f"{len(up)} of {len(results)}", id="up",
                      caption="all answering" if not down else f"{len(down)} down")]
    if avg is not None:
        tiles.append(b.metric("Average response", f"{avg} ms", id="avg"))
    if slowest:
        tiles.append(b.metric("Slowest", f"{slowest['ms']} ms", id="slowest", caption=host_of(slowest["url"])))
    page.append(b.metrics("now", tiles))
    page.append(b.table("sites", ["Site", "Status", "Code", "Response", "For", "24 h uptime"], rows, title="Sites"))
    lines = []
    for index, r in enumerate(results):
        history = (sites.get(r["url"]) or {}).get("history") or []
        points = [(feedkit.local_time(h["t"]), h["ms"]) for h in history if h["ms"] is not None and now - h["t"] <= 86400]
        lines.append(b.chart_series(host_of(r["url"]), points, color=COLORS[index % len(COLORS)]))
    page.append(b.chart("latency", lines, kind="line", title="Response time, last 24 hours", y_label="ms"))
    incidents = list(reversed(state.get("incidents") or []))[:10]
    if incidents:
        page.append(b.timeline("incidents", [
            b.timeline_item(feedkit.local_time(i["t"], "%b %d %H:%M"), f"{host_of(i['url'])} {'is back up' if i['ok'] else 'went down'}",
                            id=f"i{int(i['t'])}-{b.slug(host_of(i['url']), 30)}", detail=i["detail"],
                            tone="success" if i["ok"] else "danger")
            for i in incidents], title="Status changes"))
    page.append(b.buttons("open", [b.button(f"Open {host_of(r['url'])}"[:40], url=r["url"], style="secondary",
                                            id=f"open-{b.slug(host_of(r['url']), 30)}")
                                   for r in results[:6]]))

    alerts = []
    for change in changes:
        host = host_of(change["url"])
        if change["ok"]:
            alerts.append({"id": f"uptime-{b.slug(host, 30)}-up-{int(change['t'])}", "title": f"{host} is back up",
                           "body": f"{change['url']} answers again ({change['detail']}).", "level": "info"})
        else:
            alerts.append({"id": f"uptime-{b.slug(host, 30)}-down-{int(change['t'])}", "title": f"{host} is down",
                           "body": f"{change['url']} did not answer properly: {change['detail']}.", "level": "error"})
    summary = "All sites up" if not down else f"{len(down)} down: {', '.join(host_of(r['url']) for r in down)}"
    section = b.section(section_id, title, page, icon="globe", order=20, subtitle="Checked by the scheduler",
                        summary=summary[:80], space="home-lab", space_title="Home Lab", space_icon="server.rack")
    return section, alerts


def main(argv: list[str] | None = None, client=None) -> int:
    parser = feedkit.arg_parser(__doc__.splitlines()[0])
    parser.add_argument("--url", action="append", help="a URL to check (repeat for more); default https://example.com")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--title", default="Website uptime")
    parser.add_argument("--section-id", default=FEED)
    args = parser.parse_args(argv)
    urls = args.url or ["https://example.com"]
    results = gather(urls, args.timeout)
    state, changes = remember(feedkit.load_state(FEED, args.home), results)
    section, alerts = build(results, state, changes, section_id=args.section_id, title=args.title)
    result = feedkit.push(section, alerts, dry_run=args.dry_run, client=client, home=args.home)
    if not args.dry_run:
        feedkit.save_state(FEED, state, args.home)
    feedkit.result_line("uptime", pushed=1 if not args.dry_run else 0, sites=len(results),
                        up=sum(1 for r in results if r["ok"]), down=sum(1 for r in results if not r["ok"]),
                        alerts=result["alerts"], revision=result["revision"])
    return 0


if __name__ == "__main__":
    sys.exit(feedkit.run_feed(main))
