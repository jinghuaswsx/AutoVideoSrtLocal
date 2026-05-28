(function () {
  const STATUS_LABELS = {
    not_ready: { text: '未就绪', cls: 'badge-gray' },
    pending:   { text: '待推送', cls: 'badge-blue' },
    pushed:    { text: '已推送', cls: 'badge-green' },
    failed:    { text: '推送失败', cls: 'badge-red' },
    skipped:   { text: '不推送', cls: 'badge-skipped' },
  };
  const READINESS_LABELS = {
    has_object: '视频',
    has_cover: '封面',
    has_copywriting: '文案',
    lang_supported: '链接',
    has_push_texts: '英文文案格式正确',
    shopify_image_confirmed: '图片/链接确认',
  };
  const REWORK_ISSUES = [
    { key: 'has_object', taskKey: 'translated_video', label: '视频' },
    { key: 'has_cover', taskKey: 'translated_cover', label: '封面' },
    { key: 'has_copywriting', taskKey: 'translated_copywriting', label: '文案' },
    { key: 'lang_supported', taskKey: 'language_supported', label: '链接' },
    { key: 'has_push_texts', taskKey: 'push_texts', label: '英文文案格式' },
    { key: 'shopify_image_confirmed', taskKey: 'shopify_images', label: '图片/链接确认' },
  ];

  const PUSH_MODAL_MODES = {
    CONFIRM: 'confirm',
    JSON: 'json',
    LOCALIZED_TEXT: 'localized-text',
    LOCALIZED_JSON: 'localized-json',
    PRODUCT_LINKS_JSON: 'product-links-json',
    PRODUCT_LINKS: 'product-links',
  };
  const AI_EVALUATION_TIMEOUT_MS = 5 * 60 * 1000;
  const AI_EVAL_REQUEST_PREVIEW_ENDPOINT = (pid) => `/medias/api/products/${pid}/evaluate/request-preview`;
  const AI_EVAL_STATUS_ENDPOINT = (pid, runId) => `/medias/api/products/${pid}/evaluate/status?run_id=${encodeURIComponent(runId || '')}`;

  const state = { page: 1, pageSize: 20, total: 0, items: [] };
  const DEFAULT_FILTERS = {
    status: 'pending',
    lang: '',
    product: '',
    keyword: '',
    owner_id: '',
    audit_result: '',
    date_from: '',
    date_to: '',
    sort: 'created_at_desc',
  };
  let LANGUAGES = [];
  let OWNERS = [];

  // ---------- 工具 ----------

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k === 'style') node.setAttribute('style', v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) node.setAttribute(k, '');
      else if (v === false || v === null || v === undefined) continue;
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function safeExternalHref(url) {
    const raw = String(url == null ? '' : url).trim();
    if (!raw) return '';
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return parsed.href;
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (_) {
      return '';
    }
  }

  function safeMediaSrc(url) {
    const raw = String(url == null ? '' : url).trim();
    if (!raw) return '';
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return parsed.href;
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (_) {
      return '';
    }
  }

  function previewMediaSrc(url) {
    const src = safeMediaSrc(url);
    if (!src) return '';
    try {
      const parsed = new URL(src, window.location.origin);
      if (parsed.pathname.startsWith('/medias/obj/')) {
        return parsed.pathname + parsed.search + parsed.hash;
      }
      return src;
    } catch (_) {
      return src;
    }
  }

  async function fetchJSON(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok && resp.status !== 204) {
      const body = await resp.text();
      throw Object.assign(new Error(`HTTP ${resp.status}`), { status: resp.status, body });
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  const langCountryMap = {
    'en': '英语',
    'de': '德国',
    'fr': '法国',
    'es': '西班牙',
    'it': '意大利',
    'ja': '日本',
    'ko': '韩国',
    'pt': '葡萄牙',
    'ru': '俄罗斯',
    'nl': '荷兰',
    'sv': '瑞典',
    'fi': '芬兰'
  };

  function formatLanguageLabel(code) {
    const raw = String(code || '').trim();
    const normalized = raw.toLowerCase();
    if (!normalized) return '';
    const upper = normalized.toUpperCase();
    if (langCountryMap[normalized]) {
      return `${langCountryMap[normalized]} ${upper}`;
    }
    const lang = LANGUAGES.find(l => l && l.code === normalized);
    const name = lang && lang.name_zh ? String(lang.name_zh).trim() : '';
    return name ? `${name} ${upper}` : upper || raw;
  }

  async function copyText(text) {
    const value = String(text || '');
    if (!value) throw new Error('empty text');
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    ta.style.pointerEvents = 'none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (!ok) throw new Error('copy failed');
  }

  function flashCopyButton(btn, text) {
    if (!btn) return;
    if (!btn.dataset.originalHtml) {
      btn.dataset.originalHtml = btn.innerHTML;
    }
    btn.innerHTML = `<span style="font-size: 11px; white-space: nowrap; color: var(--oc-success-fg); font-weight: 600;">${escapeHtml(text)}</span>`;
    if (btn._copyTimer) window.clearTimeout(btn._copyTimer);
    btn._copyTimer = window.setTimeout(() => {
      btn.innerHTML = btn.dataset.originalHtml;
    }, 1200);
  }

  function createCopyButton(value, attrName) {
    const btn = el('button', {
      type: 'button',
      class: 'product-copy-btn pm-copy-btn',
      title: '复制',
    });
    btn.innerHTML = `<svg class="icon-copy" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
</svg>`;
    btn.setAttribute(attrName, String(value || ''));
    return btn;
  }

  // ---------- 筛选与列表 ----------

  async function loadLanguages() {
    try {
      const data = await fetchJSON('/medias/api/languages');
      LANGUAGES = data.items || data.languages || [];
      const sel = document.getElementById('f-lang');
      const all = document.createElement('option');
      all.value = ''; all.textContent = '全部';
      sel.innerHTML = ''; sel.appendChild(all);
      LANGUAGES.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.code; opt.textContent = formatLanguageLabel(l.code);
        sel.appendChild(opt);
      });
    } catch (e) {
      console.warn('load languages failed', e);
    }
  }

  async function loadOwners() {
    const sel = document.getElementById('f-owner');
    if (!sel) return;
    sel.innerHTML = '';
    const all = document.createElement('option');
    all.value = '';
    all.textContent = '全部翻译工作负责人';
    sel.appendChild(all);
    if (!window.PUSH_IS_ADMIN) return;

    try {
      const data = await fetchJSON('/medias/api/users/active');
      OWNERS = Array.isArray(data && data.users) ? data.users : [];
      OWNERS.forEach(user => {
        const id = user && user.id;
        const name = String((user && user.display_name) || '').trim();
        if (id === null || id === undefined || !name) return;
        const opt = document.createElement('option');
        opt.value = String(id);
        opt.textContent = name;
        sel.appendChild(opt);
      });
    } catch (e) {
      console.warn('load owners failed', e);
    }
  }

  function normalizePage(value) {
    const page = Number.parseInt(String(value || ''), 10);
    return Number.isFinite(page) && page > 0 ? page : 1;
  }

  function normalizeSort(value) {
    return value === 'created_at_asc' ? 'created_at_asc' : DEFAULT_FILTERS.sort;
  }

  function paramValue(params, key, fallback) {
    return params.has(key) ? (params.get(key) || '') : fallback;
  }

  function setSelectValue(select, value, fallback) {
    if (!select) return;
    select.value = value;
    if (select.value !== value) select.value = fallback;
  }

  function applyUrlToFilters() {
    const params = new URLSearchParams(window.location.search);
    const statusSel = document.getElementById('f-status');
    const langSel = document.getElementById('f-lang');
    const ownerSel = document.getElementById('f-owner');
    const auditResultSel = document.getElementById('f-audit-result');
    const sortSel = document.getElementById('f-sort');

    setSelectValue(statusSel, paramValue(params, 'status', DEFAULT_FILTERS.status), DEFAULT_FILTERS.status);
    setSelectValue(langSel, paramValue(params, 'lang', DEFAULT_FILTERS.lang), DEFAULT_FILTERS.lang);
    setSelectValue(ownerSel, paramValue(params, 'owner_id', DEFAULT_FILTERS.owner_id), DEFAULT_FILTERS.owner_id);
    setSelectValue(auditResultSel, paramValue(params, 'audit_result', DEFAULT_FILTERS.audit_result), DEFAULT_FILTERS.audit_result);
    setSelectValue(sortSel, normalizeSort(paramValue(params, 'sort', DEFAULT_FILTERS.sort)), DEFAULT_FILTERS.sort);
    document.getElementById('f-product').value = paramValue(params, 'product', DEFAULT_FILTERS.product);
    document.getElementById('f-keyword').value = paramValue(params, 'keyword', DEFAULT_FILTERS.keyword);
    document.getElementById('f-date-from').value = paramValue(params, 'date_from', DEFAULT_FILTERS.date_from);
    document.getElementById('f-date-to').value = paramValue(params, 'date_to', DEFAULT_FILTERS.date_to);
    state.page = params.has('page') ? normalizePage(params.get('page')) : 1;
  }

  function buildQuery() {
    const params = new URLSearchParams();
    const statusSel = document.getElementById('f-status');
    params.set('status', statusSel.value);
    const langSel = document.getElementById('f-lang');
    params.set('lang', langSel.value);
    const product = document.getElementById('f-product').value.trim();
    params.set('product', product);
    const keyword = document.getElementById('f-keyword').value.trim();
    params.set('keyword', keyword);
    const ownerSel = document.getElementById('f-owner');
    params.set('owner_id', ownerSel ? ownerSel.value : '');
    const auditResultSel = document.getElementById('f-audit-result');
    params.set('audit_result', auditResultSel ? auditResultSel.value : '');
    const df = document.getElementById('f-date-from').value;
    params.set('date_from', df);
    const dt = document.getElementById('f-date-to').value;
    params.set('date_to', dt);
    const sortSel = document.getElementById('f-sort');
    params.set('sort', sortSel.value || 'created_at_desc');
    params.set('page', String(state.page));
    return params.toString();
  }

  function syncUrlFromFilters(mode) {
    if (!window.history || (!window.history.pushState && !window.history.replaceState)) return;
    const query = buildQuery();
    const nextUrl = `${window.location.pathname}?${query}${window.location.hash || ''}`;
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash || ''}`;
    if (nextUrl === currentUrl) return;
    if (mode === 'replace' && window.history.replaceState) {
      history.replaceState(null, '', nextUrl);
      return;
    }
    if (window.history.pushState) {
      history.pushState(null, '', nextUrl);
    }
  }

  function buildPageHref(page) {
    const oldPage = state.page;
    state.page = page;
    const query = buildQuery();
    state.page = oldPage;
    return `${window.location.pathname}?${query}${window.location.hash || ''}`;
  }

  function renderReadinessText(readiness) {
    const parts = Object.entries(READINESS_LABELS).map(([key, label]) => {
      const ok = readiness[key];
      return `<span class="ready-item ${ok ? 'ready-ok' : 'ready-bad'}">${label}</span>`;
    });
    let html = `<div class="ready-row">${parts.join('<span class="ready-sep">|</span>')}</div>`;
    if (readiness.shopify_image_domain_details && readiness.shopify_image_domain_details.length > 0) {
      const domainParts = readiness.shopify_image_domain_details.map(d => {
        if (d.confirmed) {
          return `<span class="ready-item ready-ok">${escapeHtml(d.domain)} ✓</span>`;
        }
        return `<span class="ready-item ready-bad">${escapeHtml(d.domain)} ❌</span>`;
      });
      html += `<div class="ready-row ready-row-domain">${domainParts.join('<span class="ready-sep">|</span>')}</div>`;
    }
    return html;
  }

  function renderStatusBadge(status) {
    const s = STATUS_LABELS[status] || { text: status, cls: '' };
    return `<span class="badge ${s.cls}">${s.text}</span>`;
  }

  function normalizeListingStatus(status) {
    const value = String(status || '上架').trim();
    return value || '上架';
  }

  function listingStatusClass(status) {
    return normalizeListingStatus(status) === '下架' ? 'audit-status-off' : 'audit-status-on';
  }

  function renderListingStatusBadge(status) {
    const value = normalizeListingStatus(status);
    return `<span class="audit-status ${listingStatusClass(value)}">${escapeHtml(value)}</span>`;
  }

  function createListingStatusBadge(status) {
    const value = normalizeListingStatus(status);
    return el('span', { class: `audit-status ${listingStatusClass(value)}` }, value);
  }

  function formatAuditScore(score) {
    if (score === null || score === undefined || score === '') return '未评分';
    const num = Number(score);
    if (!Number.isFinite(num)) return String(score);
    return Number.isInteger(num) ? String(num) : num.toFixed(1);
  }

  function formatAuditDetail(detail) {
    if (detail === null || detail === undefined || detail === '') return '-';
    if (typeof detail === 'string') {
      const trimmed = detail.trim();
      if (!trimmed) return '-';
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch (_) {
        return detail;
      }
    }
    try {
      return JSON.stringify(detail, null, 2);
    } catch (_) {
      return String(detail);
    }
  }

  function renderAuditDetailNode(detail, options) {
    const opts = options || {};
    const wrap = el('div', { class: 'v audit-country-table-value' });
    const table = window.EvalCountryTable;
    if (table && typeof table.renderCompact === 'function') {
      const parsed = typeof table.parse === 'function' ? table.parse(detail) : null;
      if (parsed && Array.isArray(parsed.countries) && parsed.countries.length) {
        wrap.innerHTML = window.EvalCountryTable.renderCompact(detail, opts);
        return wrap;
      }
    }
    wrap.appendChild(el('pre', { class: 'audit-detail-pre' }, formatAuditDetail(detail)));
    return wrap;
  }

  function aiEvaluationFailureReason(reason) {
    const text = String(reason || '').trim();
    return text || '服务器没有返回评估结果';
  }

  function aiEvaluationErrorMessage(err) {
    if (!err) return '';
    if (err.name === 'AbortError') return '';
    if (err.body) {
      try {
        const parsed = JSON.parse(err.body);
        const text = parsed.error || parsed.message || parsed.detail || '';
        if (text) return String(text);
      } catch (_) {
        if (String(err.body).trim()) return String(err.body).trim();
      }
    }
    const message = String(err.message || err || '').trim();
    if (!message || message.includes('Unexpected end of JSON input')) return '';
    return message;
  }

  function ensureAiEvaluationRequestModalStyle() {
    if (document.getElementById('aiEvaluationRequestModalStyle')) return;
    const style = document.createElement('style');
    style.id = 'aiEvaluationRequestModalStyle';
    style.textContent = `
      .ect-modal--ai-evaluating { max-width:min(1560px, calc(100vw - 48px)); min-height:min(820px, calc(100vh - 48px)); }
      .ect-modal--ai-evaluating .ect-modal-body { display:flex; flex-direction:column; min-height:0; padding:0; overflow:hidden; }
      .ect-ai-topbar { display:flex; align-items:center; justify-content:center; gap:24px; min-height:92px; padding:22px 20px; border-bottom:1px solid var(--oc-border); background:var(--oc-bg-subtle); }
      .ect-ai-status { display:flex; align-items:center; justify-content:center; gap:14px; min-width:0; }
      .ect-ai-status-dot { width:16px; height:16px; border-radius:50%; background:var(--oc-accent); box-shadow:0 0 0 7px var(--oc-accent-ring); }
      .ect-ai-status-title { font-size:26px; line-height:1.3; font-weight:700; color:var(--oc-fg); }
      .ect-ai-request-timer { display:inline-flex; align-items:center; height:44px; padding:0 16px; border-radius:999px; background:var(--oc-cyan-subtle); color:var(--oc-accent); font-size:22px; line-height:1.3; font-weight:700; font-variant-numeric:tabular-nums; }
      .ect-ai-country-progress { padding:14px 20px; border-bottom:1px solid var(--oc-border); background:var(--oc-bg); }
      .ect-ai-country-progress:empty { display:none; }
      .ect-ai-progress-summary { display:flex; flex-wrap:wrap; gap:10px 16px; align-items:center; margin-bottom:12px; color:var(--oc-fg-muted); font-size:13px; }
      .ect-ai-country-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; }
      .ect-ai-country-card { min-height:82px; border:1px solid var(--oc-border); border-radius:10px; padding:10px; background:var(--oc-bg-subtle); display:grid; gap:6px; }
      .ect-ai-country-card.is-running { border-color:var(--oc-accent); background:var(--oc-cyan-subtle); }
      .ect-ai-country-card.is-completed { border-color:var(--oc-success); background:var(--oc-success-bg); }
      .ect-ai-country-card.is-failed { border-color:var(--oc-danger); background:var(--oc-danger-bg); }
      .ect-ai-country-head { display:flex; justify-content:space-between; gap:8px; align-items:center; min-width:0; }
      .ect-ai-country-name { font-size:13px; font-weight:700; color:var(--oc-fg); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .ect-ai-country-pill { flex:0 0 auto; border-radius:999px; padding:2px 8px; background:var(--oc-bg); color:var(--oc-fg-muted); font-size:12px; font-weight:700; }
      .ect-ai-country-meta { color:var(--oc-fg-muted); font-size:12px; line-height:1.45; overflow-wrap:anywhere; }
      .ect-ai-inline-summary { margin-top:12px; border:1px solid var(--oc-border); border-radius:8px; padding:12px; background:var(--oc-bg-subtle); display:grid; gap:12px; }
      .ect-ai-inline-summary-head { display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; }
      .ect-ai-inline-summary-title { font-size:13px; font-weight:700; color:var(--oc-fg); }
      .ect-ai-inline-summary-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
      .ect-ai-inline-result-tag { display:inline-flex; align-items:center; min-height:24px; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700; }
      .ect-ai-inline-result-tag.is-good { background:var(--oc-success-bg); color:var(--oc-success-fg); }
      .ect-ai-inline-result-tag.is-mid { background:var(--oc-warning-bg); color:var(--oc-warning-fg); }
      .ect-ai-inline-result-tag.is-bad { background:var(--oc-danger-bg); color:var(--oc-danger-fg); }
      .ect-ai-inline-result-tag.is-info { background:var(--oc-accent-subtle); color:var(--oc-accent); }
      .ect-ai-inline-result-tag.is-na { background:var(--oc-bg-muted); color:var(--oc-fg-muted); }
      .ect-ai-inline-score-card { display:grid; grid-template-columns:auto minmax(140px, 220px); gap:6px 12px; align-items:center; max-width:360px; padding:10px 12px; border:1px solid var(--oc-border); border-radius:8px; background:var(--oc-bg); }
      .ect-ai-inline-score-label { color:var(--oc-fg-muted); font-size:12px; }
      .ect-ai-inline-score-num { font-family:var(--font-mono, ui-monospace, Consolas, monospace); font-size:20px; line-height:1.2; font-weight:700; font-variant-numeric:tabular-nums; }
      .ect-ai-inline-score-num span { margin-left:2px; color:var(--oc-fg-subtle); font-size:12px; font-weight:500; }
      .ect-ai-inline-score-bar { grid-column:1 / -1; height:5px; border-radius:999px; overflow:hidden; background:var(--oc-bg-muted); }
      .ect-ai-inline-score-bar.small { height:4px; }
      .ect-ai-inline-score-fill { height:100%; border-radius:inherit; transition:width 180ms ease-out; }
      .ect-ai-inline-score-card.is-good .ect-ai-inline-score-num, .ect-ai-inline-country-summary.is-good .ect-ai-inline-country-score { color:var(--oc-success-fg); }
      .ect-ai-inline-score-card.is-good .ect-ai-inline-score-fill, .ect-ai-inline-country-summary.is-good .ect-ai-inline-score-fill { background:var(--oc-success); }
      .ect-ai-inline-score-card.is-mid .ect-ai-inline-score-num, .ect-ai-inline-country-summary.is-mid .ect-ai-inline-country-score { color:var(--oc-warning-fg); }
      .ect-ai-inline-score-card.is-mid .ect-ai-inline-score-fill, .ect-ai-inline-country-summary.is-mid .ect-ai-inline-score-fill { background:var(--oc-warning); }
      .ect-ai-inline-score-card.is-bad .ect-ai-inline-score-num, .ect-ai-inline-country-summary.is-bad .ect-ai-inline-country-score { color:var(--oc-danger-fg); }
      .ect-ai-inline-score-card.is-bad .ect-ai-inline-score-fill, .ect-ai-inline-country-summary.is-bad .ect-ai-inline-score-fill { background:var(--oc-danger); }
      .ect-ai-inline-score-card.is-na .ect-ai-inline-score-num, .ect-ai-inline-country-summary.is-na .ect-ai-inline-country-score { color:var(--oc-fg-subtle); }
      .ect-ai-inline-score-card.is-na .ect-ai-inline-score-fill, .ect-ai-inline-country-summary.is-na .ect-ai-inline-score-fill { background:var(--oc-border-strong); }
      .ect-ai-inline-summary-list { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px; }
      .ect-ai-inline-country-summary { min-width:0; border:1px solid var(--oc-border); border-radius:8px; padding:10px; background:var(--oc-bg); display:grid; gap:7px; }
      .ect-ai-inline-country-top { display:flex; align-items:baseline; justify-content:space-between; gap:8px; min-width:0; }
      .ect-ai-inline-country-name { color:var(--oc-fg); font-size:13px; font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .ect-ai-inline-country-score { flex:0 0 auto; font-family:var(--font-mono, ui-monospace, Consolas, monospace); font-size:13px; font-weight:700; font-variant-numeric:tabular-nums; }
      .ect-ai-inline-country-score span { color:var(--oc-fg-subtle); font-size:11px; font-weight:500; }
      .ect-ai-inline-country-text { color:var(--oc-fg); font-size:12px; line-height:1.55; overflow-wrap:anywhere; }
      .ect-ai-tabs { display:flex; gap:8px; padding:12px 20px 0; background:var(--oc-bg); }
      .ect-ai-tab { height:32px; padding:0 14px; border:1px solid var(--oc-border-strong); border-radius:8px 8px 0 0; background:var(--oc-bg-subtle); color:var(--oc-fg-muted); font-size:13px; font-weight:600; cursor:pointer; }
      .ect-ai-tab.active { background:var(--oc-bg); color:var(--oc-accent); border-color:var(--oc-accent); }
      .ect-ai-panels { flex:1 1 auto; min-height:0; overflow:auto; padding:20px; }
      .ect-ai-panel[hidden] { display:none !important; }
      .ect-ai-grid { display:grid; grid-template-columns:minmax(320px, 420px) minmax(0, 1fr); gap:18px; align-items:start; }
      .ect-ai-card { border:1px solid var(--oc-border); border-radius:12px; background:var(--oc-bg); padding:16px; }
      .ect-ai-card h4 { margin:0 0 12px; font-size:14px; color:var(--oc-fg); }
      .ect-ai-media { display:grid; gap:12px; justify-items:start; }
      .ect-ai-cover { width:180px; height:180px; border:1px solid var(--oc-border); border-radius:10px; overflow:hidden; background:var(--oc-bg-muted); display:flex; align-items:center; justify-content:center; color:var(--oc-fg-muted); font-size:13px; }
      .ect-ai-cover img, .ect-ai-video video { width:100%; height:100%; object-fit:contain; display:block; background:var(--oc-bg-muted); }
      .ect-ai-video-name { width:180px; min-height:58px; color:var(--oc-fg-muted); font-size:13px; line-height:1.45; overflow:hidden; overflow-wrap:anywhere; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; }
      .ect-ai-video { width:180px; height:320px; border:1px solid var(--oc-border); border-radius:10px; overflow:hidden; background:var(--oc-bg-muted); display:flex; align-items:center; justify-content:center; color:var(--oc-fg-muted); font-size:13px; }
      .ect-ai-kv { display:grid; grid-template-columns:92px minmax(0, 1fr); gap:8px 12px; font-size:13px; line-height:1.55; }
      .ect-ai-kv dt { color:var(--oc-fg-subtle); }
      .ect-ai-kv dd { margin:0; min-width:0; overflow-wrap:anywhere; color:var(--oc-fg); }
      .ect-ai-code { margin:0; max-height:260px; overflow:auto; padding:12px; border-radius:10px; background:var(--oc-bg-subtle); border:1px solid var(--oc-border); font:12px/1.55 var(--font-mono, ui-monospace, Consolas, monospace); white-space:pre-wrap; word-break:break-word; }
      .ect-ai-actions { display:flex; gap:10px; align-items:center; justify-content:flex-end; margin-bottom:14px; }
      .ect-ai-btn { height:32px; padding:0 12px; border-radius:8px; border:1px solid var(--oc-border-strong); background:var(--oc-bg); color:var(--oc-fg); font-size:13px; font-weight:600; cursor:pointer; }
      .ect-ai-btn.primary { background:var(--oc-accent); border-color:var(--oc-accent); color:var(--oc-accent-fg, #fff); }
      .ect-ai-sections { margin-top:18px; }
      .ect-ai-empty { min-height:280px; display:flex; align-items:center; justify-content:center; color:var(--oc-fg-muted); text-align:center; line-height:1.7; }
      .ect-ai-detail-modal .ect-modal-body { padding:16px; }
      .ect-ai-detail-modal .ect-modal-json { max-height:62vh; }
      @media (max-width: 900px) { .ect-ai-grid { grid-template-columns:1fr; } .ect-ai-panels { min-height:420px; } }
    `;
    document.head.appendChild(style);
  }

  function aiEvaluationElapsedSeconds(modalState) {
    return Math.max(0, Math.floor((Date.now() - modalState.startedAt) / 1000));
  }

  function stopAiEvaluationTimers(modalState) {
    if (!modalState) return;
    if (modalState.timer) {
      window.clearInterval(modalState.timer);
      modalState.timer = null;
    }
    if (modalState.timeoutTimer) {
      window.clearTimeout(modalState.timeoutTimer);
      modalState.timeoutTimer = null;
    }
    if (modalState.progressTimer) {
      window.clearInterval(modalState.progressTimer);
      modalState.progressTimer = null;
    }
  }

  function openAiEvaluationRequestModal(product) {
    if (!window.EvalCountryTable || typeof window.EvalCountryTable.openModal !== 'function') {
      throw new Error('AI评估弹窗组件未加载');
    }
    const titleText = product && product.name ? `AI评估 - ${product.name}` : 'AI评估';
    ensureAiEvaluationRequestModalStyle();
    const shell = window.EvalCountryTable.openModal('', { title: titleText });
    const modalState = {
      overlay: shell.overlay,
      modal: shell.modal,
      close: shell.close,
      body: shell.modal.querySelector('.ect-modal-body'),
      status: null,
      statusTitle: null,
      startedAt: Date.now(),
      timer: null,
      timeoutTimer: null,
      done: false,
      activeTab: 'request',
      preview: null,
      previewError: '',
      resultHtml: '',
      fullPayloadUrl: '',
      progress: null,
      progressTimer: null,
      evaluationDetail: null,
    };
    modalState.modal.classList.add('ect-modal--ai-evaluating');

    function updateElapsed() {
      if (modalState.done) return;
      if (modalState.status) {
        modalState.status.textContent = `已请求 ${aiEvaluationElapsedSeconds(modalState)} 秒`;
      }
    }
    function close() {
      stopAiEvaluationTimers(modalState);
      document.removeEventListener('keydown', onKey);
      shell.close();
    }
    function onKey(event) {
      if (event.key === 'Escape') close();
    }

    shell.overlay.querySelectorAll('.ect-modal-close, .ect-modal-button').forEach((btn) => {
      btn.addEventListener('click', close, { once: true });
    });
    shell.overlay.addEventListener('click', (event) => {
      if (event.target === shell.overlay) close();
    }, { capture: true, once: true });
    document.addEventListener('keydown', onKey);
    modalState.timer = window.setInterval(updateElapsed, 1000);
    modalState.timeoutTimer = window.setTimeout(() => {
      if (modalState.done) return;
      setAiEvaluationModalFailure(modalState, '服务器没有返回评估结果');
    }, AI_EVALUATION_TIMEOUT_MS);
    renderAiEvaluationShell(modalState);
    setAiEvaluationModalLoading(modalState);
    return modalState;
  }

  function renderAiEvaluationShell(modalState) {
    if (!modalState || !modalState.body) return;
    modalState.body.innerHTML = `
      <div class="ect-ai-topbar">
        <div class="ect-ai-status">
          <span class="ect-ai-status-dot"></span>
          <span class="ect-ai-status-title" data-ai-eval-status-title>正在请求中</span>
        </div>
        <span class="ect-ai-request-timer" data-ai-eval-status>已请求 ${aiEvaluationElapsedSeconds(modalState)} 秒</span>
      </div>
      <div class="ect-ai-country-progress" data-ai-country-progress></div>
      <div class="ect-ai-tabs" role="tablist">
        <button type="button" class="ect-ai-tab active" data-ai-eval-tab="request">请求报文</button>
        <button type="button" class="ect-ai-tab" data-ai-eval-tab="result">结果</button>
      </div>
      <div class="ect-ai-panels">
        <section class="ect-ai-panel" data-ai-eval-panel="request"></section>
        <section class="ect-ai-panel" data-ai-eval-panel="result" hidden></section>
      </div>`;
    modalState.status = modalState.body.querySelector('[data-ai-eval-status]');
    modalState.statusTitle = modalState.body.querySelector('[data-ai-eval-status-title]');
    modalState.body.querySelectorAll('[data-ai-eval-tab]').forEach((btn) => {
      btn.addEventListener('click', () => switchAiEvaluationTab(modalState, btn.dataset.aiEvalTab));
    });
    renderAiEvaluationRequestPreview(modalState);
    renderAiEvaluationResultPanel(modalState);
    renderAiEvaluationCountryProgress(modalState);
  }

  function switchAiEvaluationTab(modalState, tab) {
    modalState.activeTab = tab === 'result' ? 'result' : 'request';
    modalState.body.querySelectorAll('[data-ai-eval-tab]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.aiEvalTab === modalState.activeTab);
    });
    modalState.body.querySelectorAll('[data-ai-eval-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.aiEvalPanel !== modalState.activeTab;
    });
  }

  function aiEvaluationCountryStatusLabel(status) {
    const normalized = String(status || '').trim().toLowerCase();
    if (normalized === 'running') return '进行中';
    if (normalized === 'completed') return '已完成';
    if (normalized === 'failed') return '报错';
    return '排队中';
  }

  function aiEvaluationTerminalStatus(status) {
    return ['completed', 'partially_completed', 'failed'].includes(String(status || '').trim().toLowerCase());
  }

  function setAiEvaluationProgress(modalState, progress) {
    if (!modalState || !progress) return;
    modalState.progress = progress;
    const status = String(progress.status || '').trim().toLowerCase();
    if (modalState.statusTitle && !modalState.done) {
      if (status === 'completed') modalState.statusTitle.textContent = '评估完成';
      else if (status === 'partially_completed') modalState.statusTitle.textContent = '部分完成';
      else if (status === 'failed') modalState.statusTitle.textContent = '评估失败';
      else modalState.statusTitle.textContent = '正在评估';
    }
    renderAiEvaluationCountryProgress(modalState);
  }

  function aiEvaluationResultDetail(data) {
    const result = data && data.result;
    const detail = data && (
      data.ai_evaluation_detail
      || (result && (result.ai_evaluation_detail || result.detail))
      || data.detail
      || result
      || data
    );
    if (typeof detail === 'string') {
      try {
        return JSON.parse(detail);
      } catch (_err) {
        return detail;
      }
    }
    return detail;
  }

  function aiEvaluationNumberScore(rawScore) {
    if (rawScore === null || rawScore === undefined || rawScore === '') return NaN;
    const score = Number(rawScore);
    return Number.isFinite(score) ? score : NaN;
  }

  function aiEvaluationScoreTone(score) {
    if (!Number.isFinite(score)) return 'na';
    if (score >= 80) return 'good';
    if (score >= 60) return 'mid';
    return 'bad';
  }

  function aiEvaluationScorePercent(score) {
    if (!Number.isFinite(score)) return 0;
    return Math.max(0, Math.min(100, score));
  }

  function aiEvaluationResultTone(resultText) {
    const text = String(resultText || '').trim();
    if (!text) return 'na';
    if (text.includes('不适合')) return 'bad';
    if (text.includes('需人工')) return 'mid';
    if (text.includes('适合')) return 'good';
    return 'info';
  }

  function aiEvaluationScoreText(score, rawScore) {
    if (!Number.isFinite(score)) return '—';
    const rawText = String(rawScore === null || rawScore === undefined ? '' : rawScore).trim();
    return rawText || String(score);
  }

  function renderAiEvaluationInlineSummary(modalState) {
    const detail = modalState && modalState.evaluationDetail;
    if (!detail || typeof detail !== 'object') return '';
    const countries = Array.isArray(detail.countries) ? detail.countries : [];
    const rawScore = detail.ai_score;
    const score = rawScore === null || rawScore === undefined || rawScore === '' ? NaN : aiEvaluationNumberScore(rawScore);
    const overallTone = aiEvaluationScoreTone(score);
    const scoreText = aiEvaluationScoreText(score, rawScore);
    const scorePercent = aiEvaluationScorePercent(score);
    const resultText = String(detail.ai_evaluation_result || '').trim();
    const resultTone = aiEvaluationResultTone(resultText);
    const scoreCard = Number.isFinite(score) ? `
      <div class="ect-ai-inline-score-card is-${overallTone}">
        <span class="ect-ai-inline-score-label">综合评分</span>
        <span class="ect-ai-inline-score-num">${escapeHtml(scoreText)}<span>/100</span></span>
        <div class="ect-ai-inline-score-bar"><div class="ect-ai-inline-score-fill" style="width:${scorePercent}%"></div></div>
      </div>` : '';
    const resultTag = resultText ? `<span class="ect-ai-inline-result-tag is-${resultTone}">${escapeHtml(resultText)}</span>` : '';
    const coloredItems = countries.map((country) => {
      const countryScore = aiEvaluationNumberScore(country.score);
      const countryTone = aiEvaluationScoreTone(countryScore);
      const countryScoreText = aiEvaluationScoreText(countryScore, country.score);
      const countryPercent = aiEvaluationScorePercent(countryScore);
      const text = String(country.summary || country.reason || '').trim();
      if (!text && !Number.isFinite(countryScore)) return '';
      const label = String(country.country || country.name || country.language || country.lang || '').trim();
      const displayLabel = label || '国家';
      return `
        <div class="ect-ai-inline-country-summary is-${countryTone}">
          <div class="ect-ai-inline-country-top">
            <span class="ect-ai-inline-country-name">${escapeHtml(displayLabel)}</span>
            <span class="ect-ai-inline-country-score">${escapeHtml(countryScoreText)}<span>/100</span></span>
          </div>
          <div class="ect-ai-inline-score-bar small"><div class="ect-ai-inline-score-fill" style="width:${countryPercent}%"></div></div>
          <div class="ect-ai-inline-country-text">${escapeHtml(text || '暂无摘要')}</div>
        </div>`;
    }).filter(Boolean).slice(0, 6).join('');
    if (!scoreCard && !resultTag && !coloredItems) return '';
    return `
      <div class="ect-ai-inline-summary" data-ai-eval-summary>
        <div class="ect-ai-inline-summary-head">
          <div class="ect-ai-inline-summary-title">评估摘要</div>
          ${resultTag ? `<div class="ect-ai-inline-summary-actions">${resultTag}</div>` : ''}
        </div>
        ${scoreCard}
        ${coloredItems ? `<div class="ect-ai-inline-summary-list">${coloredItems}</div>` : ''}
      </div>`;
  }

  function renderAiEvaluationCountryProgress(modalState) {
    const box = modalState && modalState.body && modalState.body.querySelector('[data-ai-country-progress]');
    if (!box) return;
    const progress = modalState.progress || {};
    const countries = Array.isArray(progress.countries) ? progress.countries : [];
    if (!countries.length) {
      box.innerHTML = '';
      return;
    }
    const summary = progress.summary || {};
    const total = summary.total || countries.length;
    const completed = summary.completed || countries.filter((item) => item.status === 'completed').length;
    const failed = summary.failed || countries.filter((item) => item.status === 'failed').length;
    const running = summary.running || countries.filter((item) => item.status === 'running').length;
    const queued = summary.queued || countries.filter((item) => item.status === 'queued').length;
    const providerText = [progress.provider, progress.model].filter(Boolean).join(' / ');
    const cards = countries.map((country) => {
      const status = String(country.status || 'queued').trim().toLowerCase();
      const displayStatus = ['queued', 'running', 'completed', 'failed'].includes(status) ? status : 'queued';
      const countryName = country.name || country.country || country.lang || '-';
      const code = country.lang ? String(country.lang).toUpperCase() : '';
      const elapsed = country.elapsed_seconds ? `${country.elapsed_seconds} 秒` : '';
      const error = country.error ? `错误：${country.error}` : '';
      const meta = [code, elapsed, error].filter(Boolean).join(' · ') || '等待调度';
      return `
        <div class="ect-ai-country-card is-${displayStatus}">
          <div class="ect-ai-country-head">
            <span class="ect-ai-country-name">${escapeHtml(countryName)}</span>
            <span class="ect-ai-country-pill">${escapeHtml(aiEvaluationCountryStatusLabel(displayStatus))}</span>
          </div>
          <div class="ect-ai-country-meta">${escapeHtml(meta)}</div>
        </div>`;
    }).join('');
    box.innerHTML = `
      <div class="ect-ai-progress-summary">
        <span>国家进度：已完成 ${escapeHtml(String(completed))}/${escapeHtml(String(total))}</span>
        <span>进行中 ${escapeHtml(String(running))}</span>
        <span>排队中 ${escapeHtml(String(queued))}</span>
        <span>报错 ${escapeHtml(String(failed))}</span>
        ${providerText ? `<span>${escapeHtml(providerText)}</span>` : ''}
      </div>
      <div class="ect-ai-country-grid">${cards}</div>
      ${renderAiEvaluationInlineSummary(modalState)}`;
  }

  function pollAiEvaluationStatus(modalState, pid, runId, onComplete) {
    return new Promise((resolve, reject) => {
      let finished = false;
      function cleanup() {
        if (modalState.progressTimer) {
          window.clearInterval(modalState.progressTimer);
          modalState.progressTimer = null;
        }
        if (modalState.timeoutTimer) {
          window.clearTimeout(modalState.timeoutTimer);
          modalState.timeoutTimer = null;
        }
      }
      function finish(error, data) {
        if (finished) return;
        finished = true;
        cleanup();
        if (error) {
          reject(error);
          return;
        }
        if (typeof onComplete === 'function') onComplete(data);
        resolve(data);
      }
      async function tick() {
        if (finished) return;
        try {
          const data = await fetchJSON(AI_EVAL_STATUS_ENDPOINT(pid, runId));
          const progress = data && data.progress ? data.progress : data;
          setAiEvaluationProgress(modalState, progress);
          const status = progress && progress.status;
          if (!aiEvaluationTerminalStatus(status)) return;
          if (String(status || '').toLowerCase() === 'failed' && !(data && data.result)) {
            finish(new Error(progress.error || '评估失败'), data);
            return;
          }
          finish(null, data);
        } catch (err) {
          finish(err);
        }
      }
      cleanup();
      modalState.progressTimer = window.setInterval(tick, 2000);
      modalState.timeoutTimer = window.setTimeout(() => {
        finish(new Error('服务器没有返回评估结果'));
      }, AI_EVALUATION_TIMEOUT_MS);
      tick();
    });
  }

  async function loadAiEvaluationRequestPreview(modalState, pid) {
    try {
      const data = await fetchJSON(AI_EVAL_REQUEST_PREVIEW_ENDPOINT(pid));
      modalState.preview = data.payload || null;
      modalState.fullPayloadUrl = (modalState.preview && modalState.preview.full_payload_url)
        || `/medias/api/products/${pid}/evaluate/request-payload`;
      renderAiEvaluationRequestPreview(modalState);
    } catch (err) {
      modalState.previewError = aiEvaluationErrorMessage(err) || '加载请求报文失败';
      renderAiEvaluationRequestPreview(modalState);
    }
  }

  function renderAiEvaluationRequestPreview(modalState) {
    const panel = modalState && modalState.body && modalState.body.querySelector('[data-ai-eval-panel="request"]');
    if (!panel) return;
    renderAiEvaluationRequestPreviewToPanel(panel, {
      preview: modalState.preview,
      previewError: modalState.previewError,
      fullPayloadUrl: modalState.fullPayloadUrl,
    });
  }

  function formatAiEvaluationVideoProcessing(video) {
    const info = (video && video.processing) || {};
    const parts = [];
    if (video && video.clip_seconds) parts.push(`${video.clip_seconds}秒短片`);
    if (info.max_height) parts.push(`${info.max_height}P`);
    if (info.fps) parts.push(`${info.fps}帧`);
    if (info.video_bitrate) parts.push(`${String(info.video_bitrate).toUpperCase()}码率`);
    if (info.drop_audio === false && info.audio_bitrate) parts.push(`音频${info.audio_bitrate}`);
    return parts.join(' / ') || '-';
  }

  function renderAiEvaluationRequestPreviewToPanel(panel, opts) {
    const options = opts || {};
    const preview = options.preview;
    if (options.previewError) {
      panel.innerHTML = `<div class="ect-ai-empty">请求报文加载失败：${escapeHtml(options.previewError)}</div>`;
      return;
    }
    if (!preview) {
      panel.innerHTML = '<div class="ect-ai-empty">正在加载请求报文、素材和提示词...</div>';
      return;
    }
    const cover = (preview.media || []).find((item) => item.role === 'product_cover') || {};
    const video = (preview.media || []).find((item) => item.role === 'english_video') || {};
    const product = preview.product || {};
    const productUrl = safeExternalHref(product.product_url);
    const coverPreviewUrl = safeMediaSrc(cover.preview_url);
    const videoPreviewUrl = safeMediaSrc(video.preview_url);
    const videoDisplayName = video.submitted_filename || video.filename || video.object_key || '';
    const originalVideoUrl = safeMediaSrc(video.original_preview_url);
    panel.innerHTML = `
      <div class="ect-ai-actions">
        <button type="button" class="ect-ai-btn primary" data-ai-full-payload>请求报文</button>
      </div>
      <div class="ect-ai-grid">
        <div class="ect-ai-card">
          <h4>素材预览</h4>
          <div class="ect-ai-media">
            <div class="ect-ai-cover">${coverPreviewUrl ? `<img src="${escapeHtml(coverPreviewUrl)}" alt="商品主图">` : '暂无主图'}</div>
            <div class="ect-ai-video-name" title="${escapeAttr(videoDisplayName || video.object_key || '')}">${escapeHtml(videoDisplayName || '暂无视频文件名')}</div>
            <div class="ect-ai-video">${videoPreviewUrl ? `<video controls preload="metadata" src="${escapeHtml(videoPreviewUrl)}"></video>` : '暂无视频'}</div>
          </div>
        </div>
        <div class="ect-ai-card">
          <h4>请求关键元素</h4>
          <dl class="ect-ai-kv">
            <dt>产品</dt><dd>${escapeHtml(product.name || '-')} (#${escapeHtml(product.id || '-')})</dd>
            <dt>产品 ID</dt><dd>${escapeHtml(product.product_code || '-')}</dd>
            <dt>产品链接</dt><dd>${productUrl ? `<a href="${escapeHtml(productUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(product.product_url)}</a>` : escapeHtml(product.product_url || '-')}</dd>
            <dt>主图</dt><dd>${escapeHtml(cover.object_key || '-')}</dd>
            <dt>AI请求短片</dt><dd>${escapeHtml(formatAiEvaluationVideoProcessing(video))}</dd>
            <dt>短片预览</dt><dd>${videoPreviewUrl ? `<a href="${escapeHtml(videoPreviewUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(videoPreviewUrl)}</a>` : '-'}</dd>
            <dt>原始视频</dt><dd>${originalVideoUrl ? `<a href="${escapeHtml(originalVideoUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(video.object_key || originalVideoUrl)}</a>` : escapeHtml(video.object_key || '-')}</dd>
            <dt>语种</dt><dd>${escapeHtml((preview.languages || []).map((lang) => `${lang.name}(${lang.code})`).join('、') || '-')}</dd>
            <dt>UseCase</dt><dd>${escapeHtml((preview.llm && preview.llm.use_case) || '-')}</dd>
            <dt>Provider</dt><dd>${escapeHtml((preview.llm && preview.llm.provider) || '-')}</dd>
            <dt>Model</dt><dd>${escapeHtml((preview.llm && preview.llm.model) || '-')}</dd>
            <dt>Search</dt><dd>${preview.llm && preview.llm.google_search ? escapeHtml(JSON.stringify(preview.llm.tools || [])) : '-'}</dd>
            <dt>参数</dt><dd>temperature=${escapeHtml(preview.llm && preview.llm.temperature)}, max_output_tokens=${escapeHtml(preview.llm && preview.llm.max_output_tokens)}</dd>
          </dl>
        </div>
      </div>
      ${renderAiEvaluationPromptSections(preview)}`;
    const btn = panel.querySelector('[data-ai-full-payload]');
    if (btn) {
      btn.addEventListener('click', () => openAiEvaluationPayloadDetail({
        fullPayloadUrl: options.fullPayloadUrl || preview.full_payload_url,
      }));
    }
  }

  function renderAiEvaluationPromptSections(preview) {
    const prompts = (preview && preview.prompts) || {};
    return `
      <div class="ect-ai-grid ect-ai-sections">
        <div class="ect-ai-card">
          <h4>System Prompt</h4>
          <pre class="ect-ai-code">${escapeHtml(prompts.system || '')}</pre>
        </div>
        <div class="ect-ai-card">
          <h4>User Prompt</h4>
          <pre class="ect-ai-code">${escapeHtml(prompts.user || '')}</pre>
        </div>
        <div class="ect-ai-card">
          <h4>Response Schema</h4>
          <pre class="ect-ai-code">${escapeHtml(JSON.stringify(preview.response_schema || {}, null, 2))}</pre>
        </div>
        <div class="ect-ai-card">
          <h4>请求报文预览</h4>
          <pre class="ect-ai-code">${escapeHtml(JSON.stringify(preview.request || {}, null, 2))}</pre>
        </div>
      </div>`;
  }

  function renderAiEvaluationResultPanel(modalState) {
    const panel = modalState && modalState.body && modalState.body.querySelector('[data-ai-eval-panel="result"]');
    if (!panel) return;
    if (modalState.resultHtml) {
      panel.innerHTML = modalState.resultHtml;
      return;
    }
    panel.innerHTML = '<div class="ect-ai-empty">正在等待大模型返回结构化结果...</div>';
  }

  function simplifyAiEvaluationPayload(payload) {
    return JSON.parse(JSON.stringify(payload || {}, (key, value) => {
      if ((key === 'base64' || key === 'data_base64') && typeof value === 'string' && value.length > 160) {
        return `${value.slice(0, 96)}...(${value.length} chars)`;
      }
      return value;
    }));
  }

  async function copyAiEvaluationPayload(payload) {
    const text = JSON.stringify(payload || {}, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }

  async function openAiEvaluationPayloadDetail(modalState) {
    if (!modalState.fullPayloadUrl) return;
    const shell = window.EvalCountryTable.openModal('', { title: '报文详情' });
    shell.modal.classList.add('ect-ai-detail-modal');
    const body = shell.modal.querySelector('.ect-modal-body');
    body.innerHTML = '<div class="ect-ai-empty">正在加载完整请求报文...</div>';
    try {
      const data = await fetchJSON(modalState.fullPayloadUrl);
      const payload = data.payload || data;
      body.innerHTML = `
        <div class="ect-ai-actions"><button type="button" class="ect-ai-btn primary" data-ai-copy-payload>一键复制</button></div>
        <pre class="ect-modal-json">${escapeHtml(JSON.stringify(simplifyAiEvaluationPayload(payload), null, 2))}</pre>`;
      const copyBtn = body.querySelector('[data-ai-copy-payload]');
      if (copyBtn) {
        copyBtn.addEventListener('click', async () => {
          await copyAiEvaluationPayload(payload);
          copyBtn.textContent = '已复制';
        });
      }
    } catch (err) {
      body.innerHTML = `<div class="ect-ai-empty">完整报文加载失败：${escapeHtml(aiEvaluationErrorMessage(err) || err)}</div>`;
    }
  }

  function setAiEvaluationModalResult(modalState, data) {
    if (!modalState || !modalState.body) return;
    if (data && data.progress) setAiEvaluationProgress(modalState, data.progress);
    modalState.done = true;
    stopAiEvaluationTimers(modalState);
    if (modalState.statusTitle) modalState.statusTitle.textContent = '评估完成';
    if (modalState.status) modalState.status.textContent = `总耗时 ${aiEvaluationElapsedSeconds(modalState)} 秒`;
    const detail = aiEvaluationResultDetail(data);
    modalState.evaluationDetail = detail;
    renderAiEvaluationCountryProgress(modalState);
    if (window.EvalCountryTable && typeof window.EvalCountryTable.render === 'function') {
      modalState.resultHtml = window.EvalCountryTable.render(detail);
    } else {
      modalState.resultHtml = `<pre class="audit-detail-pre">${escapeHtml(JSON.stringify(detail || {}, null, 2))}</pre>`;
    }
    renderAiEvaluationResultPanel(modalState);
    switchAiEvaluationTab(modalState, 'result');
  }

  function setAiEvaluationModalLoading(modalState) {
    if (!modalState || !modalState.body) return;
    if (modalState.statusTitle) modalState.statusTitle.textContent = '正在请求中';
    renderAiEvaluationResultPanel(modalState);
  }

  function setAiEvaluationModalFailure(modalState, reason) {
    if (!modalState || !modalState.body) return;
    modalState.done = true;
    stopAiEvaluationTimers(modalState);
    if (modalState.statusTitle) modalState.statusTitle.textContent = '评估失败';
    if (modalState.status) modalState.status.textContent = `总耗时 ${aiEvaluationElapsedSeconds(modalState)} 秒`;
    modalState.resultHtml = `<div class="ect-ai-empty"><strong>本次评估失败</strong><br>${escapeHtml(aiEvaluationFailureReason(reason))}</div>`;
    renderAiEvaluationResultPanel(modalState);
    switchAiEvaluationTab(modalState, 'result');
  }

  function renderAuditCell(it) {
    const result = String(it.ai_evaluation_result || '').trim() || '未评估';
    const remark = String(it.remark || '').trim() || '暂无备注';
    const resultClass = result.includes('不适合') || normalizeListingStatus(it.listing_status) === '下架'
      ? 'audit-result-bad'
      : result.includes('适合') ? 'audit-result-good' : 'audit-result-empty';

    return `<div class="audit-cell">
      <div class="audit-line">
        ${renderListingStatusBadge(it.listing_status)}
        <span class="audit-result ${resultClass}">${escapeHtml(result)}</span>
      </div>
      <div class="audit-score">AI评分: <span>${escapeHtml(formatAuditScore(it.ai_score))}</span></div>
      <div class="audit-remark" title="${escapeAttr(remark)}">${escapeHtml(remark)}</div>
      <button type="button" class="btn-mini audit-detail-btn" data-action="ai-detail" data-id="${it.id}">AI 评估详情</button>
    </div>`;
  }

  function showAuditDetail(itemId) {
    const item = state.items.find(i => Number(i.id) === Number(itemId));
    if (!item) return;
    if (window.EvalCountryTable && typeof window.EvalCountryTable.openModal === 'function') {
      window.EvalCountryTable.openModal(item.ai_evaluation_detail);
    }
  }

  function renderActionCell(it) {
    if (!window.PUSH_IS_ADMIN) return '';
    if (it.status === 'pushed') {
      const date = (it.pushed_at || '').slice(0, 10);
      return `<span class="pushed-text">✓ 已推送 ${date}</span>
              <div class="action-menu">
                <button class="btn-mini" data-action="open-modal" data-id="${it.id}">查看/重推</button>
                <button class="btn-mini" data-action="view-logs" data-id="${it.id}">历史</button>
                <button class="btn-mini" data-action="reset" data-id="${it.id}">重置</button>
              </div>`;
    }
    if (it.status === 'skipped') {
      return `<button class="btn-push btn-disabled" disabled title="已标记不推送">推送</button>
              <button class="btn-mini btn-unskip" data-action="unskip" data-id="${it.id}">恢复推送</button>`;
    }
    if (it.status === 'not_ready') {
      const missing = Object.entries(it.readiness)
        .filter(([, v]) => !v).map(([k]) => READINESS_LABELS[k] || k).join(' / ');
      return `<button class="btn-push" disabled title="缺少：${missing}">推送</button>
              <button class="btn-mini btn-skip" data-action="skip" data-id="${it.id}">标记不推送</button>`;
    }
    const label = it.status === 'failed' ? '重试推送' : '推送';
    const historyBtn = it.status === 'failed'
      ? `<button class="btn-mini" data-action="view-logs" data-id="${it.id}" style="margin-left:8px">历史</button>` : '';
    return `<button class="btn-push" data-action="open-modal" data-id="${it.id}">${label}</button>
            <button class="btn-mini btn-skip" data-action="skip" data-id="${it.id}">标记不推送</button>${historyBtn}`;
  }

  function renderRowLegacy(it) {
    const thumbUrl = safeMediaSrc(it.cover_url);
    const thumb = thumbUrl
      ? `<img class="thumb" src="${escapeAttr(thumbUrl)}" alt="">`
      : `<div class="thumb thumb-empty"></div>`;
    const durStr = (typeof it.duration_seconds === 'number') ? it.duration_seconds.toFixed(1) + 's' : '';
    const sizeStr = (it.file_size || 0).toLocaleString() + ' B';
    return `<tr data-id="${escapeAttr(it.id)}">
      <td class="push-thumb-cell">${thumb}</td>
      <td class="push-product-cell">
        <div class="product-name product-name-line">${escapeHtml(it.product_name || '')}</div>
        <div class="product-code-row">
          <span class="product-code">${escapeHtml(it.product_code || '')}</span>
        </div>
      </td>
      <td class="push-owner-cell"><span class="product-owner-name">${escapeHtml(it.product_owner_name || '-')}</span></td>
      <td class="push-item-cell">
        <div class="item-name">${escapeHtml(it.display_name || it.filename || '')}</div>
        <div class="item-meta">${escapeHtml(durStr ? `${durStr} · ${sizeStr}` : sizeStr)}</div>
      </td>
      <td class="push-lang-cell"><span class="lang-pill">${formatLanguageLabel(it.lang)}</span></td>
      <td class="ready-cell push-ready-cell">${renderReadinessText(it.readiness)}</td>
      <td class="push-status-cell">${renderStatusBadge(it.status)}</td>
      <td class="time push-time-cell">${escapeHtml((it.created_at || '').replace('T', ' ').slice(0, 16))}</td>
      ${window.PUSH_IS_ADMIN ? `<td class="push-action-cell">${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  function renderRow(it) {
    const thumbUrl = safeMediaSrc(it.cover_url);
    const thumb = thumbUrl
      ? `<div class="push-thumb-wrap" data-action="play-video" data-id="${it.id}">` +
        `<img class="thumb" src="${escapeAttr(thumbUrl)}" alt="">` +
        `<div class="push-play-overlay"><span class="push-play-btn-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"></path></svg></span></div>` +
        `</div>`
      : `<div class="thumb thumb-empty"></div>`;
    const mainImageUrl = `/medias/cover/${it.product_id}`;
    const mainImage = `<div class="main-image-wrap">` +
                      `<img class="thumb" src="${escapeAttr(mainImageUrl)}" alt="" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">` +
                      `<div class="thumb thumb-empty" style="display:none;"></div>` +
                      `</div>`;
    const durStr = (typeof it.duration_seconds === 'number') ? it.duration_seconds.toFixed(1) + 's' : '';
    const sizeStr = (it.file_size || 0).toLocaleString() + ' B';
    const productPageUrl = safeExternalHref(it.product_page_url);
    const copyProductNameBtn = it.product_name
      ? `<button type="button" class="product-copy-btn" data-copy-product-name="${escapeAttr(it.product_name)}" title="复制产品中文名" style="margin-left: 6px; display: inline-flex; vertical-align: middle; width: 22px; height: 22px; align-items: center; justify-content: center; flex-shrink: 0;">
           <svg class="icon-copy" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
             <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
             <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
           </svg>
         </button>`
      : '';
    const productNameHtml = productPageUrl
      ? `<div class="product-name-row" style="display: flex; align-items: center; justify-content: space-between; gap: 4px; min-width: 0;">
           <a class="product-name product-link product-name-line" href="${escapeAttr(productPageUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(it.product_name || '')}</a>
           ${copyProductNameBtn}
         </div>`
      : `<div class="product-name-row" style="display: flex; align-items: center; justify-content: space-between; gap: 4px; min-width: 0;">
           <div class="product-name product-name-line">${escapeHtml(it.product_name || '')}</div>
           ${copyProductNameBtn}
         </div>`;
    const productCode = it.product_code || '';
    const productBothContent = `${it.product_name || ''}\n${productCode}`;
    const productCodeHtml = productCode
      ? `<div class="product-code-row">
           <span class="product-code">${escapeHtml(productCode)}</span>
           <button type="button" class="product-copy-btn" data-copy-product-code="${escapeAttr(productCode)}" title="复制产品代码">
             <svg class="icon-copy" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
               <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
               <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
             </svg>
           </button>
           <a href="/medias/?q=${encodeURIComponent(productCode)}" target="_blank" rel="noopener noreferrer" class="product-search-btn" title="在素材管理中搜索">
             <svg class="icon-search" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
               <circle cx="11" cy="11" r="8"></circle>
               <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
             </svg>
           </a>
           <button type="button" class="product-copy-both-btn" data-copy-both="${escapeAttr(productBothContent)}" title="复制中文名和产品Code">复制两个值</button>
         </div>`
      : `<div class="product-code-row"><span class="product-code"></span></div>`;
    const productOwnerName = String(it.product_owner_name || '').trim();
    const mkId = (it.mk_id === null || it.mk_id === undefined || it.mk_id === '') ? '—' : String(it.mk_id);
    return `<tr data-id="${it.id}">
      <td class="push-thumb-cell">${thumb}</td>
      <td class="push-product-cell">
        ${productNameHtml}
        ${productCodeHtml}
      </td>
      <td class="push-main-image-cell">${mainImage}</td>
      <td class="push-owner-cell"><span class="product-owner-name">${escapeHtml(productOwnerName || '-')}</span></td>
      <td class="mk-id-cell">${escapeHtml(mkId)}</td>
      <td class="push-item-cell">
        <div class="item-name">${escapeHtml(it.display_name || it.filename || '')}</div>
        <div class="item-meta">
          ${escapeHtml(durStr ? `${durStr} · ${sizeStr}` : sizeStr)}
          ${it.task_id ? ` · <a href="/tasks/?task_id=${it.task_id}" class="push-task-badge" title="查看任务 #${it.task_id}" target="_blank" rel="noopener noreferrer" style="font-size:10px; color:var(--oc-accent); text-decoration:none; margin-left:4px;">任务#${it.task_id}</a>` : ''}
        </div>
      </td>
      <td class="push-lang-cell"><span class="lang-pill">${escapeHtml(formatLanguageLabel(it.lang))}</span></td>
      <td class="ready-cell push-ready-cell">${renderReadinessText(it.readiness)}</td>
      <td class="audit-cell-wrap">${renderAuditCell(it)}</td>
      <td class="push-status-cell">${renderStatusBadge(it.status)}</td>
      <td class="time push-time-cell">${escapeHtml((it.created_at || '').replace('T', ' ').slice(0, 16))}</td>
      ${window.PUSH_IS_ADMIN ? `<td class="push-action-cell">${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  async function load(options = {}) {
    if (options.syncUrl !== false) {
      syncUrlFromFilters(options.urlMode || 'push');
    }
    const tbody = document.getElementById('push-tbody');
    const colspan = window.PUSH_IS_ADMIN ? 11 : 10;
    tbody.innerHTML = `<tr><td colspan="${colspan}">加载中…</td></tr>`;
    try {
      const data = await fetchJSON('/pushes/api/items?' + buildQuery());
      state.total = data.total;
      state.pageSize = data.page_size || state.pageSize;
      state.items = data.items || [];
      if (!state.items.length) {
        tbody.innerHTML = `<tr><td colspan="${colspan}">无数据</td></tr>`;
      } else {
        tbody.innerHTML = state.items.map(renderRow).join('');
      }
      renderPagination();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="${colspan}">加载失败: ${escapeHtml(e.message)}</td></tr>`;
    }
  }

  function renderPagination() {
    const box = document.getElementById('push-pagination');
    const totalPages = Math.ceil(state.total / state.pageSize) || 1;
    const parts = [`共 ${state.total} 条`];
    for (let p = 1; p <= totalPages; p++) {
      if (p === state.page) parts.push(`<strong>${p}</strong>`);
      else parts.push(`<a href="${escapeAttr(buildPageHref(p))}" data-page="${p}">${p}</a>`);
    }
    box.innerHTML = parts.join(' ');
    box.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', ev => {
        ev.preventDefault();
        state.page = Number(ev.target.getAttribute('data-page'));
        load();
      });
    });
  }

  function bindFilters() {
    document.getElementById('btn-apply').addEventListener('click', () => {
      state.page = 1; load();
    });
    document.getElementById('f-sort').addEventListener('change', () => {
      state.page = 1; load();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      document.querySelectorAll('.push-toolbar input').forEach(i => (i.value = ''));
      document.getElementById('f-status').value = 'pending';
      document.getElementById('f-lang').value = '';
      document.getElementById('f-owner').value = '';
      document.getElementById('f-audit-result').value = '';
      document.getElementById('f-sort').value = 'created_at_desc';
      state.page = 1; load();
    });
    const refreshBtn = document.getElementById('btn-refresh-cache');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', async () => {
        if (!confirm('确认要刷新数据吗？这会清空当前页面加载的数据状态缓存，从零重新加载一轮推送数据。')) return;
        refreshBtn.disabled = true;
        const originalText = refreshBtn.textContent;
        refreshBtn.textContent = '刷新中…';
        try {
          await fetchJSON('/pushes/api/cache/clear', { method: 'POST' });
          state.page = 1;
          await load();
        } catch (e) {
          alert('刷新失败: ' + e.message);
        } finally {
          refreshBtn.disabled = false;
          refreshBtn.textContent = originalText;
        }
      });
    }
  }

  // ---------- 弹窗 · 推送胶囊 ----------

  function renderPayloadView(payload, previewCoverUrl) {
    const root = el('div');

    const kv = el('div', { class: 'pm-kv' });
    const pairs = [
      ['mode', payload.mode],
      ['product_name', payload.product_name],
      ['author', payload.author],
      ['push_admin', payload.push_admin],
      ['level', payload.level],
      ['roas', payload.roas],
      ['source', payload.source],
      ['platforms', JSON.stringify(payload.platforms || [])],
      ['selling_point', payload.selling_point || '(空)'],
    ];
    pairs.forEach(([k, v]) => {
      kv.appendChild(el('span', { class: 'k' }, k));
      kv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(v ?? ''))]));
    });
    kv.appendChild(el('span', { class: 'k' }, 'tags'));
    kv.appendChild(el('div', { class: 'v' }, [renderTagList(payload.tags)]));
    root.appendChild(kv);

    if (Array.isArray(payload.product_links) && payload.product_links.length) {
      const sub = el('div', { class: 'pm-sub' }, [
        el('div', { class: 'pm-sub-title' }, `product_links (${payload.product_links.length})`),
      ]);
      const ul = el('ul', { class: 'pm-list' });
      payload.product_links.forEach(link => ul.appendChild(el('li', {}, [el('code', {}, link)])));
      sub.appendChild(ul);
      root.appendChild(sub);
    }

    if (Array.isArray(payload.texts) && payload.texts.length) {
      const sub = el('div', { class: 'pm-sub' }, [
        el('div', { class: 'pm-sub-title' }, `texts (${payload.texts.length})`),
      ]);
      payload.texts.forEach((t, i) => {
        const tkv = el('div', { class: 'pm-kv', style: 'margin-top:4px' });
        ['title', 'message', 'description'].forEach(k => {
          tkv.appendChild(el('span', { class: 'k' }, `[${i}] ${k}`));
          tkv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(t[k] || ''))]));
        });
        sub.appendChild(tkv);
      });
      root.appendChild(sub);
    }

    if (Array.isArray(payload.videos) && payload.videos.length) {
      const sub = el('div', { class: 'pm-sub' }, [
        el('div', { class: 'pm-sub-title' }, `videos (${payload.videos.length})`),
      ]);
      payload.videos.forEach((v, i) => {
        const vkv = el('div', { class: 'pm-kv', style: 'margin-top:8px' });
        ['name', 'size', 'width', 'height'].forEach(k => {
          vkv.appendChild(el('span', { class: 'k' }, `[${i}] ${k}`));
          vkv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(v[k] ?? ''))]));
        });
        sub.appendChild(vkv);

        const preview = el('div', { class: 'pm-video-preview' });
        // 展示用封面优先走 previewCoverUrl（/medias/thumb/<id> 等已登录路由，
        // 依赖本地入库 thumbnail，可靠性高）。v.image_url 是发给下游的 /medias/obj URL，
        // 老素材本地未回填时会 404。
        const coverSrc = previewMediaSrc(previewCoverUrl || v.image_url || null);
        const videoSrc = previewMediaSrc(v.url);
        if (coverSrc) {
          preview.appendChild(el('img', { class: 'pm-thumb', src: coverSrc, alt: `cover-${i}` }));
        }
        if (videoSrc) {
          preview.appendChild(el('video', {
            class: 'pm-thumb', src: videoSrc, poster: coverSrc,
            controls: true, preload: 'metadata',
          }));
        }
        sub.appendChild(preview);
      });
      root.appendChild(sub);
    }

    return root;
  }

  function renderTagList(tags) {
    const list = el('div', { class: 'pm-tag-list' });
    (Array.isArray(tags) ? tags : []).forEach(tag => {
      const value = String(tag ?? '').trim();
      if (!value) return;
      list.appendChild(el('div', { class: 'pm-inline-copy-row pm-tag-row' }, [
        el('code', {}, value),
        createCopyButton(value, 'data-copy-payload-tag'),
      ]));
    });
    return list.childNodes.length ? list : el('code', {}, '[]');
  }

  function renderModalProductInfo(item) {
    const row = el('span', { class: 'v pm-inline-copy-row' });
    const productName = String(item.product_name || '').trim();
    const productCode = String(item.product_code || '').trim();
    if (productName) row.appendChild(document.createTextNode(productName));
    if (productCode) {
      if (productName) row.appendChild(document.createTextNode('  ·  '));
      row.appendChild(el('code', {}, productCode));
      row.appendChild(createCopyButton(productCode, 'data-copy-modal-product-code'));
    }
    if (!productName && !productCode) row.appendChild(document.createTextNode('-'));
    return row;
  }

  function renderLocalizedPane(texts, targetUrl, mkId) {
    const root = el('div');

    const target = el('div', { class: 'pm-kv' });
    target.appendChild(el('span', { class: 'k' }, 'mk_id'));
    target.appendChild(el('span', { class: 'v' }, [el('code', {}, mkId ? String(mkId) : '-')]));
    target.appendChild(el('span', { class: 'k' }, '推送地址'));
    target.appendChild(el('span', { class: 'v' }, [el('code', {}, targetUrl || '(未配置 base_url)')]));
    root.appendChild(target);

    if (!texts.length) {
      root.appendChild(el('p', { class: 'pm-empty' }, '当前暂无可推送文案（需要产品下有英语或其他启用语种，且标题/文案/描述齐全的 copywriting）'));
      return root;
    }

    texts.forEach((t, index) => {
      const card = el('div', { class: 'pm-sub', style: index > 0 ? 'margin-top:12px' : '' });
      const kv = el('div', { class: 'pm-kv' });
      [['语种', formatLanguageLabel(t.lang) || ''], ['标题', t.title || ''], ['文案', t.message || ''], ['描述', t.description || '']]
        .forEach(([k, v]) => {
          kv.appendChild(el('span', { class: 'k' }, k));
          kv.appendChild(el('span', { class: 'v' }, v));
        });
      card.appendChild(kv);
      root.appendChild(card);
    });
    return root;
  }

  function renderProductLinksPane(preview) {
    const root = el('div');
    const data = preview || {};
    const links = Array.isArray(data.links) ? data.links : [];
    const payloadLinks = data.payload && Array.isArray(data.payload.product_links)
      ? data.payload.product_links
      : [];

    const info = el('div', { class: 'pm-kv' });
    info.appendChild(el('span', { class: 'k' }, '推送地址'));
    info.appendChild(el('span', { class: 'v' }, [el('code', {}, data.target_url || '(未配置)')]));
    info.appendChild(el('span', { class: 'k' }, 'handle'));
    info.appendChild(el('span', { class: 'v' }, [el('code', {}, data.payload?.handle || '-')]));
    info.appendChild(el('span', { class: 'k' }, '链接数量'));
    info.appendChild(el('span', { class: 'v' }, String(payloadLinks.length || links.length || 0)));
    root.appendChild(info);

    if (data.error) {
      root.appendChild(el('p', { class: 'pm-empty pm-error' }, data.message || data.error));
      return root;
    }

    if (!links.length && !payloadLinks.length) {
      root.appendChild(el('p', { class: 'pm-empty' }, '当前暂无可推送链接'));
      return root;
    }

    const list = el('div', { class: 'pm-sub' }, [
      el('div', { class: 'pm-sub-title' }, '推送链接'),
    ]);
    if (links.length) {
      links.forEach(link => {
        const kv = el('div', { class: 'pm-kv', style: 'margin-top:8px' });
        kv.appendChild(el('span', { class: 'k' }, formatLanguageLabel(link.lang) || link.lang || '-'));
        kv.appendChild(el('span', { class: 'v' }, [el('code', {}, link.url || '')]));
        list.appendChild(kv);
      });
    } else {
      const ul = el('ul', { class: 'pm-list' });
      payloadLinks.forEach(link => ul.appendChild(el('li', {}, [el('code', {}, link)])));
      list.appendChild(ul);
    }
    root.appendChild(list);
    return root;
  }

  function qualityScoreMeta(status) {
    const value = String(status || '').toLowerCase();
    if (value === 'passed') return { text: '优质 (9.0)', cls: 'excellent', score: 9 };
    if (value === 'warning') return { text: '中等 (6.5)', cls: 'medium', score: 6.5 };
    if (value === 'failed') return { text: '质量差 (3.0)', cls: 'poor', score: 3 };
    if (value === 'error') return { text: '质量差 (2.0)', cls: 'poor', score: 2 };
    if (value === 'running') return { text: '评估中', cls: 'running', score: null };
    return { text: '待评估', cls: 'pending', score: null };
  }

  const QUALITY_SUMMARY_LINE_LIMIT = 120;

  function normalizeQualitySummary(result) {
    const data = result || {};
    if (data.summary) return String(data.summary).replace(/\s+/g, ' ').trim();
    if (Array.isArray(data.issues) && data.issues.length) {
      return data.issues.map(issue => String(issue)).join('；').replace(/\s+/g, ' ').trim();
    }
    const meta = qualityScoreMeta(data.status);
    if (meta.cls === 'running') return '评估中';
    if (meta.cls === 'pending') return '暂无检查结果';
    return '-';
  }

  function truncateQualitySummaryText(value, limit = QUALITY_SUMMARY_LINE_LIMIT) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text) return '暂无检查结果';
    if (text.length <= limit) return text;
    return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
  }

  function renderQualitySummaryRows(qualityCheck) {
    const data = qualityCheck || {};
    const root = el('div', { class: 'pm-quality-summary-rows' });
    [
      ['文案：', data.copy_result],
      ['封面：', data.cover_result],
      ['视频：', data.video_result],
    ].forEach(([label, result]) => {
      const fullText = normalizeQualitySummary(result);
      root.appendChild(el('div', { class: 'pm-quality-summary-row', title: `${label}${fullText}` }, [
        el('span', { class: 'pm-quality-summary-label' }, label),
        el('span', { class: 'pm-quality-summary-text' }, truncateQualitySummaryText(fullText)),
      ]));
    });
    return root;
  }

  function renderQualityResultCard(label, result) {
    const data = result || {};
    const meta = qualityScoreMeta(data.status);
    const issues = Array.isArray(data.issues) ? data.issues : [];
    const summary = data.summary || (meta.cls === 'pending' ? '暂无检查结果' : '-');
    const card = el('div', { class: `pm-quality-card is-${meta.cls}` });
    card.appendChild(el('div', { class: 'pm-quality-card-head' }, [
      el('span', { class: 'pm-quality-label' }, label),
      el('span', { class: `pm-quality-badge is-${meta.cls}` }, meta.text),
    ]));
    card.appendChild(el('p', { class: 'pm-quality-summary' }, summary));
    if (issues.length) {
      const ul = el('ul', { class: 'pm-quality-issues' });
      issues.slice(0, 3).forEach(issue => ul.appendChild(el('li', {}, String(issue))));
      card.appendChild(ul);
    }
    return card;
  }

  function renderQualityCheckPanel(qualityCheck, onRetry, busy = false) {
    const root = el('div', { class: 'pm-quality-panel' });
    const statusMeta = qualityScoreMeta(qualityCheck && qualityCheck.status);
    const head = el('div', { class: 'pm-quality-head' });
    head.appendChild(el('div', {}, [
      el('div', { class: 'pm-quality-title' }, '推送前质量检查'),
      el('div', { class: 'pm-quality-subtitle' }, renderQualitySummaryRows(qualityCheck)),
    ]));
    head.appendChild(el('span', { class: `pm-quality-badge is-${statusMeta.cls}` }, statusMeta.text));
    head.appendChild(el('button', {
      type: 'button',
      class: 'btn-mini pm-quality-retry',
      disabled: busy,
      onclick: onRetry,
    }, busy ? '评估中...' : '重新评估'));
    root.appendChild(head);
    root.appendChild(el('div', { class: 'pm-quality-grid' }, [
      renderQualityResultCard('文案', qualityCheck && qualityCheck.copy_result),
      renderQualityResultCard('封面图', qualityCheck && qualityCheck.cover_result),
      renderQualityResultCard('视频', qualityCheck && qualityCheck.video_result),
    ]));
    if (Array.isArray(qualityCheck?.failed_reasons) && qualityCheck.failed_reasons.length) {
      const reasons = el('ul', { class: 'pm-quality-reasons' });
      qualityCheck.failed_reasons.slice(0, 5).forEach(reason => {
        reasons.appendChild(el('li', {}, String(reason)));
      });
      root.appendChild(reasons);
    }
    return root;
  }

  function firstPayloadVideo(payload) {
    const videos = payload && Array.isArray(payload.videos) ? payload.videos : [];
    return videos.length ? videos[0] : null;
  }

  function renderQualityIssues(result) {
    const issues = result && Array.isArray(result.issues) ? result.issues : [];
    if (!issues.length) return null;
    const list = el('ul', { class: 'pm-quality-issues' });
    issues.slice(0, 4).forEach(issue => list.appendChild(el('li', {}, String(issue))));
    return list;
  }

  function renderQualityEvidence(result) {
    const evidence = result && Array.isArray(result.evidence) ? result.evidence : [];
    if (!evidence.length) return null;
    const root = el('div', { class: 'pm-quality-evidence' });
    root.appendChild(el('div', { class: 'pm-quality-evidence-title' }, '判断依据'));
    const list = el('ul', {});
    evidence.slice(0, 4).forEach(item => list.appendChild(el('li', {}, String(item))));
    root.appendChild(list);
    return root;
  }

  function renderQualityDetailBlock(label, result, previewNode) {
    const data = result || {};
    const meta = qualityScoreMeta(data.status);
    const block = el('section', { class: `pm-quality-detail-block is-${meta.cls}` });
    block.appendChild(el('div', { class: 'pm-quality-detail-head' }, [
      el('h5', {}, label),
      el('span', { class: `pm-quality-score is-${meta.cls}` }, meta.text),
    ]));
    if (previewNode) block.appendChild(previewNode);
    block.appendChild(el('p', { class: 'pm-quality-summary' }, data.summary || '暂无检查结果'));
    const issues = renderQualityIssues(data);
    if (issues) block.appendChild(issues);
    const evidence = renderQualityEvidence(data);
    if (evidence) block.appendChild(evidence);
    return block;
  }

  function renderQualityCopyPreview(localizedText) {
    const text = localizedText || {};
    const root = el('div', { class: 'pm-quality-copy-preview' });
    const rows = [
      ['语种', formatLanguageLabel(text.lang) || text.lang || '-'],
      ['标题', text.title || '-'],
      ['文案', text.message || '-'],
      ['描述', text.description || '-'],
    ];
    rows.forEach(([k, v]) => {
      root.appendChild(el('span', { class: 'k' }, k));
      root.appendChild(el('span', { class: 'v' }, v));
    });
    return root;
  }

  function renderQualityCoverPreview(payload, previewCoverUrl) {
    const video = firstPayloadVideo(payload);
    const coverSrc = previewMediaSrc(previewCoverUrl || (video && video.image_url) || '');
    const root = el('div', { class: 'pm-quality-cover-preview pm-quality-media-preview' });
    const frame = el('div', { class: 'pm-quality-media-frame' });
    if (coverSrc) {
      frame.appendChild(el('img', { class: 'pm-quality-cover-img', src: coverSrc, alt: '封面图' }));
    } else {
      frame.appendChild(el('p', { class: 'pm-empty' }, '暂无封面图预览'));
    }
    root.appendChild(frame);
    return root;
  }

  function renderQualityVideoPreview(payload, previewCoverUrl) {
    const video = firstPayloadVideo(payload);
    const root = el('div', { class: 'pm-quality-video-preview pm-quality-media-preview' });
    const frame = el('div', { class: 'pm-quality-media-frame' });
    const videoSrc = previewMediaSrc(video && video.url);
    const posterSrc = previewMediaSrc(previewCoverUrl || (video && video.image_url) || '');
    if (videoSrc) {
      frame.appendChild(el('video', {
        class: 'pm-quality-video',
        src: videoSrc,
        poster: posterSrc,
        controls: true,
        preload: 'metadata',
      }));
    } else {
      frame.appendChild(el('p', { class: 'pm-empty' }, '暂无视频预览'));
    }
    root.appendChild(frame);
    return root;
  }

  function renderQualitySidePanel(
    qualityCheck,
    onRetry,
    payload,
    localizedText,
    previewCoverUrl,
    busy = false,
  ) {
    const root = el('aside', { class: 'pm-quality-side-panel' });
    const statusMeta = qualityScoreMeta(qualityCheck && qualityCheck.status);
    const head = el('div', { class: 'pm-quality-side-head' });
    head.appendChild(el('div', {}, [
      el('div', { class: 'pm-quality-title' }, '推送前质量检查'),
      el('div', { class: 'pm-quality-subtitle' }, renderQualitySummaryRows(qualityCheck)),
    ]));
    head.appendChild(el('span', { class: `pm-quality-score is-${statusMeta.cls}` }, statusMeta.text));
    head.appendChild(el('button', {
      type: 'button',
      class: 'btn-mini pm-quality-retry',
      disabled: busy,
      onclick: onRetry,
    }, busy ? '评估中...' : '重新评估'));
    root.appendChild(head);
    root.appendChild(renderQualityDetailBlock(
      '文案',
      qualityCheck && qualityCheck.copy_result,
      renderQualityCopyPreview(localizedText),
    ));
    root.appendChild(el('div', { class: 'pm-quality-media-row' }, [
      renderQualityDetailBlock(
        '封面图',
        qualityCheck && qualityCheck.cover_result,
        renderQualityCoverPreview(payload, previewCoverUrl),
      ),
      renderQualityDetailBlock(
        '视频',
        qualityCheck && qualityCheck.video_result,
        renderQualityVideoPreview(payload, previewCoverUrl),
      ),
    ]));
    if (Array.isArray(qualityCheck?.failed_reasons) && qualityCheck.failed_reasons.length) {
      const reasons = el('ul', { class: 'pm-quality-reasons' });
      qualityCheck.failed_reasons.slice(0, 5).forEach(reason => {
        reasons.appendChild(el('li', {}, String(reason)));
      });
      root.appendChild(reasons);
    }
    return root;
  }

  function parseErrorBody(e) {
    let body = {};
    try { body = JSON.parse(e.body || '{}'); } catch (_) {}
    return body;
  }

  function describeError(e) {
    const body = parseErrorBody(e);
    const err = body.error || '';
    const detail = body.detail || body.message || body.response_body || e.body || e.message || '';
    if (err === 'not_ready') {
      const missing = (body.missing || []).map(k => (READINESS_LABELS[k] || k)).join(' / ');
      return `素材未就绪：缺少 ${missing || '未知项'}`;
    }
    if (err === 'link_not_adapted') return `产品链接未适配：${body.url || ''}（${detail || ''}）`;
    if (err === 'already_pushed') return '该素材已推送过';
    if (err === 'copywriting_invalid') return `文案不合规：${detail}`;
    if (err === 'push_target_not_configured') return '后端未配置 PUSH_TARGET_URL（去 /settings?tab=push）';
    if (err === 'push_localized_texts_base_url_missing') return '未配置 wedev Base URL（去 /settings?tab=push）';
    if (err === 'push_localized_texts_credentials_missing') return '未配置 wedev 的 Authorization 或 Cookie（去 /settings?tab=push 或用 tools/wedev_sync.py）';
    if (err === 'mk_id_missing') return '该产品缺少 mk_id，不能推送文案';
    if (err === 'localized_texts_empty') return '当前没有可推送文案';
    if (err === 'downstream_unreachable') return `下游不可达：${detail}`;
    if (err === 'downstream_error') {
      const preview = (body.response_body || '').slice(0, 200);
      return `下游返回 HTTP ${body.upstream_status}\n${preview}`;
    }
    return detail || e.message;
  }

  function reworkCheckMap(readinessPayload) {
    const map = {};
    (readinessPayload && readinessPayload.checks || []).forEach(check => {
      const key = String(check && check.key || '');
      if (key) map[key] = check;
    });
    return map;
  }

  function renderReworkEvidence(evidence) {
    const rows = Array.isArray(evidence) ? evidence.slice(0, 3) : [];
    if (!rows.length) return el('div', { class: 'pm-rework-empty' }, '暂无可预览内容');
    const wrap = el('div', { class: 'pm-rework-evidence' });
    rows.forEach(row => {
      const type = String(row && row.type || '');
      const box = el('div', { class: 'pm-rework-evidence-item' });
      const label = row && (row.label || row.filename || row.title || row.url) || '产出';
      box.appendChild(el('div', { class: 'pm-rework-evidence-title' }, String(label)));
      if (type === 'image' && row.url) {
        box.appendChild(el('img', { src: previewMediaSrc(row.url), alt: String(label) }));
      } else if (type === 'video' && row.url) {
        box.appendChild(el('video', { src: previewMediaSrc(row.url), controls: true, preload: 'metadata' }));
      } else if (type === 'link' && row.url) {
        box.appendChild(el('a', {
          href: safeExternalHref(row.url),
          target: '_blank',
          rel: 'noopener noreferrer',
        }, String(row.url)));
      } else if (Array.isArray(row.lines) && row.lines.length) {
        row.lines.slice(0, 3).forEach(line => {
          box.appendChild(el('div', { class: 'pm-rework-text-line' }, `${line.label || ''} ${line.value || ''}`));
        });
      } else {
        const text = [row.title, row.body, row.description, row.meta]
          .filter(Boolean)
          .join(' · ');
        box.appendChild(el('div', { class: 'pm-rework-text-line' }, text || '已生成产出'));
      }
      wrap.appendChild(box);
    });
    return wrap;
  }

  function openPushModal(itemId) {
    const item = state.items.find(i => i.id === itemId);
    if (!item) return;

    const overlay = el('div', { class: 'pm-overlay' });
    const modal = el('div', { class: 'pm-modal' });
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const header = el('div', { class: 'pm-header' });
    const pillRow = el('div', { class: 'pm-pills' });
    const pillDefs = [
      { mode: PUSH_MODAL_MODES.CONFIRM, label: '推送' },
      { mode: PUSH_MODAL_MODES.JSON, label: '推送JSON' },
      { mode: PUSH_MODAL_MODES.LOCALIZED_TEXT, label: '推送文案' },
      { mode: PUSH_MODAL_MODES.LOCALIZED_JSON, label: '推送文案JSON' },
      { mode: PUSH_MODAL_MODES.PRODUCT_LINKS, label: '推送链接' },
      { mode: PUSH_MODAL_MODES.PRODUCT_LINKS_JSON, label: '推送链接JSON' },
    ];
    const pills = pillDefs.map(({ mode, label }) => el('button', {
      type: 'button', class: 'pm-pill', dataset: { mode },
    }, label));
    pills.forEach(p => pillRow.appendChild(p));
    const btnClose = el('button', { type: 'button', class: 'pm-close', 'aria-label': '关闭' }, '×');
    header.appendChild(pillRow);
    header.appendChild(btnClose);
    modal.appendChild(header);

    const shell = el('div', { class: 'pm-shell' });
    const mainPanel = el('div', { class: 'pm-main' });
    const qualitySide = el('div', { class: 'pm-quality-side' });
    const body = el('div', { class: 'pm-body' });
    mainPanel.appendChild(body);
    shell.appendChild(mainPanel);
    shell.appendChild(qualitySide);
    modal.appendChild(shell);

    const infoCard = el('section', { class: 'pm-section' }, [el('h4', {}, '素材信息')]);
    const infoKV = el('div', { class: 'pm-kv' });
    const mkIdValue = el('span', { class: 'v' }, '-');
    const addKV = (k, v) => {
      infoKV.appendChild(el('span', { class: 'k' }, k));
      if (v instanceof Node) infoKV.appendChild(v);
      else infoKV.appendChild(el('span', { class: 'v' }, v));
    };
    addKV('产品', renderModalProductInfo(item));
    addKV('语种', formatLanguageLabel(item.lang));
    addKV('文件', item.display_name || item.filename || '-');
    addKV('item_id', String(item.id));
    addKV('mk_id', mkIdValue);
    addKV('状态', STATUS_LABELS[item.status]?.text || item.status);
    infoCard.appendChild(infoKV);
    body.appendChild(infoCard);

    const auditCard = el('section', { class: 'pm-section audit-modal-section' }, [el('h4', {}, 'AI评估信息')]);
    const auditKV = el('div', { class: 'pm-kv' });
    const productId = Number(item.product_id || 0);
    const aiReevaluateBtn = el('button', {
      type: 'button',
      class: 'btn-mini pm-ai-reevaluate',
      'data-action': 'ai-reevaluate',
      title: '复用素材管理 AI评估，重新评估该产品',
      disabled: !productId,
    }, 'AI重评');
    const auditDetailLabel = el('span', { class: 'k pm-audit-detail-key' }, [
      el('span', {}, 'AI评估详情'),
      aiReevaluateBtn,
    ]);
    const addAuditKV = (k, v) => {
      auditKV.appendChild(k instanceof Node ? k : el('span', { class: 'k' }, k));
      if (v instanceof Node) auditKV.appendChild(v);
      else auditKV.appendChild(el('span', { class: 'v' }, v));
    };
    function updateAuditPanel(sourceItem = item) {
      clear(auditKV);
      addAuditKV('上架', el('span', { class: 'v' }, [createListingStatusBadge(sourceItem.listing_status)]));
      addAuditKV('AI评分', formatAuditScore(sourceItem.ai_score));
      addAuditKV('AI评估结果', sourceItem.ai_evaluation_result || '未评估');
      addAuditKV('备注说明', sourceItem.remark || '暂无备注');
      addAuditKV(auditDetailLabel, renderAuditDetailNode(sourceItem.ai_evaluation_detail, { primaryLang: item.lang }));
    }
    updateAuditPanel(item);
    auditCard.appendChild(auditKV);
    body.appendChild(auditCard);

    const contentCard = el('section', { class: 'pm-section' }, [el('h4', {}, '推送内容')]);
    const loadingTip = el('p', { class: 'pm-empty' }, '加载中…');
    const manualLinkConfirm = el('div', { class: 'pm-link-confirm', hidden: true });
    const manualLinkConfirmText = el('div', { class: 'pm-link-confirm-text' });
    const manualLinkConfirmBtn = el('button', {
      type: 'button',
      class: 'btn-mini btn-manual-link-confirm',
    }, '人工确认链接正常');
    manualLinkConfirm.appendChild(manualLinkConfirmText);
    manualLinkConfirm.appendChild(manualLinkConfirmBtn);
    const paneConfirm = el('div', { class: 'pm-pane' });
    const paneJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneLocalized = el('div', { class: 'pm-pane', hidden: true });
    const paneLocalizedJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneProductLinksJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneProductLinks = el('div', { class: 'pm-pane', hidden: true });
    contentCard.appendChild(loadingTip);
    contentCard.appendChild(manualLinkConfirm);
    contentCard.appendChild(paneConfirm);
    contentCard.appendChild(paneJson);
    contentCard.appendChild(paneLocalized);
    contentCard.appendChild(paneLocalizedJson);
    contentCard.appendChild(paneProductLinksJson);
    contentCard.appendChild(paneProductLinks);
    body.appendChild(contentCard);

    const respWrap = el('section', { class: 'pm-section pm-response', hidden: true });
    const respTitle = el('h4', {}, '推送响应');
    const respPre = el('pre', { class: 'pm-json' });
    const respMkIdTip = el('div', { class: 'pm-mk-id-tip', hidden: true });
    respWrap.appendChild(respTitle);
    respWrap.appendChild(respPre);
    respWrap.appendChild(respMkIdTip);
    mainPanel.appendChild(respWrap);

    const footer = el('div', { class: 'pm-footer' });
    const btnRework = el('button', {
      type: 'button',
      class: 'btn-push btn-rework',
      disabled: !item.task_id,
      title: item.task_id ? '' : '这条素材没有关联任务，不能打回重做',
    }, '打回重做');
    const btnRefresh = el('button', {
      type: 'button',
      class: 'btn-push btn-modal-refresh-cache',
    }, '刷新数据');
    const btnPush = el('button', {
      type: 'button',
      class: 'btn-push btn-modal-material-push',
      disabled: true,
    }, '推送');
    footer.appendChild(btnRework);
    footer.appendChild(btnRefresh);
    footer.appendChild(btnPush);
    mainPanel.appendChild(footer);

    let activeMode = PUSH_MODAL_MODES.CONFIRM;
    let payloadData = null;
    let mkId = null;
    let localizedTexts = [];
    let localizedText = null;
    let localizedTargetUrl = '';
    let productLinksPreview = null;
    let previewCoverUrl = null;
    let qualityCheck = item.quality_check || null;
    let materialPushed = item.status === 'pushed';
    let localizedPushed = false;
    let productLinksPushed = false;
    let anyPushSucceeded = false;
    let payloadLoadFailed = false;
    let manualLinkConfirmed = false;

    function isLocalizedMode(m = activeMode) {
      return m === PUSH_MODAL_MODES.LOCALIZED_TEXT || m === PUSH_MODAL_MODES.LOCALIZED_JSON;
    }

    function isProductLinksMode(m = activeMode) {
      return m === PUSH_MODAL_MODES.PRODUCT_LINKS || m === PUSH_MODAL_MODES.PRODUCT_LINKS_JSON;
    }

    function isAuditHiddenMode(m) {
      return m === PUSH_MODAL_MODES.LOCALIZED_TEXT || m === PUSH_MODAL_MODES.LOCALIZED_JSON || m === PUSH_MODAL_MODES.PRODUCT_LINKS || m === PUSH_MODAL_MODES.PRODUCT_LINKS_JSON;
    }

    function syncPushButton() {
      if (!payloadData) {
        btnPush.disabled = true;
        btnPush.textContent = payloadLoadFailed ? '载荷加载失败' : '加载中…';
        return;
      }
      if (isProductLinksMode()) {
        const linkPayload = productLinksPreview && productLinksPreview.payload;
        const linkCount = linkPayload && Array.isArray(linkPayload.product_links)
          ? linkPayload.product_links.length
          : 0;
        const noTarget = !productLinksPreview || !productLinksPreview.target_url;
        const noLinks = !linkCount;
        const readyLabel = activeMode === PUSH_MODAL_MODES.PRODUCT_LINKS_JSON
          ? '推送'
          : '推送链接';
        btnPush.disabled = productLinksPushed || !payloadData || noTarget || noLinks;
        btnPush.textContent = productLinksPushed
          ? '链接已推送'
          : noTarget
            ? '未配链接接口'
            : noLinks ? '无可推送链接' : readyLabel;
        return;
      }
      if (isLocalizedMode()) {
        const noTarget = !mkId || !localizedTargetUrl;
        const noTexts = !localizedTexts.length;
        btnPush.disabled = localizedPushed || !payloadData || noTarget || noTexts;
        btnPush.textContent = localizedPushed
          ? '文案已推送'
          : noTarget
            ? (!mkId ? '缺少 mk_id' : '未配 base_url')
            : noTexts ? '无可推送文案' : '推送文案';
        return;
      }
      btnPush.disabled = materialPushed || !payloadData;
      btnPush.textContent = materialPushed ? '素材已推送' : '推送素材';
    }

    function setMode(mode) {
      activeMode = mode;
      pills.forEach(p => p.classList.toggle('active', p.dataset.mode === mode));
      paneConfirm.hidden = mode !== PUSH_MODAL_MODES.CONFIRM;
      paneJson.hidden = mode !== PUSH_MODAL_MODES.JSON;
      paneLocalized.hidden = mode !== PUSH_MODAL_MODES.LOCALIZED_TEXT;
      paneLocalizedJson.hidden = mode !== PUSH_MODAL_MODES.LOCALIZED_JSON;
      paneProductLinksJson.hidden = mode !== PUSH_MODAL_MODES.PRODUCT_LINKS_JSON;
      paneProductLinks.hidden = mode !== PUSH_MODAL_MODES.PRODUCT_LINKS;
      auditCard.hidden = isAuditHiddenMode(mode);
      syncPushButton();
    }

    pills.forEach(p => p.addEventListener('click', () => setMode(p.dataset.mode)));

    function setQualityPanel(result, busy = false) {
      qualityCheck = result || null;
      clear(qualitySide);
      qualitySide.appendChild(renderQualitySidePanel(
        qualityCheck,
        retryQualityCheck,
        payloadData,
        localizedText,
        previewCoverUrl,
        busy,
      ));
    }
    setQualityPanel(qualityCheck);

    async function retryQualityCheck() {
      setQualityPanel(qualityCheck, true);
      try {
        const result = await fetchJSON(`/pushes/api/items/${itemId}/quality-check/retry`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        setQualityPanel(result, false);
      } catch (err) {
        const message = describeError(err);
        setQualityPanel({
          status: 'error',
          summary: message,
          copy_result: qualityCheck && qualityCheck.copy_result,
          cover_result: qualityCheck && qualityCheck.cover_result,
          video_result: qualityCheck && qualityCheck.video_result,
          failed_reasons: [message],
        }, false);
      }
    }

    function applyAiEvaluationProduct(product) {
      if (!product || typeof product !== 'object') return;
      const updates = {
        ai_score: product.ai_score !== undefined ? product.ai_score : item.ai_score,
        ai_evaluation_result: product.ai_evaluation_result !== undefined ? product.ai_evaluation_result : item.ai_evaluation_result,
        ai_evaluation_detail: product.ai_evaluation_detail !== undefined ? product.ai_evaluation_detail : item.ai_evaluation_detail,
        listing_status: product.listing_status !== undefined ? product.listing_status : item.listing_status,
        remark: product.remark !== undefined ? product.remark : item.remark,
      };
      Object.assign(item, updates);
      state.items = state.items.map((row) => (
        Number(row.id) === Number(itemId) ? { ...row, ...updates } : row
      ));
      updateAuditPanel(item);
    }

    async function retryMaterialAiEvaluation() {
      if (!productId) return;
      const originalText = aiReevaluateBtn.textContent;
      const modalState = openAiEvaluationRequestModal({
        id: productId,
        name: item.product_name,
        product_code: item.product_code,
      });
      loadAiEvaluationRequestPreview(modalState, productId);
      const controller = window.AbortController ? new AbortController() : null;
      aiReevaluateBtn.disabled = true;
      aiReevaluateBtn.textContent = '评估中...';
      const timeout = window.setTimeout(() => {
        if (controller) controller.abort();
      }, AI_EVALUATION_TIMEOUT_MS);
      try {
        const data = await fetchJSON(`/medias/api/products/${productId}/evaluate`, {
          method: 'POST',
          signal: controller ? controller.signal : undefined,
        });
        let finalData = data;
        if (data && data.progress) {
          setAiEvaluationProgress(modalState, data.progress);
        }
        if (data && data.async && data.run_id) {
          finalData = await pollAiEvaluationStatus(modalState, productId, data.run_id);
        }
        let freshProduct = null;
        try {
          const fresh = await fetchJSON(`/medias/api/products/${productId}`);
          freshProduct = fresh && fresh.product;
        } catch (_) {
          freshProduct = null;
        }
        setAiEvaluationModalResult(modalState, freshProduct || finalData.result || finalData);
        applyAiEvaluationProduct(freshProduct || finalData.result || finalData);
        aiReevaluateBtn.textContent = '已完成';
        load({ syncUrl: false }).catch(() => {});
        window.setTimeout(() => {
          aiReevaluateBtn.textContent = originalText;
          aiReevaluateBtn.disabled = false;
        }, 1200);
      } catch (err) {
        if (!modalState.done) {
          setAiEvaluationModalFailure(modalState, aiEvaluationErrorMessage(err));
        }
        aiReevaluateBtn.textContent = originalText;
        aiReevaluateBtn.disabled = false;
      } finally {
        window.clearTimeout(timeout);
      }
    }

    aiReevaluateBtn.addEventListener('click', (event) => {
      event.preventDefault();
      retryMaterialAiEvaluation();
    });

    function showResponse(obj, isError, title) {
      respWrap.hidden = false;
      respTitle.textContent = title || '推送响应';
      respPre.textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
      respPre.classList.toggle('pm-json-error', !!isError);
      respMkIdTip.hidden = true;
      respMkIdTip.textContent = '';
    }

    function showMkIdMatch(match) {
      if (!match) return;
      respMkIdTip.hidden = false;
      if (match.status === 'ok' && match.mk_id) {
        respMkIdTip.textContent = `配对 mk_id : ${match.mk_id}`;
      } else if (match.status === 'credentials_expired') {
        respMkIdTip.textContent = '配对 mk_id 失败：wedev 登录凭据已失效，请在本机跑 tools/wedev_sync.py 重新同步';
      } else {
        respMkIdTip.textContent = '配对 mk_id 失败，请检查，当前无法完成文案推送，缺失 mk_id';
      }
    }

    function close() {
      if (!overlay.parentNode) return;
      overlay.parentNode.removeChild(overlay);
      document.removeEventListener('keydown', onEsc);
      if (anyPushSucceeded) load();
    }

    function renderReworkIssueOption(issue, checkMap) {
      const check = checkMap[issue.taskKey] || null;
      const ok = check ? !!check.ok : !!(item.readiness && item.readiness[issue.key]);
      const reason = check && check.reason ? String(check.reason) : '';
      const option = el('label', { class: 'pm-rework-option' });
      const input = el('input', {
        type: 'checkbox',
        value: issue.key,
        checked: !ok,
      });
      const content = el('div', { class: 'pm-rework-option-body' });
      content.appendChild(el('div', { class: 'pm-rework-option-head' }, [
        el('span', { class: 'pm-rework-label' }, issue.label),
        el('span', { class: ok ? 'pm-rework-state-ok' : 'pm-rework-state-bad' }, ok ? '当前正常' : '当前有问题'),
      ]));
      if (reason) {
        content.appendChild(el('div', { class: 'pm-rework-reason' }, reason));
      }
      content.appendChild(renderReworkEvidence(check && check.evidence));
      option.appendChild(input);
      option.appendChild(content);
      return option;
    }

    async function openReworkModal() {
      if (!item.task_id) {
        showResponse('这条素材没有关联任务，不能打回重做。', true, '打回失败');
        return;
      }

      const reworkOverlay = el('div', { class: 'pm-rework-overlay' });
      const dialog = el('div', { class: 'pm-rework-dialog' });
      reworkOverlay.appendChild(dialog);
      overlay.appendChild(reworkOverlay);

      const header = el('div', { class: 'pm-rework-header' }, [
        el('div', {}, [
          el('h3', {}, '打回重做'),
          el('p', {}, '勾选需要负责人继续处理的产出项，并填写打回说明。'),
        ]),
        el('button', { type: 'button', class: 'pm-close', 'aria-label': '关闭' }, '×'),
      ]);
      const list = el('div', { class: 'pm-rework-list' }, [
        el('div', { class: 'pm-empty' }, '加载任务输出中…'),
      ]);
      const reason = el('textarea', {
        class: 'pm-rework-textarea',
        rows: '4',
        placeholder: '填写打回说明，例如：视频字幕错位，英文文案格式不符合三段要求。支持直接截图粘贴图片。',
      });
      const pastedImagesContainer = el('div', { class: 'pm-rework-pasted-images' });
      const status = el('div', { class: 'pm-rework-status', hidden: true });
      const actions = el('div', { class: 'pm-rework-actions' });
      const cancel = el('button', { type: 'button', class: 'btn-mini' }, '取消');
      const submit = el('button', { type: 'button', class: 'btn-push btn-rework' }, '确认打回');
      actions.appendChild(cancel);
      actions.appendChild(submit);
      dialog.appendChild(header);
      dialog.appendChild(list);
      dialog.appendChild(el('div', { class: 'pm-rework-note' }, '打回后，这条推送记录会自动变为未就绪；勾选项会在任务详情中显示为管理员已拒绝。'));
      dialog.appendChild(reason);
      dialog.appendChild(pastedImagesContainer);
      dialog.appendChild(status);
      dialog.appendChild(actions);

      const uploadedImageUrls = [];

      reason.addEventListener('paste', async (e) => {
        const items = (e.clipboardData || e.originalEvent.clipboardData).items;
        for (let i = 0; i < items.length; i++) {
          const item = items[i];
          if (item.type.indexOf('image') !== -1) {
            const file = item.getAsFile();
            if (!file) continue;

            e.preventDefault();

            const previewId = 'pasted-img-' + Date.now() + '-' + Math.random().toString(36).slice(2, 9);
            const previewEl = el('div', { class: 'pm-rework-pasted-image-card', id: previewId }, [
              el('div', { class: 'pm-rework-pasted-image-loading' }, '正在上传...'),
            ]);
            pastedImagesContainer.appendChild(previewEl);

            try {
              const formData = new FormData();
              formData.append('file', file);

              const uploadResp = await fetchJSON(`/pushes/api/items/${itemId}/upload-rework-screenshot`, {
                method: 'POST',
                body: formData,
              });

              if (uploadResp && uploadResp.url) {
                const imageUrl = uploadResp.url;
                uploadedImageUrls.push(imageUrl);

                clear(previewEl);
                previewEl.appendChild(el('img', { src: previewMediaSrc(imageUrl), class: 'pm-rework-pasted-image-img' }));
                const delBtn = el('button', { type: 'button', class: 'pm-rework-pasted-image-del' }, '×');
                delBtn.addEventListener('click', (ev) => {
                  ev.stopPropagation();
                  const idx = uploadedImageUrls.indexOf(imageUrl);
                  if (idx !== -1) uploadedImageUrls.splice(idx, 1);
                  previewEl.remove();
                });
                previewEl.appendChild(delBtn);
              } else {
                throw new Error('upload failed');
              }
            } catch (err) {
              console.error(err);
              clear(previewEl);
              previewEl.appendChild(el('span', { class: 'pm-rework-pasted-image-error' }, '上传失败'));
              window.setTimeout(() => {
                previewEl.remove();
              }, 3000);
            }
          }
        }
      });

      const closeRework = () => {
        if (reworkOverlay.parentNode) reworkOverlay.parentNode.removeChild(reworkOverlay);
      };
      header.querySelector('.pm-close').addEventListener('click', closeRework);
      cancel.addEventListener('click', closeRework);
      reworkOverlay.addEventListener('click', event => {
        if (event.target === reworkOverlay) closeRework();
      });

      let readinessPayload = null;
      try {
        readinessPayload = await fetchJSON(`/tasks/api/child/${item.task_id}/readiness`);
      } catch (err) {
        clear(list);
        list.appendChild(el('div', { class: 'pm-error' }, `任务输出加载失败：${describeError(err)}`));
      }
      if (readinessPayload) {
        const checkMap = reworkCheckMap(readinessPayload);
        clear(list);
        REWORK_ISSUES.forEach(issue => {
          list.appendChild(renderReworkIssueOption(issue, checkMap));
        });
      }

      submit.addEventListener('click', async () => {
        const selected = Array.from(list.querySelectorAll('input[type="checkbox"]:checked'))
          .map(input => input.value);
        const reasonText = reason.value.trim();
        status.hidden = false;
        status.className = 'pm-rework-status';
        if (!selected.length) {
          status.textContent = '请至少勾选一个需要打回的产出项。';
          status.classList.add('pm-rework-status--error');
          return;
        }
        if (reasonText.length < 10) {
          status.textContent = '打回说明至少 10 个字。';
          status.classList.add('pm-rework-status--error');
          return;
        }
        submit.disabled = true;
        cancel.disabled = true;
        status.textContent = '正在打回…';
        try {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/reject-to-task`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ issue_keys: selected, reason: reasonText, image_urls: uploadedImageUrls }),
          });
          status.textContent = '已打回任务负责人继续处理。';
          status.classList.add('pm-rework-status--ok');
          showResponse(body, false, '打回重做');
          anyPushSucceeded = true;
          window.setTimeout(() => {
            closeRework();
            close();
          }, 350);
        } catch (err) {
          submit.disabled = false;
          cancel.disabled = false;
          status.textContent = describeError(err);
          status.classList.add('pm-rework-status--error');
        }
      });
    }

    function onEsc(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onEsc);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    btnClose.addEventListener('click', close);
    btnRework.addEventListener('click', openReworkModal);
    btnRefresh.addEventListener('click', async () => {
      btnRefresh.disabled = true;
      const originalText = btnRefresh.textContent;
      btnRefresh.textContent = '刷新中…';
      try {
        await fetchJSON(`/pushes/api/items/${itemId}/refresh-cache`, { method: 'POST' });
        manualLinkConfirmed = false;
        await loadPayload();
      } catch (err) {
        alert('刷新失败: ' + (err.message || err));
      } finally {
        btnRefresh.disabled = false;
        btnRefresh.textContent = originalText;
      }
    });

    function showManualLinkConfirm(err) {
      const body = parseErrorBody(err);
      const url = body.url || '';
      const detail = body.detail || body.message || '';
      manualLinkConfirmText.textContent = `如果你已经自己打开确认链接正常，可以跳过本次自动探活继续。${url ? `链接：${url}` : ''}${detail ? `（${detail}）` : ''}`;
      manualLinkConfirm.hidden = false;
    }

    function applyPayloadData(data) {
      payloadData = data.payload;
      mkId = data.mk_id || null;
      localizedText = data.localized_text || null;
      previewCoverUrl = data.preview_cover_url || null;
      localizedTargetUrl = data.localized_push_target_url || '';
      localizedTexts = (data.localized_texts_request && data.localized_texts_request.texts) || [];
      productLinksPreview = data.product_links_push || null;
      setQualityPanel(data.quality_check || null);

      mkIdValue.textContent = mkId ? String(mkId) : '-';

      clear(paneConfirm);
      // 顶部显式打印推送地址
      const pushUrlRow = el('div', { class: 'pm-kv', style: 'margin-bottom:12px' });
      pushUrlRow.appendChild(el('span', { class: 'k' }, '推送地址'));
      pushUrlRow.appendChild(el('span', { class: 'v' }, [
        el('code', {}, data.push_url || '(未配置)'),
      ]));
      paneConfirm.appendChild(pushUrlRow);
      if (manualLinkConfirmed) {
        const confirmRow = el('div', { class: 'pm-kv pm-manual-link-row', style: 'margin-bottom:12px' });
        confirmRow.appendChild(el('span', { class: 'k' }, '链接确认'));
        confirmRow.appendChild(el('span', { class: 'v' }, '已人工确认链接正常，本次跳过自动探活'));
        paneConfirm.appendChild(confirmRow);
      }
      paneConfirm.appendChild(renderPayloadView(payloadData, data.preview_cover_url || null));
      paneJson.textContent = JSON.stringify(payloadData, null, 2);

      clear(paneLocalized);
      paneLocalized.appendChild(renderLocalizedPane(localizedTexts, localizedTargetUrl, mkId));
      paneLocalizedJson.textContent = JSON.stringify({
        mk_id: mkId,
        target_url: localizedTargetUrl,
        texts: localizedTexts,
      }, null, 2);

      paneProductLinksJson.textContent = JSON.stringify(
        productLinksPreview && productLinksPreview.payload
          ? productLinksPreview.payload
          : (productLinksPreview || {}),
        null,
        2,
      );
      clear(paneProductLinks);
      paneProductLinks.appendChild(renderProductLinksPane(productLinksPreview));

      loadingTip.hidden = true;
      manualLinkConfirm.hidden = true;
      setMode(activeMode);
    }

    async function loadPayload() {
      payloadLoadFailed = false;
      payloadData = null;
      loadingTip.hidden = false;
      loadingTip.textContent = manualLinkConfirmed ? '已人工确认，正在重新加载载荷…' : '加载中…';
      loadingTip.classList.remove('pm-error');
      manualLinkConfirm.hidden = true;
      syncPushButton();
      try {
        const data = await fetchJSON(`/pushes/api/items/${itemId}/payload${manualLinkConfirmed ? '?manual_link_confirmed=1' : ''}`);
        applyPayloadData(data);
      } catch (err) {
        payloadLoadFailed = true;
        loadingTip.textContent = `载荷加载失败：${describeError(err)}`;
        loadingTip.classList.add('pm-error');
        if (parseErrorBody(err).error === 'link_not_adapted') {
          showManualLinkConfirm(err);
        }
        syncPushButton();
      }
    }

    async function retryPayloadWithManualLinkConfirmation() {
      manualLinkConfirmed = true;
      await loadPayload();
    }
    manualLinkConfirmBtn.addEventListener('click', retryPayloadWithManualLinkConfirmation);
    loadPayload();

    btnPush.addEventListener('click', async () => {
      if (!payloadData) return;
      const reworkDisabledBeforePush = btnRework.disabled;
      btnPush.disabled = true;
      btnRework.disabled = true;
      btnPush.textContent = '推送中…';
      try {
        if (isProductLinksMode()) {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/product-links-push`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });
          showResponse(body, !body.ok, body.ok ? '推送链接响应' : '推送链接失败');
          productLinksPushed = !!body.ok;
        } else if (isLocalizedMode()) {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/push-localized-texts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });
          showResponse(body, false, '文案推送响应');
          localizedPushed = true;
          anyPushSucceeded = true;
        } else {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/push`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ manual_link_confirmed: manualLinkConfirmed }),
          });
          showResponse(body, false, '素材推送响应');
          showMkIdMatch(body.mk_id_match);
          materialPushed = true;
          anyPushSucceeded = true;
          // mk_id 匹配成功 → 同步刷新顶部信息 + 文案 pane + JSON 预览 + 推送按钮状态
          if (body.mk_id_match && body.mk_id_match.mk_id) {
            mkId = body.mk_id_match.mk_id;
            mkIdValue.textContent = String(mkId);
            localizedTargetUrl = body.mk_id_match.localized_push_target_url || '';
            clear(paneLocalized);
            paneLocalized.appendChild(
              renderLocalizedPane(localizedTexts, localizedTargetUrl, mkId),
            );
            paneLocalizedJson.textContent = JSON.stringify({
              mk_id: mkId,
              target_url: localizedTargetUrl,
              texts: localizedTexts,
            }, null, 2);
            syncPushButton();
          }
        }
      } catch (err) {
        showResponse(describeError(err), true,
          isProductLinksMode()
            ? '推送链接失败'
            : isLocalizedMode() ? '文案推送失败' : '素材推送失败');
      } finally {
        btnRework.disabled = reworkDisabledBeforePush;
        syncPushButton();
      }
    });
  }

  // ---------- 历史抽屉 & 重置 ----------

  async function resetPush(itemId) {
    if (!confirm('确认重置这条素材的推送状态？之前的历史记录会保留。')) return;
    await fetchJSON(`/pushes/api/items/${itemId}/reset`, { method: 'POST' });
    await load();
  }

  async function skipPush(itemId) {
    await fetchJSON(`/pushes/api/items/${itemId}/skip`, { method: 'POST' });
    await load();
  }

  async function unskipPush(itemId) {
    await fetchJSON(`/pushes/api/items/${itemId}/unskip`, { method: 'POST' });
    await load();
  }

  async function viewLogs(itemId) {
    const drawer = document.getElementById('push-log-drawer');
    const content = document.getElementById('drawer-content');
    content.textContent = '加载中…';
    drawer.hidden = false;
    try {
      const data = await fetchJSON(`/pushes/api/items/${itemId}/logs`);
      if (!data.logs.length) {
        content.innerHTML = '<p>暂无记录</p>';
      } else {
        content.innerHTML = data.logs.map(l => `
          <div class="log-row">
            <div><strong>${l.status === 'success' ? '✓ 成功' : '✗ 失败'}</strong>
                 <span class="time">${escapeHtml(l.created_at)}</span></div>
            ${l.error_message ? `<div class="err">${escapeHtml(l.error_message)}</div>` : ''}
            ${l.response_body ? `<pre>${escapeHtml(String(l.response_body || '').slice(0, 500))}</pre>` : ''}
          </div>
        `).join('');
      }
    } catch (e) {
      content.textContent = '加载失败: ' + e.message;
    }
  }

  // ---------- 绑定 ----------

  document.addEventListener('click', async ev => {
    const copyBtn = ev.target.closest(
      'button[data-copy-product-code], button[data-copy-modal-product-code], button[data-copy-payload-tag], button[data-copy-both], button[data-copy-product-name]',
    );
    if (!copyBtn) return;
    try {
      await copyText(
        copyBtn.getAttribute('data-copy-product-code')
          || copyBtn.getAttribute('data-copy-modal-product-code')
          || copyBtn.getAttribute('data-copy-payload-tag')
          || copyBtn.getAttribute('data-copy-both')
          || copyBtn.getAttribute('data-copy-product-name')
          || '',
      );
      flashCopyButton(copyBtn, '已复制');
    } catch (err) {
      flashCopyButton(copyBtn, '复制失败');
    }
  });

  function playVideoModal(itemId) {
    const item = state.items.find(i => Number(i.id) === Number(itemId));
    if (!item || !item.object_key) {
      alert('视频未就绪或未找到');
      return;
    }

    const encoded = item.object_key.split('/').map(encodeURIComponent).join('/');
    const videoUrl = '/medias/obj/' + encoded;

    const overlay = document.createElement('div');
    overlay.className = 'push-video-modal-overlay';
    overlay.setAttribute('role', 'presentation');

    overlay.innerHTML = `
      <div class="push-video-modal-container">
        <button type="button" class="push-video-modal-close" aria-label="关闭">&times;</button>
        <div class="push-video-modal-content">
          <video class="push-video-player" src="${escapeAttr(videoUrl)}" controls autoplay playsinline></video>
        </div>
      </div>
    `;

    const closeBtn = overlay.querySelector('.push-video-modal-close');
    const videoEl = overlay.querySelector('.push-video-player');

    function close() {
      if (videoEl) {
        videoEl.pause();
        videoEl.src = '';
        videoEl.load();
      }
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    }

    function onKey(e) {
      if (e.key === 'Escape') close();
    }

    document.addEventListener('keydown', onKey);
    overlay.addEventListener('click', e => {
      if (e.target === overlay) close();
    });
    closeBtn.addEventListener('click', close);

    document.body.appendChild(overlay);
  }

  document.getElementById('push-tbody').addEventListener('click', ev => {
    const clickable = ev.target.closest('[data-action]');
    if (!clickable) return;
    const action = clickable.getAttribute('data-action');
    const id = Number(clickable.getAttribute('data-id'));
    if (action === 'play-video') {
      playVideoModal(id);
      return;
    }

    const btn = clickable.closest('button');
    if (!btn) return;
    if (action === 'open-modal') openPushModal(id);
    else if (action === 'ai-detail') showAuditDetail(id);
    else if (action === 'reset') resetPush(id);
    else if (action === 'view-logs') viewLogs(id);
    else if (action === 'skip') skipPush(id);
    else if (action === 'unskip') unskipPush(id);
  });

  document.getElementById('drawer-close').addEventListener('click', () => {
    document.getElementById('push-log-drawer').hidden = true;
  });

  window.addEventListener('popstate', () => {
    applyUrlToFilters();
    load({ syncUrl: false });
  });

  function setupStickyHeaderResizeObserver() {
    const stickyHeader = document.querySelector('.push-header-sticky');
    if (stickyHeader) {
      const observer = new ResizeObserver(entries => {
        for (let entry of entries) {
          const height = entry.target.offsetHeight;
          document.documentElement.style.setProperty('--sticky-header-height', `${height}px`);
        }
      });
      observer.observe(stickyHeader);
    }
  }

  window._pushesLoad = load;
  Promise.all([loadLanguages(), loadOwners()]).then(() => {
    applyUrlToFilters();
    bindFilters();
    setupStickyHeaderResizeObserver();
    load({ urlMode: 'replace' });
  });
})();
