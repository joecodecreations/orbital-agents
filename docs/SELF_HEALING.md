# Self-healing

Orbital Agents is meant to run for months without you looking after it. Three layers keep it up and keep its folder from growing without limit:

1. **The service manager** (launchd on macOS, systemd on Linux, Task Scheduler plus the `winrun` supervisor on Windows) starts every service at login and restarts it when it exits.
2. **The watchdog** checks that each service actually answers, restarts the ones that do not, and raises an alert on your phone when it does.
3. **The janitor** (run by the watchdog) trims logs, outputs and old data.

## Services

Installed by the setup wizard, or by hand with `orbital services install`.

| Service | macOS LaunchAgent | Linux user unit | Runs | Where |
|---|---|---|---|---|
| hub | `io.orbitalagents.hub` | `orbital-hub.service` | `python -m orbital.hub` | hub only |
| satellite | `io.orbitalagents.satellite` | `orbital-satellite.service` | `python -m orbital.satellite` | every machine (`satellite.enabled`) |
| scheduler | `io.orbitalagents.scheduler` | `orbital-scheduler.service` | `python -m orbital.scheduler` | hub only (`scheduler.enabled`) |
| browser-bridge | `io.orbitalagents.browser-bridge` | `orbital-browser-bridge.service` | `python -m orbital.browser.server` | `browser.enabled` |
| watchdog | `io.orbitalagents.watchdog` | `orbital-watchdog.service` + `.timer` | `python -m orbital.scheduler.watchdog` (one pass) | every machine (`watchdog.enabled`) |
| tracker | `io.orbitalagents.tracker` | none | the installed tracker app | macOS, `tracker.enabled`, app installed |

How each kind is kept alive:

| Kind | macOS | Linux |
|---|---|---|
| daemons (hub, satellite, scheduler, browser-bridge) | `RunAtLoad`, `KeepAlive = true`, `ThrottleInterval = 10` | `Restart=always`, `RestartSec=10` |
| watchdog | `StartInterval = watchdog.interval_seconds` (one pass each time) | a oneshot service plus a timer (`OnBootSec=60s`, `OnUnitActiveSec=<interval>`) |
| tracker app | `KeepAlive = {SuccessfulExit: false}`: restarted after a crash, not after you quit it | not available |

Every service gets `ORBITAL_HOME`, a PATH that includes the repo's `.venv/bin`, `~/.local/bin`, Homebrew and the folders where agent CLIs were found at install time, and writes its stdout and stderr to `~/.orbital-agents/logs/<name>.stderr.log`. Linux user units stop at logout unless you run `loginctl enable-linger $USER` once.

```bash
orbital services status              # wanted, loaded, state, pid and health of each service
orbital services status --no-health  # skip the HTTP health checks
orbital services restart [name …]    # all, or some
orbital services stop scheduler      # stop one (start brings it back)
orbital services install             # (re)write and load what this machine should run, remove what it should not
orbital services install --dry-run   # print every file and command, change nothing
orbital services uninstall           # remove them all
```

`install` is safe to repeat. It follows `config.toml`: turn `browser.enabled` off and the next install removes the bridge service. `status` marks a service `(outdated)` when its file on disk differs from what install would write now (for example after you moved the repo).

## The watchdog

Every `watchdog.interval_seconds` (60) it checks each service that belongs on this machine:

| Service | Check | Applies when |
|---|---|---|
| hub | `GET http://127.0.0.1:<hub.port>/v1/health` | role is hub |
| satellite | `GET http://127.0.0.1:<satellite.port>/v1/health` | `satellite.enabled` |
| browser-bridge | `GET http://127.0.0.1:<browser.bridge_port>/health` | `browser.enabled` |
| tracker | `GET http://127.0.0.1:<tracker.port>/v1/health` | macOS, `tracker.enabled` |
| scheduler | `run/scheduler.json` heartbeat younger than 3 minutes, from a process that still exists | role is hub, `scheduler.enabled` |

An HTTP check gets two tries a second apart.

**Restarts.** A service that fails `watchdog.failures_before_restart` (2) passes in a row is restarted through the service manager:

| Platform | Command |
|---|---|
| macOS | `launchctl kickstart -k gui/<uid>/io.orbitalagents.<name>` |
| macOS, tracker | `open -g <tracker app>` |
| Linux | `systemctl --user restart orbital-<name>` |

**Limits.** At most one restart per service every 2 minutes (or two watchdog intervals, whichever is longer), and at most six per service per hour. A service that still will not come up after that is left alone and reported. Services that are not installed are noted ("not installed") and never restarted.

**Alerts.** Each restart raises a hub alert on your phone:

- **Self-heal: restarted `<name>`** — `<name>` on `<machine>` stopped answering (`<detail>`) and the watchdog restarted it.
- **Self-heal: could not restart `<name>`** — the restart command failed, with its exit code and output.
- **Self-heal: `<name>` keeps failing** — six restarts in an hour did not help; sent at most once a day per service. Check `orbital logs <name>`.

A satellite's watchdog sends its alerts through the hub with its hub token, so they say which machine they came from. When the hub itself is down, the restart still happens; the alert is skipped.

Run a pass by hand:

```bash
.venv/bin/python -m orbital.scheduler.watchdog --dry-run   # report only: restart and delete nothing
.venv/bin/python -m orbital.scheduler.watchdog --json      # one real pass, print the result
.venv/bin/python -m orbital.scheduler.watchdog --loop      # keep running in the foreground
```

`watchdog.enabled = false` turns it off on that machine (for example while you debug a service by hand). Turn it back on afterwards.

## The janitor

After every pass the watchdog runs the janitor. It only ever deletes inside `~/.orbital-agents` (every path is checked first, and symlinks are not followed).

| What | Rule | Setting |
|---|---|---|
| `logs/*.stderr.log`, `*.stdout.log` | past `max_bytes`, the tail is copied to `.1` (older copies shift up to `backups`) and the file is truncated in place | `logging.max_bytes`, `logging.backups` |
| a JSON log over 4 × `max_bytes` | the same (its writer should have rolled it) | `logging.max_bytes` |
| rolled logs (`*.log.1`, `*.stderr.log.2`, …) | deleted when older than the retention | `logging.retention_days` (14) |
| agent job records and output | deleted when older than the retention; the newest 20 always stay | `satellite.job_retention_days` (14) |
| feed outputs (`data/scheduler/out`, `runs`) | deleted when older than the retention | `scheduler.output_retention_days` (14) |
| scheduler request files nobody picked up | deleted after a day | |
| tracker media archive (`data/tracker/media/`) | files older than the retention; empty month folders removed. `items.json` is never touched | `tracker.media_retention_days` (60) |
| hub data (hub only) | alerts, answered actions, unused media and idle app sessions older than the retention. Media a section still uses and actions nobody answered are kept | `hub.data_retention_days` (90) |

The hub also runs its own data trim once a day. The tracker app trims its history (`tracker.history_retention_days`) at launch and once a day.

## Other things that heal themselves

- **Feeds at login** start before the hub listens; the SDK retries a refused connection for 10 seconds.
- **The scheduler** closes out a run that was in flight when it stopped, and runs that job again. A job whose slot passed while the machine slept runs once.
- **The satellite service** marks jobs that were running when it restarted as failed and stops their leftover processes. When Tailscale is not up yet it listens on loopback and rebinds to the Tailscale address once it appears.
- **The hub's own satellite** joins the hub again if the hub forgets it.
- **`orbital-track`** never blocks an agent: when the tracker is down it queues commands in `data/tracker/inbox/`, and on a satellite it queues commands for the hub in `hub-outbox/` until the hub answers.
- **Satellites** that miss three heartbeats show offline and come back with the next one.
- **Apple push** stops for 15 minutes after Apple refuses the key, and drops device tokens Apple says are gone.

## See what happened

```bash
orbital status                                   # each service up or down, restarts in the last 24 h, recent problems
orbital logs watchdog --since 1d                 # every failed check, restart and janitor result
orbital logs <name> --stderr -n 100              # why a service crashed
cat ~/.orbital-agents/run/watchdog.json          # the last pass: per service status, failures, restarts, janitor counts
```

`orbital doctor` checks the whole install and prints a fix for each problem: `config.toml`, whether `config.resolved.json` is current, `secrets.env` (file mode and the tokens this role needs), Tailscale, ports, every service (installed, up to date, running, healthy), the hub (healthy on the hub, reachable from a satellite), whether this satellite is known to the hub, satellites online, the browser extension, the size of the log folder, agent CLIs on the PATH the services use, and the agent skills. `--json` prints the same as JSON.

`orbital doctor --fix` applies the safe repairs, then checks again: it rewrites `config.resolved.json`, sets `secrets.env` to mode 0600, (re)installs service files that are missing or outdated, and restarts services that are loaded but unhealthy. `orbital doctor --fix --dry-run` prints those repairs without making them.
