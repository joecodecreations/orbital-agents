/**
 * Isolated-world content script, registered only while network capture is on. Relays fetch/XHR
 * bodies recorded by inject_network_hook.js (page world) and a "page ready" note to the
 * background worker.
 */
(function () {
  if (window.__orbitalBridgeInstalled) return;
  window.__orbitalBridgeInstalled = true;

  function send(message) {
    try {
      chrome.runtime.sendMessage(message).catch(() => {});
    } catch (_) {}
  }

  window.addEventListener("__orbital_net__", (ev) => {
    let payload = null;
    try {
      payload = typeof ev.detail === "string" ? JSON.parse(ev.detail) : null;
    } catch (_) {
      payload = null;
    }
    if (payload && typeof payload === "object") send({ type: "page.network", payload });
  });

  function ready() {
    send({ type: "page.ready", url: location.href, title: document.title });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready, { once: true });
  } else {
    ready();
  }
})();
