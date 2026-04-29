(function () {
  const STATUS_LABELS = {
    not_ready: { text: '未就绪', cls: 'badge-gray' },
    pending:   { text: '待推送', cls: 'badge-blue' },
    pushed:    { text: '已推送', cls: 'badge-green' },
    failed:    { text: '推送失败', cls: 'badge-red' },
  };
  const READINESS_LABELS = {
    has_object: '视频',
    has_cover: '封面',
    has_copywriting: '文案',
    lang_supported: '链接',
    has_push_texts: '英文文案格式正确',
    shopify_image_confirmed: '图片/链接确认',
  };

  const PUSH_MODAL_MODES = {
    CONFIRM: 'confirm',
    JSON: 'json',
    LOCALIZED_TEXT: 'localized-text',
    LOCALIZED_JSON: 'localized-json',
    PRODUCT_LINKS_JSON: 'product-links-json',
    PRODUCT_LINKS: 'product-links',
  };

  const state = { page: 1, pageSize: 20, total: 0, items: [] };
  let LANGUAGES = [];

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

  async function fetchJSON(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok && resp.status !== 204) {
      const body = await resp.text();
      throw Object.assign(new Error(`HTTP ${resp.status}`), { status: resp.status, body });
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  function formatLanguageLabel(code) {
    const raw = String(code || '').trim();
    const normalized = raw.toLowerCase();
    if (!normalized) return '';
    const lang = LANGUAGES.find(l => l && l.code === normalized);
    const name = lang && lang.name_zh ? String(lang.name_zh).trim() : '';
    return name ? `${name} (${normalized})` : raw;
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
    const original = btn.dataset.originalLabel || btn.textContent || '复制';
    btn.dataset.originalLabel = original;
    btn.textContent = text;
    if (btn._copyTimer) window.clearTimeout(btn._copyTimer);
    btn._copyTimer = window.setTimeout(() => {
      btn.textContent = original;
    }, 1200);
  }

  // ---------- 筛选与列表 ----------

  async function loadLanguages() {
    try {
      const data = await fetchJSON('/medias/api/languages');
      LANGUAGES = data.languages || [];
      const sel = document.getElementById('f-lang');
      const all = document.createElement('option');
      all.value = ''; all.textContent = '全部';
      sel.innerHTML = ''; sel.appendChild(all);
      LANGUAGES.forEach(l => {
        if (l.code === 'en') return;
        const opt = document.createElement('option');
        opt.value = l.code; opt.textContent = formatLanguageLabel(l.code);
        sel.appendChild(opt);
      });
    } catch (e) {
      console.warn('load languages failed', e);
    }
  }

  function buildQuery() {
    const params = new URLSearchParams();
    const statusSel = document.getElementById('f-status');
    if (statusSel.value) params.set('status', statusSel.value);
    const langSel = document.getElementById('f-lang');
    if (langSel.value) params.set('lang', langSel.value);
    const product = document.getElementById('f-product').value.trim();
    if (product) params.set('product', product);
    const keyword = document.getElementById('f-keyword').value.trim();
    if (keyword) params.set('keyword', keyword);
    const df = document.getElementById('f-date-from').value;
    if (df) params.set('date_from', df);
    const dt = document.getElementById('f-date-to').value;
    if (dt) params.set('date_to', dt);
    params.set('page', String(state.page));
    return params.toString();
  }

  function renderReadinessText(readiness) {
    const parts = Object.entries(READINESS_LABELS).map(([key, label]) => {
      const ok = readiness[key];
      return `<span class="ready-item ${ok ? 'ready-ok' : 'ready-bad'}">${label}</span>`;
    });
    return `<div class="ready-row">${parts.join('<span class="ready-sep">|</span>')}</div>`;
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
    if (it.status === 'not_ready') {
      const missing = Object.entries(it.readiness)
        .filter(([, v]) => !v).map(([k]) => READINESS_LABELS[k] || k).join(' / ');
      return `<button class="btn-push" disabled title="缺少：${missing}">推送</button>`;
    }
    const label = it.status === 'failed' ? '重试推送' : '推送';
    const historyBtn = it.status === 'failed'
      ? `<button class="btn-mini" data-action="view-logs" data-id="${it.id}" style="margin-left:8px">历史</button>` : '';
    return `<button class="btn-push" data-action="open-modal" data-id="${it.id}">${label}</button>${historyBtn}`;
  }

  function renderRowLegacy(it) {
    const thumb = it.cover_url
      ? `<img class="thumb" src="${it.cover_url}" alt="">`
      : `<div class="thumb thumb-empty"></div>`;
    const durStr = (typeof it.duration_seconds === 'number') ? it.duration_seconds.toFixed(1) + 's' : '';
    const sizeStr = (it.file_size || 0).toLocaleString() + ' B';
    return `<tr data-id="${it.id}">
      <td>${thumb}</td>
      <td>
        <div class="product-name product-name-line">${it.product_name || ''}</div>
        <div class="product-code-row">
          <span class="product-code">${it.product_code || ''}</span>
        </div>
      </td>
      <td><span class="product-owner-name">${escapeHtml(it.product_owner_name || '-')}</span></td>
      <td>
        <div class="item-name">${it.display_name || it.filename || ''}</div>
        <div class="item-meta">${durStr} · ${sizeStr}</div>
      </td>
      <td><span class="lang-pill">${formatLanguageLabel(it.lang)}</span></td>
      <td class="ready-cell">${renderReadinessText(it.readiness)}</td>
      <td>${renderStatusBadge(it.status)}</td>
      <td class="time">${(it.created_at || '').replace('T', ' ').slice(0, 16)}</td>
      ${window.PUSH_IS_ADMIN ? `<td>${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  function renderRow(it) {
    const thumb = it.cover_url
      ? `<img class="thumb" src="${escapeAttr(it.cover_url)}" alt="">`
      : `<div class="thumb thumb-empty"></div>`;
    const durStr = (typeof it.duration_seconds === 'number') ? it.duration_seconds.toFixed(1) + 's' : '';
    const sizeStr = (it.file_size || 0).toLocaleString() + ' B';
    const productNameHtml = it.product_page_url
      ? `<a class="product-name product-link product-name-line" href="${escapeAttr(it.product_page_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(it.product_name || '')}</a>`
      : `<div class="product-name product-name-line">${escapeHtml(it.product_name || '')}</div>`;
    const productCode = it.product_code || '';
    const productCodeHtml = productCode
      ? `<div class="product-code-row">
           <span class="product-code">${escapeHtml(productCode)}</span>
           <button type="button" class="product-copy-btn" data-copy-product-code="${escapeAttr(productCode)}">复制</button>
         </div>`
      : `<div class="product-code-row"><span class="product-code"></span></div>`;
    const productOwnerName = String(it.product_owner_name || '').trim();
    const mkId = (it.mk_id === null || it.mk_id === undefined || it.mk_id === '') ? '—' : String(it.mk_id);
    return `<tr data-id="${it.id}">
      <td>${thumb}</td>
      <td>
        ${productNameHtml}
        ${productCodeHtml}
      </td>
      <td><span class="product-owner-name">${escapeHtml(productOwnerName || '-')}</span></td>
      <td class="mk-id-cell">${escapeHtml(mkId)}</td>
      <td>
        <div class="item-name">${escapeHtml(it.display_name || it.filename || '')}</div>
        <div class="item-meta">${escapeHtml(durStr ? `${durStr} · ${sizeStr}` : sizeStr)}</div>
      </td>
      <td><span class="lang-pill">${escapeHtml(it.lang || '')}</span></td>
      <td class="ready-cell">${renderReadinessText(it.readiness)}</td>
      <td class="audit-cell-wrap">${renderAuditCell(it)}</td>
      <td>${renderStatusBadge(it.status)}</td>
      <td class="time">${escapeHtml((it.created_at || '').replace('T', ' ').slice(0, 16))}</td>
      ${window.PUSH_IS_ADMIN ? `<td>${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  async function load() {
    const tbody = document.getElementById('push-tbody');
    const colspan = window.PUSH_IS_ADMIN ? 11 : 10;
    tbody.innerHTML = `<tr><td colspan="${colspan}">加载中…</td></tr>`;
    try {
      const data = await fetchJSON('/pushes/api/items?' + buildQuery());
      state.total = data.total;
      state.items = data.items || [];
      if (!state.items.length) {
        tbody.innerHTML = `<tr><td colspan="${colspan}">无数据</td></tr>`;
      } else {
        tbody.innerHTML = state.items.map(renderRow).join('');
      }
      renderPagination();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="${colspan}">加载失败: ${e.message}</td></tr>`;
    }
  }

  function renderPagination() {
    const box = document.getElementById('push-pagination');
    const totalPages = Math.ceil(state.total / state.pageSize) || 1;
    const parts = [`共 ${state.total} 条`];
    for (let p = 1; p <= totalPages; p++) {
      if (p === state.page) parts.push(`<strong>${p}</strong>`);
      else parts.push(`<a href="#" data-page="${p}">${p}</a>`);
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
    document.getElementById('btn-reset').addEventListener('click', () => {
      document.querySelectorAll('.push-toolbar input').forEach(i => (i.value = ''));
      document.getElementById('f-status').value = 'pending';
      document.getElementById('f-lang').value = '';
      state.page = 1; load();
    });
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
      ['tags', JSON.stringify(payload.tags || [])],
    ];
    pairs.forEach(([k, v]) => {
      kv.appendChild(el('span', { class: 'k' }, k));
      kv.appendChild(el('span', { class: 'v' }, [el('code', {}, String(v ?? ''))]));
    });
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
        const coverSrc = previewCoverUrl || v.image_url || null;
        if (coverSrc) {
          preview.appendChild(el('img', { class: 'pm-thumb', src: coverSrc, alt: `cover-${i}` }));
        }
        if (v.url) {
          preview.appendChild(el('video', {
            class: 'pm-thumb', src: v.url, poster: coverSrc,
            controls: true, preload: 'metadata',
          }));
        }
        sub.appendChild(preview);
      });
      root.appendChild(sub);
    }

    return root;
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
      root.appendChild(el('p', { class: 'pm-empty' }, '当前暂无可推送小语种文案（需要产品下有非英文且标题/文案/描述齐全的 copywriting）'));
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

  function describeError(e) {
    let body = {};
    try { body = JSON.parse(e.body || '{}'); } catch (_) {}
    const err = body.error || '';
    const detail = body.detail || body.response_body || e.body || e.message || '';
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
    if (err === 'mk_id_missing') return '该产品缺少 mk_id，不能推送小语种文案';
    if (err === 'localized_texts_empty') return '当前没有可推送的小语种文案';
    if (err === 'downstream_unreachable') return `下游不可达：${detail}`;
    if (err === 'downstream_error') {
      const preview = (body.response_body || '').slice(0, 200);
      return `下游返回 HTTP ${body.upstream_status}\n${preview}`;
    }
    return detail || e.message;
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

    const body = el('div', { class: 'pm-body' });
    modal.appendChild(body);

    const infoCard = el('section', { class: 'pm-section' }, [el('h4', {}, '素材信息')]);
    const infoKV = el('div', { class: 'pm-kv' });
    const mkIdValue = el('span', { class: 'v' }, '-');
    const addKV = (k, v) => {
      infoKV.appendChild(el('span', { class: 'k' }, k));
      if (v instanceof Node) infoKV.appendChild(v);
      else infoKV.appendChild(el('span', { class: 'v' }, v));
    };
    addKV('产品', `${item.product_name || ''}  ·  ${item.product_code || ''}`);
    addKV('语种', formatLanguageLabel(item.lang));
    addKV('文件', item.display_name || item.filename || '-');
    addKV('item_id', String(item.id));
    addKV('mk_id', mkIdValue);
    addKV('状态', STATUS_LABELS[item.status]?.text || item.status);
    infoCard.appendChild(infoKV);
    body.appendChild(infoCard);

    const auditCard = el('section', { class: 'pm-section audit-modal-section' }, [el('h4', {}, 'AI评估信息')]);
    const auditKV = el('div', { class: 'pm-kv' });
    const addAuditKV = (k, v) => {
      auditKV.appendChild(el('span', { class: 'k' }, k));
      if (v instanceof Node) auditKV.appendChild(v);
      else auditKV.appendChild(el('span', { class: 'v' }, v));
    };
    addAuditKV('上架', el('span', { class: 'v' }, [createListingStatusBadge(item.listing_status)]));
    addAuditKV('AI评分', formatAuditScore(item.ai_score));
    addAuditKV('AI评估结果', item.ai_evaluation_result || '未评估');
    addAuditKV('备注说明', item.remark || '暂无备注');
    addAuditKV('AI评估详情', el('span', { class: 'v' }, [
      el('pre', { class: 'audit-detail-pre' }, formatAuditDetail(item.ai_evaluation_detail)),
    ]));
    auditCard.appendChild(auditKV);
    body.appendChild(auditCard);

    const contentCard = el('section', { class: 'pm-section' }, [el('h4', {}, '推送内容')]);
    const loadingTip = el('p', { class: 'pm-empty' }, '加载中…');
    const paneConfirm = el('div', { class: 'pm-pane' });
    const paneJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneLocalized = el('div', { class: 'pm-pane', hidden: true });
    const paneLocalizedJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneProductLinksJson = el('pre', { class: 'pm-pane pm-json', hidden: true });
    const paneProductLinks = el('div', { class: 'pm-pane', hidden: true });
    contentCard.appendChild(loadingTip);
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
    modal.appendChild(respWrap);

    const footer = el('div', { class: 'pm-footer' });
    const btnPush = el('button', { type: 'button', class: 'btn-push', disabled: true }, '推送');
    const btnCancel = el('button', { type: 'button', class: 'btn-mini' }, '关闭');
    footer.appendChild(btnPush);
    footer.appendChild(btnCancel);
    modal.appendChild(footer);

    let activeMode = PUSH_MODAL_MODES.CONFIRM;
    let payloadData = null;
    let mkId = null;
    let localizedTexts = [];
    let localizedTargetUrl = '';
    let productLinksPreview = null;
    let materialPushed = item.status === 'pushed';
    let localizedPushed = false;
    let productLinksPushed = false;
    let anyPushSucceeded = false;

    function isLocalizedMode(m = activeMode) {
      return m === PUSH_MODAL_MODES.LOCALIZED_TEXT || m === PUSH_MODAL_MODES.LOCALIZED_JSON;
    }

    function syncPushButton() {
      if (activeMode === PUSH_MODAL_MODES.PRODUCT_LINKS_JSON) {
        btnPush.disabled = true;
        btnPush.textContent = '预览无需推送';
        return;
      }
      if (activeMode === PUSH_MODAL_MODES.PRODUCT_LINKS) {
        const linkPayload = productLinksPreview && productLinksPreview.payload;
        const linkCount = linkPayload && Array.isArray(linkPayload.product_links)
          ? linkPayload.product_links.length
          : 0;
        const noTarget = !productLinksPreview || !productLinksPreview.target_url;
        const noLinks = !linkCount;
        btnPush.disabled = productLinksPushed || !payloadData || noTarget || noLinks;
        btnPush.textContent = productLinksPushed
          ? '链接已推送'
          : noTarget
            ? '未配链接接口'
            : noLinks ? '无可推送链接' : '推送链接';
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
            : noTexts ? '无可推送文案' : '推送小语种文案';
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
      auditCard.hidden = mode === PUSH_MODAL_MODES.PRODUCT_LINKS || mode === PUSH_MODAL_MODES.PRODUCT_LINKS_JSON;
      syncPushButton();
    }

    pills.forEach(p => p.addEventListener('click', () => setMode(p.dataset.mode)));

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
        respMkIdTip.textContent = '配对 mk_id 失败，请检查，当前无法完成小语种文案推送，缺失 mk_id';
      }
    }

    function close() {
      if (!overlay.parentNode) return;
      overlay.parentNode.removeChild(overlay);
      document.removeEventListener('keydown', onEsc);
      if (anyPushSucceeded) load();
    }
    function onEsc(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onEsc);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    btnClose.addEventListener('click', close);
    btnCancel.addEventListener('click', close);

    (async () => {
      try {
        const data = await fetchJSON(`/pushes/api/items/${itemId}/payload`);
        payloadData = data.payload;
        mkId = data.mk_id || null;
        localizedTargetUrl = data.localized_push_target_url || '';
        localizedTexts = (data.localized_texts_request && data.localized_texts_request.texts) || [];
        productLinksPreview = data.product_links_push || null;

        mkIdValue.textContent = mkId ? String(mkId) : '-';

        clear(paneConfirm);
        // 顶部显式打印推送地址
        const pushUrlRow = el('div', { class: 'pm-kv', style: 'margin-bottom:12px' });
        pushUrlRow.appendChild(el('span', { class: 'k' }, '推送地址'));
        pushUrlRow.appendChild(el('span', { class: 'v' }, [
          el('code', {}, data.push_url || '(未配置)'),
        ]));
        paneConfirm.appendChild(pushUrlRow);
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

        loadingTip.remove();
        setMode(PUSH_MODAL_MODES.CONFIRM);
      } catch (err) {
        loadingTip.textContent = `载荷加载失败：${describeError(err)}`;
        loadingTip.classList.add('pm-error');
      }
    })();

    btnPush.addEventListener('click', async () => {
      if (!payloadData) return;
      btnPush.disabled = true;
      btnCancel.disabled = true;
      btnPush.textContent = '推送中…';
      try {
        if (activeMode === PUSH_MODAL_MODES.PRODUCT_LINKS) {
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
          showResponse(body, false, '小语种文案推送响应');
          localizedPushed = true;
          anyPushSucceeded = true;
        } else {
          const body = await fetchJSON(`/pushes/api/items/${itemId}/push`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });
          showResponse(body, false, '素材推送响应');
          showMkIdMatch(body.mk_id_match);
          materialPushed = true;
          anyPushSucceeded = true;
          // mk_id 匹配成功 → 同步刷新顶部信息 + 小语种文案 pane + JSON 预览 + 推送按钮状态
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
          activeMode === PUSH_MODAL_MODES.PRODUCT_LINKS
            ? '推送链接失败'
            : isLocalizedMode() ? '小语种文案推送失败' : '素材推送失败');
      } finally {
        btnCancel.disabled = false;
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
                 <span class="time">${l.created_at}</span></div>
            ${l.error_message ? `<div class="err">${l.error_message}</div>` : ''}
            ${l.response_body ? `<pre>${l.response_body.slice(0, 500)}</pre>` : ''}
          </div>
        `).join('');
      }
    } catch (e) {
      content.textContent = '加载失败: ' + e.message;
    }
  }

  // ---------- 绑定 ----------

  document.getElementById('push-tbody').addEventListener('click', async ev => {
    const copyBtn = ev.target.closest('button[data-copy-product-code]');
    if (!copyBtn) return;
    try {
      await copyText(copyBtn.getAttribute('data-copy-product-code') || '');
      flashCopyButton(copyBtn, '已复制');
    } catch (err) {
      flashCopyButton(copyBtn, '复制失败');
    }
  });

  document.getElementById('push-tbody').addEventListener('click', ev => {
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = Number(btn.getAttribute('data-id'));
    if (action === 'open-modal') openPushModal(id);
    else if (action === 'ai-detail') showAuditDetail(id);
    else if (action === 'reset') resetPush(id);
    else if (action === 'view-logs') viewLogs(id);
  });

  document.getElementById('drawer-close').addEventListener('click', () => {
    document.getElementById('push-log-drawer').hidden = true;
  });

  window._pushesLoad = load;
  loadLanguages().then(() => { bindFilters(); load(); });
})();
