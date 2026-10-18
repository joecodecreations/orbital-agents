"""Headlines from RSS 2.0 or Atom feeds.

Shows: a tabs section (one tab for everything, then one per feed), a timeline of the latest
items, bullet lists of markdown links, a count of what is new since the last run, and a warning
callout for a feed that could not be read. Lives in the "Daily" space. No API keys.

    python feeds/examples/rss_headlines.py --feed https://hnrss.org/frontpage --feed https://example.com/feed.xml [--dry-run]
"""

from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import feedkit  # noqa: E402

from orbital.feeds import blocks as b  # noqa: E402

FEED = "rss-headlines"
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_BYTES = 5_000_000
KEEP_SEEN = 500


def _when(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse(xml_text: str | bytes, source_url: str) -> dict:
    """{"title", "link", "items": [{"id", "title", "link", "t"}]} from RSS 2.0 or Atom."""
    root = ET.fromstring(xml_text)
    items = []
    if root.tag == f"{ATOM}feed":
        title = (root.findtext(f"{ATOM}title") or source_url).strip()
        site = next((l.get("href") for l in root.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"), source_url)
        for entry in root.findall(f"{ATOM}entry"):
            link = next((l.get("href") for l in entry.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"), "")
            items.append({"id": (entry.findtext(f"{ATOM}id") or link).strip(), "title": (entry.findtext(f"{ATOM}title") or "").strip(),
                          "link": link, "t": _when(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"))})
    else:
        channel = root.find("channel")
        if channel is None:
            raise ValueError("not an RSS or Atom feed")
        title = (channel.findtext("title") or source_url).strip()
        site = (channel.findtext("link") or source_url).strip()
        for item in channel.findall("item"):
            link = (item.findtext("link") or "").strip()
            items.append({"id": (item.findtext("guid") or link or item.findtext("title") or "").strip(),
                          "title": (item.findtext("title") or "").strip(), "link": link, "t": _when(item.findtext("pubDate"))})
    items = [i for i in items if i["title"]]
    return {"title": title, "link": site, "url": source_url, "items": items}


def fetch(url: str, client: httpx.Client) -> dict:
    response = client.get(url)
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError("feed is larger than 5 MB")
    return parse(response.content, url)


def gather(urls: list[str]) -> tuple[list[dict], list[str]]:
    feeds, problems = [], []
    with feedkit.http_client(15) as client:
        for url in urls:
            try:
                feeds.append(fetch(url, client))
            except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
                problems.append(f"{url}: {exc.__class__.__name__}: {str(exc)[:120]}")
    return feeds, problems


def remember(state: dict, feeds: list[dict]) -> tuple[dict, int]:
    seen = state.setdefault("seen", {})
    new = 0
    for feed in feeds:
        known = seen.get(feed["url"])
        ids = [i["id"] for i in feed["items"]]
        if known is not None:
            new += sum(1 for i in ids if i not in known)
        seen[feed["url"]] = (ids + [i for i in (known or []) if i not in ids])[:KEEP_SEEN]
    return state, new


def _link(item: dict) -> str:
    title = item["title"].replace("[", "(").replace("]", ")")
    return f"[{title}]({item['link']})" if item["link"].startswith(("https://", "http://")) else title


def build(feeds: list[dict], problems: list[str], new: int, *, limit: int = 10, section_id: str = "headlines") -> dict:
    merged = [dict(item, source=feed["title"]) for feed in feeds for item in feed["items"][:limit]]
    merged.sort(key=lambda i: i["t"] or 0, reverse=True)
    everything: list[dict] = []
    for index, problem in enumerate(problems):
        everything.append(b.callout(f"problem-{index}", problem, tone="warning", title="A feed could not be read"))
    everything.append(b.metrics("counts", [b.metric("Headlines", str(len(merged)), id="items"),
                                           b.metric("New since last run", str(new), id="new"),
                                           b.metric("Feeds", f"{len(feeds)} of {len(feeds) + len(problems)}", id="feeds")]))
    everything.append(b.timeline("latest", [
        b.timeline_item(feedkit.local_time(i["t"], "%b %d %H:%M") if i["t"] else "-", i["title"][:200],
                        id=f"t{n}", detail=i["source"][:80]) for n, i in enumerate(merged[:20])], title="Latest"))
    tabs = [b.tab("all", "All", everything, icon="newspaper")]
    used: set[str] = {"all"}
    for feed in feeds:
        ident = b.slug(feed["title"], 40)
        while ident in used:
            ident = f"{ident}-x"
        used.add(ident)
        blocks_for_feed = [b.list("items", [_link(i) for i in feed["items"][:limit]], title=feed["title"][:120])]
        if feed["link"].startswith(("https://", "http://")):
            blocks_for_feed.append(b.buttons("open", [b.button("Open site", url=feed["link"], style="secondary")]))
        tabs.append(b.tab(ident, feed["title"][:40], blocks_for_feed))
    return b.section(section_id, "Headlines", icon="newspaper", order=50, tabs=tabs,
                     summary=f"{len(merged)} headlines · {new} new"[:80],
                     space="daily", space_title="Daily", space_icon="sun.max.fill")


def main(argv: list[str] | None = None, client=None) -> int:
    parser = feedkit.arg_parser(__doc__.splitlines()[0])
    parser.add_argument("--feed", action="append", help="RSS or Atom URL (repeat for more)")
    parser.add_argument("--limit", type=int, default=10, help="items per feed")
    parser.add_argument("--section-id", default="headlines")
    args = parser.parse_args(argv)
    urls = args.feed or ["https://hnrss.org/frontpage"]
    feeds, problems = gather(urls)
    state, new = remember(feedkit.load_state(FEED, args.home), feeds)
    section = build(feeds, problems, new, limit=args.limit, section_id=args.section_id)
    result = feedkit.push(section, [], dry_run=args.dry_run, client=client, home=args.home)
    if not args.dry_run:
        feedkit.save_state(FEED, state, args.home)
    feedkit.result_line("rss", pushed=1 if not args.dry_run else 0, feeds=len(feeds),
                        items=sum(len(f["items"][:args.limit]) for f in feeds), new=new,
                        errors=len(problems), revision=result["revision"])
    for problem in problems:
        print(f"feed: error {problem}", file=sys.stderr)
    return 0 if feeds else 1


if __name__ == "__main__":
    sys.exit(feedkit.run_feed(main))
