/**
 * Shopify Image Localizer Helper — Chrome extension background service worker.
 *
 * HTTP-polling bridge (no WebSocket):
 *   - SW loops fetching GET http://127.0.0.1:7778/poll to receive a command
 *   - executes the command (CDP via chrome.debugger, tab query, etc.)
 *   - POSTs result back to http://127.0.0.1:7778/result
 *
 * Protocol per HTTP cycle:
 *   GET  /poll?client=<uuid>         -> {"id": <n>, "method": "...", "params": {...}} | {"idle": true}
 *   POST /result {id,result}|{id,error}
 *   POST /hello {version}            -> optional initial handshake
 *
 * Chrome MV3 keeps SW alive via chrome.alarms and repeated fetches.
 */

const BRIDGE_BASE = "http://127.0.0.1:7778";
const POLL_INTERVAL_MS = 500;

function log(...args) { console.log("[ShopifyExt]", ...args); }

function cdpSend(debuggee, method, params = {}) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand(debuggee, method, params, (result) => {
      const err = chrome.runtime.lastError;
      if (err) return reject(new Error(err.message));
      resolve(result);
    });
  });
}

function attachDebugger(tabId) {
  return new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, "1.3", () => {
      const err = chrome.runtime.lastError;
      if (err && !String(err.message || "").toLowerCase().includes("already attached")) {
        return reject(new Error(err.message));
      }
      resolve();
    });
  });
}

function detachDebugger(tabId) {
  return new Promise((resolve) => {
    chrome.debugger.detach({ tabId }, () => resolve());
  });
}

const handlers = {
  async ping() { return { pong: true, time: Date.now() }; },

  async list_tabs({ url_contains = null } = {}) {
    const tabs = await new Promise((r) => chrome.tabs.query({}, r));
    let out = tabs.map((t) => ({ id: t.id, url: t.url, title: t.title, active: t.active, windowId: t.windowId }));
    if (url_contains) out = out.filter((t) => (t.url || "").includes(url_contains));
    return out;
  },

  async attach({ tabId }) {
    await attachDebugger(tabId);
    await cdpSend({ tabId }, "Page.enable", {});
    await cdpSend({ tabId }, "DOM.enable", {});
    await cdpSend({ tabId }, "Runtime.enable", {});
    return { attached: true };
  },

  async detach({ tabId }) {
    await detachDebugger(tabId);
    return { detached: true };
  },

  async get_frame_tree({ tabId }) {
    return await cdpSend({ tabId }, "Page.getFrameTree", {});
  },

  async evaluate({ tabId, expression, awaitPromise = true, returnByValue = true, contextId = undefined }) {
    const params = { expression, awaitPromise, returnByValue, userGesture: true };
    if (contextId) params.contextId = contextId;
    return await cdpSend({ tabId }, "Runtime.evaluate", params);
  },

  async query_selector_in_frame({ tabId, selector, frame_url_contains = null }) {
    const frameTree = await cdpSend({ tabId }, "Page.getFrameTree", {});
    const frames = [];
    function collect(n) {
      if (!n || !n.frame) return;
      const u = n.frame.url || "";
      if (!frame_url_contains || u.includes(frame_url_contains)) frames.push(n.frame);
      if (n.childFrames) for (const c of n.childFrames) collect(c);
    }
    collect(frameTree.frameTree);
    const results = [];
    for (const fr of frames) {
      try {
        const world = await cdpSend({ tabId }, "Page.createIsolatedWorld", {
          frameId: fr.id, worldName: "ShopifyLocalizerHelper",
        });
        const res = await cdpSend({ tabId }, "Runtime.evaluate", {
          contextId: world.executionContextId,
          expression: `(() => {
            const all = document.querySelectorAll(${JSON.stringify(selector)});
            return Array.from(all).map(el => ({ tag: el.tagName, type: el.getAttribute('type')||'', src: el.currentSrc || el.src || el.getAttribute('src') || '' }));
          })()`,
          returnByValue: true,
        });
        const list = (res && res.result && res.result.value) || [];
        if (list.length) results.push({ frameId: fr.id, frameUrl: fr.url, matches: list, contextId: world.executionContextId });
      } catch (e) {}
    }
    return { frames: results };
  },

  async set_file_input_in_frame({ tabId, selector, files, frame_url_contains = null }) {
    const frameTree = await cdpSend({ tabId }, "Page.getFrameTree", {});
    const frames = [];
    function collect(n) {
      if (!n || !n.frame) return;
      const u = n.frame.url || "";
      if (!frame_url_contains || u.includes(frame_url_contains)) frames.push(n.frame);
      if (n.childFrames) for (const c of n.childFrames) collect(c);
    }
    collect(frameTree.frameTree);
    for (const fr of frames) {
      try {
        const world = await cdpSend({ tabId }, "Page.createIsolatedWorld", {
          frameId: fr.id, worldName: "ShopifyLocalizerHelper",
        });
        const res = await cdpSend({ tabId }, "Runtime.evaluate", {
          contextId: world.executionContextId,
          expression: `document.querySelector(${JSON.stringify(selector)})`,
          returnByValue: false,
        });
        const obj = res && res.result;
        if (!obj || !obj.objectId) continue;
        const desc = await cdpSend({ tabId }, "DOM.describeNode", { objectId: obj.objectId });
        const backendNodeId = desc && desc.node && desc.node.backendNodeId;
        if (!backendNodeId) continue;
        await cdpSend({ tabId }, "DOM.setFileInputFiles", { backendNodeId, files });
        return { ok: true, frameId: fr.id, frameUrl: fr.url };
      } catch (e) {}
    }
    throw new Error(`no file input matched ${selector}`);
  },

  async click_in_frame({ tabId, selector, frame_url_contains = null }) {
    const frameTree = await cdpSend({ tabId }, "Page.getFrameTree", {});
    const frames = [];
    function collect(n) {
      if (!n || !n.frame) return;
      const u = n.frame.url || "";
      if (!frame_url_contains || u.includes(frame_url_contains)) frames.push(n.frame);
      if (n.childFrames) for (const c of n.childFrames) collect(c);
    }
    collect(frameTree.frameTree);
    for (const fr of frames) {
      const world = await cdpSend({ tabId }, "Page.createIsolatedWorld", {
        frameId: fr.id, worldName: "ShopifyLocalizerHelper",
      });
      const res = await cdpSend({ tabId }, "Runtime.evaluate", {
        contextId: world.executionContextId,
        expression: `(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; el.click(); return true; })()`,
        returnByValue: true,
      });
      if (res && res.result && res.result.value === true) return { ok: true, frameId: fr.id, frameUrl: fr.url };
    }
    throw new Error(`no element ${selector}`);
  },
};

// ---------- HTTP polling loop ----------
async function sendHello() {
  try {
    await fetch(`${BRIDGE_BASE}/hello`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version: chrome.runtime.getManifest().version, ts: Date.now() }),
    });
    log("hello sent");
  } catch (e) { log("hello err:", e.message); }
}

async function pollOnce() {
  try {
    const r = await fetch(`${BRIDGE_BASE}/poll`, { method: "GET" });
    if (!r.ok) return;
    const msg = await r.json();
    if (msg && msg.id && msg.method) {
      const handler = handlers[msg.method];
      let body;
      if (!handler) {
        body = { id: msg.id, error: { message: `unknown ${msg.method}` } };
      } else {
        try {
          const result = await handler(msg.params || {});
          body = { id: msg.id, result };
        } catch (e) {
          body = { id: msg.id, error: { message: String(e.message || e) } };
        }
      }
      await fetch(`${BRIDGE_BASE}/result`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    }
  } catch (e) {
    // bridge not up yet
  }
}

async function pollLoop() {
  while (true) {
    await pollOnce();
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
}

// ---------- keepalive + init ----------
chrome.alarms.create("keepalive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => { /* just waking SW */ });

sendHello();
pollLoop();
