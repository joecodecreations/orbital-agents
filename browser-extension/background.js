/**
 * Orbital Agents Browser Controller: service worker.
 *
 * Protocol with the local bridge (orbital/browser/server.py), all on http://127.0.0.1:<port>:
 *   GET  /ext/commands?wait_ms=..&instance=..   long-poll for queued commands {id, action, params}
 *   POST /ext/results  {id, ok, result | error, ms}
 *   POST /ext/events   {events: [...]}            network capture (only while capture is on)
 *
 * The bridge accepts these only from this computer (loopback). Agents on other machines talk
 * to the bridge with a token; they never reach the extension directly.
 */

importScripts("lib/settings.js", "lib/windows.js", "lib/cdp.js", "lib/capture.js", "lib/actions.js");

const LONG_POLL_MS = 15000;   // under Chrome's 30 s limit for a pending fetch
const RETRY_MS = 2000;        // wait after a failed poll (bridge not running)
const FLUSH_MS = 400;
const KEEPALIVE_MS = 20000;   // a trivial extension call keeps the worker alive while connected

let pollRunning = false;
let pollGeneration = 0;
let pollAbort = null;
const lanes = new Map();      // lane key -> promise chain (one command at a time per session)

async function postResult(out) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(`${bridgeUrl()}/ext/results`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(out),
      });
      if (res.ok) return;
    } catch (_) {}
    await sleep(300 * (attempt + 1));
  }
}

async function execCommand(cmd) {
  await ensureSettings();
  const id = cmd.id || null;
  const action = resolveAction(cmd.action);
  const params = cmd.params || cmd.args || {};
  const started = Date.now();
  let out;
  try {
    const fn = ACTIONS[action];
    if (!fn) throw new Error(`unknown action: ${cmd.action} (see the status action for the list)`);
    const result = await fn(params);
    out = { id, ok: true, action, result: result === undefined ? null : result };
  } catch (e) {
    out = { id, ok: false, action, error: errorText(e) };
  }
  out.ms = Date.now() - started;
  recordActivity({ id, action, ok: out.ok, ms: out.ms, at: started, error: out.ok ? null : String(out.error).slice(0, 200) });
  return out;
}

/** Run a command in its session's lane and post the result when it finishes. */
function dispatch(cmd) {
  const action = resolveAction(cmd.action);
  const lane = laneFor(action, cmd.params || cmd.args || {});
  const run = async () => postResult(await execCommand(cmd));
  if (!lane) {
    run();
    return;
  }
  const prev = lanes.get(lane) || Promise.resolve();
  const next = prev.then(run, run).finally(() => {
    if (lanes.get(lane) === next) lanes.delete(lane);
  });
  lanes.set(lane, next);
}

async function pollOnce(gen) {
  const url = `${bridgeUrl()}/ext/commands?wait_ms=${LONG_POLL_MS}&instance=${encodeURIComponent(state.instanceId)}` +
    `&version=${encodeURIComponent(EXT_VERSION)}&build=${encodeURIComponent(EXT_BUILD)}&capturing=${state.capturing ? 1 : 0}`;
  pollAbort = new AbortController();
  const timer = setTimeout(() => pollAbort.abort(), LONG_POLL_MS + 10000);
  try {
    const res = await fetch(url, { headers: { Accept: "application/json" }, signal: pollAbort.signal, cache: "no-store" });
    if (!res.ok) throw new Error(`bridge answered HTTP ${res.status}`);
    const data = await res.json();
    if (gen !== pollGeneration) return;
    state.link.connected = true;
    state.link.error = null;
    state.link.lastPoll = Date.now();
    state.link.lastOk = Date.now();
    for (const cmd of data.commands || []) dispatch(cmd);
  } finally {
    clearTimeout(timer);
  }
}

async function pollLoop() {
  if (pollRunning) return;
  pollRunning = true;
  const gen = pollGeneration;
  try {
    while (gen === pollGeneration) {
      await ensureSettings();
      if (!state.enabled) {
        state.link.connected = false;
        state.link.error = "paused";
        await sleep(RETRY_MS);
        continue;
      }
      try {
        await pollOnce(gen);
      } catch (e) {
        if (gen !== pollGeneration) break;
        state.link.connected = false;
        state.link.lastPoll = Date.now();
        state.link.error = e && e.name === "AbortError" ? "poll timed out" : errorText(e);
        await sleep(RETRY_MS);
      }
    }
  } finally {
    pollRunning = false;
    if (gen !== pollGeneration) pollLoop();
  }
}

/** Settings changed (e.g. a new port): drop the current poll and start again. */
function restartPolling() {
  pollGeneration += 1;
  if (pollAbort) {
    try {
      pollAbort.abort();
    } catch (_) {}
  }
  if (!pollRunning) pollLoop();
}

// ── messages from the content script, popup, options page and side panel ──

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    await ensureSettings();
    const type = msg && msg.type;
    if (type === "page.network") {
      enqueueEvent({ type: "network.body", tabId: sender.tab && sender.tab.id, ...(msg.payload || {}) });
      return { ok: true };
    }
    if (type === "page.ready") {
      enqueueEvent({ type: "page.ready", tabId: sender.tab && sender.tab.id, url: msg.url, title: msg.title });
      return { ok: true };
    }
    // Everything below comes from the extension's own pages only.
    if (!sender || sender.id !== chrome.runtime.id || (sender.tab && !String(sender.url || "").startsWith(chrome.runtime.getURL("")))) {
      return { ok: false, error: "not allowed" };
    }
    if (type === "get_status") {
      return {
        ok: true,
        version: EXT_VERSION,
        build: EXT_BUILD,
        bridgeUrl: bridgeUrl(),
        port: state.bridgePort,
        enabled: state.enabled,
        capturing: state.capturing,
        isolateWindow: state.isolateWindow,
        automationDisplay: state.automationDisplay,
        queuedEvents: queuedEvents(),
        link: state.link,
        ...windowState(),
      };
    }
    if (type === "get_activity") return { ok: true, activity: state.activity };
    if (type === "get_displays") return { ok: true, displays: await listDisplays() };
    if (type === "set_settings") {
      await saveSettings(msg.settings || {});
      restartPolling();
      return { ok: true };
    }
    if (type === "automation_ctl") return execCommand({ action: "automation_window", params: msg.args || {} });
    if (type === "test_bridge") {
      try {
        const res = await fetch(`${bridgeUrl()}/health`, { cache: "no-store" });
        return { ok: res.ok, health: await res.json() };
      } catch (e) {
        return { ok: false, error: errorText(e) };
      }
    }
    return { ok: false, error: "unknown message" };
  })().then(sendResponse, (e) => sendResponse({ ok: false, error: errorText(e) }));
  return true;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  const relevant = ["bridgePort", "enabled", "capturing", "isolateWindow", "automationDisplay"];
  if (!relevant.some((k) => k in changes)) return;
  const portChanged = "bridgePort" in changes || "enabled" in changes;
  loadSettings()
    .then(() => syncNetworkHook())
    .then(() => {
      if (portChanged) restartPolling();
    })
    .catch(() => {});
});

// ── lifecycle ────────────────────────────────────────────

chrome.alarms.create("orbital-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {
  pollLoop();
  flushEvents();
});

chrome.runtime.onInstalled.addListener(() => {
  ensureSettings().then(syncNetworkHook).then(pollLoop).catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  ensureSettings().then(syncNetworkHook).then(pollLoop).catch(() => {});
});

setInterval(flushEvents, FLUSH_MS);
setInterval(() => {
  if (state.link.connected) chrome.runtime.getPlatformInfo().catch(() => {});
}, KEEPALIVE_MS);

(async () => {
  try {
    const info = await chrome.runtime.getPlatformInfo();
    state.isMac = info.os === "mac";
  } catch (_) {
    state.isMac = false;
  }
  await ensureSettings();
  await syncNetworkHook();
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false });
  } catch (_) {}
  pollLoop();
})();
