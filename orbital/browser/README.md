# Orbital Agents Browser Controller

A Chrome extension plus a small local bridge, so AI agents (and you, from a terminal or Python) can drive real Chrome tabs on this machine, or on another of your machines over Tailscale.

```
agent / CLI / Python ──HTTP──▶ bridge (orbital/browser, port 7703) ◀──long-poll── extension (browser-extension/)
other machine ──Tailscale + token──▶ bridge           hub ──relay──▶ bridge on a satellite
```

The extension only ever talks to the bridge on its own machine (`http://127.0.0.1:<port>`). Everything that comes from elsewhere goes through that machine's bridge, which checks who is calling.

What it can do: open and switch tabs, navigate, click (CSS selector, visible text, accessible name, or coordinates; plain or as trusted mouse input), type (instantly or key by key like a person), read the page (text, HTML, links, the accessibility tree, a snapshot of its controls), run JavaScript (falls back to the DevTools protocol on pages whose security policy blocks it), fetch with the page's cookies, fill forms inside iframes, upload files through the page's real file chooser, wait for text or elements, take screenshots without bringing the window forward, and capture network traffic into a catalog of API endpoints.

Agent tabs live in their own Chrome window per agent session. Those windows open in the background, are tiled on a second display when you have one, and never take keyboard focus from you.

## Install

1. **Start the bridge** on the machine that runs Chrome:

   ```bash
   orbital browser serve                  # foreground; the setup wizard installs it as a service
   # or: python -m orbital.browser.server [--port 7703] [--bind loopback|tailscale]
   ```

   It listens on `127.0.0.1:7703` (`browser.bridge_port` in `~/.orbital-agents/config.toml`) and writes `~/.orbital-agents/run/bridge.json`.

2. **Load the extension** in Chrome:
   1. Open `chrome://extensions`.
   2. Turn on **Developer mode** (top right).
   3. Click **Load unpacked** and choose the `browser-extension/` folder of this repository.
   4. Pin it from the puzzle-piece menu if you like. The popup shows **connected** once it reaches the bridge.

   There is no build step; the folder is the extension.

3. **Port.** If you changed `browser.bridge_port`, set the same number in the extension: popup → **Set port**, or the **Options** page. The options page also has a **Test connection** button, a pause switch, the display used for agent windows, and the network-capture switch.

4. **Check it:** `orbital browser status`.

A second Chrome profile on the same machine needs its own bridge port: run another bridge with its own home and port (`orbital --home ~/.orbital-agents-2 browser serve --port 7713`) and set that profile's extension to 7713. Two profiles polling one bridge would take each other's commands; `orbital browser status` warns when that happens.

## Use it from another machine

Two ways, both over Tailscale:

- **Direct.** On the browser machine set `browser.bind = "tailscale"` in config.toml and restart the bridge. It then also listens on that machine's Tailscale IP and answers callers from the Tailscale ranges that send `Authorization: Bearer <ORBITAL_BROWSER_TOKEN>` (from `~/.orbital-agents/secrets.env`; one value for the whole fleet: the hub hands out the fleet browser token at join; rejoin (`orbital satellite join`) to refresh it; the bridge restarts automatically after a join that changed it). Without a token set, remote calls are refused.
- **Through the hub.** Satellites report `capabilities.browser` and `browser_port` when they join. The hub forwards `POST /v1/satellites/<id>/browser/<command>` (body: the parameters, flat with an optional `_timeout`, or `{"params": {...}, "timeout": N}`) to that machine's bridge as `POST /command {action: <command>, params, timeout}` with the hub's `ORBITAL_BROWSER_TOKEN`, so that machine's bridge still needs `bind = "tailscale"` (it already has the fleet's token from its join). The bridge's own endpoints are reachable this way too, as the actions `bridge_status`, `network`, `endpoints`, `events`, `export`, `clear`, `stage`, `files`, `files_read` and `purge_staged`; `BrowserClient` and `orbital browser --via hub` use them automatically.

`orbital browser nodes` lists this machine (`local`) and every joined satellite with a browser, with bridge and extension state. Pick one with `--node`:

```bash
orbital browser --node studio-mac navigate https://example.com
orbital browser --node studio-mac --via hub tabs        # force the hub relay
```

`--via auto` (the default) goes direct when `ORBITAL_BROWSER_TOKEN` is set here and falls back to the hub; local nodes are always direct. Files you upload to a remote node are first copied to it (`stage`), because the page's file input needs a path on that machine. Large files go faster direct than through the hub.

## Command line

```
orbital browser [--node NAME] [--via auto|direct|hub] [--session NAME] [--timeout S] [--json] <subcommand>

serve [--port N] [--bind loopback|tailscale]   run the bridge here
status                                         bridge + extension state
nodes                                          this machine and satellites with a browser
tabs                                           open tabs
navigate URL [--new-tab] [--no-wait]           open a URL in the session's tab
click SELECTOR [--index N] [--trusted]         click a CSS selector match
click-text TEXT [--exact]                      click the visible control with that text
type SELECTOR TEXT [--clear] [--human]         fill a field (human = key by key)
press KEY                                      Enter, Escape, Meta+Enter, ctrl+shift+k ...
eval JS [--world MAIN|ISOLATED|CDP]            evaluate JavaScript, print the value
text [SELECTOR]   dom [--out FILE]   links     read the page
screenshot [--out FILE] [--full-page]          PNG of the session's tab
capture on|off                                 network capture
network [--host H] [--path P] [--api-only]     captured traffic, newest first
endpoints [--host H]                           API endpoint catalog from the capture
upload SELECTOR FILE... [--label TEXT] [--click SELECTOR] [--stage]
stage FILE                                     copy a file to the node; prints its path there
wait-for [TEXT] [--selector S] [--url U] [--wait S] [--gone]
window [status|open|focus|release|close|on|off]  the session's agent window
command ACTION ['{"json": "params"}']          any action in the table below
files [PATH]   pull REMOTE --out LOCAL         files in the node's ~/Downloads or staged folder
export   clear                                 write / empty the capture
```

Results print as JSON. Exit codes: 0 ok, 1 the command failed, 2 usage, 3 the bridge could not be reached.

`--session NAME` gives an agent its own window and tab, so several agents can work in parallel without stepping on each other. Commands for one session run in order; different sessions run at the same time.

## Python API

```python
from orbital.browser import BrowserClient, BrowserError

b = BrowserClient()                                  # node="local"
b.navigate("https://example.com")
b.type("input[name=q]", "orbital agents", human=True)
b.press("Enter")
b.wait_for("results")
print(b.eval("document.title"))
b.screenshot("results.png")

remote = BrowserClient(node="studio-mac", session="research", timeout=90)
remote.upload("input[type=file]", "report.pdf")      # staged on that machine first
remote.call("ax_press", name="Submit")                # any action by name
```

`BrowserClient(node="local", *, session=None, timeout=60, via="auto", token=None, url=None, home=None)`

| Method | What it does |
|---|---|
| `status()`, `health()` | bridge and extension state |
| `tabs()` | open tabs |
| `navigate(url, new_tab=False, tab_id=None, session=None, wait=True)` | open a URL |
| `click(selector, index=0, trusted=False)`, `click_text(text)` | click |
| `type(selector, text, clear=False, human=False)`, `press(key)` | keyboard |
| `eval(js, world=None)` | the value of an expression |
| `text(selector=None)`, `dom(selector=None)`, `links()` | read the page |
| `screenshot(out=None, full_page=False)` | PNG bytes (and a file) |
| `wait_for(text=None, selector=None, url=None, timeout=15, gone=False)` | wait |
| `upload(selector, file_or_files, stage=None, click_labels=None, click_selector=None)` | file inputs |
| `stage(file)` | copy a file to the node, returns its path there |
| `capture(on)`, `network(host=None, path=None, limit=200, api_only=False)`, `endpoints(host=None)`, `events()`, `export()`, `clear()` | network capture |
| `files(path=None)`, `pull(remote_path, dest)` | files on the node |
| `window(op)` | the session's agent window |
| `call(action, timeout=None, **params)` | any action |

Failures raise `BrowserError` (`.status` is the HTTP status when there was one).

## Actions

Every command the extension runs. Page actions take optional `session` (default `main`), `tabId` (a specific tab) or `foreground: true` (the tab you are looking at instead of the agent window).

| Action | Parameters | Result |
|---|---|---|
| `ping` | | `{pong, ts}` |
| `status` | | extension version, build, port, settings, sessions, the action list |
| `self_reload` | | reloads the extension (picks up edited files) |
| `set_capture` | `enabled` | turns network capture on or off |
| `list_tabs` | `windowId?` | every tab: id, url, title, window, agent session |
| `automation_window` | `op`: status, open, focus, release, close, on, off, adopt (`windowId`) | the agent windows. `focus` hands a window to you until you click another Chrome window |
| `focus_tab` | `tabId`, `raise?` | makes a tab active (raises its window only with `raise`) |
| `close_tab` | `tabId?`, `force?` | closes the tab; `force` answers a "Leave site?" prompt |
| `navigate` | `url`, `new_tab?`, `wait?`, `timeoutMs?` | `{tabId, url, title, session, complete}` |
| `reload` | `bypassCache?` | reloads the tab |
| `go_back` | | history back |
| `go_forward` | | history forward |
| `ensure_visible` | | moves a fully covered agent window so its page counts as visible |
| `eval` | `code`, `world?` (MAIN, ISOLATED, CDP), `fallback?` | `{value, via}`; an expression returns its value, statements need `return`. Pages whose CSP blocks it are retried through CDP |
| `dom` | `selector?`, `maxLen?` | HTML of the page or one element |
| `text` | `selector?`, `maxLen?` | visible text |
| `links` | `limit?` | `[{href, text}]` |
| `probe_ui` | | buttons, fields, file inputs, dialogs and headings on the page |
| `ax_dump` | `roles?`, `contains?`, `limit?` | the accessibility tree (includes shadow DOM) |
| `ax_probe` | `roles?`, `limit?` | interactive accessibility nodes only |
| `screenshot` | `fullPage?`, `focus?` | `{dataUrl}` PNG |
| `page_fetch` | `url`, `maxBytes?` | GET with the tab's cookies, body as base64 |
| `page_request` | `url`, `method`, `headers`, `body`, `cookieHeaders` (or `requests: [...]`, up to 20) | `fetch` from inside the page; status and text come back |
| `frame_fill` | `op` (probe, fill, click), `label`, `value`, `index` | works inside every frame, cross-origin iframes included |
| `click` | `selector`, `index?`, `trusted?` | `element.click()` (searches shadow DOM) |
| `click_trusted` | `selector`, `index?` | a real mouse click at the element's center |
| `click_xy` | `x`, `y`, `probe?` | trusted click at viewport coordinates, or what is there |
| `click_text` | `text` or `texts`, `exact?` | clicks the visible control whose text or label matches; open dialogs first |
| `ax_click` | `text` or `texts`, `roles?`, `exact?`, `nth?` | clicks by accessible name |
| `ax_press` | `name` or `names`, `roles?`, `nth?`, `allowDisabled?` | trusted click by exact accessible name |
| `wheel` | `x?`, `y?`, `deltaX?`, `deltaY?` | scrolls |
| `type` | `selector` (or `placeholder`/`label`), `text`, `clear?`, `human?`, `trusted?` | fills a field; `human` types key by key with pauses |
| `type_human` | same as `type` | `type` with `human` on and `clear` on |
| `set_editable` | `selector` (or hints), `text` | replaces the whole content of a field or rich editor |
| `ax_type` | `name`, `text`, `clear?`, `human?` | types into the field with that accessible name |
| `hotkey` | `key` ("Enter", "Meta+Enter"...), `repeat?`, `focusSelector?` | trusted key press |
| `upload_file` | `path` or `paths`, `selector?`, `clickLabels?` or `clickSelector?` | sets files on a file input. With a label or selector it clicks the page's own upload button and answers the real file chooser (no dialog opens) |
| `ax_upload` | `name`, `path` or `paths` | the same, finding the upload button by accessible name |
| `wait` | `ms` | sleeps |
| `wait_for` | `text`/`texts`, `selector`, or `url`; `gone?`, `timeoutMs?` | waits; fails with a clear error on timeout |

Short names also work: `tabs` (list_tabs), `window` (automation_window), `upload` (upload_file), `press` and `key` (hotkey), `scroll` (wheel), `capture` (set_capture), `set_text` and `replace_editable` (set_editable), `click_cdp` (click_trusted), `accessibility` (ax_dump), `html` (dom), `back` and `forward`.

Site-specific automation (logging in to a particular service, posting to a particular site, crawling a particular catalog) does not belong in the controller. Write it as your own script on top of `BrowserClient`, so the controller stays general and your recipes stay yours.

## Bridge HTTP API

Base `http://127.0.0.1:7703` (or the Tailscale IP with a token).

| Method | Path | |
|---|---|---|
| GET | `/health` | no auth: `{"ok", "service": "orbital-browser-bridge", "extension_connected", "version", "machine"}` |
| GET | `/status` | bridge, extension, queue, capture and staging state |
| POST | `/command` | `{action, params, timeout?, wait?}` → `{ok, id, action, result \| error, ms}`. 503 when the extension is not connected (the command is dropped, never run later), 504 when it did not finish in time |
| POST | `/<action>` | the same with the params as the body (`_timeout`, `_wait` optional); the hub relay's shape |
| GET | `/result/<id>` | result of a command sent with `wait: false` (kept 10 minutes) |
| GET | `/network` | `host`, `path`, `limit`, `api_only` |
| GET | `/endpoints` | endpoint catalog: method, host, path with ids folded to `{id}`, count, statuses, query keys, whether an Authorization header was sent, a response sample |
| GET | `/events` | raw captured events |
| POST | `/export`, `/clear` | write `captures/full_export.json` and `endpoints_catalog.json`; empty the buffers |
| POST | `/stage?name=FILE` | raw body (or JSON `{name, content_base64}`) → `{path}` in the staged folder |
| GET | `/files?path=` | list `~/Downloads` or the staged folder |
| POST | `/files/read` | `{path}` → base64 contents |
| POST | `/purge_staged` | `{max_age_s}` |

The bridge endpoints also accept POST with their parameters in the body, and through `POST /command` as the actions `bridge_status`, `health`, `network`, `endpoints`, `events`, `export`, `clear`, `stage` (`{name, content_base64}`), `files`, `files_read` and `purge_staged` (how the hub relay reaches them; `status` there is the extension's own status). The extension uses `GET /ext/commands` (long poll), `POST /ext/results` and `POST /ext/events`, which answer loopback only.

## Security model

- **Who can connect.** With `bind = "loopback"` only processes on this machine. With `bind = "tailscale"` also the Tailscale ranges (100.64.0.0/10, fd7a:115c:a1e0::/48). Everything else gets 403 before any work is done. Never expose the port with Tailscale Funnel or a public address.
- **Token.** Every caller that is not on this machine must send `Authorization: Bearer $ORBITAL_BROWSER_TOKEN` (compared in constant time). No token configured means no remote access. Processes on this machine need no token, as the extension and local agents run here.
- **Web pages cannot use it.** The bridge sends no CORS headers, refuses any request carrying a web `Origin` header, refuses preflights, and refuses loopback requests whose `Host` is not a loopback name, so a page you visit cannot reach it even through DNS rebinding. The extension declares no `externally_connectable`, so pages cannot message it either; its control messages are accepted only from its own popup, options and side panel.
- **What it means.** Anyone who can call the bridge can act in your logged-in tabs. Treat the browser token like a password, and pause the extension (Options → Take commands from the bridge) when you do not want agents driving the browser.
- **Network capture** is off by default, and while it is off the extension injects nothing into the pages you browse. When on, it records request metadata for every tab and adds two small scripts to pages loaded from then on to read fetch/XHR bodies; the bridge stores events in memory (capped at `browser.max_events`) and in `captures/events.jsonl` (rolled at `browser.events_log_mb`). Credential values are replaced with `[redacted]` before anything is stored: Authorization, Cookie and other token, session, key or password headers, matching URL query parameters, form fields and JSON keys. `browser.capture_secrets = true` turns redaction off; avoid it.
- **Files.** Staged uploads live in `data/browser/staged/`, deleted after `browser.staged_max_age_hours` (24) or when the folder passes `browser.staged_max_mb` (2000). `/files` reads only that folder and `~/Downloads` (`browser.read_roots`).
- **Logs** (`logs/browser-bridge.log`) record each command's action, id, duration, success and caller address; never parameters, page contents, results or tokens.

## Settings

In `~/.orbital-agents/config.toml`:

```toml
[browser]
enabled = true            # false: the bridge does not start
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

Data lives in `~/.orbital-agents/data/browser/` (`staged/`, `captures/`), the pid/port file in `run/bridge.json`, logs in `logs/browser-bridge.log`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Popup says **not running** | Start the bridge (`orbital browser serve`) and check the port matches the extension's. |
| `extension NOT connected` in `orbital browser status` | Chrome closed, the extension disabled, paused in Options, or set to another port. |
| 503 "not connected" | Same as above; the command was dropped, not queued for later. |
| 504 timeout | The page is slow; raise `--timeout`. `GET /result/<id>` has the result if it finishes later. |
| `401` from another machine | the browser machine's `ORBITAL_BROWSER_TOKEN` is stale or missing: rerun `orbital satellite join <hub_url> <token>` there (it stores the fleet's token and restarts the bridge). |
| `403 not allowed from this address` | The browser machine's bridge runs with `bind = "loopback"`; set `tailscale` and restart it. |
| `eval` fails on a strict site | Use `--world CDP`, or read the page with `text`, `dom`, `ax_dump` or `probe_ui`. |
| An upload is ignored by the site | Use `--label "<the page's upload button text>"` so the real file chooser path is used. |
| Edited the extension files | Click reload on `chrome://extensions`, or run `orbital browser command self_reload`. |
