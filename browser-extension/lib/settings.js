/**
 * Orbital Agents Browser Controller: settings and shared state.
 *
 * Loaded first by background.js (importScripts). Everything here is a global the other
 * lib/ files use. Settings live in chrome.storage.local and are edited in the options page
 * or the popup; background.js reloads them whenever storage changes.
 *
 * The extension only ever talks to the bridge on this computer: http://127.0.0.1:<port>.
 * Other machines reach this Chrome through that bridge, never through the extension.
 */

/* exported EXT_VERSION, EXT_BUILD, DEFAULT_PORT, DEFAULT_SESSION, AUTOMATION_PAGE, state,
   ensureSettings, loadSettings, saveSettings, bridgeUrl, validPort, sessionKey, sleep, rand,
   errorText, recordActivity, SETTING_DEFAULTS */

const EXT_VERSION = chrome.runtime.getManifest().version;
/** Bump with every change to the background code so agents can tell which build is running. */
const EXT_BUILD = "0.1.0-1";
const DEFAULT_PORT = 7703;
const DEFAULT_SESSION = "main";
const AUTOMATION_PAGE = "automation.html";

const SETTING_DEFAULTS = {
  bridgePort: DEFAULT_PORT,  // the local bridge port (browser.bridge_port in config.toml)
  enabled: true,             // poll the bridge at all
  capturing: false,          // network capture (off until you turn it on)
  isolateWindow: true,       // agent tabs live in their own window and never take focus
  automationDisplay: "",     // display name (or part of it) for automation windows; "" = auto
};

const state = {
  settingsLoaded: false,
  settingsInflight: null,
  bridgePort: DEFAULT_PORT,
  enabled: true,
  capturing: false,
  isolateWindow: true,
  automationDisplay: "",
  /** Random id for this install, so the bridge can tell two pollers apart. */
  instanceId: "",
  isMac: false,
  /** Connection to the bridge, as the popup shows it. */
  link: { connected: false, error: null, lastPoll: null, lastOk: null },
  /** Last commands run: {id, action, ok, ms, at, error}. No arguments, no results. */
  activity: [],
  // Automation windows (see lib/windows.js)
  automationWindowId: null,
  activeTabId: null,
  sessions: {},       // session name -> { windowId, tabId, slot }
  userWindowId: null, // last Chrome window the user was really using
  heldWindowId: null, // window the user asked to work in; focus is left there
  focusRestoreGen: 0,
};

function validPort(value) {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 65535 ? n : null;
}

function bridgeUrl() {
  return `http://127.0.0.1:${state.bridgePort}`;
}

async function ensureSettings() {
  if (state.settingsLoaded) return;
  if (!state.settingsInflight) {
    state.settingsInflight = (async () => {
      await loadSettings();
      state.settingsLoaded = true;
    })().finally(() => {
      state.settingsInflight = null;
    });
  }
  await state.settingsInflight;
}

async function loadSettings() {
  const s = await chrome.storage.local.get({ ...SETTING_DEFAULTS, instanceId: "" });
  state.bridgePort = validPort(s.bridgePort) || DEFAULT_PORT;
  state.enabled = s.enabled !== false;
  state.capturing = s.capturing === true;
  state.isolateWindow = s.isolateWindow !== false;
  state.automationDisplay = String(s.automationDisplay || "").trim();
  if (!s.instanceId) {
    s.instanceId = crypto.randomUUID().slice(0, 8);
    await chrome.storage.local.set({ instanceId: s.instanceId });
  }
  state.instanceId = s.instanceId;
  await loadWindowState();
}

async function saveSettings(partial) {
  const clean = {};
  for (const [key, value] of Object.entries(partial || {})) {
    if (!(key in SETTING_DEFAULTS)) continue;
    if (key === "bridgePort") {
      const port = validPort(value);
      if (!port) throw new Error("port must be a number from 1 to 65535");
      clean.bridgePort = port;
    } else if (key === "automationDisplay") {
      clean.automationDisplay = String(value || "").trim().slice(0, 80);
    } else {
      clean[key] = !!value;
    }
  }
  await chrome.storage.local.set(clean);
  await loadSettings();
  if ("capturing" in clean) await syncNetworkHook();
}

/** "main" unless the caller names a session. Session names are short and safe. */
function sessionKey(argsOrName) {
  const raw = argsOrName && typeof argsOrName === "object" ? argsOrName.session : argsOrName;
  const name = String(raw == null || raw === "" ? DEFAULT_SESSION : raw).trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(name)) throw new Error("bad session name: " + name);
  return name;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function rand(lo, hi) {
  return lo + Math.floor(Math.random() * (hi - lo + 1));
}

function errorText(e) {
  return String((e && e.message) || e || "error");
}

function recordActivity(entry) {
  state.activity.unshift(entry);
  if (state.activity.length > 50) state.activity.length = 50;
}
