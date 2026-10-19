/* Agent Satellite Tracker panel (Windows / Linux). State comes from the pywebview bridge
   (window.pywebview.api.poll); media loads from the tracker's own GET /v1/media route. */
(function () {
  "use strict";

  const PALETTE = [
    [255, 107, 107], [51, 189, 204], [255, 166, 51], [140, 107, 255],
    [92, 204, 115], [242, 102, 199], [77, 148, 255], [224, 194, 61],
  ];
  const AGENT_NAMES = { claude: "Claude", chatgpt: "ChatGPT", grok: "Grok", gemini: "Gemini", other: "AI agent" };
  const EVIDENCE = [
    ["off", "Off", "Agents attach screenshots when they normally would"],
    ["min1", "Min 1", "At least 1 screenshot for every UI, website or game change"],
    ["all", "All", "Every feature screenshotted (and filmed for games) for phone review"],
  ];

  const S = {
    data: null, version: "", filter: "all", view: "active", selected: null, expanded: null,
    range: "today", query: "", page: 0, pageSize: 10, menu: null, typing: false,
    openSubs: new Set(), lightbox: null, icons: {},
  };
  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;

  // ---------- helpers ----------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function color(i) { const n = PALETTE.length; return PALETTE[((i % n) + n) % n]; }
  function bubbleStyle(i) {
    const [r, g, b] = color(i);
    const mix = (c) => Math.round(c * 0.42 + 30 * 0.58);
    return `--c: rgb(${r},${g},${b}); --bg: rgba(${mix(r)},${mix(g)},${mix(b)},0.94); --edge: rgba(${r},${g},${b},0.75);`;
  }
  function date(s) { const d = s ? new Date(s) : null; return d && !isNaN(d) ? d : null; }
  function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  function clock(d) {
    if (!d) return "";
    const now = new Date();
    const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (sameDay(d, now)) return t;
    const days = (now - d) / 86400000;
    if (days < 6) return d.toLocaleDateString([], { weekday: "short" }) + " " + t;
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + t;
  }
  function duration(sec) {
    const s = Math.max(0, Math.floor(sec));
    if (s < 60) return s + "s";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m";
    const h = Math.floor(m / 60);
    if (h < 24) return m % 60 === 0 ? h + "h" : `${h}h ${m % 60}m`;
    return `${Math.floor(h / 24)}d ${h % 24}h`;
  }
  function ago(d) { if (!d) return ""; const s = (Date.now() - d) / 1000; return s < 45 ? "just now" : duration(s) + " ago"; }
  function normAgent(a) {
    const s = String(a || "").trim().toLowerCase();
    if (!s) return "other";
    if (s.includes("claude") || s.includes("anthropic")) return "claude";
    if (s.includes("chatgpt") || s.includes("openai") || s.includes("codex") || s.startsWith("gpt")) return "chatgpt";
    if (s.includes("grok") || s === "xai" || s.includes("x.ai")) return "grok";
    if (s.includes("gemini") || s.includes("google") || s.includes("antigravity") || s === "agy" || s.includes("bard")) return "gemini";
    return s;
  }
  function agentName(a) { const n = normAgent(a); return AGENT_NAMES[n] || (String(a || "AI").charAt(0).toUpperCase() + String(a || "AI").slice(1)); }
  function finishedAt(it) { return date(it.completedAt) || date(it.updatedAt) || date(it.createdAt); }
  function mediaUrl(owner, id, thumb) {
    return `http://127.0.0.1:${S.data.port}/v1/media/${encodeURIComponent(owner)}/${encodeURIComponent(id)}` + (thumb ? "?variant=thumb" : "");
  }
  function filterMatch(it) {
    if (S.filter === "ai") return it.source === "agent";
    if (S.filter === "mine") return it.source !== "agent";
    return true;
  }

  // ---------- agent icons ----------
  function glyph(agent) {
    const n = normAgent(agent);
    if (S.icons[n]) return `<img src="${S.icons[n]}" alt="">`;
    if (n === "claude") {
      const L = [0.34, 0.27, 0.33, 0.25, 0.35, 0.28, 0.32, 0.26, 0.34, 0.27, 0.31, 0.24];
      const lines = L.map((len, i) => {
        const a = (i / L.length) * Math.PI * 2 + 0.2;
        return `<line x1="${12 + Math.cos(a) * 1.2}" y1="${12 + Math.sin(a) * 1.2}" x2="${12 + Math.cos(a) * 24 * len}" y2="${12 + Math.sin(a) * 24 * len}"/>`;
      }).join("");
      return `<svg viewBox="0 0 24 24"><rect width="24" height="24" rx="5.5" fill="#d97857"/><g stroke="#fff" stroke-width="2" stroke-linecap="round">${lines}</g></svg>`;
    }
    if (n === "chatgpt") {
      const petals = [0, 60, 120, 180, 240, 300].map((r) => `<ellipse cx="12" cy="8.2" rx="3.1" ry="4.6" transform="rotate(${r} 12 12)"/>`).join("");
      return `<svg viewBox="0 0 24 24"><rect width="24" height="24" rx="5.5" fill="#10a37f"/><g fill="none" stroke="#fff" stroke-width="1.4">${petals}</g></svg>`;
    }
    if (n === "grok") {
      return `<svg viewBox="0 0 24 24"><rect width="24" height="24" rx="5.5" fill="#000"/><path d="M16.5 5.5 7 18.5" stroke="#fff" stroke-width="2" stroke-linecap="round"/><path d="M15.6 9.6a5 5 0 0 1-5.9 6.7M8.4 14.4a5 5 0 0 1 5.9-6.7" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/></svg>`;
    }
    if (n === "gemini") {
      return `<svg viewBox="0 0 24 24"><defs><linearGradient id="gg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f8cff"/><stop offset="0.55" stop-color="#9b72f2"/><stop offset="1" stop-color="#e46e8a"/></linearGradient></defs><rect width="24" height="24" rx="5.5" fill="#1f1f24"/><path d="M12 3.5c.6 4.4 4.1 7.9 8.5 8.5-4.4.6-7.9 4.1-8.5 8.5-.6-4.4-4.1-7.9-8.5-8.5 4.4-.6 7.9-4.1 8.5-8.5z" fill="url(#gg)"/></svg>`;
    }
    const letter = esc((n || "?").charAt(0).toUpperCase());
    return `<svg viewBox="0 0 24 24"><rect width="24" height="24" rx="5.5" fill="rgba(255,255,255,0.25)"/><text x="12" y="16.5" text-anchor="middle" font-size="13" font-weight="800" fill="#fff" font-family="system-ui, sans-serif">${letter}</text></svg>`;
  }
  function sourceIcon(it, big) {
    const cls = "agent" + (big ? " big" : "");
    if (it.source === "agent") return `<span class="${cls}" title="${esc(agentName(it.agent))}">${glyph(it.agent)}</span>`;
    const pencil = `<svg viewBox="0 0 24 24"><path d="M6 18l1-4 8.5-8.5 3 3L10 17zM14 7l3 3" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>`;
    return `<span class="${cls} you" title="You typed this">${pencil}</span>`;
  }
  function loadIcons(items) {
    const a = api();
    if (!a) return;
    const names = new Set();
    items.forEach((it) => { names.add(normAgent(it.agent)); (it.subagents || []).forEach((s) => names.add(normAgent(s.agent))); });
    names.forEach((n) => {
      if (n in S.icons) return;
      S.icons[n] = null;
      a.icon(n).then((url) => { if (url) { S.icons[n] = url; render(); } });
    });
  }

  // ---------- markdown (escaped first; a small, safe subset) ----------
  function inlineMd(text, ctx) {
    let s = esc(text);
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = s.replace(/!\[([^\]]*)\]\(\s*&lt;?([^)\s]+?)(?:&gt;)?(?:\s+&quot;[^&]*&quot;)?\s*\)/g, (_, alt, src) => mdImage(alt, src, ctx));
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, t, u) => `<a data-url="${u}">${t}</a>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/__([^_]+)__/g, "<b>$1</b>");
    s = s.replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>").replace(/(^|[^_\w])_([^_\s][^_]*)_(?!\w)/g, "$1<i>$2</i>");
    s = s.replace(/~~([^~]+)~~/g, "<s>$1</s>");
    s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
    return s;
  }
  function unesc(s) { return s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'"); }
  function mdImage(alt, rawSrc, ctx) {
    let src = unesc(rawSrc);
    if (src.startsWith("file://")) src = decodeURIComponent(src.slice(7)).replace(/^\/([A-Za-z]:)/, "$1");
    const hit = (ctx && ctx.media || []).find((m) => m.ref.originalPath === src || m.ref.originalPath.replace(/\\/g, "/") === src.replace(/\\/g, "/"));
    if (hit) {
      return `<img class="inline" src="${mediaUrl(hit.owner, hit.ref.id, hit.ref.kind !== "image")}" alt="${alt}" data-owner="${esc(hit.owner)}" data-media="${esc(hit.ref.id)}">`;
    }
    return `<span class="img-missing">🖼 ${alt || esc(src.split(/[\\/]/).pop())}</span>`;
  }
  function md(text, ctx) {
    if (!text) return "";
    const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let list = null, para = [], i = 0;
    const flushPara = () => { if (para.length) { out.push(`<p>${para.map((l) => inlineMd(l, ctx)).join("<br>")}</p>`); para = []; } };
    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        flushPara(); closeList();
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
        i++;
        continue;
      }
      let m;
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        flushPara(); closeList();
        const lvl = Math.min(m[1].length, 4);
        out.push(`<h${lvl}>${inlineMd(m[2], ctx)}</h${lvl}>`);
      } else if ((m = line.match(/^\s*[-*+]\s+\[([ xX])\]\s+(.*)$/))) {
        flushPara();
        if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
        const on = m[1] !== " ";
        out.push(`<li class="task"><span class="box${on ? " on" : ""}">${on ? "✓" : ""}</span><span>${inlineMd(m[2], ctx)}</span></li>`);
      } else if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
        flushPara();
        if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
        out.push(`<li>${inlineMd(m[1], ctx)}</li>`);
      } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
        flushPara();
        if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
        out.push(`<li>${inlineMd(m[1], ctx)}</li>`);
      } else if ((m = line.match(/^>\s?(.*)$/))) {
        flushPara(); closeList();
        out.push(`<blockquote>${inlineMd(m[1], ctx)}</blockquote>`);
      } else if (!line.trim()) {
        flushPara(); closeList();
      } else {
        closeList();
        para.push(line);
      }
      i++;
    }
    flushPara(); closeList();
    return `<div class="md">${out.join("")}</div>`;
  }

  // ---------- pieces ----------
  function thumbs(refs, owner) {
    if (!refs.length) return "";
    return `<div class="media">${refs.map((r) => {
      const label = r.kind === "video" ? (r.durationSeconds ? "▶ " + duration(r.durationSeconds) : "▶ video") : r.kind === "file" ? "file" : "";
      const name = esc((r.originalPath || "").split(/[\\/]/).pop());
      const inner = r.kind === "file" && !r.archivePath
        ? `<div class="gone">📄 ${name}</div>`
        : `<img src="${mediaUrl(owner, r.id, true)}" alt="" onerror="this.outerHTML='<div class=&quot;gone&quot;>${name}<br>not on this machine</div>'">`;
      return `<div class="thumb" data-owner="${esc(owner)}" data-media="${esc(r.id)}" data-kind="${r.kind}" title="${esc(r.caption || name)}">${inner}${label ? `<span class="kind">${label}</span>` : ""}</div>`;
    }).join("")}</div>`;
  }
  function notesHtml(notes) {
    if (!notes || !notes.length) return "";
    return `<div class="notes">${notes.slice(-60).map((n) => `<span class="t">${esc(clock(date(n.at)))}</span><span class="md">${inlineMd(n.text)}</span>`).join("")}</div>`;
  }
  function ctxFor(it) {
    const media = (it.media || []).map((ref) => ({ ref, owner: it.id }));
    (it.subagents || []).forEach((s) => (s.media || []).forEach((ref) => media.push({ ref, owner: s.id })));
    return { media };
  }
  function metaLine(it) {
    const who = it.source === "agent" ? (it.agentLabel || agentName(it.agent)) : (it.source === "voice" ? "You said this" : "You typed this");
    const created = date(it.createdAt);
    const parts = [esc(who)];
    if (it.machine) parts.push("on " + esc(it.machine));
    if (created) parts.push("started " + esc(clock(created)));
    if (it.status === "active") parts.push("running " + duration((Date.now() - created) / 1000));
    else {
      const f = finishedAt(it);
      if (f) parts.push("finished " + esc(clock(f)));
      if (f && created) parts.push("took " + duration((f - created) / 1000));
    }
    return parts.join(" · ");
  }
  function detailBody(it) {
    const ctx = ctxFor(it);
    let h = `<div class="meta">${metaLine(it)}`;
    if (it.workspace) h += `<div class="path">📁 <span>${esc(it.workspace)}</span></div>`;
    h += `</div>`;
    if (it.details) h += `<h5>Task</h5>${md(it.details, ctx)}`;
    if (it.summary) h += `<h5>Summary</h5>${md(it.summary, ctx)}`;
    if (it.notes && it.notes.length) h += `<h5>Progress</h5>${notesHtml(it.notes)}`;
    if (it.media && it.media.length) h += `<h5>Screenshots &amp; media</h5>${thumbs(it.media, it.id)}`;
    const subs = it.subagents || [];
    if (subs.length) {
      const running = subs.filter((s) => s.status === "active").length;
      h += `<h5>Subagents ${running ? "being worked on" : ""}<span class="aside">${running} running · ${subs.length - running} done</span></h5><div class="subs-list">`;
      subs.forEach((s) => {
        const open = S.openSubs.has(s.id);
        const first = (s.summary || s.details || "").split("\n").find((l) => l.trim()) || "";
        const when = s.status === "done" && s.completedAt ? duration((date(s.completedAt) - date(s.createdAt)) / 1000) : ago(date(s.updatedAt));
        h += `<div class="sub ${s.status === "done" ? "done" : ""}" data-sub="${esc(s.id)}">
          <div class="line"><span class="agent">${glyph(s.agent)}</span><span class="dot ${s.status === "done" ? "done" : ""}"></span>
          <span class="name">${esc(s.title)}</span><span class="when">${esc(when)}</span><span class="chev">${open ? "▾" : "▸"}</span></div>`;
        if (!open && first) h += `<div class="first">${esc(first)}</div>`;
        if (open) {
          const sctx = { media: (s.media || []).map((ref) => ({ ref, owner: s.id })) };
          h += `<div class="body"><div class="meta">${esc(s.agentLabel || agentName(s.agent))}${s.machine ? " · on " + esc(s.machine) : ""}</div>`;
          if (s.details) h += md(s.details, sctx);
          if (s.summary) h += `<h5>Summary</h5>${md(s.summary, sctx)}`;
          if (s.notes && s.notes.length) h += `<h5>Progress</h5>${notesHtml(s.notes)}`;
          if (s.media && s.media.length) h += `<h5>Media</h5>${thumbs(s.media, s.id)}`;
          h += `</div>`;
        }
        h += `</div>`;
      });
      h += `</div>`;
    }
    return h;
  }

  // ---------- views ----------
  function renderBar() {
    const d = S.data;
    const active = d.items.filter((i) => i.status === "active" && !i.dismissed).length;
    $("bar-sub").textContent = `${active} active · ${d.machine}`;
    $("status-dot").classList.toggle("live", true);
    document.querySelectorAll("#filter button").forEach((b) => b.classList.toggle("on", b.dataset.filter === S.filter));
    $("btn-history").classList.toggle("active", S.view === "history");
    $("btn-add").classList.toggle("active", S.typing);
    $("btn-more").classList.toggle("active", S.menu === "more");
    const ev = d.settings.evidence;
    const eb = $("btn-evidence");
    eb.classList.toggle("evidence-on", ev !== "off");
    eb.classList.toggle("active", S.menu === "evidence" && ev === "off");
    eb.title = `Screenshots from AI agents: ${d.settings.label}`;
    const badge = $("evidence-badge");
    badge.classList.toggle("hidden", ev === "off");
    badge.textContent = ev === "all" ? "ALL" : "1";
  }
  function renderMenu() {
    const m = $("menu");
    if (!S.menu) { m.classList.add("hidden"); return; }
    m.classList.remove("hidden");
    const d = S.data;
    if (S.menu === "evidence") {
      const by = { tracker: "this tracker", app: "Orbital Agents", cli: "the CLI" }[d.settings.updatedBy] || "default";
      m.innerHTML = `<h4>Screenshots from AI agents</h4>` + EVIDENCE.map(([id, label, hint]) =>
        `<button class="row" data-evidence="${id}"><span class="check">${d.settings.evidence === id ? "✓" : ""}</span><span><span class="label">${label}</span><span class="hint">${hint}</span></span></button>`).join("") +
        `<div class="kv"><span>Last changed</span><b>${d.settings.updatedAt ? esc(clock(date(d.settings.updatedAt))) + " by " + esc(by) : "never"}</b></div>`;
    } else {
      m.innerHTML = `<h4>This machine</h4>
        <div class="kv"><span>Name</span><b>${esc(d.machine)}</b></div>
        <div class="kv"><span>Role</span><b>${esc(d.role)}${d.relay ? " → " + esc(d.hubUrl) : ""}</b></div>
        <div class="kv"><span>Platform</span><b>${esc(d.platform)}</b></div>
        <div class="kv"><span>API</span><b>127.0.0.1:${d.port}</b></div>
        <div class="kv"><span>Version</span><b>${esc(d.version_app)}</b></div>
        <hr>
        <button class="row" data-act="data"><span class="check"></span><span class="label">Open data folder</span></button>
        <button class="row" data-act="log"><span class="check"></span><span class="label">Open log</span></button>
        <button class="row" data-act="hide"><span class="check"></span><span class="label">Hide panel</span></button>
        <button class="row" data-act="quit"><span class="check"></span><span class="label">Quit Agent Satellite Tracker</span></button>`;
    }
  }
  function renderActive() {
    const items = S.data.items.filter((i) => i.status === "active" && !i.dismissed && filterMatch(i))
      .sort((a, b) => (a.createdAt < b.createdAt ? -1 : 1));
    $("bubbles").innerHTML = items.map((it) => {
      const subs = it.subagents || [];
      const running = subs.filter((s) => s.status === "active").length;
      return `<div class="bubble live${S.selected === it.id ? " selected" : ""}" style="${bubbleStyle(it.colorIndex)}" data-id="${esc(it.id)}" title="${esc((it.details || it.title).slice(0, 200))}">
        ${sourceIcon(it)}<span class="title">${esc(it.title)}</span>
        ${subs.length ? `<span class="subs" title="${running} subagent(s) running">👥 ${running || subs.length}</span>` : ""}
        <span class="x" data-close="${esc(it.id)}" title="Mark done and clear">✕</span></div>`;
    }).join("");
    $("empty").classList.toggle("hidden", items.length > 0 || S.typing);
    const sel = S.selected && S.data.items.find((i) => i.id === S.selected);
    const card = $("detail");
    if (!sel) { card.classList.add("hidden"); if (S.selected && !S.data.items.some((i) => i.id === S.selected)) S.selected = null; return; }
    card.classList.remove("hidden");
    card.innerHTML = `<div class="head">${sourceIcon(sel, true)}<div class="grow"><h2>${esc(sel.title)}</h2>
      <div class="state ${sel.status === "done" ? "done" : ""}">${sel.status === "done" ? "Done" : "In progress"}</div></div>
      <button class="icon-btn" data-act="deselect" title="Close details">✕</button></div>
      ${detailBody(sel)}
      ${sel.status === "active" ? `<div class="actions"><button class="pill ok" data-done="${esc(sel.id)}">✓ Mark done</button></div>` : ""}`;
  }
  function inRange(d) {
    if (!d) return false;
    const now = new Date();
    if (S.range === "today") return sameDay(d, now);
    if (S.range === "week") {
      const start = new Date(now); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      return d >= start;
    }
    if (S.range === "month") return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
    return true;
  }
  function searchText(it) {
    const parts = [it.title, it.details, it.summary, it.agent, it.agentLabel, it.machine, it.workspace];
    (it.notes || []).forEach((n) => parts.push(n.text));
    (it.subagents || []).forEach((s) => parts.push(s.title, s.details, s.summary));
    return parts.filter(Boolean).join("\n").toLowerCase();
  }
  function highlight(text, q) {
    if (!q) return esc(text);
    const i = text.toLowerCase().indexOf(q);
    if (i < 0) return esc(text);
    return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
  }
  function renderHistory() {
    document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", b.dataset.range === S.range));
    const q = S.query.trim().toLowerCase();
    const items = S.data.items.filter((i) => i.status === "done" && filterMatch(i) && inRange(finishedAt(i)) && (!q || searchText(i).includes(q)))
      .sort((a, b) => finishedAt(b) - finishedAt(a));
    const pages = Math.max(1, Math.ceil(items.length / S.pageSize));
    S.page = Math.min(S.page, pages - 1);
    const slice = items.slice(S.page * S.pageSize, (S.page + 1) * S.pageSize);
    $("history-list").innerHTML = slice.length ? slice.map((it) => {
      const open = S.expanded === it.id;
      const f = finishedAt(it), c = date(it.createdAt);
      const who = it.source === "agent" ? agentName(it.agent) : "You";
      const subs = (it.subagents || []).length;
      return `<div class="h-row${open ? " open" : ""}" style="${bubbleStyle(it.colorIndex)}">
        <div class="line" data-expand="${esc(it.id)}"><span class="stripe"></span>${sourceIcon(it)}
          <div class="txt"><div class="ttl">${highlight(it.title, q)}</div>
          <div class="sub2">${esc(who)}${it.machine ? " · " + esc(it.machine) : ""}${subs ? ` · ${subs} subagent${subs > 1 ? "s" : ""}` : ""}${(it.media || []).length ? ` · ${(it.media || []).length} 📷` : ""}</div></div>
          <div class="right">${esc(clock(f))}<br>${f && c ? duration((f - c) / 1000) : ""}</div></div>
        ${open ? `<div class="expanded">${detailBody(it)}</div>` : ""}</div>`;
    }).join("") : `<div class="h-empty">${q ? "No finished work matches “" + esc(S.query) + "”." : "Nothing finished in this range yet."}</div>`;
    $("history-count").textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
    $("page-label").textContent = `${S.page + 1} / ${pages}`;
    $("page-prev").disabled = S.page === 0;
    $("page-next").disabled = S.page >= pages - 1;
    const sel = $("page-size");
    if (![...sel.options].some((o) => +o.value === S.pageSize)) sel.add(new Option(String(S.pageSize), String(S.pageSize)));
    sel.value = String(S.pageSize);
    if ($("search").value !== S.query) $("search").value = S.query;
  }
  let toastTimer = null;
  function renderToast() {
    const toast = S.data.toast;
    if (toast && toast.at !== S.toastAt) {
      S.toastAt = toast.at;
      S.toastText = toast.text;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { S.toastText = null; render(); }, Math.max(500, 4000 - toast.age * 1000));
    }
    const t = $("toast");
    t.classList.toggle("hidden", !S.toastText);
    t.textContent = S.toastText || "";
  }
  function renderLightbox() {
    const lb = $("lightbox");
    if (!S.lightbox) { lb.classList.add("hidden"); lb.innerHTML = ""; return; }
    const { owner, media, kind, caption } = S.lightbox;
    const url = mediaUrl(owner, media, false);
    lb.classList.remove("hidden");
    if (lb.dataset.key === owner + "/" + media) return;
    lb.dataset.key = owner + "/" + media;
    lb.innerHTML = (kind === "video" ? `<video src="${url}" controls autoplay muted playsinline onerror="this.outerHTML='<img src=&quot;${mediaUrl(owner, media, true)}&quot;>'"></video>` : `<img src="${url}" alt="">`) +
      `<div class="cap">${esc(caption || "")}</div><div class="row"><button class="pill" data-act="open-media">Open</button><button class="pill" data-act="close-lightbox">Close</button></div>`;
  }

  let fitTimer = null;
  function render() {
    if (!S.data) return;
    renderBar();
    renderMenu();
    renderToast();
    $("typing").classList.toggle("hidden", !S.typing);
    $("active-view").classList.toggle("hidden", S.view !== "active");
    $("history-view").classList.toggle("hidden", S.view !== "history");
    if (S.view === "active") renderActive(); else renderHistory();
    if (!S.lightbox) $("lightbox").dataset.key = "";
    renderLightbox();
    clearTimeout(fitTimer);
    fitTimer = setTimeout(fit, 30);
  }
  function fit() {
    const root = $("root");
    const h = S.lightbox ? Math.max(root.scrollHeight, 420) : root.scrollHeight;
    const a = api();
    if (a) a.fit(Math.ceil(h) + 2);
    setTimeout(() => document.body.classList.toggle("scroll", window.innerHeight + 1 < root.scrollHeight), 120);
  }
  function savePrefs() { const a = api(); if (a) a.save_prefs({ filter: S.filter, pageSize: S.pageSize, range: S.range }); }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-close],[data-id],[data-done],[data-filter],[data-range],[data-evidence],[data-act],[data-expand],[data-sub],.thumb,img.inline,a[data-url],#btn-history,#btn-evidence,#btn-add,#btn-more,#page-prev,#page-next,#type-ok");
    if (!t) return;
    const a = api();
    if (t.dataset.close) { e.stopPropagation(); if (a) a.close_item(t.dataset.close); if (S.selected === t.dataset.close) S.selected = null; return; }
    if (t.dataset.done) { if (a) a.mark_done(t.dataset.done); S.selected = null; render(); return; }
    if (t.dataset.id) { S.selected = S.selected === t.dataset.id ? null : t.dataset.id; S.openSubs.clear(); render(); return; }
    if (t.dataset.filter) { S.filter = t.dataset.filter; S.page = 0; savePrefs(); render(); return; }
    if (t.dataset.range) { S.range = t.dataset.range; S.page = 0; S.expanded = null; savePrefs(); render(); return; }
    if (t.dataset.evidence) { if (a) a.set_evidence(t.dataset.evidence); S.menu = null; render(); return; }
    if (t.dataset.expand) { S.expanded = S.expanded === t.dataset.expand ? null : t.dataset.expand; S.openSubs.clear(); render(); return; }
    if (t.dataset.sub) { const id = t.dataset.sub; if (e.target.closest(".thumb")) return openThumb(e.target.closest(".thumb")); S.openSubs.has(id) ? S.openSubs.delete(id) : S.openSubs.add(id); render(); return; }
    if (t.classList.contains("thumb") || (t.tagName === "IMG" && t.classList.contains("inline"))) return openThumb(t);
    if (t.dataset.url) { if (a) a.open_url(t.dataset.url); return; }
    switch (t.id || t.dataset.act) {
      case "btn-history": S.view = S.view === "history" ? "active" : "history"; S.menu = null; break;
      case "btn-evidence": S.menu = S.menu === "evidence" ? null : "evidence"; break;
      case "btn-more": S.menu = S.menu === "more" ? null : "more"; break;
      case "btn-add": S.typing = !S.typing; S.menu = null; if (S.typing) setTimeout(() => $("type-input").focus(), 50); break;
      case "type-ok": submitTyped(); return;
      case "page-prev": S.page = Math.max(0, S.page - 1); break;
      case "page-next": S.page += 1; break;
      case "deselect": S.selected = null; break;
      case "data": case "log": if (a) a.open_path(t.dataset.act); S.menu = null; break;
      case "hide": S.menu = null; if (a) a.hide(); break;
      case "quit": if (a) a.quit(); return;
      case "close-lightbox": S.lightbox = null; break;
      case "open-media": if (a && S.lightbox) a.open_media(S.lightbox.owner, S.lightbox.media); return;
      default: return;
    }
    render();
  });
  function openThumb(el) {
    S.lightbox = { owner: el.dataset.owner, media: el.dataset.media, kind: el.dataset.kind || "image", caption: el.title || el.alt || "" };
    render();
  }
  function submitTyped() {
    const v = $("type-input").value.trim();
    $("type-input").value = "";
    S.typing = false;
    const a = api();
    if (v && a) a.add_item(v);
    render();
  }
  $("type-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitTyped();
    if (e.key === "Escape") { S.typing = false; render(); }
  });
  $("search").addEventListener("input", (e) => { S.query = e.target.value; S.page = 0; render(); });
  $("page-size").addEventListener("change", (e) => { S.pageSize = Math.max(1, Math.min(999, parseInt(e.target.value, 10) || 10)); S.page = 0; savePrefs(); render(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (S.lightbox) S.lightbox = null; else if (S.menu) S.menu = null; else if (S.selected) S.selected = null; else return;
    render();
  });

  // Testing hook (POST /v1/debug/ui on the tracker's API).
  window.trackerDebug = function (o) {
    if (o.view === "active" || o.view === "history") S.view = o.view;
    if (o.filter) S.filter = o.filter;
    if (o.range) S.range = o.range;
    if ("select" in o) S.selected = o.select || null;
    if ("expand" in o) S.expanded = o.expand || null;
    if ("query" in o) S.query = String(o.query || "");
    if (typeof o.pageSize === "number") S.pageSize = Math.max(1, Math.min(999, o.pageSize));
    if (typeof o.page === "number") S.page = Math.max(0, o.page);
    if ("menu" in o) S.menu = o.menu || null;
    if ("typing" in o) { S.typing = !!o.typing; if (typeof o.typing === "string") $("type-input").value = o.typing; }
    if ("openSubs" in o) { S.openSubs = new Set(o.openSubs || []); }
    if ("lightbox" in o) {
      if (!o.lightbox) S.lightbox = null;
      else { const [owner, media] = String(o.lightbox).split("/"); const el = document.querySelector(`[data-owner="${owner}"][data-media="${media}"]`); S.lightbox = { owner, media, kind: (el && el.dataset.kind) || "image", caption: (el && el.title) || "" }; }
    }
    render();
    if (o.click) { const el = document.querySelector(o.click); if (el) el.click(); }
    if (typeof o.submitTyped === "string") { $("type-input").value = o.submitTyped; submitTyped(); }
    return { view: S.view, selected: S.selected, expanded: S.expanded, filter: S.filter, range: S.range, menu: S.menu, height: $("root").scrollHeight };
  };

  // ---------- polling ----------
  async function poll() {
    const a = api();
    if (!a) return;
    try {
      const state = await a.poll(S.version);
      if (state) {
        const first = !S.data;
        S.data = state;
        S.version = state.version;
        if (first && state.prefs) {
          if (["all", "ai", "mine"].includes(state.prefs.filter)) S.filter = state.prefs.filter;
          if (typeof state.prefs.pageSize === "number") S.pageSize = state.prefs.pageSize;
          if (["today", "week", "month", "all"].includes(state.prefs.range)) S.range = state.prefs.range;
        }
        loadIcons(state.items);
        render();
      }
    } catch (err) { /* the window is closing */ }
  }
  function start() {
    poll();
    setInterval(poll, 700);
    setInterval(() => { if (S.data && !S.typing) render(); }, 5000);  // keep "running 12m" current
  }
  if (window.pywebview && window.pywebview.api) start(); else window.addEventListener("pywebviewready", start);
})();
