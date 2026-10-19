# Testing

Two kinds of tests:

- **Repo tests** check the code: Python unit and integration tests, the tracker's Swift tests, the app's unit tests (and unsigned builds), and lint. They never touch your install: each test gets a temporary `ORBITAL_HOME` and free ports (never 7700–7704).
- **Live checks** (`orbital test --live`) check the running install on this machine: services up, the hub answering, a section and an alert round trip, the tracker, the scheduler, every satellite and every browser.

## Quick reference

| Command | Runs |
|---|---|
| `make test` | `test-python`, `test-swift`, `test-app` and `lint` below |
| `make test-python` | `pytest -m "not live" -q` |
| `make test-swift` | `cd apps/tracker && swift build && swift test` (macOS with a Swift toolchain; skipped elsewhere) |
| `make test-app` | `apps/orbital-app/scripts/test.sh`: generates the Xcode project, builds the app unsigned and runs its unit tests on an iPhone simulator. With Xcode but no iPhone simulator it only builds (`scripts/build.sh`); without full Xcode it is skipped with a message |
| `make lint` | `compileall` on all Python, `bash -n setup` and the shell scripts, `node --check` on every extension script |
| `make test-live` | `orbital test --live` |
| `orbital test` | the Python tests (live checks excluded), with a PASS/FAIL table |
| `orbital test --live` | only the live checks |
| `orbital test --all` | both |
| `.venv/bin/pytest tests/hub -q` | one folder |
| `.venv/bin/pytest tests/test_public_safety.py` | the public-safety scan |

`orbital test` options: `--json` (machine-readable results; exit 0 means everything passed), `-k EXPR` (pytest's keyword filter), `-x` (stop at the first failure), `-v` (show pytest's own output), and extra pytest arguments after `--`.

## Python tests

```
tests/
  hub/          the hub API: sections, ops, validate, alerts, actions, media, pulse, auth, allow lists,
                satellites registry and relays, tracker view and relay, desk, retention, the feeds CLI
  satellite/    the satellite API, jobs (queue, timeouts, output caps), tracker reporting (orbital-track, or the
                hub's tracker relay where there is no CLI), joining a real hub, the CLI
  scheduler/    timetable math, runs, lanes, timeouts, outcome classification, the watchdog, the janitor
  feeds/        every example feed in --dry-run and against a real in-process hub
  browser/      the bridge, its auth and origin checks, nodes, the client and CLI, the extension against a fake Chrome
  services/     the launchd plist, systemd unit and Task Scheduler task specs (fake launchctl/systemctl/schtasks)
  tracker/      the orbital-track CLI
  wizard/       the setup wizard
  live/         live checks (skipped unless ORBITAL_LIVE=1, which `orbital test --live` sets)
  test_public_safety.py
```

Markers (`pyproject.toml`): `live` (operational checks) and `slow` (starts real servers). CI runs `-m "not live and not slow"`; `make test-python` runs everything except `live`.

Tests that need a hub or a satellite start one in-process or as a subprocess on a free port, with a temporary `ORBITAL_HOME`, and stop it afterwards. Nothing writes to `~/.orbital-agents`, `~/Library/LaunchAgents` or `~/.config/systemd`.

The scheduler also has a quick built-in self-test: `.venv/bin/python -m orbital.scheduler --self-test`.

## Swift tests (the tracker)

```bash
cd apps/tracker
swift build
swift test
```

`Tests/OrbitalTrackerCoreTests/` covers commands, config parsing, hotkeys, the JSON-line log, models, the hub relay and media archiver, retention and settings. The CLI end to end is tested from Python in `tests/tracker/test_cli.py`.

## App build tests

```bash
apps/orbital-app/scripts/build.sh            # unsigned iOS Simulator build (generates the Xcode project first)
apps/orbital-app/scripts/build.sh --mac      # unsigned Mac Catalyst build
apps/orbital-app/scripts/build.sh --all      # both
apps/orbital-app/scripts/build.sh --release  # Release instead of Debug
```

No Apple account, Team ID or device is needed. Build output goes to `apps/orbital-app/.derivedData` (git-ignored).

The app's unit tests live in `apps/orbital-app/OrbitalAgentsTests/` (decoding hub payloads from `Fixtures/`, section models, app behaviour). Run them unsigned on a simulator:

```bash
apps/orbital-app/scripts/test.sh                         # the first available iPhone simulator
apps/orbital-app/scripts/test.sh --device "iPhone 16"    # a simulator by name or UDID
apps/orbital-app/scripts/test.sh --mac                   # on this Mac, as Mac Catalyst
```

`test.sh` picks an iPhone simulator that is shut down, so one you are using is left alone; it boots it for the run and shuts it down afterwards. `make test` and CI run it. The tests decode every sample section in `apps/orbital-app/Fixtures/preview/` (which between them use every block type), the satellites, agent jobs, jobs, alerts, desk, tracker and hub payloads, and check dates, `orbital://` links, hub addresses and error wording.

## Live checks

```bash
orbital test --live
```

prints one row per check, `PASS`, `FAIL` (with the reason and usually a fix) or `skip` (with why), then a count. It uses this machine's real config and secrets and the ports in its `config.toml`, cleans up what it creates (a test section, a silent alert, a tracker item, a browser tab), and skips parts that are turned off here. The setup wizard runs it at the end (answer `verify.live_test`).

| Check | What a PASS proves |
|---|---|
| services: every enabled service is installed, loaded, running and answering | launchd / systemd has each service this machine should run, and its health endpoint answers |
| logs: a log file exists for each running service | logging is wired up |
| logs: rotation settings are sane and no file is over max_bytes × 1.1 | logs roll and the janitor keeps up |
| hub: /v1/health answers; /v1/hub/info identifies the hub | the hub is up and discoverable |
| hub: sign in with the configured username and password; a wrong password is refused | the app can sign in, and only with the right password |
| hub: the automation token is accepted | feeds and the CLI can write |
| hub: push a test section, read it back, delete it | the data pipeline works end to end |
| hub: raise a (silent) test alert and retract it | alerts are stored and can be cancelled |
| tracker: orbital-track start/update/done shows up in the hub's /v1/tracker/items | the CLI reaches the tracker app and the hub sees its items (the phone's view). On a satellite without a tracker (Linux, or `tracker.enabled = false`) it checks that the hub's tracker relay takes this machine's commands instead |
| scheduler: responsive, runs the demo job and records the result | the scheduler heartbeats, accepts run requests, runs a feed and records its outcome |
| satellite: this machine's satellite service answers /v1/health | agents can be started here |
| satellite: the hub accepts this machine's heartbeat (satellites only) | this machine is joined |
| satellites: every online satellite answers a ping through the hub | the hub can reach every machine |
| satellites: an echo agent job runs end to end on every online satellite | starting, running and reading an agent job works on each machine (with the harmless `echo` agent) |
| satellites: heartbeats are fresh (offline ones listed) | no machine has gone quiet |
| browser: each node's bridge answers, its extension is connected, and it opens, reads and closes a tab | remote and local browser control works with a real Chrome (on `https://example.com`) |


Use `orbital test --live --json` in scripts. Fix failures with the hint in the row, `orbital doctor`, and [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main` and every pull request:

| Job | Runs |
|---|---|
| Python tests | Ubuntu and macOS, Python 3.11 and 3.13: `pip install -e ".[dev]"`, `pytest -m "not live and not slow"`, `compileall`, `bash -n setup`, and a non-interactive wizard dry run that must write nothing |
| Public-safety scan | `pytest tests/test_public_safety.py` |
| Tracker | `swift build` and `swift test` on macOS |
| App | macOS: `apps/orbital-app/scripts/build.sh --mac` (unsigned Mac Catalyst build), then `apps/orbital-app/scripts/test.sh` (unsigned simulator build and the unit tests on an iPhone simulator) |
| Browser extension lint | `node --check` on every script and a JSON check of `manifest.json` |

## The public-safety test

`tests/test_public_safety.py` scans every tracked file (and untracked files that are not ignored) for private keys, API key patterns (OpenAI, Anthropic, xAI, GitHub, Slack, Stripe, Google, AWS), home-folder paths (`/Users/<name>/` other than placeholders like `/Users/you/`), Tailscale host names (`*.tail<hex>.ts.net`) and Apple team id assignments. Add your own words that must never ship (your name, company, host names) to `.public-safety.local` at the repo root, one per line; that file is git-ignored. Run it before every commit:

```bash
.venv/bin/pytest tests/test_public_safety.py -q
```
