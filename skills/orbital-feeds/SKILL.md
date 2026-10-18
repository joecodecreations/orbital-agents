---
name: orbital-feeds
description: Put data into the Orbital Agents app - push or update a section (a page of blocks), raise an alert, post a live number, upload an image, or answer the buttons people tap - with the orbital.feeds Python SDK, `orbital push`, or plain HTTP to the hub. Load it when the user asks to show something in the app or on their phone, build or fix a feed, schedule a feed in jobs.json, send a notification, or when a push to the hub returns an error.
---

# Orbital Agents feeds: push data to the app

A **section** is one page in the app, owned by one feed, with a stable id (`^[a-z0-9][a-z0-9-]{0,63}$`). It is made of **blocks**. Sections that share a `space` are grouped into a home tile. Full reference: `docs/DATA_PIPELINE.md` in the repo; live contract: `GET /v1/contract`.

All pushes use the automation token `ORBITAL_AUTOMATION_TOKEN` from `~/.orbital-agents/secrets.env`. Never print it, never put it in a section, a log line, `jobs.json` or a reply.

## Python SDK (preferred)

Run with the repo's Python (`<repo>/.venv/bin/python`) or `{python}` in `jobs.json`, so `orbital.feeds` imports.

```python
from orbital.feeds import HubClient, HubError, blocks as b

page = b.section("home-server", "Home server", [
    b.metrics("vitals", [b.metric("CPU", "12%"), b.metric("Disk free", "412 GB", delta="-3 GB", direction="down")]),
    b.chart("load", b.chart_series("Load", [("09:00", 0.4), ("10:00", 0.9)]), title="Load"),
    b.table("checks", ["When", "Result"], [["09:00", "200 OK"]], title="Latest checks"),
    b.buttons("controls", [b.button("Restart media server", post="server.restart", value={"service": "media"},
                                    confirm="Restart it?", id="restart-media")]),
    b.text("checked", "Checked at 10:00."),
], icon="server.rack", summary="All services up", space="home-lab", space_title="Home Lab", space_icon="house.fill")

with HubClient.from_config() as hub:              # hub URL from config.toml, token from secrets.env
    answer = hub.validate("home-server", section=page)   # check first; answer["warnings"]
    hub.put_section(page)                          # create or replace the whole page
    hub.upsert_block("home-server", b.callout("late", "Backup is late", tone="warning"))   # change one block
    hub.alert("Backup failed", "Exit code 2.", section_id="home-server", level="warning")
```

`HubError` carries `status`, `code`, `message`, `hint` and `heal` (repair steps). Read them, fix the payload, try again. Do not invent a different shape.

## Block cheat-sheet

| Builder (`blocks as b`) | Type | For |
|---|---|---|
| `b.heading(id, text, level=2)` | heading | a title |
| `b.text(id, markdown)` | paragraph | prose, a "checked at" line |
| `b.list(id, items, title=, ordered=)` | bullets | a short list |
| `b.callout(id, text, tone="info", title=)` | callout | info, success, warning, danger notice; use when a source has no data yet |
| `b.divider(id)` | divider | a break |
| `b.metrics(id, [b.metric(label, value, delta=, direction=, caption=)])` | metrics | a few big numbers (values are display strings) |
| `b.key_value(id, {"OS": "Ubuntu"})` | key_value | label / value rows |
| `b.table(id, columns, rows, title=, limit=)` | table | rows |
| `b.timeline(id, [b.timeline_item(at, title, detail=, tone=)])` | timeline | dated events (`at` is display text) |
| `b.chart(id, [b.chart_series(name, [(x, y)])], kind="line", y_format=)` | chart | line, area, bar, scatter; under ~400 points |
| `b.image(id, src, alt, caption=, aspect=)` | image | `/v1/media/…` or https |
| `b.hero(id, title, eyebrow=, subtitle=, image_src=, actions=[...])` | hero | a big header card |
| `b.buttons(id, [b.button(label, url= / open= / agent= / post=, value=, confirm=)])` | buttons | up to 6 |
| `b.button(label, run_agent="prompt", machine=, agent_name=, cwd=, title=)` | buttons | opens the app's Run agent sheet filled in; the person starts it |
| `b.progress(id, [b.progress_item(label, value, max=100, display=, caption=, tone=)])` | progress | labelled bars (up to 30); default max 100 shows a percent |
| `b.status(id, [b.status_item(label, "up", detail=, since=ISO, url=)])` | status | up / down rows (up to 100); words: up/ok/online/healthy/running/pass, down/error/offline/failed/critical, degraded/warn/slow/partial, unknown |
| `b.links(id, [b.link(label, url, detail=, icon=)])` | links | places to go (up to 30): https, http, orbital://, mailto:, tel:, ssh://, vnc://, smb://; each url once |
| `b.tasks(id, [b.task(id, title, done=, due=)], add_label=)` | tasks | a checklist people tick and add to |
| `b.ledger(id, [b.ledger_day(date, amount, product=)], currency="USD", space=)` | ledger | money per day (real figures only) |
| `b.series(id, [b.series_metric(id, label, [(date, y)])], kind=, product=, space=)` | series | daily numbers for the dashboards |
| `b.product(kind="site", headline=, tone=, metrics=, links=)` | product | the status card of an app, game, site or service |
| `b.section(id, title, blocks, icon=, layout=, tabs=[b.tab(...)], nodes=[b.node(...)], space=)` | section | `page`, `tabs` or `sidebar` layout |
| `b.slug(text)` | | a valid id from any text |

`insight_cards` and `videos` have no builder: write the dict (see the docs). Inside `product`, `ledger`, `series`, `insight_cards` and `videos`, `space` (older name `company`) groups numbers across sections; a section's own `space` is the home-tile id.

On `tabs` and `sidebar` sections, the section's own `blocks` are drawn above the tab or node content; block ops without `node_id`/`tab_id` edit them on any layout.

## Partial updates

```python
hub.patch_section("servers", [
    {"op": "upsert_block", "node_id": "nas", "block": b.callout("smart", "Disk errors", tone="warning")},
    {"op": "merge_node", "node_id": "nas", "value": {"subtitle": "1 disk failing"}},
    {"op": "merge_section", "value": {"summary": "1 problem"}},
], alerts=[...])
```

Ops: `upsert_block`, `delete_block`, `reorder_blocks`, `merge_section`, `replace_section`, `delete_section`, `upsert_node`, `merge_node`, `delete_node`, `reorder_nodes`, `upsert_tab`, `delete_tab`, `reorder_tabs`. Addressing: no `node_id`/`tab_id` edits the section's own blocks (any layout); a tab of a `tabs` section needs `tab_id`; a sidebar node needs `node_id` (and `tab_id` when that node has tabs). The section must exist (PUT it once). A 409 `revision_conflict` on id-addressed ops is retried once by the SDK; never retry `replace_section` or `delete_section` blindly.

## Buttons and taps (actions)

A `post` button or a ticked task queues an action for the section's owner. Answer it, then push the page again:

```python
for action in hub.get_actions("home-server"):          # type, item_id, block_id, value, id
    if action["type"] == "server.restart":
        hub.post_action_result("home-server", action["id"], "applied", "Restarted")
    else:
        hub.post_action_result("home-server", action["id"], "failed", "unknown action")
```

Built-in types: `task.set_done {done}`, `task.add {title, …}`, `insight.email.approve`, `insight.agent.approve`, `insight.snooze`, `insight.dismiss`. To react within seconds, add `{orbital_home}/data/hub/actions/<section>.json` to the job's `watch` list.

## Alerts, media, live numbers

```python
hub.alert(title, body, section_id="home-server", level="warning")   # passive | info | warning | error | critical
hub.alert(title, body, section_id="x", id="site-down-example-com-2026-10-17")   # the same id never notifies twice
url = hub.upload_media("/abs/photo.png")["url"]                      # jpeg, png, webp, gif; up to 8 MB
hub.pulse([{"key": "home-server.cpu", "section_id": "home-server", "label": "CPU", "value": 31, "display": "31%"}])
```

Alert only when someone would act on it. Without `id=`, the same section, title and body on the same day count once.

## CLI

```bash
orbital push page.json                       # PUT (the document's "id" names it)
orbital push page.json --validate            # check, write nothing
orbital push ops.json --section servers      # {"ops": [...], "alerts"?: [...]}
cat page.json | orbital push -
orbital push --alert "Backup failed" --body "Exit code 2" --section home-server --level warning
```

## HTTP

```bash
TOKEN=$(grep '^ORBITAL_AUTOMATION_TOKEN=' ~/.orbital-agents/secrets.env | cut -d= -f2-)   # do not echo it
HUB=http://127.0.0.1:7700                     # a satellite: the hub.url in its config.toml
curl -s -X PUT  "$HUB/v1/sections/home-server"          -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data @page.json
curl -s -X POST "$HUB/v1/sections/home-server/ops"      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"ops":[...]}'
curl -s -X POST "$HUB/v1/sections/home-server/validate" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"section":{...}}'
curl -s -X POST "$HUB/v1/alerts"                        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"alerts":[{"schema":"orbital.alert.v1","id":"...","kind":"event","title":"...","body":"..."}]}'
curl -s -X POST "$HUB/v1/media"                         -H "Authorization: Bearer $TOKEN" -F "file=@photo.png;type=image/png"
curl -s "$HUB/v1/catalog"                               -H "Authorization: Bearer $TOKEN"
```

## Schedule a feed

Add a row to `~/.orbital-agents/jobs.json` (the scheduler reloads it within a second):

```json
{"id": "home-server", "name": "Home server", "group": "home-lab", "every": 300, "offset": 30, "lane": "local",
 "timeout": 120, "cwd": "{repo}", "argv": ["{python}", "feeds/home_server.py"], "targets": [{"section": "home-server"}],
 "watch": ["{orbital_home}/data/hub/actions/home-server.json"]}
```

End every run with one result line, `name key=value key=value …`; a failed part says so as a value (`api=unavailable`). Then check:

```bash
orbital jobs                          # schedule and last result of every feed
orbital jobs run home-server --wait   # run now, print the result and issues
orbital logs scheduler -n 50
```

## Rules

- Write only your own section id; a new subject gets a new id.
- Send real observations only. No estimates, no placeholders that look like data; use a callout when there is nothing yet.
- Keep block, node and tab ids stable across runs.
- Put the as-of time on the page.
- Validate before the first push of a new shape. On a 400, follow `heal`.
- No secrets, tokens or private paths in sections or alerts.
