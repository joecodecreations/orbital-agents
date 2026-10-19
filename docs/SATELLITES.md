# Satellites: more than one machine

Orbital Agents has one **hub** and any number of **satellites**. Every machine, the hub included, runs a satellite service, so "start an agent on machine X" works the same everywhere: from the CLI on any joined machine, from the app, or from a script.

```
                     ┌──────────── hub machine ────────────┐
 iPhone / Mac app ──▶│ hub :7700   scheduler   watchdog     │
                     │ satellite :7704   tracker   bridge   │
                     └───────▲──────────────────────────────┘
                 Tailscale   │ join once, then heartbeats; the hub relays agent runs and browser commands
           ┌─────────────────┼─────────────────┐
  ┌────────┴────────┐ ┌──────┴────────┐ ┌──────┴────────┐
  │ laptop (macOS)  │ │ build box     │ │ home server   │
  │ satellite :7704 │ │ (Linux)       │ │ (Linux)       │
  │ watchdog        │ │ satellite     │ │ satellite     │
  │ tracker, bridge │ │ watchdog      │ │ watchdog      │
  └─────────────────┘ └───────────────┘ └───────────────┘
```

## What a satellite runs

| Service | When | Does |
|---|---|---|
| satellite (`:7704`) | `satellite.enabled` (default on) | starts agent CLIs when the hub asks, reports jobs, sends a heartbeat every `satellite.heartbeat_seconds` |
| watchdog | `watchdog.enabled` (default on) | health checks, restarts and trimming on this machine ([SELF_HEALING.md](SELF_HEALING.md)) |
| tracker (`:7701`) | macOS with the tracker app | the floating panel; on a satellite every `orbital-track` command is also relayed to the hub's tracker |
| browser bridge (`:7703`) | `browser.enabled` | lets agents drive Chrome on this machine ([BROWSER_CONTROLLER.md](BROWSER_CONTROLLER.md)) |

A satellite does not run the hub or the scheduler. Feeds can still run there (cron, your own loop) and push to the hub over Tailscale with the automation token.

## Prerequisites

- **Tailscale** on every machine, signed in to the same tailnet. MagicDNS on (the default) gives each machine a stable name like `my-laptop.your-tailnet.ts.net`.
- The hub's port (7700) reachable from the satellites, and each satellite's port (7704) reachable from the hub. With default Tailscale settings every machine can reach every other; if you use Tailscale ACLs, allow those ports (and 7703 for remote browser control).
- Python 3.11 or newer and this repository on the satellite (`git clone`, then `./setup`).
- The agent CLIs you want to run there (`claude`, `codex`, `grok`, `gemini`, or your own), installed and signed in on that machine.

The hub and satellites only answer loopback and Tailscale addresses (`allow_networks`). Do not expose these ports with Tailscale Funnel, port forwarding or a public address.

## Add a machine

### With the setup wizard (recommended)

On the new machine:

```bash
git clone https://github.com/joecodecreations/orbital-agents && cd orbital-agents && ./setup
```

The wizard looks for a hub on your tailnet, picks it when there is exactly one (or asks which), and asks for the **join token**. The hub's setup printed it at the end; print it again on the hub with:

```bash
orbital satellites invite
```

Then it installs the services, joins, and checks the hub knows the machine. On the hub, `orbital satellites` now lists it.

For an unattended satellite setup, see [SETUP.md](SETUP.md#non-interactive-setup); the join token can come from the `ORBITAL_JOIN_TOKEN` environment variable so it never sits in a file.

### By hand

```bash
# on the hub
orbital satellites invite            # prints: orbital satellite join http://<hub>:7700 <token>

# on the new machine (after cloning and installing the repo)
orbital satellite find               # optional: the hubs Tailscale can see
orbital satellite join http://my-mac.your-tailnet.ts.net:7700 <join-token>
orbital services install             # satellite + watchdog (+ tracker, + bridge) at login
orbital satellite status             # up, agents found, last heartbeat ok
```

### How discovery and joining work

1. `tailscale status --json` lists the online peers.
2. Each peer is asked `GET http://<peer>:7700/v1/hub/info` (1.5 s timeout, all in parallel). A hub answers `{"service": "orbital-hub", "name", "machine", "hub_id", "version"}`. This endpoint needs no token and says nothing secret.
3. The machine makes its own **satellite token** (`ORBITAL_SATELLITE_TOKEN`) if it has none, then calls `POST /v1/satellites/join` with the join token in `X-Orbital-Join-Token` and a description of itself: name, Tailscale address, port, OS, version, and capabilities (which agent CLIs it has, whether it has a browser bridge and a tracker).
4. The hub checks the join token and that the reported address is inside `hub.allow_networks`, stores the machine (keeping the same id when that machine joins again), and answers with a **hub token**. The machine saves it as `ORBITAL_HUB_TOKEN`, writes `hub.url` and `system.role = "satellite"` into its `config.toml` (keeping your other lines), and saves its id in `data/satellite/identity.json`.
5. From then on the satellite sends a heartbeat with its hub token every minute. Three missed heartbeats show it **offline**; the next beat brings it back.

The hub's own satellite joins the local hub by itself, and joins again if the hub forgets it.

## Tokens

| Token | Lives on | Who sends it | Purpose |
|---|---|---|---|
| `ORBITAL_JOIN_TOKEN` | hub | a new machine, once | proves the machine may join |
| `ORBITAL_HUB_TOKEN` | each satellite | satellite → hub | heartbeats, tracker relay, alerts from its watchdog, listing machines, the agent and browser relays |
| `ORBITAL_SATELLITE_TOKEN` | each machine | hub → that machine | starting and reading agent jobs there |
| `ORBITAL_AUTOMATION_TOKEN` | hub (copy it where needed) | feeds, scripts, the CLI → hub | everything a script does, including sections and alerts |
| `ORBITAL_BROWSER_TOKEN` | every machine (one fleet-wide value) | hub or another machine → a bridge | remote browser commands; the hub hands out the fleet browser token at join; rejoin (`orbital satellite join`) to refresh it; the bridge restarts automatically after a join that changed it |

The join token only matters at join time. Anyone holding it can add a machine, so keep it like a password and rotate it after sharing it somewhere you do not trust ([SECURITY.md](../SECURITY.md#rotating-tokens)).

## Run an agent on another machine

```bash
orbital satellites                                         # names, status, agents each machine has
orbital agents run --on build-box "fix the failing tests"  # that machine's default agent, in its first workdir
orbital agents run --on build-box --agent codex --cwd ~/code/api --title "Fix API tests" --wait "fix the failing tests"
orbital agents jobs                                        # jobs on every machine (live, or from the last heartbeat)
orbital agents jobs --on build-box
orbital agents show build-box j20261017180741-5a755d       # status, exit code, the end of the output
orbital agents log build-box j20261017180741-5a755d        # the whole log
orbital agents cancel build-box j20261017180741-5a755d
```

`--on` takes the satellite id, the machine name or the display name. `orbital agents` works from the hub with the automation token, and from any satellite with its hub token, so every joined machine can start work on the others. `--wait` prints the output when the job ends and exits 0 only when it finished `done`. `--no-track` keeps the job out of the tracker.

On the machine itself, without the hub:

```bash
orbital satellite run claude "summarize the README" --cwd ~/code/site --wait
orbital satellite jobs
```

From Python, through the hub:

```python
from orbital.feeds import HubClient

with HubClient.from_config() as hub:
    job = hub.run_agent("build-box", "fix the failing tests", "claude", cwd="~/code/api", title="Fix API tests")
    print(hub.agent_job("build-box", job["job_id"])["status"])
```

From the app: the **Agents** tab (in the sidebar on iPad and Mac) lists every machine, with its status, OS, last heartbeat, the agent CLIs it offers and how many jobs it is running, and below them every job (All / Running / Failed). **Run an agent** sits at the top of that screen. It is also on each machine's page ("Run an agent here", with that machine already chosen), on Home's agent card and menu, and in the Desk's menu. A feed button with the `run_agent` action and an `orbital://run?machine=…&agent=…&prompt=…` link open it already filled in.

The Run agent sheet asks for:

- **Machine:** only machines that are online.
- **Agent:** the CLIs installed on that machine. Claude is picked when it is there.
- **Prompt.**
- **Working folder** (optional): it must be inside one of the machine's `satellite.workdirs`. Empty uses the machine's default.
- **Title** (optional): what the tracker shows.

**Run** sends `POST /v1/satellites/{id}/agents/run` and opens the job. The job page shows the agent, machine, folder, status and running time, the prompt (with a copy button), and the output. While the job runs, the output refreshes every 3 seconds, and **Follow new output** keeps the newest lines in view. **Stop** cancels it, **Run again** starts the same prompt on the same machine, and **Tracker** opens its tracker item. Jobs also update live as the hub broadcasts them.

Over HTTP: `POST /v1/satellites/{id}/agents/run {"agent", "prompt", "cwd"?, "title"?, "track"?}` on the hub returns `{job_id, satellite_id, machine, status}`. The other routes are in [ARCHITECTURE.md](ARCHITECTURE.md#hub-http-api-base-httphub7700).

### What happens on the satellite

- The agent's command comes from `[agents.<name>]` with `{prompt}` and `{cwd}` filled in. No shell is ever used, so a prompt cannot inject shell commands.
- `cwd` must be inside one of `satellite.workdirs` (default `["~"]`). Without `cwd` the job starts in the first workdir; a relative `cwd` is taken relative to it.
- At most `satellite.max_concurrent_jobs` (2) run at once; up to 50 more wait in order.
- A job past `satellite.job_timeout_minutes` (60) is stopped (SIGTERM to its process group, then SIGKILL) and marked `failed`.
- Output (stdout and stderr together) goes to `data/satellite/out/<job>.log`, capped at `satellite.max_output_bytes` (2 MB): the first 64 KB and the end are kept.
- Status goes `queued` → `running` → `done`, `failed` or `cancelled`. Records stay `satellite.job_retention_days` (14), the newest 20 always.
- With `track` on (the default) the job appears in the tracker as a bubble with the agent's icon and the machine name, and is closed when the job ends. Where `orbital-track` is installed (macOS with the tracker) the satellite reports through it. Where it isn't (Linux, or a Mac without the tracker app or with `tracker.enabled = false`), a joined satellite sends the same start and done commands straight to the hub's tracker relay (`POST /v1/tracker/command` with its hub token), from a background thread: the job never waits for the hub, commands queue in memory and are retried while the hub is unreachable, and a failure is logged once. On the hub machine without a tracker app there is nothing to relay to, so reporting is skipped.
- The agent process gets `ORBITAL_JOB_ID`, `ORBITAL_AGENT`, `ORBITAL_MACHINE` and, when tracked, `ORBITAL_TRACK_ID` (its tracker item, so it can add notes with `orbital-track update $ORBITAL_TRACK_ID --note …`).
- A job still running when the satellite service restarts is marked failed ("interrupted"), and its leftover process is stopped.

## Custom agents

Any command-line program can be an agent. Add it to `config.toml` on the machines that have it:

```toml
[agents.aider]
command = ["aider", "--yes", "--message", "{prompt}"]

[agents.local-llm]
command = ["/usr/local/bin/my-llm-cli", "--cwd", "{cwd}"]   # no {prompt}: the prompt arrives on stdin

[agents.gemini]
enabled = false                                              # stop offering one
```

Run `orbital satellite status` to see which agents this machine finds. The list reaches the hub at the next heartbeat (within a minute), or right away after `orbital services restart satellite`. A satellite only offers an agent whose executable it can find. Services start with a short PATH, so the satellite also searches Homebrew, `/usr/local/bin`, `~/.local/bin`, `~/.npm-global/bin`, nvm, bun and cargo folders; for anything else give the full path in `command`.

## Status

```bash
orbital satellites               # on any joined machine: every satellite, online or offline, with its agents
orbital satellite status         # this machine: service up, agents, workdirs, hub link, last heartbeat
orbital status                   # one screen with every local service
```

`●` is online, `○` offline (no heartbeat for three periods). Open apps are told when a machine goes offline or comes back.

## Security model

- **Network.** The hub and every satellite answer only loopback and Tailscale addresses. Tailscale already encrypts and authenticates every connection between your machines.
- **Tokens.** On top of that, each call carries a token: the join token once, then per-machine tokens in both directions. Tokens are compared in constant time and stored hashed on the hub where possible.
- **What a satellite's hub token may do.** List machines, use the agent and browser relays, post alerts (stamped with its machine name), and use the tracker relay (commands without a `machine` are stamped with its name). It cannot read or write sections, actions, media, the desk or sync, cannot retract or acknowledge alerts, and cannot remove machines. A feed that runs on a satellite and needs those uses the automation token.
- **Remote agent runs are opt-in per machine** (`satellite.enabled`) and limited to `satellite.workdirs`. Set `satellite.enabled = false` on a machine where you never want agents started remotely, or narrow `workdirs` to the folders you trust agents with.
- **No shell.** Agent commands are argument lists with the prompt as one argument.

Agents act with your account's permissions on that machine. Treat "run an agent on machine X" like opening a terminal there. More in [SECURITY.md](../SECURITY.md).

## Remove a machine

```bash
orbital satellites remove build-box      # on the hub (or with the automation token): forget it
```

Then on that machine, if you are done with it:

```bash
orbital services uninstall               # stop and remove its login services
rm -rf ~/.orbital-agents                 # its config, tokens, job records and logs
```

A removed machine that still runs its satellite service logs that the hub no longer knows it (at most once an hour). To bring it back, run `./setup` there again or `orbital satellite join <hub_url> <token>`; it gets the same id as before.

## Linux satellites

Linux machines run the satellite service, the watchdog and, if you want, the browser bridge (with Chrome or Chromium and the extension). There is no tracker app on Linux.

- Services are systemd user units: `~/.config/systemd/user/orbital-satellite.service`, `orbital-watchdog.service` plus `orbital-watchdog.timer`, and `orbital-browser-bridge.service`. Check them with `orbital services status` or `systemctl --user status orbital-satellite`.
- User units stop when you log out unless lingering is on. On a server, run `loginctl enable-linger $USER` once (the installer prints this hint; it never changes it for you).
- Service output goes to `~/.orbital-agents/logs/<name>.stderr.log`.
- Jobs started there show in the hub's tracker (and the app's Tracker tab) through the hub's tracker relay, since there is no `orbital-track` on Linux; everything else (jobs, logs, `orbital agents`) works the same.

## Windows satellites

Windows machines run the satellite service, the watchdog and, if you want, the browser bridge, from the repo's `.venv` (`.venv\Scripts\python.exe`). There is no Swift tracker on Windows.

- `orbital services install` registers one Task Scheduler task per service under the `\OrbitalAgents\` folder (`orbital-satellite`, `orbital-watchdog`, `orbital-browser-bridge`; on a hub also `orbital-hub` and `orbital-scheduler`). Each runs at logon as you, with no admin rights, no time limit and one instance. Check them with `orbital services status`, or `schtasks /Query /TN \OrbitalAgents\orbital-satellite`.
- Each task runs `pythonw.exe -m orbital.services.winrun <run spec>`, a small supervisor, because Task Scheduler can't set environment variables or restart a program that exits. The run spec, `%LOCALAPPDATA%\OrbitalAgents\services\orbital-<name>.json`, holds the command, environment, folder and log file. The supervisor restarts the service 10 s after it exits and runs the watchdog once every interval. Stopping the task also stops the service and anything it started.
- Service output goes to `%USERPROFILE%\.orbital-agents\logs\<name>.stderr.log`. Restart counts and the last exit code are in `run\winsvc-<name>.json`, and `orbital services status` shows them.
- Some locked-down accounts can't register logon tasks ("Access is denied"). The service then uses a value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` instead, which starts the same supervisor when you sign in. `orbital services start|stop` handle both kinds.
- Tasks run only while you are signed in. For a machine that must work with nobody signed in, open Task Scheduler and set the task to *Run whether user is logged on or not*. That asks for your Windows password, so setup never does it for you.

## Troubleshooting

| Symptom | Fix |
|---|---|
| The wizard or `orbital satellite find` finds no hub | `tailscale status` on both machines; is the hub's service up (`orbital status` on the hub)? Try `curl http://<hub>:7700/v1/hub/info` from the satellite |
| `join failed: the hub refused the join (401)` | wrong join token; run `orbital satellites invite` on the hub again |
| `join failed: … ip … is not inside hub.allow_networks` | the satellite reported a non-Tailscale address; check `tailscale ip` on it, or that `satellite.bind` is `tailscale` |
| A machine shows offline | `orbital satellite status` there; `orbital logs satellite`; is Tailscale up on both ends? |
| `502 … join it again` when starting a job | the hub's copy of that machine's token is stale; run `orbital satellite join` on the machine again |
| `agent X isn't available on <machine>` | the CLI is not installed or not found under the service's PATH there; `orbital satellite status` on that machine shows where it looked |
| `cwd … is outside satellite.workdirs` | add the folder to that machine's `satellite.workdirs`, then `orbital services restart satellite` |

More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
