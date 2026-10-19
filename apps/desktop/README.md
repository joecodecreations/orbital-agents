# Orbital Agents desktop app (Windows, Linux, macOS)

The Windows and Linux version of the Orbital Agents app. It shows every machine joined to your hub, with its health, CPU, memory, disks, browser controller and agents. You can open a machine, see what each agent job is running and read its log, start an agent, and stop a job. It also lists the hub's alerts.

It is a small Python program with a web-page UI in a native window ([pywebview](https://pywebview.flowrl.com/): Edge WebView2 on Windows, GTK or Qt WebKit on Linux, WebKit on macOS). There is no Node or Electron. All hub calls go through the Python side. The session token stays there and in the OS keyring, and the page never sees it.

On a Mac you'd normally use the Mac app in `apps/orbital-app` (Mac Catalyst). This app runs on macOS too, which is how it was developed and tested.

## Run it

You need Python 3.10 or newer.

### Windows 10/11

```powershell
cd apps\desktop
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

Or install it with a Start Menu shortcut and the `orbital://` link handler:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\install_windows.ps1
```

The window uses Edge WebView2, which ships with Windows 10/11. If the window stays blank, install the "WebView2 Runtime" from Microsoft.

### Linux

pywebview needs a GUI toolkit. Use your distro's GTK WebKit bindings (recommended):

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1        # Debian/Ubuntu; Fedora: python3-gobject webkit2gtk4.1
cd apps/desktop
python3 -m venv --system-site-packages .venv          # --system-site-packages lets the venv see python3-gi
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Or use Qt from pip instead of GTK: `.venv/bin/pip install "pywebview[qt]"`.

To install it with an app-menu entry, an icon and the `orbital://` link handler:

```bash
apps/desktop/packaging/install_linux.sh          # add --qt to use Qt, --no-url-handler to skip the link handler
```

### macOS

```bash
cd apps/desktop
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

### No WebView available?

`python run.py --browser` serves the same UI on `127.0.0.1` and opens it in your default browser. It binds to loopback only and uses a random per-run key. The hub token still stays in Python.

## Signing in

The app tries these in order, so you usually type nothing:

1. **A pairing link.** On the hub, run `orbital pair --link`. Then either paste the `orbital://connect?hub=…&pair=…` link into the sign-in screen, run `python run.py "orbital://connect?…"` (or `--pair-link LINK`), or click the link if the link handler is installed. The code works once and expires.
2. **A saved session.** After you sign in once, the session token is kept in the OS keyring: Windows Credential Manager, the Secret Service (GNOME Keyring / KWallet) on Linux, or the macOS Keychain. If there is no keyring, it goes in a `session.token` file with mode 0600.
3. **This computer's hub.** On the hub machine itself, if `~/.orbital-agents/secrets.env` is readable, the app creates a pairing code with `POST /v1/pair/new` on `127.0.0.1` and pairs with it. The automation token is used for that one request only. It is never shown, logged or stored.
4. **The form.** Enter the hub address and the app username and password (`POST /v1/login`). **Find hubs** lists this computer's hub plus any hubs that `orbital satellites discover` finds on your Tailscale network, if the `orbital` CLI is installed.

Use **Sign out** in the app, or `python run.py --sign-out`, to end the session.

Where things are kept:

| OS | Folder |
|---|---|
| Windows | `%APPDATA%\OrbitalAgents` |
| macOS | `~/Library/Application Support/OrbitalAgentsDesktop` |
| Linux | `~/.config/orbital-agents-desktop` |

The folder holds `settings.json` (hub address, username, hub id; no secrets), `desktop.log`, and `session.token` only when there's no keyring. Set `ORBITAL_DESKTOP_HOME` to use another folder. Set `ORBITAL_DESKTOP_NO_KEYRING=1` to skip the keyring.

## What it shows

The same structure as the iPhone and Mac app. The left bar has five fixed sections, then whatever the hub's navigation adds, then the sidebar tree.

- **Home:** machines online, agent jobs running, tracker items active, pending alerts and failing feeds. Below that, *Needs you* (offline machines, failing feeds, alerts), *Agents at work* (running jobs and active tracker items), a tile per space, and the machines.
- **System:** the hub strip on top (name, address, machines online, live state), then four panes:
  - **Satellites** (`GET /v1/satellites`): one card per machine, with its online state, heartbeat, CPU, memory and disks, uptime, version, agents, running jobs and browser controller state. Sections filed in the `system` space (machine health pages and the like) are listed here as *System pages*, not as a space.
  - **Automations** (`GET /v1/jobs`): every feed with its schedule, a countdown to the next run, and what the last run did. **Run** runs one feed; **Run all** runs a group or everything.
  - **Manage:** the navigation in use (default or the hub's own, with *Reset to default*), the spaces, and every section with its space, layout, order, revision and last update.
  - **Settings:** the connection, the hub (name, machine, version, hub id, Agent model, screenshot evidence level), this app, and sign out.
- **A machine:** health details, load averages, memory, a bar for every disk, the browser controller (bridge, extension and version, last poll, bind, whether the hub can drive it, and how to fix it), its jobs, **Ping**, and **Run an agent** (`POST /v1/satellites/{id}/agents/run`).
- **Agent:** *Desk* talks to the hub's agent (`GET /v1/desk`, `POST /v1/desk/say`; replies also arrive live). *Agent jobs* lists every machine's jobs (`GET /v1/agents/jobs`), all or running. A job shows its prompt, folder, status, exit code, elapsed time and log (refreshed every 2.5 s while it runs); **Stop** asks for a second click.
- **Tracker** (`GET /v1/tracker/items`): what every agent is working on, active or finished, with search. An item shows its details, summary, notes and pictures.
- **Alerts** (`GET /v1/alerts`): pending or all. Clear one, or clear them all.

**Navigation** comes from `GET /v1/nav` (schema `orbital.nav.v1`, see `orbital/hub/nav.py`). Its `tabs` show after the five fixed sections. Its `sidebar` replaces the default tree, which is each space (an *Overview* and its sections) followed by sections in no space. Groups nest and open or close (the app remembers which). Targets open a section (and a page and tab in it), a space, a built-in view, or a link (`https://` opens in your browser; `orbital://section|space|view/…` opens inside the app). A `{"type": "nav"}` message on the live socket redraws the bar at once.

**Spaces** come from `GET /v1/catalog`: the hub's declared spaces with their aliases, then spaces named by sections. A space page has:
- a switcher in the header to move to another space. It keeps the area you picked, and resets the platform if the other space doesn't have it.
- area chips: *Overview*, then *Site traffic*, *Social media*, *Players*, *Usage* and *Search* (from each `series` block's `kind`), *Money* (ledgers), *Videos* (videos blocks) and *Pages*.
- an *All / iOS / Android / Roblox* filter, shown when the space's money comes from more than one platform. The platform is read off ledger titles and sources (App Store, Google Play, Robux).

Sections draw their blocks: headings, text, callouts, lists, metrics, key/value, tables, charts, series, ledgers, products, to-dos, timelines, hero, images, buttons, videos, the docket, insight cards and marketing. Hub images (`/v1/media/…`) are fetched by the Python side, so the page never holds the token. `#/space/<id>/<area>/<platform>` opens a space on an area, for example `--route space/home-lab/site`.

Everything updates live over the `/v1/live` websocket (machines, jobs, alerts, sections, catalog, navigation, desk) and is also refreshed every 10 s.

## Command line

```
python run.py [LINK] [--pair-link LINK] [--hub URL] [--no-auto] [--browser [--port N] [--no-open]]
              [--route home | system/<pane> | machine/<id> | job/<machine>/<job> | agent[/jobs] | tracker
                       | alerts | space/<id>[/<area>[/<platform>]] | section/<id>[/<node>[/<tab>]]]
              [--size 1280x840]
              [--position X,Y] [--sign-out] [--debug]
```

`--debug` turns on the WebView dev tools and debug logging. A negative `--position` needs the `=` form: `--position=-1600,-1500`. `ORBITAL_DESKTOP_NOFOCUS=1` opens the window without taking focus, which helps for screenshots and tests.

## Build a standalone app (optional)

Build on the OS you're targeting, because PyInstaller doesn't cross-compile:

```bash
cd apps/desktop
pip install -r requirements.txt pyinstaller
pyinstaller packaging/orbital_desktop.spec --noconfirm
```

The output goes to `dist/OrbitalAgents/`: `OrbitalAgents.exe` on Windows, `OrbitalAgents` on Linux, `OrbitalAgents.app` on macOS. Put `packaging/icon.ico` or `icon.icns` there first if you want an icon. On macOS, a new build is a different program to the Keychain, so the first launch asks whether it may read the saved session. Click *Always Allow*. If nobody answers within 60 s, the app carries on without the saved session.

## Files

| Path | What |
|---|---|
| `run.py` | launcher (`python -m orbital_desktop` works too) |
| `orbital_desktop/app.py` | command line, the window, and the `--browser` server |
| `orbital_desktop/api.py` | the calls the UI makes, and the live websocket thread |
| `orbital_desktop/hub.py` | hub HTTP, pairing, login, local auto-pair, discovery |
| `orbital_desktop/storage.py` | settings, plus the session token in the keyring or a 0600 file |
| `orbital_desktop/ui/model.js` | spaces, areas, platforms and navigation as plain functions (same rules as the iPhone app) |
| `orbital_desktop/ui/blocks.js` | icons, section blocks and charts |
| `orbital_desktop/ui/app.js` | the screens, routes and live updates |
| `tests/` | `.venv/bin/pytest apps/desktop/tests -q` (the model runs under node when it is installed) |
| `orbital_desktop/ui/` | `index.html` and `app.css` (single page, hash routes) |
| `packaging/` | PyInstaller spec, Windows and Linux installers, `.desktop` file, icon |
