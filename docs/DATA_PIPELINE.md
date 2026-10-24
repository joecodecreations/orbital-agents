# The data pipeline: put your own data in the app

The Orbital Agents app shows whatever your scripts send to the hub. A home server's disks, a website's response time, a GitHub repo's stars, the weather, your step count, a shop's sales, a reading list: if a script can read it, the app can show it, notify you about it, and give you buttons that act on it.

This page explains every piece, then walks you through your first feed.

```
 your script (a "feed")                hub (port 7700)                       Orbital Agents app
 ──────────────────────                ───────────────                       ──────────────────
 reads a source        ── section ──▶  stores one JSON page per id   ──live──▶ draws the page
 builds blocks         ── ops ──────▶  patches it by id                        (iPhone, Mac)
                       ── alert ────▶  notifies once per alert id    ──push──▶ notification
                       ── pulse ────▶  keeps the newest live value   ──live──▶ live tile
 applies the taps      ◀── actions ──  queues button taps            ◀─────── you tap a button
 answers them          ── results ──▶  marks them applied / failed   ──live──▶ "Restarted"
```

## Contents

- [The pieces](#the-pieces): sections, layouts, spaces, blocks, alerts, actions, pulse, media
- [Three ways to push](#three-ways-to-push): `orbital push`, the Python SDK, plain HTTP
- [Build your first feed in 10 minutes](#build-your-first-feed-in-10-minutes)
- [Sections](#sections)
- [Block reference](#block-reference): every block type with an example
- [Partial updates (ops)](#partial-updates-ops)
- [Alerts and notification levels](#alerts-and-notification-levels)
- [Actions: buttons that come back to your feed](#actions-buttons-that-come-back-to-your-feed)
- [Media](#media)
- [Pulse: live numbers](#pulse-live-numbers)
- [Python SDK reference](#python-sdk-reference)
- [HTTP reference](#http-reference)
- [Validation and errors](#validation-and-errors)
- [Limits](#limits)
- [Schemas](#schemas)
- [Rules for healthy feeds](#rules-for-healthy-feeds)

## The pieces

| Piece | What it is | In the app |
|---|---|---|
| **Section** | One page, owned by one feed, with an id like `home-server`. Pushing it again replaces it. | a page in Browse, and a tile in its space |
| **Layout** | `page` (a list of blocks), `tabs` (tabs of blocks), or `sidebar` (nodes, each a page or tabs: one per server, repo, site) | how the page is laid out |
| **Space** | A group of sections: a project, a household, a server fleet. Any section that sets the same `space` joins it. | the home tiles |
| **Block** | One piece of a page: metrics, a chart, a table, buttons, a to-do list… | cards on the page |
| **Alert** | A notification. Its id is a natural key: the same id never notifies twice. | a notification and a row in the Alerts inbox |
| **Action** | A tap in the app (a button, a ticked to-do) waiting for the feed that owns the page | the button shows as pending until your feed answers |
| **Pulse** | A live number that changes every minute. Never bumps the page or notifies. | a live tile with a beating dot |
| **Media** | An image you upload once and reference by URL | images in blocks and section icons |

The hub stores what you send and checks its shape. It never runs your feed and never invents data. The scheduler ([FEEDS.md](FEEDS.md)) runs feeds on a timetable, but any script, cron job or agent can push.

## Three ways to push

All three use the **automation token** (`ORBITAL_AUTOMATION_TOKEN` in `~/.orbital-agents/secrets.env`, created by the setup wizard). On the hub it is already there. On another machine, copy that one line into its `secrets.env`, or set the environment variable.

### 1. `orbital push` (any JSON file)

```bash
orbital push home-server.json                         # PUT the section (its "id" names it)
orbital push home-server.json --validate              # check only, write nothing, print warnings
orbital push ops.json --section home-server           # a file holding {"ops": [...]} patches one section
cat home-server.json | orbital push -                 # read from stdin
orbital push --alert "Backup failed" --body "Exit code 2" --section home-server --level warning
orbital push home-server.json --json                  # print the hub's full answer
```

It prints `home-server: revision 5` (or `alert <id>`). Exit code 1 means the hub refused; the message says why and how to fix it.

### 2. The Python SDK (`orbital.feeds`)

```python
from orbital.feeds import HubClient, blocks as b

with HubClient.from_config() as hub:        # hub address from config.toml, token from secrets.env
    hub.put_section(b.section("home-server", "Home server", [
        b.metrics("vitals", [b.metric("CPU", "12%"), b.metric("Disk free", "412 GB")]),
        b.chart("load", b.chart_series("Load", [("09:00", 0.4), ("10:00", 0.9)]), title="Load"),
    ], icon="server.rack", space="home-lab", space_title="Home Lab"))
    hub.alert("Backup failed", "The nightly backup exited with code 2.", section_id="home-server", level="warning")
```

The SDK is plain Python plus `httpx`. It is installed with the repo (`pip install -e .`, which `./setup` runs). Every function is listed in the [SDK reference](#python-sdk-reference).

### 3. Plain HTTP (any language, curl)

```bash
TOKEN=$(grep '^ORBITAL_AUTOMATION_TOKEN=' ~/.orbital-agents/secrets.env | cut -d= -f2-)
HUB=http://127.0.0.1:7700        # on another machine: http://<hub>.<your-tailnet>.ts.net:7700

curl -s -X PUT "$HUB/v1/sections/home-server" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data @home-server.json
```

The answer is the stored section with its new `revision`. Never print the token or paste it into a section, a log or a chat.

## Build your first feed in 10 minutes

You will build a **reading list**: the books you want to read, kept in a small JSON file. The app shows totals, a checklist you can tick from your phone, and a table. Ticking a book or adding one in the app comes back to your file.

**1. Check the hub answers.**

```bash
orbital status          # "Hub ... reachable"
```

**2. Make the source.** Any data works; here it is a file you edit by hand:

```bash
cat > ~/reading.json <<'EOF'
{"books": [
  {"id": "dune", "title": "Dune", "pages": 412, "done": true},
  {"id": "the-hobbit", "title": "The Hobbit", "pages": 310, "done": false}
]}
EOF
```

**3. Write the feed.** Save this as `~/orbital-feeds/reading_list.py`:

```python
"""Reading list: the books on your list, from a JSON file you edit by hand."""

import argparse
import json
from pathlib import Path

from orbital.feeds import HubClient, HubError, blocks as b

SECTION = "reading-list"
SOURCE = Path("~/reading.json").expanduser()


def load() -> dict:
    if SOURCE.exists():
        return json.loads(SOURCE.read_text())
    return {"books": []}


def save(data: dict) -> None:
    SOURCE.write_text(json.dumps(data, indent=2) + "\n")


def handle_taps(hub: HubClient, data: dict) -> int:
    """Apply the taps people made in the app since the last run."""
    handled = 0
    for tap in hub.get_actions(SECTION):
        book = next((x for x in data["books"] if x["id"] == tap.get("item_id")), None)
        if tap["type"] == "task.set_done" and book:
            book["done"] = tap["value"]["done"]
            hub.post_action_result(SECTION, tap["id"], "applied", "Saved")
        elif tap["type"] == "task.add":
            title = tap["value"]["title"]
            data["books"].append({"id": b.slug(title), "title": title, "pages": 0, "done": False})
            hub.post_action_result(SECTION, tap["id"], "applied", f"Added {title}")
        else:
            hub.post_action_result(SECTION, tap["id"], "failed", "Unknown button")
        handled += 1
    if handled:
        save(data)
    return handled


def build(data: dict) -> dict:
    books = data["books"]
    finished = [x for x in books if x["done"]]
    return b.section(SECTION, "Reading list", [
        b.metrics("totals", [
            b.metric("On the list", len(books) - len(finished)),
            b.metric("Finished", len(finished)),
            b.metric("Pages read", sum(x["pages"] for x in finished)),
        ]),
        b.tasks("books", [b.task(x["id"], x["title"], done=x["done"]) for x in books],
                title="Books", add_label="Add a book"),
        b.table("details", ["Title", "Pages"], [[x["title"], x["pages"]] for x in books], title="All books"),
    ], icon="books.vertical.fill", summary=f"{len(books) - len(finished)} to read",
       space="home", space_title="Home", space_icon="house.fill")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the section, push nothing")
    args = parser.parse_args()
    data = load()
    if args.dry_run:
        print(json.dumps(build(data), indent=2))
        return 0
    try:
        with HubClient.from_config() as hub:
            taps = handle_taps(hub, data)
            answer = hub.put_section(build(data))
    except HubError as exc:
        print(exc)
        print("reading-list pushed=0 hub=error")
        return 1
    print(f"reading-list pushed=1 books={len(data['books'])} taps={taps} revision={answer['revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Use the repo's Python so `orbital.feeds` imports: `<repo>/.venv/bin/python`. Below, `PY` stands for that path.

**4. Look at what it would send.**

```bash
$PY ~/orbital-feeds/reading_list.py --dry-run > /tmp/reading.json
orbital push /tmp/reading.json --validate        # "reading-list: ok" and any warnings
```

**5. Push it.**

```bash
$PY ~/orbital-feeds/reading_list.py
# reading-list pushed=1 books=2 taps=0 revision=1
```

Open the app: a **Home** tile now holds **Reading list** with three numbers, a checklist and a table.

**6. Tap something.** Tick *The Hobbit* or add a book in the app. The row shows as pending. Run the feed again: it reads the taps, updates `~/reading.json`, answers each tap, and pushes the rebuilt page. The pending mark goes away.

**7. Schedule it.** Add a row to `~/.orbital-agents/jobs.json` (create it from `feeds/jobs.example.json` if you have none):

```json
{
  "id": "reading-list",
  "name": "Reading list",
  "group": "daily",
  "every": 3600,
  "offset": 600,
  "lane": "local",
  "timeout": 60,
  "cwd": "~",
  "argv": ["{python}", "~/orbital-feeds/reading_list.py"],
  "targets": [{"section": "reading-list"}],
  "watch": ["~/reading.json", "{orbital_home}/data/hub/actions/reading-list.json"]
}
```

It now runs every hour at :10, again within about 3 seconds when you edit `~/reading.json`, and again when someone taps a button (the hub writes every tap to `data/hub/actions/reading-list.json`). `{python}` is the Python that runs the scheduler, so `orbital.feeds` is importable.

**8. Check it.**

```bash
orbital jobs                          # schedule, state, next run, last result line
orbital jobs run reading-list --wait  # run it now and print the result
orbital logs scheduler -n 20          # what the scheduler did
```

That is a complete feed. Everything below is reference.

## Sections

A section is one JSON document (`orbital.section.v1`). `PUT /v1/sections/{id}` creates or replaces it; the hub adds `revision` (bumped on every change) and `updated_at`.

```json
{
  "schema": "orbital.section.v1",
  "id": "home-server",
  "title": "Home server",
  "subtitle": "Ubuntu box in the closet",
  "icon": "server.rack",
  "order": 10,
  "layout": "page",
  "summary": "All services up",
  "accent": "#3B82F6",
  "space": "home-lab",
  "space_title": "Home Lab",
  "space_icon": "house.fill",
  "blocks": [
    {"type": "metrics", "id": "vitals", "items": [{"label": "CPU", "value": "12%"}, {"label": "Disk free", "value": "412 GB"}]}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema` | no | `orbital.section.v1` (added when missing) |
| `id` | yes | `^[a-z0-9][a-z0-9-]{0,63}$`. The URL decides the id; a different `id` in the body is overwritten. |
| `title` | yes | 1 to 80 characters |
| `icon` | yes | an SF Symbol name, for example `server.rack`, `globe`, `cloud.sun.fill`, `heart.fill` |
| `order` | yes (SDK: 100) | integer; lower comes first in lists |
| `layout` | yes (SDK: guessed) | `page`, `tabs` or `sidebar` |
| `blocks` / `tabs` / `nodes` | for the layout | arrays (the others may be empty) |
| `subtitle` | no | short line under the title |
| `summary` | no | at most 80 characters, shown on tiles and in lists ("3 sites up") |
| `accent` | no | `#RRGGBB` tint for the page |
| `icon_image` | no | a `/v1/media/…` path or an `https://` URL shown in place of `icon` |
| `space`, `space_title`, `space_icon` | no | group into a home tile; `space` is an id like `home-lab`, the title is 1–40 characters, the icon an SF Symbol |

### Layouts

**page**: `blocks` is the whole page.

**tabs**: `tabs` is a list of `{id, title, icon?, blocks}`. Tab titles are 1–40 characters.

On `tabs` and `sidebar` layouts the section's own `blocks` are drawn **above** the tab or node content, on every tab and every node: a banner, today's status, a row of buttons. Leave `blocks` empty if you do not need that.

```json
{"id": "weather", "title": "Weather", "icon": "cloud.sun.fill", "order": 30, "layout": "tabs",
 "tabs": [
   {"id": "now", "title": "Now", "blocks": [{"type": "metrics", "id": "now", "items": [{"label": "Temp", "value": "14°C"}]}]},
   {"id": "week", "title": "Week", "icon": "calendar", "blocks": []}
 ],
 "blocks": [{"type": "callout", "id": "warning", "tone": "warning", "text": "Storm warning until 18:00."}]}
```

**sidebar**: `nodes` is a list of entries, one per thing you watch (a server, a repo, a site). Each node has `id`, `title`, an integer `order`, `layout` (`page` or `tabs`), and its `blocks` or `tabs`, plus optional `subtitle`, `icon` and `icon_image`.

```json
{"id": "servers", "title": "Servers", "icon": "server.rack", "order": 20, "layout": "sidebar",
 "nodes": [
   {"id": "nas", "title": "NAS", "order": 10, "layout": "page",
    "blocks": [{"type": "metrics", "id": "now", "items": [{"label": "Disk free", "value": "2.1 TB"}]}]},
   {"id": "pi", "title": "Raspberry Pi", "order": 20, "layout": "tabs",
    "tabs": [{"id": "overview", "title": "Overview", "blocks": [{"type": "paragraph", "id": "about", "text": "Runs the DNS filter."}]},
             {"id": "logs", "title": "Logs", "blocks": []}]}
 ]}
```

### Spaces

A space is a home tile that groups sections. There is no separate "create space" call: set `space` on each section that belongs to it. The first section that gives `space_title` and `space_icon` names the tile; without a title the id is used (`home-lab` → "Home Lab"). `GET /v1/catalog` lists the spaces:

```json
{"sections": [ ... ], "spaces": [{"id": "home-lab", "title": "Home Lab", "icon": "house.fill", "sections": ["home-server", "servers"]}]}
```

Good spaces: a project, a team, a household, a server fleet, "Daily" (weather, headlines), "Health".

## Block reference

Every block has a `type` and an `id` (`^[a-z0-9][a-z0-9-]{0,63}$`, unique within its list). Keep ids stable between runs, so partial updates and the app's scroll position keep working. The SDK builder for each type is named after it; see the [SDK reference](#python-sdk-reference).

| Type | For | SDK builder |
|---|---|---|
| [`heading`](#heading) | a title inside the page | `heading()` |
| [`paragraph`](#paragraph) | markdown prose | `text()` (alias `paragraph()`) |
| [`bullets`](#bullets) | a short list | `list()` (alias `bullets()`) |
| [`callout`](#callout) | one notice | `callout()` |
| [`divider`](#divider) | a break | `divider()` |
| [`metrics`](#metrics) | a few big numbers | `metrics()` + `metric()` |
| [`key_value`](#key_value) | label / value rows | `key_value()` |
| [`table`](#table) | columns and rows | `table()` |
| [`timeline`](#timeline) | dated events | `timeline()` + `timeline_item()` |
| [`chart`](#chart) | a line, area, bar or scatter chart | `chart()` + `chart_series()` |
| [`image`](#image) | a picture | `image()` (alias `media()`) |
| [`hero`](#hero) | a big title with a picture and buttons | `hero()` |
| [`buttons`](#buttons) | up to six buttons | `buttons()` (alias `actions()`) + `button()` |
| [`progress`](#progress) | labelled bars | `progress()` + `progress_item()` |
| [`status`](#status) | up / down rows | `status()` + `status_item()` |
| [`links`](#links) | places to go | `links()` + `link()` |
| [`tasks`](#tasks) | a checklist people tick and add to | `tasks()` + `task()` |
| [`ledger`](#ledger) | money for one source, added up across sections | `ledger()` + `ledger_day()` |
| [`product`](#product) | the status card for an app, game, site or service | `product()` |
| [`series`](#series) | daily numbers for the dashboard charts | `series()` + `series_metric()` |
| [`insight_cards`](#insight_cards) | findings with actions you approve | none (plain dict) |
| [`videos`](#videos) | a video channel and where each video was posted | none (plain dict) |

The hub checks `product`, `ledger`, `tasks`, `series`, `insight_cards`, `videos`, `buttons`, `hero`, `progress`, `status` and `links` field by field, because the app adds them up across sections, acts on them, or draws their numbers, colours and links. Other types are checked for `type` and `id` only. A type the app does not draw is stored and skipped on screen; `validate` warns about it (`not_rendered`).

Pass only figures your source returned. Format numbers the way you want them shown (`"412 GB"`, `"12%"`): `value` fields in metrics are display strings.

**`space` inside blocks.** `product`, `ledger`, `series`, `insight_cards` and `videos` blocks take an optional `space` (free text, up to 80 characters, for example `"Example Shop"` or `"Me"`) that groups their numbers across sections in the app's Home, Money and chart views. `company` is accepted as its older name. This is separate from a section's `space` id, which picks its home tile.

### heading

```json
{"type": "heading", "id": "title", "text": "Backups", "level": 2}
```

A bold title inside the page. `level` is 1, 2 (default) or 3.

### paragraph

```json
{"type": "paragraph", "id": "intro", "text": "Last run **finished** at 03:12. See the [runbook](https://example.com/runbook)."}
```

A block of markdown: emphasis, links, inline code. Good for a "checked at" line or a short explanation.

### bullets

```json
{"type": "bullets", "id": "steps", "title": "Next steps", "ordered": true, "items": ["Rotate the old disk", "Check the restore test"]}
```

A short bulleted list, numbered when `ordered` is true. Items are strings (markdown links work).

### callout

```json
{"type": "callout", "id": "late", "tone": "warning", "title": "Backup is late", "text": "The nightly backup has not finished for 2 days."}
```

A tinted notice card. `tone`: `info` (blue), `success` (green), `warning` (amber) or `danger` (red). Use one when a source has no data yet, so the page explains itself instead of looking empty.

### divider

```json
{"type": "divider", "id": "split"}
```

A thin line between groups of blocks.

### metrics

```json
{"type": "metrics", "id": "vitals", "title": "Right now", "items": [
  {"label": "CPU", "value": "12%", "caption": "5 min average"},
  {"label": "Disk free", "value": "412 GB", "delta": "-3 GB", "direction": "down"},
  {"label": "Uptime", "value": "21 days"}
]}
```

A row of large figures. Each tile: `label` and `value` (display strings, up to 60 characters), optional `caption` (a small line under it), `delta` (a short change like `+3` or `-12 ms`) and `direction` (`up`, `down` or `flat`, which colours the delta), and an optional `id`. Keep it to about eight tiles.

### key_value

```json
{"type": "key_value", "id": "facts", "title": "Machine", "items": [{"label": "OS", "value": "Ubuntu 24.04"}, {"label": "Kernel", "value": "6.8.0"}]}
```

Two-column rows: the label on the left, the value on the right.

### table

```json
{"type": "table", "id": "checks", "title": "Latest checks", "columns": ["When", "Site", "Result"],
 "rows": [["09:00", "example.com", "200 OK"], ["09:05", "example.com", "200 OK"]], "limit": 10}
```

A table of strings. `limit` shows that many rows first, then a **Show all** control. Split a huge table across nodes instead of sending thousands of rows.

### timeline

```json
{"type": "timeline", "id": "incidents", "title": "Incidents", "items": [
  {"id": "down-1", "at": "Mon 14:02", "title": "Site down", "detail": "HTTP 502 for 6 minutes", "tone": "danger"},
  {"id": "up-1", "at": "Mon 14:08", "title": "Back up", "tone": "success"}
]}
```

Events down a vertical line. `at` is display text, not parsed, so write it the way you want it read. `tone` (`info`, `success`, `warning`, `danger`) colours the dot.

### chart

```json
{"type": "chart", "id": "latency", "chart": "line", "title": "Response time", "caption": "p95 per hour",
 "x_label": "hour", "y_label": "ms", "y_format": "number",
 "series": [{"name": "example.com", "points": [{"x": "09:00", "y": 210}, {"x": "10:00", "y": 182}, {"x": "11:00", "y": 195}]}]}
```

A chart with one or more series. `chart` is `line` (default), `area`, `bar` or `scatter`. Each series has a `name`, optional `color`, and `points` of `{x, y}`: `x` is a label or a number, `y` a number. `y_format`: `number`, `currency`, `percent` (0.12 shows as 12%) or `compact` (1.2k). Keep a chart under about 400 points; trim old points in your feed. Feeds that need history between runs keep it themselves (the example feeds use `~/.orbital-agents/data/feeds/<name>.json`).

### image

```json
{"type": "image", "id": "rack", "src": "/v1/media/92f1150bfd3a38e582d0042f", "alt": "The server rack", "caption": "Taken on Monday", "aspect": "4:3"}
```

A picture. `src` is a `/v1/media/…` path from an [upload](#media) or an `https://` URL. `aspect`: `16:9`, `4:3`, `1:1` or `3:2`. Always give `alt`.

### hero

```json
{"type": "hero", "id": "banner", "eyebrow": "Home Lab", "title": "Everything is up", "subtitle": "6 services, 2 machines",
 "image": {"src": "https://example.com/rack.jpg", "alt": "Server rack"},
 "actions": [{"id": "open-dash", "label": "Open dashboard", "url": "https://example.com/dash"}]}
```

One dominant card at the top of a page: a small `eyebrow` line, a big `title`, a `subtitle`, an optional picture, and up to three [buttons](#buttons) in `actions`.

### buttons

```json
{"type": "buttons", "id": "controls", "items": [
  {"id": "restart-media", "label": "Restart media server", "style": "primary",
   "action": {"type": "post", "action": "server.restart", "value": {"service": "media"}, "confirm": "Restart the media server?"}},
  {"id": "docs", "label": "Runbook", "style": "secondary", "url": "https://example.com/runbook"},
  {"id": "ask", "label": "Ask an agent", "style": "link", "action": {"type": "agent", "prompt": "Why is the disk filling up on the home server?"}},
  {"id": "weather", "label": "Weather", "style": "link", "action": {"type": "open", "section_id": "weather"}},
  {"id": "fix-tests", "label": "Fix the tests", "style": "secondary",
   "action": {"type": "run_agent", "machine": "build-box", "agent": "claude", "cwd": "~/code/api", "title": "Fix API tests",
              "prompt": "Run the test suite and fix what fails."}}
]}
```

Up to six buttons. Each has an `id` (unique in the block), a `label` (up to 40 characters) and a `style`: `primary`, `secondary`, `link` or `destructive`. What a tap does:

| Button | Tap |
|---|---|
| `"url": "https://…"` | opens the link |
| `"action": {"type": "url", "url": "https://…"}` | the same |
| `"action": {"type": "open", "section_id": "weather", "node_id"?: "…", "tab_id"?: "…"}` | opens another page in the app |
| `"action": {"type": "agent", "prompt": "…"}` | opens the Desk (the Agent tab) with the prompt filled in; `desk` is an alias for `agent` |
| `"action": {"type": "run_agent", "machine"?: "…", "agent"?: "…", "cwd"?: "…", "title"?: "…", "prompt"?: "…"}` | opens the app's **Run agent** sheet filled in: which machine, which agent, the folder, the tracker title and the prompt. The person checks it and starts it; nothing runs until they do. `run` is an alias for `run_agent`. `machine` is a satellite id or machine name (up to 64 characters), `agent` an agent name (40), `cwd` a folder inside that machine's `satellite.workdirs` (1000), `title` up to 120, `prompt` up to 100,000 characters |
| `"action": {"type": "post", "action": "server.restart", "value": {…}, "confirm"?: "…"}` | asks to confirm (when `confirm` is set), then sends an [action](#actions-buttons-that-come-back-to-your-feed) back to your feed; the button shows as pending until you answer |

### progress

```json
{"type": "progress", "id": "disks", "title": "Disks", "items": [
  {"id": "root", "label": "/", "value": 62, "caption": "412 GB free"},
  {"id": "backups", "label": "Backups", "value": 1.6, "max": 2, "display": "1.6 of 2 TB", "tone": "warning"}
]}
```

Labelled bars: disk use, a goal, a backup in progress. Each bar fills `value` out of `max`.

| Field | Meaning |
|---|---|
| `items[]` | up to 30: `label` (required, up to 80 characters), `value` (required, a number), optional `id` (up to 64), `max` (above 0, default 100), `display` (up to 40), `caption` (up to 120), `tone` |
| `value` / `max` | with the default `max` of 100 the bar shows a percent (`62%`); with another `max` it shows `value / max` |
| `display` | replaces the shown number (`"1.6 of 2 TB"`) |
| `tone` | `info`, `success`, `warning` or `danger`: the bar's colour |
| `title` | up to 120 characters |

### status

```json
{"type": "status", "id": "services", "title": "Services", "items": [
  {"id": "web", "label": "Web", "status": "up", "since": "2026-09-26T08:00:00Z"},
  {"id": "db", "label": "Database", "status": "degraded", "detail": "slow queries"},
  {"id": "backups", "label": "Backups", "status": "down", "detail": "last run failed", "url": "https://example.com/backups"}
]}
```

Up / down rows: services on a server, sites behind a monitor, devices on a network. Each row gets a green, red, amber or grey dot. Home also lists every status block that has a row down or degraded, with those rows.

| Field | Meaning |
|---|---|
| `items[]` | up to 100: `label` (required, up to 80 characters), optional `id` (up to 64), `status`, `detail` (up to 200), `since`, `url` |
| `status` | one of these words (any case); missing means unknown: |
| | up: `up`, `ok`, `online`, `healthy`, `running`, `pass`, `passing`, `green` |
| | down: `down`, `error`, `offline`, `failed`, `fail`, `failing`, `red`, `critical` |
| | degraded: `degraded`, `warn`, `warning`, `slow`, `partial`, `yellow` |
| | unknown: `unknown` |
| `since` | ISO 8601 time the row has been in this state, like `2026-09-26T08:00:00Z` (a date alone also works) |
| `url` | where a tap goes; the same schemes as [links](#links) |
| `title` | up to 120 characters |

### links

```json
{"type": "links", "id": "bookmarks", "title": "Links", "items": [
  {"label": "Status page", "url": "https://status.example.com", "detail": "public", "icon": "waveform.path.ecg"},
  {"label": "Server shell", "url": "ssh://admin@home-server.local", "icon": "terminal"},
  {"label": "Ops mailbox", "url": "mailto:ops@example.com", "icon": "envelope"}
]}
```

A short list of places to go: a dashboard, docs, a status page, a machine.

| Field | Meaning |
|---|---|
| `items[]` | up to 30: `url` (required), optional `label` (up to 80 characters; the URL when missing), `detail` (up to 200), `icon` (an SF Symbol name, up to 64) |
| `url` | `https://`, `http://`, `orbital://` (a page in the app), `mailto:`, `tel:`, `ssh://`, `vnc://` or `smb://`; up to 600 characters; each URL once per block |
| `title` | up to 120 characters |

### tasks

```json
{"type": "tasks", "id": "chores", "title": "Chores", "empty_text": "All done.", "add": {"label": "Add a chore"}, "items": [
  {"id": "replace-filter", "title": "Replace the air filter", "due": "2026-11-01", "done": false, "group": "House", "tags": ["monthly"]},
  {"id": "update-router", "title": "Update the router firmware", "done": true}
]}
```

A checklist you can tick in the app, and add to when `add` is set. The app also gathers open items from every `tasks` block on its Home screen, most urgent first.

| Field | Meaning |
|---|---|
| `items[]` | up to 300: `id` (stable, 1–128 characters), `title`, `done`, optional `due` (`YYYY-MM-DD`), `due_label`, `note` (markdown), `group`, `status` (`ready`, `waiting`, `blocked`, `contingent`, `later`, `done`), `tags` (up to 8 short strings), `can_toggle` (false hides the checkbox) |
| `add` | `{label, group?, groups?: [{id, label}], remind_options?: [{days: [7, 1], label}]}`: shows an add field |
| `title`, `subtitle`, `empty_text`, `collapsed`, `limit` | presentation |

A tick sends `task.set_done {done}` with `item_id`; the add field sends `task.add {title, group?, due?, remind_days?, note?}` with `block_id`. The hub marks the row pending at once, so every device redraws; your feed writes the change to its own source and pushes the list again. See [Actions](#actions-buttons-that-come-back-to-your-feed).

### ledger

```json
{"type": "ledger", "id": "sales", "title": "Shop sales", "currency": "USD", "space": "Example Shop", "source": "checkout",
 "connected": true, "as_of": "2026-10-17T09:00:00Z",
 "days": [{"date": "2026-10-16", "amount": 120.5, "count": 4, "product": "Mugs"},
          {"date": "2026-10-17", "amount": 89.0, "count": 3, "product": "Mugs"}],
 "entries": [{"id": "order-1042", "at": "2026-10-17T08:41:00Z", "amount": 29.0, "label": "Order 1042", "product": "Mugs"}]}
```

Money for one source: a shop's sales, donations, subscription income, or spending you want to watch. The app's Money view adds up every ledger block across sections: today, this week, this month, a chart, and breakdowns by space, product, category and source.

| Field | Meaning |
|---|---|
| `currency` | uppercase code, default `USD` |
| `days[]` | up to 3000: `date` (`YYYY-MM-DD`), `amount` (a JSON number), optional `count`, `net`, and tags `product`, `category`, `source`, `space` (`company` is the older name) |
| `entries[]` | up to 200 recent items: `at`, `amount`, optional `id`, `label`, `status`, `net` and the same tags |
| `connected` | false when the source is not set up yet (the view says so instead of showing zero) |
| `space` | groups this money with other ledgers (`company` is the older name); up to 80 characters |
| `title`, `source`, `note`, `as_of`, `timezone`, `metrics` | presentation |

Amounts must be real numbers from your source. A demo ledger should use zeros, so your totals stay true.

### product

```json
{"type": "product", "id": "product", "kind": "site", "headline": "Up, 182 ms", "tone": "success", "space": "Example Shop",
 "checked_at": "2026-10-17T09:00:00Z", "metrics": [{"label": "Visitors today", "value": "1,204"}],
 "releases": [{"id": "web", "platform": "web", "status": "live", "version": "2.3.0"}],
 "links": [{"label": "Open site", "url": "https://example.com"}]}
```

The status card for one thing you ship or run. The Home screen shows one card per product block, with pending releases, the latest reviews, and a warning when `checked_at` is old.

| Field | Meaning |
|---|---|
| `kind` | `app`, `game`, `site` or `service` |
| `headline`, `tone` | one line and its colour (`info`, `success`, `warning`, `danger`) |
| `releases[]` | up to 20: `platform`, `status` (`live`, `pending`, `testing`, `draft`, `rejected`, `removed`), optional `id`, `store`, `version`, `build`, `state`, `since`, `note`, `url` (https) |
| `ratings[]` | up to 6: `value`, optional `max`, `count`, `store`, `label` |
| `reviews[]` | up to 50: `at`, optional `rating` (0–5), `title`, `body`, `author`, `store`, `territory`, `version`, `id` |
| `metrics[]` | metric tiles, as in [metrics](#metrics) |
| `links[]` | up to 6: `label`, `url` (https) |
| `space` | groups this product with others on Home (`company` is the older name) |
| `checked_at` | when you last checked; drives the stale badge |

### series

```json
{"type": "series", "id": "steps-series", "kind": "health", "space": "Me", "product": "Fitness", "title": "Daily steps", "window": 30,
 "metrics": [{"id": "steps", "label": "Steps", "format": "number",
              "points": [{"x": "2026-10-15", "y": 8210}, {"x": "2026-10-16", "y": 10342}]}]}
```

Daily numbers for one thing, drawn as one line chart with a toggle per metric. The app's dashboard charts also add series up across sections by `kind`, `space` and `product`.

| Field | Meaning |
|---|---|
| `kind` | `social`, `site`, `usage`, `players`, `search`, `health`, `money` or `other` |
| `product` | required: what the numbers belong to |
| `space` | optional: who or which group they belong to (use "Me" for personal data); `company` is the older name |
| `metrics[]` | 1 to 60: `id`, `label`, optional `format` (`number`, `usd`, `percent`, `duration`), `platform`, `stat`, `group`, `level`, and `points` (up to 120, `x` is `YYYY-MM-DD`, each day once) |
| `level` | `true` for a reading (a daily average, a peak, a balance): the app shows its latest value and carries it across days instead of adding the days up. `percent` metrics are readings unless they say `"level": false` |
| `window` | days shown, 1 to 90 |
| `title`, `caption`, `site`, `as_of` | presentation |

### insight_cards

```json
{"type": "insight_cards", "id": "findings", "title": "Needs a look", "cards": [
  {"id": "cert-expiry", "kind": "certificate", "severity": "high", "state": "new",
   "title": "TLS certificate expires in 5 days", "summary": "example.com's certificate ends on 2026-10-22.",
   "deadline": "2026-10-22", "evidence": ["Issuer: Example CA", "Not after: 2026-10-22"],
   "actions": [
     {"id": "renew", "kind": "agent", "label": "Renew it",
      "brief": {"prompt": "Renew the TLS certificate for example.com and reload the web server.", "title": "Renew TLS certificate"}},
     {"id": "tell-team", "kind": "email", "label": "Email the team",
      "email": {"to": "ops@example.com", "subject": "Certificate renewal", "body": "Renewing the example.com certificate today."}}
   ]}
]}
```

Findings your feed raised, one card each, with up to four actions you approve in the app. The very top of Home shows every open card (state `new`, `pending`, `in_progress`, `waiting` or `failed`) across all sections, most severe first.

| Field | Meaning |
|---|---|
| `cards[].id` | 1–128 letters, digits or `. _ : -`; it is the action's `item_id` |
| `severity` | `critical`, `high`, `medium`, `low` or `good` |
| `state` | `new`, `pending`, `in_progress`, `waiting`, `done`, `failed`, `snoozed`, `dismissed`, `resolved` |
| `space` | on the block: groups the cards (`company` is the older name) |
| `kind`, `title` | required; `summary`, `product`, `deadline` (`YYYY-MM-DD`), `deadline_label`, `count`, `count_label`, `status_text`, `evidence` (up to 16 lines), `metrics`, `links` (up to 4, https) |
| `actions[]` | up to 4: `id`, `kind` (`email`, `agent`, `note`), `label`, optional `approve_label`, `state`. An `email` action carries `email {to, subject, body, from?, reply_to?}` (a preview; your feed sends it), an `agent` action carries `brief {prompt, title?, …}` |

Taps send `insight.email.approve` or `insight.agent.approve {action_id}`, `insight.snooze {days: 1–30}` or `insight.dismiss {}` with the card id as `item_id`. The hub marks the card pending; your feed does the work and pushes the card's new state.

### videos

```json
{"type": "videos", "id": "channel", "space": "Me", "product": "Woodworking channel", "title": "Videos", "items": [
  {"id": "dovetail-jig", "title": "Making a dovetail jig", "made_at": "2026-10-15T18:00:00Z", "duration": 642,
   "posts": [{"platform": "youtube", "status": "posted", "url": "https://example.com/watch/123", "at": "2026-10-15T19:00:00Z", "views": 312}]}
]}
```

One channel's videos and where each was posted. The app's Videos view shows the videos from every `videos` block, grouped by day, newest first, with a chart of how many came out each day. Items (up to 400) have `id`, `title`, optional `caption`, `file`, `made_at`, `thumbnail` (media path or https), `duration` (seconds), `tags`, and `posts` (up to 20: `platform`, `status` of `posted`, `posting`, `checking`, `failed` or `removed`, `url` (https), `at`, `views`, `post_id`, `account`, `note`). `product` is required; `space` (older name `company`) is optional.

## Partial updates (ops)

Instead of resending a whole section, send a batch of **ops** that name ids. A batch is atomic: if one op fails, nothing changes. The section must exist (create it once with PUT).

```bash
curl -s -X POST "$HUB/v1/sections/servers/ops" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "schema": "orbital.ops.v1",
  "ops": [
    {"op": "upsert_block", "node_id": "nas", "block": {"type": "callout", "id": "smart", "tone": "warning", "text": "One disk reports SMART errors."}},
    {"op": "merge_node", "node_id": "nas", "value": {"subtitle": "1 disk failing"}},
    {"op": "merge_section", "value": {"summary": "1 problem"}}
  ],
  "alerts": [{"schema": "orbital.alert.v1", "id": "nas-smart-2026-10-17", "kind": "event", "section_id": "servers", "node_id": "nas",
              "title": "NAS disk errors", "body": "One disk reports SMART errors.", "interruption_level": "timeSensitive"}]
}'
```

The answer: `{"ok": true, "revision": 7, "section": {...}, "alert_ids": [...]}`. Alerts in the same batch are stored only if the ops succeed.

| Op | Fields | Does |
|---|---|---|
| `upsert_block` | `block`, `node_id`?, `tab_id`? | replace the block with that id, or append it |
| `delete_block` | `block_id`, `node_id`?, `tab_id`? | remove it |
| `reorder_blocks` | `ids`, `node_id`?, `tab_id`? | listed blocks first, in that order, the rest after |
| `merge_section` | `value` | set `title`, `subtitle`, `icon`, `icon_image`, `order`, `accent`, `layout`, `summary`, `space`, `space_title`, `space_icon`; `null` removes a key |
| `replace_section` | `section` | rewrite the whole document (keeps the id) |
| `delete_section` | | delete the section; must be the only op in the batch |
| `upsert_node` | `node` | replace or append a sidebar node |
| `merge_node` | `node_id`, `value` | set `title`, `subtitle`, `icon`, `icon_image`, `order` without resending its tabs |
| `delete_node` | `node_id` | remove a node |
| `reorder_nodes` | `ids` | reorder and renumber nodes (order 10, 20, …) |
| `upsert_tab` | `tab`, `node_id`? | replace or append a tab |
| `delete_tab` | `tab_id`, `node_id`? | remove a tab |
| `reorder_tabs` | `ids`, `node_id`? | reorder tabs |

**Addressing.** A block op without `node_id` and `tab_id` edits the section's own `blocks`, on every layout (on `tabs` and `sidebar` sections those are the blocks drawn above the tab or node content). To reach blocks inside a tab or a node:

| Where the block lives | Block ops need |
|---|---|
| the section's own `blocks` (any layout) | nothing (neither `node_id` nor `tab_id`) |
| a tab of a `tabs` section | `tab_id` |
| a node with layout `page` in a `sidebar` section | `node_id` |
| a tab of a node with layout `tabs` | `node_id` and `tab_id` |

`node_id` on a `tabs` section, `tab_id` on a page node, or `tab_id` without `node_id` on a `sidebar` section is refused with a message saying which to send.

Tab ops on a `page` section fail; switch it first with `merge_section {"layout": "tabs"}`.

**Revisions.** Add `"if_revision": 6` to make a batch conditional. If the hub's revision differs, it answers `409` with `"code": "revision_conflict"` and the current `revision`. Id-addressed ops are safe to retry once at that revision (the SDK's `patch_section` does this for you); `replace_section` and `delete_section` are not, because they would overwrite someone else's change. Read the section again first.

With the SDK:

```python
hub.upsert_block("servers", b.callout("smart", "One disk reports SMART errors.", tone="warning"), node_id="nas")
hub.patch_section("servers", [
    {"op": "merge_node", "node_id": "nas", "value": {"subtitle": "1 disk failing"}},
    {"op": "upsert_block", "node_id": "pi", "tab_id": "logs", "block": b.table("recent", ["When", "Line"], [["09:01", "restarted dnsd"]])},
], alerts=[...])
```

With the CLI, put `{"ops": [...]}` (and optionally `"alerts"` and `"if_revision"`) in a file and run `orbital push ops.json --section servers`.

## Alerts and notification levels

An alert becomes a notification on every signed-in device and a row in the app's Alerts inbox.

```json
{
  "schema": "orbital.alert.v1",
  "id": "home-server-disk-2026-10-17",
  "kind": "event",
  "section_id": "home-server",
  "title": "Disk almost full",
  "body": "/ has 4% free (18 GB left).",
  "interruption_level": "timeSensitive",
  "thread_id": "home-server",
  "launch_url": "orbital://open?section=home-server"
}
```

Send it with `POST /v1/alerts {"alerts": [...]}`, inside an ops batch, with `hub.alert(...)`, or with `orbital push --alert`.

**The id is a natural key.** The same id never notifies twice (unless it was retracted). Build ids from what the alert is about plus the day or the event, so a feed that runs every five minutes notifies once: `website-down-example-com-2026-10-17`, `release-v2-3-0`. `hub.alert()` without `id=` does this for you: the same section, title and body on the same day count as one alert.

| Field | Meaning |
|---|---|
| `id` | required, 1–128 characters of letters, digits, `.`, `_`, `-` |
| `kind` | required: `event` (now) or `reminder` (scheduled) |
| `title`, `body` | required; `subtitle` optional |
| `section_id`, `node_id`, `tab_id` | the page a tap opens |
| `launch_url` | where a tap goes instead: `orbital://open?section=<id>&node=<id>&tab=<id>`, `orbital://alerts`, `orbital://agent`, `orbital://tracker?id=<item>` |
| `interruption_level` | `passive`, `active`, `timeSensitive`, `critical` |
| `thread_id` | groups notifications (the SDK uses the section id) |
| `deliver_at` | ISO time for a reminder; the app schedules it locally |
| `repeats` | `{hour, minute, weekday?}` for a repeating reminder (not with `deliver_at`) |
| `relevance_score` | 0 to 1, for notification summaries |
| `badge`, `sound`, `category_id`, `target_content_id`, `summary_argument`, `summary_argument_count`, `user_info`, `attachments` | passed to the notification |
| `actions` | up to 4 notification buttons |

**Notification buttons.** `actions` adds up to 4 buttons to the notification: `{id, title, style?, foreground?, text_input?}`. `style: "destructive"` shows the button in red. `text_input: {button_title, placeholder?}` makes it a reply field. `foreground` defaults to true, which opens the app.

A button whose id is `act.<type>` writes back to your feed without opening anything. The app posts an `orbital.action.v1` with that `type` to the alert's section (or to `user_info.action_section`), from the Lock Screen, and then marks the alert done. Set `foreground: false` on these. Two optional `user_info` keys fill in the action:

- `action_item` becomes the action's `item_id`.
- `action_value` is a JSON object written as a string, and becomes its `value`. A `task.set_done` button without one sends `{"done": true}`.

The feed sees the action like any tap in the app ([Actions](#actions-buttons-that-come-back-to-your-feed)). Every notification also has the app's own Open, Snooze (1 hour), Reply (opens the Desk with your text) and Dismiss.

```json
{"id": "backup-failed-2026-10-17", "kind": "event", "section_id": "server-health",
 "title": "Nightly backup failed", "body": "It stopped at 41%: the backup drive is full.",
 "actions": [{"id": "act.backup.retry", "title": "Retry now", "foreground": false}],
 "user_info": {"action_item": "nightly-backup", "action_value": "{\"service\": \"backup\"}"}}
```

**Levels.** iOS decides how loud a notification is from its interruption level. The SDK and the CLI take friendlier names:

| SDK / CLI `level` | `interruption_level` | On the phone |
|---|---|---|
| `passive` | `passive` | silent, straight to the notification list |
| `info` (default), `success` | `active` | normal sound and banner |
| `warning`, `error` | `timeSensitive` | breaks through Focus modes that allow time-sensitive alerts |
| `critical` | `critical` | the loudest level; iOS plays it as a critical alert only for apps that hold Apple's critical-alerts entitlement |

Alert only when someone would act on it or be sorry to miss it. A routine refresh is not an alert.

**Clearing.** `POST /v1/alerts/ack {"ids": [...]}` marks alerts done on every device. `POST /v1/alerts/retract {"ids": [...]}` cancels reminders the apps scheduled but should no longer fire (and lets that id notify again later). `GET /v1/alerts?status=pending|all` lists them.

How an alert travels to the phone (live socket, background refresh, local notifications, optional direct Apple push): [NOTIFICATIONS.md](NOTIFICATIONS.md).

## Actions: buttons that come back to your feed

The hub never touches your source. When someone taps a `post` button, ticks a task or approves an insight card, the hub:

1. stores the action in a queue for that section (`data/hub/actions/<section>.json`),
2. marks the button, row or card as pending in the section, so every device redraws at once,
3. waits for the feed that owns the section to answer.

Your feed then reads the queue, does the work, answers each action, and pushes the rebuilt section (which clears the pending marks).

```python
from orbital.feeds import HubClient, blocks as b

with HubClient.from_config() as hub:
    for action in hub.get_actions("home-server"):          # pending taps, oldest first
        if action["type"] == "server.restart":
            ok = restart(action["value"]["service"])       # your code
            hub.post_action_result("home-server", action["id"],
                                   "applied" if ok else "failed",
                                   "Restarted in 4 s" if ok else "systemctl said no")
        else:
            hub.post_action_result("home-server", action["id"], "failed", f"unknown action {action['type']}")
    hub.put_section(build_page())                          # clears the pending marks
```

An action as your feed receives it:

```json
{"schema": "orbital.action.v1", "id": "act-20261017180650-71c1bd", "section_id": "home-server", "status": "pending",
 "type": "server.restart", "block_id": "controls", "item_id": "restart-media", "node_id": null, "tab_id": null,
 "value": {"service": "media"}, "source": "button", "created_at": "2026-10-17T18:06:50Z"}
```

| Field | Meaning |
|---|---|
| `type` | dotted lowercase: your button's `action` (`server.restart`, `garden.water`), or a built-in one |
| `item_id` | the button id, task id or card id that was tapped |
| `block_id`, `node_id`, `tab_id` | where it was tapped |
| `value` | your button's `value` as sent (at most 8 KB), or the built-in type's fields |
| `source` | `button`, `tap`, `notification` or `form` |

Built-in types:

| Type | From | `value` |
|---|---|---|
| `task.set_done` | ticking a [tasks](#tasks) row | `{done: true/false}`, `item_id` = the task id |
| `task.add` | a tasks block's add field | `{title, group?, due?, remind_days?, note?}`, `block_id` = the block |
| `insight.email.approve`, `insight.agent.approve` | an [insight card](#insight_cards) action | `{action_id, digest?}`, `item_id` = the card id |
| `insight.snooze` | an insight card | `{days}` (1–30) |
| `insight.dismiss` | an insight card | `{}` |

**Answer** with `post_action_result(section_id, action_id, "applied" | "failed", message)` or `POST /v1/sections/{id}/actions/results {"results": [{"id", "status", "message"?}]}`. The app shows the message (up to 500 characters). Unanswered actions stay pending; answered ones are kept for `hub.data_retention_days`.

**React quickly.** A feed that only runs every hour answers taps an hour late. Add the queue file to the job's `watch` list in `jobs.json`, and the scheduler runs the feed about three seconds after every tap:

```json
"watch": ["{orbital_home}/data/hub/actions/home-server.json"]
```

**Test a button without the app:**

```bash
curl -s -X POST "$HUB/v1/sections/home-server/actions" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"schema": "orbital.action.v1", "type": "server.restart", "block_id": "controls", "item_id": "restart-media", "value": {"service": "media"}, "source": "button"}'
```

## Media

Upload an image once, then use its URL in `image` blocks, `hero` images and `icon_image`.

```bash
curl -s -X POST "$HUB/v1/media" -H "Authorization: Bearer $TOKEN" -F "file=@rack.png;type=image/png"
# {"id": "92f1150bfd3a38e582d0042f", "url": "/v1/media/92f1150bfd3a38e582d0042f", "content_type": "image/png", "bytes": 12862}
```

```python
url = hub.upload_media("~/Pictures/rack.png")["url"]
b.image("rack", url, "The server rack", aspect="4:3")
```

JPEG, PNG, WebP and GIF, up to `hub.max_media_mb` (8 MB). The id is a hash of the content, so uploading the same file again returns the same URL; a feed can upload on every run without piling up copies. Media no section points at is deleted after `hub.data_retention_days`.

## Pulse: live numbers

Some numbers change every minute: CPU load, people on a site right now, a temperature. Pushing the whole section for each would bump its revision and redraw the page. A **pulse** sends just the number. The hub keeps the newest value per key and pushes it to open apps, which show it as a live tile with a beating dot that greys out when the value goes stale.

```bash
curl -s -X POST "$HUB/v1/pulse" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "schema": "orbital.pulse.v1",
  "items": [{"key": "home-server.cpu", "section_id": "home-server", "label": "CPU", "value": 31, "display": "31%", "ttl_seconds": 120}]
}'
# {"ok": true, "keys": ["home-server.cpu"], "removed": []}
```

```python
hub.pulse([{"key": "home-server.cpu", "section_id": "home-server", "label": "CPU", "value": 31, "display": "31%"}])
hub.pulse([], remove=["home-server.cpu"])
```

| Field | Meaning |
|---|---|
| `key` | required, `^[a-z0-9][a-z0-9._-]{0,127}$`; one key per live number |
| `section_id` | required, an existing section (the tile links to it); `node_id` optional |
| `label` | required, up to 60 characters |
| `value` | required, a finite JSON number |
| `display` | how to show it (`"31%"`), else the number |
| `detail`, `unit`, `source`, `platform` | short extra text |
| `at` | when it was measured (ISO; default now) |
| `ttl_seconds` | how long it counts as live: 10 to 86400, default 180 |
| `order` | integer for sorting tiles |
| `series` | up to 120 `{x, y}` points (`x` a string) for a sparkline |

A pulse never bumps a section revision and never notifies. At most 300 keys; values older than 7 days are dropped. `GET /v1/pulse` returns them all.

## Python SDK reference

```python
from orbital.feeds import HubClient, HubError, blocks, conflict_retry_revision
```

### `HubClient`

`HubClient(base_url, token, *, timeout=30.0, startup_wait=None, transport=None)`. Most feeds use `HubClient.from_config(cfg=None, *, token=None, **kwargs)`: the hub address from `config.toml` (`127.0.0.1:<hub.port>` on the hub, `hub.url` on a satellite) and `ORBITAL_AUTOMATION_TOKEN`. `ORBITAL_HUB_URL` and `ORBITAL_AUTOMATION_TOKEN` in the environment override both. A refused connection at start-up is retried for `startup_wait` seconds (default `ORBITAL_STARTUP_WAIT` or 10), because at login every feed starts before the hub listens. Use it as a context manager or call `close()`.

| Method | Does |
|---|---|
| `health()`, `info()` | `GET /v1/health`, `GET /v1/hub/info` (no token) |
| `contract()` | `GET /v1/contract`: block types, ops, limits, rules |
| `catalog()` | `GET /v1/catalog`: every section and space |
| `section(section_id)` | one section |
| `put_section(section, section_id=None)` | create or replace a whole section (id from the document unless given) |
| `patch_section(section_id, ops, *, alerts=None, if_revision=None)` | apply an ops batch; a 409 on id-addressed ops is retried once |
| `upsert_block(section_id, block, *, node_id=None, tab_id=None)` | one `upsert_block` op |
| `delete_section(section_id)` | delete a section |
| `validate(section_id, ops=None, *, section=None, alerts=None, if_revision=None)` | check a document (`section=`) or an ops batch without writing; returns `warnings` |
| `alert(title, body, section_id=None, level="info", *, id=None, kind="event", subtitle=None, node_id=None, tab_id=None, launch_url=None, thread_id=None, deliver_at=None, **extra)` | raise one alert, returns its id |
| `alerts(alerts)` | post `orbital.alert.v1` objects as they are |
| `retract(ids)`, `ack(ids)` | cancel scheduled alerts; mark alerts done everywhere |
| `upload_media(path, content_type=None)` | upload an image, returns `{id, url, content_type, bytes}` |
| `pulse(items, remove=None)` | post live values |
| `get_actions(section_id, status="pending", limit=100)` | the taps waiting for this section (`status="all"` includes answered ones) |
| `post_action_result(section_id, action_id, status="applied", message=None)` | answer one action |
| `resolve_actions(section_id, results)` | answer several: `[{id, status, message?}]` |
| `satellites()`, `remove_satellite(satellite)` | the machines joined to the hub |
| `run_agent(satellite, prompt, agent=None, *, cwd=None, title=None, track=None)` | start an agent on a machine ([SATELLITES.md](SATELLITES.md)) |
| `agent_jobs(satellite=None)`, `agent_job(satellite, job_id)`, `agent_log(satellite, job_id)`, `cancel_agent(satellite, job_id)` | follow agent jobs |
| `browser(satellite, command, **params)` | a browser command on a machine through the hub ([BROWSER_CONTROLLER.md](BROWSER_CONTROLLER.md)) |
| `tracker_command(command)` | relay an `orbital-track` command to the hub's tracker |
| `request(method, path, *, json_body=None, params=None, files=None, auth=True, timeout=None)` | any other endpoint |

**`HubError`** is raised for every refusal. It carries `status` (0 when the hub could not be reached), `code`, `message`, `hint`, `heal` (a list of repair steps), `revision` and `section_id`; `str(error)` prints them all. `conflict_retry_revision(ops, error)` returns the revision to retry a 409 at, or `None` when the payload must be fixed instead.

### `blocks`

Every builder returns a plain dict. Optional fields left as `None` are dropped. `from orbital.feeds import blocks as b`.

| Function | Signature |
|---|---|
| `section` | `section(id, title, blocks=(), *, icon="square.grid.2x2", order=100, layout=None, subtitle=None, summary=None, accent=None, icon_image=None, tabs=(), nodes=(), space=None, space_title=None, space_icon=None)`; `layout` is guessed: `sidebar` with nodes, `tabs` with tabs, else `page` |
| `tab` | `tab(id, title, blocks=(), *, icon=None)` |
| `node` | `node(id, title, blocks=(), *, order=10, tabs=(), subtitle=None, icon=None, icon_image=None)`; layout `tabs` when tabs are given |
| `heading` | `heading(id, text, level=2)` |
| `text` / `paragraph` | `text(id, text)` → type `paragraph` |
| `list` / `bullets` | `list(id, items, *, title=None, ordered=False)` → type `bullets` |
| `callout` | `callout(id, text, *, tone="info", title=None)` |
| `divider` | `divider(id)` |
| `metric` | `metric(label, value, *, id=None, delta=None, direction=None, caption=None)`; `value` is turned into a string |
| `metrics` | `metrics(id, items, *, title=None)`; items is a list of `metric()` tiles or one tile |
| `key_value` | `key_value(id, items, *, title=None)`; items is a dict or `(label, value)` pairs |
| `table` | `table(id, columns, rows, *, title=None, limit=None)`; cells become strings, `None` becomes `""` |
| `chart_series` | `chart_series(name, points, *, color=None)`; points are `(x, y)` pairs or `{"x", "y"}` dicts |
| `chart` | `chart(id, series, *, kind="line", title=None, caption=None, x_label=None, y_label=None, y_format=None)` |
| `timeline_item` | `timeline_item(at, title, *, id=None, detail=None, tone=None)` |
| `timeline` | `timeline(id, items, *, title=None)` |
| `image` / `media` | `image(id, src, alt, *, caption=None, aspect=None)` |
| `hero` | `hero(id, title, *, eyebrow=None, subtitle=None, image_src=None, image_alt=None, actions=())` |
| `button` | `button(label, *, id=None, style="primary", url=None, open=None, agent=None, post=None, value=None, confirm=None, run_agent=None, machine=None, agent_name=None, cwd=None, title=None)`; `post="server.restart"` makes a post action, `agent="prompt"` prefills the Desk, `open={"section_id": …}` opens a page, `run_agent="prompt"` (or a dict with `prompt`, `machine`, `agent`, `cwd`, `title`) opens Run agent filled in, with `machine=`, `agent_name=` (the agent CLI), `cwd=` and `title=`; the id defaults to a slug of the label |
| `buttons` / `actions` | `buttons(id, items)` |
| `progress_item` | `progress_item(label, value, *, id=None, max=None, display=None, caption=None, tone=None)`; the id defaults to a slug of the label |
| `progress` | `progress(id, items, *, title=None)` |
| `status_item` | `status_item(label, status, *, id=None, detail=None, since=None, url=None)`; `since` is an ISO 8601 time |
| `status` | `status(id, items, *, title=None)` |
| `link` | `link(label, url, *, detail=None, icon=None)` |
| `links` | `links(id, items, *, title=None)` |
| `task` | `task(id, title, *, done=False, due=None, note=None, group=None, status=None, tags=(), can_toggle=True)` |
| `tasks` | `tasks(id, items, *, title=None, add_label=None, empty_text=None)`; `add_label` turns on the add field |
| `ledger_day` | `ledger_day(date, amount, *, count=None, net=None, **tags)`; tags: `product`, `category`, `source`, `space` |
| `ledger` | `ledger(id, days, *, title=None, currency="USD", source=None, space=None, entries=(), connected=True, note=None, as_of=None, company=None)`; `as_of` defaults to now; `company=` is the older name for `space=` |
| `series_metric` | `series_metric(id, label, points, *, format=None, platform=None, stat=None, group=None)`; points are `(YYYY-MM-DD, y)` pairs |
| `series` | `series(id, metrics, *, kind, product, space=None, title=None, caption=None, site=None, window=None, company=None)`; `company=` is the older name for `space=` |
| `product` | `product(id="product", *, kind="app", headline=None, tone=None, space=None, metrics=(), releases=(), ratings=(), links=(), company=None)`; `checked_at` is set to now; `company=` is the older name for `space=` |
| `slug` | `slug(raw, limit=64)`: a valid id from any text (`"My Server #2"` → `"my-server-2"`) |
| `now_iso` | `now_iso()`: the current UTC time as `YYYY-MM-DDTHH:MM:SSZ` |

For `insight_cards` and `videos`, write the dict yourself, as in the [block reference](#block-reference).

## HTTP reference

Base `http://<hub>:7700`. Send `Authorization: Bearer <ORBITAL_AUTOMATION_TOKEN>` (or `X-Orbital-Token: <token>`) and `Content-Type: application/json`. Requests from outside `hub.allow_networks` (loopback and Tailscale by default) get 403 before anything else happens.

| Method | Path | Body → answer |
|---|---|---|
| GET | `/v1/health` | no token: `{ok, service, version, contract, apns}` |
| GET | `/v1/contract` | block types, ops, limits, action types, rules |
| GET | `/v1/catalog` | `{sections: [...], spaces: [...]}` |
| GET | `/v1/sections/{id}` | the section |
| PUT | `/v1/sections/{id}` | a section document → the stored section with `revision` |
| DELETE | `/v1/sections/{id}` | `{ok, id}` |
| POST | `/v1/sections/{id}/ops` | `{schema?, ops, alerts?, if_revision?}` → `{ok, revision, section, alert_ids}` |
| POST | `/v1/sections/{id}/validate` | `{section}` or an ops body → `{ok, would_write, revision, warnings}`; writes nothing |
| GET | `/v1/alerts?status=pending\|all&limit=200` | `{alerts}` |
| POST | `/v1/alerts` | `{alerts: [...]}` → `{ok, alert_ids}` |
| POST | `/v1/alerts/ack` | `{ids}` → `{ok, acked, ids}` |
| POST | `/v1/alerts/retract` | `{ids}` → `{ok, retracted}` |
| POST | `/v1/sections/{id}/actions` | an `orbital.action.v1` tap (the app sends these) → `{ok, action, revision, section}` |
| GET | `/v1/sections/{id}/actions?status=pending\|all&limit=100` | `{actions}` |
| POST | `/v1/sections/{id}/actions/results` | `{results: [{id, status, message?}]}` → `{ok, resolved}` |
| POST | `/v1/media` | multipart `file` → `{id, url, content_type, bytes}` |
| GET | `/v1/media/{id}` | the image |
| POST | `/v1/pulse` | `{schema?, items, remove?}` → `{ok, keys, removed}` |
| GET | `/v1/pulse` | `{items}` |
| GET | `/v1/sync?known=id:rev,…` | what the apps read: catalog, changed sections, alerts, pulse, prefs, jobs, satellites |
| GET, POST | `/v1/prefs` | settings every app shares: `{prefs: {key: value}}`, `null` removes a key |
| WS | `/v1/live` | app sessions and the automation token (`?token=`): pushes `section`, `catalog`, `alerts`, `pulse`, `action`, `action_result`, `jobs`, `satellites` frames |

The full hub API, including satellites, agents, the tracker and the scheduler, is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Validation and errors

Check before you write:

```bash
orbital push home-server.json --validate
```

```python
answer = hub.validate("home-server", section=doc)          # or hub.validate("home-server", ops)
for warning in answer["warnings"]:
    print(warning["hint"])
```

`POST /v1/sections/{id}/validate` accepts `{"section": {...}}` (the section need not exist yet) or an ops body (it must). It answers `{"ok": true, "would_write": …, "revision": …, "warnings": [...]}`, or the same error a real write would get. Warnings are not errors: `not_rendered` means a block type the app does not draw.

Every error has the same shape:

```json
{"ok": false, "error": "node_id is required with tab_id on a sidebar section", "detail": "node_id is required with tab_id on a sidebar section",
 "code": "invalid_value", "status": 400, "hint": "node_id is required with tab_id on a sidebar section", "heal": ["GET /v1/contract for ids, layouts, and limits.", "Fix the field named in error. ..."],
 "contract": "/v1/contract", "section_id": "servers", "section_url": "/v1/sections/servers", "validate_url": "/v1/sections/servers/validate"}
```

| Code | Status | Meaning and fix |
|---|---|---|
| `unauthorized` | 401 | missing or wrong token; read `ORBITAL_AUTOMATION_TOKEN` again |
| `bad_login` | 401 | app sign-in failed (feeds use the token, not the password) |
| `forbidden` | 403 | your address is outside `hub.allow_networks`; connect over Tailscale |
| `not_found` | 404 | unknown section (PUT it once first) or action id |
| `revision_conflict` | 409 | `if_revision` did not match; read the section and retry at `revision` |
| `too_large` | 413 | section over 2 MB or media over `max_media_mb` |
| `bad_schema` | 400 | wrong `schema` name |
| `unknown_op` | 400 | an op name the hub does not know |
| `invalid_value` | 400 | a field is wrong; `error` names it |
| `rejected` | 400 or 500 | anything else; follow `heal` |
| `unavailable` | 502, 504 | a satellite or service did not answer |
| `not_configured` | 503 | a key or setting is missing on the hub |

The hub never guesses a different shape. Fix the field named in `error` and send again.

## Limits

| What | Limit |
|---|---|
| Section document | 2 MB |
| Blocks per list (page, tab or node) | 200 |
| Tabs per section or node | 30 |
| Nodes per section | 500 |
| Chart points | about 400 (guidance) |
| Buttons per block | 6 (hero: 3) |
| Tasks per block | 300 |
| Items per `progress` / `status` / `links` block | 30 / 100 / 30 |
| Agent prompt (`run_agent` buttons, agent runs) | 100,000 characters |
| Alert actions | 4 |
| Alerts kept | the newest 500, and none older than `hub.data_retention_days` |
| Action value | 8 KB |
| Media file | `hub.max_media_mb` (8 MB) |
| Pulse keys | 300, values kept 7 days |
| Ids | `^[a-z0-9][a-z0-9-]{0,63}$` for sections, nodes, tabs, blocks and spaces |
| Section title / summary | 80 characters each |

`GET /v1/contract` returns the live list.

## Schemas

JSON Schema files for every document the hub accepts are in [`schema/`](../schema):

| File | Document |
|---|---|
| `orbital.section.v1.schema.json` | a section (`PUT /v1/sections/{id}`) |
| `orbital.ops.v1.schema.json` | an ops batch (`POST /v1/sections/{id}/ops`) |
| `orbital.alert.v1.schema.json` | an alert |
| `orbital.action.v1.schema.json` | an app tap |
| `orbital.pulse.v1.schema.json` | a pulse batch |

The hub's own checks (`orbital/hub/ops.py`, `orbital/hub/blocks.py`, `orbital/hub/alerts.py`) are the final word; `validate` runs exactly those.

## Rules for healthy feeds

- **Own one section id.** A new subject gets a new section, not a corner of someone else's.
- **Make runs safe to repeat.** Rebuild the whole section, or patch blocks by stable id.
- **Send real observations.** When a source has no data yet, show a callout that says so. Never estimate or fill in.
- **Put the time on the page.** A quiet run still updates a "checked at" line so the page does not look abandoned.
- **Pick the right block:** a chart for a series, a table for rows, metrics for a handful of figures.
- **Alert sparingly**, with ids that dedupe.
- **Print one result line** at the end (`name key=value …`) so the scheduler can tell ok from warn ([FEEDS.md](FEEDS.md)).
- **No secrets** in a section, an alert, a log line or `jobs.json`.
- **Keep your own history.** Store chart history under `~/.orbital-agents/data/feeds/`; the hub keeps only the current page.

The example feeds in [`feeds/examples/`](../feeds/examples) follow every rule; `hello_blocks.py` shows each block type once and answers its own buttons.
