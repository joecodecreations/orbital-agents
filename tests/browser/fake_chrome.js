/**
 * Runs the extension's real service worker (browser-extension/background.js and lib/*.js) in Node
 * with a small fake `chrome` API, against a real bridge. Used by test_extension_node.py to check
 * the extension <-> bridge protocol without a browser.
 *
 *   node fake_chrome.js <extension dir> <bridge port> <seconds to run> [switch:<after s>:<new port>]
 *
 * The fake browser has one normal window with one tab. Pages are not real: scripting.executeScript
 * returns canned values for a few functions; tabs "load" instantly.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [extDir, port, seconds, extra] = process.argv.slice(2);
if (!globalThis.crypto) globalThis.crypto = require("crypto").webcrypto;

function event() {
  const listeners = [];
  return {
    addListener: (fn) => listeners.push(fn),
    removeListener: (fn) => {
      const i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    },
    hasListener: (fn) => listeners.includes(fn),
    fire: (...args) => listeners.slice().forEach((fn) => fn(...args)),
    listeners,
  };
}

function area(store) {
  return {
    async get(defaults) {
      if (defaults && typeof defaults === "object" && !Array.isArray(defaults)) {
        const out = {};
        for (const [k, v] of Object.entries(defaults)) out[k] = k in store ? store[k] : v;
        return out;
      }
      return { ...store };
    },
    async set(values) {
      const changes = {};
      for (const [k, v] of Object.entries(values)) {
        changes[k] = { oldValue: store[k], newValue: v };
        store[k] = v;
      }
      if (store === localStore) setTimeout(() => storageChanged.fire(changes, "local"), 0);
    },
  };
}

const localStore = {};
const sessionStore = {};
const storageChanged = event();

let nextWindow = 100;
let nextTab = 1000;
const windows = new Map();
const tabs = new Map();
const tabUpdated = event();
const registeredScripts = [];

function makeWindow(props = {}) {
  const w = { id: nextWindow++, type: "normal", state: "normal", focused: !!props.focused, left: props.left || 0, top: props.top || 0, width: props.width || 1200, height: props.height || 800 };
  windows.set(w.id, w);
  return w;
}

function makeTab(windowId, url, active = true) {
  const t = { id: nextTab++, windowId, url, title: url, active, status: "complete", pinned: false };
  if (active) for (const other of tabs.values()) if (other.windowId === windowId) other.active = false;
  tabs.set(t.id, t);
  return t;
}

function load(tab, url) {
  tab.url = url;
  tab.title = "Title of " + url;
  tab.status = "loading";
  setTimeout(() => {
    tab.status = "complete";
    tabUpdated.fire(tab.id, { status: "complete" }, { ...tab });
  }, 20);
}

const userWindow = makeWindow({ focused: true });
makeTab(userWindow.id, "https://user.example/");

function windowCopy(w, populate) {
  const out = { ...w };
  if (populate) out.tabs = [...tabs.values()].filter((t) => t.windowId === w.id).map((t) => ({ ...t }));
  return out;
}

const chrome = {
  runtime: {
    id: "fakeextensionid",
    getManifest: () => JSON.parse(fs.readFileSync(path.join(extDir, "manifest.json"), "utf8")),
    getURL: (p) => "chrome-extension://fakeextensionid/" + String(p || "").replace(/^\//, ""),
    getPlatformInfo: async () => ({ os: "mac" }),
    reload: () => {},
    onMessage: event(),
    onInstalled: event(),
    onStartup: event(),
    sendMessage: async () => ({}),
  },
  storage: { local: area(localStore), session: area(sessionStore), onChanged: storageChanged },
  alarms: { create: () => {}, onAlarm: event() },
  sidePanel: { setPanelBehavior: async () => {} },
  system: {
    display: {
      getInfo: async () => [{ name: "Built-in", isPrimary: true, bounds: { left: 0, top: 0, width: 1440, height: 900 }, workArea: { left: 0, top: 25, width: 1440, height: 875 } }],
    },
  },
  windows: {
    WINDOW_ID_NONE: -1,
    onFocusChanged: event(),
    onRemoved: event(),
    async get(id, opts) {
      const w = windows.get(id);
      if (!w) throw new Error("No window with id: " + id);
      return windowCopy(w, opts && opts.populate);
    },
    async getAll() {
      return [...windows.values()].map((w) => windowCopy(w));
    },
    async getLastFocused() {
      const w = [...windows.values()].find((x) => x.focused) || userWindow;
      return windowCopy(w);
    },
    async create(props) {
      const w = makeWindow({ ...props, focused: !!props.focused });
      const t = makeTab(w.id, props.url || "about:blank");
      return { ...windowCopy(w), tabs: [{ ...t }] };
    },
    async update(id, props) {
      const w = windows.get(id);
      if (!w) throw new Error("No window with id: " + id);
      Object.assign(w, props);
      if (props.focused) for (const other of windows.values()) other.focused = other.id === id;
      return windowCopy(w);
    },
    async remove(id) {
      windows.delete(id);
      for (const t of [...tabs.values()]) if (t.windowId === id) tabs.delete(t.id);
      chrome.windows.onRemoved.fire(id);
    },
  },
  tabs: {
    onUpdated: tabUpdated,
    async query(q = {}) {
      return [...tabs.values()]
        .filter((t) => (q.windowId == null || t.windowId === q.windowId) && (q.active == null || t.active === q.active))
        .filter((t) => !q.lastFocusedWindow || windows.get(t.windowId).focused)
        .map((t) => ({ ...t }));
    },
    async get(id) {
      const t = tabs.get(id);
      if (!t) throw new Error("No tab with id: " + id);
      return { ...t };
    },
    async create(props) {
      const t = makeTab(props.windowId || userWindow.id, "about:blank", props.active !== false);
      load(t, props.url);
      return { ...t };
    },
    async update(id, props) {
      const t = tabs.get(id);
      if (!t) throw new Error("No tab with id: " + id);
      if (props.url) load(t, props.url);
      if (props.active) for (const other of tabs.values()) other.active = other.id === id || (other.windowId !== t.windowId && other.active);
      if (props.pinned != null) t.pinned = props.pinned;
      return { ...t };
    },
    async remove(id) {
      tabs.delete(id);
    },
    async reload(id) {
      load(tabs.get(id), tabs.get(id).url);
    },
    async goBack() {},
    async goForward() {},
    async captureVisibleTab() {
      return "data:image/png;base64,iVBORw0KGgo=";
    },
  },
  scripting: {
    async getRegisteredContentScripts(filter) {
      return registeredScripts.filter((s) => !filter || !filter.ids || filter.ids.includes(s.id));
    },
    async registerContentScripts(list) {
      for (const s of list) {
        for (const file of s.js) if (!fs.existsSync(path.join(extDir, file))) throw new Error("missing script " + file);
        registeredScripts.push(s);
      }
    },
    async unregisterContentScripts(filter) {
      for (const id of filter.ids) {
        const i = registeredScripts.findIndex((s) => s.id === id);
        if (i >= 0) registeredScripts.splice(i, 1);
      }
    },
    async executeScript({ target, func, args }) {
      const tab = tabs.get(target.tabId);
      if (!tab) throw new Error("No tab with id: " + target.tabId);
      // A fake page: enough for document.title and visibilityState style probes.
      const src = String(func);
      if (src.includes("visibilityState")) return [{ result: "visible" }];
      if (src.includes("new Function")) return [{ result: { ok: true, value: "evaluated:" + args[0] } }];
      return [{ result: { ok: true, fake: true } }];
    },
  },
  debugger: {
    onEvent: event(),
    async attach() {},
    async detach() {},
    async sendCommand(target, method) {
      if (method === "Page.captureScreenshot") return { data: "iVBORw0KGgo=" };
      return {};
    },
  },
  webRequest: {
    onBeforeRequest: event(),
    onSendHeaders: event(),
    onCompleted: event(),
    onErrorOccurred: event(),
  },
};

// The fake page fires a network event when capture is on, so the test can see it arrive.
globalThis.__fireFakeRequest = () => {
  chrome.webRequest.onSendHeaders.fire({
    requestId: "r1",
    tabId: 1000,
    url: "https://api.example.com/v2/things/123456?api_key=hidden",
    method: "GET",
    requestHeaders: [{ name: "Authorization", value: "Bearer not-for-logs" }, { name: "Accept", value: "application/json" }],
  });
};

globalThis.chrome = chrome;
globalThis.self = globalThis;
globalThis.importScripts = (...files) => {
  for (const f of files) {
    const file = path.join(extDir, f);
    vm.runInThisContext(fs.readFileSync(file, "utf8"), { filename: file });
  }
};

localStore.bridgePort = Number(port);

vm.runInThisContext(fs.readFileSync(path.join(extDir, "background.js"), "utf8"), { filename: "background.js" });

// Report state the test checks, then exit.
setInterval(() => {
  if (chrome.webRequest.onSendHeaders.listeners.length) globalThis.__fireFakeRequest();
}, 300);

// "switch:<after s>:<port>" saves a new bridge port the way the options page does.
if (extra && extra.startsWith("switch:")) {
  const [, after, newPort] = extra.split(":");
  setTimeout(() => chrome.storage.local.set({ bridgePort: Number(newPort) }), Number(after) * 1000);
}

function reportAndExit() {
  process.stdout.write(
    JSON.stringify({
      registered: registeredScripts.map((s) => s.id),
      windows: windows.size,
      tabs: [...tabs.values()].map((t) => ({ id: t.id, url: t.url, windowId: t.windowId })),
      storage: localStore,
    }) + "\n",
    () => process.exit(0)
  );
}

process.on("SIGTERM", reportAndExit);
setTimeout(reportAndExit, Number(seconds) * 1000);
