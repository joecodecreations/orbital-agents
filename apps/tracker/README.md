# Orbital Agents - Agent Satellite Tracker

A floating, transparent macOS panel that shows what every AI agent (Claude, ChatGPT/Codex, Grok,
Gemini, or any CLI agent) is working on, on this Mac and, through the hub, on every machine in
your Orbital Agents setup. Each piece of work is a colored bubble with the agent's icon. Click it
for details: markdown description, progress notes, screenshots and videos, subagents, and the
machine it runs on. Finished work leaves the panel and stays in a searchable history. The bar is
labelled **Agent Satellite Tracker**; the companion iPhone/Mac app is **Orbital Agents**.

![The panel: bubbles from several agents and machines, one item's details open](docs/screenshot.png)

Agents report with the `orbital-track` CLI (or plain HTTP). You can also type your own items, or
say them out loud when the optional on-device voice mode is on.

## Build and run

Requirements: macOS 14+, Swift 6 toolchain (Xcode 16+ or the matching Command Line Tools).

```bash
cd apps/tracker
scripts/build.sh                 # → build/Orbital Agents - Agent Satellite Tracker.app (release, signed)
scripts/install.sh               # build + copy to /Applications + link ~/.local/bin/orbital-track
scripts/install.sh --dest ~/Applications --open    # somewhere else, and start it
swift build && swift test        # debug build and the unit tests
```

- `build.sh` signs with your "Apple Development" identity when the keychain has one (the
  microphone permission then survives rebuilds), otherwise ad-hoc. `CODESIGN_IDENTITY=-` forces ad-hoc.
- The bundle id is `<apple.bundle_prefix>.orbitalagents.tracker`, read from
  `$ORBITAL_HOME/config.resolved.json` (`com.example.orbitalagents.tracker` before setup).
- The icon is built from `docs/assets/icon-macos-1024.png` (`scripts/make-icon.sh`, all sizes, iconutil).
- Starting at login is done by the services installer: `orbital services install` writes the
  LaunchAgent `io.orbitalagents.tracker`, which runs
  `<app>/Contents/MacOS/OrbitalTracker`. The app is single-instance (bundle id plus a lock on
  `run/tracker.lock`; a second copy exits 0), quits cleanly with exit code 0 on SIGTERM, and saves
  everything before it goes.
- Under launchd the app reads `~/.orbital-agents` unless the LaunchAgent sets `ORBITAL_HOME` in its
  `EnvironmentVariables` (needed only for a non-default home).
- `OrbitalTracker --version` prints the version. `ORBITAL_TRACKER_NO_HOTKEYS=1` skips the global
  shortcuts whatever the config says (for tests, or a second build running next to an installed one).

Global shortcuts (the modifiers come from `tracker.hotkey_modifiers`, default `ctrl+option` = ⌃⌥):

| Shortcut | Action |
|---|---|
| modifiers + T (⌃⌥T) | Show / hide the panel |
| modifiers + N (⌃⌥N) | Type an item |
| modifiers + M (⌃⌥M) | Mic on / off (registered only in voice mode) |

`tracker.hotkey_modifiers` takes any '+'-joined combination of `ctrl`, `option`/`alt`, `shift` and
`cmd`/`command` with at least one of ctrl, option or cmd (e.g. `"ctrl+option+shift"` when another app
already uses ⌃⌥). An unusable value falls back to ctrl+option and logs a warning. `tracker.hotkeys =
false` registers none, and so does `ORBITAL_TRACKER_NO_HOTKEYS=1`. If another app already owns a
combination, the tracker logs which ones it couldn't take. The panel's help texts, the menus and the
**…** › *This machine* section show the combination in use.

Drag the bar to move the panel; it remembers where you put it. The menu-bar icon (an antenna)
has the same actions, plus the data folder and the log.

## The panel

`status` · name + status line · **All / AI / Mine** · history ⟲ · 📷 evidence level · `+` type · `…` settings

- **All / AI / Mine** filters bubbles and history. *Mine* is what you typed or said.
- Bubbles are oldest → newest. Work in progress **blinks**. Done items leave the panel immediately
  (`orbital-track done`, **Mark done**, the bubble's **X**, or saying "I'm done with …").
- **History**: Today / This Week / This Month / All, newest first, with the machine each item ran on.
  **Items per page** (bottom right, default 10): click the number and type 1–999, or pick a preset.
- **Item details** (**…** › *Item details*): inline under the bubbles, or one floating glass window
  per item (resizable, live-updating, Esc closes it). The inline card has a pop-out button.
- **…** menu: speech settings (voice mode), item details style, screenshot evidence level, Grok
  titles, this machine's name/role/port and whether the login item is installed, data folder, log.

## Screenshot evidence (Off / Min 1 / All)

One level, shared by every agent, says how much proof they attach when their work changes something
you can see (an app or iOS screen, a website, a game, a map, a render).

| Level | Agents must attach |
|---|---|
| **Off** | Nothing extra. Media only when they'd make it anyway. |
| **Min 1** | At least one screenshot of the result for every visual change, plus a short video for a game feature where the action matters. |
| **All** | A screenshot of every feature, screen and state they changed, a video of every game feature in motion, each listed with its picture in the summary, for review from the phone. |

Set it with the bar's camera (grey = Off; green with **1** or **ALL** = on), **…** › *Screenshots
from AI agents*, `orbital-track evidence set <level>`, or the Orbital Agents app. It lives in
`<tracker data>/settings.json`:

```json
{"evidence": "off|min1|all", "updatedAt": "2026-01-02T03:04:05Z", "updatedBy": "tracker|app|cli"}
```

The app re-reads the file every 1.5 s, so a change from the hub (the phone) or the CLI shows within
two seconds, with a toast saying who changed it. Unknown `updatedBy` values (including the
name older phone-app builds wrote) read as `app`. On a satellite, the tracker and the CLI also read
the hub's level (`GET <hub>/v1/tracker/settings`); the most recently changed copy wins.

## orbital-track (the agent CLI)

```bash
ID=$(orbital-track start --agent claude --title "Fix Login Redirect" --details "markdown…")
orbital-track update $ID --note "found it" --media /tmp/shot.png
orbital-track sub $ID --agent claude --title "Audit Routes"         # subagents only → prints SUB_ID
orbital-track done $ID --summary "markdown…" --media /tmp/after.png
orbital-track evidence [--json] | orbital-track evidence set <off|min1|all>
orbital-track list [--all] [--json]
orbital-track ping
orbital-track help
```

| Command | Does |
|---|---|
| `start --agent A --title T [--details MD] [--workspace DIR] [--label L] [--id ID] [--media F]...` | New item; prints its id (stdout is only the id). `add` is an alias. A title can also be given as trailing words. |
| `sub <PARENT> --agent A --title T [--details MD] [--media F]...` | Subagent work inside the parent; prints the sub id. |
| `update <ID> [--note N] [--details MD [--append]] [--title T] [--agent A] [--media F]...` | Progress; `note <ID> text…` is a shortcut. |
| `done <ID> [--summary MD] [--media F]...` | Finish an item or sub (`complete`, `finish` are aliases). Prints `ok`. |
| `evidence` | Prints the level and exactly what it asks of an agent; `--json` for the raw settings; `set <level>` changes it. |
| `list` | Active items (`--all` for everything, `--json` for the raw items). Works without the app (reads items.json). |
| `ping` | JSON status: tracker health, this machine, the hub relay (token set, queued commands). Exit 1 when nothing is reachable. |

- Text options also take `--details-file F` / `--summary-file F` / `--note-file F`, or `-` for stdin.
- `--media` takes images or videos; markdown images `![alt](/abs/path.png)` in details/summary/notes
  are attached the same way. The CLI archives a small copy right away (temp files may vanish).
- `--quiet` silences warnings. Agent names are normalised (`Claude Code` → `claude`, `codex` →
  `chatgpt`, …); without `--agent` the CLI guesses from the environment.
- `ORBITAL_TRACK_PARENT=<id>` in the environment turns `start` into `sub` (subagents can't create
  top-level bubbles). `ORBITAL_TRACK_DISABLE=1` makes every command a no-op that still prints an id.
- `start`, `sub` and `done` print the evidence instruction on stderr when the level isn't Off;
  `done` warns when the item has no pictures yet.
- **Tracking never blocks an agent.** Delivery problems warn on stderr, the id is still printed,
  and the exit code is 0. Only usage errors (missing title, bad id) exit 2.

### Delivery

1. HTTP to this Mac's tracker: `POST http://127.0.0.1:<tracker.port>/v1/command`.
2. If that fails: a file in `<tracker data>/inbox/`, else `/tmp/orbital-tracker-inbox-<uid>/` (for
   sandboxed agents). The app applies queued files in order when it starts (and every 5 s).
3. **Satellites** (`system.role = "satellite"` with `hub_url` set) also send every command to the hub:
   `POST <hub_url>/v1/tracker/command` with `Authorization: Bearer <ORBITAL_HUB_TOKEN>` and the same
   JSON, plus each attachment's archived JPEG inline (`media[].inlineArchive`, base64, ≤ 256 KB) because
   the hub can't read this machine's files. Commands that can't reach the hub wait in
   `<tracker data>/hub-outbox/` and go out, oldest first, with the next command once the hub answers
   (at most 500 kept, 7 days). After a failure the hub is skipped for 60 s, so a hub that is down
   costs one short timeout, not one per command. 4xx answers drop the command (retrying can't help).
4. With `tracker.enabled = false` (a machine without the tracker app) the CLI only relays to the hub.

The hub relays `/v1/tracker/command` bodies to its own tracker's `POST /v1/command` (or writes them
into the hub tracker's `inbox/` when it isn't running). Items keep the `machine` they came from, and
media from another machine always shows its archived copy.

## HTTP API

`http://127.0.0.1:<tracker.port>` (default 7701), loopback only. Requests with an `Origin` header
(browsers), a foreign `Host` header, or a POST body that isn't `Content-Type: application/json`
are refused. Bodies are limited to 16 MB; slow clients are cut off after 10 s.

| Method | Path | Body / result |
|---|---|---|
| GET | `/v1/health` | `{"ok":true,"service":"orbital-tracker","version","app","machine","role","port","pid","active","items","evidence","voice":{"enabled","status"},"titles":{"mode","grok"},"relay_to_hub","hotkeys":{"enabled","modifiers","symbols"}}` |
| POST | `/v1/command` | a TrackerCommand (below) → `{"ok":true,"id"}` or `400 {"ok":false,"error"}` |
| POST | `/v1/start`, `/v1/sub/<parent>`, `/v1/update/<id>`, `/v1/done/<id>` | the same fields without `op` (the friendly curl form; start/sub get an id when none is given) |
| GET | `/v1/items` | active items (`?all=1` for everything, history included) |
| GET | `/v1/items/<id>` | one item |
| GET | `/v1/media/<item or sub id>/<media id>` | the original file while it exists on this machine, else the archived JPEG |
| GET | `/v1/settings` | `{"ok":true,"settings":{"evidence","label","blurb","updatedAt","updatedBy"}}` |
| POST | `/v1/settings` | `{"evidence":"off\|min1\|all","by":"tracker\|app\|cli"}` |

A TrackerCommand:

```json
{"op": "start|sub|update|done", "id": "k7m2x9qa", "parent": "…", "agent": "claude", "agentLabel": "Claude Code",
 "machine": "studio-mac", "title": "…", "details": "markdown", "appendDetails": true, "note": "…", "summary": "…",
 "workspace": "/abs/dir", "sentAt": "2026-01-02T03:04:05Z",
 "media": [{"path": "/abs/shot.png", "caption": "…", "archived": {…MediaRef…}, "inlineArchive": "base64 JPEG"}]}
```

Ids (and parents) are 2–64 characters of `[A-Za-z0-9_-]`. A `start` for an id that already exists
updates it, so retries are safe.

### Testing hooks (loopback only)

```bash
curl -s -X POST localhost:7701/v1/debug/ui -H 'Content-Type: application/json' -d '{"view":"history","range":"week"}'
curl -s -X POST localhost:7701/v1/debug/ui -H 'Content-Type: application/json' \
  -d '{"pageSize":15,"page":1,"detailStyle":"window","openWindow":"<id>","toast":"hello"}'
curl -s -X POST localhost:7701/v1/debug/wake -H 'Content-Type: application/json' -d '{"phrase":"hey tracker, fix the build"}'
curl -s -X POST localhost:7701/v1/debug/audio -H 'Content-Type: application/json' -d '{"path":"/abs/phrase.wav"}'   # voice mode
curl -s -X POST localhost:7701/v1/debug/retention -H 'Content-Type: application/json' -d '{}'
```

`debug/ui` also returns the panel's frame and `windowNumber` (for `screencapture -l`). Run a scratch
copy against a temp folder: write a `config.toml` with a free `[tracker] port`, then
`ORBITAL_HOME=/tmp/x orbital config --apply` and
`ORBITAL_HOME=/tmp/x ORBITAL_TRACKER_NO_HOTKEYS=1 ".../Contents/MacOS/OrbitalTracker"`.

## Configuration

Everything comes from `$ORBITAL_HOME/config.resolved.json` (`ORBITAL_HOME` defaults to
`~/.orbital-agents`), which `orbital config --apply` and the setup wizard write from `config.toml`.
Without it the tracker runs on the same defaults as `orbital/core/config.py`. Restart the tracker
after changing settings.

| Setting | Default | Used for |
|---|---|---|
| `system.role`, `hub_url` | `hub`, — | satellites relay every command to the hub |
| `machine_name` | short host name | the `machine` on every item |
| `tracker.enabled` | `true` | `false`: the CLI doesn't deliver locally (relay only) |
| `tracker.port` | `7701` | the HTTP API |
| `tracker.stt_port` | `7702` | the speech sidecar |
| `tracker.voice` | `false` | microphone + on-device speech |
| `tracker.hotkeys` | `true` | `false`: no global shortcuts |
| `tracker.hotkey_modifiers` | `ctrl+option` | modifiers for the M / T / N shortcuts |
| `tracker.titles` | `auto` | `auto`/`grok`: titles from the `grok` CLI when installed; `none`: local titles only |
| `tracker.grok_model` | grok's default | optional model for those titles |
| `tracker.history_retention_days` | `365` | finished items older than this leave items.json (0 = keep) |
| `tracker.media_retention_days` | `60` | archived media older than this is deleted (0 = keep) |
| `logging.level`, `max_bytes`, `backups`, `retention_days` | `INFO`, 5 MB, 3, 14 | the log, and how long stale inbox/outbox files stay |
| `apple.bundle_prefix` | `com.example` | the bundle id at build time |
| `paths.tracker_data`, `paths.logs`, `paths.run`, `paths.secrets` | under `ORBITAL_HOME` | where things live |

Secrets: only `ORBITAL_HUB_TOKEN` (satellites), read from `secrets.env` (`KEY=VALUE`, mode 0600) or
the environment. It is never logged.

## Files

| Path | What |
|---|---|
| `data/tracker/items.json` | every item with its subagents, notes and media refs (the app is the only writer) |
| `data/tracker/settings.json` | the screenshot evidence level, shared with the CLI and the hub |
| `data/tracker/inbox/` (+ `failed/`) | queued commands; unreadable ones move to `failed/` |
| `data/tracker/hub-outbox/` | satellites: commands waiting for the hub |
| `data/tracker/media/YYYY-MM/` | archived copies: images at 480 px JPEG q0.42 (10–40 KB), videos as a 2×2 frame sheet (~20 KB) |
| `data/tracker/icons/<agent>.png` | optional provider logos (`claude`, `chatgpt`, `grok`, `gemini`, or any agent name) |
| `logs/tracker.log` (+ `.1`…) | JSON lines `{"ts","level","component":"tracker","msg",…}`, rolled at `logging.max_bytes`, `logging.backups` kept; item events have `"event": "added\|titled\|updated\|completed\|dismissed\|discarded\|sub_added\|sub_completed"` |
| `logs/tracker-voice.stderr.log` | the speech sidecar's raw output (voice mode) |
| `run/tracker.json` | `{service, version, port, pid, startedAt}` while the app runs |
| `run/tracker.lock` | single-instance lock |

Retention runs at launch and once a day. Per item, at most 300 notes, 200 attachments and 200
subagents are kept (oldest dropped), so nothing grows without bound.

Agent icons: `icons/<agent>.png` → the installed desktop app's icon (Claude, ChatGPT) → a drawn mark.

## Voice (optional)

Off by default. To turn it on:

```bash
apps/tracker/scripts/setup-voice.sh       # Python env in ~/.orbital-agents/voice/venv + model download
# then in ~/.orbital-agents/config.toml:   [tracker] voice = true
orbital config --apply                    # and restart the tracker
```

`setup-voice.sh` downloads about **1 GB** of Python packages (PyTorch with Metal, transformers,
crisperwhisper) and the **~1.5 GB** model `nyralabs/CrisperWhisper2.0_medium` (Hugging Face cache).
Check the model's license on its model card before commercial use. Then:

1. The mic you pick in the **⌄** menu is captured at 16 kHz; a voice-activity detector ends a phrase
   when you pause (1.1 s by default, adjustable).
2. `stt/crisper_server.py` (a resident sidecar on `127.0.0.1:<tracker.stt_port>`, started and stopped
   by the app) transcribes it on the GPU (PyTorch MPS, float16, SDPA attention), about 0.4 s per phrase
   on Apple silicon after a ~5 s load. Audio is never stored.
3. A bubble appears straight away with a local title; with `grok` installed (and `tracker.titles` not
   `none`) it is renamed to the 2–4 word topic. Saying you finished something ("I'm done with the
   marketing research") closes the matching bubble instead.

Wake words (optional, **…** › *Edit wake words…*): comma-separated, each can be several words
(default `hey tracker, track`). Then only phrases starting with one count; the wake word alone arms
the next phrase for 8 s. Python override: `defaults write <bundle id> sttPython /path/to/python`
or `ORBITAL_TRACKER_STT_PYTHON`.

## Agent rules and skill

Agents learn how to report from the project-wide skill `skills/orbital-tracker/SKILL.md` and the always-on
rule `skills/agent-rules.md`. The setup wizard (`orbital skills install`) installs them for Claude Code,
Codex, Grok and Gemini, fenced with `ORBITAL-AGENTS:BEGIN/END`.

## Layout

```
Package.swift
Sources/OrbitalTrackerCore/   config.resolved.json + secrets, paths, models, commands, settings, media archiver,
                              retention, JSON-line logger, hub relay (shared by the app and the CLI; unit tested)
Sources/OrbitalTracker/       the app: panel, detail windows, store, HTTP + inbox, audio + VAD, speech sidecar, titles, views
Sources/orbital-track/        the agent CLI
Tests/OrbitalTrackerCoreTests/  swift test
stt/crisper_server.py         optional CrisperWhisper 2.0 Metal sidecar
scripts/                      build, install, make-icon, setup-voice
Resources/Info.plist.in       app Info.plist template
```

CLI integration tests: `tests/tracker/test_cli.py` at the repo root (`.venv/bin/pytest tests/tracker`).
