# Setup

One command sets up a machine as the **hub** (the first machine) or a **satellite** (every other machine):

```bash
git clone https://github.com/joecodecreations/orbital-agents && cd orbital-agents && ./setup
```

It is safe to run again at any time; it keeps your answers.

## Before you start

| Need | For | Get it |
|---|---|---|
| macOS 14+ or Linux | everything | |
| Python 3.11+ | everything | `./setup` finds it (Homebrew, python.org, pyenv, system) and on macOS offers `brew install python@3.13` |
| Tailscale, signed in | other machines and the phone reaching the hub | [tailscale.com/download](https://tailscale.com/download); Linux: `curl -fsSL https://tailscale.com/install.sh \| sh && sudo tailscale up` |
| Xcode Command Line Tools (macOS) | the tracker app | `xcode-select --install` |
| Full Xcode (macOS) | the Mac and iPhone apps | the App Store; open it once |
| An Apple Developer Team ID | signing the Mac and iPhone apps (a free account works, with 7-day installs) | Xcode → Settings → Accounts |
| Google Chrome (or Chromium) | the Browser Controller, optional | |
| Agent CLIs (`claude`, `codex`, `grok`, `gemini`, …) | running agents, optional | each tool's own installer; sign in to each one |

Without Tailscale the hub still works on its own machine, but satellites and the phone cannot reach it. Without Xcode the wizard skips the apps; re-run it after installing Xcode.

## What `./setup` does

1. Finds Python 3.11 or newer.
2. Creates `.venv` in the repo and installs this package into it (`pip install -e ".[dev]"`); later runs reinstall only when `pyproject.toml` changed.
3. Links the `orbital` command into `~/.local/bin` (and tells you if that folder is not on your PATH).
4. Runs the wizard: `orbital setup` with the same options.

| Option | Meaning |
|---|---|
| `--role hub` / `--role satellite` | skip the role question |
| `--answers FILE` | take answers from a TOML file ([non-interactive setup](#non-interactive-setup)) |
| `--yes`, `-y` | never ask: use answers, kept values and defaults (also the behaviour when there is no terminal) |
| `--dry-run` | print every action and change nothing |
| `--reconfigure` | ask every question again, with the current values as defaults |

`orbital setup` takes the same options once the repo is installed.

## The hub

The first machine. It runs the hub, the scheduler, the watchdog, its own satellite service, and on a Mac the tracker (plus the browser bridge if you want it).

**[1/9] Checking this machine.** Prints the OS, Python, Tailscale (name and address), Xcode, Chrome, which agent CLIs it found, and whether the ports it will use are free (7700–7704, or the ones in your answers file). Only a missing Python stops setup; for the rest it says what you lose and how to fix it. Without Tailscale it asks "Continue without Tailscale for now?".

**[2/9] Role.** It looks for hubs on your tailnet. With none found it suggests **hub**.

**[3/9] This system.**

| Question | Default | Notes |
|---|---|---|
| System name (shown in the apps) | Orbital Agents | |
| This machine's name | its Tailscale name, else the host name | shown in the tracker, the machine list and alerts |
| App sign-in username | your login name | letters, digits and `. _ @ -` |
| Generate a strong password for the app sign-in? | yes when none is set | or type one (8+ characters); stored as `ORBITAL_HUB_PASSWORD` |

**[4/9] Components.**

| Question | Default |
|---|---|
| Agent Satellite Tracker (floating panel on this Mac) | yes (macOS only) |
| Tracker keyboard shortcuts (show/hide, type an item, mic) | yes |
| Shortcut modifiers | `ctrl+option`; `ctrl+option+shift` is suggested when another agent tracker seems to be installed |
| Browser Controller (agents can drive Chrome on this machine) | yes when Chrome is installed |
| Let your other machines drive this Chrome over Tailscale | no on the hub |
| Run agents on this machine when asked | yes |
| Scheduler (runs data feeds and pushes them to the app) | yes |
| Example feeds to schedule | all six from `feeds/jobs.example.json` (machine health, website uptime, GitHub, weather, headlines, the block demo) |
| Install login services (start at login, restart on crash) | yes |

If a port is taken by another program it asks for another one (non-interactive: the next free port).

**[5/9] Keys and tokens.** Optional API keys: xAI (Agent tab voice replies) and Fish Audio (spoken replies). Then it generates the tokens the hub needs (`ORBITAL_AUTOMATION_TOKEN`, `ORBITAL_JOIN_TOKEN`, `ORBITAL_SATELLITE_TOKEN`, `ORBITAL_BROWSER_TOKEN`). Values are never printed in full; everything goes to `~/.orbital-agents/secrets.env` (mode 0600).

**[6/9] Apps.** On macOS:

| Question | Default |
|---|---|
| Build and install the tracker app now | yes when it is not installed yet |
| Apple Developer Team ID (empty = skip the Mac/iPhone apps for now) | found from your Apple Development certificate when there is one |
| Bundle id prefix (your reverse domain) | suggested from your login name, e.g. `com.yourname` |
| Build and install the Orbital Agents Mac app | yes when it is not in /Applications yet |
| iPhone to install to | the connected iPhone (asks when there are several) |
| Build and install the iPhone app on `<device>` | yes on a first setup |

No iPhone connected? It says so; plug it in, trust the Mac, and re-run setup later. More in [APP.md](APP.md).

**[7/9] Agent skills.** When it finds `~/.claude`, `~/.codex`, `~/.grok` or `~/.gemini`, it offers to install the Orbital Agents skills and the always-on rule block for them ([skills](#agent-skills)).

**[8/9] Installing.** Writes `config.toml` (keeping a `config.toml.bak` of the previous one), `secrets.env`, `jobs.json` (an existing one is kept and only gets the newly chosen feeds), and `config.resolved.json`; builds and installs the apps you chose; installs and starts the login services; installs the skills; and shows how to load the Chrome extension (it opens `chrome://extensions` and the extension folder for you).

**[9/9] Checking that it works.** Waits for the services to answer, then runs `orbital test --live` and prints a PASS/FAIL table.

**Summary.** The hub's local and Tailscale addresses, where the app password is, the join token (masked; `orbital satellites invite` prints it), an `orbital://connect?hub=…` link for the app (and a QR code when `qrencode` is installed), the services, the logs folder, and next steps.

Then: open the app on your phone, use the link or QR code, and sign in ([APP.md](APP.md#connect-and-sign-in)).

## A satellite

Every other machine. It runs its satellite service and the watchdog, plus the tracker on a Mac and the browser bridge if you want them.

```bash
git clone https://github.com/joecodecreations/orbital-agents && cd orbital-agents && ./setup
```

**[1/9] Checking this machine.** As on the hub.

**[2/9] Role.** It finds the hub on your tailnet ("found hub … on …") and suggests **satellite**.

**[3/9] Hub.**

| Question | Notes |
|---|---|
| This machine's name | as on the hub |
| Which hub | asked when it found several; "another hub" lets you type an address. With none found, it asks for the address (`http://my-mac.your-tailnet.ts.net:7700`) |
| Join token | the hub's setup printed it; on the hub, `orbital satellites invite` prints it again |

It checks that the hub answers before going on.

**[4/9] Components.** The tracker and its shortcuts (macOS), the Browser Controller, "Let your other machines drive this Chrome over Tailscale" (yes by default on satellites), "Run agents on this machine when asked", and the login services. Satellites have no scheduler.

**[5/9] Apps.** The tracker app (macOS). The Mac and iPhone apps are built on the hub.

**[6/9] Agent skills.** As on the hub.

**[7/9] Installing.** Config, secrets (it generates `ORBITAL_SATELLITE_TOKEN`, and a browser token that the join replaces with the fleet's), services, skills, and the extension steps.

**[8/9] Joining the hub.** `POST /v1/satellites/join` with the join token. It stores the hub token and the fleet's browser token (`ORBITAL_BROWSER_TOKEN`) it gets back, restarts the satellite service (and the browser bridge, when the browser token changed) so they use them, and checks the hub accepts its heartbeat. On the hub, `orbital satellites` now lists the machine. How joining works: [SATELLITES.md](SATELLITES.md#how-discovery-and-joining-work).

**[9/9] Checking that it works.** `orbital test --live` on this machine (its satellite, its heartbeat, its services, the tracker relay, and the hub's view of it). With `components.services = false` it is skipped, because nothing would be running yet.

Because every machine gets the same browser token at join, remote browser control works without copying anything; see [BROWSER_CONTROLLER.md](BROWSER_CONTROLLER.md#use-it-from-another-machine).

## Non-interactive setup

For scripts, provisioning tools and AI agents:

```bash
cp config/answers.example.toml answers.toml     # edit it
./setup --answers answers.toml --yes
./setup --answers answers.toml --yes --dry-run  # print the plan first
```

Every key is optional. A missing key (or `""`) takes its default with `--yes`. Unknown keys are reported, so typos do not pass silently. Secrets can come from the environment instead of the file, so they never sit on disk: `ORBITAL_HUB_PASSWORD`, `ORBITAL_JOIN_TOKEN`, `XAI_API_KEY`, `FISH_API_KEY`.

| Key | Meaning |
|---|---|
| `role` | `auto` (satellite when a hub answers on the tailnet, else hub), `hub` or `satellite` |
| `system.name` | name shown in the apps (hub) |
| `system.machine_name` | this machine's name; empty = short host name |
| `ports.hub`, `ports.tracker`, `ports.stt`, `ports.browser`, `ports.satellite` | 7700, 7701, 7702, 7703, 7704 |
| `ports.on_conflict` | `next-free`, `keep` or `fail` when another program uses a port |
| `components.tracker` | run the tracker here (macOS) |
| `components.browser` | run the browser bridge here |
| `components.browser_remote` | let other machines drive this Chrome (bridge binds to Tailscale) |
| `components.agents` | run agents here when asked (satellite service) |
| `components.scheduler` | hub: run the scheduler |
| `components.feeds` | hub: example feeds to enable: a list of job ids, `"all"`, `"none"` or `"default"` |
| `components.services` | install the login services |
| `tracker.hotkeys`, `tracker.hotkey_modifiers` | tracker shortcuts and their modifiers |
| `login.username` | hub: app sign-in username |
| `login.password` | hub: app sign-in password; empty = keep the current one or generate one (env `ORBITAL_HUB_PASSWORD`) |
| `login.generate_password` | hub: generate a password when none is set and none is given (`false`: report a problem instead) |
| `keys.XAI_API_KEY`, `keys.FISH_API_KEY` | optional API keys (or the same env vars) |
| `apple.team_id` | Apple Developer Team ID; empty = detect from the Apple Development certificate |
| `apple.bundle_prefix` | e.g. `com.yourname` |
| `apple.ios_device` | iPhone to install to; empty = the first connected one |
| `apple.build_tracker` | build and install the tracker app: `true` or `false` |
| `apple.build_mac_app` | build and install the Mac app: `true` or `false` |
| `apple.build_iphone_app` | `auto` (when a device is connected), `true` or `false` |
| `browser.open_extensions_page` | open `chrome://extensions` and the extension folder |
| `satellite.hub_url` | satellite: the hub's URL; empty = find it on the tailnet |
| `satellite.join_token` | satellite: the join token (env `ORBITAL_JOIN_TOKEN`) |
| `skills.install` | install the agent skills where agent homes exist |
| `skills.agents` | which agent homes: a list like `["claude", "codex"]`, or `"all"` |
| `skills.rules` | always-on rule block: `auto` (skip files that already have another tracker's rule), `yes` or `no` |
| `verify.live_test` | run `orbital test --live` at the end |
| `verify.wait_seconds` | how long to wait for services before testing (default 25) |

A satellite, fully unattended:

```bash
cat > answers.toml <<'EOF'
role = "satellite"
[satellite]
hub_url = "http://my-mac.your-tailnet.ts.net:7700"
[components]
browser_remote = true
EOF
ORBITAL_JOIN_TOKEN='<paste the token>' ./setup --answers answers.toml --yes
```

The exit code is 0 when setup finished without problems and 1 when something needs attention (listed under "Needs attention" in the summary).

## Re-running setup

Run `./setup` again to pick up a new version, add the apps after installing Xcode, connect an iPhone, or change answers:

- Without options it **keeps** every answer already in `config.toml` (shown as "kept") and asks only new questions. Generated tokens and your password are kept too.
- `--reconfigure` asks everything again, with the current values as defaults.
- Changing a machine's role removes the services of the old role.
- `config.toml` is rewritten from your answers (`config.toml.bak` keeps the previous version); `jobs.json` is never replaced, only extended with newly chosen feeds.
- The wizard never edits files it did not create, and the always-on rule block in your agent files is fenced with `ORBITAL-AGENTS:BEGIN/END`.

To change one setting, editing `config.toml` is quicker: then `orbital config --apply && orbital services restart` ([CONFIG.md](CONFIG.md)).

## Agent skills

The wizard copies each folder in `skills/` into every agent home it finds, as `orbital-tracker`, `orbital-feeds`, `orbital-satellites`, `orbital-browser` and `orbital-ops`:

| Agent | Skills folder | Always-on rules file |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `~/.claude/CLAUDE.md` |
| Codex | `~/.codex/skills/` | `~/.codex/AGENTS.md` |
| Grok | `~/.grok/skills/` | `~/.grok/AGENTS.md` |
| Gemini | `~/.gemini/config/skills/` | `~/.gemini/GEMINI.md` |

The rule block (from `skills/agent-rules.md`) tells agents to report their work with `orbital-track` and where the skills are. The first change to an existing rules file saves `<file>.orbital-backup`; text outside the fence is never touched. If a rules file already tells agents to report to another tracker, the block is not added there by default, so agents do not report twice.

```bash
orbital skills status                 # what is installed, per agent
orbital skills install                # install or update (also after pulling a new version)
orbital skills install --agent claude --rules no
orbital skills uninstall              # remove the skills and the rule blocks
orbital skills uninstall --rules-only
```

## Uninstall

On each machine:

```bash
orbital services uninstall            # stop and remove the login services
orbital skills uninstall              # remove the agent skills and rule blocks
rm -rf ~/.orbital-agents              # config, secrets, data and logs (back up anything you want first)
rm -f ~/.local/bin/orbital ~/.local/bin/orbital-track
```

Then, if you installed them: delete `/Applications/Orbital Agents - Agent Satellite Tracker.app` and `/Applications/Orbital Agents.app`, remove the app from your iPhone, remove the extension on `chrome://extensions`, and delete the repo folder. On the hub, `orbital satellites remove <id>` forgets a machine you are retiring (do it before you delete the hub's folder, or not at all if you are removing everything). Tailscale stays as it was.
