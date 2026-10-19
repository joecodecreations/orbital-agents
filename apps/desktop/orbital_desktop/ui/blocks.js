/* Drawing what the hub sends: icons (SF Symbol names → glyphs), section blocks, and small SVG charts.
   Every string from the hub goes through esc(). Images under /v1/media are fetched by the Python side
   (the page never holds the token) and swapped in by hydrateMedia(). */
"use strict";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- icons ----------
const SYMBOLS = [
  [/^house/, "🏠"], [/^cpu|server|desktopcomputer|laptop/, "🖥"], [/^gearshape|gear/, "⚙️"], [/^sparkles/, "✨"],
  [/^bell/, "🔔"], [/radiowaves|antenna/, "📡"], [/^briefcase/, "💼"], [/^gamecontroller/, "🎮"], [/^building\.columns/, "🏛"],
  [/^sun|cloud|weather/, "☀️"], [/^folder/, "📁"], [/^doc\.on\.doc|doc\.text|^doc/, "📄"], [/^square\.grid/, "▦"],
  [/^square\.stack/, "🗂"], [/^globe/, "🌐"], [/^bubble/, "💬"], [/^person\.2|person/, "👥"], [/^magnifyingglass/, "🔍"],
  [/^dollarsign|banknote|creditcard/, "💲"], [/^play\.rectangle|play|video|film/, "▶️"], [/^chart|waveform/, "📈"],
  [/^lightbulb/, "💡"], [/^text|^list/, "📝"], [/^hand/, "✋"], [/^building/, "🏢"], [/^chart\.bar|chart\.pie/, "📊"], [/^megaphone/, "📣"], [/^cube/, "🧊"], [/^hand\.wave/, "👋"], [/^newspaper/, "📰"], [/^binoculars/, "🔭"],
  [/^link/, "🔗"], [/^clock|timer/, "⏱"], [/^slider|wrench|hammer/, "🛠"], [/^checkmark|checklist/, "✅"], [/^heart/, "❤️"],
  [/^star/, "⭐"], [/^bolt/, "⚡"], [/^lock|key/, "🔒"], [/^envelope|mail/, "✉️"], [/^calendar/, "📅"], [/^cart|bag/, "🛒"],
  [/^apple/, ""], [/^smartphone|iphone/, "📱"], [/^map|location/, "📍"], [/^photo|camera/, "🖼"], [/^book/, "📚"],
  [/^exclamationmark|warning/, "⚠️"], [/^info/, "ℹ️"], [/^questionmark/, "❔"], [/^flag/, "🚩"], [/^leaf/, "🍃"],
];
function glyph(symbol) {
  const s = String(symbol || "");
  for (const [re, g] of SYMBOLS) if (re.test(s)) return g;
  return "•";
}
/** An icon: an uploaded image (/v1/media/…) when there is one, else a glyph for the SF Symbol name. */
function icon(symbol, image, cls = "ico") {
  if (image && String(image).startsWith("/v1/")) return `<span class="${cls} img" data-media="${esc(image)}"></span>`;
  if (symbol && String(symbol).startsWith("/v1/")) return `<span class="${cls} img" data-media="${esc(symbol)}"></span>`;
  return `<span class="${cls}" aria-hidden="true">${esc(glyph(symbol))}</span>`;
}
const accentStyle = (accent) => (accent && /^#[0-9a-f]{6}$/i.test(accent) ? `style="--tint:${accent}"` : "");

const mediaCache = {};
async function hydrateMedia(root, call) {
  for (const el of root.querySelectorAll("[data-media]")) {
    const path = el.dataset.media;
    if (el.dataset.loaded === path) continue;
    el.dataset.loaded = path;
    const url = mediaCache[path] || (mediaCache[path] = call("media", path).then((r) => (r.ok ? r.url : null)));
    Promise.resolve(url).then((u) => {
      if (!u || !el.isConnected) return;
      if (el.tagName === "IMG") el.src = u;
      else el.style.backgroundImage = `url("${u}")`;
    });
  }
}

// ---------- numbers ----------
function num(v) {
  const n = Number(v);
  if (!isFinite(n)) return String(v ?? "–");
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
  if (a >= 1e4) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1) + "k";
  return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function money(v, currency = "USD") {
  try { return Number(v || 0).toLocaleString(undefined, { style: "currency", currency, maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2 }); }
  catch { return `${num(v)} ${currency}`; }
}

// ---------- charts ----------
const PALETTE = ["var(--accent)", "var(--accent-2)", "#3ecf8e", "#c084fc", "#f472b6", "#22d3ee", "#facc15"];
/** A line chart of [{name, points:[{x,y}]}]. Small, axis-light, readable in both themes. */
function lineChart(series, { height = 120, area = true, labels = true } = {}) {
  series = (series || []).filter((s) => (s.points || []).length);
  if (!series.length) return `<div class="note">No data yet</div>`;
  const W = 600, H = height, P = 4;
  const ys = series.flatMap((s) => s.points.map((p) => Number(p.y) || 0));
  const max = Math.max(...ys, 0), min = Math.min(...ys, 0);
  const span = max - min || 1;
  const n = Math.max(...series.map((s) => s.points.length));
  const X = (i) => P + (i * (W - 2 * P)) / Math.max(1, n - 1);
  const Y = (y) => H - P - ((y - min) * (H - 2 * P)) / span;
  const paths = series.map((s, si) => {
    const pts = s.points.map((p, i) => `${X(i + n - s.points.length).toFixed(1)},${Y(Number(p.y) || 0).toFixed(1)}`);
    const color = PALETTE[si % PALETTE.length];
    const fill = area && series.length === 1 ? `<polygon points="${X(n - s.points.length)},${H - P} ${pts.join(" ")} ${X(n - 1)},${H - P}" fill="${color}" opacity=".14"/>` : "";
    return `${fill}<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
  }).join("");
  const first = series[0].points[0], last = series[0].points[series[0].points.length - 1];
  const legend = labels && series.length > 1 ? `<div class="legend">${series.map((s, i) => `<span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(s.name || s.label || "")}</span>`).join("")}</div>` : "";
  const axis = labels ? `<div class="axis"><span>${esc(first.x)}</span><span>max ${esc(num(max))}</span><span>${esc(last.x)}</span></div>` : "";
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${paths}</svg>${axis}${legend}`;
}
function barChart(series, { height = 120 } = {}) {
  const s = (series || [])[0];
  if (!s || !(s.points || []).length) return `<div class="note">No data yet</div>`;
  const W = 600, H = height, n = s.points.length, max = Math.max(...s.points.map((p) => Number(p.y) || 0), 1);
  const bw = (W / n) * 0.7;
  const bars = s.points.map((p, i) => { const h = ((Number(p.y) || 0) / max) * (H - 6); return `<rect x="${(i * W) / n + (W / n - bw) / 2}" y="${H - h}" width="${bw}" height="${h}" rx="2" fill="var(--accent)"/>`; }).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${bars}</svg>
    <div class="axis"><span>${esc(s.points[0].x)}</span><span>max ${esc(num(max))}</span><span>${esc(s.points[n - 1].x)}</span></div>`;
}
const latest = (points) => (points && points.length ? points[points.length - 1].y : null);
const total = (points) => (points || []).reduce((t, p) => t + (Number(p.y) || 0), 0);

// ---------- series & ledgers (the space page's cards) ----------
function seriesCard(block, { eyebrow, open } = {}) {
  const metrics = block.metrics || [];
  return `<div class="card series" ${open ? `data-open="${esc(open)}"` : ""}>
    <div class="eyebrow">${esc(eyebrow || Model.areaTitle(block.kind))}${open ? ` <span class="faint">›</span>` : ""}</div>
    <h3>${esc(block.product || block.title || "")}</h3>${block.product && block.title ? `<div class="faint small">${esc(block.title)}</div>` : ""}
    <div class="tiles">${metrics.map((m) => `<div class="tile"><div class="k">${esc(m.label || m.id)}</div><div class="v">${esc(num(latest(m.points)))}</div>
      <div class="faint small">${esc(num(total(m.points)))} in ${m.points ? m.points.length : 0} days</div></div>`).join("")}</div>
    ${lineChart(metrics.map((m) => ({ name: m.label, points: m.points })))}</div>`;
}
/** Several series of one kind in a space: one chart per metric label, a line per product. */
function rollupCard(kind, refs, linkOf) {
  const byLabel = {};
  for (const r of refs) for (const m of r.block.metrics || []) (byLabel[m.label || m.id] = byLabel[m.label || m.id] || []).push({ name: r.block.product || r.block.title, points: m.points, ref: r });
  const labels = Object.keys(byLabel);
  return `<div class="card series wide"><div class="eyebrow">${esc(Model.areaTitle(kind))}</div>
    <div class="rollup">${labels.slice(0, 6).map((label) => `<div><h3 style="margin-top:6px">${esc(label)}</h3>
      ${lineChart(byLabel[label], { height: 90 })}
      <div class="rows">${byLabel[label].map((s) => `<div class="rowline click" data-open="${esc(linkOf(s.ref))}"><span class="grow ellipsis">${esc(s.name)}</span>
        <b>${esc(num(latest(s.points)))}</b><span class="faint small">${esc(num(total(s.points)))} total</span></div>`).join("")}</div></div>`).join("")}</div>
    ${labels.length > 6 ? `<div class="note">+${labels.length - 6} more</div>` : ""}</div>`;
}
function ledgerTotals(ledgers) {
  const now = Date.now(), day = 86400000;
  const sums = {};
  for (const l of ledgers) {
    const cur = l.block.currency || "USD";
    const s = (sums[cur] = sums[cur] || { d7: 0, d30: 0, all: 0 });
    for (const d of l.block.days || []) {
      const t = Date.parse(d.date), a = Number(d.amount) || 0;
      s.all += a;
      if (now - t <= 7 * day) s.d7 += a;
      if (now - t <= 30 * day) s.d30 += a;
    }
  }
  return sums;
}
function moneySummary(ledgers, title = "Money") {
  const sums = ledgerTotals(ledgers);
  const rows = Object.entries(sums);
  return `<div class="card money"><div class="eyebrow">${esc(title)}</div>
    ${rows.length ? rows.map(([cur, s]) => `<div class="tiles"><div class="tile"><div class="k">Last 7 days</div><div class="v">${esc(money(s.d7, cur))}</div></div>
      <div class="tile"><div class="k">Last 30 days</div><div class="v">${esc(money(s.d30, cur))}</div></div>
      <div class="tile"><div class="k">All shown</div><div class="v">${esc(money(s.all, cur))}</div></div></div>`).join("") : `<div class="note">No money recorded.</div>`}
    <div class="rows">${ledgers.map((l) => `<div class="rowline"><span class="grow ellipsis">${esc(l.title)}</span><span class="faint small">${esc(l.block.source || "")}</span>
      <b>${esc(money((l.block.days || []).reduce((t, d) => t + (Number(d.amount) || 0), 0), l.block.currency || "USD"))}</b></div>`).join("")}</div></div>`;
}

// ---------- blocks ----------
const TONES = { info: "accent", success: "ok", ok: "ok", warning: "warn", warn: "warn", danger: "bad", error: "bad", critical: "bad" };
const tone = (t) => TONES[String(t || "").toLowerCase()] || "";
function md(text) {
  // Just enough: `code`, **bold**, links. Everything else stays literal (escaped first).
  return esc(text).replace(/!\[([^\]]*)\]\((\/v1\/[^)\s]+)\)/g, '<img class="pic inline" alt="$1" data-media="$2">')
    .replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>').replace(/\n/g, "<br>");
}
function actionAttrs(item, sectionId) {
  if (item.url) return `data-url="${esc(item.url)}"`;
  const a = item.action || {};
  if (a.type === "open") return `data-open="${esc(sectionRoute(a.section_id || sectionId, a.node_id, a.tab_id))}"`;
  if (a.type === "post" || a.action) return `data-action="${esc(JSON.stringify({ type: a.action || a.id, value: a.value ?? {}, block_id: item.block, item_id: item.id, source: "button" }))}"`;
  return "";
}
function sectionRoute(section, node, tab) {
  return "#/section/" + [section, node || "-", tab || "-"].map(encodeURIComponent).join("/").replace(/(\/-)+$/, "");
}

function renderBlock(b, ctx = {}) {
  const title = b.title ? `<h3>${esc(b.title)}</h3>` : "";
  switch (b.type) {
    case "heading": return `<div class="blk heading h${b.level || 2}">${esc(b.text)}</div>`;
    case "paragraph": return `<div class="blk para">${md(b.text)}</div>`;
    case "divider": return `<hr class="blk divider">`;
    case "callout": return `<div class="blk callout ${tone(b.tone)}">${b.title ? `<b>${esc(b.title)}</b> ` : ""}${md(b.text || b.body || "")}</div>`;
    case "bullets": {
      const tag = b.ordered ? "ol" : "ul";
      return `<div class="blk card">${title}<${tag}>${(b.items || []).map((i) => `<li>${md(typeof i === "string" ? i : i.text || i.title)}</li>`).join("")}</${tag}></div>`;
    }
    case "metrics": return `<div class="blk tiles">${(b.items || []).map((m) => `<div class="tile ${tone(m.tone)}"><div class="k">${esc(m.label)}</div>
      <div class="v">${esc(m.value)}${m.unit ? `<small> ${esc(m.unit)}</small>` : ""}</div>
      ${m.delta ? `<div class="delta ${m.direction === "down" ? "down" : "up"}">${esc(m.delta)}</div>` : ""}${m.caption ? `<div class="faint small">${esc(m.caption)}</div>` : ""}</div>`).join("")}</div>`;
    case "key_value": return `<div class="blk card">${title}<dl class="kv">${(b.items || []).map((i) => `<dt>${esc(i.label)}</dt><dd>${esc(i.value)}</dd>`).join("")}</dl></div>`;
    case "table": return `<div class="blk card">${title}<div class="scroll-x"><table class="jobs"><thead><tr>${(b.columns || []).map((c) => `<th>${esc(typeof c === "string" ? c : c.title || c.label)}</th>`).join("")}</tr></thead>
      <tbody>${(b.rows || []).slice(0, 500).map((r) => `<tr>${(Array.isArray(r) ? r : r.cells || Object.values(r)).map((c) => `<td>${esc(typeof c === "object" && c ? c.text ?? c.value : c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      ${b.caption ? `<div class="note">${esc(b.caption)}</div>` : ""}</div>`;
    case "chart": {
      const series = (b.series || []).map((s) => ({ name: s.name, points: s.points || [] }));
      return `<div class="blk card">${title}${b.chart === "bar" ? barChart(series) : lineChart(series)}${b.caption ? `<div class="note">${esc(b.caption)}</div>` : ""}</div>`;
    }
    case "series": return `<div class="blk">${seriesCard(b)}</div>`;
    case "ledger": return `<div class="blk">${ledgerCard(b)}</div>`;
    case "product": return `<div class="blk card product ${tone(b.tone)}"><div class="eyebrow">${esc(b.kind || "product")}${b.company ? " · " + esc(b.company) : ""}</div>
      <h3>${esc(b.headline || b.title || "")}</h3>
      ${(b.metrics || []).length ? `<div class="tiles">${b.metrics.map((m) => `<div class="tile"><div class="k">${esc(m.label)}</div><div class="v">${esc(m.value)}</div></div>`).join("")}</div>` : ""}
      ${(b.releases || []).length ? `<div class="rows">${b.releases.map((r) => `<div class="rowline"><span class="grow">${esc(r.store || r.platform || "")}</span><b>${esc(r.version || "")}</b><span class="faint small">${esc(r.status || r.state || "")}</span></div>`).join("")}</div>` : ""}
      ${(b.links || []).length ? `<div class="chips">${b.links.map((l) => `<a class="badge" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label || l.title || l.url)}</a>`).join("")}</div>` : ""}</div>`;
    case "tasks": return `<div class="blk card">${title}${(b.items || []).length ? `<div class="rows">${b.items.map((t) => `<div class="rowline"><span class="check ${t.done ? "on" : ""}">${t.done ? "✓" : ""}</span>
      <span class="grow ${t.done ? "faint strike" : ""}">${esc(t.title)}</span>${t.due ? `<span class="faint small">${esc(t.due)}</span>` : ""}</div>`).join("")}</div>` : `<div class="note">${esc(b.empty_text || "Nothing here.")}</div>`}</div>`;
    case "timeline": return `<div class="blk card">${title}<div class="timeline">${(b.items || []).map((i) => `<div class="tl ${tone(i.tone)}"><i></i><div><div class="faint small">${esc(i.at || "")}</div><b>${esc(i.title)}</b>${i.detail ? `<div class="small">${md(i.detail)}</div>` : ""}</div></div>`).join("")}</div></div>`;
    case "hero": return `<div class="blk hero">${b.image && b.image.src ? `<span class="hero-img" data-media="${esc(b.image.src)}"></span>` : ""}<div>
      ${b.eyebrow ? `<div class="eyebrow">${esc(b.eyebrow)}</div>` : ""}<h2>${esc(b.title)}</h2>${b.subtitle ? `<div class="sub">${esc(b.subtitle)}</div>` : ""}
      <div class="row wrap">${(b.actions || []).map((a) => `<button class="${a.style === "primary" ? "primary" : ""}" ${actionAttrs(a, ctx.section)}>${esc(a.label)}</button>`).join("")}</div></div></div>`;
    case "image": return `<figure class="blk card"><img alt="${esc(b.alt || "")}" data-media="${esc(b.src)}" class="pic">${b.caption ? `<figcaption class="note">${esc(b.caption)}</figcaption>` : ""}</figure>`;
    case "buttons": return `<div class="blk row wrap">${(b.items || []).map((i) => `<button class="${i.style === "primary" ? "primary" : ""}" ${actionAttrs({ ...i, block: b.id }, ctx.section)}>${esc(i.label)}</button>`).join("")}</div>`;
    case "videos": return `<div class="blk card">${b.product ? `<div class="eyebrow">${esc(b.product)}</div>` : ""}${title}
      ${(b.experiments || []).length ? `<div class="chips">${b.experiments.map((e) => `<button class="small" ${actionAttrs(e, ctx.section)} title="${esc(e.detail || "")}">${esc(e.label)}</button>`).join("")}</div>` : ""}
      <div class="rows">${(b.items || []).slice(0, 60).map((v) => `<div class="rowline"><span class="grow ellipsis">${esc(v.title || v.id)}</span>
        ${(v.posts || []).map((p) => p.url ? `<a class="badge ${p.status === "posted" ? "ok" : ""}" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.platform)}</a>` : `<span class="badge">${esc(p.platform)} · ${esc(p.status || "")}</span>`).join("")}</div>`).join("")}</div>
      ${(b.items || []).length > 60 ? `<div class="note">+${b.items.length - 60} more</div>` : ""}</div>`;
    case "insight_cards": return `<div class="blk card">${title}${(b.cards || []).length ? b.cards.map((c) => `<div class="insight ${c.severity === "high" ? "bad" : c.severity === "medium" ? "warn" : ""}">
      <div class="row"><b class="grow">${esc(c.title)}</b>${c.deadline ? `<span class="badge warn">${esc(c.deadline_label || "Due")} ${esc(c.deadline)}</span>` : ""}</div>
      ${c.summary ? `<div class="small">${esc(c.summary)}</div>` : ""}<div class="faint small">${esc([c.product, c.state].filter(Boolean).join(" · "))}</div></div>`).join("") : `<div class="note">${esc(b.empty_text || "Nothing needs you.")}</div>`}</div>`;
    default: {
      const local = window.ORBITAL_PRIVATE_BLOCKS && window.ORBITAL_PRIVATE_BLOCKS[b.type];   // private_blocks.js, local-only
      if (local) return local(b, ctx, title);
      return `<div class="blk card"><div class="eyebrow">${esc(b.type || "block")}</div>${title}<div class="note">This block type isn't drawn on the desktop app yet.</div></div>`;
    }
  }
}
function ledgerCard(b) {
  const days = b.days || [], entries = b.entries || [];
  const sum = days.reduce((t, d) => t + (Number(d.amount) || 0), 0);
  return `<div class="card ledger"><div class="eyebrow">${esc(b.source || "Ledger")}${b.connected === false ? ` · <span class="bad-text">not connected</span>` : ""}</div>
    <h3>${esc(b.title || "")}</h3><div class="tiles"><div class="tile"><div class="k">${days.length} days</div><div class="v">${esc(money(sum, b.currency || "USD"))}</div></div></div>
    ${barChart([{ points: days.map((d) => ({ x: d.date, y: d.amount })) }], { height: 80 })}
    ${entries.length ? `<div class="rows">${entries.slice(0, 20).map((e) => `<div class="rowline"><span class="grow ellipsis">${esc(e.label || e.title || e.product || "")}</span><span class="faint small">${esc(e.date || e.at || "")}</span><b>${esc(money(e.amount, b.currency || "USD"))}</b></div>`).join("")}</div>` : ""}
    ${b.note ? `<div class="note">${esc(b.note)}</div>` : ""}</div>`;
}
