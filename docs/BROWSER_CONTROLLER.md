# Orbital Agents Browser Controller

A Chrome extension plus a small bridge, so AI agents (and you, from a terminal or Python) can drive real Chrome tabs: on this machine, or on another of your machines over Tailscale. Agents get their own background windows and never take keyboard focus from you.

```
agent / CLI / Python ──HTTP──▶ bridge :7703 (orbital/browser) ◀──long poll── extension (browser-extension/)
another machine ──Tailscale + browser token──▶ bridge
hub ──relay /v1/satellites/<id>/browser/<command>──▶ bridge on a satellite
```

The extension only ever talks to the bridge on its own machine (`http://127.0.0.1:<port>`). Everything from elsewhere goes through that bridge, which checks who is calling.

What it does: list, open and switch tabs; navigate; click by CSS selector, visible text, accessible name or coordinates (plain or as real mouse input); type instantly or key by key; read text, HTML, links and the accessibility tree; run JavaScript (falling back to the DevTools protocol on strict pages); fetch with the page's cookies; fill forms inside iframes; upload files through the page's real file chooser; wait for text, an element or a URL; take screenshots without bringing the window forward; capture network traffic into a catalog of API endpoints; copy files from the machine's Downloads folder.

The full reference (every action and its parameters, the bridge HTTP API) is [`orbital/browser/README.md`](../orbital/browser/README.md).

## Install

The setup wizard installs the bridge as a service (`io.orbitalagents.browser-bridge`) when you answer yes to "Browser Controller", and opens `chrome://extensions` and the extension folder for you. Chrome does not allow loading an extension automatically, so one step is yours:

1. Open `chrome://extensions` in Chrome (116 or newer).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose the repository's `browser-extension/` folder. There is no build step; the folder is the extension.
4. Pin **Orbital Agents Browser Controller** from the puzzle-piece menu if you like. Its popup shows **connected** once it reaches the bridge.
5. Check from a terminal:

   ```bash
   orbital browser status          # bridge up, extension connected
   ```

Without the wizard:

```bash
orbital browser serve                     # the bridge in the foreground (Ctrl-C stops it)
orbital services install browser-bridge   # or as a login service
```

The extension needs these Chrome permissions: tabs, scripting, storage, webNavigation, webRequest, debugger (for real mouse and key input, screenshots and strict pages), activeTab, sidePanel, alarms, system.display (to put agent windows on a second display), and access to all sites (so it can act on the pages agents open).

## Port

The bridge listens on `browser.bridge_port` (default 7703) from `~/.orbital-agents/config.toml`. If you change it, set the same number in the extension: popup → **Set port**, or the **Options** page. Options also has **Test connection**, a switch to pause taking commands, the display used for agent windows, and the network-capture switch.

A second Chrome profile on the same machine needs its own bridge: `orbital --home ~/.orbital-agents-2 browser serve --port 7713`, and set that profile's extension to 7713. Two profiles polling one bridge take each other's commands; `orbital browser status` warns when it sees that.

## Use it here

```bash
orbital browser navigate https://example.com
orbital browser text
orbital browser click-text "More information"
orbital browser screenshot --out shot.png --full-page
```

| Command | Does |
|---|---|
| `status` | bridge and extension state |
| `nodes` | this machine and every joined machine with a browser |
| `tabs` | open tabs |
| `navigate URL [--new-tab] [--no-wait] [--tab-id N]` | open a URL in the session's tab |
| `click SELECTOR [--index N] [--trusted]` | click a CSS selector match (`--trusted`: a real mouse click) |
| `click-text TEXT [--exact]` | click the visible button or link with that text |
| `type SELECTOR TEXT [--clear] [--human]` | fill a field (`--human`: key by key with natural pauses) |
| `press KEY` | `Enter`, `Escape`, `Meta+Enter`, `ctrl+shift+k` … |
| `eval JS [--world MAIN\|ISOLATED\|CDP]` | evaluate JavaScript and print the value |
| `text [SELECTOR] [--max-len N]`, `dom [--selector S] [--out FILE]`, `links` | read the page |
| `screenshot [--out FILE] [--full-page]` | PNG of the session's tab (default `screenshot.png`) |
| `wait-for [TEXT] [--selector S] [--url U] [--wait SECONDS] [--gone]` | wait for text, an element or a URL |
| `upload SELECTOR FILE [--label TEXT] [--click SELECTOR] [--stage]` | put a file in a file input; `--label` clicks the page's own upload button and answers the real file chooser |
| `stage FILE` | copy a file to the node's staged folder and print its path there |
| `files [PATH]`, `pull REMOTE [--out LOCAL]` | list and copy files from the node's `~/Downloads` or staged folder |
| `capture on\|off`, `network [--host H] [--path P] [--limit N] [--api-only]`, `endpoints [--host H]`, `export`, `clear` | network capture |
| `window [status\|open\|focus\|release\|close\|on\|off\|adopt]` | the session's agent window |
| `command ACTION '{"param": 1}'` | any extension action by name |
| `serve [--port N] [--bind loopback\|tailscale]` | run the bridge here |

Global options (before or after the subcommand): `--node <machine>`, `--via auto|direct|hub`, `--session NAME`, `--timeout SECONDS` (default 60), `--json`. Results print as JSON. Exit codes: 0 ok, 1 the command failed, 2 usage, 3 the bridge could not be reached.

**Sessions.** `--session NAME` gives an agent its own Chrome window and tab, so several agents can work at once without stepping on each other. Commands for one session run in order; different sessions run side by side. The default session is `main`. Agent windows open in the background, are tiled on a second display when you have one, and never take keyboard focus; `window focus` hands one to you.

## Use it from another machine

Two ways, both over Tailscale.

**Direct.** On the machine with Chrome, set `browser.bind = "tailscale"` in `config.toml` (the wizard asks "Let your other machines drive this Chrome over Tailscale"), then `orbital config --apply && orbital services restart browser-bridge`. The bridge then also listens on that machine's Tailscale address and answers callers from the Tailscale ranges that send `Authorization: Bearer <ORBITAL_BROWSER_TOKEN>`. The calling machine needs the same `ORBITAL_BROWSER_TOKEN` in its `secrets.env`, which every joined machine already has (see below). With no token set on the browser machine, remote calls are refused.

**Through the hub.** Satellites report `capabilities.browser` and their bridge port when they join. `POST /v1/satellites/<id>/browser/<command>` on the hub forwards the command to that machine's bridge with the browser token, and returns the bridge's answer. The browser machine must run with `bind = "tailscale"` (the wizard suggests it on satellites).

**One browser token for the fleet.** You do not copy tokens by hand. When a machine joins (`./setup` or `orbital satellite join`), the hub's answer includes its `ORBITAL_BROWSER_TOKEN`; the machine stores it in its `secrets.env`, and a rejoin refreshes it. The bridge reads its token when it starts, so when a join changes the token, `orbital satellite join` and the setup wizard restart the bridge for you (if no service manager is available, they say so: restart it yourself with `orbital services restart browser-bridge`). Every joined machine can therefore reach every bridge that binds to Tailscale, directly or through the hub. How to rotate the token: [SECURITY.md](../SECURITY.md#rotating-tokens).

Then:

```bash
orbital browser nodes                                     # NODE, KIND, HUB, BRIDGE, EXTENSION, URL for each machine
orbital browser --node studio-mac navigate https://example.com
orbital browser --node studio-mac --via hub tabs          # force the hub relay
orbital browser --node studio-mac --via direct tabs       # force the direct path
```

`--via auto` (the default) goes direct when `ORBITAL_BROWSER_TOKEN` is set here and falls back to the hub; the local node is always direct. A file you upload to a remote node is first copied to it (`stage`), because the page's file input needs a path on that machine. Large files are faster direct than through the hub.

From Python through the hub: `HubClient.from_config().browser("studio-mac", "navigate", url="https://example.com")`.

## Python API

```python
from orbital.browser import BrowserClient, BrowserError

b = BrowserClient()                                  # node="local", session "main"
b.navigate("https://example.com")
b.type("input[name=q]", "orbital agents", human=True)
b.press("Enter")
b.wait_for("results")
print(b.eval("document.title"))
b.screenshot("results.png")

remote = BrowserClient(node="studio-mac", session="research", timeout=90)
remote.upload("input[type=file]", "report.pdf")       # staged on that machine first
remote.call("ax_press", name="Submit")                 # any action by name
```

`BrowserClient(node="local", *, session=None, timeout=60, via="auto", token=None, url=None, home=None)`

| Method | Does |
|---|---|
| `status()`, `health()` | bridge and extension state |
| `tabs()` | open tabs |
| `navigate(url, new_tab=False, tab_id=None, session=None, wait=True)` | open a URL |
| `reload(**params)`, `close_tab(tab_id=None, force=False)` | reload or close a tab |
| `click(selector, index=0, trusted=False)`, `click_text(text, exact=False)` | click |
| `type(selector, text, clear=False, human=False)`, `press(key)` | keyboard |
| `eval(js, world=None)` | the value of an expression |
| `text(selector=None)`, `dom(selector=None)`, `links()` | read the page |
| `screenshot(out=None, full_page=False)` | PNG bytes (and a file when `out` is given) |
| `wait_for(text=None, selector=None, url=None, …)` | wait |
| `upload(selector, file_or_files, stage=None, …)`, `stage(file)` | files into the page |
| `files(path=None)`, `pull(remote_path, dest)` | files on the node |
| `capture(on)`, `network(host=None, path=None, limit=200, api_only=False)`, `endpoints(host=None)`, `events()`, `export()`, `clear()` | network capture |
| `window(op)` | the session's agent window |
| `call(action, timeout=None, **params)` | any action |

Failures raise `BrowserError`; `.status` is the HTTP status when there was one.

## Security

Anyone who can call a bridge can act in your logged-in tabs. The defaults keep that to you:

- **Who can connect.** `bind = "loopback"` (the default): only processes on this machine. `bind = "tailscale"`: also Tailscale addresses (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`), each with the browser token. Everything else gets 403 before any work is done. Never expose the port with Tailscale Funnel or a public address.
- **Token.** Callers from another machine must send `Authorization: Bearer $ORBITAL_BROWSER_TOKEN` (compared in constant time). Processes on the same machine need none, since the extension and local agents run there.
- **Web pages cannot use it.** The bridge sends no CORS headers, refuses requests with a web `Origin` header, refuses preflights, and refuses loopback requests whose `Host` is not a loopback name (DNS rebinding). The extension has no `externally_connectable`, so pages cannot message it, and it accepts control messages only from its own popup, options page and side panel.
- **Network capture** is off by default; while off, nothing is injected into the pages you browse. When on, credential values (Authorization, Cookie and other token, session, key or password headers, matching query parameters, form fields and JSON keys) are replaced with `[redacted]` before anything is stored. `browser.capture_secrets = true` turns redaction off; avoid it.
- **Files.** Staged uploads live in `~/.orbital-agents/data/browser/staged/` and are deleted after `browser.staged_max_age_hours` (24) or when the folder passes `browser.staged_max_mb` (2000). `/files` reads only that folder and `~/Downloads` (`browser.read_roots`).
- **Logs** (`logs/browser-bridge.log`) record each command's action, id, duration, success and caller address; never its parameters, page contents, results or tokens.
- **Pause** the extension (Options → **Take commands from the bridge**) whenever you do not want agents driving your browser.

The agent skill (`skills/orbital-browser`) tells agents to ask you before taking control of your browser unless you already approved it for the task, and never to enter passwords or payment details, solve CAPTCHAs, or send, post, buy or delete without your explicit yes.

## Settings

```toml
[browser]
enabled = true            # false: no bridge on this machine
bridge_port = 7703
bind = "loopback"         # or "tailscale"
# Optional, with their defaults:
# max_events = 20000            captured events kept in memory
# events_log_mb = 50            captures/events.jsonl size before it rolls (0 = no file)
# capture_secrets = false       keep credential values in captures (not recommended)
# max_stage_mb = 500            largest single staged file
# staged_max_mb = 2000          staged folder cap
# staged_max_age_hours = 24
# command_timeout = 60          default seconds a command may take
# read_roots = ["~/Downloads"]  folders /files may read besides staged/
```

Data: `~/.orbital-agents/data/browser/` (`staged/`, `captures/`). The pid and port file: `run/bridge.json`. Logs: `orbital logs browser-bridge`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Popup says **not running** | start the bridge (`orbital services restart browser-bridge`, or `orbital browser serve` to see errors) and check the ports match |
| `extension NOT connected` | Chrome closed, the extension disabled, paused in Options, or set to another port |
| 503 "not connected" | the same; the command was dropped, never queued for later |
| 504 timeout | the page is slow: raise `--timeout`; `GET /result/<id>` on the bridge has the result if it finishes later |
| `401` from another machine | `ORBITAL_BROWSER_TOKEN` differs between the machines (one of them joined before the token was rotated): run `orbital satellite join <hub_url> <token>` on it |
| `502 the machine refused the hub's token` (through the hub) | the browser machine's token is stale: run `orbital satellite join <hub_url> <token>` there; it stores the fleet's token and restarts the bridge |
| `403 not allowed from this address` | the browser machine's bridge runs with `bind = "loopback"`; set `tailscale` and restart it |
| `… has no browser bridge open to the hub` | that satellite reported no browser: set `browser.enabled = true` there, then `orbital services restart satellite` so its capabilities refresh |
| `502 no answer from http://<machine>:7703` (through the hub) | the browser machine's bridge is down or listens on loopback only: `bind = "tailscale"` there and restart its bridge |
| `eval` fails on a strict site | `--world CDP`, or read the page with `text`, `dom`, `command ax_dump` or `command probe_ui` |
| A site ignores an upload | use `--label "<the page's upload button text>"` so the real file chooser is used |
| You edited the extension files | reload it on `chrome://extensions`, or `orbital browser command self_reload` |
