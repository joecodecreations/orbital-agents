/**
 * Network capture. Off until you turn it on (popup, options, or `orbital browser capture on`).
 *
 * While on:
 *   - chrome.webRequest records every request's URL, method, request headers, status and errors;
 *   - two small scripts in each page (inject_network_hook.js and content_bridge.js, registered
 *     only while capture is on) record fetch/XHR request and response bodies (text and JSON, up
 *     to 200 KB each);
 *   - events are sent in batches to the local bridge, which keeps a capped buffer, builds an
 *     endpoint catalog (method + host + path) and redacts credentials (Authorization, Cookie, ...).
 *
 * Nothing is captured for requests to the bridge itself.
 */

/* exported enqueueEvent, flushEvents, syncNetworkHook, syncCaptureListeners */

const HOOK_ID = "orbital-net-hook";
const RELAY_ID = "orbital-net-relay";
const MAX_QUEUE = 5000;
let eventQueue = [];
let listenersOn = false;

function enqueueEvent(evt) {
  if (!state.capturing) return;
  evt.ts = evt.ts || Date.now();
  eventQueue.push(evt);
  if (eventQueue.length > MAX_QUEUE) eventQueue.splice(0, eventQueue.length - MAX_QUEUE);
}

function queuedEvents() {
  return eventQueue.length;
}

async function flushEvents() {
  if (!state.enabled || !eventQueue.length) return;
  const batch = eventQueue.splice(0, 200);
  try {
    const res = await fetch(`${bridgeUrl()}/ext/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: batch, source: "extension", instance: state.instanceId }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    eventQueue = batch.concat(eventQueue).slice(-MAX_QUEUE);
    state.link.error = errorText(e);
  }
}

function skipUrl(url) {
  return !url || url.startsWith(bridgeUrl()) || url.startsWith("chrome-extension://");
}

function onBeforeRequest(details) {
  if (skipUrl(details.url)) return;
  let requestBody = null;
  if (details.requestBody) {
    if (details.requestBody.formData) requestBody = { formData: details.requestBody.formData };
    else if (details.requestBody.raw) requestBody = { rawParts: details.requestBody.raw.length };
  }
  enqueueEvent({
    type: "network.request",
    requestId: details.requestId,
    tabId: details.tabId,
    method: details.method,
    url: details.url,
    resourceType: details.type,
    frameId: details.frameId,
    requestBody,
  });
}

function onSendHeaders(details) {
  if (skipUrl(details.url)) return;
  const headers = {};
  for (const h of details.requestHeaders || []) headers[h.name] = h.value;
  enqueueEvent({
    type: "network.request_headers",
    requestId: details.requestId,
    tabId: details.tabId,
    url: details.url,
    method: details.method,
    headers,
  });
}

function onCompleted(details) {
  if (skipUrl(details.url)) return;
  enqueueEvent({
    type: "network.completed",
    requestId: details.requestId,
    tabId: details.tabId,
    url: details.url,
    method: details.method,
    statusCode: details.statusCode,
    fromCache: details.fromCache,
    ip: details.ip,
    resourceType: details.type,
  });
}

function onErrorOccurred(details) {
  if (skipUrl(details.url)) return;
  enqueueEvent({
    type: "network.error",
    requestId: details.requestId,
    tabId: details.tabId,
    url: details.url,
    method: details.method,
    error: details.error,
  });
}

/** webRequest listeners exist only while capture is on, so normal browsing pays nothing. */
function syncCaptureListeners() {
  const filter = { urls: ["<all_urls>"] };
  if (state.capturing && !listenersOn) {
    chrome.webRequest.onBeforeRequest.addListener(onBeforeRequest, filter, ["requestBody"]);
    chrome.webRequest.onSendHeaders.addListener(onSendHeaders, filter, ["requestHeaders", "extraHeaders"]);
    chrome.webRequest.onCompleted.addListener(onCompleted, filter);
    chrome.webRequest.onErrorOccurred.addListener(onErrorOccurred, filter);
    listenersOn = true;
  } else if (!state.capturing && listenersOn) {
    chrome.webRequest.onBeforeRequest.removeListener(onBeforeRequest);
    chrome.webRequest.onSendHeaders.removeListener(onSendHeaders);
    chrome.webRequest.onCompleted.removeListener(onCompleted);
    chrome.webRequest.onErrorOccurred.removeListener(onErrorOccurred);
    listenersOn = false;
  }
}

/**
 * The page scripts (body hook + relay) are registered only while capture is on, so with capture
 * off the extension injects nothing into pages. They apply to pages loaded after turning it on.
 */
async function syncNetworkHook() {
  syncCaptureListeners();
  try {
    const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [HOOK_ID, RELAY_ID] });
    if (state.capturing && existing.length < 2) {
      if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: existing.map((s) => s.id) });
      await chrome.scripting.registerContentScripts([
        {
          id: RELAY_ID,
          matches: ["<all_urls>"],
          js: ["content_bridge.js"],
          runAt: "document_start",
          allFrames: false,
          persistAcrossSessions: true,
        },
        {
          id: HOOK_ID,
          matches: ["<all_urls>"],
          js: ["inject_network_hook.js"],
          runAt: "document_start",
          world: "MAIN",
          allFrames: false,
          persistAcrossSessions: true,
        },
      ]);
    } else if (!state.capturing && existing.length) {
      await chrome.scripting.unregisterContentScripts({ ids: existing.map((s) => s.id) });
    }
    state.link.hookError = null;
  } catch (e) {
    state.link.hookError = errorText(e);
  }
}
