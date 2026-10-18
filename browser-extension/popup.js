/* Popup: bridge status, capture and window switches, bridge port. */
const $ = (id) => document.getElementById(id);
let editingPort = false;

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function age(ms) {
  if (!ms) return "";
  const s = Math.round((Date.now() - ms) / 1000);
  return s < 2 ? "just now" : `${s}s ago`;
}

async function refresh() {
  let st;
  try {
    st = await send({ type: "get_status" });
  } catch (e) {
    $("conn").textContent = "extension error";
    return;
  }
  if (!st || !st.ok) return;
  const link = st.link || {};
  const dot = $("dot");
  if (!st.enabled) {
    dot.className = "dot wait";
    $("conn").textContent = "paused";
  } else if (link.connected) {
    dot.className = "dot on";
    $("conn").textContent = "connected";
  } else {
    dot.className = "dot off";
    $("conn").textContent = link.lastPoll ? "not running" : "checking";
  }
  const err = $("err");
  if (!link.connected && link.error && st.enabled) {
    const reason = /fetch|network/i.test(link.error) ? "" : ` (${link.error})`;
    err.textContent = `No bridge is answering on port ${st.port}${reason}; last try ${age(link.lastPoll)}.`;
    err.classList.remove("hidden");
  } else {
    err.classList.add("hidden");
  }
  $("addr").textContent = `127.0.0.1:${st.port}`;
  $("build").textContent = `v${st.version}`;
  $("cap").checked = !!st.capturing;
  $("iso").checked = st.isolateWindow !== false;
  $("queue").textContent = String(st.queuedEvents || 0);
  $("wins").textContent = String((st.sessions || []).length);
  $("focusAuto").textContent = st.holdFocus ? "Return focus" : "Use agent window";
  $("focusAuto").disabled = st.isolateWindow === false;
  if (!editingPort) $("port").value = st.port;
}

$("cap").addEventListener("change", async (e) => {
  await send({ type: "set_settings", settings: { capturing: e.target.checked } });
  refresh();
});

$("iso").addEventListener("change", async (e) => {
  await send({ type: "set_settings", settings: { isolateWindow: e.target.checked } });
  refresh();
});

$("port").addEventListener("focus", () => { editingPort = true; });
$("port").addEventListener("blur", () => { editingPort = false; });

$("portForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const port = Number($("port").value);
  const r = await send({ type: "set_settings", settings: { bridgePort: port } });
  if (r && r.ok === false) {
    $("err").textContent = r.error;
    $("err").classList.remove("hidden");
  }
  editingPort = false;
  $("port").blur();
  refresh();
});

$("focusAuto").addEventListener("click", async () => {
  const st = await send({ type: "get_status" });
  await send({ type: "automation_ctl", args: { op: st.holdFocus ? "release" : "focus" } });
  refresh();
});

$("panel").addEventListener("click", async () => {
  try {
    const win = await chrome.windows.getCurrent();
    await chrome.sidePanel.open({ windowId: win.id });
    window.close();
  } catch (_) {}
});

$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());

refresh();
setInterval(refresh, 1000);
