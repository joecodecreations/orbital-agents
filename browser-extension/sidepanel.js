/* Side panel: connection status and the last commands agents ran (action, result, time). */
const $ = (id) => document.getElementById(id);

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function when(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return new Date(ts).toLocaleTimeString();
}

function render(activity) {
  const list = $("log");
  list.textContent = "";
  if (!activity.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No commands yet.";
    list.appendChild(li);
    return;
  }
  for (const a of activity) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot " + (a.ok ? "on" : "off");
    const name = document.createElement("span");
    name.className = "mono";
    name.textContent = a.action;
    const meta = document.createElement("span");
    meta.className = "small muted";
    meta.textContent = `${a.ms} ms, ${when(a.at)}`;
    li.append(dot, name, meta);
    if (!a.ok && a.error) {
      const err = document.createElement("span");
      err.className = "err";
      err.textContent = a.error;
      li.appendChild(err);
    }
    list.appendChild(li);
  }
}

async function refresh() {
  try {
    const st = await send({ type: "get_status" });
    if (st && st.ok) {
      const link = st.link || {};
      $("dot").className = "dot " + (!st.enabled ? "wait" : link.connected ? "on" : "off");
      $("conn").textContent = !st.enabled ? "paused" : link.connected ? "connected" : "not running";
      $("addr").textContent = `127.0.0.1:${st.port}`;
      $("cap").textContent = st.capturing ? "on" : "off";
      $("wins").textContent = String((st.sessions || []).length);
      $("build").textContent = `v${st.version}`;
    }
    const r = await send({ type: "get_activity" });
    if (r && r.ok) render(r.activity || []);
  } catch (_) {}
}

refresh();
setInterval(refresh, 1000);
