const DPI_ROOT_ID = "dpi-procurement-root";
const MAX_TEXT_SCAN = 7000;
const PORTAL_BASE = "http://172.16.254.106";

let lastPointerTarget = null;
let panelCollapsed = false;
let placementFrame = 0;
let lastModalSignature = "";
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

function isVisibleElement(element) {
  if (!(element instanceof Element)) return false;
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);
  return (
    rect.width > 0
    && rect.height > 0
    && style.display !== "none"
    && style.visibility !== "hidden"
    && Number(style.opacity || 1) > 0
  );
}

function findPurchaseModal() {
  const modalSelectors = [
    "[role='dialog']",
    "[aria-modal='true']",
    ".modal",
    ".modal-dialog",
    ".modal-content",
    ".layui-layer",
    ".el-dialog",
    ".ant-modal",
    ".ui-dialog",
  ];
  const directCandidates = modalSelectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
  const broadCandidates = uniqueLimitedElements([...directCandidates, ...Array.from(document.querySelectorAll("div"))]);
  return broadCandidates
    .filter((element) => {
      if (!isVisibleElement(element)) return false;
      const rect = element.getBoundingClientRect();
      if (rect.width < 520 || rect.height < 260) return false;
      if (rect.width > window.innerWidth - 120 || rect.height > window.innerHeight - 20) return false;
      const text = compactText(element.innerText, 1200);
      return /生成采购单/.test(text) && /(采购计划|添加商品|采购单价|采购数量|云仓商品信息)/.test(text);
    })
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (ar.width * ar.height) - (br.width * br.height);
    })[0] || null;
}

function uniqueLimitedElements(elements) {
  const seen = new Set();
  const out = [];
  elements.forEach((element) => {
    if (!(element instanceof Element) || seen.has(element)) return;
    seen.add(element);
    out.push(element);
  });
  return out;
}

function getScanRoot() {
  const modal = findPurchaseModal();
  if (modal) return modal;
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
  const numericSkuCandidates = tokens.filter((token) => /^\d{8,}$/.test(token));
  const shopifyProductIds = uniqueLimited(numericSkuCandidates, 6);

  return {
    skus: uniqueLimited([...labelledSkus, ...skuCandidates, ...numericSkuCandidates], 16),
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

function renderPeriodRows(periods) {
  const normalized = periods || {};
  const rows = [
    ["today", "今天"],
    ["yesterday", "昨天"],
    ["last_7d", "7天"],
    ["last_30d", "30天"],
  ];
  return rows.map(([key, fallbackLabel]) => {
    const row = normalized[key] || {};
    return `
      <tr>
        <th scope="row">${escapeHtml(row.label || fallbackLabel)}</th>
        <td class="dpi-period-orders">${fmtNumber(row.orders)}</td>
        <td>$${fmtNumber(row.ad_spend_usd, 2)}</td>
        <td>${fmtRoas(row.roas)}</td>
      </tr>
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

function renderProductLinks(product, matched) {
  const productCode = compactText(product && product.product_code, 180);
  if (!matched || !productCode) return "";
  const encodedCode = encodeURIComponent(productCode);
  const productCenterUrl = `${PORTAL_BASE}/medias/?q=${encodedCode}`;
  const orderCenterUrl = `${PORTAL_BASE}/order-analytics/dxm-orders-view/order-trend/${encodedCode}`;
  return `
    <div class="dpi-product-actions">
      <a href="${escapeHtml(productCenterUrl)}" target="_blank" rel="noopener noreferrer">产品中心</a>
      <a href="${escapeHtml(orderCenterUrl)}" target="_blank" rel="noopener noreferrer">订单中心</a>
    </div>
  `;
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
  const totalOrders = summary.total_orders ?? orders.last_30d ?? 0;
  return `
    <div class="dpi-body">
      <div class="dpi-product-line">
        <span class="dpi-product-name">${matched ? escapeHtml(product.name || product.product_code || "已匹配产品") : "未匹配产品"}</span>
        ${renderProductLinks(product, matched)}
      </div>
      <div class="dpi-status-line">
        <span class="dpi-status ${statusClass(summary.delivery_status)}">${escapeHtml(summary.delivery_label || "--")}</span>
        <span class="dpi-muted">${escapeHtml(product.match_method || "")}</span>
      </div>
      <div class="dpi-total-grid">
        <div class="dpi-total-card">
          <span>总消耗</span>
          <strong class="dpi-total-value">$${fmtNumber(summary.ad_spend_usd, 2)}</strong>
        </div>
        <div class="dpi-total-card">
          <span>总ROAS</span>
          <strong class="dpi-total-value">${fmtRoas(summary.true_roas)}</strong>
        </div>
        <div class="dpi-total-card">
          <span>总订单量</span>
          <strong class="dpi-total-value">${fmtNumber(totalOrders)}</strong>
        </div>
      </div>
      <table class="dpi-period-table">
        <thead>
          <tr>
            <th scope="col">周期</th>
            <th scope="col">订单</th>
            <th scope="col">消耗</th>
            <th scope="col">ROAS</th>
          </tr>
        </thead>
        <tbody>${renderPeriodRows(summary.periods)}</tbody>
      </table>
      <div class="dpi-markets">
        ${renderMarkets(payload.markets)}
      </div>
      <div class="dpi-foot">
        <span>${escapeHtml(product.match_value || "")}</span>
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
  syncPanelPlacement();
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

function syncPanelPlacement() {
  const root = document.getElementById(DPI_ROOT_ID);
  if (!root) return;
  const modal = findPurchaseModal();
  if (!modal) {
    root.classList.remove("dpi-modal-anchored");
    root.style.left = "";
    root.style.right = "";
    root.style.top = "";
    root.style.width = "";
    root.style.height = "";
    return;
  }
  const rect = modal.getBoundingClientRect();
  const gap = 12;
  const rightMargin = 12;
  const top = Math.max(10, Math.round(rect.top));
  const height = Math.max(280, Math.min(Math.round(rect.height), window.innerHeight - top - 10));
  const desiredLeft = Math.round(rect.right + gap);
  const minWidth = 320;
  const left = Math.min(desiredLeft, Math.max(10, window.innerWidth - minWidth - rightMargin));
  root.classList.add("dpi-modal-anchored");
  root.style.left = `${left}px`;
  root.style.right = `${rightMargin}px`;
  root.style.top = `${top}px`;
  root.style.width = "auto";
  root.style.height = `${height}px`;

  const signature = compactText(modal.innerText, 240);
  if (signature && signature !== lastModalSignature && currentState.status !== "loading") {
    lastModalSignature = signature;
    window.setTimeout(refreshInsights, 250);
  }
}

function schedulePanelPlacement() {
  if (placementFrame) return;
  placementFrame = window.requestAnimationFrame(() => {
    placementFrame = 0;
    syncPanelPlacement();
  });
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
  const observer = new MutationObserver(schedulePanelPlacement);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "aria-hidden"],
  });
  window.addEventListener("resize", schedulePanelPlacement, { passive: true });
  window.addEventListener("scroll", schedulePanelPlacement, { passive: true });
  window.setInterval(schedulePanelPlacement, 500);
  window.setTimeout(refreshInsights, 1200);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", installPanel, { once: true });
} else {
  installPanel();
}
