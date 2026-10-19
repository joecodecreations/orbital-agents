/* The data model behind the sidebar and the space pages, as plain functions over what the hub sends:
   GET /v1/catalog (sections + spaces with aliases), GET /v1/sections/<id>, GET /v1/nav (orbital.nav.v1).
   Mirrors apps/orbital-app (Models/Spaces.swift, Models/Navigation.swift, Views/SpaceViews.swift) so the
   desktop app shows the same spaces, areas and platforms. No DOM here; tests run it under node. */
"use strict";

const Model = (() => {
  /** "Home Lab LLC" → "homelabllc": the key spaces, aliases and products are matched on. */
  const key = (name) => String(name ?? "").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

  /** Spaces filed under System instead of shown as a space (machine health pages and the like). */
  const SYSTEM_SPACES = new Set(["system"]);
  const isSystemSpace = (id) => SYSTEM_SPACES.has(key(id));

  /** Areas a space page offers, in order (SpaceArea in SpaceViews.swift). */
  const AREA_ORDER = ["site", "social", "players", "usage", "search"];
  const AREA_TITLES = { overview: "Overview", site: "Site traffic", social: "Social media", players: "Players", usage: "Usage",
                        search: "Search", money: "Money", videos: "Videos", pages: "Pages", health: "Health", other: "Other" };
  const AREA_ICONS = { overview: "square.grid.2x2", site: "globe", social: "bubble.left.and.bubble.right", players: "gamecontroller",
                       usage: "person.2", search: "magnifyingglass", money: "dollarsign.circle", videos: "play.rectangle", pages: "doc.on.doc" };
  const areaTitle = (id) => AREA_TITLES[id] || String(id || "").replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  /** Areas that the platform filter applies to. */
  const PLATFORM_AREAS = ["overview", "money", "players", "site", "social", "usage", "search"];

  /** Where money comes from, read off a ledger's title and source (SpacePlatform). */
  const PLATFORM_ORDER = ["ios", "android", "roblox", "web"];
  const PLATFORM_TITLES = { ios: "iOS", android: "Android", roblox: "Roblox", web: "Web" };
  function platformOf(ledger) {
    const b = ledger.block || ledger;
    const text = [ledger.title, b.title, b.source].filter(Boolean).join(" ").toLowerCase();
    if (text.includes("app store") || /\bios\b/.test(text) || text.includes("apple")) return "ios";
    if (text.includes("google play") || text.includes("android") || text.includes("play store")) return "android";
    if (text.includes("robux") || text.includes("roblox")) return "roblox";
    if (text.includes("stripe") || text.includes("web")) return "web";
    return null;
  }

  /** Every block in a section with where it sits: {block, loc: {section, node?, tab?}, node?}. */
  function collectBlocks(doc) {
    const out = [];
    if (!doc || !doc.id) return out;
    const push = (blocks, loc, node) => (blocks || []).forEach((block) => block && out.push({ block, loc, node }));
    push(doc.blocks, { section: doc.id });
    (doc.tabs || []).forEach((t) => push(t.blocks, { section: doc.id, tab: t.id }));
    (doc.nodes || []).forEach((n) => {
      push(n.blocks, { section: doc.id, node: n.id }, n);
      (n.tabs || []).forEach((t) => push(t.blocks, { section: doc.id, node: n.id, tab: t.id }, n));
    });
    return out;
  }

  /** Spaces from the catalog, their aliases, and the blocks the space pages read.
      catalog = {sections, spaces}; sections = {id: document}. */
  function buildDirectory(catalog, sections) {
    catalog = catalog || {};
    sections = sections || {};
    const aliases = {};
    const spaces = [];
    for (const raw of catalog.spaces || []) {
      const id = key(raw.id);
      if (!id) continue;
      aliases[id] = id;
      aliases[key(raw.title)] = aliases[key(raw.title)] || id;
      for (const a of raw.aliases || []) if (key(a)) aliases[key(a)] = id;
      spaces.push({ id, rawId: raw.id, title: raw.title || raw.id, subtitle: raw.subtitle || "", icon: raw.icon || "square.grid.2x2",
                    accent: raw.accent || null, order: raw.order ?? 10000, sections: [...(raw.sections || [])],
                    system: isSystemSpace(raw.id) });
    }
    const resolve = (raw) => { const k = key(raw); return aliases[k] || k; };
    const entries = Object.fromEntries((catalog.sections || []).map((s) => [s.id, s]));
    const series = [], ledgers = [], products = [], videos = [];
    // Catalog order (the left-bar order), so products and charts don't move with load order.
    const order = [...(catalog.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.title).localeCompare(String(b.title))).map((s) => s.id);
    const ids = [...order.filter((id) => sections[id]), ...Object.keys(sections).filter((id) => !order.includes(id))];
    for (const id of ids) {
      for (const row of collectBlocks(sections[id])) {
        const b = row.block;
        if (b.type === "series") series.push({ block: b, loc: row.loc, space: resolve(b.space), product: key(b.product) });
        else if (b.type === "ledger") ledgers.push({ block: b, loc: row.loc, space: resolve(b.space), title: b.title || entries[id]?.title || "Ledger" });
        else if (b.type === "videos") videos.push({ block: b, loc: row.loc, space: resolve(b.company || b.space) });
        else if (b.type === "product") {
          const name = b.title || b.name || (row.node && row.node.title) || sections[id].title;
          products.push({ block: b, loc: { section: id, node: row.loc.node }, id: key(name), name,
                          icon: row.node?.icon || sections[id].icon, image: row.node?.icon_image || null,
                          space: resolve(b.space || b.company) });
        }
      }
    }
    const visible = spaces.filter((s) => !s.system).sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
    const systemSections = spaces.filter((s) => s.system).flatMap((s) => s.sections);
    return { spaces, visible, aliases, resolve, entries, series, ledgers, products, videos, systemSections,
             space: (id) => spaces.find((s) => s.id === key(id)) || null };
  }

  /** What a space page shows for one space, platform and area (SpaceView.refs / ledgers / areas). */
  function spaceData(dir, spaceId, platform) {
    const space = dir.space(spaceId);
    const id = space ? space.id : key(spaceId);
    const mine = new Set(space ? space.sections : []);
    const inSpace = (row) => row.space === id || mine.has(row.loc.section);
    const allLedgers = dir.ledgers.filter(inSpace);
    // Products by the platforms their money comes from (App Store → iOS, Google Play → Android, Robux → Roblox).
    const productPlatforms = {};
    for (const l of allLedgers) {
      const p = platformOf(l);
      if (!p) continue;
      const add = (name) => { const k = key(name); if (k) (productPlatforms[k] = productPlatforms[k] || new Set()).add(p); };
      add(String(l.title).split(" · ")[0]);
      for (const d of l.block.days || []) if (d.product) add(d.product);
    }
    const found = new Set(Object.values(productPlatforms).flatMap((s) => [...s]));
    const platforms = PLATFORM_ORDER.filter((p) => found.has(p));
    const filtering = platform && platform !== "all" && platforms.length > 1;
    let refs = dir.series.filter(inSpace);
    let ledgers = allLedgers;
    if (filtering) {
      refs = refs.filter((r) => r.product && productPlatforms[r.product] && productPlatforms[r.product].has(platform));
      ledgers = ledgers.filter((l) => platformOf(l) === platform);
    }
    const kinds = [];
    for (const r of refs) if (!kinds.includes(r.block.kind)) kinds.push(r.block.kind);
    const videos = dir.videos.filter((v) => mine.has(v.loc.section));
    const areas = [{ id: "overview", title: "Overview", icon: AREA_ICONS.overview }];
    for (const k of AREA_ORDER) if (kinds.includes(k)) areas.push({ id: k, title: areaTitle(k), icon: AREA_ICONS[k] });
    for (const k of kinds) if (!AREA_ORDER.includes(k)) areas.push({ id: k, title: areaTitle(k), icon: "chart.xyaxis.line" });
    if (ledgers.length || allLedgers.length) areas.push({ id: "money", title: "Money", icon: AREA_ICONS.money });
    if (videos.length) areas.push({ id: "videos", title: "Videos", icon: AREA_ICONS.videos });
    areas.push({ id: "pages", title: "Pages", icon: AREA_ICONS.pages });
    const products = dir.products.filter((p) => p.space === id || mine.has(p.loc.section));
    return { space, id, refs, ledgers, allLedgers, platforms, kinds, videos, areas, products,
             sections: (space ? space.sections : []).filter((s) => dir.entries[s]) };
  }

  /** The platform to keep after switching to a space that may not have it (SpaceView.onChange). */
  const keepPlatform = (platform, platforms) => (platform !== "all" && !platforms.includes(platform) ? "all" : platform);

  // ---------- navigation (orbital.nav.v1) ----------

  /** The default sidebar: each space with an Overview and its sections, then loose sections. */
  function defaultSidebar(dir, catalog) {
    const filed = new Set(dir.systemSections);
    const out = dir.visible.map((s) => {
      s.sections.forEach((id) => filed.add(id));
      const pages = s.sections.filter((id) => dir.entries[id]).map((id) => {
        const e = dir.entries[id];
        return { id, title: e.title, icon: e.icon_image || e.icon, accent: e.accent, target: { kind: "section", section: id } };
      });
      return { id: "space-" + s.id, title: s.title, icon: s.icon, accent: s.accent,
               children: pages.length ? [{ id: "overview", title: "Overview", icon: "square.grid.2x2", target: { kind: "space", space: s.id } }, ...pages]
                                      : [{ id: "open", title: "Open " + s.title, target: { kind: "space", space: s.id } }] };
    });
    const loose = [...((catalog && catalog.sections) || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.title).localeCompare(String(b.title)))
      .filter((e) => !filed.has(e.id));
    if (loose.length) out.push({ id: "sections", title: "Sections", icon: "doc.on.doc",
      children: loose.map((e) => ({ id: e.id, title: e.title, icon: e.icon_image || e.icon, accent: e.accent, target: { kind: "section", section: e.id } })) });
    return out;
  }

  /** An item by its key: "tab:<id>/<id>…" or "side:<id>/<id>…" (NavDoc.item). */
  function navItem(nav, keyPath, fallbackSidebar) {
    const m = /^(tab|side):(.+)$/.exec(keyPath || "");
    if (!m) return null;
    let level = m[1] === "tab" ? (nav && nav.tabs) || [] : (nav && nav.sidebar) || fallbackSidebar || [];
    let found = null;
    for (const id of m[2].split("/")) {
      found = level.find((i) => i.id === id);
      if (!found) return null;
      level = found.children || [];
    }
    return found;
  }

  /** The route a nav item opens; null for a group (it opens its own list page). */
  function navRoute(item, keyPath) {
    const t = item && item.target;
    if (!t) return "#/nav/" + encodeURIComponent(keyPath);
    if (t.kind === "section") return "#/section/" + [t.section, t.node || "-", t.tab || "-"].map(encodeURIComponent).join("/").replace(/(\/-)+$/, "");
    if (t.kind === "space") return "#/space/" + encodeURIComponent(t.space);
    if (t.kind === "view") return VIEW_ROUTES[t.view] || "#/home";
    if (t.kind === "link") return t.url;
    return "#/home";
  }
  const VIEW_ROUTES = { home: "#/home", system: "#/system", satellites: "#/system/satellites", automations: "#/system/automations",
                        manage: "#/system/manage", settings: "#/system/settings", agent: "#/agent", tracker: "#/tracker",
                        alerts: "#/alerts", browse: "#/browse", spaces: "#/spaces" };

  return { key, isSystemSpace, platformOf, collectBlocks, buildDirectory, spaceData, keepPlatform, defaultSidebar, navItem, navRoute,
           areaTitle, AREA_ORDER, AREA_ICONS, PLATFORM_ORDER, PLATFORM_TITLES, PLATFORM_AREAS, VIEW_ROUTES };
})();

if (typeof module !== "undefined") module.exports = Model;
