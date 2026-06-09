const DEFAULT_BACKEND_HOST_PARTS = ["172", "16", "254", "106"];
const DEFAULT_BACKEND_BASE = `http://${DEFAULT_BACKEND_HOST_PARTS.join(".")}:8080`;

function normalizeBackendBase(value) {
  const text = String(value || "").trim().replace(/\/+$/, "");
  if (!text) return DEFAULT_BACKEND_BASE;
  if (!/^https?:\/\//i.test(text)) return `http://${text}`;
  return text;
}

async function getConfig() {
  const stored = await chrome.storage.sync.get({
    backendBase: DEFAULT_BACKEND_BASE,
  });
  return {
    backendBase: normalizeBackendBase(stored.backendBase),
  };
}

async function setConfig(nextConfig) {
  const backendBase = normalizeBackendBase(nextConfig.backendBase);
  await chrome.storage.sync.set({ backendBase });
  return { backendBase };
}

function buildUrl(base, path, params = {}) {
  const url = new URL(path, `${base}/`);
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    const text = String(value).trim();
    if (!text) return;
    url.searchParams.set(key, text);
  });
  return url;
}

async function fetchJson(url) {
  const response = await fetch(url, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  let payload = null;
  if (contentType.includes("application/json")) {
    try {
      payload = text ? JSON.parse(text) : {};
    } catch (error) {
      payload = null;
    }
  }
  if (!response.ok) {
    const detail = payload && (payload.detail || payload.error);
    throw new Error(detail || `后台请求失败 HTTP ${response.status}`);
  }
  if (!payload) {
    const finalUrl = response.url || "";
    if (finalUrl.includes("/login") || /<html|<form|请先登录/i.test(text)) {
      throw new Error("请先在 AutoVideoSrtLocal 后台登录后再刷新");
    }
    throw new Error("后台返回了非 JSON 内容");
  }
  return payload;
}

async function getInsights(clues) {
  const config = await getConfig();
  const params = {
    sku: (clues.skus || [])[0] || clues.sku || "",
    skus: (clues.skus || []).join(","),
    product_sku: (clues.productSkus || []).join(","),
    sku_code: (clues.skuCodes || []).join(","),
    shopify_product_id: (clues.shopifyProductIds || []).join(","),
    product_code: clues.productCode || "",
    product_name: clues.productName || "",
    page_url: clues.pageUrl || "",
  };
  const url = buildUrl(
    config.backendBase,
    "/dianxiaomi-procurement-insights/api/insights",
    params,
  );
  return fetchJson(url);
}

async function health() {
  const config = await getConfig();
  const url = buildUrl(config.backendBase, "/dianxiaomi-procurement-insights/api/health");
  return fetchJson(url);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      if (!message || !message.type) {
        throw new Error("unknown message");
      }
      if (message.type === "dpi:getConfig") {
        sendResponse({ ok: true, result: await getConfig() });
        return;
      }
      if (message.type === "dpi:setConfig") {
        sendResponse({ ok: true, result: await setConfig(message.config || {}) });
        return;
      }
      if (message.type === "dpi:health") {
        sendResponse({ ok: true, result: await health() });
        return;
      }
      if (message.type === "dpi:getInsights") {
        sendResponse({ ok: true, result: await getInsights(message.clues || {}) });
        return;
      }
      if (message.type === "dpi:openBackend") {
        const config = await getConfig();
        await chrome.tabs.create({ url: config.backendBase });
        sendResponse({ ok: true, result: { opened: true } });
        return;
      }
      throw new Error(`unknown message: ${message.type}`);
    } catch (error) {
      sendResponse({
        ok: false,
        error: String((error && error.message) || error),
      });
    }
  })();
  return true;
});
