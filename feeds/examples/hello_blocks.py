"""Hello, blocks: one page that uses every block type once, and answers its own buttons.

A tour of the data pipeline, not a real data source. It shows each block type (heading, paragraph,
bullets, callout, metrics, key_value, table, timeline, divider, chart, image, hero, buttons, tasks,
ledger, product, series) across four tabs, uploads an image with upload_media(), updates one block
in place with patch_section(), raises one alert the first time it runs, and answers the taps people
make in the app (the Ping button, ticking or adding a to-do). Every figure on it is labelled as a
demo; the ledger's amounts are all zero so your Money totals stay true. Lives in the "Demo" space.

    python feeds/examples/hello_blocks.py [--dry-run] [--no-upload]
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import feedkit  # noqa: E402

from orbital.feeds import blocks as b  # noqa: E402

FEED = "hello-blocks"
SECTION = "hello"
IMAGE = Path(__file__).resolve().parents[2] / "docs" / "assets" / "icon-128.png"
DEFAULT_TASKS = [
    {"id": "try-a-feed", "title": "Copy feeds/jobs.example.json to ~/.orbital-agents/jobs.json", "done": False},
    {"id": "tap-ping", "title": "Tap Ping on the Actions tab", "done": False},
    {"id": "write-your-own", "title": "Write your own feed (see feeds/README.md)", "done": False},
]


def handle_actions(client, state: dict) -> list[str]:
    """Answer the taps waiting for this section: hello.ping, task.set_done, task.add."""
    done: list[str] = []
    tasks = state.setdefault("tasks", [dict(t) for t in DEFAULT_TASKS])
    for action in client.get_actions(SECTION):
        kind = action.get("type") or action.get("action")
        value = action.get("value") or {}
        if kind == "hello.ping":
            message = f"Pong at {time.strftime('%H:%M:%S')}"
            state["last_ping"] = message
        elif kind == "task.set_done":
            item = next((t for t in tasks if t["id"] == (action.get("item_id") or value.get("id"))), None)
            if item is None:
                client.post_action_result(SECTION, action["id"], "failed", "That to-do is gone.")
                continue
            item["done"] = bool(value.get("done", True))
            message = f"Marked {'done' if item['done'] else 'not done'}"
        elif kind == "task.add":
            title = str(value.get("title") or "").strip()[:200]
            if not title:
                client.post_action_result(SECTION, action["id"], "failed", "A to-do needs a title.")
                continue
            tasks.append({"id": b.slug(f"{title}-{int(time.time())}", 60), "title": title, "done": False})
            message = "Added"
        else:
            client.post_action_result(SECTION, action["id"], "failed", f"hello does not know {kind}")
            continue
        client.post_action_result(SECTION, action["id"], "applied", message)
        done.append(kind)
    return done


def build(state: dict, *, image_src: str, today: date | None = None) -> dict:
    today = today or date.today()
    runs = int(state.get("runs", 0))
    days = [(today - timedelta(days=n)).isoformat() for n in range(13, -1, -1)]
    demo_wave = [(d, 10 + (i * 7) % 11) for i, d in enumerate(days)]
    tasks = state.get("tasks") or [dict(t) for t in DEFAULT_TASKS]

    text_tab = [
        b.heading("welcome", "Hello from Orbital Agents", level=1),
        b.text("intro", "This page is pushed by `feeds/examples/hello_blocks.py`. Every **block type** "
                        "appears once; see [the data pipeline docs](https://example.com/docs) for each one's fields."),
        b.list("steps", ["A feed gathers data", "builds a section with `orbital.feeds.blocks`",
                         "and pushes it with `HubClient.put_section()`"], title="How a feed works", ordered=True),
        b.callout("info", "Callouts come in four tones.", tone="info", title="Info"),
        b.callout("success", "Everything worked.", tone="success", title="Success"),
        b.callout("warning", "Something needs a look.", tone="warning", title="Warning"),
        b.callout("danger", "Something is broken.", tone="danger", title="Danger"),
        b.divider("split"),
        b.key_value("facts", {"Feed": FEED, "Runs so far": runs, "Last ping": state.get("last_ping") or "not yet"},
                    title="Key / value"),
        b.timeline("story", [b.timeline_item("Step 1", "Gather", id="gather", detail="read your source", tone="info"),
                             b.timeline_item("Step 2", "Build", id="build", detail="blocks → section"),
                             b.timeline_item("Step 3", "Push", id="push", detail="PUT /v1/sections/hello", tone="success")],
                   title="Timeline"),
    ]
    numbers_tab = [
        b.metrics("tiles", [b.metric("Runs", str(runs), id="runs", caption="demo counter"),
                            b.metric("Up", "+3", id="up", delta="+3", direction="up"),
                            b.metric("Down", "-2", id="down", delta="-2", direction="down"),
                            b.metric("Flat", "0", id="flat", delta="0", direction="flat")]),
        b.table("table", ["Name", "Kind", "Value"], [["alpha", "demo", "1"], ["beta", "demo", "2"], ["gamma", "demo", "3"]],
                title="Table"),
        b.chart("line", [b.chart_series("Demo A", demo_wave), b.chart_series("Demo B", [(d, y / 2) for d, y in demo_wave])],
                kind="line", title="Line chart", caption="Demo numbers"),
        b.chart("bar", b.chart_series("Demo", [("Mon", 3), ("Tue", 5), ("Wed", 2), ("Thu", 6), ("Fri", 4)]),
                kind="bar", title="Bar chart"),
        b.series("series", [b.series_metric("demo", "Demo value", demo_wave)], kind="other",
                 company="Orbital Agents demo", product="Hello feed", title="Series (daily, for the dashboards)", window=14),
        b.ledger("ledger", [b.ledger_day(d, 0.0, product="Hello feed") for d in days[-7:]], title="Ledger (all zero)",
                 company="Orbital Agents demo", source="demo", note="Demo ledger: every amount is zero so your Money totals stay true."),
        b.product("product", kind="service", headline="Demo product card", tone="info", company="Orbital Agents demo",
                  metrics=[b.metric("Runs", str(runs))]),
    ]
    media_tab = [
        b.hero("hero", "Orbital Agents", eyebrow="Hero block", subtitle="Big title, picture and buttons.",
               image_src=image_src, image_alt="Orbital Agents icon",
               actions=[b.button("Open the demo site", url="https://example.com", id="hero-site")]),
        b.image("picture", image_src, "Orbital Agents icon", caption="Uploaded with HubClient.upload_media()", aspect="1:1"),
    ]
    actions_tab = [
        b.buttons("buttons", [
            b.button("Ping", post="hello.ping", id="ping"),
            b.button("Open example.com", url="https://example.com", style="secondary", id="site"),
            b.button("Ask an agent", agent="Explain what the hello feed does.", style="secondary", id="ask"),
            b.button("Website uptime", open={"section_id": "website-uptime"}, style="link", id="uptime"),
        ]),
        b.tasks("todo", [b.task(t["id"], t["title"], done=bool(t.get("done"))) for t in tasks], title="To-dos",
                add_label="Add a to-do", empty_text="Nothing left. Nice."),
    ]
    return b.section(SECTION, "Hello, blocks", icon="hand.wave.fill", order=90,
                     subtitle="Every block type, once", summary=f"Demo page · {runs} runs",
                     tabs=[b.tab("text", "Text", text_tab, icon="text.alignleft"),
                           b.tab("numbers", "Numbers", numbers_tab, icon="chart.bar"),
                           b.tab("media", "Media", media_tab, icon="photo"),
                           b.tab("actions", "Actions", actions_tab, icon="hand.tap")],
                     space="demo", space_title="Demo", space_icon="sparkles")


def main(argv: list[str] | None = None, client=None) -> int:
    parser = feedkit.arg_parser(__doc__.splitlines()[0])
    parser.add_argument("--no-upload", action="store_true", help="skip the image upload (uses a placeholder)")
    args = parser.parse_args(argv)
    state = feedkit.load_state(FEED, args.home)
    state["runs"] = int(state.get("runs", 0)) + 1
    if args.dry_run:
        section = build(state, image_src="/v1/media/placeholder")
        feedkit.push(section, dry_run=True)
        feedkit.result_line("hello", pushed=0, blocks=sum(len(t["blocks"]) for t in section["tabs"]), actions=0, alerts=0)
        return 0
    own = client is None
    if own:
        from orbital.feeds import HubClient

        client = HubClient.from_config(feedkit.config(args.home))
    try:
        handled = handle_actions(client, state)
        image_src = state.get("image_src") or "/v1/media/placeholder"
        if not args.no_upload and IMAGE.is_file() and not state.get("image_src"):
            image_src = client.upload_media(IMAGE)["url"]
            state["image_src"] = image_src
        section = build(state, image_src=image_src)
        answer = client.put_section(section) or {}
        # A partial update: change one block without sending the rest.
        client.upsert_block(SECTION, b.metrics("tiles", [b.metric("Runs", str(state["runs"]), id="runs",
                                                                  caption=f"updated in place at {time.strftime('%H:%M')}"),
                                                         b.metric("Up", "+3", id="up", delta="+3", direction="up"),
                                                         b.metric("Down", "-2", id="down", delta="-2", direction="down"),
                                                         b.metric("Flat", "0", id="flat", delta="0", direction="flat")]),
                            tab_id="numbers")
        alerts = 0
        if not state.get("welcomed"):
            client.alert("Hello from Orbital Agents", "Your first feed reached the hub. Open the Demo space to see every block type.",
                         section_id=SECTION, level="info", id="hello-welcome")
            state["welcomed"] = True
            alerts = 1
        feedkit.save_state(FEED, state, args.home)
    finally:
        if own:
            client.close()
    feedkit.result_line("hello", pushed=1, blocks=sum(len(t["blocks"]) for t in section["tabs"]),
                        actions=len(handled), alerts=alerts, revision=answer.get("revision"))
    return 0


if __name__ == "__main__":
    sys.exit(feedkit.run_feed(main))
