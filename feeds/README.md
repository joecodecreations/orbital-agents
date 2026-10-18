# Feeds

A feed is a small script that reads something (this machine, a website, a public API) and pushes it to the hub as a **section**: one page in the Orbital Agents app, made of blocks. The scheduler runs feeds on a timetable; the app shows what they pushed, raises their alerts, and sends button taps back to them.

## Try the examples

```bash
cp feeds/jobs.example.json ~/.orbital-agents/jobs.json   # the setup wizard offers to do this
orbital jobs                                             # the timetable, next runs, last results
orbital jobs run weather --wait                          # run one now through the scheduler
```

Every example also runs on its own. `--dry-run` prints the section instead of pushing it:

```bash
.venv/bin/python feeds/examples/machine_health.py --dry-run
```

| Feed | Reads | Shows off | Space |
| --- | --- | --- | --- |
| `machine_health.py` | CPU load, memory, disks, uptime (macOS, Linux) | metrics, intraday charts kept across runs, a daily `series` (kind `health`), disk-full callout and alert | Home Lab |
| `website_uptime.py --url …` | an HTTP GET per URL | status table, one latency line per site, incident timeline, alerts on down and back up | Home Lab |
| `github_repo.py --repo owner/name` | the public GitHub API (no key; `GITHUB_TOKEN` optional) | a sidebar section, one node per repo with tabs, stars over time, a release alert, a stale-data callout when the API refuses | Projects |
| `weather.py --place … --lat … --lon …` | Open-Meteo (free, no key) | line and bar charts, a 7-day table, a rain alert | Daily |
| `rss_headlines.py --feed URL` | RSS 2.0 and Atom | tabs per feed, a timeline, markdown link lists, new-since-last-run | Daily |
| `hello_blocks.py` | nothing | every block type once, `upload_media`, `upsert_block`, and answering its own buttons and to-dos | Demo |

Spaces are the app's home tiles: sections that set the same `space` (with `space_title` and `space_icon`) are grouped together.

## Write your own

```python
from orbital.feeds import HubClient, blocks as b

section = b.section("garden", "Garden", [
    b.metrics("now", [b.metric("Soil moisture", "41%"), b.metric("Last watered", "2 h ago")]),
    b.chart("moisture", b.chart_series("Moisture", [("09:00", 0.44), ("12:00", 0.41)]), y_format="percent"),
    b.buttons("do", [b.button("Water now", post="garden.water")]),
], icon="leaf.fill", space="home", space_title="Home")

with HubClient.from_config() as hub:          # hub address from config.toml, ORBITAL_AUTOMATION_TOKEN from secrets.env
    hub.put_section(section)
    for tap in hub.get_actions("garden"):     # "Water now" presses waiting for you
        ...                                   # do it, then answer
        hub.post_action_result("garden", tap["id"], "applied", "Watering for 5 minutes")
print("garden pushed moisture=41 pump=ok alerts=0")
```

Then add a row to `~/.orbital-agents/jobs.json`:

```json
{"id": "garden", "name": "Garden", "group": "home-lab", "every": 600, "offset": 30, "lane": "local",
 "timeout": 120, "cwd": "{repo}", "argv": ["{python}", "feeds/garden.py"], "targets": [{"section": "garden"}]}
```

The scheduler reloads `jobs.json` by itself. Rules that keep feeds healthy:

- Make the run safe to repeat: rebuild the whole section, or patch blocks by id.
- Print one **result line** at the end, `name key=value …`. A part that failed says so as a value (`source=error`, `api=unavailable`, `signed_out`, `timeout`): the scheduler then marks the run `warn` and the app says what broke. A crash or a non-zero exit is an `error`.
- Keep your own history under `~/.orbital-agents/data/feeds/` (see `feedkit.py`); the scheduler only keeps output logs.
- Pass only figures your source returned; never estimate. Never put secrets in a section, a log line or `jobs.json`.

`jobs.json` fields: `id`, `name`, `group`, `reads`, `targets` (sections the job writes), `every` (seconds), `offset`, `lane` (jobs sharing an account or rate limit share a lane and run one at a time), `timeout`, `cwd`, `argv` (no shell), `env`, `watch` (files that trigger a run when they change), `enabled`. In paths and arguments, `~/` is your home folder, `{repo}` this repository, `{python}` the scheduler's Python and `{orbital_home}` `~/.orbital-agents`. Each run gets `ORBITAL_HOME`, `ORBITAL_JOB_ID`, `ORBITAL_RUN_REASON`, `ORBITAL_SCHEDULE_LABEL` and `ORBITAL_NEXT_RUN_AT`, and its output lands in `~/.orbital-agents/data/scheduler/out/<id>.log`.
