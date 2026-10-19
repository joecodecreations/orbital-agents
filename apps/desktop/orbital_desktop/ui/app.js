/* Orbital Agents desktop UI. Every hub call goes through the Python bridge (bridge.call), which holds
   the session token; this page never sees it.

   Same structure as the iPhone/Mac app: five fixed sections (Home, System, Agent, Tracker, Alerts),
   then the hub's navigation document (GET /v1/nav, orbital.nav.v1): extra items after the fixed five and
   the sidebar tree (or the default tree: each space, then its sections). Spaces come from GET /v1/catalog.
   Hash routes:
     #/home   #/system[/satellites|automations|manage|settings]   #/machine/<id>   #/job/<machine>/<job>
     #/agent[/jobs]   #/tracker[/<item>]   #/alerts   #/spaces   #/space/<id>[/<area>[/<platform>]]   #/browse
     #/section/<id>[/<node>|-[/<tab>]]   #/nav/<key>   #/tab/<id>                                     */
"use strict";

const POLL_MS = 10000;
const JOB_POLL_MS = 2500;

// Bridge: pywebview's js_api in the app; POST /api/<name> with the per-run key in --browser mode.
const bridge = (() => {
  const key = new URLSearchParams(location.search).get("key");
  if (key) {
    return {
      ready: Promise.resolve(),
      async call(name, ...args) {
        try {
          const r = await fetch("/api/" + name, { method: "POST", headers: { "Content-Type": "application/json", "X-Orbital-Key": key }, body: JSON.stringify(args) });
          return await r.json();
        } catch (e) { return { ok: false, error: "The desktop app stopped (" + e.message + ")" }; }
      },
    };
  }
  const ready = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) resolve();
    else window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
  return {
    ready,
    async call(name, ...args) {
      await ready;
      try { return await window.pywebview.api[name](...args); }
      catch (e) { return { ok: false, error: String(e && e.message || e) }; }
    },
  };
})();
const call = (name, ...args) => bridge.call(name, ...args);

// Per-viewer conveniences (last area, open groups). Never required: storage can throw or be empty.
const prefs = {
  get(k, d) { try { const v = localStorage.getItem("orbital." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("orbital." + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

const state = {
  session: null,
  satellites: [], satFetchedAt: 0,
  jobs: [], jobsLoaded: false,
  machineJobs: {},         // id -> {jobs, error, at}
  alerts: [], alertsMode: "pending",
  jobsFilter: "all",
  live: null, liveDetail: "",
  hubError: "",
  job: null, jobLog: null, jobError: "",
  runForm: {},             // satellite id -> draft
  // navigation, spaces, sections
  nav: { schema: "orbital.nav.v1", tabs: [], sidebar: null }, navDefault: true, navLoaded: false,
  catalog: null, sections: {}, dir: Model.buildDirectory({}, {}),
  // system / agent / tracker
  hubInfo: null, appInfo: null,
  feeds: null, feedsError: "", feedGroup: "",
  tracker: null, trackerFilter: "active", trackerSearch: "", trackerItem: null,
  desk: null, deskSending: false, deskError: "",
};

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);

function bytes(n) {
  if (n == null || isNaN(n)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0; n = Number(n);
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return (n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
}
function duration(s) {
  if (s == null || isNaN(s)) return "–";
  s = Math.max(0, Math.round(s));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}
function parseTime(v) {
  if (v == null || v === "") return null;
  if (typeof v === "number") return v > 1e12 ? v : v * 1000;
  const t = Date.parse(v);
  return isNaN(t) ? null : t;
}
function ago(v) {
  const t = parseTime(v);
  if (t == null) return "never";
  const s = (Date.now() - t) / 1000;
  if (s < 5) return "just now";
  if (s < 0) return "in " + duration(-s);
  return duration(s) + " ago";
}
function until(v) {
  const t = parseTime(v);
  if (t == null) return "–";
  const s = (t - Date.now()) / 1000;
  return s <= 0 ? "due now" : "in " + duration(s);
}
function when(v) {
  const t = parseTime(v);
  if (t == null) return "–";
  const d = new Date(t);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
               : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
const agoSpan = (v) => `<span data-ago="${esc(v ?? "")}">${esc(ago(v))}</span>`;
const untilSpan = (v) => `<span data-until="${esc(v ?? "")}">${esc(until(v))}</span>`;
const level = (pct, warn = 75, bad = 90) => (pct >= bad ? "bad" : pct >= warn ? "warn" : "ok");
const osName = (os) => ({ macos: "macOS", darwin: "macOS", windows: "Windows", win32: "Windows", linux: "Linux" }[String(os || "").toLowerCase()] || (os || "Unknown OS"));
const isRunning = (j) => ["queued", "running"].includes(String(j && j.status));
const jobId = (j) => j.id || j.job_id;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function meter(label, pct, val, opts = {}) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return `<div class="meter"><div class="top"><span class="label">${esc(label)}</span><span class="val">${val}</span></div>
    <div class="bar ${opts.big ? "big " : ""}${level(p, opts.warn, opts.bad)}"><span style="width:${p.toFixed(1)}%"></span></div></div>`;
}
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "bad" ? 7000 : 4000);
}
function header(eyebrow, title, sub = "", right = "", accent = null) {
  return `<div class="page-head" ${accentStyle(accent)}><div class="grow">${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ""}<h1>${title}</h1>
    ${sub ? `<div class="sub">${sub}</div>` : ""}</div>${right}</div>`;
}
const seg = (id, options, value) => `<div class="seg" id="${id}">${options.map(([v, label]) => `<button data-v="${esc(v)}" class="${v === value ? "on" : ""}">${label}</button>`).join("")}</div>`;
const systemName = () => (state.hubInfo && state.hubInfo.name) || "Orbital Agents";

// ---------- browser controller ----------
const BROWSER = {
  connected: { label: "Chrome connected", cls: "ok" },
  no_extension: { label: "Bridge up, extension not connected", cls: "warn" },
  bridge_down: { label: "Bridge not running", cls: "bad" },
  disabled: { label: "Browser control off", cls: "" },
};
function browserInfo(sat) {
  const b = sat.browser_status;
  if (!b || !b.state) {
    const caps = sat.capabilities || {};
    return { label: caps.browser ? "Browser status not reported" : "No browser bridge", cls: "", b: null };
  }
  return { ...(BROWSER[b.state] || { label: b.state, cls: "" }), b };
}
function browserChip(sat) {
  const i = browserInfo(sat);
  return `<span class="badge ${i.cls}" title="Browser controller"><i></i>${esc(i.label)}</span>`;
}

// ---------- metrics ----------
function cpuPct(m) {
  if (!m || !m.load || !m.cpus) return null;
  return Math.min(100, (m.load[0] / m.cpus) * 100);
}
function machineMeters(sat, { allDisks = false } = {}) {
  const m = sat.system_metrics;
  if (!m || !Object.keys(m).length) return `<div class="note">No system metrics yet. This satellite's version may not send them.</div>`;
  const out = [];
  const cpu = cpuPct(m);
  if (cpu != null) out.push(meter("CPU load", cpu, `${m.load[0].toFixed(2)} · ${m.cpus} cores`));
  else if (m.cpus) out.push(`<div class="meter"><div class="top"><span class="label">CPU</span><span class="val">${m.cpus} cores</span></div></div>`);
  if (m.memory && m.memory.percent != null) out.push(meter("Memory", m.memory.percent, `${m.memory.percent.toFixed(0)}% · ${bytes(m.memory.used)} / ${bytes(m.memory.total)}`));
  const disks = m.disks || [];
  const shown = allDisks ? disks : disks.slice(0, 2);
  for (const d of shown) out.push(meter(`Disk ${d.mount || ""}`, d.percent, `${(d.percent ?? 0).toFixed(0)}% · ${bytes(d.free)} free of ${bytes(d.total)}`, { big: allDisks }));
  if (!allDisks && disks.length > 2) out.push(`<div class="note">+${disks.length - 2} more disk${disks.length - 2 > 1 ? "s" : ""}</div>`);
  return `<div class="meters">${out.join("")}</div>`;
}

// ---------- data ----------
async function loadSatellites() {
  const r = await call("satellites");
  if (r.ok) { state.satellites = r.satellites || []; state.satFetchedAt = Date.now(); setHubError(""); }
  else if (r.status !== 401) setHubError(r.error);
  return r;
}
async function loadJobs() {
  const r = await call("jobs");
  if (r.ok) { state.jobs = r.jobs || []; state.jobsLoaded = true; }
  return r;
}
async function loadMachineJobs(id) {
  const r = await call("machine_jobs", id);
  state.machineJobs[id] = r.ok ? { jobs: r.jobs || [], at: Date.now() } : { jobs: null, error: r.error, at: Date.now() };
  return r;
}
async function loadAlerts() {
  const r = await call("alerts", state.alertsMode);
  if (r.ok) state.alerts = r.alerts || [];
  updateAlertCount();
  return r;
}
async function loadNav() {
  const r = await call("nav");
  if (r.ok) { state.nav = r.nav; state.navDefault = r.default; state.navLoaded = true; }
  return r;
}
let catalogBusy = null;
/** The catalog, then every section whose revision changed (four at a time), then the directory. */
function loadCatalog() {
  if (catalogBusy) return catalogBusy;
  catalogBusy = (async () => {
    const r = await call("catalog");
    if (!r.ok) return r;
    state.catalog = { sections: r.sections, spaces: r.spaces };
    const ids = new Set(r.sections.map((s) => s.id));
    for (const id of Object.keys(state.sections)) if (!ids.has(id)) delete state.sections[id];
    const stale = r.sections.filter((s) => !state.sections[s.id] || state.sections[s.id].revision !== s.revision).map((s) => s.id);
    rebuildDirectory();
    const queue = [...stale];
    await Promise.all([0, 1, 2, 3].map(async () => {
      while (queue.length) {
        const id = queue.shift();
        const s = await call("section", id);
        if (s.ok) state.sections[id] = s.section;
      }
    }));
    rebuildDirectory();
    return r;
  })().finally(() => { catalogBusy = null; });
  return catalogBusy;
}
function rebuildDirectory() { state.dir = Model.buildDirectory(state.catalog, state.sections); }
async function loadFeeds() {
  const r = await call("feeds");
  if (r.ok) { state.feeds = r; state.feedsError = ""; } else state.feedsError = r.error;
  return r;
}
async function loadTracker() {
  const r = await call("tracker");
  if (r.ok) state.tracker = r;
  return r;
}
async function loadDesk() {
  const r = await call("desk");
  if (r.ok) { state.desk = r; state.deskError = ""; } else state.deskError = r.error;
  return r;
}
function setHubError(msg) { state.hubError = msg || ""; renderBanner(); }

// ---------- chrome: top bar, sidebar ----------
const FIXED = [
  { id: "home", title: "Home", icon: "house.fill", route: "#/home" },
  { id: "system", title: "System", icon: "cpu", route: "#/system" },
  { id: "agent", title: "Agent", icon: "sparkles", route: "#/agent" },
  { id: "tracker", title: "Tracker", icon: "dot.radiowaves.left.and.right", route: "#/tracker" },
  { id: "alerts", title: "Alerts", icon: "bell.fill", route: "#/alerts" },
];
/** Which fixed section a route belongs to, for the highlight. */
function fixedOf(r) {
  if (["machine", "job", "system"].includes(r.name)) return "system";
  if (r.name === "section" && state.dir.systemSections.includes(r.args[0])) return "system";
  return { home: "home", agent: "agent", tracker: "tracker", alerts: "alerts" }[r.name] || "";
}
function renderBanner() {
  const el = $("#banner");
  el.innerHTML = state.hubError ? `Can't load from the hub: ${esc(state.hubError)}. Retrying every ${POLL_MS / 1000}s.` : "";
  el.className = "banner" + (state.hubError ? " bad" : "");
  el.hidden = !state.hubError;
}
function renderLive() {
  const el = $("#live");
  el.className = "live " + (state.live === true ? "on" : state.live === false ? "off" : "");
  $("#live-label").textContent = state.live === true ? "Live" : state.live === false ? "Polling" : "Connecting";
  el.title = state.live === true ? "Live updates connected (websocket /v1/live)" : (state.liveDetail || "Live updates not connected; refreshing every 10 s");
}
const sidebarTree = () => state.nav.sidebar || Model.defaultSidebar(state.dir, state.catalog);
function renderSidebar() {
  const r = route();
  const here = location.hash;
  const active = fixedOf(r);
  const unacked = state.alerts.filter((a) => !a.acked).length;
  const fixed = FIXED.map((f) => `<a href="${f.route}" class="fixed-item ${active === f.id ? "active" : ""}">${icon(f.icon)}<span class="grow">${f.title}</span>
      ${f.id === "alerts" && unacked ? `<span class="count">${unacked > 99 ? "99+" : unacked}</span>` : ""}</a>`).join("");
  const extra = (state.nav.tabs || []).map((t) => {
    const k = "tab:" + t.id;
    const to = t.target ? Model.navRoute(t, k) : "#/tab/" + encodeURIComponent(t.id);
    return `<a href="${esc(t.target && t.target.kind === "link" ? "#" : to)}" ${linkAttrs(t)} class="fixed-item ${here === to || (r.name === "tab" && r.args[0] === t.id) ? "active" : ""}" ${accentStyle(t.accent)}>${icon(t.icon || symbolFor(t))}<span class="grow">${esc(t.title)}</span></a>`;
  }).join("");
  $("#fixed-nav").innerHTML = fixed + (extra ? `<div class="sep"></div>${extra}` : "");
  $("#tree").innerHTML = treeList(sidebarTree(), "side:", 0, here) || `<div class="note" style="padding:8px">Loading spaces…</div>`;
  $("#tree-title").textContent = state.nav.sidebar ? "Navigation" : "Spaces";
  bindTree();
}
function symbolFor(item) {
  const t = item.target;
  if (!t) return "folder.fill";
  return { section: "doc.text", space: "square.stack.3d.up", link: "link", view: { home: "house.fill", agent: "sparkles", tracker: "dot.radiowaves.left.and.right", alerts: "bell.fill", settings: "gearshape.fill", spaces: "square.stack.3d.up.fill" }[t.view] || "cpu" }[t.kind];
}
const linkAttrs = (item) => (item.target && item.target.kind === "link" ? `data-link="${esc(item.target.url)}"` : "");
function treeList(items, prefix, depth, here) {
  return (items || []).map((item) => {
    const k = prefix + (prefix.endsWith(":") ? "" : "/") + item.id;
    const pad = `style="padding-left:${10 + depth * 14}px"`;
    if (!item.target) {
      const open = prefs.get("nav.open." + k, depth === 0);
      return `<div class="tree-group ${open ? "open" : ""}" ${accentStyle(item.accent)}><button class="tree-row group" data-toggle="${esc(k)}" ${pad}>${icon(item.icon || "folder.fill")}
        <span class="grow ellipsis">${esc(item.title)}</span><span class="chev">›</span></button>
        ${open ? treeList(item.children, k, depth + 1, here) : ""}</div>`;
    }
    const to = Model.navRoute(item, k);
    const isLink = item.target.kind === "link";
    return `<a class="tree-row ${!isLink && here === to ? "active" : ""}" href="${esc(isLink ? "#" : to)}" ${linkAttrs(item)} ${pad} ${accentStyle(item.accent)}>${icon(item.icon || symbolFor(item))}<span class="grow ellipsis">${esc(item.title)}</span>${isLink ? `<span class="faint">↗</span>` : ""}</a>`;
  }).join("");
}
function bindTree() {
  document.querySelectorAll("#tree [data-toggle]").forEach((b) => (b.onclick = () => {
    const k = "nav.open." + b.dataset.toggle;
    prefs.set(k, !b.parentElement.classList.contains("open"));
    renderSidebar();
  }));
  document.querySelectorAll("#sidebar [data-link]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openLink(a.dataset.link); }));
  hydrateMedia($("#sidebar"), call);
}
/** orbital:// links open inside the app where they name a page; https:// goes to the system browser. */
function openLink(url) {
  const m = /^orbital:\/\/(section|space|view)\/([^?#]+)/.exec(url || "");
  if (m) {
    const parts = m[2].split("/").map(decodeURIComponent);
    if (m[1] === "section") return go(Model.navRoute({ target: { kind: "section", section: parts[0], node: parts[1], tab: parts[2] } }));
    if (m[1] === "space") return go("#/space/" + encodeURIComponent(parts[0]));
    return go(Model.VIEW_ROUTES[parts[0]] || "#/home");
  }
  if (/^https:\/\//.test(url || "")) return call("open_url", url).then((r) => !r.ok && toast(r.error, "bad"));
  toast("This link only works in the phone app: " + url);
}
function renderChrome() {
  const signedIn = !!(state.session && state.session.signed_in);
  $("#sidebar").hidden = !signedIn;
  $("#session").hidden = !signedIn;
  if (signedIn) {
    const s = state.session;
    $("#hub-name").textContent = `${s.username || "signed in"} @ ${(s.hub || "").replace(/^https?:\/\//, "")}`;
    $("#hub-name").title = `Hub ${s.hub}${s.hub_id ? " (" + s.hub_id + ")" : ""}`;
    $("#brand-name").textContent = systemName();
    renderSidebar();
  }
  renderLive();
}
function updateAlertCount() { if (state.session && state.session.signed_in) renderSidebar(); }

// ---------- routing ----------
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  return { name: parts[0] || "home", args: parts.slice(1) };
}
function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}
function render() {
  renderChrome();
  const view = $("#view");
  if (!state.session || !state.session.signed_in) { view.innerHTML = signinView(); bindSignin(); return; }
  const r = route();
  const scroll = view.scrollTop;
  const keepScroll = view.dataset.route === location.hash;
  let html;
  switch (r.name) {
    case "machine": html = r.args[0] ? machineView(r.args[0]) : systemView("satellites"); break;
    case "job": html = r.args[1] ? jobView(r.args[0], r.args[1]) : jobsView(); break;
    case "system": html = systemView(r.args[0] || prefs.get("system.pane", "satellites")); break;
    case "agent": html = r.args[0] === "jobs" ? agentView("jobs") : agentView("desk"); break;
    case "jobs": html = agentView("jobs"); break;
    case "tracker": html = r.args[0] ? trackerItemView(r.args[0]) : trackerView(); break;
    case "alerts": html = alertsView(); break;
    case "spaces": html = spacesView(); break;
    case "space": html = spaceView(r.args[0]); break;
    case "section": html = sectionView(r.args[0], r.args[1], r.args[2]); break;
    case "browse": html = browseView(); break;
    case "nav": html = navGroupView(r.args.join("/")); break;
    case "tab": html = navTabView(r.args[0]); break;
    case "machines": html = systemView("satellites"); break;
    default: html = homeView();
  }
  view.innerHTML = html;
  view.dataset.route = location.hash;
  if (keepScroll) view.scrollTop = scroll;
  bindView(r);
  hydrateMedia(view, call);
}

// ---------- Home ----------
function homeView() {
  const sats = state.satellites;
  const online = sats.filter((s) => s.status === "online").length;
  const offline = sats.filter((s) => s.status !== "online");
  const running = state.jobs.filter(isRunning);
  const pending = state.alerts.filter((a) => !a.acked);
  const feeds = (state.feeds && state.feeds.jobs) || [];
  const failing = feeds.filter((f) => f.failing_since || (f.last && f.last.status && !["ok", "skipped"].includes(f.last.status)));
  const active = ((state.tracker && state.tracker.items) || []).filter((i) => i.status === "active");
  const needs = [
    ...offline.map((s) => `<a class="rowline click" href="#/machine/${encodeURIComponent(s.id)}"><span class="dot offline"></span><span class="grow ellipsis"><b>${esc(s.name || s.id)}</b> is offline</span><span class="faint small">last seen ${agoSpan(s.last_seen)}</span></a>`),
    ...failing.slice(0, 5).map((f) => `<a class="rowline click" href="#/system/automations"><span class="badge bad">feed</span><span class="grow ellipsis"><b>${esc(f.name || f.id)}</b> <span class="faint">${esc((f.last && f.last.summary) || f.last?.status || "failing")}</span></span>${f.failing_since ? `<span class="faint small">since ${agoSpan(f.failing_since)}</span>` : ""}</a>`),
    ...pending.slice(0, 5).map((a) => `<a class="rowline click" href="#/alerts"><span class="badge warn">alert</span><span class="grow ellipsis"><b>${esc(a.title || "Alert")}</b> <span class="faint">${esc(a.body || "")}</span></span><span class="faint small">${agoSpan(a.created_at)}</span></a>`),
  ];
  const doing = [
    ...running.map((j) => `<a class="rowline click" href="#/job/${encodeURIComponent(j.satellite_id)}/${encodeURIComponent(jobId(j))}">${statusBadge(j.status)}<span class="grow ellipsis"><b>${esc(j.title || (j.prompt || "").slice(0, 60))}</b> <span class="faint">${esc(j.agent || "")} on ${esc(j.machine || j.satellite_id)}</span></span>${elapsed(j)}</a>`),
    ...active.slice(0, 8).map((i) => `<a class="rowline click" href="#/tracker/${encodeURIComponent(i.id)}"><span class="badge accent">${esc(i.agent_name || i.agent || "agent")}</span><span class="grow ellipsis"><b>${esc(i.title)}</b> <span class="faint">${esc(i.last_note || "")}</span></span><span class="faint small">${agoSpan(i.updated_at)}</span></a>`),
  ];
  const spaces = state.dir.visible;
  return header(esc(systemName()), "Home", "What needs you, what the agents are doing, and every space.") +
    `<div class="summary">
      <a class="stat click" href="#/system/satellites"><div class="k">Machines online</div><div class="v">${online}<small>/${sats.length}</small></div></a>
      <a class="stat click" href="#/agent/jobs"><div class="k">Agent jobs running</div><div class="v">${running.length}</div></a>
      <a class="stat click" href="#/tracker"><div class="k">Tracker active</div><div class="v">${active.length}</div></a>
      <a class="stat click" href="#/alerts"><div class="k">Pending alerts</div><div class="v" style="color:${pending.length ? "var(--warn)" : "inherit"}">${pending.length}</div></a>
      <a class="stat click" href="#/system/automations"><div class="k">Feeds failing</div><div class="v" style="color:${failing.length ? "var(--bad)" : "inherit"}">${state.feeds ? failing.length : "–"}</div></a>
    </div>
    <div class="detail">
      <div class="card"><h2>Needs you</h2>${needs.length ? `<div class="rows">${needs.join("")}</div>` : `<div class="note">Nothing needs you right now.</div>`}</div>
      <div class="card"><h2>Agents at work</h2>${doing.length ? `<div class="rows">${doing.join("")}</div>` : `<div class="note">No agent jobs or tracked work running.</div>`}</div>
    </div>
    <div class="row" style="margin:22px 0 10px"><h2 style="margin:0">Spaces</h2><span class="spacer"></span><a href="#/spaces" class="small">All spaces</a></div>
    ${spaces.length ? `<div class="tiles-grid">${spaces.map(spaceTile).join("")}</div>` : `<div class="empty">${state.catalog ? "No spaces yet. Feeds create them when they push a section with a space." : "Loading spaces…"}</div>`}
    <div class="row" style="margin:22px 0 10px"><h2 style="margin:0">Machines</h2><span class="spacer"></span><a href="#/system/satellites" class="small">System</a></div>
    ${sats.length ? `<div class="grid">${[...sats].sort(byOnline).slice(0, 6).map(machineCard).join("")}</div>` : `<div class="empty">No machines yet.</div>`}`;
}
function spaceTile(s) {
  const d = Model.spaceData(state.dir, s.id, "all");
  const facts = [plural(d.sections.length, "section"), d.products.length ? plural(d.products.length, "product") : "", d.allLedgers.length ? "money" : ""].filter(Boolean).join(" · ");
  return `<a class="tile-card click" href="#/space/${encodeURIComponent(s.id)}" ${accentStyle(s.accent)}>${icon(s.icon, null, "ico big")}
    <div class="grow"><div class="name ellipsis">${esc(s.title)}</div><div class="faint small ellipsis">${esc(s.subtitle || facts)}</div>
    ${s.subtitle ? `<div class="faint small">${esc(facts)}</div>` : ""}</div></a>`;
}
const byOnline = (a, b) => (a.status === "online" ? 0 : 1) - (b.status === "online" ? 0 : 1) || String(a.name).localeCompare(String(b.name));

// ---------- System ----------
const PANES = [["satellites", "Satellites"], ["automations", "Automations"], ["manage", "Manage"], ["settings", "Settings"]];
function systemView(pane) {
  if (!PANES.some(([p]) => p === pane)) pane = "satellites";
  prefs.set("system.pane", pane);
  const online = state.satellites.filter((s) => s.status === "online").length;
  const info = state.hubInfo || {};
  const host = (state.session.hub || "").replace(/^https?:\/\//, "");
  const strip = `<a class="hub-strip click" href="#/system/settings">${icon("server.rack", null, "ico big")}<div class="grow"><b>${esc(info.name || systemName())}</b>
      <div class="faint small ellipsis">${esc(host)} · ${online}/${state.satellites.length} online${info.machine ? " · hub on " + esc(info.machine) : ""}${info.version ? " · v" + esc(info.version) : ""}</div></div>
      <span class="live ${state.live ? "on" : state.live === false ? "off" : ""}"><i></i>${state.live ? "Live" : state.live === false ? "Polling" : "Connecting"}</span><span class="faint">›</span></a>`;
  const tabs = `<div class="seg big" id="system-pane">${PANES.map(([p, t]) => `<a href="#/system/${p}" class="${p === pane ? "on" : ""}">${t}</a>`).join("")}</div>`;
  const body = { satellites: satellitesPane, automations: automationsPane, manage: managePane, settings: settingsPane }[pane]();
  return `<div class="system-top">${strip}${tabs}</div>${body}`;
}
function satellitesPane() {
  const sats = [...state.satellites].sort(byOnline);
  const online = sats.filter((s) => s.status === "online").length;
  const running = sats.reduce((n, s) => n + (Number(s.running_jobs) || 0), 0);
  const browsers = sats.filter((s) => s.browser_status && s.browser_status.state === "connected").length;
  const systemPages = state.dir.systemSections.filter((id) => state.dir.entries[id]);
  const head = `<div class="row" style="margin:4px 0 14px"><div class="grow sub">Every computer joined to your hub, its health, and the agents it runs.</div>
      <div class="faint small">Updated ${state.satFetchedAt ? agoSpan(state.satFetchedAt) : "–"}</div></div>
    <div class="summary">
      <div class="stat"><div class="k">Machines</div><div class="v">${sats.length}</div></div>
      <div class="stat"><div class="k">Online</div><div class="v" style="color:var(--ok)">${online}</div></div>
      <div class="stat"><div class="k">Offline</div><div class="v" style="color:${sats.length - online ? "var(--bad)" : "inherit"}">${sats.length - online}</div></div>
      <div class="stat"><div class="k">Agent jobs running</div><div class="v">${running}</div></div>
      <div class="stat"><div class="k">Browsers connected</div><div class="v">${browsers}</div></div>
    </div>`;
  const pages = systemPages.length ? `<div class="card" style="margin-top:18px"><h2>System pages</h2><div class="rows">${systemPages.map((id) => sectionRow(state.dir.entries[id])).join("")}</div></div>` : "";
  if (!state.satFetchedAt) return head + `<div class="loading" style="height:30vh"><div class="spinner"></div></div>`;
  if (!sats.length) return head + `<div class="empty">No machines have joined this hub yet.<br><span class="small">On another computer run <code>orbital satellite join</code>, or <code>orbital satellites invite</code> on the hub.</span></div>` + pages;
  return head + `<div class="grid">${sats.map(machineCard).join("")}</div>` + pages;
}
function sectionRow(e) {
  if (!e) return "";
  return `<a class="rowline click" href="#/section/${encodeURIComponent(e.id)}" ${accentStyle(e.accent)}>${icon(e.icon, e.icon_image)}<span class="grow"><b>${esc(e.title)}</b>
    <div class="faint small ellipsis">${esc(e.summary || e.subtitle || (e.updated_at ? "Updated " + ago(e.updated_at) : ""))}</div></span><span class="faint">›</span></a>`;
}
function machineCard(s) {
  const caps = s.capabilities || {};
  const online = s.status === "online";
  const m = s.system_metrics || {};
  const agents = (caps.agents || []).map((a) => `<span class="badge agent">${esc(a)}</span>`).join("") || `<span class="faint small">No agents</span>`;
  const jobs = Number(s.running_jobs) || 0;
  return `<div class="card machine ${online ? "" : "offline"}" tabindex="0" data-machine="${esc(s.id)}">
    <div class="head"><span class="dot ${online ? "online" : "offline"}"></span>
      <div class="grow"><div class="name ellipsis">${esc(s.name || s.machine || s.id)}</div>
        <div class="host ellipsis">${esc(osName(s.os))} · ${esc(s.host || s.ip || "")}</div></div>
      <span class="badge ${online ? "ok" : "bad"}">${online ? "Online" : "Offline"}</span></div>
    <div class="health">${online ? "Heartbeat" : "Last seen"} <b>${agoSpan(s.last_seen)}</b>${m.uptime_s ? ` · up <b>${duration(m.uptime_s)}</b>` : ""}${s.version ? ` · v${esc(s.version)}` : ""}</div>
    ${machineMeters(s)}
    <div class="chips">${agents}</div>
    <div class="foot">${browserChip(s)}<span class="badge ${jobs ? "accent" : ""}"><i></i>${jobs} job${jobs === 1 ? "" : "s"} running</span>
      ${caps.tracker ? `<span class="badge">Tracker</span>` : ""}</div>
  </div>`;
}
function automationsPane() {
  const f = state.feeds;
  if (!f) return state.feedsError ? `<div class="empty">${esc(state.feedsError)}</div>` : `<div class="loading" style="height:30vh"><div class="spinner"></div></div>`;
  const sch = f.scheduler || {};
  const groups = f.groups || [];
  const jobs = (f.jobs || []).filter((j) => !state.feedGroup || j.group === state.feedGroup);
  const by = {};
  for (const j of jobs) (by[j.group || ""] = by[j.group || ""] || []).push(j);
  const title = (g) => (groups.find((x) => x.id === g) || {}).title || g || "Other";
  const failing = (f.jobs || []).filter((j) => j.failing_since).length;
  return `<div class="row wrap" style="margin:4px 0 14px"><div class="grow sub">Every feed: how often it runs, when it runs next, and what its last run did.</div>
      <select id="feed-group" style="width:auto"><option value="">All groups</option>${groups.map((g) => `<option value="${esc(g.id)}" ${g.id === state.feedGroup ? "selected" : ""}>${esc(g.title)}</option>`).join("")}</select>
      <button id="run-all" class="small">Run ${state.feedGroup ? esc(title(state.feedGroup)) : "all"}</button></div>
    <div class="summary">
      <div class="stat"><div class="k">Scheduler</div><div class="v" style="color:${sch.up ? "var(--ok)" : "var(--bad)"}">${sch.up ? "Up" : "Down"}</div><div class="faint small">${sch.heartbeat_age_s != null ? "heartbeat " + duration(sch.heartbeat_age_s) + " ago" : ""}</div></div>
      <div class="stat"><div class="k">Feeds</div><div class="v">${(f.jobs || []).length}</div></div>
      <div class="stat"><div class="k">Running / queued</div><div class="v">${sch.running ?? 0} / ${sch.queued ?? 0}</div></div>
      <div class="stat"><div class="k">Failing</div><div class="v" style="color:${failing ? "var(--bad)" : "inherit"}">${failing}</div></div>
      <div class="stat"><div class="k">Disk free</div><div class="v">${f.disk ? esc(f.disk.free_gb) + " GB" : "–"}</div></div>
    </div>
    ${Object.keys(by).sort((a, b) => title(a).localeCompare(title(b))).map((g) => `<div class="card" style="margin-bottom:14px"><h2>${esc(title(g))}</h2>
      <table class="jobs"><thead><tr><th>Feed</th><th>Schedule</th><th>Next</th><th>Last run</th><th></th></tr></thead><tbody>
      ${by[g].map((j) => { const last = j.last || {}; const bad = j.failing_since || (last.status && !["ok", "skipped"].includes(last.status));
        return `<tr><td><div class="title">${esc(j.name || j.id)}</div><div class="prompt">${esc(j.reads || "")}</div></td>
          <td class="num">${esc(j.schedule || "")}${j.paused ? ` <span class="badge warn">paused</span>` : ""}${j.enabled === false ? ` <span class="badge">off</span>` : ""}</td>
          <td class="num">${j.state === "running" ? `<span class="badge accent"><i></i>running</span>` : j.state === "queued" ? `<span class="badge warn">queued</span>` : untilSpan(j.next_run_at)}</td>
          <td><span class="badge ${bad ? "bad" : last.status ? "ok" : ""}">${esc(last.status || "never")}</span> <span class="faint small">${last.finished_at ? agoSpan(last.finished_at) : ""}</span>
            <div class="prompt">${esc(last.summary || "")}</div></td>
          <td><button class="small" data-run-feed="${esc(j.id)}" ${j.state === "running" ? "disabled" : ""}>Run</button></td></tr>`; }).join("")}
      </tbody></table></div>`).join("") || `<div class="empty">No feeds. Add one to <code>~/.orbital-agents/jobs.json</code>.</div>`}`;
}
function managePane() {
  const sections = [...((state.catalog && state.catalog.sections) || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.title).localeCompare(String(b.title)));
  const spaceOf = {};
  for (const s of state.dir.spaces) for (const id of s.sections) spaceOf[id] = s;
  const nav = state.nav;
  const count = (items) => (items || []).reduce((n, i) => n + 1 + count(i.children), 0);
  return `<div class="detail" style="margin-top:4px">
    <div class="card"><h2>Navigation</h2><dl class="kv">
      <dt>Layout</dt><dd>${state.navDefault ? "Default (spaces, then their sections)" : "Custom, from the hub"}</dd>
      <dt>Extra items</dt><dd>${(nav.tabs || []).length ? nav.tabs.map((t) => esc(t.title)).join(", ") : "None"}</dd>
      <dt>Sidebar</dt><dd>${nav.sidebar ? plural(count(nav.sidebar), "item") : "Default tree"}</dd></dl>
      <div class="note" style="margin-top:10px">Change it on the hub with <code>orbital nav</code>; every app updates at once.</div>
      ${state.navDefault ? "" : `<div class="row" style="margin-top:10px"><button class="small danger" id="nav-reset">Reset to default</button></div>`}</div>
    <div class="card"><h2>Spaces</h2><div class="rows">${state.dir.spaces.map((s) => `<a class="rowline click" href="${s.system ? "#/system/satellites" : "#/space/" + encodeURIComponent(s.id)}" ${accentStyle(s.accent)}>${icon(s.icon)}<span class="grow"><b>${esc(s.title)}</b>
      <div class="faint small">${plural(s.sections.length, "section")}${s.system ? " · shown under System" : ""}</div></span></a>`).join("") || `<div class="note">No spaces.</div>`}</div></div>
    <div class="card wide"><h2>Sections (${sections.length})</h2><div class="note" style="margin-bottom:8px">Feeds own what is inside each one.</div>
      <table class="jobs"><thead><tr><th>Section</th><th>Space</th><th>Layout</th><th>Order</th><th>Revision</th><th>Updated</th></tr></thead><tbody>
      ${sections.map((e) => `<tr class="click" data-open="#/section/${esc(encodeURIComponent(e.id))}"><td><div class="row">${icon(e.icon, e.icon_image)}<div><div class="title">${esc(e.title)}</div><div class="prompt">${esc(e.id)}</div></div></div></td>
        <td>${spaceOf[e.id] ? esc(spaceOf[e.id].title) : `<span class="faint">none</span>`}</td><td>${esc(e.layout || "page")}${e.node_count ? ` · ${e.node_count} pages` : ""}</td>
        <td class="num">${esc(e.order ?? "")}</td><td class="num">${esc(e.revision ?? "")}</td><td class="num">${e.updated_at ? agoSpan(e.updated_at) : "–"}</td></tr>`).join("")}
      </tbody></table></div></div>`;
}
function settingsPane() {
  const s = state.session, info = state.hubInfo || {}, app = state.appInfo || {};
  const ev = state.tracker && state.tracker.settings;
  return `<div class="detail" style="margin-top:4px">
    <div class="card"><h2>Connection</h2><dl class="kv">
      <dt>Status</dt><dd>${state.live ? "Live" : state.live === false ? "Polling every 10 s" : "Connecting"}</dd>
      <dt>Hub address</dt><dd class="mono">${esc(s.hub || "")}</dd>
      <dt>Signed in as</dt><dd>${esc(s.username || "–")}</dd>
      <dt>Session kept in</dt><dd>${esc(app.token_store || "–")}</dd></dl>
      <div class="row" style="margin-top:12px"><button class="small" id="reconnect">Reconnect now</button><button class="small danger" id="sign-out-2">Sign out</button></div></div>
    <div class="card"><h2>Hub</h2><dl class="kv">
      <dt>Name</dt><dd>${esc(info.name || "–")}</dd><dt>Machine</dt><dd>${esc(info.machine || "–")}</dd>
      <dt>Version</dt><dd>${esc(info.version || "–")}</dd><dt>Hub id</dt><dd class="mono">${esc(info.hub_id || s.hub_id || "–")}</dd>
      <dt>Machines online</dt><dd>${state.satellites.filter((x) => x.status === "online").length} of ${state.satellites.length}</dd>
      <dt>Agent model</dt><dd>${esc(state.desk && state.desk.model ? deskModel(state.desk.model) : "–")}</dd>
      <dt>Screenshot evidence</dt><dd>${esc(ev ? ev.label || ev.evidence : "–")}</dd></dl></div>
    <div class="card"><h2>This app</h2><dl class="kv">
      <dt>Version</dt><dd>${esc(app.version || "–")}</dd><dt>System</dt><dd>${esc(app.platform || "–")}</dd>
      <dt>Python</dt><dd>${esc(app.python || "–")}</dd><dt>Settings folder</dt><dd class="mono">${esc(app.data_dir || "–")}</dd></dl></div>
  </div>`;
}
const deskModel = (m) => (typeof m === "string" ? m : m.configured === false ? "Not set up" : m.model || m.provider || "–");

// ---------- Machine drill-down ----------
function machineView(id) {
  const s = state.satellites.find((x) => x.id === id);
  if (!s) return state.satFetchedAt ? `<div class="empty">No machine <b>${esc(id)}</b> on this hub. <a href="#/system/satellites">Back to machines</a></div>`
                                    : `<div class="loading"><div class="spinner"></div></div>`;
  const caps = s.capabilities || {};
  const online = s.status === "online";
  const m = s.system_metrics || {};
  const bi = browserInfo(s);
  const b = bi.b;
  const mj = state.machineJobs[id];
  const jobs = mj && mj.jobs ? mj.jobs : state.jobs.filter((j) => j.satellite_id === id);
  const draft = state.runForm[id] || {};
  const agents = caps.agents || [];
  const loadBlock = m.load ? `<div class="loadavg"><div><b>${m.load[0].toFixed(2)}</b><span>1 min</span></div><div><b>${m.load[1].toFixed(2)}</b><span>5 min</span></div><div><b>${m.load[2].toFixed(2)}</b><span>15 min</span></div><div><b>${m.cpus || "–"}</b><span>cores</span></div></div>` : "";
  return `<div class="crumb"><a href="#/system/satellites">System</a> › <a href="#/system/satellites">Satellites</a> › ${esc(s.name || s.id)}</div>
  <div class="page-head"><div class="grow"><div class="row"><span class="dot ${online ? "online" : "offline"}"></span><h1>${esc(s.name || s.id)}</h1>
      <span class="badge ${online ? "ok" : "bad"}">${online ? "Online" : "Offline"}</span></div>
      <div class="sub">${esc(osName(s.os))} · ${esc(s.host || "")}${s.ip ? " · " + esc(s.ip) : ""}</div></div>
    <button id="ping" ${online ? "" : "disabled"}>Ping</button>
    <button class="primary" id="goto-run" ${online && agents.length ? "" : "disabled"}>Run an agent</button></div>
  <div class="detail">
    <div class="card"><h2>Health</h2><dl class="kv">
      <dt>Status</dt><dd>${online ? "Online" : "Offline"}</dd>
      <dt>Last heartbeat</dt><dd>${agoSpan(s.last_seen)} <span class="faint">(${esc(when(s.last_seen))})</span></dd>
      <dt>Heartbeat every</dt><dd>${s.heartbeat_seconds ? s.heartbeat_seconds + " s" : "–"} <span class="faint">(offline after 3 missed)</span></dd>
      <dt>Connection</dt><dd>${esc(s.reachability || "callback")}${s.port ? ` · port ${esc(s.port)}` : ""}</dd>
      <dt>Uptime</dt><dd>${m.uptime_s ? duration(m.uptime_s) : "–"}</dd>
      <dt>Satellite version</dt><dd>${esc(s.version || "–")}</dd>
      <dt>Joined</dt><dd>${esc(when(s.joined_at))}</dd>
      <dt>Metrics taken</dt><dd>${m.time ? agoSpan(m.time) : "–"}</dd>
      <dt>Machine id</dt><dd class="mono">${esc(s.id)}</dd>
    </dl><div id="ping-out" class="note" style="margin-top:10px"></div></div>
    <div class="card"><h2>CPU &amp; memory</h2>${loadBlock}
      <div class="meters">${cpuPct(m) != null ? meter("CPU load (1 min ÷ cores)", cpuPct(m), cpuPct(m).toFixed(0) + "%", { big: true }) : `<div class="note">No CPU data</div>`}
      ${m.memory && m.memory.percent != null ? meter("Memory", m.memory.percent, `${m.memory.percent.toFixed(1)}% · ${bytes(m.memory.used)} of ${bytes(m.memory.total)}`, { big: true }) : `<div class="note">No memory data</div>`}</div></div>
    <div class="card"><h2>Disks</h2>${(m.disks || []).length ? `<div class="meters">${m.disks.map((d) => meter(d.mount || "disk", d.percent, `${(d.percent ?? 0).toFixed(1)}% used`, { big: true }) + `<div class="note" style="margin:-4px 0 4px">${bytes(d.free)} free · ${bytes(d.used)} used · ${bytes(d.total)} total</div>`).join("")}</div>` : `<div class="note">No disk data</div>`}</div>
    <div class="card"><h2>Browser controller</h2>
      <div style="margin-bottom:12px"><span class="badge ${bi.cls}"><i></i>${esc(bi.label)}</span></div>
      ${b ? `<dl class="kv">
        <dt>Bridge</dt><dd>${b.bridge_up ? "Running" : "Not running"}${caps.browser_port ? ` · port ${esc(caps.browser_port)}` : ""}</dd>
        <dt>Chrome extension</dt><dd>${b.extension_connected ? "Connected" : "Not connected"}${b.extension_version ? " · v" + esc(b.extension_version) : ""}</dd>
        <dt>Last poll</dt><dd>${b.last_poll_age_s != null ? duration(b.last_poll_age_s) + " ago" : "–"}</dd>
        <dt>Bind</dt><dd>${esc(b.bind || "–")}</dd>
        <dt>Hub can drive it</dt><dd>${b.hub_can_drive ? "Yes" : "No"}</dd>
        <dt>Checked</dt><dd>${b.checked_at ? agoSpan(b.checked_at) : "–"}</dd></dl>
        ${b.hub_can_drive ? "" : `<div class="note warn" style="margin-top:10px">The hub can't drive this browser (bind = ${esc(b.bind || "loopback")}). Set <code>browser.bind = "tailscale"</code> on that machine to allow it.</div>`}
        ${b.state === "no_extension" ? `<div class="note" style="margin-top:8px">Load the extension in Chrome: chrome://extensions → Developer mode → Load unpacked → browser-extension/.</div>` : ""}`
      : `<div class="note">This machine hasn't reported its browser controller yet${caps.browser ? ` (bridge on port ${esc(caps.browser_port || "?")})` : ""}.</div>`}</div>
    <div class="card"><h2>Agents</h2><div class="chips">${agents.map((a) => `<span class="badge agent">${esc(a)}</span>`).join("") || `<span class="note">No agent CLIs found on this machine.</span>`}</div>
      <div class="note" style="margin-top:10px">${caps.tracker ? "Tracker installed. " : ""}${Number(s.running_jobs) || 0} job(s) running now.</div></div>
    <div class="card wide"><div class="row" style="margin-bottom:10px"><h2 style="margin:0">Jobs on this machine</h2><span class="spacer"></span>
      <span class="faint small">${mj && mj.error ? "Live list unavailable: " + esc(mj.error) + " (showing the hub's copy)" : mj ? "Live from the machine" : "Loading…"}</span></div>
      ${jobsTable(jobs, false)}</div>
    <div class="card wide" id="run"><h2>Run an agent on ${esc(s.name || s.id)}</h2>
      ${online ? "" : `<div class="note warn" style="margin-bottom:10px">This machine is offline; it can't start jobs until it's back.</div>`}
      <form class="stack" id="run-form" autocomplete="off">
        <div class="two"><label class="field">Agent<select name="agent">${agents.map((a) => `<option ${draft.agent === a ? "selected" : ""}>${esc(a)}</option>`).join("")}<option value="" ${draft.agent === "" ? "selected" : ""}>Machine default</option></select></label>
          <label class="field">Title (optional)<input name="title" maxlength="120" placeholder="Fix the failing tests" value="${esc(draft.title || "")}"></label></div>
        <label class="field">Working folder (optional, must be inside that machine's satellite.workdirs)<input name="cwd" placeholder="~/code/app" value="${esc(draft.cwd || "")}"></label>
        <label class="field">Prompt<textarea name="prompt" rows="4" placeholder="What should the agent do?">${esc(draft.prompt || "")}</textarea></label>
        <div class="row"><button class="primary" type="submit" ${online && agents.length ? "" : "disabled"}>Run</button><span class="err" id="run-err"></span></div>
      </form></div>
  </div>`;
}
function statusBadge(status) {
  const cls = { running: "accent", queued: "warn", done: "ok", failed: "bad", cancelled: "" }[status] || "";
  return `<span class="badge ${cls}"><i></i>${esc(status || "unknown")}</span>`;
}
function elapsed(j) {
  if (j.duration_s != null && !isRunning(j)) return duration(j.duration_s);
  const start = parseTime(j.started_at || j.created_at);
  if (!start) return "–";
  const end = parseTime(j.finished_at) || Date.now();
  return `<span ${isRunning(j) ? `data-since="${esc(j.started_at || j.created_at)}"` : ""}>${duration((end - start) / 1000)}</span>`;
}
function jobsTable(jobs, showMachine) {
  if (!jobs || !jobs.length) return `<div class="empty" style="padding:24px">No agent jobs yet.</div>`;
  const sorted = [...jobs].sort((a, b) => (isRunning(b) - isRunning(a)) || String(b.created_at || "").localeCompare(String(a.created_at || "")));
  return `<table class="jobs"><thead><tr><th>Status</th><th>Job</th>${showMachine ? "<th>Machine</th>" : ""}<th>Agent</th><th>Started</th><th>Elapsed</th></tr></thead><tbody>
    ${sorted.slice(0, 200).map((j) => `<tr class="click" data-job="${esc(j.satellite_id)}/${esc(jobId(j))}">
      <td>${statusBadge(j.status)}</td>
      <td><div class="title">${esc(j.title || (j.prompt || "").slice(0, 60) || jobId(j))}</div><div class="prompt">${esc(j.prompt || "")}</div></td>
      ${showMachine ? `<td>${esc(j.machine || j.satellite_id)}</td>` : ""}
      <td>${esc(j.agent || "–")}</td><td class="num">${esc(when(j.started_at || j.created_at))}</td><td class="num">${elapsed(j)}</td></tr>`).join("")}
  </tbody></table>`;
}

// ---------- Agent: the hub's desk, and every agent job ----------
function agentView(pane) {
  const tabs = `<div class="seg big" id="agent-pane"><a href="#/agent" class="${pane === "desk" ? "on" : ""}">Desk</a><a href="#/agent/jobs" class="${pane === "jobs" ? "on" : ""}">Agent jobs</a></div>`;
  return header(esc(systemName()), "Agent", pane === "desk" ? "Talk to the hub's agent. It can start work on any machine." : "What every machine is running, and what it ran.", tabs) +
    (pane === "jobs" ? jobsBody() : deskBody());
}
function jobsBody() {
  const all = state.jobs;
  const list = state.jobsFilter === "running" ? all.filter(isRunning) : all;
  const running = all.filter(isRunning).length;
  return `<div class="row" style="margin-bottom:12px">${seg("jobs-filter", [["all", `All (${all.length})`], ["running", `Running (${running})`]], state.jobsFilter)}</div>
    ${state.jobsLoaded ? `<div class="card">${jobsTable(list, true)}</div>` : `<div class="loading" style="height:30vh"><div class="spinner"></div></div>`}`;
}
const jobsView = () => agentView("jobs");
function deskBody() {
  const d = state.desk;
  if (!d) return state.deskError ? `<div class="empty">${esc(state.deskError)}</div>` : `<div class="loading" style="height:30vh"><div class="spinner"></div></div>`;
  const msgs = (d.messages || []).slice(-80);
  return `<div class="desk card"><div class="row" style="margin-bottom:8px"><span class="faint small grow">${esc(d.model ? deskModel(d.model) : "")}</span>
      ${(d.tasks || []).filter((t) => ["running", "queued"].includes(t.status)).length ? `<span class="badge accent"><i></i>${plural(d.tasks.filter((t) => ["running", "queued"].includes(t.status)).length, "task")} running</span>` : ""}</div>
    <div class="messages" id="messages">${msgs.map(deskMessage).join("") || `<div class="note">No messages yet. Say hello.</div>`}
      ${state.deskSending ? `<div class="msg assistant pending"><div class="bubble"><span class="spinner tiny"></span> Thinking…</div></div>` : ""}</div>
    <form class="row" id="desk-form" autocomplete="off"><input name="text" placeholder="Ask the agent… (Enter to send)" maxlength="6000" ${state.deskSending ? "disabled" : ""}>
      <button class="primary" type="submit" ${state.deskSending ? "disabled" : ""}>Send</button></form>
    ${state.deskError ? `<div class="err small" style="margin-top:6px">${esc(state.deskError)}</div>` : ""}</div>`;
}
function deskMessage(m) {
  if (m.role === "event") return `<div class="msg event"><span class="faint small">${esc(m.text || m.kind)} · ${esc(when(m.at))}</span></div>`;
  const did = (m.did || []).map((x) => `<span class="badge">${esc(typeof x === "string" ? x : x.label || x.kind || x.tool || "did")}</span>`).join("");
  return `<div class="msg ${m.role === "user" ? "user" : "assistant"}"><div class="bubble">${md(m.text || "")}</div>
    <div class="faint small">${esc(when(m.at))}${m.source === "voice" ? " · voice" : ""}${m.latency_ms ? " · " + (m.latency_ms / 1000).toFixed(1) + " s" : ""}</div>${did ? `<div class="chips">${did}</div>` : ""}</div>`;
}

// ---------- One job ----------
function jobView(satId, id) {
  const j = state.job && jobId(state.job) === id ? state.job : state.jobs.find((x) => jobId(x) === id && x.satellite_id === satId);
  const s = state.satellites.find((x) => x.id === satId);
  const mname = (s && s.name) || satId;
  const crumb = `<div class="crumb"><a href="#/agent/jobs">Agent jobs</a> › <a href="#/machine/${encodeURIComponent(satId)}">${esc(mname)}</a> › Job</div>`;
  if (!j) return crumb + (state.jobError ? `<div class="empty">${esc(state.jobError)}</div>` : `<div class="loading"><div class="spinner"></div></div>`);
  const running = isRunning(j);
  const L = state.jobLog;
  return crumb + `<div class="page-head"><div class="grow"><div class="row wrap"><h1>${esc(j.title || "Agent job")}</h1>${statusBadge(j.status)}${j.cancel_requested && running ? `<span class="badge warn">stopping…</span>` : ""}</div>
      <div class="sub">${esc(j.agent || "agent")} on ${esc(mname)} · ${esc(id)}</div></div>
      ${running ? `<button class="danger" id="stop">Stop</button>` : ""}</div>
    <div class="detail">
      <div class="card"><h2>Details</h2><dl class="kv">
        <dt>Status</dt><dd>${esc(j.status)}${j.exit_code != null ? ` · exit ${esc(j.exit_code)}` : ""}${j.timed_out ? " · timed out" : ""}</dd>
        <dt>Agent</dt><dd>${esc(j.agent || "–")}</dd>
        <dt>Machine</dt><dd>${esc(mname)}</dd>
        <dt>Folder</dt><dd class="mono">${esc(j.cwd || "–")}</dd>
        <dt>Started by</dt><dd>${esc(j.by || "–")}</dd>
        <dt>Created</dt><dd>${esc(when(j.created_at))}</dd>
        <dt>Started</dt><dd>${esc(when(j.started_at))}</dd>
        <dt>Finished</dt><dd>${esc(when(j.finished_at))}</dd>
        <dt>Elapsed</dt><dd>${elapsed(j)}</dd>
        ${j.pid ? `<dt>PID</dt><dd>${esc(j.pid)}</dd>` : ""}
        ${j.tracker_id ? `<dt>Tracker item</dt><dd class="mono"><a href="#/tracker/${encodeURIComponent(j.tracker_id)}">${esc(j.tracker_id)}</a></dd>` : ""}
        ${j.error ? `<dt>Error</dt><dd style="color:var(--bad)">${esc(j.error)}</dd>` : ""}
      </dl></div>
      <div class="card"><h2>Prompt</h2><pre class="block">${esc(j.prompt || "")}</pre></div>
      <div class="card wide"><div class="row" style="margin-bottom:10px"><h2 style="margin:0">Output</h2><span class="spacer"></span>
        <span class="faint small">${L ? `${L.bytes != null ? bytes(L.bytes) : ""}${L.truncated ? " · showing the end" : ""}${running ? " · live" : ""}` : ""}</span>
        <button class="small" id="copy-log">Copy</button></div>
        <pre class="block log" id="log">${L ? esc(L.log || (running ? "Waiting for output…" : "(no output)")) : "Loading…"}</pre></div>
    </div>`;
}

// ---------- Tracker ----------
function trackerView() {
  const t = state.tracker;
  if (!t) return header(esc(systemName()), "Tracker") + `<div class="loading" style="height:30vh"><div class="spinner"></div></div>`;
  const q = state.trackerSearch.toLowerCase();
  const items = (t.items || []).filter((i) => (state.trackerFilter === "all" || (state.trackerFilter === "active" ? i.status === "active" : i.status !== "active")))
    .filter((i) => !q || [i.title, i.last_note, i.project, i.agent_name, i.machine].join(" ").toLowerCase().includes(q));
  const counts = { active: t.items.filter((i) => i.status === "active").length, done: t.items.filter((i) => i.status !== "active").length };
  const ev = t.settings || {};
  return header(esc(systemName()), "Tracker", "What every agent is working on, across your machines. The same list as the floating tracker.",
      `<span class="badge" title="Screenshot evidence level">Evidence: ${esc(ev.label || ev.evidence || "–")}</span>`) +
    `<div class="row wrap" style="margin-bottom:14px">${seg("tracker-filter", [["active", `Active (${counts.active})`], ["done", `Finished (${counts.done})`], ["all", "All"]], state.trackerFilter)}
      <input id="tracker-search" placeholder="Search" value="${esc(state.trackerSearch)}" style="max-width:260px"></div>
    ${items.length ? `<div class="grid">${items.slice(0, 120).map(trackerCard).join("")}</div>` : `<div class="empty">${state.trackerFilter === "active" ? "No agent is working on anything right now." : "Nothing here."}</div>`}`;
}
function trackerCard(i) {
  const active = i.status === "active";
  return `<a class="card tracker click" href="#/tracker/${encodeURIComponent(i.id)}">
    ${i.cover && i.cover.thumb_url ? `<span class="cover" data-media="${esc(i.cover.thumb_url)}"></span>` : ""}
    <div class="row"><span class="dot ${active ? "online" : ""}"></span><b class="grow ellipsis">${esc(i.title)}</b><span class="badge ${active ? "accent" : "ok"}">${active ? "active" : esc(i.status)}</span></div>
    <div class="faint small">${esc([i.agent_name || i.agent, i.machine, i.project].filter(Boolean).join(" · "))}</div>
    ${i.last_note ? `<div class="small clamp">${esc(i.last_note)}</div>` : ""}
    <div class="faint small">${active ? "updated" : "finished"} ${agoSpan(i.completed_at || i.updated_at)}${i.media_count ? ` · ${plural(i.media_count, "picture")}` : ""}${i.subagents && i.subagents.total ? ` · ${plural(i.subagents.total, "subagent")}` : ""}</div></a>`;
}
function trackerItemView(id) {
  const base = ((state.tracker && state.tracker.items) || []).find((i) => i.id === id);
  const i = state.trackerItem && state.trackerItem.id === id ? { ...base, ...state.trackerItem } : base;
  const crumb = `<div class="crumb"><a href="#/tracker">Tracker</a> › ${esc((i && i.title) || id)}</div>`;
  if (!i) return crumb + `<div class="loading"><div class="spinner"></div></div>`;
  const notes = i.notes || i.updates || [];
  const media = i.media || [];
  return crumb + header("", esc(i.title), esc([i.agent_name || i.agent, i.machine, i.project].filter(Boolean).join(" · ")), `<span class="badge ${i.status === "active" ? "accent" : "ok"}">${esc(i.status)}</span>`) +
    `<div class="detail">
      <div class="card"><h2>Details</h2><dl class="kv"><dt>Started</dt><dd>${esc(when(i.created_at))}</dd><dt>Updated</dt><dd>${agoSpan(i.updated_at)}</dd>
        ${i.completed_at ? `<dt>Finished</dt><dd>${esc(when(i.completed_at))}</dd>` : ""}<dt>Folder</dt><dd class="mono">${esc(i.workspace || "–")}</dd><dt>Id</dt><dd class="mono">${esc(i.id)}</dd></dl>
        ${i.details ? `<div class="para" style="margin-top:10px">${md(i.details)}</div>` : ""}</div>
      ${i.summary ? `<div class="card"><h2>Summary</h2><div class="para">${md(i.summary)}</div></div>` : ""}
      ${notes.length ? `<div class="card wide"><h2>Notes</h2><div class="timeline">${notes.slice().reverse().map((n) => `<div class="tl"><i></i><div><div class="faint small">${esc(when(n.at || n.created_at))}</div>${md(n.note || n.text || "")}</div></div>`).join("")}</div></div>` : ""}
      ${media.length ? `<div class="card wide"><h2>Pictures</h2><div class="thumbs">${media.filter((m) => m.thumb_url).map((m) => `<figure><span class="thumb" data-media="${esc(m.thumb_url)}"></span><figcaption class="faint small">${esc(m.caption || m.file_name || "")}</figcaption></figure>`).join("")}</div></div>` : ""}
    </div>`;
}

// ---------- Alerts ----------
function alertsView() {
  const list = state.alerts;
  return header(esc(systemName()), "Alerts", "Notifications from feeds, machines and the tracker.",
      `${seg("alerts-mode", [["pending", "Pending"], ["all", "All"]], state.alertsMode)}${state.alertsMode === "pending" && list.length ? `<button id="ack-all">Clear all</button>` : ""}`) +
    (list.length ? list.map((a) => `<div class="card alert"><span class="dot ${a.acked ? "" : "online"}" style="margin-top:6px"></span>
      <div class="grow"><div class="row wrap"><h3>${esc(a.title || "Alert")}</h3>${a.subtitle ? `<span class="badge">${esc(a.subtitle)}</span>` : ""}${a.machine ? `<span class="badge">${esc(a.machine)}</span>` : ""}</div>
      ${a.body ? `<div class="body">${esc(a.body)}</div>` : ""}<div class="faint small" style="margin-top:6px">${esc(when(a.created_at))} · ${agoSpan(a.created_at)}</div></div>
      ${a.acked ? `<span class="faint small">cleared</span>` : `<button class="small" data-ack="${esc(a.id)}">Clear</button>`}</div>`).join("")
      : `<div class="empty">${state.alertsMode === "pending" ? "No pending alerts." : "No alerts."}</div>`);
}

// ---------- Spaces ----------
function spacesView() {
  const spaces = state.dir.visible;
  return header(esc(systemName()), "Spaces", plural(spaces.length, "space")) +
    (spaces.length ? `<div class="tiles-grid">${spaces.map(spaceTile).join("")}</div>` : `<div class="empty">${state.catalog ? "No spaces yet." : "Loading…"}</div>`);
}
/** A space: header with the switcher, area chips, platform chips, then the area (SpaceViews.swift). */
function spaceView(id) {
  const dir = state.dir;
  const space = dir.space(id);
  if (!space) return state.catalog ? `<div class="empty">No space <b>${esc(id)}</b>. <a href="#/spaces">All spaces</a></div>` : `<div class="loading"><div class="spinner"></div></div>`;
  if (space.system) return `<div class="empty">${esc(space.title)} lives under <a href="#/system/satellites">System</a>.</div>`;
  let platform = prefs.get("space.platform", "all");
  const area = prefs.get("space.area", "overview");
  let d = Model.spaceData(dir, space.id, platform);
  const kept = Model.keepPlatform(platform, d.platforms);
  if (kept !== platform) { platform = kept; prefs.set("space.platform", platform); d = Model.spaceData(dir, space.id, platform); }
  const platformName = platform === "all" ? "all platforms" : Model.PLATFORM_TITLES[platform];
  const linkOf = (ref) => Model.navRoute({ target: { kind: "section", section: ref.loc.section, node: ref.loc.node, tab: ref.loc.tab } });
  const chip = (attr, value, title, sym, on) => `<button class="chip ${on ? "on" : ""}" ${attr}="${esc(value)}">${icon(sym)}${esc(title)}</button>`;
  const areas = d.areas.map((a) => chip("data-area", a.id, a.title, a.icon, a.id === area)).join("") +
    (d.areas.some((a) => a.id === area) ? "" : chip("data-area", area, Model.areaTitle(area), "questionmark.circle", true));
  const platforms = d.platforms.length > 1 && Model.PLATFORM_AREAS.includes(area)
    ? `<div class="chips-row">${chip("data-platform", "all", "All", "square.stack", platform === "all")}${d.platforms.map((p) => chip("data-platform", p, Model.PLATFORM_TITLES[p], p === "ios" ? "apple.logo" : p === "android" ? "smartphone" : p === "roblox" ? "cube.fill" : "globe", platform === p)).join("")}</div>` : "";
  const empty = (text) => `<div class="card note">${esc(text)}</div>`;
  const kindCard = (kind) => {
    const mine = d.refs.filter((r) => r.block.kind === kind);
    if (mine.length === 1) return seriesCard(mine[0].block, { eyebrow: mine[0].block.product || Model.areaTitle(kind), open: linkOf(mine[0]) });
    return mine.length ? rollupCard(kind, mine, linkOf) : "";
  };
  let body = "";
  if (area === "overview") {
    body = (d.products.length ? `<div class="products">${d.products.map((p) => `<a class="product-btn click" href="${esc(Model.navRoute({ target: { kind: "section", section: p.loc.section, node: p.loc.node } }))}">${icon(p.icon, p.image, "ico app")}<span>${esc(p.name)}</span></a>`).join("")}</div>` : "") +
      `<div class="cards">${d.kinds.map(kindCard).join("")}${d.ledgers.length ? moneySummary(d.ledgers) : ""}</div>` + pagesCard(d);
  } else if (area === "money") {
    body = d.ledgers.length ? `<div class="cards">${platform === "all" ? moneySummary(d.ledgers) : ""}${d.ledgers.map((l) => ledgerCard(l.block)).join("")}</div>` : empty(`No money for ${platformName} in ${space.title}.`);
  } else if (area === "videos") {
    body = d.videos.length ? d.videos.map((v) => renderBlock(v.block, { section: v.loc.section })).join("") : empty(`No videos in ${space.title}.`);
  } else if (area === "pages") {
    body = pagesCard(d) || empty(`No pages in ${space.title}.`);
  } else {
    body = d.kinds.includes(area) ? `<div class="cards">${kindCard(area)}</div>` : empty(`No ${Model.areaTitle(area).toLowerCase()} data in ${space.title}${platform === "all" ? "" : " for " + platformName}.`);
  }
  const switcher = `<select id="space-switch" class="switcher" aria-label="Switch space, now ${esc(space.title)}">${dir.visible.map((s) => `<option value="${esc(s.id)}" ${s.id === space.id ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select>`;
  return `<div class="space-head" ${accentStyle(space.accent)}>${icon(space.icon, null, "ico huge")}<div class="grow"><div class="eyebrow">Space</div>${switcher}
      ${space.subtitle || d.products.length ? `<div class="sub">${esc(space.subtitle || d.products.map((p) => p.name).join(" · "))}</div>` : ""}</div></div>
    <div class="chips-row" ${accentStyle(space.accent)}>${areas}</div>${platforms}<div ${accentStyle(space.accent)}>${body}</div>`;
}
function pagesCard(d) {
  if (!d.sections.length) return "";
  return `<div class="card" style="margin-top:16px"><h2>Sections</h2><div class="rows">${d.sections.map((id) => sectionRow(state.dir.entries[id])).join("")}</div></div>`;
}

// ---------- Sections ----------
function browseView() {
  const sections = [...((state.catalog && state.catalog.sections) || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.title).localeCompare(String(b.title)));
  return header(esc(systemName()), "All sections", plural(sections.length, "section")) +
    `<div class="card"><div class="rows">${sections.map(sectionRow).join("") || `<div class="note">No sections yet.</div>`}</div></div>`;
}
function sectionView(id, nodeId, tabId) {
  const doc = state.sections[id];
  const entry = state.dir.entries[id];
  if (!doc) return entry || !state.catalog ? `<div class="loading"><div class="spinner"></div><p>Loading ${esc((entry && entry.title) || id)}…</p></div>` : `<div class="empty">No section <b>${esc(id)}</b>. <a href="#/browse">All sections</a></div>`;
  nodeId = nodeId && nodeId !== "-" ? nodeId : null;
  tabId = tabId && tabId !== "-" ? tabId : null;
  const space = state.dir.spaces.find((s) => s.sections.includes(id));
  const crumbs = [space ? (space.system ? `<a href="#/system/satellites">System</a>` : `<a href="#/space/${encodeURIComponent(space.id)}">${esc(space.title)}</a>`) : `<a href="#/browse">Sections</a>`];
  const node = nodeId ? (doc.nodes || []).find((n) => n.id === nodeId) : null;
  const holder = node || doc;
  if (node) crumbs.push(`<a href="${esc(Model.navRoute({ target: { kind: "section", section: id } }))}">${esc(doc.title)}</a>`);
  const tabs = holder.tabs || [];
  const tab = tabs.length ? tabs.find((t) => t.id === tabId) || tabs[0] : null;
  const blocks = tab ? tab.blocks || [] : holder.blocks || [];
  const ctx = { section: id, node: nodeId, tab: tab && tab.id };
  const nodes = !node ? (doc.nodes || []).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0)) : [];
  return `<div class="crumb">${crumbs.join(" › ")}</div>
    <div class="page-head" ${accentStyle(doc.accent)}>${icon(holder.icon || doc.icon, holder.icon_image || doc.icon_image, "ico huge")}<div class="grow"><h1>${esc(holder.title)}</h1>
      <div class="sub">${esc(holder.subtitle || doc.summary || "")}</div></div><div class="faint small">Updated ${agoSpan(doc.updated_at)} · rev ${esc(doc.revision ?? "")}</div></div>
    ${tabs.length > 1 ? `<div class="chips-row" ${accentStyle(doc.accent)}>${tabs.map((t) => `<a class="chip ${t === tab ? "on" : ""}" href="${esc(Model.navRoute({ target: { kind: "section", section: id, node: nodeId, tab: t.id } }))}">${t.icon ? icon(t.icon) : ""}${esc(t.title)}</a>`).join("")}</div>` : ""}
    ${nodes.length ? `<div class="card" style="margin-bottom:16px"><div class="rows">${nodes.map((n) => `<a class="rowline click" href="${esc(Model.navRoute({ target: { kind: "section", section: id, node: n.id } }))}">${icon(n.icon, n.icon_image)}<span class="grow"><b>${esc(n.title)}</b><div class="faint small">${esc(n.subtitle || "")}</div></span><span class="faint">›</span></a>`).join("")}</div></div>` : ""}
    <div class="blocks" ${accentStyle(doc.accent)}>${blocks.map((b) => renderBlock(b, ctx)).join("") || (nodes.length ? "" : `<div class="empty">This page is empty.</div>`)}</div>`;
}

// ---------- Nav groups and extra tabs ----------
function navGroupView(keyPath) {
  const item = Model.navItem(state.nav, keyPath, sidebarTree());
  if (!item) return `<div class="empty">This item was removed from the hub's navigation.</div>`;
  const children = item.children || [];
  return header(esc(systemName()), esc(item.title), plural(children.length, "item"), "", item.accent) +
    (children.length ? `<div class="card"><div class="rows">${children.map((c) => {
      const k = keyPath + "/" + c.id;
      const isLink = c.target && c.target.kind === "link";
      return `<a class="rowline click" href="${esc(isLink ? "#" : Model.navRoute(c, k))}" ${linkAttrs(c)} ${accentStyle(c.accent)}>${icon(c.icon || symbolFor(c))}<span class="grow"><b>${esc(c.title)}</b></span>
        ${c.target ? "" : `<span class="faint small">${(c.children || []).length}</span>`}<span class="faint">${isLink ? "↗" : "›"}</span></a>`;
    }).join("")}</div></div>` : `<div class="card note">Nothing in here yet. Add items to it in the hub's navigation (<code>orbital nav</code>).</div>`);
}
function navTabView(id) {
  const item = (state.nav.tabs || []).find((t) => t.id === id);
  if (!item) return `<div class="empty">This tab was removed. The hub's navigation no longer has it.</div>`;
  return navGroupView("tab:" + id);
}

// ---------- Sign in ----------
function signinView() {
  const s = state.session || {};
  return `<div class="signin"><h1>Connect to your hub</h1>
    <p class="lead">This app shows every machine on your Orbital Agents hub, the agents running on them, and your spaces.</p>
    ${s.error ? `<div class="banner bad" style="border-radius:10px;margin-bottom:16px">${esc(s.error)}</div>` : ""}
    <div class="cols">
      <div class="card"><h3>Pairing link</h3><p>On the hub run <code>orbital pair --link</code> and paste the <code>orbital://connect?…</code> link. No password needed.</p>
        <form class="stack" id="link-form"><input name="link" placeholder="orbital://connect?hub=…&amp;pair=…" autocomplete="off" spellcheck="false">
          <div class="row"><button class="primary" type="submit">Pair</button><span class="err" id="link-err"></span></div></form>
        <hr style="border:0;border-top:1px solid var(--line);margin:18px 0">
        <h3>This computer runs the hub</h3><p>Sign in with this computer's own hub, no typing.</p>
        <div class="row"><button id="local-btn">Use this computer's hub</button><span class="err" id="local-err"></span></div></div>
      <div class="card"><h3>Address and password</h3><p>The hub's address on your Tailscale network and the app sign-in from setup.</p>
        <form class="stack" id="pw-form" autocomplete="off">
          <label class="field">Hub address<input name="hub" placeholder="http://my-mac.tailnet.ts.net:7700" value="${esc(s.hub || "")}" spellcheck="false"></label>
          <div class="row"><button type="button" class="small" id="discover">Find hubs</button><span class="faint small" id="discover-note"></span></div>
          <div class="hubs" id="hubs"></div>
          <div class="two"><label class="field">Username<input name="username" value="${esc(s.username || "")}" autocomplete="username"></label>
            <label class="field">Password<input name="password" type="password" autocomplete="current-password"></label></div>
          <div class="row"><button class="primary" type="submit">Sign in</button><span class="err" id="pw-err"></span></div></form></div>
    </div></div>`;
}
function bindSignin() {
  const signedIn = (r) => { state.session = r; startSession(); };
  $("#link-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button"); btn.disabled = true; $("#link-err").textContent = "";
    const r = await call("sign_in_link", e.target.link.value);
    btn.disabled = false;
    if (r.ok) signedIn(r); else $("#link-err").textContent = r.error;
  };
  $("#local-btn").onclick = async (e) => {
    e.target.disabled = true; $("#local-err").textContent = "";
    const r = await call("sign_in_local");
    e.target.disabled = false;
    if (r.ok) signedIn(r); else $("#local-err").textContent = r.error;
  };
  $("#pw-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, btn = f.querySelector("button[type=submit]");
    btn.disabled = true; $("#pw-err").textContent = "";
    const r = await call("sign_in_password", f.hub.value, f.username.value, f.password.value);
    f.password.value = "";
    btn.disabled = false;
    if (r.ok) signedIn(r); else $("#pw-err").textContent = r.error;
  };
  $("#discover").onclick = async (e) => {
    e.target.disabled = true; $("#discover-note").textContent = "Looking on this computer and your Tailscale network…";
    const r = await call("discover");
    e.target.disabled = false;
    const hubs = (r.ok && r.hubs) || [];
    $("#discover-note").textContent = r.ok ? (hubs.length ? `Found ${hubs.length}` : "No hubs found. Type the address.") : r.error;
    $("#hubs").innerHTML = hubs.map((h) => `<button type="button" data-url="${esc(h.url)}"><span>${esc(h.name)}${h.is_self ? " (this computer)" : ""}</span><span class="faint mono">${esc(h.url)}</span></button>`).join("");
    $("#hubs").querySelectorAll("button").forEach((b) => (b.onclick = () => { $("#pw-form").hub.value = b.dataset.url; $("#pw-form").username.focus(); }));
  };
}

// ---------- view bindings ----------
function bindView(r) {
  const view = $("#view");
  view.querySelectorAll("[data-machine]").forEach((el) => {
    const open = () => go("#/machine/" + encodeURIComponent(el.dataset.machine));
    el.onclick = open;
    el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
  });
  view.querySelectorAll("[data-job]").forEach((el) => {
    el.onclick = () => { const [sid, ...jid] = el.dataset.job.split("/"); go(`#/job/${encodeURIComponent(sid)}/${encodeURIComponent(jid.join("/"))}`); };
  });
  view.querySelectorAll("[data-open]").forEach((el) => (el.onclick = (e) => { if (e.target.closest("a[href]:not([href='#'])") && e.target.closest("a") !== el) return; e.preventDefault(); go(el.dataset.open); }));
  view.querySelectorAll("[data-url]").forEach((el) => (el.onclick = (e) => { e.preventDefault(); openLink(el.dataset.url); }));
  view.querySelectorAll("[data-link]").forEach((el) => (el.onclick = (e) => { e.preventDefault(); openLink(el.dataset.link); }));
  view.querySelectorAll("a[target=_blank]").forEach((el) => (el.onclick = (e) => { e.preventDefault(); openLink(el.href); }));
  view.querySelectorAll("[data-action]").forEach((el) => (el.onclick = () => sectionAction(el, r)));
  if (r.name === "machine") bindMachine(r.args[0]);
  if (r.name === "job") bindJob(r.args[0], r.args[1]);
  const jf = $("#jobs-filter");
  if (jf) jf.querySelectorAll("button").forEach((b) => (b.onclick = () => { state.jobsFilter = b.dataset.v; render(); }));
  if (r.name === "alerts") {
    $("#alerts-mode").querySelectorAll("button").forEach((b) => (b.onclick = async () => { state.alertsMode = b.dataset.v; await loadAlerts(); render(); }));
    view.querySelectorAll("[data-ack]").forEach((b) => (b.onclick = () => ack([b.dataset.ack])));
    const all = $("#ack-all");
    if (all) all.onclick = () => ack(state.alerts.filter((a) => !a.acked).map((a) => a.id));
  }
  if (r.name === "space") {
    view.querySelectorAll("[data-area]").forEach((b) => (b.onclick = () => { prefs.set("space.area", b.dataset.area); render(); }));
    view.querySelectorAll("[data-platform]").forEach((b) => (b.onclick = () => { prefs.set("space.platform", b.dataset.platform); render(); }));
    // Switching keeps the chosen area (and the platform when the other space has it).
    const sw = $("#space-switch");
    if (sw) sw.onchange = (e) => go("#/space/" + encodeURIComponent(e.target.value));
  }
  if (r.name === "tracker" && !r.args[0] && $("#tracker-filter")) {
    $("#tracker-filter").querySelectorAll("button").forEach((b) => (b.onclick = () => { state.trackerFilter = b.dataset.v; render(); }));
    const s = $("#tracker-search");
    s.oninput = () => { state.trackerSearch = s.value; const pos = s.selectionStart; render(); const n = $("#tracker-search"); n.focus(); n.setSelectionRange(pos, pos); };
  }
  if (r.name === "system") bindSystem();
  if (r.name === "agent" && r.args[0] !== "jobs") bindDesk();
}
function bindSystem() {
  const g = $("#feed-group");
  if (g) g.onchange = () => { state.feedGroup = g.value; render(); };
  const all = $("#run-all");
  if (all) all.onclick = async () => {
    all.disabled = true;
    const r = await call("run_all_feeds", state.feedGroup);
    toast(r.ok ? "Started the feeds" : r.error, r.ok ? "ok" : "bad");
    await loadFeeds(); render();
  };
  document.querySelectorAll("[data-run-feed]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    const r = await call("run_feed", b.dataset.runFeed);
    toast(r.ok ? `Started ${b.dataset.runFeed}` : r.error, r.ok ? "ok" : "bad");
    await loadFeeds(); render();
  }));
  const reset = $("#nav-reset");
  if (reset) reset.onclick = async () => {
    if (reset.dataset.confirm !== "1") { reset.dataset.confirm = "1"; reset.textContent = "Click again to reset"; return; }
    const r = await call("reset_nav");
    toast(r.ok ? "Navigation back to the default" : r.error, r.ok ? "ok" : "bad");
    await loadNav(); render();
  };
  const rc = $("#reconnect");
  if (rc) rc.onclick = () => { poll(); loadCatalog().then(render); };
  const so = $("#sign-out-2");
  if (so) so.onclick = signOut;
}
function bindDesk() {
  const m = $("#messages");
  if (m) m.scrollTop = m.scrollHeight;
  const f = $("#desk-form");
  if (!f) return;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const text = f.text.value.trim();
    if (!text) return;
    state.deskSending = true; state.deskError = "";
    state.desk.messages = [...(state.desk.messages || []), { id: "local-" + Date.now(), role: "user", text, at: new Date().toISOString() }];
    render();
    const r = await call("desk_say", text);
    state.deskSending = false;
    if (!r.ok) state.deskError = r.error;
    await loadDesk();
    if (route().name === "agent") { render(); const i = $("#desk-form input"); if (i) i.focus(); }
  };
  if (!state.deskSending) f.text.focus();
}
async function sectionAction(el, r) {
  let body;
  try { body = JSON.parse(el.dataset.action); } catch { return; }
  if (r.name === "section") { if (r.args[1] && r.args[1] !== "-") body.node_id = r.args[1]; if (r.args[2] && r.args[2] !== "-") body.tab_id = r.args[2]; }
  el.disabled = true;
  const res = await call("section_action", r.args[0], body);
  el.disabled = false;
  toast(res.ok ? "Sent" : res.error, res.ok ? "ok" : "bad");
}
async function ack(ids) {
  const r = await call("ack_alerts", ids);
  if (!r.ok) return toast(r.error, "bad");
  await loadAlerts(); render();
}
function bindMachine(id) {
  const form = $("#run-form");
  if (!form) return;
  const save = () => { state.runForm[id] = { agent: form.agent.value, title: form.title.value, cwd: form.cwd.value, prompt: form.prompt.value }; };
  form.oninput = save; form.onchange = save;
  $("#goto-run").onclick = () => { $("#run").scrollIntoView({ behavior: "smooth" }); form.prompt.focus(); };
  $("#ping").onclick = async (e) => {
    e.target.disabled = true; $("#ping-out").textContent = "Pinging…";
    const r = await call("ping", id);
    e.target.disabled = false;
    $("#ping-out").textContent = r.ok ? `Answered in ${r.ping.ms} ms` : `No answer: ${r.error}`;
  };
  form.onsubmit = async (e) => {
    e.preventDefault(); save();
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; $("#run-err").textContent = "";
    const d = state.runForm[id];
    const r = await call("run_agent", id, d.agent, d.prompt, d.cwd, d.title);
    btn.disabled = false;
    if (!r.ok) { $("#run-err").textContent = r.error; return; }
    const jid = r.job && (r.job.job_id || r.job.id);
    state.runForm[id] = { agent: d.agent, cwd: d.cwd };
    toast(`Started ${d.agent || "the default agent"} on ${id}`, "ok");
    if (jid) go(`#/job/${encodeURIComponent(id)}/${encodeURIComponent(jid)}`);
  };
}
function bindJob(satId, id) {
  const log = $("#log");
  if (log && log.dataset.stick !== "0") log.scrollTop = log.scrollHeight;
  if (log) log.onscroll = () => { log.dataset.stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30 ? "1" : "0"; };
  const copy = $("#copy-log");
  if (copy) copy.onclick = async () => {
    try { await navigator.clipboard.writeText((state.jobLog && state.jobLog.log) || ""); toast("Log copied", "ok"); }
    catch { const range = document.createRange(); range.selectNodeContents(log); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); toast("Log selected; press Ctrl/Cmd+C"); }
  };
  const stop = $("#stop");
  if (stop) stop.onclick = async () => {
    if (stop.dataset.confirm !== "1") { stop.dataset.confirm = "1"; stop.textContent = "Click again to stop"; setTimeout(() => { if (stop.isConnected) { stop.dataset.confirm = ""; stop.textContent = "Stop"; } }, 4000); return; }
    stop.disabled = true; stop.textContent = "Stopping…";
    const r = await call("cancel_job", satId, id);
    if (!r.ok) { toast("Couldn't stop it: " + r.error, "bad"); stop.disabled = false; stop.textContent = "Stop"; return; }
    toast("Stop sent", "ok");
    refreshJob(satId, id);
  };
}

// ---------- refresh loops ----------
let jobTimer = null;
async function refreshJob(satId, id) {
  const [jr, lr] = await Promise.all([call("job", satId, id), call("job_log", satId, id)]);
  const r = route();
  if (r.name !== "job" || r.args[1] !== id) return;
  if (jr.ok) { state.job = { ...jr.job, satellite_id: satId }; state.jobError = ""; }
  else state.jobError = jr.error;
  if (lr.ok) state.jobLog = lr;
  else if (!state.jobLog) state.jobLog = { log: "Log not available: " + lr.error };
  render();
  clearTimeout(jobTimer);
  if (state.job && isRunning(state.job)) jobTimer = setTimeout(() => refreshJob(satId, id), JOB_POLL_MS);
}
async function onRoute() {
  const r = route();
  clearTimeout(jobTimer);
  if (r.name !== "job") { state.job = null; state.jobLog = null; state.jobError = ""; }
  if (r.name === "system" && !r.args[0]) { location.replace("#/system/" + prefs.get("system.pane", "satellites")); return; }
  // #/space/<id>/<area>[/<platform>] picks the area (and platform), which then stay picked like a chip tap.
  if (r.name === "space" && r.args[1]) {
    prefs.set("space.area", r.args[1]);
    if (r.args[2]) prefs.set("space.platform", r.args[2]);
    location.replace("#/space/" + encodeURIComponent(r.args[0]));
    return;
  }
  render();
  if (!state.session || !state.session.signed_in) return;
  if (r.name === "machine" && r.args[0]) { await loadMachineJobs(r.args[0]); render(); }
  else if (r.name === "job" && r.args[1]) {
    if (!state.job || jobId(state.job) !== r.args[1]) { state.job = null; state.jobLog = null; }
    refreshJob(r.args[0], r.args[1]);
  } else if (r.name === "agent" || r.name === "jobs") { await Promise.all([loadJobs(), r.args[0] === "jobs" ? null : loadDesk()]); render(); }
  else if (r.name === "alerts") { await loadAlerts(); render(); }
  else if (r.name === "system" && r.args[0] === "automations") { await loadFeeds(); render(); }
  else if (r.name === "tracker") {
    await loadTracker();
    if (r.args[0]) { const it = await call("tracker_item", r.args[0]); if (it.ok) state.trackerItem = it.item.item || it.item; }
    render();
  } else if (r.name === "home") { await Promise.all([loadJobs(), loadFeeds(), loadTracker()]); render(); }
}
let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  if (state.session && state.session.signed_in) {
    await loadSatellites();
    const r = route();
    if (["jobs", "machine", "home", "agent"].includes(r.name)) await loadJobs();
    if (r.name === "machine" && r.args[0]) await loadMachineJobs(r.args[0]);
    if (r.name === "home" || (r.name === "system" && r.args[0] === "automations")) await loadFeeds();
    if (r.name === "home" || r.name === "tracker") await loadTracker();
    loadAlerts().then(() => { if (route().name === "alerts") render(); });
    if (!["job"].includes(r.name) && !isTyping()) render();
    else renderChrome();
  }
  pollTimer = setTimeout(poll, POLL_MS);
}
function isTyping() {
  const a = document.activeElement;
  return a && ["INPUT", "TEXTAREA", "SELECT"].includes(a.tagName) && a.closest("#run-form, .signin, #desk-form, #tracker-search, #feed-group, #space-switch");
}
let eventsRunning = false;
async function eventLoop() {
  if (eventsRunning) return;
  eventsRunning = true;
  while (state.session && state.session.signed_in) {
    const r = await call("next_events", 25);
    if (!r || !r.ok) { await new Promise((res) => setTimeout(res, 3000)); continue; }
    for (const ev of r.events || []) handleEvent(ev);
  }
  eventsRunning = false;
}
let catalogTimer = null;
const rerender = () => { if (!isTyping() && route().name !== "job") render(); else renderChrome(); };
function handleEvent(ev) {
  switch (ev.type) {
    case "live": state.live = !!ev.connected; state.liveDetail = ev.detail || ""; renderLive(); break;
    case "hello": state.live = true; renderLive(); break;
    case "satellites":
      if (Array.isArray(ev.satellites)) {
        // The live list may lack fields the REST list has (browser_status etc.): merge by id.
        const old = Object.fromEntries(state.satellites.map((s) => [s.id, s]));
        state.satellites = ev.satellites.map((s) => ({ ...(old[s.id] || {}), ...s }));
        state.satFetchedAt = Date.now();
        rerender();
      }
      break;
    case "agent_job": {
      const r = route();
      loadJobs().then(() => { if (["jobs", "machine", "home", "agent"].includes(r.name)) rerender(); });
      loadSatellites();
      if (r.name === "machine") loadMachineJobs(r.args[0]).then(rerender);
      if (r.name === "job") refreshJob(r.args[0], r.args[1]);
      break;
    }
    case "alert": case "alerts": case "alerts_acked": case "alerts_retracted":
      loadAlerts().then(() => { if (route().name === "alerts") render(); });
      break;
    case "nav":
      // The hub's navigation changed: redraw the sidebar and extra items at once.
      if (ev.nav) { state.nav = ev.nav; state.navDefault = !ev.nav.sidebar && !(ev.nav.tabs || []).length; rerender(); }
      break;
    case "section":
      if (ev.section && ev.section.id) { state.sections[ev.section.id] = ev.section; rebuildDirectory(); rerender(); }
      break;
    case "section_deleted":
      delete state.sections[ev.id]; rebuildDirectory(); rerender(); break;
    case "catalog":
      clearTimeout(catalogTimer);
      catalogTimer = setTimeout(() => loadCatalog().then(rerender), 400);
      break;
    case "jobs":
      if (route().name === "system" || route().name === "home") loadFeeds().then(rerender);
      break;
    case "desk_message": case "desk_task":
      if (route().name === "agent" && !state.deskSending) loadDesk().then(rerender);
      break;
    case "signed_out":
      call("sign_out").then(() => { state.session = { signed_in: false, error: ev.reason }; stopSession(); render(); });
      break;
  }
}
async function startSession() {
  state.live = null;
  const want = location.hash && location.hash !== "#/signin" ? location.hash : (state.session.route ? "#/" + state.session.route : "#/home");
  if (location.hash !== want) location.hash = want; else onRoute();
  poll();
  eventLoop();
  const [info, app, sess] = await Promise.all([call("hub_info"), call("app_info"), call("session"), loadNav()]);
  if (sess.ok && typeof sess.live === "boolean" && state.live == null) state.live = sess.live;   // after a page reload
  if (info.ok) state.hubInfo = info.info;
  if (app.ok) state.appInfo = app;
  renderChrome();
  await loadCatalog();
  rerender();
  loadDesk();
  loadTracker();
}
function stopSession() {
  clearTimeout(pollTimer); clearTimeout(jobTimer);
  Object.assign(state, { satellites: [], satFetchedAt: 0, jobs: [], jobsLoaded: false, alerts: [], machineJobs: {}, catalog: null, sections: {},
    dir: Model.buildDirectory({}, {}), feeds: null, tracker: null, desk: null, hubInfo: null, navLoaded: false,
    nav: { schema: "orbital.nav.v1", tabs: [], sidebar: null } });
}
async function signOut() {
  await call("sign_out");
  stopSession();
  state.session = { signed_in: false, hub: state.session && state.session.hub, username: state.session && state.session.username };
  location.hash = "#/signin";
  render();
}

// ---------- start ----------
window.addEventListener("hashchange", onRoute);
setInterval(() => {
  document.querySelectorAll("[data-ago]").forEach((el) => { if (el.dataset.ago) el.textContent = ago(isNaN(el.dataset.ago) ? el.dataset.ago : Number(el.dataset.ago)); });
  document.querySelectorAll("[data-until]").forEach((el) => { if (el.dataset.until) el.textContent = until(el.dataset.until); });
  document.querySelectorAll("[data-since]").forEach((el) => { const t = parseTime(el.dataset.since); if (t) el.textContent = duration((Date.now() - t) / 1000); });
}, 1000);

document.addEventListener("DOMContentLoaded", async () => {
  $("#sign-out").onclick = signOut;
  $("#refresh").onclick = () => { poll(); onRoute(); loadCatalog().then(rerender); loadNav().then(rerender); };
  await bridge.ready;
  const r = await call("bootstrap");
  state.session = r.ok ? r : { signed_in: false, error: r.error };
  if (state.session.signed_in) {
    if (r.warning) setHubError(r.warning);
    startSession();
  } else render();
});
