/**
 * Every command the bridge can send. `orbital browser command <action> '<json params>'` runs any
 * of them; the CLI and the Python client wrap the common ones.
 *
 * Targeting: a command acts on its session's tab (session "main" unless `session` is given) in
 * that session's own automation window. Pass `tabId` for a specific tab, or `foreground: true`
 * to use the tab you are looking at instead.
 *
 * Commands for the same session run one at a time, in order; different sessions run in parallel.
 */

/* exported ACTIONS, ACTION_ALIASES, NO_TAB_ACTIONS, resolveAction, laneFor */

const ACTIONS = {};
const ACTION_ALIASES = {
  tabs: "list_tabs",
  window: "automation_window",
  upload: "upload_file",
  press: "hotkey",
  key: "hotkey",
  scroll: "wheel",
  capture: "set_capture",
  set_text: "set_editable",
  replace_editable: "set_editable",
  click_cdp: "click_trusted",
  accessibility: "ax_dump",
  html: "dom",
  back: "go_back",
  forward: "go_forward",
};
/** These never touch a page, so they skip the per-session queue. */
const NO_TAB_ACTIONS = new Set(["ping", "status", "self_reload", "set_capture", "list_tabs", "focus_tab", "wait"]);

function resolveAction(name) {
  const raw = String(name || "").trim();
  return ACTION_ALIASES[raw] || raw;
}

function laneFor(action, args) {
  if (NO_TAB_ACTIONS.has(action)) return null;
  if (action === "automation_window" && (!args.op || args.op === "status")) return null;
  if (args && args.tabId != null) return "tab:" + args.tabId;
  try {
    return "session:" + sessionKey(args);
  } catch (_) {
    return "session:" + DEFAULT_SESSION;
  }
}

function clamp(value, lo, hi, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(lo, Math.min(hi, n));
}

// ── which tab ────────────────────────────────────────────

function wantsIsolate(args) {
  if (!state.isolateWindow) return false;
  if (args && (args.foreground === true || args.isolate === false)) return false;
  return true;
}

async function focusedWindowTab() {
  if (state.activeTabId != null && !state.isolateWindow) {
    try {
      const t = await chrome.tabs.get(state.activeTabId);
      if (t && isWorkTab(t)) return t;
    } catch (_) {}
  }
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const t = tabs[0];
  if (t && isWorkTab(t)) return t;
  const all = await chrome.tabs.query({ lastFocusedWindow: true });
  return all.find(isWorkTab) || t || null;
}

async function activeTab(args) {
  if (wantsIsolate(args)) {
    const session = sessionKey(args);
    const work = await automationWorkTab(session);
    if (work) return work;
    const win = await ensureAutomationWindow(session);
    const tabs = await chrome.tabs.query({ windowId: win.id });
    return tabs.find((t) => !isPlaceholderUrl(t.url)) || tabs[0] || null;
  }
  return focusedWindowTab();
}

async function requireTab(args) {
  const tab = args.tabId != null ? await chrome.tabs.get(Number(args.tabId)) : await activeTab(args);
  if (!tab) throw new Error("no tab: navigate somewhere first");
  return tab;
}

async function runInTab(tab, func, fargs = [], world = "ISOLATED", allFrames = false) {
  const results = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames }, world, func, args: fargs });
  if (allFrames) return results.map((r) => r.result);
  if (!results || !results[0]) throw new Error("script did not run in this tab");
  return results[0].result;
}

function waitTabComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    let done = false;
    const finish = (complete) => {
      if (done) return;
      done = true;
      clearInterval(iv);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve(complete);
    };
    function onUpdated(id, info) {
      if (id === tabId && info.status === "complete") finish(true);
    }
    chrome.tabs.onUpdated.addListener(onUpdated);
    const iv = setInterval(async () => {
      if (Date.now() - t0 > timeoutMs) return finish(false);
      try {
        const t = await chrome.tabs.get(tabId);
        if (t.status === "complete") finish(true);
      } catch (_) {
        finish(false);
      }
    }, 250);
  });
}

function tabInfo(t) {
  return {
    id: t.id,
    url: t.url,
    title: t.title,
    active: t.active,
    status: t.status,
    windowId: t.windowId,
    automation: isAutomationWindow(t.windowId),
    session: sessionForWindow(t.windowId),
    placeholder: isPlaceholderUrl(t.url),
  };
}

// ── meta ─────────────────────────────────────────────────

ACTIONS.ping = async () => ({ pong: true, ts: Date.now() });

ACTIONS.status = async () => ({
  version: EXT_VERSION,
  build: EXT_BUILD,
  bridgeUrl: bridgeUrl(),
  port: state.bridgePort,
  instance: state.instanceId,
  enabled: state.enabled,
  capturing: state.capturing,
  queuedEvents: queuedEvents(),
  ...windowState(),
  actions: Object.keys(ACTIONS).sort(),
  aliases: ACTION_ALIASES,
});

ACTIONS.self_reload = async () => {
  setTimeout(() => chrome.runtime.reload(), 400);
  return { reloading: true, build: EXT_BUILD };
};

ACTIONS.set_capture = async (args) => {
  await saveSettings({ capturing: !!(args.enabled ?? args.on ?? args.state === "on") });
  return { capturing: state.capturing };
};

// ── tabs and windows ─────────────────────────────────────

ACTIONS.list_tabs = async (args) => {
  const query = args.windowId != null ? { windowId: Number(args.windowId) } : {};
  const tabs = await chrome.tabs.query(query);
  return tabs.map(tabInfo);
};

ACTIONS.automation_window = async (args) => {
  const op = String(args.op || "status");
  const session = sessionKey(args);
  if (op === "on" || op === "off") {
    await saveSettings({ isolateWindow: op === "on" });
    if (!state.isolateWindow) releaseFocusHold();
  } else if (op === "open") {
    await ensureAutomationWindow(session);
  } else if (op === "adopt") {
    if (args.windowId == null) throw new Error("windowId required (see list_tabs)");
    await adoptWindow(session, args.windowId);
  } else if (op === "focus") {
    const w = await ensureAutomationWindow(session);
    pauseFocusRestore(w.id);
    await chrome.windows.update(w.id, { focused: true });
  } else if (op === "release") {
    releaseFocusHold();
    await rememberUserWindow();
    if (state.userWindowId != null && !isAutomationWindow(state.userWindowId)) {
      try {
        await chrome.windows.update(state.userWindowId, { focused: true });
      } catch (_) {}
    }
  } else if (op === "close") {
    await closeSession(session);
  } else if (op !== "status") {
    throw new Error("unknown window op: " + op + " (status, on, off, open, focus, release, close, adopt)");
  }
  return automationStatus();
};

ACTIONS.focus_tab = async (args) => {
  if (args.tabId == null) throw new Error("tabId required");
  const tabId = Number(args.tabId);
  const tab = await chrome.tabs.get(tabId);
  const raise = args.raise === true || args.focus === true || !wantsIsolate(args);
  if (raise) {
    if (isAutomationWindow(tab.windowId)) pauseFocusRestore(tab.windowId);
    await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  } else {
    await runWithoutStealingFocus(() => chrome.tabs.update(tabId, { active: true }));
  }
  const owner = sessionForWindow(tab.windowId);
  if (owner) await rememberSessionTab(owner, tabId, tab.windowId);
  else if (!wantsIsolate(args)) state.activeTabId = tabId;
  return { tabId, raised: raise, session: owner };
};

ACTIONS.close_tab = async (args) => {
  const tab = await requireTab(args);
  const closed = { tabId: tab.id, url: tab.url, title: tab.title };
  if (args.force) {
    // Answer a "Leave site?" (beforeunload) prompt with Leave, including one already open.
    let dialogs = 0;
    const limit = (promise, ms) => Promise.race([promise.catch(() => null), sleep(ms).then(() => null)]);
    const target = { tabId: tab.id };
    const onDialog = (source, method) => {
      if (!source || source.tabId !== tab.id || method !== "Page.javascriptDialogOpening") return;
      dialogs += 1;
      cdp(target, "Page.handleJavaScriptDialog", { accept: true }).catch(() => {});
    };
    const gone = async () => {
      try {
        await chrome.tabs.get(tab.id);
        return false;
      } catch (_) {
        return true;
      }
    };
    chrome.debugger.onEvent.addListener(onDialog);
    try {
      await limit(chrome.debugger.attach(target, "1.3"), 3000);
      await limit(cdp(target, "Page.handleJavaScriptDialog", { accept: true }), 1500);
      await limit(cdp(target, "Page.enable"), 3000);
      await limit(cdp(target, "Page.close"), 3000);
      for (let i = 0; i < 20 && !(await gone()); i++) await sleep(250);
      if (!(await gone())) await limit(chrome.tabs.remove(tab.id), 5000);
    } finally {
      chrome.debugger.onEvent.removeListener(onDialog);
      await limit(chrome.debugger.detach(target), 2000);
    }
    closed.dialogs = dialogs;
  } else {
    await chrome.tabs.remove(tab.id);
  }
  if (state.activeTabId === tab.id) state.activeTabId = null;
  const owner = sessionForWindow(tab.windowId);
  if (owner && state.sessions[owner] && state.sessions[owner].tabId === tab.id) {
    state.sessions[owner].tabId = null;
    await persistSessions();
  }
  return { closed, session: owner };
};

function normalizeUrl(raw) {
  const url = String(raw || "").trim();
  if (!url) throw new Error("url required");
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) return url;
  return "https://" + url;
}

ACTIONS.navigate = async (args) => {
  const url = normalizeUrl(args.url);
  const isolate = wantsIsolate(args);
  const session = isolate ? sessionKey(args) : null;
  let tab = args.tabId != null ? await chrome.tabs.get(Number(args.tabId)) : null;
  if (!tab && isolate) {
    await ensureAutomationWindow(session);
    tab = args.new_tab ? null : await automationWorkTab(session);
  } else if (!tab) {
    tab = await focusedWindowTab();
  }
  const placeholder = tab && isPlaceholderUrl(tab.url);
  const needNew =
    !!args.new_tab ||
    !tab ||
    (!placeholder && (!tab.url || /^(chrome|chrome-extension|about|devtools):/.test(tab.url)));
  const sessionWindowId = session && state.sessions[session] ? state.sessions[session].windowId : null;
  const inAuto = isolate && sessionWindowId != null && ((tab && tab.windowId === sessionWindowId) || args.tabId == null);
  const activate = !isolate || inAuto || args.focus === true;
  const go = async () => {
    if (needNew && !placeholder) {
      const props = { url, active: activate };
      if (inAuto && args.tabId == null) props.windowId = sessionWindowId;
      else if (args.new_tab && tab && tab.windowId != null) props.windowId = tab.windowId;
      tab = await chrome.tabs.create(props);
    } else if (needNew && placeholder && args.new_tab) {
      tab = await chrome.tabs.create({ url, windowId: tab.windowId, active: activate });
    } else {
      const update = { url };
      if (activate) update.active = true;
      tab = await chrome.tabs.update(tab.id, update);
    }
  };
  if (activate && inAuto && args.focus !== true) await runWithoutStealingFocus(go);
  else await go();
  if (session && (args.tabId == null || tab.windowId === sessionWindowId)) await rememberSessionTab(session, tab.id, tab.windowId);
  else if (!isolate) state.activeTabId = tab.id;
  let complete = true;
  if (args.wait !== false) complete = await waitTabComplete(tab.id, clamp(args.timeoutMs, 1000, 300000, 30000));
  const fresh = await chrome.tabs.get(tab.id);
  const out = {
    tabId: fresh.id,
    url: fresh.url,
    title: fresh.title,
    windowId: fresh.windowId,
    session: session || sessionForWindow(fresh.windowId),
    automation: isAutomationWindow(fresh.windowId),
    complete,
  };
  if (!complete && args.wait !== false) out.warning = "page was still loading when the wait ended";
  return out;
};

ACTIONS.reload = async (args) => {
  const tab = await requireTab(args);
  await chrome.tabs.reload(tab.id, { bypassCache: !!args.bypassCache });
  const complete = await waitTabComplete(tab.id, clamp(args.timeoutMs, 1000, 300000, 30000));
  const fresh = await chrome.tabs.get(tab.id);
  return { tabId: fresh.id, url: fresh.url, title: fresh.title, complete };
};

async function historyStep(args, back) {
  const tab = await requireTab(args);
  if (back) await chrome.tabs.goBack(tab.id);
  else await chrome.tabs.goForward(tab.id);
  await sleep(300);
  const complete = await waitTabComplete(tab.id, clamp(args.timeoutMs, 1000, 120000, 15000));
  const fresh = await chrome.tabs.get(tab.id);
  return { tabId: fresh.id, url: fresh.url, title: fresh.title, complete };
}
ACTIONS.go_back = (args) => historyStep(args, true);
ACTIONS.go_forward = (args) => historyStep(args, false);

ACTIONS.ensure_visible = async (args) => {
  // A window that other windows cover completely counts as hidden to its pages, and some
  // pages then stop loading media. Slide the session window to the top-right corner of its
  // display, where the 20 px tiling margin leaves a strip of it uncovered. Never focuses.
  const tab = await requireTab(args);
  const visibility = () => runInTab(tab, () => document.visibilityState);
  const before = await visibility();
  let moved = null;
  if (before !== "visible" && state.heldWindowId !== tab.windowId) {
    const display = await preferredDisplay();
    const win = await chrome.windows.get(tab.windowId);
    const area = display ? display.workArea || display.bounds : null;
    if (area && win.state === "normal") {
      const width = Math.min(Number(args.width) || win.width || 1250, area.width - 40);
      const height = Math.min(Number(args.height) || win.height || 900, area.height);
      const bounds = { left: area.left + area.width - width, top: area.top, width, height };
      await runWithoutStealingFocus(() => chrome.windows.update(win.id, bounds));
      moved = bounds;
      await sleep(800);
    }
  }
  return { before, after: await visibility(), moved, windowId: tab.windowId };
};

// ── reading pages ────────────────────────────────────────

function looksLikeCspBlock(message) {
  return /unsafe-eval|Content Security Policy|Trusted ?Types?|TrustedScript|EvalError|Refused to evaluate/i.test(message || "");
}

ACTIONS.eval = async (args) => {
  const tab = await requireTab(args);
  const code = String(args.code ?? args.js ?? "");
  if (!code.trim()) throw new Error("code required");
  const world = String(args.world || "MAIN").toUpperCase();
  if (world === "CDP") return { value: await cdpEvaluate(tab.id, code), via: "cdp" };
  const r = await runInTab(
    tab,
    async (src) => {
      try {
        let fn;
        try {
          fn = new Function(`"use strict"; return (async () => (${src}\n))();`);
        } catch (e) {
          if (!(e instanceof SyntaxError)) throw e;
          fn = new Function(`"use strict"; return (async () => { ${src}\n })();`);
        }
        const val = await fn();
        try {
          return { ok: true, value: val === undefined ? null : JSON.parse(JSON.stringify(val)) };
        } catch (_) {
          return { ok: true, value: String(val) };
        }
      } catch (e) {
        return { ok: false, error: String(e && e.message ? e.message : e), name: e && e.name };
      }
    },
    [code],
    world === "ISOLATED" ? "ISOLATED" : "MAIN"
  );
  if (r && r.ok) return { value: r.value, via: world.toLowerCase() };
  const message = (r && r.error) || "eval failed";
  // Sites with a strict Content-Security-Policy refuse `new Function`; CDP evaluation is allowed.
  if (args.fallback !== false && looksLikeCspBlock(message)) return { value: await cdpEvaluate(tab.id, code), via: "cdp" };
  throw new Error(message);
};

ACTIONS.dom = async (args) => {
  const tab = await requireTab(args);
  return runInTab(
    tab,
    (selector, maxLen) => {
      const el = selector ? document.querySelector(selector) : document.documentElement;
      if (!el) return { ok: false, error: "not found: " + selector, url: location.href };
      const html = el.outerHTML;
      return { url: location.href, title: document.title, html: html.slice(0, maxLen), htmlLength: html.length, truncated: html.length > maxLen };
    },
    [args.selector ? String(args.selector) : "", clamp(args.maxLen, 1000, 5000000, 200000)]
  );
};

ACTIONS.text = async (args) => {
  const tab = await requireTab(args);
  return runInTab(
    tab,
    (selector, maxLen) => {
      const el = selector ? document.querySelector(selector) : document.body;
      if (!el) return { ok: false, error: "not found: " + selector, url: location.href };
      const text = el.innerText || el.textContent || "";
      return { url: location.href, title: document.title, text: text.slice(0, maxLen), length: text.length, truncated: text.length > maxLen };
    },
    [args.selector ? String(args.selector) : "", clamp(args.maxLen, 100, 5000000, 100000)]
  );
};

ACTIONS.links = async (args) => {
  const tab = await requireTab(args);
  return runInTab(
    tab,
    (limit) =>
      [...document.querySelectorAll("a[href]")]
        .map((a) => ({ href: a.href, text: (a.innerText || a.getAttribute("aria-label") || "").trim().slice(0, 100) }))
        .slice(0, limit),
    [clamp(args.limit, 1, 5000, 500)]
  );
};

ACTIONS.probe_ui = async (args) => {
  // One snapshot of what an agent can act on: buttons, fields, file inputs, dialogs, headings.
  const tab = await requireTab(args);
  return runInTab(tab, () => {
    const vis = (el) => {
      try {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        return r.width >= 1 && r.height >= 1 && st.display !== "none" && st.visibility !== "hidden";
      } catch (_) {
        return false;
      }
    };
    const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
    const label = (el) => clean(el.getAttribute("aria-label") || el.getAttribute("title") || el.innerText || el.textContent).slice(0, 100);
    const buttons = [];
    const seen = new Set();
    for (const el of document.querySelectorAll("button, [role='button'], a[href], [role='menuitem'], [role='tab'], [role='link'], input[type=submit]")) {
      if (!vis(el)) continue;
      const t = label(el) || clean(el.value);
      if (!t || t.length > 80 || seen.has(t)) continue;
      seen.add(t);
      buttons.push(t);
      if (buttons.length >= 80) break;
    }
    const fields = [...document.querySelectorAll("input:not([type=hidden]):not([type=file]), textarea, select, [contenteditable='true'], [role='textbox']")]
      .filter(vis)
      .slice(0, 40)
      .map((el) => ({
        tag: el.tagName,
        type: el.getAttribute("type") || "",
        name: el.getAttribute("name") || "",
        id: el.id || "",
        label: clean(el.getAttribute("aria-label") || el.getAttribute("placeholder") || (el.labels && el.labels[0] && el.labels[0].innerText) || "").slice(0, 100),
        role: el.getAttribute("role") || "",
      }));
    const files = [...document.querySelectorAll("input[type=file]")].map((el, i) => ({
      i,
      accept: el.getAttribute("accept") || "",
      multiple: !!el.multiple,
      visible: vis(el),
      name: el.getAttribute("name") || "",
    }));
    const dialogs = [...document.querySelectorAll("[role='dialog'], [aria-modal='true'], dialog[open]")]
      .filter(vis)
      .slice(0, 8)
      .map((el) => ({ label: el.getAttribute("aria-label") || "", text: clean(el.innerText).slice(0, 160) }));
    const headings = [...document.querySelectorAll("h1, h2, h3, [role='heading']")]
      .filter(vis)
      .map((el) => clean(el.innerText).slice(0, 80))
      .filter(Boolean)
      .slice(0, 20);
    return {
      url: location.href,
      title: document.title,
      buttons,
      fields,
      files,
      fileCount: files.length,
      dialogs,
      headings,
      bodyText: clean(document.body && document.body.innerText).slice(0, 500),
    };
  });
};

ACTIONS.ax_dump = async (args) => {
  // The accessibility tree: what a screen reader sees, including controls inside shadow DOM.
  const tab = await requireTab(args);
  const wantRoles = Array.isArray(args.roles) ? args.roles : args.role ? [args.role] : [];
  const contains = String(args.contains || "").toLowerCase();
  const limit = clamp(args.limit, 1, 2000, 400);
  return withDebugger(tab.id, async (target) => {
    await cdp(target, "Accessibility.enable").catch(() => {});
    const tree = await cdp(target, "Accessibility.getFullAXTree");
    const nodes = (tree && tree.nodes) || [];
    const prop = (n, key) => {
      for (const p of n.properties || []) {
        if (p.name !== key) continue;
        const v = p.value;
        return v && typeof v === "object" && "value" in v ? v.value : v;
      }
      return null;
    };
    const out = [];
    for (const n of nodes) {
      if (n.ignored && !args.includeIgnored) continue;
      const role = (n.role && n.role.value) || "";
      const name = String((n.name && n.name.value) || "").replace(/\s+/g, " ").trim();
      if (!name) continue;
      if (wantRoles.length && !wantRoles.includes(role)) continue;
      if (contains && !name.toLowerCase().includes(contains)) continue;
      out.push({
        role,
        name: name.slice(0, 180),
        value: prop(n, "value") ?? (n.value && n.value.value) ?? null,
        checked: prop(n, "checked"),
        disabled: prop(n, "disabled"),
        focused: prop(n, "focused"),
        ignored: !!n.ignored,
      });
      if (out.length >= limit) break;
    }
    return { url: tab.url, total: nodes.length, count: out.length, nodes: out };
  });
};

ACTIONS.ax_probe = async (args) => {
  // Interactive controls only (buttons, links, fields, tabs, menu items), one per role + name.
  const roles = ["button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "option", "slider", "spinbutton"];
  const dump = await ACTIONS.ax_dump({ ...args, roles: Array.isArray(args.roles) ? args.roles : roles, limit: clamp(args.limit, 1, 2000, 200) });
  const seen = new Set();
  const nodes = [];
  for (const n of dump.nodes) {
    const k = n.role + "|" + n.name;
    if (seen.has(k)) continue;
    seen.add(k);
    nodes.push(n);
  }
  return { url: dump.url, total: dump.total, count: nodes.length, nodes };
};

ACTIONS.screenshot = async (args) => {
  // PNG data URL. In the automation window this uses CDP, so the window does not come forward.
  const tab = await requireTab(args);
  const isolate = wantsIsolate(args) && args.focus !== true;
  if (isolate || args.fullPage) {
    try {
      return { dataUrl: await captureTabPng(tab.id, { fullPage: !!args.fullPage }), tabId: tab.id, url: tab.url, via: "cdp" };
    } catch (e) {
      if (args.fullPage) throw e;
      await runWithoutStealingFocus(() => chrome.tabs.update(tab.id, { active: true }));
    }
  } else {
    await chrome.tabs.update(tab.id, { active: true });
  }
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  return { dataUrl, tabId: tab.id, url: tab.url, via: "visible" };
};

ACTIONS.page_fetch = async (args) => {
  // GET a URL with the tab's cookies; the body comes back base64.
  const tab = await requireTab(args);
  const url = String(args.url || tab.url || "");
  return runInTab(
    tab,
    async (href, maxBytes) => {
      const resp = await fetch(href, { credentials: "include" });
      const buf = new Uint8Array(await resp.arrayBuffer());
      if (buf.length > maxBytes) return { ok: false, status: resp.status, error: `response is ${buf.length} bytes (limit ${maxBytes})` };
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      return {
        ok: resp.ok,
        status: resp.status,
        type: resp.headers.get("content-type") || "",
        disposition: resp.headers.get("content-disposition") || "",
        bytes: buf.length,
        b64: btoa(bin),
        url: href,
      };
    },
    [url, clamp(args.maxBytes, 1, 100 * 1024 * 1024, 50 * 1024 * 1024)],
    "MAIN"
  );
};

ACTIONS.page_request = async (args) => {
  // fetch() from inside the page with its cookies, a method, headers and a body; or
  // `requests: [...]` (up to 20, in order). Works where the page's CSP blocks eval.
  // `cookieHeaders: {"X-CSRF-Token": "csrftoken"}` copies a readable cookie into a header in the
  // page, so the value never leaves Chrome. Only status and response text come back.
  const tab = await requireTab(args);
  const list = Array.isArray(args.requests) ? args.requests : [args];
  const reqs = list.slice(0, 20).map((q) => ({
    id: q.id == null ? null : String(q.id),
    url: String(q.url || ""),
    method: String(q.method || "GET").toUpperCase(),
    headers: q.headers && typeof q.headers === "object" ? q.headers : {},
    body: q.body == null ? null : typeof q.body === "string" ? q.body : JSON.stringify(q.body),
    cookieHeaders: q.cookieHeaders && typeof q.cookieHeaders === "object" ? q.cookieHeaders : {},
    maxChars: clamp(q.maxChars, 1, 4000000, 500000),
  }));
  return runInTab(
    tab,
    async (reqs) => {
      const responses = [];
      for (const q of reqs) {
        try {
          const headers = { ...q.headers };
          for (const [header, name] of Object.entries(q.cookieHeaders)) {
            const escaped = String(name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
            const m = document.cookie.match(new RegExp(`(?:^|;\\s*)${escaped}=([^;]+)`));
            if (m) headers[header] = decodeURIComponent(m[1]);
          }
          const bodyless = q.method === "GET" || q.method === "HEAD";
          const resp = await fetch(q.url, { method: q.method, headers, body: bodyless ? undefined : q.body, credentials: "include" });
          const text = await resp.text();
          responses.push({
            id: q.id,
            ok: resp.ok,
            status: resp.status,
            type: resp.headers.get("content-type") || "",
            length: text.length,
            truncated: text.length > q.maxChars,
            text: text.slice(0, q.maxChars),
          });
        } catch (e) {
          responses.push({ id: q.id, ok: false, status: 0, error: String(e && e.message ? e.message : e).slice(0, 300) });
        }
      }
      return { url: location.href, responses };
    },
    [reqs],
    "MAIN"
  );
};

ACTIONS.frame_fill = async (args) => {
  // Probe, fill or click controls in every frame of the page, cross-origin iframes included.
  // op "probe" lists controls; "fill" sets the field whose label contains `label`; "click" clicks it.
  const tab = await requireTab(args);
  const job = {
    op: String(args.op || "probe"),
    label: String(args.label || ""),
    value: args.value == null ? "" : String(args.value),
    index: Number(args.index || 0),
  };
  const frames = await runInTab(
    tab,
    (job) => {
      const vis = (el) => {
        try {
          const r = el.getBoundingClientRect();
          const st = getComputedStyle(el);
          return r.width > 1 && r.height > 1 && st.visibility !== "hidden" && st.display !== "none";
        } catch (_) {
          return false;
        }
      };
      const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
      const labelOf = (el) => {
        let byFor = "";
        if (el.id) {
          try {
            const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (l) byFor = l.innerText || "";
          } catch (_) {}
        }
        const labelled = (el.getAttribute("aria-labelledby") || "")
          .split(/\s+/)
          .filter(Boolean)
          .map((i) => (document.getElementById(i) || {}).innerText || "")
          .join(" ");
        return clean(el.getAttribute("aria-label") || byFor || labelled || el.getAttribute("placeholder") || el.getAttribute("name") || "").slice(0, 140);
      };
      const controls = [];
      for (const el of document.querySelectorAll("input, textarea, select, [role='combobox'], [role='listbox'], button, [role='button'], [role='option']")) {
        if (!vis(el)) continue;
        const r = el.getBoundingClientRect();
        controls.push({
          i: controls.length,
          tag: el.tagName,
          type: (el.getAttribute("type") || "").slice(0, 24),
          role: el.getAttribute("role") || "",
          label: labelOf(el),
          text: clean(el.innerText || "").slice(0, 80),
          value: (el.getAttribute("type") || "").toLowerCase() === "password" ? "" : clean(el.value || "").slice(0, 80),
          x: Math.round(r.left),
          y: Math.round(r.top),
          w: Math.round(r.width),
          h: Math.round(r.height),
        });
        if (controls.length >= 50) break;
      }
      let acted = null;
      const want = job.label.toLowerCase();
      if (job.op === "fill" && want) {
        const matches = [];
        for (const el of document.querySelectorAll("input, textarea")) {
          if (!vis(el)) continue;
          const type = (el.getAttribute("type") || "text").toLowerCase();
          if (["hidden", "checkbox", "radio", "file"].includes(type)) continue;
          if (labelOf(el).toLowerCase().includes(want)) matches.push(el);
        }
        const el = matches[job.index] || null;
        if (!el) acted = { ok: false, error: "no match", matches: matches.length };
        else {
          const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, "value");
          el.focus();
          if (desc && desc.set) desc.set.call(el, job.value);
          else el.value = job.value;
          el.dispatchEvent(new InputEvent("input", { bubbles: true, data: job.value, inputType: "insertText" }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          acted = { ok: true, label: labelOf(el) };
        }
      }
      if (job.op === "click" && want) {
        const matches = [];
        for (const el of document.querySelectorAll("button, [role='button'], [role='combobox'], [role='option'], [role='listbox'], a, li, span, div")) {
          if (!vis(el)) continue;
          const inner = clean(el.innerText || "").toLowerCase();
          const hit = labelOf(el).toLowerCase().includes(want) || (inner.length > 0 && inner.length < 80 && inner === want);
          if (!hit) continue;
          const role = el.getAttribute("role") || "";
          const rank = role === "option" ? 0 : role === "listbox" || role === "combobox" ? 1 : role === "button" || el.tagName === "BUTTON" ? 2 : 5;
          const r = el.getBoundingClientRect();
          matches.push({ el, rank, area: Math.round(r.width * r.height) });
        }
        matches.sort((a, b) => a.rank - b.rank || a.area - b.area);
        const el = (matches[job.index] || {}).el || null;
        if (!el) acted = { ok: false, error: "no match", matches: matches.length };
        else {
          el.click();
          acted = { ok: true, tag: el.tagName, text: clean(el.innerText || el.getAttribute("aria-label") || "").slice(0, 80) };
        }
      }
      const iframes = [];
      if (window === window.top) {
        for (const frame of document.querySelectorAll("iframe")) {
          const r = frame.getBoundingClientRect();
          if (r.width < 2 || r.height < 2) continue;
          iframes.push({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), src: (frame.src || "").slice(0, 140) });
        }
      }
      const text = clean((document.body && document.body.innerText) || "").slice(0, 360);
      if (!controls.length && !iframes.length && text.length < 30) return null;
      return { href: location.href.slice(0, 180), text, controls, iframes, acted };
    },
    [job],
    "ISOLATED",
    true
  );
  return frames.filter(Boolean);
};

// ── clicking ─────────────────────────────────────────────

async function elementCenter(tab, selector, index) {
  return runInTab(
    tab,
    (sel, idx) => {
      const out = [];
      const visit = (root) => {
        for (const el of root.querySelectorAll(sel)) out.push(el);
        for (const el of root.querySelectorAll("*")) if (el.shadowRoot) visit(el.shadowRoot);
      };
      try {
        visit(document);
      } catch (e) {
        return { ok: false, error: "bad selector: " + sel };
      }
      const el = out[idx];
      if (!el) return { ok: false, error: `not found: ${sel}[${idx}] (matches: ${out.length})`, count: out.length };
      el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return { ok: false, error: "element has no size: " + sel, count: out.length };
      return {
        ok: true,
        count: out.length,
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        w: r.width,
        h: r.height,
        tag: el.tagName,
        text: (el.innerText || el.getAttribute("aria-label") || el.value || "").replace(/\s+/g, " ").trim().slice(0, 120),
      };
    },
    [selector, index]
  );
}

ACTIONS.click = async (args) => {
  const selector = String(args.selector || "");
  if (!selector) throw new Error("selector required");
  if (args.trusted) return ACTIONS.click_trusted(args);
  const tab = await requireTab(args);
  const r = await runInTab(
    tab,
    (sel, idx) => {
      const out = [];
      const visit = (root) => {
        for (const el of root.querySelectorAll(sel)) out.push(el);
        for (const el of root.querySelectorAll("*")) if (el.shadowRoot) visit(el.shadowRoot);
      };
      try {
        visit(document);
      } catch (e) {
        return { ok: false, error: "bad selector: " + sel };
      }
      const el = out[idx];
      if (!el) return { ok: false, error: `not found: ${sel}` + (out.length ? ` (index ${idx} of ${out.length})` : "") };
      el.scrollIntoView({ block: "center", behavior: "instant" });
      el.click();
      return { ok: true, tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim().slice(0, 120), count: out.length };
    },
    [selector, Number(args.index || 0)]
  );
  if (!r || r.ok === false) throw new Error((r && r.error) || "click failed");
  return r;
};

ACTIONS.click_trusted = async (args) => {
  // A real mouse click at the element's center through CDP (the page sees isTrusted: true).
  const tab = await requireTab(args);
  const selector = String(args.selector || "");
  if (!selector) throw new Error("selector required");
  return withDebugger(tab.id, async (target) => {
    await sleep(250); // the debugger bar may shift the page; measure after it appears
    const info = await elementCenter(tab, selector, Number(args.index || 0));
    if (!info || !info.ok) throw new Error((info && info.error) || "element not found");
    await trustedClick(target, info.x, info.y, { clickCount: Number(args.clickCount || 1) });
    await sleep(clamp(args.holdMs, 0, 10000, 300));
    return { ...info, trusted: true };
  });
};

ACTIONS.click_xy = async (args) => {
  // Trusted click at viewport coordinates, or `probe: true` to see what is there.
  const tab = await requireTab(args);
  const x = Number(args.x);
  const y = Number(args.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("x and y required");
  if (args.probe) {
    const hit = await runInTab(
      tab,
      (px, py) => {
        const el = document.elementFromPoint(px, py);
        if (!el) return { ok: false };
        const r = el.getBoundingClientRect();
        return {
          ok: true,
          tag: el.tagName,
          role: el.getAttribute("role") || "",
          label: (el.getAttribute("aria-label") || el.innerText || "").replace(/\s+/g, " ").slice(0, 80),
          x: Math.round(r.left),
          y: Math.round(r.top),
          w: Math.round(r.width),
          h: Math.round(r.height),
        };
      },
      [x, y]
    );
    return { probe: hit, x, y };
  }
  return withDebugger(tab.id, async (target) => {
    await trustedClick(target, x, y, { clickCount: Number(args.clickCount || 1) });
    return { x, y, trusted: true };
  });
};

ACTIONS.click_text = async (args) => {
  // Click the visible control whose text or aria-label matches. Open dialogs are searched first.
  const tab = await requireTab(args);
  const texts = Array.isArray(args.texts) ? args.texts : [args.text || args.label || ""].filter(Boolean);
  if (!texts.length) throw new Error("text required");
  const r = await runInTab(
    tab,
    (wantList, exact, rolePrefer) => {
      const wants = wantList.map((t) => String(t || "").toLowerCase().replace(/\s+/g, " ").trim()).filter(Boolean);
      const isVisible = (el) => {
        const st = getComputedStyle(el);
        if (st.display === "none" || st.visibility === "hidden" || st.opacity === "0") return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2 && r.bottom > 0 && r.top < innerHeight + 20;
      };
      const textOf = (el) => (el.innerText || el.textContent || el.getAttribute("aria-label") || el.value || "").replace(/\s+/g, " ").trim();
      const dialogs = [...document.querySelectorAll("[role='dialog'], [aria-modal='true'], dialog[open]")].filter(isVisible);
      const selectors = ["button", "[role='button']", "a", "[role='link']", "[role='menuitem']", "[role='tab']", "[role='option']", "input[type=submit]", "input[type=button]", "label", "summary", "span", "div", "[aria-label]"];
      const collect = (roots) => {
        const seen = new Set();
        const cands = [];
        for (const root of roots) {
          for (const sel of selectors) {
            for (const el of root.querySelectorAll(sel)) {
              if (seen.has(el) || !isVisible(el)) continue;
              seen.add(el);
              const label = textOf(el);
              const aria = (el.getAttribute("aria-label") || "").trim();
              if (!(label || aria) || (label || aria).length > 80) continue;
              cands.push({ el, label: label || aria, aria, tag: el.tagName, inDialog: !!el.closest("[role='dialog'], [aria-modal='true'], dialog") });
            }
          }
        }
        return cands;
      };
      let cands = collect(dialogs.length ? dialogs : [document]);
      if (!cands.length && dialogs.length) cands = collect([document]);
      const score = (c, want) => {
        const L = c.label.toLowerCase();
        const A = c.aria.toLowerCase();
        if (L === want || A === want) return exact ? 100 : 90;
        if (exact) return 0;
        if (L.startsWith(want) || A.startsWith(want)) return 70;
        if (L.includes(want) || A.includes(want)) return 50;
        return 0;
      };
      let best = null;
      let bestScore = 0;
      let bestWant = "";
      for (const want of wants) {
        for (const c of cands) {
          let s = score(c, want);
          if (!s) continue;
          const role = (c.el.getAttribute("role") || "").toLowerCase();
          if (rolePrefer && (role === "button" || c.tag === "BUTTON" || c.tag === "A")) s += 15;
          if (c.inDialog) s += 25;
          s += Math.max(0, 20 - Math.min(20, c.label.length / 2));
          if (s > bestScore) {
            bestScore = s;
            best = c;
            bestWant = want;
          }
        }
      }
      if (!best) return { ok: false, error: "no match for " + wants.join(" | "), sample: cands.slice(0, 25).map((c) => c.label.slice(0, 60)) };
      let target = best.el;
      if (target.tagName === "SPAN" || target.tagName === "DIV") {
        const btn = target.closest("button, [role='button'], a, label");
        if (btn) target = btn;
      }
      target.scrollIntoView({ block: "center", behavior: "instant" });
      target.click();
      return { ok: true, matched: best.label.slice(0, 80), want: bestWant, tag: target.tagName, score: bestScore };
    },
    [texts, !!args.exact, args.rolePrefer !== false]
  );
  if (!r || r.ok === false) throw new Error((r && r.error) + (r && r.sample ? " ; visible: " + JSON.stringify(r.sample) : ""));
  return r;
};

ACTIONS.ax_click = async (args) => {
  // Click the node whose accessible name matches (exact, prefix, or contains), via the DOM.
  const tab = await requireTab(args);
  const texts = Array.isArray(args.texts) ? args.texts : [args.text || args.name || args.label || ""].filter(Boolean);
  if (!texts.length) throw new Error("text required");
  const roles = Array.isArray(args.roles) ? args.roles : ["button", "link", "menuitem", "tab", "option", "checkbox", "radio"];
  const nth = Math.max(0, Number(args.nth || 0));
  return withDebugger(tab.id, async (target) => {
    await cdp(target, "Accessibility.enable").catch(() => {});
    await cdp(target, "DOM.enable");
    const tree = await cdp(target, "Accessibility.getFullAXTree");
    const nodes = (tree && tree.nodes) || [];
    const wants = texts.map((t) => String(t).toLowerCase().trim());
    const ranked = [];
    for (const n of nodes) {
      if (n.ignored || !n.backendDOMNodeId) continue;
      const role = (n.role && n.role.value) || "";
      const name = String((n.name && n.name.value) || "").trim().toLowerCase();
      if (!name) continue;
      for (const w of wants) {
        let s = 0;
        if (name === w) s = 100;
        else if (!args.exact && name.startsWith(w)) s = 80;
        else if (!args.exact && name.includes(w)) s = 50;
        if (!s) continue;
        if (roles.includes(role)) s += 20;
        if (role === "button") s += 10;
        ranked.push({ n, s });
        break;
      }
    }
    const bestScore = ranked.reduce((m, item) => Math.max(m, item.s), 0);
    const tied = ranked.filter((item) => item.s === bestScore);
    const best = tied[nth] ? tied[nth].n : null;
    if (!best) {
      const sample = nodes.filter((n) => n.name && n.name.value).slice(0, 30).map((n) => ((n.role && n.role.value) || "") + ":" + n.name.value);
      throw new Error("no accessible node named " + wants.join(" | ") + " ; sample " + JSON.stringify(sample));
    }
    const resolved = await cdp(target, "DOM.resolveNode", { backendNodeId: best.backendDOMNodeId });
    const objectId = resolved && resolved.object && resolved.object.objectId;
    if (!objectId) throw new Error("could not resolve the node");
    await cdp(target, "Runtime.callFunctionOn", {
      objectId,
      functionDeclaration: "function() { this.scrollIntoView({block: 'center', behavior: 'instant'}); this.click(); return true; }",
      returnByValue: true,
    });
    return { name: (best.name && best.name.value) || "", role: (best.role && best.role.value) || "", score: bestScore, nth, matches: tied.length };
  });
};

ACTIONS.ax_press = async (args) => {
  // Trusted click on the control with this exact accessible name (shadow DOM included).
  // Refuses a disabled control unless allowDisabled.
  const tab = await requireTab(args);
  const { names, roles, nth } = axArgs(args);
  return withDebugger(tab.id, async (target) => {
    const found = await axFindNode(target, names, roles.length ? roles : ["button", "link", "menuitem", "tab", "checkbox", "radio", "option", "switch"], nth);
    if (!found.node) throw new Error(`no control named ${names.join(" | ")}; sample ${JSON.stringify(found.sample)}`);
    if (found.disabled && !args.allowDisabled) throw new Error(`${names[0]} is disabled`);
    const c = await axNodeCenter(target, found.node.backendDOMNodeId);
    if (!(c.w > 1 && c.h > 1)) throw new Error(`${names[0]} is not visible`);
    await trustedClick(target, c.x, c.y);
    return {
      name: String((found.node.name && found.node.name.value) || ""),
      role: String((found.node.role && found.node.role.value) || ""),
      matches: found.matches,
      x: Math.round(c.x),
      y: Math.round(c.y),
    };
  });
};

ACTIONS.wheel = async (args) => {
  const tab = await requireTab(args);
  const x = Number(args.x ?? 400);
  const y = Number(args.y ?? 300);
  const deltaX = Number(args.deltaX || 0);
  const deltaY = Number(args.deltaY ?? 480);
  return withDebugger(tab.id, async (target) => {
    await cdp(target, "Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX, deltaY, pointerType: "mouse" });
    return { x, y, deltaX, deltaY };
  });
};

// ── typing ───────────────────────────────────────────────

/** Mark an element in the page (by selector, placeholder/label hint, or first editable) and focus it. */
async function findEditable(tab, args) {
  const selectors = Array.isArray(args.selectors) ? args.selectors : args.selector ? [args.selector] : [];
  const hints = [args.placeholder, args.label, args.ariaHint, ...(Array.isArray(args.ariaHints) ? args.ariaHints : [])]
    .map((s) => String(s || "").toLowerCase().trim())
    .filter(Boolean);
  const found = await runInTab(
    tab,
    (sels, hints, index) => {
      const vis = (el) => {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        return r.width >= 4 && r.height >= 4 && st.display !== "none" && st.visibility !== "hidden";
      };
      const deep = (sel) => {
        const out = [];
        const visit = (root) => {
          for (const el of root.querySelectorAll(sel)) out.push(el);
          for (const el of root.querySelectorAll("*")) if (el.shadowRoot) visit(el.shadowRoot);
        };
        visit(document);
        return out;
      };
      const pick = (el, via) => {
        if (!el || !vis(el)) return null;
        el.scrollIntoView({ block: "center", behavior: "instant" });
        el.focus();
        const mark = "orbital-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);
        el.setAttribute("data-orbital-target", mark);
        const r = el.getBoundingClientRect();
        return {
          ok: true,
          via,
          mark,
          tag: el.tagName,
          editable: !!el.isContentEditable,
          x: r.left + r.width / 2,
          y: r.top + r.height / 2,
          w: r.width,
          h: r.height,
        };
      };
      for (const s of sels) {
        let list;
        try {
          list = deep(s);
        } catch (e) {
          return { ok: false, error: "bad selector: " + s };
        }
        const hit = pick(list[index || 0], s);
        if (hit) return hit;
      }
      if (sels.length && !hints.length) return { ok: false, error: "not found: " + sels.join(", ") };
      const editables = deep("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea, [contenteditable='true'], [contenteditable=''], [role='textbox']");
      for (const h of hints) {
        for (const el of editables) {
          const text = [el.getAttribute("aria-label"), el.getAttribute("aria-placeholder"), el.getAttribute("placeholder"), el.getAttribute("name"), el.labels && el.labels[0] && el.labels[0].innerText]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          if (text.includes(h)) {
            const hit = pick(el, "hint:" + h);
            if (hit) return hit;
          }
        }
      }
      if (hints.length) return { ok: false, error: "no field matching " + hints.join(" | ") };
      const active = document.activeElement;
      if (active && active !== document.body && (active.isContentEditable || /INPUT|TEXTAREA/.test(active.tagName))) {
        const hit = pick(active, "focused");
        if (hit) return hit;
      }
      for (const el of editables) {
        const hit = pick(el, "first-editable");
        if (hit) return hit;
      }
      return { ok: false, error: "no editable field on the page" };
    },
    [selectors, hints, Number(args.index || 0)]
  );
  if (!found || !found.ok) throw new Error((found && found.error) || "field not found");
  return found;
}

async function readMarked(tab, mark) {
  return runInTab(
    tab,
    (mark) => {
      const byMark = (mark) => { const sel = `[data-orbital-target="${mark}"]`; const visit = (root) => { const hit = root.querySelector(sel); if (hit) return hit; for (const e of root.querySelectorAll("*")) if (e.shadowRoot) { const h = visit(e.shadowRoot); if (h) return h; } return null; }; return visit(document); };
      const el = byMark(mark);
      if (!el) return "";
      el.removeAttribute("data-orbital-target");
      return typeof el.value === "string" ? (el.type === "password" ? "" : el.value) : el.innerText || el.textContent || "";
    },
    [mark]
  );
}

async function selectAllAndDelete(target) {
  await cdp(target, "Input.dispatchKeyEvent", {
    type: "rawKeyDown",
    key: "a",
    code: "KeyA",
    windowsVirtualKeyCode: 65,
    modifiers: state.isMac ? 4 : 2,
    commands: ["selectAll"],
  });
  await cdp(target, "Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", windowsVirtualKeyCode: 65, modifiers: state.isMac ? 4 : 2 });
  await keyPress(target, "Backspace");
  await sleep(80);
}

/** Trusted typing into a field found by findEditable(). */
async function typeTrusted(tab, args, field) {
  const text = String(args.text ?? "");
  const human = !!args.human;
  return withDebugger(tab.id, async (target) => {
    await sleep(200);
    const again = await runInTab(
      tab,
      (mark) => {
        const byMark = (mark) => { const sel = `[data-orbital-target="${mark}"]`; const visit = (root) => { const hit = root.querySelector(sel); if (hit) return hit; for (const e of root.querySelectorAll("*")) if (e.shadowRoot) { const h = visit(e.shadowRoot); if (h) return h; } return null; }; return visit(document); };
        const el = byMark(mark);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
      },
      [field.mark]
    );
    const at = again || field;
    if (at.w > 1 && at.h > 1 && args.clickFirst !== false) await trustedClick(target, at.x, at.y);
    await sleep(human ? rand(120, 220) : 60);
    if (args.clear) await selectAllAndDelete(target);
    const typed = await insertText(target, text, { human, minDelayMs: clamp(args.minDelayMs, 10, 400, 30), maxDelayMs: clamp(args.maxDelayMs, 10, 800, 110) });
    await sleep(150);
    return typed;
  });
}

ACTIONS.type = async (args) => {
  // Put text in a field. Default: set the value the way a framework (React, Vue) notices.
  // human: true types one key at a time with trusted input and pauses; trusted: true types
  // with trusted input at full speed. clear: true empties the field first.
  const tab = await requireTab(args);
  const text = String(args.text ?? "");
  const field = await findEditable(tab, args);
  if (args.human || args.trusted) {
    const typed = await typeTrusted(tab, args, field);
    return { typed, via: field.via, human: !!args.human, value: (await readMarked(tab, field.mark)).slice(0, 500) };
  }
  const r = await runInTab(
    tab,
    (mark, value, clear) => {
      const byMark = (mark) => { const sel = `[data-orbital-target="${mark}"]`; const visit = (root) => { const hit = root.querySelector(sel); if (hit) return hit; for (const e of root.querySelectorAll("*")) if (e.shadowRoot) { const h = visit(e.shadowRoot); if (h) return h; } return null; }; return visit(document); };
      const el = byMark(mark);
      if (!el) return { ok: false, error: "field went away" };
      el.removeAttribute("data-orbital-target");
      el.focus();
      if (el.isContentEditable) {
        if (clear) {
          const range = document.createRange();
          range.selectNodeContents(el);
          const sel = getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
        }
        document.execCommand("insertText", false, value);
        return { ok: true, value: (el.innerText || "").slice(0, 500) };
      }
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, "value");
      const next = (clear ? "" : el.value || "") + value;
      if (desc && desc.set) desc.set.call(el, next);
      else el.value = next;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true, value: String(el.type === "password" ? "" : el.value).slice(0, 500) };
    },
    [field.mark, text, !!args.clear]
  );
  if (!r || r.ok === false) throw new Error((r && r.error) || "type failed");
  return { typed: text.length, via: field.via, human: false, value: r.value };
};

ACTIONS.type_human = (args) => ACTIONS.type({ ...args, human: true, clear: args.clear !== false });

ACTIONS.set_editable = async (args) => {
  // Replace the whole content of a field or contenteditable (rich editors, auto-filled titles).
  const tab = await requireTab(args);
  const field = await findEditable(tab, args);
  const r = await runInTab(
    tab,
    (mark, desired) => {
      const byMark = (mark) => { const sel = `[data-orbital-target="${mark}"]`; const visit = (root) => { const hit = root.querySelector(sel); if (hit) return hit; for (const e of root.querySelectorAll("*")) if (e.shadowRoot) { const h = visit(e.shadowRoot); if (h) return h; } return null; }; return visit(document); };
      const el = byMark(mark);
      if (!el) return { ok: false, error: "field went away" };
      el.removeAttribute("data-orbital-target");
      const before = (el.innerText || el.textContent || el.value || "").trim().slice(0, 200);
      el.focus();
      if (!el.isContentEditable && "value" in el) {
        const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, "value");
        if (desc && desc.set) desc.set.call(el, desired);
        else el.value = desired;
      } else {
        try {
          const range = document.createRange();
          range.selectNodeContents(el);
          const sel = getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
        } catch (_) {}
        let inserted = false;
        try {
          inserted = document.execCommand("insertText", false, desired);
        } catch (_) {}
        if (!inserted || (el.innerText || "").trim() !== desired.trim()) el.textContent = desired;
      }
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: desired, inputType: "insertText" }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      const after = (el.innerText || el.textContent || el.value || "").trim().slice(0, 200);
      return { ok: true, exact: after === desired.trim().slice(0, 200), before, after };
    },
    [field.mark, String(args.text ?? "")]
  );
  if (!r || r.ok === false) throw new Error((r && r.error) || "set_editable failed");
  return { ...r, via: field.via };
};

ACTIONS.ax_type = async (args) => {
  // Type into the field with this exact accessible name (shadow DOM included). clear defaults
  // to true; human defaults to true. Returns what the field holds afterwards.
  const tab = await requireTab(args);
  const { names, roles, nth } = axArgs(args);
  const text = String(args.text ?? "");
  return withDebugger(tab.id, async (target) => {
    const found = await axFindNode(target, names, roles.length ? roles : ["textbox", "searchbox", "combobox"], nth);
    if (!found.node) throw new Error(`no field named ${names.join(" | ")}; sample ${JSON.stringify(found.sample)}`);
    if (found.disabled) throw new Error(`field ${names[0]} is disabled`);
    const backendNodeId = found.node.backendDOMNodeId;
    await cdp(target, "DOM.focus", { backendNodeId }).catch(() => {});
    const c = await axNodeCenter(target, backendNodeId);
    if (c.w > 1 && c.h > 1) await trustedClick(target, c.x, c.y);
    await sleep(150);
    if (args.clear !== false) await selectAllAndDelete(target);
    const typed = await insertText(target, text, { human: args.human !== false });
    await sleep(200);
    return {
      name: String((found.node.name && found.node.name.value) || ""),
      role: String((found.node.role && found.node.role.value) || ""),
      typed,
      value: await axNodeText(target, backendNodeId),
    };
  });
};

ACTIONS.hotkey = async (args) => {
  // A key or combination with trusted input: "Enter", "Escape", "Meta+Enter", "ctrl+shift+k".
  // Optional focusSelector focuses an element first.
  const tab = await requireTab(args);
  let extra = 0;
  if (args.alt) extra |= MODIFIER_BITS.alt;
  if (args.ctrl) extra |= MODIFIER_BITS.ctrl;
  if (args.meta) extra |= MODIFIER_BITS.meta;
  if (args.shift) extra |= MODIFIER_BITS.shift;
  if (args.focusSelector) await findEditable(tab, { selector: args.focusSelector });
  return withDebugger(tab.id, async (target) => {
    const times = clamp(args.repeat, 1, 100, 1);
    let k = null;
    for (let i = 0; i < times; i++) k = await keyPress(target, args.key || "Enter", extra);
    return { key: k.key, modifiers: k.modifiers, repeat: times };
  });
};

// ── files ────────────────────────────────────────────────

ACTIONS.upload_file = async (args) => {
  // Put local file(s) (paths on THIS computer) into a page's file input.
  // With clickSelector or clickLabels: intercept the file chooser, click the page's own upload
  // button with a trusted click, and answer the chooser (the path a person's pick takes; some
  // sites only accept that). Otherwise, or if no chooser opens, set the files on the input
  // matching `selector` (default input[type=file]) directly. No native dialog ever opens.
  const tab = await requireTab(args);
  const files = (Array.isArray(args.paths) ? args.paths : [args.path]).map((s) => String(s || "").trim()).filter(Boolean);
  if (!files.length) throw new Error("path required");
  const selector = String(args.selector || "input[type=file]");
  const clickSelector = args.clickSelector ? String(args.clickSelector) : "";
  const clickLabels = Array.isArray(args.clickLabels) ? args.clickLabels : args.clickLabel ? [args.clickLabel] : [];
  const timeoutMs = clamp(args.timeoutMs, 1000, 60000, 10000);
  const ext = (files[0].split(".").pop() || "").toLowerCase();
  return withDebugger(tab.id, async (target) => {
    await cdp(target, "Page.enable");
    await cdp(target, "DOM.enable");
    let click = null;
    if (clickSelector || clickLabels.length) {
      let onEv = null;
      const chooser = new Promise((resolve) => {
        const t = setTimeout(() => resolve(null), timeoutMs);
        onEv = (source, method, params) => {
          if (!source || source.tabId !== tab.id || method !== "Page.fileChooserOpened") return;
          clearTimeout(t);
          resolve(params || {});
        };
        chrome.debugger.onEvent.addListener(onEv);
      });
      await cdp(target, "Page.setInterceptFileChooserDialog", { enabled: true });
      try {
        click = await runInTab(
          tab,
          (sel, labels) => {
            const norm = (s) => String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
            const isVis = (el) => {
              const st = getComputedStyle(el);
              const r = el.getBoundingClientRect();
              return st.display !== "none" && st.visibility !== "hidden" && r.width > 1 && r.height > 1;
            };
            const center = (el, how) => {
              el.scrollIntoView({ block: "center", behavior: "instant" });
              const r = el.getBoundingClientRect();
              return { ok: true, how, tag: el.tagName, text: norm(el.innerText || el.getAttribute("aria-label")).slice(0, 60), x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
            };
            if (sel) {
              const el = document.querySelector(sel);
              return el && isVis(el) ? center(el, "selector") : { ok: false, error: "click target not found: " + sel };
            }
            const wants = labels.map(norm).filter(Boolean);
            const dialogs = [...document.querySelectorAll("[role='dialog'], [aria-modal='true'], dialog[open]")];
            const scopes = dialogs.length ? [...dialogs, document] : [document];
            const kinds = "button, [role='button'], [role='menuitem'], [role='option'], a, label, input[type=button]";
            for (const exact of [true, false]) {
              for (const scope of scopes) {
                for (const el of scope.querySelectorAll(kinds)) {
                  if (!isVis(el)) continue;
                  const t = norm(el.innerText || el.textContent || el.getAttribute("aria-label") || el.value);
                  if (wants.some((w) => (exact ? t === w : t.includes(w)))) return center(el, exact ? "label-exact" : "label-contains");
                }
              }
            }
            return { ok: false, error: "no upload button labelled " + labels.join(" | ") };
          },
          [clickSelector, clickLabels]
        );
        if (click && click.ok) {
          await trustedClick(target, click.x, click.y);
          const params = await chooser;
          if (params && params.backendNodeId) {
            await cdp(target, "DOM.setFileInputFiles", { backendNodeId: params.backendNodeId, files });
            return { ok: true, mode: "chooser", files, click };
          }
        }
      } finally {
        if (onEv) chrome.debugger.onEvent.removeListener(onEv);
        await cdp(target, "Page.setInterceptFileChooserDialog", { enabled: false }).catch(() => {});
      }
    }
    // Direct: mark the best matching file input (shadow DOM included) and set its files.
    const found = await runInTab(
      tab,
      (sel, ext) => {
        const out = [];
        const visit = (root) => {
          try {
            for (const el of root.querySelectorAll(sel)) out.push(el);
          } catch (_) {}
          for (const el of root.querySelectorAll("input[type=file]")) if (!out.includes(el)) out.push(el);
          for (const el of root.querySelectorAll("*")) if (el.shadowRoot) visit(el.shadowRoot);
        };
        visit(document);
        const inputs = out.filter((el) => el.tagName === "INPUT" && el.type === "file");
        if (!inputs.length) return { ok: false, error: "no file input on the page (try clickLabels or clickSelector)" };
        const family = /^(mp4|mov|webm|mkv|avi|m4v)$/.test(ext) ? "video" : /^(png|jpe?g|gif|webp|heic|svg|bmp)$/.test(ext) ? "image" : /^(mp3|wav|m4a|aac|ogg|flac)$/.test(ext) ? "audio" : "";
        const score = (el) => {
          let s = 0;
          const accept = (el.getAttribute("accept") || "").toLowerCase();
          if (!accept || accept.includes("*/*")) s += 5;
          if (ext && accept.includes("." + ext)) s += 50;
          if (family && accept.includes(family)) s += 40;
          if (el.closest("[role='dialog'], [aria-modal='true'], dialog")) s += 30;
          if (el.matches(sel)) s += 20;
          return s;
        };
        inputs.sort((a, b) => score(b) - score(a));
        const mark = "orbital-upload-" + Date.now();
        inputs[0].setAttribute("data-orbital-upload", mark);
        return { ok: true, mark, accept: inputs[0].getAttribute("accept") || "", multiple: !!inputs[0].multiple, count: inputs.length };
      },
      [selector, ext]
    );
    if (!found || !found.ok) throw new Error((found && found.error) + (click && !click.ok ? " ; " + click.error : ""));
    if (files.length > 1 && !found.multiple) throw new Error("that file input takes one file");
    const evaluated = await cdp(target, "Runtime.evaluate", {
      expression: `(() => { const find = (root) => { const hit = root.querySelector('[data-orbital-upload="${found.mark}"]'); if (hit) return hit; for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) { const h = find(el.shadowRoot); if (h) return h; } } return null; }; return find(document); })()`,
    });
    const objectId = evaluated && evaluated.result && evaluated.result.objectId;
    if (!objectId) throw new Error("could not resolve the file input");
    await cdp(target, "DOM.setFileInputFiles", { objectId, files });
    await cdp(target, "Runtime.releaseObject", { objectId }).catch(() => {});
    return { ok: true, mode: "direct", files, accept: found.accept, inputs: found.count, click };
  });
};

ACTIONS.ax_upload = async (args) => {
  // Upload through the control with this exact accessible name (shadow DOM included):
  // intercept the chooser, trusted-click the control, answer the chooser with `path`.
  const tab = await requireTab(args);
  const { names, roles, nth } = axArgs(args);
  const files = (Array.isArray(args.paths) ? args.paths : [args.path]).map((s) => String(s || "").trim()).filter(Boolean);
  if (!files.length) throw new Error("path required");
  return withDebugger(tab.id, async (target) => {
    let onEv = null;
    try {
      await cdp(target, "Page.enable");
      const found = await axFindNode(target, names, roles.length ? roles : ["button", "link"], nth);
      if (!found.node) throw new Error(`no control named ${names.join(" | ")}; sample ${JSON.stringify(found.sample)}`);
      const chooser = new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error("the file chooser did not open")), clamp(args.timeoutMs, 1000, 60000, 12000));
        onEv = (source, method, params) => {
          if (!source || source.tabId !== tab.id || method !== "Page.fileChooserOpened") return;
          clearTimeout(t);
          resolve(params || {});
        };
        chrome.debugger.onEvent.addListener(onEv);
      });
      await cdp(target, "Page.setInterceptFileChooserDialog", { enabled: true });
      const c = await axNodeCenter(target, found.node.backendDOMNodeId);
      if (!(c.w > 1 && c.h > 1)) throw new Error(`${names[0]} is not visible`);
      await trustedClick(target, c.x, c.y);
      const params = await chooser;
      if (!params.backendNodeId) throw new Error("the chooser named no file input");
      await cdp(target, "DOM.setFileInputFiles", { backendNodeId: params.backendNodeId, files });
      return { ok: true, files, mode: params.mode || "", via: "ax-chooser" };
    } finally {
      if (onEv) chrome.debugger.onEvent.removeListener(onEv);
      await cdp(target, "Page.setInterceptFileChooserDialog", { enabled: false }).catch(() => {});
    }
  });
};

// ── waiting ──────────────────────────────────────────────

ACTIONS.wait = async (args) => {
  const ms = clamp(args.ms, 0, 120000, 1000);
  await sleep(ms);
  return { waited: ms };
};

ACTIONS.wait_for = async (args) => {
  // Wait until text appears (page text or aria-labels), a selector matches a visible element,
  // or the URL contains a string. gone: true waits for it to disappear instead.
  const tab = await requireTab(args);
  const texts = Array.isArray(args.texts) ? args.texts : [args.text || ""].filter(Boolean);
  const selector = args.selector ? String(args.selector) : "";
  const urlPart = args.url ? String(args.url) : "";
  if (!texts.length && !selector && !urlPart) throw new Error("text, selector or url required");
  const timeoutMs = clamp(args.timeoutMs, 200, 300000, 15000);
  const gone = !!args.gone;
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const fresh = await chrome.tabs.get(tab.id);
      let hit = null;
      if (urlPart) hit = fresh.url && fresh.url.includes(urlPart) ? { found: "url", url: fresh.url } : null;
      if (!hit && (texts.length || selector)) {
        hit = await runInTab(
          tab,
          (wantList, sel) => {
            if (sel) {
              let el = null;
              try {
                el = document.querySelector(sel);
              } catch (e) {
                return { error: "bad selector: " + sel };
              }
              if (el) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return { found: "selector", selector: sel };
              }
            }
            const wants = wantList.map((t) => String(t).toLowerCase());
            if (wants.length) {
              const body = ((document.body && document.body.innerText) || "").toLowerCase();
              for (const w of wants) if (w && body.includes(w)) return { found: "text", text: w };
              for (const el of document.querySelectorAll("[aria-label]")) {
                const a = (el.getAttribute("aria-label") || "").toLowerCase();
                for (const w of wants) if (w && a.includes(w)) return { found: "text", text: w, via: "aria" };
              }
            }
            return null;
          },
          [texts, selector]
        );
        if (hit && hit.error) throw new Error(hit.error);
      }
      if (gone ? !hit : !!hit) return { ...(hit || { found: "gone" }), gone, waitedMs: Date.now() - t0, url: fresh.url };
    } catch (e) {
      if (/bad selector/.test(errorText(e))) throw e;
      // the page is navigating; try again
    }
    await sleep(400);
  }
  const what = [texts.length ? "text " + JSON.stringify(texts) : "", selector ? "selector " + selector : "", urlPart ? "url " + urlPart : ""].filter(Boolean).join(", ");
  throw new Error(`wait_for timed out after ${timeoutMs} ms waiting for ${gone ? "the end of " : ""}${what}`);
};
