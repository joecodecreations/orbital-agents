/**
 * Page-world script, registered only while network capture is on. Wraps fetch and
 * XMLHttpRequest to record request and response bodies (text, JSON, JavaScript; up to
 * 200 KB each) for the endpoint catalog, and hands them to content_bridge.js as a
 * JSON string in a window event. The page's own requests are unchanged.
 */
(function () {
  if (window.__orbitalNetHookInstalled) return;
  window.__orbitalNetHookInstalled = true;

  const MAX_BODY = 200000;

  function emit(payload) {
    try {
      window.dispatchEvent(new CustomEvent("__orbital_net__", { detail: JSON.stringify(payload) }));
    } catch (_) {}
  }

  function clip(value) {
    if (value == null) return null;
    let text;
    if (typeof value === "string") text = value;
    else if (value instanceof URLSearchParams) text = value.toString();
    else if (typeof FormData !== "undefined" && value instanceof FormData) return "[form data]";
    else if (typeof Blob !== "undefined" && value instanceof Blob) return "[blob " + value.size + " bytes]";
    else if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return "[binary " + value.byteLength + " bytes]";
    else text = String(value);
    return text.length > MAX_BODY ? text.slice(0, MAX_BODY) + "...[truncated]" : text;
  }

  function headersToObject(h) {
    const out = {};
    try {
      if (!h) return out;
      if (typeof h.forEach === "function") h.forEach((v, k) => { out[k] = v; });
      else if (Array.isArray(h)) for (const [k, v] of h) out[k] = v;
      else if (typeof h === "object") Object.assign(out, h);
    } catch (_) {}
    return out;
  }

  function wantsBody(contentType) {
    const ct = (contentType || "").toLowerCase();
    return ct.includes("json") || ct.includes("text") || ct.includes("javascript") || ct.includes("xml");
  }

  const origFetch = window.fetch;
  if (typeof origFetch === "function") {
    window.fetch = async function (input, init) {
      const started = Date.now();
      let url = "";
      let method = "GET";
      let requestHeaders = {};
      let requestBody = null;
      try {
        url = typeof input === "string" ? input : input && input.url ? input.url : String(input);
        method = (init && init.method) || (input && input.method) || "GET";
        requestHeaders = headersToObject((init && init.headers) || (input && input.headers));
        requestBody = init && init.body != null ? clip(init.body) : null;
      } catch (_) {}
      try {
        const res = await origFetch.apply(this, arguments);
        let responseBody = null;
        try {
          if (wantsBody(res.headers.get("content-type"))) responseBody = clip(await res.clone().text());
        } catch (_) {}
        emit({
          channel: "fetch", url, method, requestHeaders, requestBody, status: res.status,
          responseHeaders: headersToObject(res.headers), responseBody, durationMs: Date.now() - started, ok: res.ok,
        });
        return res;
      } catch (err) {
        emit({
          channel: "fetch", url, method, requestHeaders, requestBody,
          error: String(err && err.message ? err.message : err), durationMs: Date.now() - started,
        });
        throw err;
      }
    };
  }

  const OrigXHR = window.XMLHttpRequest;
  if (typeof OrigXHR === "function") {
    const open = OrigXHR.prototype.open;
    const send = OrigXHR.prototype.send;
    OrigXHR.prototype.open = function (method, url) {
      this.__orbital = { method: method || "GET", url: String(url || ""), started: 0 };
      return open.apply(this, arguments);
    };
    OrigXHR.prototype.send = function (body) {
      const info = this.__orbital || { method: "GET", url: "" };
      info.started = Date.now();
      info.requestBody = body != null ? clip(body) : null;
      this.addEventListener("loadend", () => {
        let responseBody = null;
        try {
          if (this.responseType === "" || this.responseType === "text") {
            if (wantsBody(this.getResponseHeader("content-type"))) responseBody = clip(this.responseText);
          } else if (this.responseType === "json") {
            responseBody = clip(JSON.stringify(this.response));
          }
        } catch (_) {}
        emit({
          channel: "xhr", url: info.url, method: info.method, requestBody: info.requestBody,
          status: this.status, responseBody, durationMs: Date.now() - info.started,
        });
      });
      return send.apply(this, arguments);
    };
  }
})();
