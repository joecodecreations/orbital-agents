/**
 * Automation windows: every agent session gets its own Chrome window, so agent work never
 * lands in the windows you are using and never takes keyboard focus from you.
 *
 * - "main" is the session agents use unless they name another one.
 * - Windows open unfocused, on the display named in the options (or, by default, a
 *   secondary display when there is one), tiled 2 x 2.
 * - Focus is only handed back, never taken: if a Chrome call raises an automation window
 *   while you are typing in another Chrome window, focus goes back to that window. If Chrome
 *   is not the front app, nothing is focused at all.
 * - A minimized or fullscreen automation window is repaired at most once every 30 s; a window
 *   you dragged to another display stays there.
 * - Sessions are stored in chrome.storage.local, so reloading the extension keeps them.
 */

/* exported loadWindowState, isAutomationWindow, sessionForWindow, persistSessions, windowState,
   automationPageUrl, isWorkTab, isPlaceholderUrl, runWithoutStealingFocus, pauseFocusRestore,
   releaseFocusHold, rememberUserWindow, listDisplays, preferredDisplay, ensureAutomationWindow,
   automationWorkTab, rememberSessionTab, automationStatus, closeSession, adoptWindow */

const FIT_RETRY_MS = 30000;
const lastFitAt = new Map();

async function loadWindowState() {
  try {
    const sess = await chrome.storage.session.get({ userWindowId: null, heldWindowId: null });
    state.heldWindowId = sess.heldWindowId || null;
    if (sess.userWindowId != null) state.userWindowId = sess.userWindowId;
    const saved = await chrome.storage.local.get({ automationSessions: {} });
    const sessions = saved.automationSessions && typeof saved.automationSessions === "object"
      ? saved.automationSessions
      : {};
    for (const name of Object.keys(sessions)) {
      const entry = sessions[name];
      if (!entry || entry.windowId == null) {
        delete sessions[name];
        continue;
      }
      try {
        await chrome.windows.get(entry.windowId);
      } catch (_) {
        delete sessions[name];
      }
    }
    state.sessions = sessions;
    const main = sessions[DEFAULT_SESSION];
    state.automationWindowId = main ? main.windowId : null;
    state.activeTabId = main && main.tabId != null ? main.tabId : null;
  } catch (_) {}
}

function isAutomationWindow(id) {
  if (id == null) return false;
  if (id === state.automationWindowId) return true;
  return Object.values(state.sessions).some((s) => s && s.windowId === id);
}

function sessionForWindow(windowId) {
  for (const [name, entry] of Object.entries(state.sessions)) {
    if (entry && entry.windowId === windowId) return name;
  }
  return null;
}

async function persistSessions() {
  const main = state.sessions[DEFAULT_SESSION];
  state.automationWindowId = main && main.windowId != null ? main.windowId : null;
  if (main && main.tabId != null) state.activeTabId = main.tabId;
  await chrome.storage.local.set({ automationSessions: state.sessions });
  await chrome.storage.session.set({ heldWindowId: state.heldWindowId, userWindowId: state.userWindowId });
}

function windowState() {
  return {
    isolateWindow: state.isolateWindow,
    automationWindowId: state.automationWindowId,
    holdFocus: state.heldWindowId != null,
    heldWindowId: state.heldWindowId,
    userWindowId: state.userWindowId,
    sessions: Object.entries(state.sessions).map(([name, entry]) => ({
      session: name,
      windowId: entry && entry.windowId != null ? entry.windowId : null,
      tabId: entry && entry.tabId != null ? entry.tabId : null,
    })),
  };
}

function automationPageUrl() {
  return chrome.runtime.getURL(AUTOMATION_PAGE);
}

function isWorkTab(tab) {
  const url = tab && tab.url;
  return !!url && (url.startsWith("http://") || url.startsWith("https://"));
}

function isPlaceholderUrl(url) {
  if (!url) return false;
  if (url === "about:blank") return true;
  const home = automationPageUrl();
  return url === home || url.startsWith(home + "?") || url.startsWith(home + "#");
}

/**
 * Run a Chrome call that may raise an automation window (activating a tab does on macOS),
 * then hand focus back to the window the user was in. Only a jump onto an automation window
 * is undone; moving to one of the user's own windows is their choice.
 */
async function runWithoutStealingFocus(fn) {
  if (!state.isolateWindow) return fn();
  await rememberUserWindow();
  let before = null;
  try {
    before = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
  } catch (_) {}
  const gen = state.focusRestoreGen;
  const result = await fn();
  if (!before || !before.focused) return result;
  const snap = async () => {
    if (gen !== state.focusRestoreGen || !state.isolateWindow) return;
    try {
      const now = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
      if (!now || !now.focused || now.id === before.id) return;
      if (!isAutomationWindow(now.id)) return;
      await chrome.windows.update(before.id, { focused: true });
    } catch (_) {}
  };
  await snap();
  setTimeout(snap, 100);
  setTimeout(snap, 350);
  return result;
}

/** The user wants to work in this automation window: stop handing focus back. */
function pauseFocusRestore(windowId) {
  state.heldWindowId = windowId || state.automationWindowId;
  state.focusRestoreGen += 1;
  chrome.storage.session.set({ heldWindowId: state.heldWindowId }).catch(() => {});
}

function releaseFocusHold() {
  state.heldWindowId = null;
  state.focusRestoreGen += 1;
  chrome.storage.session.set({ heldWindowId: null }).catch(() => {});
}

async function rememberUserWindow() {
  try {
    const w = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
    if (w && !isAutomationWindow(w.id)) {
      state.userWindowId = w.id;
      await chrome.storage.session.set({ userWindowId: w.id });
      return w.id;
    }
  } catch (_) {}
  if (state.userWindowId != null && !isAutomationWindow(state.userWindowId)) {
    try {
      await chrome.windows.get(state.userWindowId);
      return state.userWindowId;
    } catch (_) {
      state.userWindowId = null;
    }
  }
  try {
    const wins = await chrome.windows.getAll({ windowTypes: ["normal"] });
    const other = wins.find((w) => !isAutomationWindow(w.id));
    if (other) {
      state.userWindowId = other.id;
      chrome.storage.session.set({ userWindowId: other.id }).catch(() => {});
      return other.id;
    }
  } catch (_) {}
  return null;
}

async function listDisplays() {
  try {
    const displays = await chrome.system.display.getInfo();
    return displays.map((d) => ({
      name: d.name || "",
      primary: !!d.isPrimary,
      left: d.bounds && d.bounds.left,
      top: d.bounds && d.bounds.top,
      width: d.bounds && d.bounds.width,
      height: d.bounds && d.bounds.height,
    }));
  } catch (e) {
    return [{ error: errorText(e) }];
  }
}

/**
 * Where automation windows go: the display whose name contains the "automation display"
 * setting; otherwise the first secondary display; otherwise the primary display.
 */
function pickDisplay(displays) {
  if (!displays || !displays.length) return null;
  const want = (state.automationDisplay || "").toLowerCase();
  if (want) {
    const named = displays.find((d) => String(d.name || "").toLowerCase().includes(want));
    if (named) return named;
  }
  return displays.find((d) => !d.isPrimary) || displays.find((d) => d.isPrimary) || displays[0];
}

async function preferredDisplay() {
  try {
    return pickDisplay(await chrome.system.display.getInfo());
  } catch (_) {
    return null;
  }
}

function sessionSlot(name) {
  const entry = state.sessions[name];
  if (entry && entry.slot != null) return entry.slot;
  const used = new Set(Object.values(state.sessions).map((s) => (s ? s.slot : null)).filter((n) => n != null));
  let slot = 0;
  while (used.has(slot)) slot += 1;
  return slot;
}

function slotBounds(area, slot) {
  const cols = 2;
  const rows = 2;
  const margin = 20;
  const col = slot % cols;
  const row = Math.floor(slot / cols) % rows;
  const width = Math.max(640, Math.floor((area.width - margin * (cols + 1)) / cols));
  const height = Math.max(480, Math.floor((area.height - margin * (rows + 1)) / rows));
  return {
    left: Math.round(area.left + margin + col * (width + margin)),
    top: Math.round(area.top + margin + row * (height + margin)),
    width,
    height,
  };
}

function centerOnDisplay(win, display) {
  if (!display || !display.bounds) return false;
  const b = display.bounds;
  const cx = (win.left || 0) + (win.width || 0) / 2;
  const cy = (win.top || 0) + (win.height || 0) / 2;
  return cx >= b.left && cx < b.left + b.width && cy >= b.top && cy < b.top + b.height;
}

async function waitWhileState(windowId, winState, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const w = await chrome.windows.get(windowId);
    if (w.state !== winState) return w;
    await sleep(100);
  }
  return chrome.windows.get(windowId);
}

/** Keep an automation window usable: not minimized, not fullscreen, on some display. */
async function fitAutomationWindow(windowId, name) {
  const win = await chrome.windows.get(windowId);
  let displays = [];
  try {
    displays = await chrome.system.display.getInfo();
  } catch (_) {}
  const badState = !!win.state && win.state !== "normal";
  const offScreen = displays.length > 0 && !displays.some((d) => centerOnDisplay(win, d));
  if (!badState && !offScreen) return win;
  if (Date.now() - (lastFitAt.get(windowId) || 0) < FIT_RETRY_MS) return win;
  lastFitAt.set(windowId, Date.now());
  const display = pickDisplay(displays);
  await runWithoutStealingFocus(async () => {
    if (badState) {
      try {
        await chrome.windows.update(windowId, { state: "normal" });
        if (win.state === "fullscreen") await waitWhileState(windowId, "fullscreen", 3000);
      } catch (_) {}
    }
    if (display && offScreen) {
      try {
        await chrome.windows.update(windowId, slotBounds(display.workArea || display.bounds, sessionSlot(name)));
      } catch (_) {}
    }
  });
  return chrome.windows.get(windowId);
}

async function ensureAutomationWindow(session = DEFAULT_SESSION) {
  const name = sessionKey(session);
  const known = state.sessions[name];
  if (known && known.windowId != null) {
    try {
      const existing = await chrome.windows.get(known.windowId);
      state.sessions[name] = { windowId: existing.id, tabId: known.tabId != null ? known.tabId : null, slot: known.slot != null ? known.slot : sessionSlot(name) };
      if (name === DEFAULT_SESSION) state.automationWindowId = existing.id;
      if (existing.id !== state.heldWindowId) await fitAutomationWindow(existing.id, name);
      return existing;
    } catch (_) {
      delete state.sessions[name];
      if (name === DEFAULT_SESSION) state.automationWindowId = null;
    }
  }
  const prior = await rememberUserWindow();
  const display = await preferredDisplay();
  const slot = sessionSlot(name);
  const bounds = display ? slotBounds(display.workArea || display.bounds, slot) : { width: 1200, height: 840 };
  let created;
  await runWithoutStealingFocus(async () => {
    created = await chrome.windows.create({
      url: automationPageUrl() + "?session=" + encodeURIComponent(name),
      focused: false,
      type: "normal",
      state: "normal",
      ...bounds,
    });
    state.sessions[name] = { windowId: created.id, tabId: null, slot };
    await persistSessions();
    const tabs = created.tabs || (await chrome.tabs.query({ windowId: created.id }));
    if (tabs && tabs[0]) {
      try {
        await chrome.tabs.update(tabs[0].id, { pinned: true });
      } catch (_) {}
    }
  });
  if (prior && !isAutomationWindow(prior)) state.userWindowId = prior;
  return created;
}

/** The session's web tab (http/https), or null when it only has its placeholder page. */
async function automationWorkTab(session = DEFAULT_SESSION) {
  const name = sessionKey(session);
  const win = await ensureAutomationWindow(name);
  const entry = state.sessions[name];
  if (entry && entry.tabId != null) {
    try {
      const t = await chrome.tabs.get(entry.tabId);
      if (t && t.windowId === win.id && isWorkTab(t)) return t;
    } catch (_) {}
  }
  const tabs = await chrome.tabs.query({ windowId: win.id });
  return tabs.find(isWorkTab) || null;
}

async function rememberSessionTab(name, tabId, windowId) {
  const session = sessionKey(name);
  const prev = state.sessions[session] || {};
  state.sessions[session] = { windowId: windowId != null ? windowId : prev.windowId, tabId, slot: prev.slot };
  await persistSessions();
}

async function automationStatus() {
  const list = [];
  for (const name of Object.keys(state.sessions)) {
    const entry = state.sessions[name];
    if (!entry || entry.windowId == null) continue;
    try {
      const w = await chrome.windows.get(entry.windowId, { populate: true });
      list.push({
        session: name,
        windowId: w.id,
        tabId: entry.tabId || null,
        exists: true,
        focused: !!w.focused,
        state: w.state || "normal",
        left: w.left,
        top: w.top,
        width: w.width,
        height: w.height,
        tabCount: (w.tabs || []).length,
        held: state.heldWindowId === w.id,
      });
    } catch (_) {
      delete state.sessions[name];
    }
  }
  return {
    ...windowState(),
    sessions: list,
    extBuild: EXT_BUILD,
    displays: await listDisplays(),
  };
}

async function closeSession(session) {
  const entry = state.sessions[session];
  const id = entry && entry.windowId != null ? entry.windowId : null;
  delete state.sessions[session];
  if (state.heldWindowId === id) releaseFocusHold();
  await persistSessions();
  if (id != null) {
    try {
      await chrome.windows.remove(id);
    } catch (_) {}
  }
}

async function adoptWindow(session, windowId) {
  const w = await chrome.windows.get(Number(windowId));
  if (!w || w.type !== "normal") throw new Error("window not found");
  const prev = state.sessions[session] || {};
  state.sessions[session] = { windowId: w.id, tabId: prev.tabId != null ? prev.tabId : null, slot: prev.slot };
  await persistSessions();
}

chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  if (isAutomationWindow(windowId)) return;
  state.userWindowId = windowId;
  chrome.storage.session.set({ userWindowId: windowId }).catch(() => {});
  if (state.heldWindowId != null) releaseFocusHold();
});

chrome.windows.onRemoved.addListener((windowId) => {
  lastFitAt.delete(windowId);
  let changed = false;
  for (const [name, entry] of Object.entries(state.sessions)) {
    if (entry && entry.windowId === windowId) {
      delete state.sessions[name];
      changed = true;
    }
  }
  if (state.heldWindowId === windowId) {
    releaseFocusHold();
    changed = true;
  }
  if (changed) persistSessions().catch(() => {});
});
