# Troubleshooting

Start with these three; they find most problems and say how to fix them:

```bash
orbital status                  # what is up and down on this machine, and the last warnings
orbital doctor                  # every check, with a fix for each problem (orbital doctor --fix applies the safe ones)
orbital test --live             # end-to-end checks with a PASS/FAIL table
```

Then read the logs: `orbital logs --level WARNING --since 1h` ([LOGGING.md](LOGGING.md)).

## Tailscale is down or not set up

**Symptoms:** setup says "Tailscale isn't running", satellites show offline, the phone cannot reach the hub, `orbital satellite find` finds nothing.

```bash
tailscale status                # are you signed in, and is the other machine listed as online?
tailscale ip -4                 # this machine's Tailscale address (100.x.y.z)
```

- macOS: open the Tailscale app from the menu bar and sign in. Linux: `sudo tailscale up`.
- Every machine and your phone must be signed in to the **same tailnet**.
- After Tailscale comes back, the satellite service rebinds to its Tailscale address by itself within about 30 seconds. To hurry it: `orbital services restart satellite`.

## The phone (or another machine) cannot reach the hub

1. On the hub: `orbital status` must say `Hub … reachable`. If not, see [a service is not starting](#a-service-is-not-starting).
2. On the phone: the Tailscale app must be connected (the VPN icon shows in the status bar).
3. From another machine on the tailnet: `curl http://<hub-name>.<your-tailnet>.ts.net:7700/v1/hub/info` should answer `{"service": "orbital-hub", …}`.
4. In the app, the hub address must be the Tailscale name or `100.x` address with port `7700`, not `127.0.0.1` or a LAN address.
5. A `403 this network may not use the hub` means the request came from outside `hub.allow_networks` (for example your Wi-Fi address instead of Tailscale). Connect over Tailscale. Only if you really must, add your LAN range to `hub.allow_networks` in `config.toml`, then `orbital config --apply && orbital services restart hub`.
6. Tailscale ACLs: if you use them, allow port 7700 to the hub from your phone and machines.

## Port already in use

**Symptoms:** setup asks for another port; a service exits right after start with "address already in use"; `orbital doctor` reports a port conflict.

```bash
lsof -nP -iTCP:7700 -sTCP:LISTEN          # who holds the port (7700 hub, 7701 tracker, 7702 speech, 7703 bridge, 7704 satellite)
```

Either stop that program, or move ours: set the port in `config.toml` (`hub.port`, `tracker.port`, `tracker.stt_port`, `browser.bridge_port`, `satellite.port`), then

```bash
orbital config --apply
orbital services install        # rewrites the services with the new port and reloads them
```

If you move `hub.port`, update `hub.url` on every satellite too. If you move `browser.bridge_port`, set the same port in the extension (popup → **Set port**). Re-running `./setup` with `ports.on_conflict = "next-free"` picks free ports for you.

## A service is not starting

```bash
orbital services status                     # wanted / loaded / state / pid / health for each
orbital logs hub --stderr -n 100            # the service's own stdout/stderr: Python tracebacks land here
orbital logs hub -n 50                      # its JSON log
orbital logs watchdog --since 1h            # restarts the watchdog tried
```

Common causes:

| Seen in the stderr log | Fix |
|---|---|
| `ModuleNotFoundError` | the repo's `.venv` is missing or stale: run `./setup` again (it reinstalls when needed) |
| `address already in use` | [port already in use](#port-already-in-use) |
| `No such file or directory` for `.venv/bin/python` | the repo moved after setup: `orbital services install` rewrites the paths (`status` shows `(outdated)`) |
| `jobs.json not loaded` (scheduler) | fix the JSON; `orbital jobs` shows the error |
| nothing at all, and `loaded: no` | `orbital services install`, or `orbital doctor --fix` |

On macOS you can also look at launchd directly: `launchctl print gui/$(id -u)/io.orbitalagents.hub | head -40`. On Linux: `systemctl --user status orbital-hub` and `journalctl --user -u orbital-hub -n 50`. Linux user services stop at logout unless you ran `loginctl enable-linger $USER`.

## The browser extension is not connected

**Symptoms:** `orbital browser status` says `extension NOT connected`, commands answer 503, the popup says **not running**.

1. Chrome must be open, with the extension loaded: `chrome://extensions` → **Orbital Agents Browser Controller** is on. If it is missing: **Developer mode** on → **Load unpacked** → the repo's `browser-extension/` folder.
2. The popup must say **connected**. If it says **not running**, the bridge is down: `orbital services status browser-bridge`, or run `orbital browser serve` in the foreground to see errors.
3. The ports must match: the extension's port (popup → **Set port**, or Options) must equal `browser.bridge_port` (7703).
4. The extension may be paused: Options → **Take commands from the bridge** on.
5. Two Chrome profiles on one bridge take each other's commands; give the second profile its own bridge and port ([BROWSER_CONTROLLER.md](BROWSER_CONTROLLER.md)).
6. After editing extension files: reload it on `chrome://extensions` or `orbital browser command self_reload`.

From another machine: `401` means that machine's `ORBITAL_BROWSER_TOKEN` is stale; `403 not allowed from this address` means the browser machine uses `browser.bind = "loopback"`. Through the hub, a stale token shows as `502 the machine refused the hub's token`. You never copy the token: the hub hands out the fleet browser token at join; rejoin (`orbital satellite join`) to refresh it; the bridge restarts automatically after a join that changed it. So run `orbital satellite join <hub_url> <join_token>` on the machine with the stale token (the join token: `orbital satellites invite` on the hub).

## An agent CLI is "not found" under the services

**Symptoms:** `orbital satellites` does not list an agent you installed; `agent X isn't available on <machine>`; `orbital doctor` warns about the agent PATH. It works in your terminal because your shell's PATH differs from the one services get.

```bash
orbital satellite status        # each agent: installed, or "not found", and the path it found
which claude                    # where your shell finds it
```

Fixes, easiest first:

1. Re-run `orbital services install`: the services' PATH includes the folders where agent CLIs were found at install time.
2. Give the full path in `config.toml`:

   ```toml
   [agents.claude]
   command = ["/Users/you/.local/bin/claude", "-p", "{prompt}"]
   ```

   then `orbital services restart satellite`.
3. Make sure the CLI is installed and signed in **for the same user** that runs the services.

The satellite also looks in Homebrew, `/usr/local/bin`, `~/.local/bin`, `~/.npm-global/bin`, nvm's node folders, bun and cargo. CLIs installed by a version manager that changes PATH only in interactive shells are the usual culprits.

## iPhone build and signing errors

Messages from the app's own scripts (`apps/orbital-app/scripts/`, `generate_xcodeproj.py`):

| Message | Fix |
|---|---|
| `No Apple Team ID is set, so the app can't be signed for a device.` (deploy-iphone.sh) | set `apple.team_id` with `orbital setup`, or run with `ORBITAL_TEAM_ID=<your team id> ORBITAL_BUNDLE_PREFIX=com.yourname`. For a simulator build no team is needed: `scripts/build.sh` |
| `Team ID must be 10 letters and digits (Xcode → Settings → Accounts), got …` | copy it from Xcode → Settings → Accounts → your team, or developer.apple.com → Membership, into `apple.team_id` |
| `bundle prefix must look like com.yourname, got …` | `apple.bundle_prefix` is letters, digits and hyphens separated by dots, with no trailing dot |
| `no iPhone or iPad is paired with this Mac. Connect it by cable once and trust this Mac.` | connect the phone by cable, unlock it, tap **Trust This Computer**; after that the same network works. `xcrun devicectl list devices` shows what the Mac sees (Xcode 15 or newer) |
| `no paired device matches '…' (xcrun devicectl list devices)` | `--device` and `apple.ios_device` match part of the device's name or its identifier; check the spelling against `xcrun devicectl list devices` |
| `note: push (notifications.apns) needs an Apple Team ID; leaving the push entitlement out` | push needs a paid account's Team ID; set `apple.team_id`, or leave push off (alerts still arrive by the live connection and background refresh) |
| `note: no Apple Team ID set; signing to run on this Mac only` (deploy-mac.sh) | fine for your own Mac; set a Team ID for push or to run the app on other Macs |
| `/Applications/<name>.app is a different app (…), so it was left alone.` (deploy-mac.sh) | another app already has that name: set `apple.app_name` to something else, or install elsewhere with `ORBITAL_MAC_APP_DIR` |
| `build did not produce …/OrbitalAgents.app` | the xcodebuild step above it failed; the first `error:` line in its output says why (often one of the Xcode messages below) |

Messages from Xcode and the device:

| Message | Fix |
|---|---|
| `No Accounts` / `No signing certificate "Apple Development" found` | Xcode → Settings → Accounts: add your Apple Account; then Manage Certificates → **+** → Apple Development |
| `Failed to register bundle identifier` / `is not available` | another team already uses that bundle id: choose a unique `apple.bundle_prefix` (for example `com.yourname`) and run the script again |
| `Device is not registered` / `Unable to install` | connect the iPhone by cable, unlock it, tap **Trust This Computer**; Xcode registers it on the first signed build (`-allowProvisioningUpdates`) |
| Developer Mode is off | on the iPhone: Settings → Privacy & Security → Developer Mode → on, then restart it |
| `Untrusted Developer` when opening the app | free Apple accounts: Settings → General → VPN & Device Management → your developer certificate → **Trust** |
| the app stops opening after 7 days | free Apple accounts sign apps for 7 days; run `deploy-iphone.sh` again, or use a paid developer account (a year) |
| `Provisioning profile … doesn't include the aps-environment entitlement` | push is on but the App ID lacks Push Notifications: build once from Xcode with automatic signing, or turn `notifications.apns` off ([NOTIFICATIONS.md](NOTIFICATIONS.md)) |

The setup wizard's build logs are in `~/.orbital-agents/logs/setup-iphone app.log` and `setup-mac app.log`. More in [APP.md](APP.md#build-and-install).

## A satellite shows offline

A satellite is offline when the hub has had no heartbeat from it for three periods (about 3 minutes).

On that machine:

```bash
orbital satellite status        # is the service up? when was the last heartbeat, and what did the hub say?
orbital logs satellite -n 50
tailscale status                # is it online on the tailnet?
```

| Seen | Fix |
|---|---|
| `Satellite … DOWN` | `orbital services restart satellite` (or `orbital services install`) |
| last heartbeat `unknown satellite: join again` / 401 | the hub no longer knows it (it was removed, or the hub's data was reset): `orbital satellite join <hub_url> <token>` (the token: `orbital satellites invite` on the hub) |
| `Hub not configured (hub.url is empty)` | join it: `orbital satellite join …`, or re-run `./setup` |
| `could not reach the hub` | Tailscale is down on one side, or the hub is down |
| the machine was asleep | it comes back online at its next heartbeat after waking |

## Other problems

| Symptom | Fix |
|---|---|
| `orbital: command not found` | `~/.local/bin` is not on your PATH: add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc` or `~/.bashrc`, open a new terminal. Or use `<repo>/.venv/bin/orbital` |
| `./setup` says Python 3.11+ is missing | macOS: `brew install python@3.13` (setup offers it); Debian/Ubuntu: `sudo apt install python3 python3-venv python3-pip`; or set `ORBITAL_PYTHON=/path/to/python3.11+` |
| The app's sign-in fails | the username and password are `ORBITAL_HUB_USERNAME` and `ORBITAL_HUB_PASSWORD` in the hub's `secrets.env`; `orbital logs hub` shows `sign-in refused` |
| The app shows no pages | no feed has pushed yet: `orbital jobs`; try `orbital jobs run hello --wait` |
| A feed shows `error` | `orbital jobs run <id> --wait` prints the issues; full output in `~/.orbital-agents/data/scheduler/out/<id>.log` ([FEEDS.md](FEEDS.md)) |
| `orbital push` says `unauthorized` | the automation token on this machine differs from the hub's, or is missing (`ORBITAL_AUTOMATION_TOKEN` in `secrets.env`) |
| Alerts arrive only when the app is opened | direct Apple push is off; see [NOTIFICATIONS.md](NOTIFICATIONS.md) |
| The tracker panel is not showing | `orbital services status tracker`; toggle it with ctrl+option+T (or the modifiers in `tracker.hotkey_modifiers`); `orbital-track ping` |
| Tracker shortcuts do nothing | another app owns them: set `tracker.hotkey_modifiers = "ctrl+option+shift"`, `orbital config --apply`, restart the tracker |
| `orbital-track` not found | `apps/tracker/scripts/install.sh` links it into `~/.local/bin` (setup does this when it builds the tracker) |
| The disk is filling up | `orbital logs --list`, `du -sh ~/.orbital-agents/*`; lower the retention settings ([CONFIG.md](CONFIG.md)); the watchdog's janitor trims on every pass |
| Everything is weird after moving the repo | `./setup` again (it keeps your settings), then `orbital services install` |

Still stuck? Open an issue with the output of `orbital doctor --json` and `orbital logs --level WARNING --since 1h` (check that nothing private is in it first).
