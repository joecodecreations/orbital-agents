# Logging

Every component on a machine writes to one folder: `~/.orbital-agents/logs/`. Each line is one JSON object, so people read them with `orbital logs` and scripts and agents can parse them.

## Where

| File | Written by |
|---|---|
| `hub.log` | the hub (requests refused, sign-ins, satellites joining, alerts it raised, unhandled errors with tracebacks, retention trims) |
| `satellite.log` | the satellite service (jobs started and finished, heartbeats that failed, rebinds) |
| `scheduler.log` | the scheduler (one line per finished feed run, `jobs.json` reloads, requests from the app) |
| `watchdog.log` | the watchdog (checks that failed, restarts, janitor results) |
| `browser-bridge.log` | the browser bridge (each command's action, id, duration, success and caller) |
| `tracker.log` | the tracker app (item events: added, updated, completed…) |
| `app.log` | the Orbital Agents Mac app |
| `wizard.log` | the setup wizard |
| `cli.log`, `feeds.log` | the CLI and feeds that use `get_logger` |
| `<name>.stderr.log` | the stdout and stderr of each background service, written by launchd or systemd (start-up crashes land here) |
| `tracker-voice.stderr.log` | the tracker's optional speech sidecar |

`orbital logs --list` shows every file with its size and age.

## Format

```json
{"ts": "2026-10-17T18:07:41.512Z", "level": "INFO", "component": "satellite", "msg": "job started", "job": "j20261017180741-5a755d", "agent": "echo", "pid": 48211}
```

`ts` (UTC), `level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), `component` and `msg` are always there. Anything else is a field of that event. An exception adds `error` with the traceback.

In Python:

```python
from orbital.core import get_logger

log = get_logger("feeds")
log.info("section pushed", extra={"fields": {"section": "home-server", "blocks": 4}})
```

The Swift apps write the same format with the same rolling rule.

## What is never logged

Secrets, tokens, passwords, API keys, device push tokens (they appear masked), full request bodies, and the parameters, page contents and results of browser commands. The hub turns off per-request access logs because a websocket URL can carry a token. When something must refer to a secret, it uses `orbital.core.secrets.mask()`: the first three characters and the length.

## Rolling and retention

From `[logging]` in `config.toml`:

| Setting | Default | Effect |
|---|---|---|
| `level` | `INFO` | lowest level written; set `DEBUG` while chasing a problem, then back |
| `max_bytes` | 5 MB | a log rolls to `<name>.log.1` at this size (older copies shift to `.2`, `.3`) |
| `backups` | 3 | rolled copies kept per component |
| `retention_days` | 14 | rolled copies older than this are deleted |

launchd and systemd never roll the `*.stderr.log` files they write, so the watchdog's janitor does: once one passes `max_bytes`, its last `max_bytes` are copied to `.1` and the file is truncated in place (the service keeps writing). The janitor runs on every watchdog pass and deletes nothing outside `~/.orbital-agents`. Details: [SELF_HEALING.md](SELF_HEALING.md#the-janitor).

Other outputs have their own caps: feed runs in `data/scheduler/out/` (`scheduler.max_output_bytes`, deleted after `scheduler.output_retention_days`) and agent jobs in `data/satellite/out/` (`satellite.max_output_bytes`, deleted after `satellite.job_retention_days`).

## `orbital logs`

```bash
orbital logs                              # the last 50 lines of every component, merged by time
orbital logs hub                          # one component
orbital logs satellite -f                 # follow it (Ctrl-C stops)
orbital logs --level WARNING --since 1h   # problems in the last hour, everywhere
orbital logs scheduler -n 200             # more lines
orbital logs --since 2026-10-17T09:00     # since a local time
orbital logs hub --stderr                 # the service manager's stdout/stderr file for the hub
orbital logs watchdog --json              # raw JSON lines, for scripts
orbital logs --list                       # every file, size and age
```

| Option | Meaning |
|---|---|
| `component` | `hub`, `satellite`, `scheduler`, `watchdog`, `browser-bridge`, `tracker`, `app`, `wizard`, `cli`, `feeds` |
| `-f`, `--follow` | keep printing new lines (survives a roll) |
| `--level` | this level and above |
| `--since` | `30m`, `2h`, `1d`, `1w`, or a date/time like `2026-10-01T12:00` |
| `-n`, `--lines` | how many lines (default 50) |
| `--stderr` | the `<component>.stderr.log` file instead |
| `--json` | print the JSON lines as they are |
| `--list` | list the files |

Rolled files are read too, so `--since 1d` reaches back past a roll. `orbital status` also shows the last five warnings and errors of the past day.

Other machines' logs stay on those machines. Read them there (`ssh <machine> orbital logs`), or through the tools they feed: `orbital agents log <machine> <job>` for an agent job's output, `orbital jobs` and `GET /v1/jobs/{id}/log` for feeds.

## Reading logs to debug (for people and agents)

1. **Start wide.** `orbital status` (what is up), then `orbital logs --level WARNING --since 1h`.
2. **A service will not start or keeps restarting.** Its Python traceback is usually in the stderr file: `orbital logs <name> --stderr -n 100`. Then `orbital logs watchdog --since 1h` shows the restarts.
3. **Follow one request.** Run `orbital logs <component> -f` in one terminal and repeat the action in another.
4. **Filter by field** with `--json` and `jq`:

   ```bash
   orbital logs satellite --json -n 500 | jq -c 'select(.job == "j20261017180741-5a755d")'
   orbital logs scheduler --json -n 500 | jq -c 'select(.status != "ok") | {ts, job, status, summary}'
   ```

5. **Feeds.** `orbital jobs` gives each feed's last result; `orbital jobs run <id> --wait` prints its issues; the full output is in `~/.orbital-agents/data/scheduler/out/<id>.log`.
6. **Agent jobs.** `orbital agents show <machine> <job>` (status, exit code, the end of the output) and `orbital agents log <machine> <job>` (all of it).
7. **Turn up detail** with `[logging] level = "DEBUG"`, `orbital config --apply`, and `orbital services restart <name>`. Put it back to `INFO` afterwards.

Agents: read logs with `orbital logs … --json` rather than opening the files, never paste a log line that contains a token into a reply (there should be none; report it as a bug if you find one), and quote the `msg` and the fields you relied on when you report a cause.
