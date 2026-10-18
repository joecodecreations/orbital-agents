# Notifications

An alert that a feed, the watchdog or the hub raises ([DATA_PIPELINE.md](DATA_PIPELINE.md#alerts-and-notification-levels)) reaches your iPhone and Mac in up to four ways. The first three need nothing beyond the app; the fourth is optional.

| Path | When it works | How |
|---|---|---|
| **Live socket** | the app is open (or was just in use) | the app keeps `WS /v1/live` open; the hub pushes every new alert the moment it is stored |
| **Sync** | the app comes to the front, or is opened from a notification | `GET /v1/sync` returns every pending alert, plus the ones cleared on other devices |
| **Background refresh** | the app is closed, when iOS decides to wake it (roughly every 15 minutes at best, less often on battery) | the app syncs in the background and shows new alerts as local notifications |
| **Direct Apple push** (optional) | the app is closed, within seconds | the hub sends each new alert to Apple's push service (APNs), which delivers it to the device |

Whichever path delivers an alert, the app turns it into a local notification with the alert's title, body, interruption level, thread and deep link, and remembers its id so it is never shown twice. Reminders (`deliver_at`, `repeats`) are scheduled on the device, so they fire on time even when the hub is asleep. Clearing an alert on one device (`POST /v1/alerts/ack`) removes it from the others at their next live message or sync.

Without direct push a closed app learns about an alert only when iOS runs background refresh, which can be minutes or hours later. Turn on direct Apple push when you want alerts promptly with the app closed.

## Direct Apple push

The hub talks to Apple directly, with your own key. No Firebase or other relay is involved, and nothing leaves your hub except the notification itself (title, body, ids for the deep link) going to Apple.

You need: a paid Apple Developer Program membership (push needs it; a free account can still build and run the app without push), the app built with your Team ID and bundle id, and the hub machine.

### 1. Create an APNs key

1. Sign in at [developer.apple.com](https://developer.apple.com/account) → **Certificates, Identifiers & Profiles** → **Keys** → **+**.
2. Name it (for example "Orbital Agents push"), tick **Apple Push Notifications service (APNs)**, then **Continue** and **Register**.
3. **Download** the `.p8` file. Apple lets you download it once; keep a copy somewhere safe.
4. Note the **Key ID** (10 characters, shown with the key) and your **Team ID** (top right of the developer site, or Xcode → Settings → Accounts).

One key works for all your apps and both environments.

### 2. Put the key on the hub

```bash
cp ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.orbital-agents/apns.p8
chmod 600 ~/.orbital-agents/apns.p8
```

Add the two ids to `~/.orbital-agents/secrets.env`:

```
APNS_KEY_ID=<the 10-character Key ID>
APNS_TEAM_ID=<your 10-character Team ID>
```

The `.p8` file is a private key. It is git-ignored (`*.p8`); never commit or share it.

### 3. Install the push extras

The hub needs `cryptography` (to sign Apple's provider token) and HTTP/2 support:

```bash
cd <repo> && .venv/bin/pip install -e '.[push]'
```

### 4. Turn it on

In `~/.orbital-agents/config.toml`:

```toml
[notifications]
apns = true
apns_environment = "development"     # Xcode and devicectl installs; "production" for TestFlight / App Store builds
```

Then:

```bash
orbital config --apply
orbital services restart hub
```

The environment must match how the app was installed: a build you install from Xcode or with `devicectl` uses Apple's sandbox (`development`); a TestFlight or App Store build uses `production`. A mismatch shows up as `BadDeviceToken` in the hub log.

### 5. Rebuild the app with the push entitlement

The app asks iOS for a push token once the hub reports push as enabled, and iOS only hands one to a build that carries the `aps-environment` entitlement. The project generator adds that entitlement only when push is on (so a free account can still build the app). After step 4, regenerate and reinstall:

```bash
python3 apps/orbital-app/generate_xcodeproj.py --print    # shows apns: True and the environment
python3 apps/orbital-app/generate_xcodeproj.py            # writes the project with the entitlement
```

then build and install it again. The deploy scripts regenerate the project before building:

```bash
apps/orbital-app/scripts/deploy-iphone.sh      # the iPhone app, on the connected device
apps/orbital-app/scripts/deploy-mac.sh         # the Mac app, into /Applications
```

(Re-running `./setup` with `apple.build_iphone_app = true` and `apple.build_mac_app = true` in an answers file does the same.) The generator reads `notifications.apns` and `notifications.apns_environment` from `config.resolved.json`; `--apns` / `--no-apns` and `--apns-environment` override them. See [APP.md](APP.md).

Your App ID needs the Push Notifications capability. The deploy scripts build with `-allowProvisioningUpdates`, so Xcode's automatic signing adds it the first time you build with the entitlement.

### 6. Check it

Open the app once. It sees that the hub has push on, gets a push token from iOS and registers it with the hub (`POST /v1/devices`). Then:

```bash
curl -s http://127.0.0.1:7700/v1/health        # "apns": {"enabled": true, "configured": true, "environment": "development"}
orbital push --alert "Push test" --body "If this arrives with the app closed, push works." --level info
```

Close the app (swipe it away) before sending the test. `orbital logs hub --since 10m` shows what Apple answered if it does not arrive.

## How the hub sends

- **Topic** is `<apple.bundle_prefix>.orbitalagents`, the app's bundle id.
- **Every new alert** goes to every device that registered a push token, except reminders the app schedules itself (`deliver_at` more than 30 seconds in the future, or `repeats`), and alerts already acked or retracted. A duplicate alert id is not new, so it is not sent again.
- **Levels** carry over: `passive` alerts go out quietly (no sound, low priority); others play the default sound. The payload has `interruption-level`, `thread-id` (the section), `relevance-score` and `category` when the alert sets them, and an `orbital` object with the alert id, section, node, tab and launch URL so a tap opens the right page.
- **Size**: the body is cut to 1,500 characters so the payload stays under Apple's 4 KB.
- **Provider token**: an ES256 JWT signed with `apns.p8`, reused for 50 minutes.
- **Sending runs in the background** and never slows down the request that raised the alert. A network error is retried once.

| Apple answers | The hub |
|---|---|
| 200 | sent |
| 410, or 400 `BadDeviceToken` / `Unregistered` | removes that device token (the app registers a new one next time it starts) |
| 403 (bad key, key id or team id) | stops sending for 15 minutes and reports the error in `GET /v1/health` → `apns` and in the log |
| anything else | logs `apns rejected a push` with the status and reason |

When anything is missing (`notifications.apns` off, no `apns.p8`, no `APNS_KEY_ID` or `APNS_TEAM_ID`, or the extras not installed) the hub logs one INFO line saying exactly what is missing, and alerts keep reaching apps through the other three paths. Keys, provider tokens and device tokens are never logged (device tokens appear masked).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `GET /v1/health` shows `"configured": false` | `orbital logs hub` names what is missing: the key file, an id in `secrets.env`, or `pip install -e '.[push]'` |
| `"enabled": false` but `"configured": true` | `notifications.apns = true` is not set, or the hub was not restarted after `orbital config --apply` |
| `apns refused the provider token` | wrong `APNS_KEY_ID` or `APNS_TEAM_ID`, or a revoked key; fix it and restart the hub (it also retries after 15 minutes) |
| `BadDeviceToken` | `apns_environment` does not match the build (Xcode builds: `development`; TestFlight: `production`) |
| Nothing arrives, no errors | the app has no push token: rebuild with the entitlement (step 5), open the app once, allow notifications in iOS Settings |
| Alerts arrive only when the app opens | direct push is off; background refresh alone is slow by design |
| Notifications are silent | the alert level is `passive`, or a Focus mode is on (use `warning` for time-sensitive) |
