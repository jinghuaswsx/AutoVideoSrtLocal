const DPI_ROOT_ID = "dpi-procurement-root";
const MAX_TEXT_SCAN = 7000;

let lastPointerTarget = null;
let panelCollapsed = false;
let currentState = {
  status: "idle",
  clues: null,
  payload: null,
  error: "",
};

document.addEventListener("mousemove", (event) => {
  if (event.target instanceof Element) {
    lastPointerTarget = event.target;
  }
}, { passive: true });

function sendMessage(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        resolve({ ok: false, error: lastError.message });
        return;
      }
      resolve(response || { ok: false, error: "empty response" });
    });
  });
}

function compactText(text, limit = 240) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function getScanRoot() {
  const target = lastPointerTarget || document.activeElement;
  if (target instanceof Element) {
    const row = target.closest(
      "tr,[role='row'],.el-table__row,.ant-table-row,.layui-table-click,.vxe-body--row,.dxm-table-row",
    );
    if (row && compactText(row.innerText, 80)) return row;
    const card = target.closest("[data-row-key],[data-id],.card,.item,.product,.goods,.sku");
    if (card && compactText(card.innerText, 80)) return card;
  }
  return document.body;
}

function extractLabelValues(text, labels) {
  const values = [];
  const escaped = labels.map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const pattern = new RegExp(`(?:${escaped})\\s*[:：]?\\s*([A-Za-z0-9][A-Za-z0-9._\\-/]{1,80})`, "gi");
  let match = pattern.exec(text);
  while (match) {
    values.push(match[1]);
    match = pattern.exec(text);
  }
  return values;
}

function uniqueLimited(values, limit = 16) {
  const seen = new Set();
  const out = [];
  values.forEach((value) => {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    out.push(text);
  });
  return out.slice(0, limit);
}

function looksLikeSku(token) {
  if (!token || token.length < 3 || token.length > 80) return false;
  if (/^\d{1,4}$/.test(token)) return false;
  if (/^\d{4}-\d{1,2}-\d{1,2}$/.test(token)) return false;
  if (/^(http|https|www|com|cn|json|html)$/i.test(token)) return false;
  return /[A-Za-z]/.test(token) && /[0-9_-]/.test(token);
}

function extractProductCode(text) {
  const urlMatch = String(location.href || "").match(/\/products\/([a-z0-9][a-z0-9-]{5,120})/i);
  if (urlMatch) return urlMatch[1];
  const matches = text.match(/\b[a-z0-9]+(?:-[a-z0-9]+){2,}\b/gi) || [];
  const candidate = matches.find((item) => /-rjc$/i.test(item)) || matches[0];
  return candidate || "";
}

function extractProductName(text) {
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => compactText(line, 120))
    .filter(Boolean);
  const ignored = /(sku|SKU|编码|库存|采购|建议|数量|价格|重量|属性|订单|销量|复制)/;
  const candidate = lines.find((line) => /[\u4e00-\u9fff]/.test(line) && !ignored.test(line) && line.length >= 4);
  return candidate || "";
}

function collectClues() {
  const root = getScanRoot();
  const rootText = compactText(root ? root.innerText : "", MAX_TEXT_SCAN);
  const bodyText = rootText || compactText(document.body ? document.body.innerText : "", MAX_TEXT_SCAN);
  const skuLabels = ["货品SKU", "商品SKU", "SKU", "sku", "平台SKU", "平台sku"];
  const productSkuLabels = ["商品编码", "商品SKU", "商家编码", "商品货号", "货号"];
  const skuCodeLabels = ["SKU编码", "SKU 编码", "商品编码", "编码"];

  const labelledSkus = extractLabelValues(bodyText, skuLabels);
  const labelledProductSkus = extractLabelValues(bodyText, productSkuLabels);
  const labelledSkuCodes = extractLabelValues(bodyText, skuCodeLabels);
  const tokens = bodyText.match(/\b[A-Za-z0-9][A-Za-z0-9._-]{2,80}\b/g) || [];
  const skuCandidates = tokens.filter(looksLikeSku);
  const shopifyProductIds = uniqueLimited(tokens.filter((token) => /^\d{8,}$/.test(token)), 6);

  return {
    skus: uniqueLimited([...labelledSkus, ...skuCandidates], 16),
    productSkus: uniqueLimited(labelledProductSkus, 10),
    skuCodes: uniqueLimited(labelledSkuCodes, 10),
    shopifyProductIds,
    productCode: extractProductCode(bodyText),
    productName: extractProductName(bodyText),
    pageUrl: location.href,
    sourceTextPreview: bodyText.slice(0, 500),
  };
}

function fmtNumber(value, digits = 0) {
  const number = Number(value || 0);
  return number.toLocaleString("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function fmtRoas(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number.toFixed(2);
}

function statusClass(status) {
  if (status === "active") return "is-active";
  if (status === "stopped") return "is-stopped";
  return "is-never";
}

function renderMarkets(markets) {
  const list = Array.isArray(markets) ? markets : [];
  const visible = list
    .filter((market) => market.delivery_status !== "never" || Number((market.orders || {}).last_7d || 0) > 0)
    .slice(0, 8);
  if (!visible.length) return '<div class="dpi-empty-line">暂无市场明细</div>';
  return visible.map((market) => {
    const orders = market.orders || {};
    return `
      <div class="dpi-market-row">
        <span class="dpi-market-name">${escapeHtml(market.label || market.lang || "--")}</span>
        <span class="dpi-market-status ${statusClass(market.delivery_status)}">${escapeHtml(market.delivery_label || "--")}</span>
        <span class="dpi-market-meta">7日 ${fmtNumber(orders.last_7d)} 单 · ROAS ${fmtRoas(market.last_7d_ad_roas)}</span>
      </div>
    `;
  }).join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderContent() {
  if (panelCollapsed) return "";
  if (currentState.status === "loading") {
    return `
      <div class="dpi-body">
        <div class="dpi-loading">读取中</div>
      </div>
    `;
  }
  if (currentState.status === "error") {
    return `
      <div class="dpi-body">
        <div class="dpi-error">${escapeHtml(currentState.error || "请求失败")}</div>
      </div>
    `;
  }
  const payload = currentState.payload;
  if (!payload) {
    return `
      <div class="dpi-body">
        <div class="dpi-empty-line">等待刷新</div>
      </div>
    `;
  }
  const summary = payload.summary || {};
  const orders = summary.orders || {};
  const product = payload.product || {};
  const matched = Boolean(payload.matched);
  return `
    <div class="dpi-body">
      <div class="dpi-product-line">
        <span>${matched ? escapeHtml(product.name || product.product_code || "已匹配产品") : "未匹配产品"}</span>
      </div>
      <div class="dpi-status-line">
        <span class="dpi-status ${statusClass(summary.delivery_status)}">${escapeHtml(summary.delivery_label || "--")}</span>
        <span class="dpi-muted">消耗 $${fmtNumber(summary.ad_spend_usd, 2)}</span>
      </div>
      <div class="dpi-kpis">
        <div><span>今日</span><strong>${fmtNumber(orders.today)}</strong></div>
        <div><span>昨日</span><strong>${fmtNumber(orders.yesterday)}</strong></div>
        <div><span>7日</span><strong>${fmtNumber(orders.last_7d)}</strong></div>
        <div><span>真实ROAS</span><strong>${fmtRoas(summary.true_roas)}</strong></div>
      </div>
      <div class="dpi-markets">
        ${renderMarkets(payload.markets)}
      </div>
      <div class="dpi-foot">
        <span>${escapeHtml(product.match_method || "")}</span>
        <span>${escapeHtml((payload.data_quality || {}).status || "")}</span>
      </div>
    </div>
  `;
}

function renderPanel() {
  const root = document.getElementById(DPI_ROOT_ID);
  if (!root) return;
  root.innerHTML = `
    <div class="dpi-panel ${panelCollapsed ? "is-collapsed" : ""}">
      <div class="dpi-head">
        <button type="button" class="dpi-title" data-dpi-action="toggle" title="折叠或展开">采购洞察</button>
        <div class="dpi-actions">
          <button type="button" data-dpi-action="refresh" title="刷新">↻</button>
          <button type="button" data-dpi-action="backend" title="打开后台">↗</button>
        </div>
      </div>
      ${renderContent()}
    </div>
  `;
}

async function refreshInsights() {
  currentState = { status: "loading", clues: null, payload: null, error: "" };
  renderPanel();
  const clues = collectClues();
  const response = await sendMessage({ type: "dpi:getInsights", clues });
  if (!response || !response.ok) {
    currentState = {
      status: "error",
      clues,
      payload: null,
      error: (response && response.error) || "请求失败",
    };
    renderPanel();
    return;
  }
  currentState = {
    status: "done",
    clues,
    payload: response.result,
    error: "",
  };
  renderPanel();
}

function installPanel() {
  if (document.getElementById(DPI_ROOT_ID)) return;
  const root = document.createElement("div");
  root.id = DPI_ROOT_ID;
  document.documentElement.appendChild(root);
  root.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-dpi-action]") : null;
    if (!button) return;
    const action = button.getAttribute("data-dpi-action");
    if (action === "toggle") {
      panelCollapsed = !panelCollapsed;
      renderPanel();
    } else if (action === "refresh") {
      refreshInsights();
    } else if (action === "backend") {
      sendMessage({ type: "dpi:openBackend" });
    }
  });
  renderPanel();
  window.setTimeout(refreshInsights, 1200);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", installPanel, { once: true });
} else {
  installPanel();
}
