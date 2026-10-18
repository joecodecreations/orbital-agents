# Orbital Agents for iPhone, iPad and Mac

The app for your Orbital Agents hub. It draws whatever your feeds publish (sections made of blocks), shows your machines and the AI agents running on them, takes alerts as notifications, and gives you a voice desk for starting work while you are away from the keyboard. One SwiftUI target runs on iPhone, iPad and, as Mac Catalyst, on the Mac.

<p>
<img src="../../docs/assets/screenshots/app-home.png" width="19%" alt="Home">
<img src="../../docs/assets/screenshots/app-section-charts.png" width="19%" alt="A section with charts">
<img src="../../docs/assets/screenshots/app-agents.png" width="19%" alt="Agents">
<img src="../../docs/assets/screenshots/app-tracker.png" width="19%" alt="Tracker">
<img src="../../docs/assets/screenshots/app-alerts.png" width="19%" alt="Alerts">
</p>

The screenshots are the app in preview mode with the sample data in `Fixtures/preview`.

## Requirements

Xcode 16 or newer on a Mac. The app needs iOS 17 / iPadOS 17, or macOS 14 for the Mac version. A hub to sign in to (`./setup` at the repo root), reachable from the device, normally over Tailscale. You need no Apple account to build for the simulator, a free one to install on your own iPhone, and a paid one only for push notifications.

## Build and run

```bash
apps/orbital-app/scripts/build.sh             # iOS Simulator build, unsigned
apps/orbital-app/scripts/build.sh --all       # and the Mac Catalyst build
apps/orbital-app/scripts/test.sh              # unit tests on an iPhone simulator
apps/orbital-app/scripts/screenshots.sh       # preview mode on a simulator → docs/assets/screenshots
```

Every script runs `generate_xcodeproj.py` first, then `xcodebuild`, with build output in `apps/orbital-app/.derivedData*` (git-ignored). To work in Xcode, run `python3 apps/orbital-app/generate_xcodeproj.py` and open `apps/orbital-app/OrbitalAgents.xcodeproj`. Run the generator again after adding or removing a Swift file.

The test and screenshot scripts pick an iPhone simulator that is shut down, boot it, and shut it down again when they finish. A simulator you already have running is left alone. `--device "iPhone 16 Pro"` picks one by name.

## Signing: Team ID, bundle id, app name

The Xcode project is generated, not committed, because it carries your Apple Team ID and bundle id. `generate_xcodeproj.py` takes each setting from the first of these that has it:

| Setting | Flag | Environment | `~/.orbital-agents/config.resolved.json` | Default |
|---|---|---|---|---|
| Team ID | `--team` | `ORBITAL_TEAM_ID` | `apple.team_id` | none: unsigned builds only |
| Bundle prefix | `--bundle-prefix` | `ORBITAL_BUNDLE_PREFIX` | `apple.bundle_prefix` | `com.example` |
| App name | `--app-name` | `ORBITAL_APP_NAME` | `apple.app_name` | `Orbital Agents` |
| Push entitlement | `--apns` / `--no-apns` | `ORBITAL_APNS=1` | `notifications.apns` | off |
| Push environment | `--apns-environment` | `ORBITAL_APNS_ENVIRONMENT` | `notifications.apns_environment` | `development` |
| iPhone to install on | (deploy script `--device`) | `ORBITAL_IOS_DEVICE` | `apple.ios_device` | first connected iPhone |

`orbital setup` writes the config file (`ORBITAL_HOME` moves it). The bundle id is `<bundle prefix>.orbitalagents`, and the tests are `<bundle prefix>.orbitalagents.tests`. The Mac version uses the same bundle id. The app name is what the Home Screen and the Mac's Applications folder show. `python3 generate_xcodeproj.py --print` shows the settings it would use.

Your Team ID is under Xcode → Settings → Accounts → your team (10 letters and digits). A free Apple account works for your own devices: Xcode makes a 7-day provisioning profile, so reinstall each week.

## Install on your iPhone or iPad

```bash
apps/orbital-app/scripts/deploy-iphone.sh                    # build, install, launch
apps/orbital-app/scripts/deploy-iphone.sh --device "My iPad" # by name or identifier
```

The script builds Debug signed with your Team ID (`-allowProvisioningUpdates`), then installs and launches it with `xcrun devicectl`. Connect the device by cable once and trust the Mac. On iOS 16 and later, also turn on Settings → Privacy & Security → Developer Mode. Without a Team ID the script stops and says how to set one.

## Install on your Mac

```bash
apps/orbital-app/scripts/deploy-mac.sh          # Release build → /Applications/<app name>.app
apps/orbital-app/scripts/deploy-mac.sh --open   # and open it
```

The Mac app is the same target built for Mac Catalyst. The script stops a running copy, replaces `/Applications/<app name>.app`, registers it so `orbital://` links open it, and reopens it in the background if it was running. It never replaces an app at that path with a different bundle id. Without a Team ID it signs the app to run on this Mac only. `ORBITAL_MAC_APP_DIR` installs somewhere else.

On the Mac and on iPad, the app uses a sidebar (Home, Agents, Desk, Tracker, Alerts, your spaces and sections) with the page beside it. On iPhone the same screens are tabs. The Mac window opens at 1280 × 860 and won't shrink below 960 × 620. The Mac keeps its session in the keychain like the phone does. If the keychain refuses an unsigned build, it falls back to files in `~/Library/Application Support/<app name>/secrets` (mode 600).

## Signing in

The sign-in screen asks for the hub's address (for example `http://your-hub.your-tailnet.ts.net:7700`, or its `100.x` Tailscale address), the username and the password. The username and password are `ORBITAL_HUB_USERNAME` and `ORBITAL_HUB_PASSWORD` in `~/.orbital-agents/secrets.env` on the hub. An address without a port gets `:7700`. The app checks `GET /v1/hub/info` first, so a typo or a stopped hub says "Can't reach the hub. Is Tailscale on…" rather than a generic error, and a wrong password says so.

`./setup` and `orbital setup` print an `orbital://connect?hub=<address>` link. Opening it on the device (AirDrop or message it to yourself) fills in the address.

## Notifications

Alerts reach the app three ways, and any one is enough. The live socket (`/v1/live`) delivers them while the app is open. Background refresh (`$(PRODUCT_BUNDLE_IDENTIFIER).refresh`) syncs every so often and posts local notifications. Apple push, which is optional, delivers them when the app isn't running.

To use push, you need a paid Apple developer account, an APNs key on the hub (see `docs/NOTIFICATIONS.md`), `notifications.apns = true` in the config, and a project generated with the Team ID set. The generator then adds the `aps-environment` entitlement. When `GET /v1/health` reports `apns.enabled`, the app asks for a device token and sends it to `POST /v1/devices` as `apns_token` with `platform` (`ios` or `mac`). Tapping a push opens the alert's section, using the `orbital` dictionary in the payload (`alert_id`, `section_id`, `node_id`, `tab_id`, `launch_url`). The app drops duplicates, so the same alert never shows twice through push and the local path. Without push nothing changes: free accounts build exactly as before.

## Preview mode (no hub)

Debug builds can draw a folder of sample payloads in place of a hub, which is handy for UI work and screenshots:

```bash
xcrun simctl boot "iPhone 16 Pro"
xcrun simctl install booted apps/orbital-app/.derivedData/Build/Products/Debug-iphonesimulator/OrbitalAgents.app
SIMCTL_CHILD_ORBITAL_PREVIEW_DIR="$PWD/apps/orbital-app/Fixtures/preview" \
SIMCTL_CHILD_ORBITAL_PREVIEW_ROUTE="orbital://agents" \
  xcrun simctl launch booted com.example.orbitalagents
```

`ORBITAL_PREVIEW_DIR` points at a folder shaped like the hub's answers. `ORBITAL_PREVIEW_ROUTE` (optional) opens an `orbital://` link at launch. In Xcode, set both under Scheme → Run → Environment Variables. Release builds don't contain preview code.

| File | Stands in for |
|---|---|
| `hub.json` | `GET /v1/hub/info` |
| `catalog.json` | `GET /v1/catalog` (its `spaces`) |
| `prefs.json` | `GET /v1/prefs` (sections pinned to Home) |
| `sections/*.json` | `GET /v1/sections/{id}` |
| `alerts.json`, `pulse.json` | alerts and live values from `/v1/sync` |
| `jobs.json`, `job-logs/<id>.txt` | `GET /v1/jobs` and a feed's log |
| `satellites.json` | `GET /v1/satellites` |
| `agent-jobs.json`, `agent-logs/<job>.txt` | `GET /v1/agents/jobs` and a job's log |
| `desk/desk.json`, `desk/speech.json` | `GET /v1/desk`, `GET /v1/speech/status` |
| `desk/tracker.json`, `desk/items/<id>.json` | `GET /v1/tracker/items` and one item |
| `media/<id>`, `desk/media/<id>.jpg` | `/v1/media/<id>` and tracker pictures |

So the samples always look current, any JSON string may be a date token: `"@now"`, `"@now-15m"`, `"@now+2h"`, `"@now-3d"` become ISO timestamps and `"@day"`, `"@day-6"` become `yyyy-MM-dd` days as the file loads. The samples are generic: a home server, website uptime, GitHub repositories, weather, fitness, store sales, a household list and runbooks. Between them they use every block type, and the unit tests decode all of them.

## Screens and the hub data behind them

| Screen | What it shows | Hub API |
|---|---|---|
| Sign in | address, username, password | `GET /v1/hub/info`, `POST /v1/login` |
| Home | needs-you strip (open insight cards), attention (unread alerts, offline machines, failed agent jobs, services down, urgent tasks, releases waiting), agent activity, pinned sections with their headline numbers, space tiles, live values, automations | `/v1/sync`, `/v1/prefs`, `/v1/satellites`, `/v1/agents/jobs`, `/v1/jobs` |
| Space | every section filed under a space, its products, charts across them, money | sections' `space` / `space_title` / `space_icon`, catalog `spaces` |
| Section | the section's page, tabs, or sidebar of nodes, drawn block by block (a tabs or sidebar section's own `blocks` are a summary above every tab or node); pin to Home | `GET /v1/sections/{id}`, `POST /v1/sections/{id}/actions` |
| Agents | machines (status, OS, agent CLIs, browser, tracker), jobs (all / running / failed), Run an agent | `GET /v1/satellites`, `GET /v1/agents/jobs`, `POST /v1/satellites/{id}/agents/run` |
| Machine | facts, agents, its jobs, remove it | `GET /v1/satellites/{id}`, `/v1/satellites/{id}/agents/jobs`, `DELETE /v1/satellites/{id}` |
| Agent job | status, prompt, live output, Stop, Run again, its tracker item | `/v1/satellites/{id}/agents/jobs/{job}`, `…/log`, `…/cancel` |
| Desk | voice and text thread with the hub's desk model, tasks it started, or a "not configured on your hub" card with the fix | `/v1/desk…`, `/v1/speech/status`, `/v1/speech/tts`, `/v1/stt/models` |
| Tracker | Agent Satellite Tracker: live work and history, notes, screenshots and video, subagents, the evidence level | `/v1/tracker/items`, `/v1/tracker/items/{id}`, `/v1/tracker/media/…`, `/v1/tracker/settings` |
| Alerts | inbox (waiting, history, by section), test notification, scheduled reminders | `/v1/sync`, `POST /v1/alerts/ack` |
| Automations | feeds' schedules, last runs, logs, run now, pause | `/v1/jobs`, `/v1/jobs/{id}/run`, `/v1/jobs/run-all`, `/v1/jobs/{id}/log`, `/v1/jobs/{id}/settings` |
| Manage | create, rename, reorder and delete sections | `PUT` / `DELETE /v1/sections/{id}` |
| Settings | hub address, account, notifications, desk voice | `/v1/devices`, `/v1/logout` |

Everything updates live over `WS /v1/live` (sections, alerts, pulse, prefs, jobs, satellites and agent jobs, desk and tracker events), falling back to `/v1/sync` when the socket drops. Every request carries `Authorization: Bearer <token>` and `X-Orbital-Device: <device id>`.

### Block types

The app draws the hub's block types (`schema/orbital.section.v1.schema.json`): `heading`, `paragraph`, `bullets`, `callout`, `metrics`, `key_value`, `table`, `timeline`, `divider`, `chart`, `image`, `hero`, `buttons`, `product`, `ledger`, `tasks`, `series`, `insight_cards` and `videos`. It also draws `progress` (labelled bars), `status` (up / down rows) and `links`. A block type it doesn't know is skipped and the rest of the page still draws; once an update teaches the app that type, it fetches the section again in full.

Buttons take `{"type": "agent", "prompt"}` (opens the Desk with the prompt), `open` (a section, node or tab), `url` (an https link), `post` (sends `{"action", "value"}` back to the feed that owns the section, after `confirm` if set; the button shows pending until the feed answers), and the app's own `run_agent` (opens Run agent, filled in with `agent`, `machine`, `cwd`, `prompt`).

### Links

`orbital://home`, `orbital://open?section=<id>&node=<id>&tab=<id>`, `orbital://space?id=<space>`, `orbital://agents`, `orbital://agents?job=<job id>`, `orbital://agents?machine=<name>`, `orbital://run?machine=<name>&agent=claude&prompt=…&cwd=…`, `orbital://desk?prompt=…`, `orbital://tracker?id=<item>`, `orbital://alerts`, `orbital://automations`, `orbital://settings`, `orbital://connect?hub=<address>`.

## Layout

```
apps/orbital-app/
  generate_xcodeproj.py      writes OrbitalAgents.xcodeproj, the entitlements, and (with --icons) the app icons
  OrbitalAgents/
    App/                     app entry, router, Mac window
    Models/                  section and block decoding (lenient), spaces, satellites, jobs, alerts
    Services/                hub API client, store, cache, live socket, preview loader, images, keychain fallback
    Notifications/           local and remote notifications, actions, background refresh, test alert
    Views/                   Home, spaces, sections and blocks, Agents, alerts, automations, settings, sign-in
    Desk/                    the desk thread, voice settings, tracker screens
    Voice/                   speech to text, text to speech, audio routing, glasses and car
    Theme/                   colours, cards, components
    Assets.xcassets          AppIcon (from docs/assets), BrandMark, colours
  OrbitalAgentsTests/        decoding and behaviour tests over Fixtures/
  Fixtures/preview/          generic sample payloads (preview mode and tests)
  scripts/                   build, test, screenshots, deploy-iphone, deploy-mac
```

The app icon comes from `docs/assets/icon-1024.png` (iOS) and `docs/assets/icon-macos-1024.png` (every Mac size). After changing either, run `python3 generate_xcodeproj.py --icons`.
