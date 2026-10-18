# Feeds and the scheduler

A **feed** is a small program that reads something and pushes it to the hub as a section ([DATA_PIPELINE.md](DATA_PIPELINE.md)). The **scheduler** runs your feeds on a timetable. It runs on the hub as the `io.orbitalagents.scheduler` service (`orbital-scheduler` on Linux) and reads one file: `~/.orbital-agents/jobs.json`.

The hub never runs a feed itself. Its `/v1/jobs` endpoints show the scheduler's state to the app and pass the app's buttons (run now, pause, change the interval) to the scheduler.

## Quick start

```bash
cp feeds/jobs.example.json ~/.orbital-agents/jobs.json   # the setup wizard offers to do this
orbital jobs                                             # every feed: schedule, state, next run, last result
orbital jobs run weather --wait                          # run one now through the scheduler
```

The scheduler notices changes to `jobs.json` within a second; there is nothing to restart.

## jobs.json

```json
{
  "version": 1,
  "max_parallel": 2,
  "groups": [{"id": "home-lab", "title": "Home Lab", "icon": "server.rack"}],
  "lanes": {"web": {"title": "Public websites and APIs", "kind": "api"}},
  "jobs": [
    {
      "id": "website-uptime",
      "name": "Website uptime",
      "group": "home-lab",
      "reads": "An HTTP GET to each URL",
      "targets": [{"section": "website-uptime"}],
      "every": 300,
      "offset": 60,
      "lane": "web",
      "timeout": 120,
      "cwd": "{repo}",
      "argv": ["{python}", "feeds/examples/website_uptime.py", "--url", "https://example.com"],
      "env": {},
      "watch": []
    }
  ]
}
```

### Top level

| Key | Meaning |
|---|---|
| `version` | `1` |
| `about` | free text, ignored |
| `max_parallel` | feeds running at once across all lanes; overrides `scheduler.max_parallel` in `config.toml` |
| `groups` | `[{id, title, icon}]`: how the app groups feeds on its Automations screen; a job names one in `group` |
| `lanes` | `{id: {title, kind}}`: labels for lanes (`kind` is `local` or `api`, for display). A lane a job names but this map leaves out is added with its id as title |
| `jobs` | the feeds |

### Job fields

| Field | Required | Default | Meaning |
|---|---|---|---|
| `id` | yes | | lowercase letters, digits, `-` or `_`, up to 64; unique |
| `argv` | yes | | the command as a list of strings. No shell: no pipes, globs or `$VARS` |
| `every` | yes | | seconds between runs, at least 10 |
| `name` | no | the id | shown in the app and `orbital jobs` |
| `group` | no | none | a group id from `groups` |
| `reads` | no | | one line saying what the feed reads, shown in the app |
| `targets` | no | `[]` | sections the feed writes: `[{"section": "id"}]`, or `[{"section": "id", "node": "node-id"}]`. The app's "refresh this page" button runs the jobs that target the page |
| `offset` | no | `0` | seconds after the top of the hour the grid starts (see [Schedule](#schedule)) |
| `lane` | no | the job's own id | jobs in one lane run one at a time |
| `timeout` | no | `600` | seconds before the run is stopped |
| `cwd` | no | `~` | working folder |
| `env` | no | `{}` | extra environment variables (a `PATH` value may list several folders) |
| `watch` | no | `[]` | files or folders; a change runs the job about 3 seconds later |
| `enabled` | no | `true` | `false` keeps it listed but never runs it |
| `quiet_history` | no | `false` | for jobs that run every minute: repeat results with the same status update the last history row instead of adding one, and are logged only when the status changes |

**Placeholders** in `argv`, `cwd`, `env` and `watch`:

| Placeholder | Becomes |
|---|---|
| `~` or `~/…` at the start | your home folder |
| `{repo}` | this repository |
| `{python}` | the Python running the scheduler (the repo's `.venv`), so `orbital.feeds` imports |
| `{orbital_home}` | `ORBITAL_HOME` (default `~/.orbital-agents`) |

A broken `jobs.json` (bad JSON, a duplicate id, a missing `argv`) is not loaded: `orbital jobs` prints `jobs.json problem: …` and the scheduler logs `jobs.json not loaded`. The scheduler keeps running the last timetable it loaded (none, if it just started) until the file is fixed.

## Schedule

Runs land on a fixed grid: every `every` seconds, shifted by `offset` from the top of the hour. `every 900, offset 120` runs at :02, :17, :32 and :47. Spread feeds that hit the same API with different offsets.

- A job that never ran runs when the scheduler starts.
- After a restart or sleep, a job whose slot passed runs once (reason `catch-up`), not once per missed slot.
- A run that lands within a third of an interval before a slot (a manual run, the run at start) skips that slot, so a feed does not run twice in a row.
- `orbital jobs plan` prints the timetable with the next run of each job.

The app can change a job's interval to one of 60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200 or 86400 seconds, or pause it. Those overrides live in `data/scheduler/settings.json` and win over `jobs.json`; setting the interval back to the file's value removes the override.

## Lanes

Jobs that share something (an account, a browser profile, an API rate limit) belong in one lane, which runs one job at a time. Different lanes run side by side, up to `max_parallel` runs at once. A job without a lane is its own lane. "Refresh everything" runs lane by lane: the first job of every lane together, then the second, and so on.

## Timeouts

A run past its `timeout` gets SIGTERM, then SIGKILL 10 seconds later, sent to its whole process group (child processes included). It is recorded as an error: "Timed out and was stopped".

## What a run gets

Environment, besides your own and the job's `env`:

| Variable | Value |
|---|---|
| `ORBITAL_HOME` | the home folder |
| `ORBITAL_JOB_ID` | the job id |
| `ORBITAL_RUN_REASON` | `schedule`, `startup`, `catch-up`, `manual`, `refresh-all`, `watch` or `restart` |
| `ORBITAL_SCHEDULE_LABEL` | for example `Every 15 min (:02, :17, :32, :47), by the Orbital Agents scheduler` (handy for a "checked at" line) |
| `ORBITAL_SCHEDULE_EVERY` | the interval in seconds |
| `ORBITAL_NEXT_RUN_AT` | when the next run is due (ISO time) |
| `PYTHONPATH` | includes the repository, so `from orbital.feeds import HubClient` works |
| `PATH` | your PATH plus the usual install folders (Homebrew, `~/.local/bin`, npm, nvm, bun, cargo) |
| `PYTHONUNBUFFERED` | `1`, so output arrives line by line |

stdin is empty. The run starts in its own process group.

## Outcome: ok, warn, error

The scheduler reads the exit code and the feed's **result line**: the last line of output with three or more `key=value` pairs, for example

```
website-uptime pushed=1 sites=3 down=0 alerts=0
```

| Status | When |
|---|---|
| `ok` | exit code 0 and nothing in the result line reports a problem |
| `warn` | exit code 0, but a value in the result line is one of `error`, `failed`, `timeout`, `unavailable`, `unreachable`, `offline`, `down`, `signed_out`, `wrong_account`, `not_connected`, `quota`, `rate_limited`, `denied`, `unauthorized`, `forbidden`, `missing`, `stale`, `skip`, or the output has a traceback; also `errors=N`, `N failed` or `problems=N` above zero |
| `error` | a non-zero exit code, a crash, a timeout, or the command could not start |

So a feed that reads two sources and one is down prints `radar=unavailable` and exits 0: the page still updates, and the app says what broke. A line like `radar: unavailable: HTTP 503 from the API` anywhere in the output gives that issue a detail. The one-line summary in the app is the result line (or, on an error, the exception or last line).

## Run now, cancel, refresh

| From | How |
|---|---|
| CLI | `orbital jobs run <id> [<id>…] [--wait]`: queue through the running scheduler; `--wait` prints each result and exits 0 (ok), 1 (warn), 2 (error) or 3 (not finished / scheduler down) |
| CLI | `orbital jobs run <id> --here`: run once in this terminal; only when the scheduler is stopped, so a feed never runs twice at once |
| CLI | `orbital jobs cancel <id>`: drop it from the queue or stop the run |
| CLI | `orbital jobs refresh [--group G]`: run everything (or one group) now, lane by lane |
| App | the run, refresh-page, refresh-group, pause and interval buttons on the Automations screen and on each page |
| HTTP | `POST /v1/jobs/{id}/run`, `POST /v1/jobs/{id}/cancel`, `POST /v1/jobs/run-all {group?}` or `{section_id, node_id?}`, `POST /v1/jobs/{id}/settings {every_s?, paused?}`, `GET /v1/jobs`, `GET /v1/jobs/{id}/log` |
| Module | `python -m orbital.scheduler --plan`, `--run <id> [--wait]`, `--self-test` |

A run requested while the job is already running runs once more right after it.

## Outputs and state

| Path (under `~/.orbital-agents/data/scheduler/`) | What |
|---|---|
| `out/<job>.log` | every run's stdout and stderr (stderr lines start `[stderr]`), each run under a `===== <time> <job> (<reason>) =====` header. At most `scheduler.max_output_bytes` per run; the file rolls to `.log.1` past that size |
| `runs/<job>.log` | the last run's final 400 lines, with a header; this is what the app's log view and `GET /v1/jobs/{id}/log` show |
| `state.json` | every job's last result, history (24 runs), and the snapshot the hub serves |
| `settings.json` | interval and pause overrides from the app |
| `requests/` | run and cancel requests from the hub and the CLI, picked up within a second |

`~/.orbital-agents/run/scheduler.json` is the scheduler's heartbeat (updated every few seconds); the watchdog restarts the scheduler when it is older than 3 minutes. Old outputs are deleted after `scheduler.output_retention_days`. The scheduler's own log is `orbital logs scheduler`: one line per finished run (`<job> ok|warn|error`), reloads, and requests.

Feeds keep their own history (for charts across runs) under `~/.orbital-agents/data/feeds/`, never in the scheduler's folders.

## The example feeds

All in `feeds/examples/`. Each one also runs by hand; `--dry-run` prints the section instead of pushing it, and `--home` picks another `ORBITAL_HOME`.

| Job id | Script | Reads | What the page shows | Space |
|---|---|---|---|---|
| `machine-health` | `machine_health.py [--disk DIR] [--disk-alert-pct N]` | CPU load, memory, disks and uptime (macOS, Linux) | metrics, intraday charts kept across runs, a daily `series` (kind `health`), a disk-full callout and alert | Home Lab |
| `website-uptime` | `website_uptime.py --url URL [--url URL…]` | an HTTP GET per URL | a status table, one latency line per site, an incident timeline, alerts when a site goes down and comes back | Home Lab |
| `github` | `github_repo.py --repo owner/name [--repo …]` | the public GitHub API (no key; `GITHUB_TOKEN` raises the rate limit) | a sidebar with one node per repo and tabs, stars over time, a release alert, a callout when the API refuses | Projects |
| `weather` | `weather.py --place NAME --lat N --lon N [--units imperial]` | Open-Meteo (free, no key) | line and bar charts, a 7-day table, a rain alert | Daily |
| `headlines` | `rss_headlines.py --feed URL [--feed …] [--limit N]` | RSS 2.0 and Atom | a tab per feed, a timeline, markdown link lists, what is new since the last run | Daily |
| `hello` | `hello_blocks.py [--no-upload]` | nothing | every block type once, an uploaded image, a block updated in place, and answering its own buttons and to-dos | Demo |

`feeds/examples/feedkit.py` holds what they share: arguments, a small state file per feed, HTTP fetching, pushing, and the result line. Copy it next to your own feeds if you like the pattern (`gather()` reads, `build()` makes the section with no I/O, `main()` pushes and prints the result line).

Edit the example rows in your `jobs.json` to point at your own sites, repos and place. The `hello` row watches `{orbital_home}/data/hub/actions/hello.json`, so it answers taps within seconds.

## Writing a feed

1. Read your source and build a section (the SDK's `blocks` builders, or plain JSON).
2. Push it with `HubClient.from_config().put_section(...)`, `orbital push`, or HTTP.
3. Answer any pending actions for your section.
4. Print one result line and exit 0, even when one part of the source failed (say so in the line).

The step-by-step version, with a complete feed, is in [DATA_PIPELINE.md](DATA_PIPELINE.md#build-your-first-feed-in-10-minutes). Feeds can be any language: the scheduler only needs `argv`, and the hub only needs HTTP.

## Troubleshooting

| Symptom | Look at |
|---|---|
| `orbital jobs` says `Scheduler NOT running` | `orbital services status scheduler`, `orbital logs scheduler --stderr`, then `orbital services restart scheduler` |
| A job never runs | `enabled: false`, paused in the app (`orbital jobs` shows `paused`), or `jobs.json problem: …` |
| A job is `error` | `orbital jobs run <id> --wait` prints the issues; the full output is in `data/scheduler/out/<id>.log` |
| `ModuleNotFoundError: orbital` | use `{python}` in `argv`, not `python3` |
| A command is "not found" under the scheduler but works in your terminal | give the full path in `argv`, or add its folder to the job's `env.PATH` |
| A tap takes an hour to apply | add `{orbital_home}/data/hub/actions/<section>.json` to the job's `watch` |
