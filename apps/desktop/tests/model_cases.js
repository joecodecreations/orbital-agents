// Runs ui/model.js against a small generic hub payload; prints one JSON result for test_model.py.
const M = require(process.argv[2]);
const catalog = {
  sections: [
    { id: "games", title: "Games", order: 1, icon: "gamecontroller.fill" },
    { id: "site", title: "Website", order: 2, icon: "globe" },
    { id: "health", title: "Machine health", order: 3, icon: "cpu" },
    { id: "loose", title: "Reading list", order: 4, icon: "book" },
  ],
  spaces: [
    { id: "studio", title: "Studio", aliases: ["Studio LLC"], sections: ["games", "site"], order: 1, accent: "#A78BFA" },
    { id: "home-lab", title: "Home Lab", sections: [] },
    { id: "system", title: "System", sections: ["health"], order: 99 },
  ],
};
const series = (kind, product, space) => ({ type: "series", id: kind + product, kind, product, space, metrics: [{ label: "Count", points: [{ x: "2026-01-01", y: 1 }, { x: "2026-01-02", y: 3 }] }] });
const sections = {
  games: { id: "games", title: "Games", nodes: [
    { id: "puzzle", title: "Puzzle Cat", tabs: [{ id: "overview", blocks: [{ type: "product", id: "product", kind: "game" }] },
      { id: "money", blocks: [
        { type: "ledger", id: "ios", title: "Puzzle Cat · App Store", source: "App Store", days: [{ date: "2026-01-01", amount: 2, product: "Puzzle Cat" }] },
        { type: "ledger", id: "android", title: "Puzzle Cat · Google Play", source: "Google Play", days: [] },
      ] }, { id: "players", blocks: [series("players", "Puzzle Cat")] }] },
    { id: "blocky", title: "Blocky Run", blocks: [
      { type: "ledger", id: "robux", title: "Blocky Run · Robux", source: "Roblox", days: [{ date: "2026-01-01", amount: 5 }] },
      series("players", "Blocky Run"), { type: "videos", id: "videos", items: [] }] },
  ] },
  site: { id: "site", title: "Website", blocks: [series("site", "Studio site", "Studio LLC"), series("social", "Studio site")] },
  health: { id: "health", title: "Machine health", blocks: [series("health", "box", "Home Lab")] },
};
const dir = M.buildDirectory(catalog, sections);
const out = { visible: dir.visible.map((s) => s.id), system: dir.systemSections, alias: dir.resolve("Studio LLC") };
for (const p of ["all", "ios", "roblox", "android"]) {
  const d = M.spaceData(dir, "studio", p);
  out[p] = { areas: d.areas.map((a) => a.id), platforms: d.platforms, refs: d.refs.map((r) => r.block.product), ledgers: d.ledgers.map((l) => l.block.id), products: d.products.map((x) => x.name) };
}
out.homelab = M.spaceData(dir, "home-lab", "all").areas.map((a) => a.id);
out.keep = [M.keepPlatform("ios", ["ios", "android"]), M.keepPlatform("ios", ["web"]), M.keepPlatform("all", [])];
const side = M.defaultSidebar(dir, catalog);
out.sidebar = side.map((i) => [i.title, (i.children || []).map((c) => c.title)]);
const nav = { tabs: [{ id: "work", title: "Work", children: [{ id: "deep", title: "Deep", children: [{ id: "x", title: "X", target: { kind: "view", view: "tracker" } }] }] }], sidebar: null };
out.navItem = (M.navItem(nav, "tab:work/deep/x", side) || {}).title || null;
out.navItemSide = (M.navItem(nav, "side:space-studio/games", side) || {}).title || null;
out.routes = [
  M.navRoute({ target: { kind: "section", section: "games", node: "puzzle", tab: "money" } }),
  M.navRoute({ target: { kind: "section", section: "games" } }),
  M.navRoute({ target: { kind: "space", space: "studio" } }),
  M.navRoute({ target: { kind: "view", view: "automations" } }),
  M.navRoute({ id: "g", title: "G", children: [] }, "side:g"),
];
out.platformOf = [M.platformOf({ title: "X · App Store" }), M.platformOf({ title: "X", block: { source: "Google Play" } }), M.platformOf({ title: "Robux" }), M.platformOf({ title: "Stripe charges" }), M.platformOf({ title: "Cash" })];
console.log(JSON.stringify(out));
