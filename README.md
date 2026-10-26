<p align="center">
  <img src="docs/assets/orbital-agents-hero.jpg" alt="Orbital Agents: a self-hosted hub with agents, machines and work orbiting a private Tailscale network" width="900">
</p>

# Orbital Agents

[![CI](https://github.com/joecodecreations/orbital-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/joecodecreations/orbital-agents/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Run, watch and steer AI agents (Claude, Codex, Grok, Gemini, or your own command-line tools) across all your machines, Mac, Windows and Linux. Talk to one fast Agent from your phone and it hands the real work to parallel agents on whichever machine fits. A floating tracker on your Mac, mirrored on your iPhone, shows what every agent is doing, how far along it is and how much of your subscription it is using. The same app shows your dashboards, money, alerts and the health of every machine. Agents can drive a real Chrome browser, here or on another machine. Everything runs on your own computers and talks over your private Tailscale network; there is no cloud service in the middle and no paid API: agents run on the subscriptions you already have.

<p align="center">
  <img src="docs/assets/capabilities.png" alt="Four capabilities: AI agents on every machine, real browser automation, feeds dashboards and alerts, and visibility into every stream of work" width="900">
</p>

## What you can do with it

- **Say it, and it gets done somewhere.** "Why is DNS slow? Run it on the NAS." The Agent answers in about a second, starts an agent on the NAS, keeps talking to you while it works, and tells you what it found. Bigger asks are split into parallel jobs across machines.
- **See every agent at once.** Claude in a terminal, Codex on the build box, Grok on a laptop: each one is a bubble with its notes, progress %, screenshots, subagents and token use, on your Mac and in your pocket.
- **Know what your subscriptions are spending.** Every tracked task and every automation shows the tokens and the share of your plan it used, per run, on average and in total, so a runaway job stands out the same day.
- **Watch every machine.** CPU load, memory, every disk, uptime, heartbeat, which agent CLIs are installed and whether the browser extension is connected, for the hub and each satellite.
- **Turn anything into a dashboard.** A few lines of Python make a page with figures, charts, tables, to-do lists and buttons that call back into your script. Group pages into spaces (a product, a server fleet, a household) and add your own tabs to the bottom bar, with no app update.
- **Get told only when it matters.** Feeds raise alerts and insight cards ("Refunds doubled this week" with one-tap fixes). Failing automations show up on Home with a retry button.
- **Add a machine in one step.** Open joining for 15 minutes and every machine on your Tailscale account that runs setup connects by itself. No token to copy.
- **Let agents use a real browser.** Open pages, click, type, read, upload and take screenshots in your own signed-in Chrome, on this machine or another one.

## See it

The Orbital Agents app on iPhone (sample data; the same screens sit side by side on iPad and Mac):

| Home | Pinned pages | A feed's page | Agent | Tracker |
|:---:|:---:|:---:|:---:|:---:|
| <img src="docs/assets/screenshots/app-home.webp" alt="Home: two insights to act on with one-tap fixes, a failing feed with a retry button, and what needs a look" width="160"> | <img src="docs/assets/screenshots/app-home-pinned.webp" alt="Pinned pages on Home: a home server's CPU, memory, disk and services, website uptime with a visitors chart, and store sales" width="160"> | <img src="docs/assets/screenshots/app-feed-page.webp" alt="A store sales page: today's sales, orders, average order, refunds and a 30-day payouts chart" width="160"> | <img src="docs/assets/screenshots/app-agent.webp" alt="The Agent: a spoken question, a fast answer, and a DNS investigation started on another machine" width="160"> | <img src="docs/assets/screenshots/app-tracker.webp" alt="Tracker: two agents working on different machines, with subagents and screenshots" width="160"> |
| **Satellites** | **A machine** | **Automations** | **Agent settings** | **Alerts** |
| <img src="docs/assets/screenshots/app-satellites.webp" alt="System, Satellites: three machines with CPU load, memory, disk, uptime, installed agents, browser status and heartbeat" width="160"> | <img src="docs/assets/screenshots/app-machine.webp" alt="One machine: health, CPU load, memory, uptime, each disk's free space, and the agent jobs running there" width="160"> | <img src="docs/assets/screenshots/app-automations.webp" alt="System, Automations: working, failing and updating feeds, a refresh button, and subscription usage" width="160"> | <img src="docs/assets/screenshots/app-agent-settings.webp" alt="System, Settings: which subscription the Agent talks with, how it hands out work, and plain-words routing rules" width="160"> | <img src="docs/assets/screenshots/app-alerts.webp" alt="Alerts inbox: a new order, a slow website and a finished agent job, each with Open and Done" width="160"> |

The bottom bar always has **Home, System, Agent, Tracker, Alerts** and **Sections** (opens spaces and pages). Anything after them (here **Money** and **Home Lab**) comes from the hub with `orbital nav`, and so does the sidebar on iPad and Mac. More about the app: [docs/APP.md](docs/APP.md).

<p align="center">
  <img src="apps/tracker/docs/screenshot.png" alt="The Agent Satellite Tracker: bubbles for four pieces of work from different agents, one opened to show its task list, progress, a screenshot and two subagents" width="420">
</p>

## What you get

| Piece | What it does |
|---|---|
| **Hub** | A small server on your main machine. It stores the pages your scripts push, raises alerts, keeps the list of machines and their health, and relays agent runs and browser commands to them. |
| **Satellites** | Every machine (the hub too) runs a satellite service on macOS, Windows or Linux. It starts agent CLIs when asked, reports CPU, memory, disks and its browser extension every minute, and joins the hub by itself. |
| **The Agent** | A fast talker on your phone and Mac, by text or voice. It runs on the Grok or Claude subscription already signed in on the hub (Apple's on-device model as a last resort), answers in seconds and hands real work to agents on your machines, in parallel. |
| **Agent Satellite Tracker** | A floating, see-through panel on your Mac: one bubble per piece of work, with the agent's icon, progress %, notes, screenshots, subagents, token use and the machine it runs on. Agents report to it with `orbital-track`. Windows and Linux have their own tracker. |
| **Orbital Agents app** | iPhone, iPad and Mac. Home shows what needs you; spaces and pages show your data; System holds machines, automations, sections and settings; Agent is the talker; Tracker and Alerts mirror the Mac. |
| **Browser Controller** | A Chrome extension and a local bridge, so agents can open pages, click, type, read, upload and take screenshots in your real browser, on this machine or another one over Tailscale. |
| **Feeds and scheduler** | Small scripts that read anything (a server, a website, an API) and push it to the app as pages, charts, tables, money ledgers, to-do lists and buttons. The scheduler runs them on a timetable, marks each one as a plain script or an agent run, and measures the subscription use of agent runs. |
| **Self-healing** | Login services that restart on crash, a watchdog that restarts what stops answering and tells your phone, and a janitor that keeps logs and data from growing without limit. |
| **Agent skills** | Instructions installed into Claude Code, Codex, Grok and Gemini, so your agents know how to report work, push data, use other machines and drive the browser. |

## How the pieces talk

```mermaid
flowchart TB
    subgraph you["You, anywhere"]
        phone["iPhone / iPad<br/>Orbital Agents app"]
        mac["Mac app +<br/>floating tracker"]
    end

    subgraph hubbox["Main machine: the hub"]
        direction LR
        hub["Hub API<br/>pages · alerts · machines<br/>Agent · usage · nav"]
        sched["Scheduler<br/>feeds and automations"]
        hubsat["Satellite<br/>claude · codex · grok · gemini"]
        bridge["Browser bridge<br/>+ Chrome extension"]
        sched --> hub
        hub <--> hubsat
        hub <--> bridge
    end

    subgraph fleet["Your other machines"]
        s1["Mac satellite"]
        s2["Windows satellite"]
        s3["Linux satellite"]
    end

    you <-->|"Tailscale"| hubbox
    hubbox <-->|"run agents · drive Chrome<br/>heartbeat: CPU, memory, disks, jobs"| fleet
```

## One ask, many agents

The Agent stays free to talk. Anything that takes real work goes to agents on your machines, and you hear back when they finish.

```mermaid
sequenceDiagram
    actor You
    participant Agent as Agent (fast talker,<br/>your subscription)
    participant Hub
    participant A as Satellite: NAS
    participant B as Satellite: build box
    participant T as Tracker

    You->>Agent: "Why is DNS slow? And add dark mode to the status page."
    Agent-->>You: "On it: two jobs." (about a second)
    Agent->>Hub: dispatch 2 jobs in parallel
    Hub->>A: codex: investigate DNS
    Hub->>B: claude: dark mode
    A-->>T: progress 40%, "reading the query log"
    B-->>T: progress 70%, screenshots, tokens used
    T-->>You: live bubbles on Mac and iPhone
    A->>Hub: done: "primary upstream slow since 13:05"
    Hub->>Agent: result
    Agent-->>You: spoken summary + alert
```

## Your data in, decisions out

```mermaid
flowchart LR
    src["Anything a script can read<br/>servers · sites · stores · APIs"] --> feed["Feed script<br/>(Python, JSON or HTTP)"]
    feed -->|"orbital push / SDK"| hub["Hub"]
    hub --> pages["Pages and spaces<br/>figures · charts · tables · money"]
    hub --> alerts["Alerts and insight cards<br/>push to your phone"]
    pages --> app["App: Home, spaces,<br/>custom tabs"]
    alerts --> app
    app -->|"button tap"| action["Action back to your feed<br/>or an agent run"]
    action --> feed
```

## Adding a machine

```mermaid
sequenceDiagram
    actor You
    participant Hub
    participant New as New machine
    You->>Hub: orbital satellites invite (or Add machines in the app)
    Note over Hub: joining open for 15 min,<br/>your own Tailscale account only
    New->>New: ./setup (Windows: setup.cmd), pick satellite
    New->>Hub: finds the hub on Tailscale and asks to join
    Hub-->>New: its own tokens, both directions
    New-->>Hub: heartbeat every minute
    Note over Hub: window closes: no more joins<br/>without the join token
```

<p align="center">
  <img src="docs/assets/visibility-flow.png" alt="Work visibility flow: many agents report with orbital-track, the floating tracker shows bubbles with notes and screenshots, and the same view mirrors on your phone" width="900">
</p>

## Requirements

- **macOS 14 or newer** for the hub, the tracker and building the apps. Linux works as a satellite (agents and the browser controller).
- **Python 3.11 or newer.** `./setup` finds it, and on macOS offers to install it with Homebrew.
- **Tailscale** on every machine and on your phone, signed in to the same tailnet.
- **Xcode** (full Xcode from the App Store) to build the iPhone and Mac apps; the Command Line Tools are enough for the tracker. An Apple Developer account; a free one works (apps then need reinstalling every 7 days).
- **Google Chrome**, optional, for the Browser Controller.
- The agent CLIs you want to use (`claude`, `codex`, `grok`, `gemini`, …), installed and signed in.

## Quick start

On your main machine:

```bash
git clone https://github.com/joecodecreations/orbital-agents && cd orbital-agents && ./setup
```

The wizard checks the machine, asks a few questions (each has a sensible default), writes your settings, installs the background services, builds the apps, installs the agent skills, and finishes with a live end-to-end check. At the end it prints an `orbital://connect` link for the app (and a QR code when `qrencode` is installed) and where the sign-in password is. Walkthrough: [docs/SETUP.md](docs/SETUP.md).

Then open the Orbital Agents app on your phone, follow the link (or type the hub's Tailscale address), and sign in.

## Add another machine

On the other machine, the same command:

```bash
git clone https://github.com/joecodecreations/orbital-agents && cd orbital-agents && ./setup
```

On Windows (PowerShell, with [Git](https://git-scm.com/download/win) and [Tailscale](https://tailscale.com/download/windows) installed), run `setup.cmd` instead, or `.\setup.ps1`:

```powershell
git clone https://github.com/joecodecreations/orbital-agents; cd orbital-agents; .\setup.cmd
```

First open joining on the hub: `orbital satellites invite`, or **Add machines** in the app under System → Satellites. For the next 15 minutes any machine on your Tailscale account that runs the satellite connects by itself, no token to copy. Setup finds the hub, and a machine that was set up while joining was closed connects on its own as soon as you open it. Outside that window only the join token works (`orbital satellites invite --token`). After that, `orbital satellites` on any machine lists it, and you can start agents there from the app or the command line. Details: [docs/SATELLITES.md](docs/SATELLITES.md).

## Set up with an AI agent

Point your coding agent at [AGENTS.md](AGENTS.md). It has the exact unattended steps:

```bash
cp config/answers.example.toml answers.toml     # fill in what you want
./setup --answers answers.toml --yes
orbital doctor && orbital test --live
```

Secrets such as the app password and the join token can come from environment variables instead of the file.

## How it works

<p align="center">
  <img src="docs/assets/how-it-fits.png" alt="Architecture: hub in the center with data API, alerts, scheduler, relays, satellite and watchdog; Agent Satellite Tracker and Browser Controller on the left; iPhone app and feeds on the right; satellites on a laptop, build box and home server linked over Tailscale" width="900">
</p>

Every machine keeps its settings, secrets, data and logs in `~/.orbital-agents`. The contracts between the pieces are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Feed your own data

Anything a script can read can be a page in the app: disk space on a home server, a website's response time, a repo's stars, the weather, your daily steps, a shop's sales, a reading list. A few lines of Python:

```python
from orbital.feeds import HubClient, blocks as b

with HubClient.from_config() as hub:
    hub.put_section(b.section("home-server", "Home server", [
        b.metrics("now", [b.metric("CPU", "12%"), b.metric("Disk free", "412 GB")]),
        b.chart("load", b.chart_series("Load", [("09:00", 0.4), ("10:00", 0.9)]), title="Load"),
        b.buttons("do", [b.button("Restart media server", post="server.restart")]),
    ], icon="server.rack", space="home-lab", space_title="Home Lab"))
    hub.alert("Backup failed", "The nightly backup exited with code 2.", section_id="home-server", level="warning")
```

Or push a JSON file with `orbital push`, or use plain HTTP from any language. Buttons in the app come back to your script as actions. Six example feeds are included in [`feeds/examples/`](feeds/examples). Start with [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md); it has a ten-minute walkthrough.

## Daily commands

| Command | What it does |
|---|---|
| `orbital status` | one screen: this machine's role, the hub, each service up or down, satellites, recent problems |
| `orbital doctor` | checks everything and says how to fix each problem; `--fix` applies the safe fixes |
| `orbital logs -f` | follow the logs of every component (`orbital logs hub`, `--level WARNING --since 1h`) |
| `orbital satellites` | the machines joined to the hub, online or offline, with their agents |
| `orbital agents run --on <machine> "prompt"` | start an agent on another machine (`--agent codex`, `--cwd ~/code/app`, `--wait`) |
| `orbital agents jobs` | agent jobs on every machine |
| `orbital browser status` | the browser bridge and extension; `orbital browser --node <machine> navigate URL` drives another machine's Chrome |
| `orbital push page.json` | push a page (or `--alert "Title" --body "…"`) to the app |
| `orbital jobs` | your feeds: schedule, next run, last result; `orbital jobs run <id> --wait` runs one now |
| `orbital services status` | the background services; `orbital services restart [name]` |
| `orbital test --live` | end-to-end checks against the running install, with a PASS/FAIL table |
| `orbital-track start --agent claude --title "…"` | what agents run to show up in the tracker |

## Documentation

| Doc | About |
|---|---|
| [SETUP.md](docs/SETUP.md) | the setup wizard for a hub and for a satellite, unattended setup, re-running, uninstalling |
| [DATA_PIPELINE.md](docs/DATA_PIPELINE.md) | putting your data in the app: sections, every block type, alerts, buttons, the Python SDK, HTTP |
| [FEEDS.md](docs/FEEDS.md) | the scheduler, `jobs.json`, and the example feeds |
| [SATELLITES.md](docs/SATELLITES.md) | more machines, running agents remotely, custom agents, Linux |
| [BROWSER_CONTROLLER.md](docs/BROWSER_CONTROLLER.md) | the Chrome extension and bridge, here and remotely |
| [APP.md](docs/APP.md) | the iPhone and Mac app: connecting, building, signing; the tracker app |
| [NOTIFICATIONS.md](docs/NOTIFICATIONS.md) | how alerts reach your phone, and optional direct Apple push |
| [CONFIG.md](docs/CONFIG.md) | every setting and secret |
| [LOGGING.md](docs/LOGGING.md) | where logs are and how to read them |
| [SELF_HEALING.md](docs/SELF_HEALING.md) | services, the watchdog and the janitor |
| [TESTING.md](docs/TESTING.md) | the test suites, live checks and CI |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | common problems and exact fixes |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the pieces fit, ports, files, APIs |
| [AGENTS.md](AGENTS.md) | instructions for AI agents working with this repo |
| [SECURITY.md](SECURITY.md) | the security model, rotating tokens, reporting a vulnerability |

## Security in brief

- Nothing listens on the public internet. The hub and satellites answer only loopback and Tailscale addresses; the tracker and (by default) the browser bridge listen on loopback only.
- Every call carries a token: the app signs in with a username and password, scripts use an automation token, machines join once with a join token and then use their own tokens in both directions.
- Remote agent runs are opt-in per machine and limited to the folders you allow; commands never go through a shell.
- Secrets live only in `~/.orbital-agents/secrets.env` (mode 0600). They are never written to config files, logs or the repo.

Read [SECURITY.md](SECURITY.md) before you give other people access to your tailnet. Report vulnerabilities privately as described there.

## Contributing

Bug reports, fixes and new feed examples are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, tests and the pull request checklist.

## License

[MIT](LICENSE)
