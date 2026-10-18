---
name: orbital-ops
description: Operate and debug an Orbital Agents install - check health with `orbital status` and `orbital doctor`, read the central logs with `orbital logs`, restart or reinstall the background services, run the live end-to-end checks with `orbital test --live`, and find where config, secrets, data and logs live. Load it when something in Orbital Agents is down, slow or erroring (the app shows nothing, a feed fails, a machine is offline, the tracker or browser is not connected), after changing config.toml, or before telling the user a setup works.
---

# Orbital Agents operations

Everything for one machine lives in `~/.orbital-agents` (or `$ORBITAL_HOME`). The repo holds only code. Docs: `docs/TROUBLESHOOTING.md`, `docs/LOGGING.md`, `docs/SELF_HEALING.md`, `docs/CONFIG.md`.

## Look first

```bash
orbital status                 # role, machine, hub reachable, each local service up/down, satellites, last problems
orbital doctor                 # every check with a fix for each problem
orbital doctor --json          # the same, for parsing
orbital services status        # wanted / loaded / state / pid / health per service
orbital satellites             # every machine joined to the hub, online or offline
orbital jobs                   # every feed's schedule and last result (hub)
```

`orbital doctor` checks config.toml, config.resolved.json, secrets (file mode, required tokens), Tailscale, ports, every service, the hub link, satellites, the browser extension, log size, agent CLIs on the services' PATH, and the agent skills.

## Fix

```bash
orbital doctor --fix --dry-run      # show the safe fixes
orbital doctor --fix                # apply them: rewrite config.resolved.json, chmod secrets.env, reinstall missing or
                                    # outdated service files, restart unhealthy services; then check again
orbital services restart [name …]   # hub, satellite, scheduler, browser-bridge, watchdog, tracker (default: all)
orbital services install            # (re)write and load what this machine should run; safe to repeat
orbital services install --dry-run  # print the files and commands only
orbital services stop|start <name>
```

Ask the user before installing or uninstalling services on a machine they did not mention, and before `orbital services uninstall`.

## Logs

```bash
orbital logs --level WARNING --since 1h     # problems everywhere, last hour
orbital logs hub -n 100                     # one component: hub, satellite, scheduler, watchdog, browser-bridge, tracker, app, wizard, cli, feeds
orbital logs satellite -f                   # follow
orbital logs hub --stderr -n 100            # the service's raw stdout/stderr (start-up crashes, tracebacks)
orbital logs scheduler --json -n 500 | jq -c 'select(.level != "INFO")'
orbital logs --list                         # files, sizes, ages
```

Each line is JSON: `ts`, `level`, `component`, `msg`, plus fields. When you report a cause, quote the `msg` and the fields you used. Logs never hold secrets; if you ever see a token in one, tell the user it is a bug and do not repeat it.

Other places with output:

| What | Where |
|---|---|
| a feed's runs | `orbital jobs run <id> --wait` (issues), `~/.orbital-agents/data/scheduler/out/<id>.log` |
| an agent job | `orbital agents show <machine> <job>`, `orbital agents log <machine> <job>` |
| the watchdog's last pass | `~/.orbital-agents/run/watchdog.json` |
| the scheduler's heartbeat | `~/.orbital-agents/run/scheduler.json` |

## Verify end to end

```bash
orbital test --live            # PASS/FAIL table against the running install; exit 0 = all passed
orbital test --live --json
orbital test                   # the repo's unit and integration tests
```

The live checks cover: services up and logs rolling; hub health, sign-in, automation token, a section round trip and a silent alert; the tracker round trip through the hub; the scheduler running the demo job; every online satellite (ping and an `echo` agent job); every browser node (bridge, extension, open and close a tab). They clean up what they create and skip parts that are turned off here. Run them before you tell the user something works.

## Where things live

| Path (under `~/.orbital-agents/`) | What |
|---|---|
| `config.toml` | settings (edit, then `orbital config --apply` and `orbital services restart`) |
| `secrets.env` | passwords, tokens, API keys (mode 0600). **Never print, copy, commit or paste its values.** Refer to keys by name |
| `config.resolved.json` | merged settings for the Swift apps (generated) |
| `jobs.json` | the feed timetable |
| `data/hub/`, `data/scheduler/`, `data/satellite/`, `data/tracker/`, `data/browser/`, `data/feeds/` | each component's data |
| `logs/` | `<component>.log` and `<component>.stderr.log` |
| `run/` | pid, port and heartbeat files |

```bash
orbital config                 # merged settings (no secrets)
orbital config hub.port        # one value
orbital config --paths         # every path above
orbital --home /path config    # another ORBITAL_HOME
```

Services: macOS LaunchAgents `~/Library/LaunchAgents/io.orbitalagents.<name>.plist`; Linux user units `~/.config/systemd/user/orbital-<name>.service`.

## Common fixes

| Symptom | Do |
|---|---|
| `Hub … unreachable` on the hub | `orbital services restart hub`, then `orbital logs hub --stderr -n 50` |
| `Hub … unreachable` on a satellite | `tailscale status`; `curl http://<hub>:7700/v1/hub/info`; check `hub.url` with `orbital config hub.url` |
| a port is in use | `lsof -nP -iTCP:7700 -sTCP:LISTEN`; change the port in `config.toml` (on every machine for `hub.port`), `orbital config --apply`, `orbital services install` |
| an agent CLI "not found" under the services | `orbital doctor` (agent PATH check); `orbital satellite status`; give the full path in `[agents.<name>].command` |
| a machine is offline | on it: `orbital satellite status`, `orbital logs satellite`; rejoin with `orbital satellite join <hub_url> <token>` |
| the extension is not connected | Chrome open? extension enabled and not paused? same port as `browser.bridge_port`? `orbital browser status` |
| a feed shows `error` | `orbital jobs run <id> --wait`; read `data/scheduler/out/<id>.log` |
| after editing `config.toml` | `orbital config --apply && orbital services restart` |

## Setup and skills

```bash
./setup                                # the wizard (hub or satellite); safe to run again
./setup --answers answers.toml --yes   # unattended, see config/answers.example.toml
orbital setup --dry-run --yes          # print the plan, change nothing
orbital skills status                  # are these skills and the rule block installed for each agent?
orbital skills install                 # (re)install them
```
