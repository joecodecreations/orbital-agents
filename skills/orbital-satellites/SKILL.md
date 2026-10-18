---
name: orbital-satellites
description: Work across the user's machines with Orbital Agents - list the machines joined to the hub, start an agent (claude, codex, grok, gemini, or a custom CLI) on another machine, follow, read and cancel its jobs, and check a machine's satellite service and logs. Load it when the user says "run this on my laptop / the build box / another machine", asks which machines are online or what agents are running, or wants work spread across machines.
---

# Orbital Agents satellites: run agents on other machines

Every machine (the hub too) runs a satellite service that starts agent CLIs when asked. The hub keeps the list of machines and relays requests to them over Tailscale. `orbital agents` works from the hub and from every joined satellite. Full guide: `docs/SATELLITES.md`.

## See the machines

```bash
orbital satellites                 # ● online / ○ offline, address, last seen, agents each machine has, id
orbital satellites --json
orbital satellite status           # this machine: service up, agents found, workdirs, hub link, last heartbeat
```

A machine is offline after three missed heartbeats (about 3 minutes). Use the id, the machine name or the display name wherever a command asks for `<machine>`.

## Start an agent on another machine

Before starting work on a machine the user did not mention, ask them. Starting an agent there is like opening a terminal on that machine.

```bash
orbital agents run --on build-box "fix the failing tests"          # that machine's default agent, in its first workdir
orbital agents run --on build-box --agent codex --cwd ~/code/api --title "Fix API Tests" "fix the failing tests"
orbital agents run --on build-box --agent claude --wait "summarize the open TODOs in ~/code/api"   # waits, prints the output
```

| Option | Meaning |
|---|---|
| `--on <machine>` | required: where to run |
| `--agent <name>` | an agent that machine offers (see `orbital satellites`); default: its `satellite.default_agent` |
| `--cwd <dir>` | must be inside that machine's `satellite.workdirs`; relative paths start from its first workdir |
| `--title <text>` | the tracker title (default: the first line of the prompt) |
| `--no-track` | keep the job out of the tracker |
| `--wait` | wait for the end, print the output; exit 0 only when the job finished `done` |
| `--json` | print JSON |

The prompt is one argument; quote it. It reaches the agent as a single argument (or on stdin), never through a shell.

## Follow jobs

```bash
orbital agents jobs                        # every machine (live, or from the last heartbeat when one does not answer)
orbital agents jobs --on build-box
orbital agents show build-box <job-id>     # status, agent, exit code, the end of the output
orbital agents log build-box <job-id>      # the whole output
orbital agents cancel build-box <job-id>
```

Status: `queued` → `running` → `done`, `failed` or `cancelled`. A job past `satellite.job_timeout_minutes` (60) is stopped and marked failed. At most `satellite.max_concurrent_jobs` (2) run at once per machine; more wait in order.

## On this machine only

```bash
orbital satellite run claude "summarize the README" --cwd ~/code/site --wait
orbital satellite jobs
```

## From Python

```python
from orbital.feeds import HubClient

with HubClient.from_config() as hub:
    for machine in hub.satellites():
        print(machine["name"], machine["status"], machine["capabilities"]["agents"])
    job = hub.run_agent("build-box", "fix the failing tests", "claude", cwd="~/code/api", title="Fix API Tests")
    status = hub.agent_job("build-box", job["job_id"])["status"]
    log = hub.agent_log("build-box", job["job_id"])["log"]
```

## When something is wrong

| Message | Meaning and fix |
|---|---|
| `agent X isn't available on <machine>` | that CLI is not installed there, or the service cannot find it; `orbital satellite status` on that machine lists what it found |
| `cwd … is outside satellite.workdirs` | pick a folder inside that machine's workdirs, or ask the user to add one to its `config.toml` |
| `no satellite <name>` | check the name with `orbital satellites` |
| `502 … join it again` | that machine's token is stale: run `orbital satellite join <hub_url> <token>` there (the token: `orbital satellites invite` on the hub) |
| `no answer from …` / offline | the machine is asleep, off Tailscale, or its service is down: `orbital status` and `orbital logs satellite` on that machine |

Logs on a machine: `orbital logs satellite -f`. The job's own output stays on that machine in `~/.orbital-agents/data/satellite/out/<job>.log`; read it remotely with `orbital agents log`.

## Adding agents or machines

- New machine: on it, `./setup` (choose satellite) or `orbital satellite join <hub_url> <join-token>`. Ask the user before installing services on a machine they did not mention.
- New agent CLI on a machine: add `[agents.<name>] command = ["tool", "--flag", "{prompt}"]` to that machine's `~/.orbital-agents/config.toml`, then `orbital services restart satellite`. `{prompt}` and `{cwd}` are filled in; without `{prompt}` the prompt goes to stdin.
- Remove a machine: `orbital satellites remove <id>`.

Never print or copy `secrets.env` values (join token, hub token, satellite token) into replies or files.
