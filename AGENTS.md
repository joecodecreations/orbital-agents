# AGENTS.md: instructions for AI agents

You are an AI agent working with the Orbital Agents repository: setting it up for a user, operating it, adding a feed, or changing the code. Read this file first. Human docs are in `docs/`; the contracts between components are in `docs/ARCHITECTURE.md`.

## Rules

1. **Never print, log, commit or paste a secret.** Secrets live only in `~/.orbital-agents/secrets.env` (passwords, tokens, API keys) and `~/.orbital-agents/apns.p8`. Refer to keys by name (`ORBITAL_AUTOMATION_TOKEN`), never by value. Do not `cat` that file into your output; read single values into variables when a command needs them.
2. **Never commit** `secrets.env`, `config.toml`, `*.p8`, logs, or anything from `~/.orbital-agents`. Run `.venv/bin/pytest tests/test_public_safety.py -q` before every commit.
3. **Ask before installing services on a machine the user did not mention**, before `orbital services uninstall`, before deleting `~/.orbital-agents`, and before starting agents on a machine the user did not name.
4. **Ask before driving the user's browser**, unless they already approved it for this task. Never enter passwords or payment details, never solve CAPTCHAs, never send, post, buy or delete through the browser without the user's explicit yes in this conversation.
5. **Report your work** to the tracker with `orbital-track` (see "Report your work" below).
6. Examples, docs and test data stay generic (home lab, website uptime, weather, a reading list). No real people, companies or private data.

## Set it up (unattended)

Requirements: macOS 14+ or Linux, Python 3.11+, Tailscale signed in (check with `tailscale status`). On macOS, Xcode for the apps (optional).

```bash
git clone https://github.com/joecodecreations/orbital-agents
cd orbital-agents
cp config/answers.example.toml answers.toml        # edit only what you need
./setup --answers answers.toml --yes --dry-run     # print the plan; changes nothing
./setup --answers answers.toml --yes               # do it; exit code 0 = no problems, 1 = see "Needs attention"
```

`./setup` creates `.venv`, installs the package, links `orbital` into `~/.local/bin` (use `.venv/bin/orbital` if that is not on PATH), then runs the wizard. Pass secrets through the environment so they never sit in a file: `ORBITAL_HUB_PASSWORD`, `ORBITAL_JOIN_TOKEN`, `XAI_API_KEY`, `FISH_API_KEY`.

### Answers file keys (`config/answers.example.toml` documents each)

| Key | Values |
|---|---|
| `role` | `auto` (satellite when a hub answers on the tailnet, else hub), `hub`, `satellite` |
| `system.name`, `system.machine_name` | display name (hub); this machine's name (empty = host name) |
| `ports.hub`, `ports.tracker`, `ports.stt`, `ports.browser`, `ports.satellite`, `ports.on_conflict` | 7700–7704; `next-free`, `keep` or `fail` |
| `components.tracker`, `components.browser`, `components.browser_remote`, `components.agents`, `components.scheduler`, `components.services` | `true` / `false` |
| `components.feeds` | hub: `"default"`, `"all"`, `"none"`, or a list of ids from `feeds/jobs.example.json` |
| `tracker.hotkeys`, `tracker.hotkey_modifiers` | `true` / `false`; e.g. `"ctrl+option+shift"` |
| `login.username`, `login.password`, `login.generate_password` | hub app sign-in; prefer `ORBITAL_HUB_PASSWORD` in the environment, or let it generate one |
| `keys.XAI_API_KEY`, `keys.FISH_API_KEY` | optional; prefer the environment |
| `apple.team_id`, `apple.bundle_prefix`, `apple.ios_device` | app signing; empty team id = detect it |
| `apple.build_tracker`, `apple.build_mac_app`, `apple.build_iphone_app` | `true` / `false`; iPhone also `auto` |
| `browser.open_extensions_page` | open `chrome://extensions` for the user (loading the extension is a manual click) |
| `network.mode`, `network.public_url` | `tailscale` (default) or `tunnel`; tunnel hub HTTPS URL for phones/satellites |
| `satellite.hub_url`, `satellite.join_token` | satellite: the hub URL (empty = Tailscale discover; required in tunnel mode); token (prefer `ORBITAL_JOIN_TOKEN`) |
| `skills.install`, `skills.agents`, `skills.rules` | install skills; `"all"` or a list; `auto`, `yes`, `no` |
| `verify.live_test`, `verify.wait_seconds` | run `orbital test --live` at the end; seconds to wait for services |

### A hub, then a satellite

```bash
# hub
printf 'role = "hub"\n[apple]\nbuild_iphone_app = false\n' > answers.toml
ORBITAL_HUB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')" ./setup --answers answers.toml --yes
orbital satellites invite --json        # contains the join token: hand it to the next step, do not print it to the user's chat

# satellite (on the other machine)
printf 'role = "satellite"\n[satellite]\nhub_url = "http://<hub-name>.<tailnet>.ts.net:7700"\n' > answers.toml
ORBITAL_JOIN_TOKEN='<token>' ./setup --answers answers.toml --yes
```

The one manual step: loading the Chrome extension (`chrome://extensions` → Developer mode → Load unpacked → `browser-extension/`). Ask the user to do it; then check with `orbital browser status`.

Re-running `./setup` keeps existing answers; `--reconfigure` asks again. Full walkthrough: `docs/SETUP.md`.

## Where things live

Everything for one machine is in `~/.orbital-agents` (or `$ORBITAL_HOME`; every `orbital` command takes `--home DIR`).

| Path | What |
|---|---|
| `config.toml` | settings. After editing: `orbital config --apply && orbital services restart` |
| `secrets.env` | secrets, mode 0600 (never print) |
| `config.resolved.json` | merged settings for the Swift apps (generated; do not edit) |
| `jobs.json` | the feed timetable (the scheduler reloads it by itself) |
| `data/hub/`, `data/scheduler/`, `data/satellite/`, `data/tracker/`, `data/browser/`, `data/feeds/` | each component's data |
| `logs/<component>.log`, `logs/<component>.stderr.log` | JSON-line logs; service stdout/stderr |
| `run/` | pid, port and heartbeat files |

`orbital config --paths` prints them; `orbital config <dotted.key>` prints one setting. Every setting: `docs/CONFIG.md`.

## Verify

```bash
orbital status                  # role, hub reachable, each service up or down, recent problems
orbital doctor                  # every check with a fix; --json to parse; --fix applies the safe fixes
orbital test --live             # PASS/FAIL table against the running install; exit 0 = all passed
orbital test --live --json
```

Do not tell the user the install works until `orbital test --live` passes (a `skip` with a reason is fine; quote any FAIL with its hint).

## Read logs

```bash
orbital logs --level WARNING --since 1h        # problems everywhere
orbital logs <component> -n 100                 # hub, satellite, scheduler, watchdog, browser-bridge, tracker, app, wizard, cli, feeds
orbital logs <component> --stderr -n 100        # a service's raw stdout/stderr: start-up tracebacks
orbital logs <component> --json -n 500          # JSON lines for jq
orbital logs --list
```

Each line has `ts`, `level`, `component`, `msg` and fields. When you report a cause, quote the `msg` and fields. More: `docs/LOGGING.md`, `docs/TROUBLESHOOTING.md`.

## Add a feed (put data in the app)

1. Write a script that reads the source and pushes one section with the SDK (run it with `.venv/bin/python`):

   ```python
   from orbital.feeds import HubClient, blocks as b
   with HubClient.from_config() as hub:
       hub.put_section(b.section("website", "Website", [
           b.metrics("now", [b.metric("Status", "Up"), b.metric("Response", "182 ms")]),
       ], icon="globe", space="home-lab", space_title="Home Lab"))
   print("website pushed=1 status=up")
   ```

2. Check the shape first: `hub.validate("website", section=doc)` or `orbital push file.json --validate`.
3. Schedule it: add a job to `~/.orbital-agents/jobs.json` with `"argv": ["{python}", "/abs/path/feed.py"]`, `every`, `offset`, `targets`.
4. Check: `orbital jobs run <id> --wait`, then `orbital jobs`.

Rules: one section id per feed, stable block ids, real data only (a callout when there is none), one result line per run (`name key=value …`). Everything: `docs/DATA_PIPELINE.md` and `docs/FEEDS.md`; the `orbital-feeds` skill has a cheat-sheet.

## Run agents on other machines

```bash
orbital satellites                                                   # machines, status, agents each offers
orbital agents run --on <machine> --agent claude --cwd ~/code/app --title "Fix Tests" "fix the failing tests"
orbital agents jobs
orbital agents show <machine> <job>          # status and the end of the output
orbital agents log <machine> <job>
orbital agents cancel <machine> <job>
```

`--cwd` must be inside that machine's `satellite.workdirs`. Add `--wait` to block until it ends and print the output. Details: `docs/SATELLITES.md` and the `orbital-satellites` skill.

## Use the browser controller

After asking the user (rule 4):

```bash
orbital browser status                                      # bridge up, extension connected
orbital browser --session <you> navigate https://example.com
orbital browser --session <you> text
orbital browser --session <you> click-text "More information"
orbital browser --session <you> screenshot --out /abs/shot.png
orbital browser --node <machine> --session <you> tabs        # another machine's Chrome
```

Python: `from orbital.browser import BrowserClient`. Use your own `--session` so you get your own background window. Details: `docs/BROWSER_CONTROLLER.md` and the `orbital-browser` skill.

## Report your work

```bash
orbital-track start --agent <your-name> --title "2-5 Word Title" --details "goal, project"   # prints ID
orbital-track update <ID> --note "milestone" [--media /abs/shot.png]
orbital-track done <ID> --summary "what changed, where, what is left"
```

Subagents: if your prompt has `ORBITAL_TRACK_PARENT=<ID>`, use `orbital-track sub <ID> …` and `orbital-track done <SUB_ID> …`, never `start`. For visual work check `orbital-track evidence` and attach the screenshots it asks for. If `orbital-track` is missing, carry on. Details: the `orbital-tracker` skill.

## Skills

`skills/` holds the agent skills (`orbital-tracker`, `orbital-feeds`, `orbital-satellites`, `orbital-browser`, `orbital-ops`) and `skills/agent-rules.md`, the always-on rule block. Setup installs them into `~/.claude/skills`, `~/.codex/skills`, `~/.grok/skills` and `~/.gemini/config/skills` (`orbital skills status|install|uninstall`). `.claude/skills/` in this repo links to the same folders, so Claude Code sessions inside the repo have them. Edit only `skills/`; there is one source of truth.

## Working on the code

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'    # dev install without configuring the machine
make test                        # Python tests, tracker swift tests, unsigned app build, lint
.venv/bin/pytest tests/hub -q    # one area
make lint
.venv/bin/pytest tests/test_public_safety.py -q
```

Tests use a temporary `ORBITAL_HOME` and free ports; never point them at the user's `~/.orbital-agents` or ports 7700–7704. If code and `docs/ARCHITECTURE.md` disagree, fix one of them in the same change. One CLI subcommand per file in `orbital/cli/commands/`. Log with `orbital.core.get_logger(component)`; never log secrets or request bodies. More: `CONTRIBUTING.md`, `docs/TESTING.md`.
