/* Options page: bridge port, pause switch, window behavior, display, capture. */
const $ = (id) => document.getElementById(id);
const DEFAULTS = { bridgePort: 7703, enabled: true, isolateWindow: true, automationDisplay: "", capturing: false };

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function show(text, cls) {
  const el = $("saved");
  el.textContent = text;
  el.className = "small " + (cls || "muted");
}

async function load() {
  const st = await send({ type: "get_status" });
  if (!st || !st.ok) return;
  $("build").textContent = `v${st.version} (build ${st.build})`;
  $("port").value = st.port;
  $("enabled").checked = st.enabled !== false;
  $("iso").checked = st.isolateWindow !== false;
  $("cap").checked = !!st.capturing;
  const r = await send({ type: "get_displays" });
  const select = $("display");
  while (select.options.length > 1) select.remove(1);
  for (const d of (r && r.displays) || []) {
    if (!d.name) continue;
    const opt = document.createElement("option");
    opt.value = d.name;
    opt.textContent = `${d.name}${d.primary ? " (main)" : ""} ${d.width}x${d.height}`;
    select.appendChild(opt);
  }
  if (st.automationDisplay && ![...select.options].some((o) => o.value === st.automationDisplay)) {
    const opt = document.createElement("option");
    opt.value = st.automationDisplay;
    opt.textContent = `${st.automationDisplay} (not connected)`;
    select.appendChild(opt);
  }
  select.value = st.automationDisplay || "";
}

async function save(values) {
  const r = await send({ type: "set_settings", settings: values });
  if (r && r.ok === false) {
    show(r.error || "Could not save.", "bad");
    return false;
  }
  show("Saved.", "ok");
  setTimeout(test, 600);
  return true;
}

async function test() {
  $("health").textContent = "Checking...";
  const r = await send({ type: "test_bridge" });
  if (r && r.ok && r.health) {
    const h = r.health;
    $("health").innerHTML = "";
    const span = document.createElement("span");
    span.className = "ok";
    span.textContent = `Bridge ${h.version || ""} is running` + (h.machine ? ` on ${h.machine}` : "") + ".";
    $("health").appendChild(span);
  } else {
    $("health").textContent = `No bridge answered on port ${$("port").value}. Start it with "orbital browser serve".`;
  }
}

$("save").addEventListener("click", () =>
  save({
    bridgePort: Number($("port").value),
    enabled: $("enabled").checked,
    isolateWindow: $("iso").checked,
    automationDisplay: $("display").value,
    capturing: $("cap").checked,
  })
);

$("reset").addEventListener("click", async () => {
  if (await save(DEFAULTS)) {
    $("display").value = "";
    await load();
  }
});

$("test").addEventListener("click", test);

load().then(test);
