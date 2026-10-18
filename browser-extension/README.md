# Orbital Agents Browser Controller (Chrome extension)

Load this folder in Chrome: `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → choose this `browser-extension/` folder. No build step.

The extension talks only to the bridge on the same computer, `http://127.0.0.1:7703` by default (change the port in the popup or the Options page). Start the bridge with `orbital browser serve`, then check with `orbital browser status`.

Files:

| File | What it is |
|---|---|
| `manifest.json` | Manifest V3: permissions, popup, options page, side panel, icons |
| `background.js` | service worker: long-polls the bridge, runs commands, posts results |
| `lib/settings.js` | settings (bridge port, capture, own window, display) and shared state |
| `lib/windows.js` | one background Chrome window per agent session; never takes focus |
| `lib/cdp.js` | DevTools-protocol helpers: trusted input, accessibility tree, screenshots, CSP-proof eval |
| `lib/capture.js` | network capture (off by default) |
| `lib/actions.js` | every command the bridge can send |
| `content_bridge.js`, `inject_network_hook.js` | page scripts, registered only while capture is on |
| `popup.*`, `options.*`, `sidepanel.*`, `automation.*`, `ui.css` | the extension's pages |

Full documentation (install, remote machines, CLI, Python API, every action, security model): [`orbital/browser/README.md`](../orbital/browser/README.md).
