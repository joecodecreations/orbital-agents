# Configuration

Every machine has one folder for its settings, secrets, data and logs: `~/.orbital-agents` (set `ORBITAL_HOME` to use another one). The repository only holds code.

| File | What it holds | Who writes it |
|---|---|---|
| `config.toml` | every setting that differs from the defaults | the setup wizard, then you |
| `secrets.env` | passwords, tokens and API keys, mode 0600 | the setup wizard, `orbital satellite join` |
| `config.resolved.json` | the merged settings plus file paths, no secrets | `orbital config --apply` and the wizard |
| `jobs.json` | the scheduler's timetable of feeds ([FEEDS.md](FEEDS.md)) | you, the wizard, the app's schedule buttons (as overrides) |
| `apns.p8` | optional Apple push key ([NOTIFICATIONS.md](NOTIFICATIONS.md)) | you |

The defaults live in `orbital/core/config.py` (`DEFAULTS`). `config/orbital.example.toml` shows every key with a comment. Your `config.toml` only needs the keys you change; anything missing uses the default.

## After you edit config.toml

```bash
orbital config --apply          # rewrite config.resolved.json (the Swift apps read this file)
orbital services restart        # restart the background services so they read the new values
```

The tracker app reads `config.resolved.json` at start, so restart it too (`orbital services restart tracker`) after changing a `[tracker]` key.

## `orbital config`

| Command | Prints |
|---|---|
| `orbital config` | the merged settings as JSON (no secrets) |
| `orbital config hub.port` | one value, by dotted key |
| `orbital config --paths` | where config, secrets, data, logs and the run folder live |
| `orbital config --apply` | rewrites `config.resolved.json` and prints its path |
| `orbital --home /path/to/home config` | the same for another `ORBITAL_HOME` |

## `config.resolved.json`

The Swift apps (the tracker and the app's build tooling) do not read TOML. They read `$ORBITAL_HOME/config.resolved.json`, which holds every merged setting plus:

- `machine_name`: the resolved machine name (the short host name when `system.machine_name` is empty)
- `hub_url`: where this machine reaches the hub (`http://127.0.0.1:<hub.port>` on the hub, `hub.url` on a satellite)
- `paths`: `home`, `data`, `hub_data`, `tracker_data`, `scheduler_data`, `satellite_data`, `browser_data`, `feeds_data`, `logs`, `run`, `secrets`, `jobs`

It never contains a secret value, only the path to `secrets.env`. Do not edit it by hand; it is overwritten.

## Settings

### `[system]`

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `name` | `"Orbital Agents"` | name shown in the apps | you want your own name on the dashboard |
| `role` | `"hub"` | `hub` (the primary machine) or `satellite` (every other machine) | the wizard and `orbital satellite join` set it; change it by hand only if you move the hub |
| `machine_name` | `""` | this machine's name in the tracker, satellites list and alerts; empty means the short host name | the host name is unclear (`Mac`, `localhost`) or two machines share one |

### `[hub]`

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `host` | `"0.0.0.0"` | address the hub listens on | rarely; the allow list below already limits who is answered |
| `port` | `7700` | hub port | another program uses 7700 (change it on every machine's `hub.url` too) |
| `url` | `""` | satellites only: the hub's address, for example `http://my-mac.your-tailnet.ts.net:7700` | the hub moved; the wizard and `orbital satellite join` write it |
| `allow_networks` | loopback + Tailscale (`127.0.0.0/8`, `::1/128`, `100.64.0.0/10`, `fd7a:115c:a1e0::/48`) | client addresses the hub answers; everything else gets 403 | you really need a LAN range (for example `"192.168.1.0/24"`). Prefer Tailscale |
| `data_retention_days` | `90` | alerts, answered actions, unused media and idle app sessions older than this are deleted | you want a longer or shorter history |
| `max_media_mb` | `8` | largest image `POST /v1/media` accepts | you upload larger screenshots |

### `[tracker]` (macOS)

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `enabled` | `true` | run the Agent Satellite Tracker on this Mac | a Mac that should only relay `orbital-track` to the hub |
| `port` | `7701` | the tracker's loopback API | the port is taken |
| `stt_port` | `7702` | the optional speech sidecar, loopback | the port is taken |
| `voice` | `false` | on-device speech input (first use downloads a ~1.5 GB model) | you want to speak items into the tracker |
| `titles` | `"auto"` | `auto`, `grok` or `none`: who shortens spoken items into titles | you have no `grok` CLI (`none`) |
| `grok_model` | `""` | the model the tracker asks the `grok` CLI to use for titles (empty = the CLI's default) | you want a specific model |
| `hotkeys` | `true` | global shortcuts: modifiers + M (mic), T (show/hide), N (type an item) | another app needs those keys |
| `hotkey_modifiers` | `"ctrl+option"` | modifiers for the shortcuts, `+`-joined from `ctrl`, `option`/`alt`, `shift`, `cmd` | another app already uses ctrl+option |
| `history_retention_days` | `365` | finished items older than this leave the history (0 keeps everything) | you want a shorter history |
| `media_retention_days` | `60` | archived screenshots and video frame sheets older than this are deleted (0 keeps them) | disk space |

### `[browser]` (Orbital Agents Browser Controller)

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `enabled` | `true` | run the browser bridge on this machine | this machine has no Chrome |
| `bridge_port` | `7703` | bridge port; the extension's options must use the same number | the port is taken |
| `bind` | `"loopback"` | `loopback`, or `tailscale` to let your other machines (and the hub relay) drive this Chrome | you want remote browser control of this machine |

Optional keys the bridge reads, with their defaults: `max_events = 20000` (captured network events kept in memory), `events_log_mb = 50` (size of `captures/events.jsonl` before it rolls; 0 = no file), `capture_secrets = false` (keep credential values in captures; leave it off), `max_stage_mb = 500` (largest staged upload), `staged_max_mb = 2000` (staged folder cap), `staged_max_age_hours = 24`, `command_timeout = 60` (seconds), `read_roots = ["~/Downloads"]` (folders `/files` may read besides the staged folder). Details: [BROWSER_CONTROLLER.md](BROWSER_CONTROLLER.md).

### `[satellite]` (runs agents on this machine)

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `enabled` | `true` | run the satellite service here (the hub runs one too) | you never want agents started on this machine |
| `port` | `7704` | satellite service port | the port is taken |
| `bind` | `"tailscale"` | `tailscale` (this machine's Tailscale IP + 127.0.0.1) or `loopback` | a machine that should only run agents you start locally |
| `max_concurrent_jobs` | `2` | agent jobs running at once; the rest queue | a big machine (raise) or a small one (lower) |
| `job_timeout_minutes` | `60` | a job running longer is stopped and marked failed | long agent runs |
| `workdirs` | `["~"]` | agents may only start inside these folders | you want to restrict agents to, say, `["~/code"]` |
| `default_agent` | `"claude"` | agent used when a request names none | your main agent is another CLI |
| `heartbeat_seconds` | `60` | how often a satellite tells the hub it is alive; three missed beats show it offline | rarely |
| `max_output_bytes` | `2000000` | output kept per job (the first 64 KB and the tail survive) | very chatty agents |
| `job_retention_days` | `14` | finished job records and output older than this are deleted (the newest 20 always stay) | you want more history |
| `allow_networks` | loopback + Tailscale | client addresses the satellite answers | rarely |

### `[agents.<name>]`

How each agent CLI is started. `{prompt}` and `{cwd}` are replaced in each argument. No shell is involved, so quoting and pipes do not apply. A command without `{prompt}` gets the prompt on stdin. An agent is offered to the hub only when its executable is found; services look in PATH plus the usual install folders (Homebrew, `/usr/local/bin`, `~/.local/bin`, `~/.npm-global/bin`, nvm, bun, cargo).

| Name | Default command |
|---|---|
| `claude` | `["claude", "-p", "{prompt}"]` |
| `codex` | `["codex", "exec", "{prompt}"]` |
| `grok` | `["grok", "-p", "{prompt}"]` |
| `gemini` | `["gemini", "-p", "{prompt}"]` |
| `echo` | `["/bin/echo", "{prompt}"]`, a harmless test agent used by `orbital test --live` |

Add your own, or turn one off:

```toml
[agents.myagent]
command = ["/usr/local/bin/my-agent", "--task", "{prompt}", "--dir", "{cwd}"]

[agents.gemini]
enabled = false
```

Names are letters, digits, `.`, `_` and `-`. More in [SATELLITES.md](SATELLITES.md).

### `[scheduler]` (hub only)

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `enabled` | `true` | run the scheduler for data feeds | you push data only from your own scripts |
| `max_parallel` | `2` | feeds running at once across lanes (`jobs.json` may override it with its own `max_parallel`) | many slow feeds |
| `output_retention_days` | `14` | feed run outputs older than this are deleted | you want more history |
| `max_output_bytes` | `1000000` | output kept per feed run in `out/<job>.log` | chatty feeds |

### `[watchdog]`

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `enabled` | `true` | health checks, restarts and trimming on this machine | debugging a service by hand (turn it back on after) |
| `interval_seconds` | `60` | time between passes | rarely |
| `failures_before_restart` | `2` | failed checks in a row before a restart | a service that is slow to answer |

See [SELF_HEALING.md](SELF_HEALING.md).

### `[logging]`

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING` or `ERROR` | `DEBUG` while chasing a problem |
| `max_bytes` | `5000000` | a log file rolls at this size | disk space |
| `backups` | `3` | rolled files kept per component | more history |
| `retention_days` | `14` | rolled files older than this are deleted | more history |

See [LOGGING.md](LOGGING.md).

### `[desk]` (the app's Agent tab)

| Key | Default | Meaning | Change it when |
|---|---|---|---|
| `provider` | `"xai"` | provider for fast voice replies; needs `XAI_API_KEY` in `secrets.env` (the tab explains when it is missing) | |
| `model` | `"grok-4-fast-non-reasoning"` | model for those replies | |
| `worker_agent` | `"claude"` | agent the desk starts for real work | your main agent is another CLI |
| `projects` | `[]` | folders the desk may start work in: `[[desk.projects]]` tables with `name`, `path`, optional `about` and `aliases` | you want to say "work on the website" and have it pick the folder |
| `reminders_list` | `""` | macOS Reminders list for "remind me…" (empty = the default list) | |

```toml
[[desk.projects]]
name = "website"
path = "~/code/website"
about = "Personal website, static HTML"
```

### `[speech]`

| Key | Default | Meaning |
|---|---|---|
| `fish_model` | `""` | optional Fish Audio voice model id for spoken replies (needs `FISH_API_KEY`) |

### `[apple]` (iPhone and Mac app builds; the wizard fills these)

| Key | Default | Meaning |
|---|---|---|
| `team_id` | `""` | your Apple Developer Team ID |
| `bundle_prefix` | `"com.example"` | reverse-domain prefix: the app is `<prefix>.orbitalagents`, the tracker `<prefix>.orbitalagents.tracker` |
| `app_name` | `"Orbital Agents"` | the app's display name |
| `ios_device` | `""` | iPhone to install to; empty means the first connected one |

### `[notifications]`

| Key | Default | Meaning |
|---|---|---|
| `apns` | `false` | direct Apple push so alerts reach a closed app ([NOTIFICATIONS.md](NOTIFICATIONS.md)) |
| `apns_environment` | `"development"` | `development` for Xcode and `devicectl` installs, `production` for TestFlight and the App Store |

## secrets.env

`~/.orbital-agents/secrets.env` is a plain `KEY=value` file, mode 0600. Keep it out of git, backups you share, and chat. A value in the environment wins over the file (handy for CI). The hub re-reads the file when it changes, so a new app password needs no restart.

| Key | Where | Used for |
|---|---|---|
| `ORBITAL_HUB_USERNAME` | hub | app sign-in |
| `ORBITAL_HUB_PASSWORD` | hub | app sign-in |
| `ORBITAL_AUTOMATION_TOKEN` | hub (copy it to any machine that pushes data) | feeds, scripts and the CLI calling the hub (`Authorization: Bearer`) |
| `ORBITAL_JOIN_TOKEN` | hub | satellites present it once to join |
| `ORBITAL_HUB_TOKEN` | satellite | issued by the hub at join; heartbeats, tracker relay, agent and browser relays |
| `ORBITAL_SATELLITE_TOKEN` | every machine | the hub uses it to start agents on this machine |
| `ORBITAL_BROWSER_TOKEN` | every machine (one fleet-wide value) | calls to a bridge from another machine; the hub hands out the fleet browser token at join; rejoin (`orbital satellite join`) to refresh it; the bridge restarts automatically after a join that changed it |
| `XAI_API_KEY` | hub, optional | Agent tab voice replies |
| `FISH_API_KEY` | hub, optional | spoken replies |
| `APNS_KEY_ID`, `APNS_TEAM_ID` | hub, optional | direct Apple push, with `apns.p8` next to `secrets.env` |

Tokens are random URL-safe strings. The wizard creates the ones a machine needs. To make one by hand:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

How to rotate each token: [SECURITY.md](../SECURITY.md#rotating-tokens).

## Environment variables

| Variable | Effect |
|---|---|
| `ORBITAL_HOME` | use another home folder (the CLI's `--home` sets it) |
| `ORBITAL_HUB_URL`, `ORBITAL_AUTOMATION_TOKEN` | override the hub address and token for `HubClient.from_config()` and the CLI |
| `ORBITAL_STARTUP_WAIT` | seconds the SDK retries a refused connection at start-up (default 10) |
| `ORBITAL_TRACK_PARENT` | makes `orbital-track start` act as `sub` (subagents) |
| `ORBITAL_TRACK_DISABLE=1` | every `orbital-track` command becomes a no-op |
| `ORBITAL_TRACK_BIN` | path to `orbital-track` for satellite job reporting, or `off` |
| `ORBITAL_TRACKER_NO_HOTKEYS=1` | the tracker registers no global shortcuts |
| `ORBITAL_TRACKER_APP` | path to the tracker app bundle for the services installer |
