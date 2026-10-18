/**
 * Chrome DevTools Protocol helpers (chrome.debugger). Used for input the page sees as trusted
 * (isTrusted: true), the accessibility tree (which pierces shadow DOM), the real file-chooser
 * path for uploads, screenshots without focusing the window, and evaluation on pages whose
 * Content-Security-Policy blocks scripts.
 *
 * While the debugger is attached Chrome shows a "started debugging this browser" bar on that
 * tab. withDebugger() detaches as soon as the work is done.
 */

/* exported withDebugger, cdp, trustedClick, keyPress, insertText, axFindNode, axNodeCenter,
   axNodeText, axArgs, captureTabPng, cdpEvaluate, keyInfo, MODIFIER_BITS */

async function attachDebugger(target) {
  try {
    await chrome.debugger.attach(target, "1.3");
    return true;
  } catch (e) {
    if (/already attached/i.test(errorText(e))) return false;
    throw e;
  }
}

/** Attach to a tab, run fn(target), detach (only if we attached it). */
async function withDebugger(tabId, fn, { keepAttached = false } = {}) {
  const target = { tabId };
  const attachedHere = await attachDebugger(target);
  try {
    return await fn(target);
  } finally {
    if (attachedHere && !keepAttached) {
      try {
        await chrome.debugger.detach(target);
      } catch (_) {}
    }
  }
}

function cdp(target, method, params) {
  return chrome.debugger.sendCommand(target, method, params || {});
}

async function trustedClick(target, x, y, { button = "left", clickCount = 1 } = {}) {
  await cdp(target, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  for (const type of ["mousePressed", "mouseReleased"]) {
    await cdp(target, "Input.dispatchMouseEvent", {
      type,
      x,
      y,
      button,
      buttons: type === "mousePressed" ? 1 : 0,
      clickCount,
      pointerType: "mouse",
    });
  }
}

const MODIFIER_BITS = { alt: 1, ctrl: 2, control: 2, meta: 4, cmd: 4, command: 4, shift: 8 };

const NAMED_KEYS = {
  enter: { key: "Enter", code: "Enter", vk: 13, text: "\r" },
  return: { key: "Enter", code: "Enter", vk: 13, text: "\r" },
  tab: { key: "Tab", code: "Tab", vk: 9 },
  escape: { key: "Escape", code: "Escape", vk: 27 },
  esc: { key: "Escape", code: "Escape", vk: 27 },
  backspace: { key: "Backspace", code: "Backspace", vk: 8 },
  delete: { key: "Delete", code: "Delete", vk: 46 },
  space: { key: " ", code: "Space", vk: 32, text: " " },
  arrowup: { key: "ArrowUp", code: "ArrowUp", vk: 38 },
  arrowdown: { key: "ArrowDown", code: "ArrowDown", vk: 40 },
  arrowleft: { key: "ArrowLeft", code: "ArrowLeft", vk: 37 },
  arrowright: { key: "ArrowRight", code: "ArrowRight", vk: 39 },
  up: { key: "ArrowUp", code: "ArrowUp", vk: 38 },
  down: { key: "ArrowDown", code: "ArrowDown", vk: 40 },
  left: { key: "ArrowLeft", code: "ArrowLeft", vk: 37 },
  right: { key: "ArrowRight", code: "ArrowRight", vk: 39 },
  home: { key: "Home", code: "Home", vk: 36 },
  end: { key: "End", code: "End", vk: 35 },
  pageup: { key: "PageUp", code: "PageUp", vk: 33 },
  pagedown: { key: "PageDown", code: "PageDown", vk: 34 },
};

/** "Enter", "a", "Meta+Enter", "ctrl+shift+k" -> {key, code, vk, text, modifiers}. */
function keyInfo(spec, extraModifiers = 0) {
  const parts = String(spec || "Enter").split("+").map((p) => p.trim()).filter(Boolean);
  let modifiers = extraModifiers;
  let main = parts.pop() || "Enter";
  for (const p of parts) {
    const bit = MODIFIER_BITS[p.toLowerCase()];
    if (!bit) throw new Error("unknown modifier: " + p);
    modifiers |= bit;
  }
  const named = NAMED_KEYS[main.toLowerCase()];
  if (named) return { ...named, modifiers };
  if (/^f([1-9]|1[0-2])$/i.test(main)) {
    const n = Number(main.slice(1));
    return { key: "F" + n, code: "F" + n, vk: 111 + n, modifiers };
  }
  if (main.length === 1) {
    const upper = main.toUpperCase();
    const isLetter = /[A-Z]/.test(upper);
    const isDigit = /[0-9]/.test(main);
    return {
      key: main,
      code: isLetter ? "Key" + upper : isDigit ? "Digit" + main : "",
      vk: isLetter || isDigit ? upper.charCodeAt(0) : 0,
      text: modifiers & (2 | 4) ? undefined : main,
      modifiers,
    };
  }
  return { key: main, code: main, vk: 0, modifiers };
}

async function keyPress(target, spec, extraModifiers = 0) {
  const k = keyInfo(spec, extraModifiers);
  const base = { key: k.key, code: k.code, windowsVirtualKeyCode: k.vk, nativeVirtualKeyCode: k.vk, modifiers: k.modifiers };
  await cdp(target, "Input.dispatchKeyEvent", { type: k.text ? "keyDown" : "rawKeyDown", ...base });
  if (k.text) await cdp(target, "Input.dispatchKeyEvent", { type: "char", text: k.text, unmodifiedText: k.text, modifiers: k.modifiers });
  await cdp(target, "Input.dispatchKeyEvent", { type: "keyUp", ...base });
  return k;
}

/** Type text with trusted input. human=true types one character at a time with pauses. */
async function insertText(target, text, { human = false, minDelayMs = 30, maxDelayMs = 110 } = {}) {
  const lines = String(text).split("\n");
  let typed = 0;
  for (let i = 0; i < lines.length; i++) {
    if (i > 0) {
      await keyPress(target, "Enter");
      await sleep(human ? rand(150, 300) : 20);
    }
    const line = lines[i];
    if (!line) continue;
    if (!human) {
      await cdp(target, "Input.insertText", { text: line });
      typed += line.length;
      continue;
    }
    for (const ch of line) {
      await cdp(target, "Input.insertText", { text: ch });
      typed += 1;
      if (ch === " ") await sleep(rand(minDelayMs * 3, maxDelayMs * 2));
      else if (/[.,!?;:]/.test(ch)) await sleep(rand(minDelayMs + 40, maxDelayMs + 80));
      else await sleep(rand(minDelayMs, maxDelayMs));
      if (typed % rand(12, 22) === 0) await sleep(rand(200, 450));
    }
  }
  return typed;
}

/** The nth accessibility node whose name equals one of `names` (case-insensitive). */
async function axFindNode(target, names, roles, nth, { contains = false } = {}) {
  await cdp(target, "Accessibility.enable").catch(() => {});
  await cdp(target, "DOM.enable");
  const tree = await cdp(target, "Accessibility.getFullAXTree");
  const nodes = (tree && tree.nodes) || [];
  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const wants = (names || []).map(norm).filter(Boolean);
  const hits = [];
  for (const n of nodes) {
    if (n.ignored || !n.backendDOMNodeId) continue;
    const role = (n.role && n.role.value) || "";
    const name = norm(n.name && n.name.value);
    if (!name) continue;
    const match = contains ? wants.some((w) => name.includes(w)) : wants.includes(name);
    if (!match) continue;
    if (roles && roles.length && !roles.includes(role)) continue;
    hits.push(n);
  }
  const node = hits[Math.max(0, Number(nth || 0))] || null;
  const disabled = !!(node && (node.properties || []).some((p) => p.name === "disabled" && p.value && p.value.value));
  return {
    node,
    disabled,
    matches: hits.length,
    sample: node
      ? undefined
      : nodes
          .filter((n) => !n.ignored && n.name && n.name.value)
          .slice(0, 40)
          .map((n) => ((n.role && n.role.value) || "") + ":" + String(n.name.value).slice(0, 60)),
  };
}

/** Viewport center of a DOM node after scrolling it into view. */
async function axNodeCenter(target, backendNodeId) {
  await cdp(target, "DOM.scrollIntoViewIfNeeded", { backendNodeId }).catch(() => {});
  let q = null;
  try {
    const { quads } = await cdp(target, "DOM.getContentQuads", { backendNodeId });
    q = quads && quads[0];
  } catch (_) {}
  if (!q) {
    const { model } = await cdp(target, "DOM.getBoxModel", { backendNodeId });
    q = model.border;
  }
  const xs = [q[0], q[2], q[4], q[6]];
  const ys = [q[1], q[3], q[5], q[7]];
  return {
    x: xs.reduce((a, b) => a + b) / 4,
    y: ys.reduce((a, b) => a + b) / 4,
    w: Math.max(...xs) - Math.min(...xs),
    h: Math.max(...ys) - Math.min(...ys),
  };
}

/** The value of a text field, or the text of a contenteditable, behind a node. */
async function axNodeText(target, backendNodeId) {
  const resolved = await cdp(target, "DOM.resolveNode", { backendNodeId });
  const objectId = resolved && resolved.object && resolved.object.objectId;
  if (!objectId) return "";
  const r = await cdp(target, "Runtime.callFunctionOn", {
    objectId,
    functionDeclaration: "function() { return typeof this.value === 'string' ? this.value : (this.innerText || this.textContent || ''); }",
    returnByValue: true,
  });
  return String((r && r.result && r.result.value) || "");
}

function axArgs(args) {
  const names = Array.isArray(args.names) ? args.names : [args.name || args.label || args.text || ""].filter(Boolean);
  if (!names.length) throw new Error("name required");
  const roles = Array.isArray(args.roles) ? args.roles : args.role ? [args.role] : [];
  return { names, roles, nth: Number(args.nth || 0) };
}

/** PNG data URL of the tab without bringing its window forward. */
async function captureTabPng(tabId, { fullPage = false } = {}) {
  return withDebugger(tabId, async (target) => {
    const params = { format: "png" };
    if (fullPage) {
      const metrics = await cdp(target, "Page.getLayoutMetrics");
      const size = metrics.cssContentSize || metrics.contentSize;
      if (size) {
        params.captureBeyondViewport = true;
        params.clip = { x: 0, y: 0, width: Math.ceil(size.width), height: Math.min(Math.ceil(size.height), 16000), scale: 1 };
      }
    }
    const shot = await cdp(target, "Page.captureScreenshot", params);
    if (!shot || !shot.data) throw new Error("empty screenshot");
    return "data:image/png;base64," + shot.data;
  });
}

/** Evaluate an expression through CDP. Works on pages whose CSP blocks eval. */
async function cdpEvaluate(tabId, code) {
  return withDebugger(tabId, async (target) => {
    // An expression returns its value; statements run as a function body (use `return`).
    const asExpression = `(async () => (${code}\n))()`;
    let expression = asExpression;
    try {
      const check = await cdp(target, "Runtime.compileScript", { expression: asExpression, sourceURL: "", persistScript: false });
      if (check && check.exceptionDetails) expression = `(async () => { ${code}\n })()`;
    } catch (_) {}
    const r = await cdp(target, "Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
    if (r && r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error((d.exception && d.exception.description) || d.text || "evaluation failed");
    }
    const v = r && r.result;
    return v && Object.prototype.hasOwnProperty.call(v, "value") ? v.value : v && v.description != null ? v.description : null;
  });
}
