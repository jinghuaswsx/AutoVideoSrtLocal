(function() {
  const state = {
    page: 1,
    pageSize: 100,
    loaded: false,
    languages: [],
    items: [],
    bindingItem: null,
    mkResults: [],
  };

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function icon(name, size = 14) {
    return `<svg width="${size}" height="${size}"><use href="#ic-${name}"/></svg>`;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      let message = text || `HTTP ${res.status}`;
      try {
        const data = JSON.parse(text);
        message = data.message || data.error || message;
      } catch (_) {}
      throw new Error(message);
    }
    return res.json();
  }

  function fmtDate(value) {
    if (!value) return '-';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtDateParts(value) {
    const full = fmtDate(value);
    if (full === '-') return { date: '-', time: '' };
    const parts = full.split(' ');
    return { date: parts[0] || full, time: parts[1] || '' };
  }

  function fmtSize(value) {
    const n = Number(value || 0);
    if (!n) return '-';
    if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
    if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${n} B`;
  }

  function langName(code) {
    const raw = String(code || '').toLowerCase();
    const row = state.languages.find(item => String(item.code || '').toLowerCase() === raw);
    const upper = raw.toUpperCase();
    return row ? `${row.name_zh || upper} (${upper})` : upper;
  }

  function activeTab() {
    return document.querySelector('.oc-page-tabs [data-media-tab].active')?.dataset.mediaTab || 'products';
  }

  function initialTab() {
    const configured = String(window.MEDIAS_ACTIVE_TAB || '').trim();
    if (configured === 'products' || configured === 'videos') return configured;
    if (new URLSearchParams(window.location.search).get('tab') === 'videos') return 'videos';
    return activeTab();
  }

  function setTab(tab) {
    document.querySelectorAll('[data-media-tab]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mediaTab === tab);
      btn.setAttribute('aria-selected', btn.dataset.mediaTab === tab ? 'true' : 'false');
    });
    const products = $('mediaProductsPanel');
    const videos = $('videoMaterialsPanel');
    if (products) products.hidden = tab !== 'products';
    if (videos) videos.hidden = tab !== 'videos';
    if (tab === 'videos' && !state.loaded) {
      loadVideoMaterials();
    }
  }

  async function ensureLanguages() {
    if (state.languages.length) return state.languages;
    const data = await fetchJSON('/medias/api/languages');
    state.languages = data.items || [];
    const select = $('vmLang');
    if (select && select.options.length <= 1) {
      state.languages.forEach(lang => {
        const opt = document.createElement('option');
        opt.value = lang.code;
        const code = String(lang.code || '').trim().toUpperCase();
        opt.textContent = lang.name_zh ? `${lang.name_zh} (${code})` : code;
        select.appendChild(opt);
      });
    }
    return state.languages;
  }

  function videoParams() {
    const params = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize),
    });
    const keyword = $('vmKeyword') ? $('vmKeyword').value.trim() : '';
    const lang = $('vmLang') ? $('vmLang').value.trim() : '';
    const adPlan = $('vmAdPlan') ? $('vmAdPlan').value.trim() : 'all';
    if (keyword) params.set('keyword', keyword);
    if (lang) params.set('lang', lang);
    if (adPlan && adPlan !== 'all') params.set('ad_plan_status', adPlan);
    return params;
  }

  function renderVideoSkeleton() {
    const host = $('vmList');
    if (!host) return;
    host.innerHTML = '<div class="oc-state"><div class="icon">' + icon('film', 28) + '</div><p class="title">加载中</p></div>';
  }

  async function loadVideoMaterials() {
    const host = $('vmList');
    if (!host) return;
    state.loaded = true;
    renderVideoSkeleton();
    try {
      await ensureLanguages();
      const data = await fetchJSON('/medias/api/video-materials?' + videoParams().toString());
      state.items = data.items || [];
      renderVideoTable(data);
      renderVideoPager(data);
      host.scrollTop = 0;
      host.scrollLeft = 0;
      updateStickyOffsetsSoon();
    } catch (err) {
      host.innerHTML = `<div class="oc-state"><div class="icon">${icon('alert', 28)}</div><p class="title">加载失败</p><p class="desc">${esc(err.message || err)}</p></div>`;
      updateStickyOffsetsSoon();
    }
  }

  function previewHtml(item) {
    const src = item.cover_url || item.thumbnail_url || '';
    if (src) {
      return `<img src="${esc(src)}" alt="">`;
    }
    return `<span>${icon('film', 18)}</span>`;
  }

  function bindingHtml(item) {
    const binding = item.mk_binding;
    if (!binding) {
      return '<span class="oc-vm-muted">未绑定</span>';
    }
    return `
      <div class="oc-vm-binding-name">${esc(binding.mk_video_name || binding.mk_video_path)}</div>
      <div class="oc-vm-binding-meta">${esc(binding.mk_product_name || '')}</div>
    `;
  }

  function adPlanHtml(item) {
    const planClass = item.has_ad_plan ? 'has' : 'none';
    const planText = item.has_ad_plan ? '有广告计划' : '没有广告计划';
    const pushed = fmtDateParts(item.pushed_at);
    const detail = item.ad_plan_detail || null;
    const content = `
      <span class="oc-vm-plan ${planClass}">${planText}</span>
      <span class="oc-vm-plan-meta" title="${esc(fmtDate(item.pushed_at))}">
        <span class="oc-vm-plan-meta-line">${esc(pushed.date)}</span>
        ${pushed.time ? `<span class="oc-vm-plan-meta-line">${esc(pushed.time)}</span>` : ''}
      </span>
    `;
    if (item.has_ad_plan && detail && detail.url) {
      const title = detail.name || detail.code || '广告计划详情';
      return `
        <button type="button" class="oc-vm-plan-link" data-ad-plan-item="${esc(item.id)}" title="打开广告计划详情：${esc(title)}">
          ${content}
        </button>
      `;
    }
    return `<span class="oc-vm-plan-box">${content}</span>`;
  }

  function openAdPlanDetail(item) {
    const url = item && item.ad_plan_detail && item.ad_plan_detail.url;
    if (!url) return;
    const opened = window.open(url, '_blank');
    if (opened && typeof opened.focus === 'function') {
      opened.focus();
    }
  }

  function rowHtml(item) {
    return `
      <tr data-item-id="${esc(item.id)}">
        <td class="oc-vm-preview">${previewHtml(item)}</td>
        <td class="mono">${esc(item.product_id)}</td>
        <td>
          <div class="oc-vm-strong">${esc(item.product_name || '-')}</div>
          <div class="oc-vm-muted mono">${esc(item.product_code || '')}</div>
        </td>
        <td>
          <div class="oc-vm-strong">${esc(item.display_name || item.filename)}</div>
          <div class="oc-vm-muted mono">${esc(item.filename)}</div>
        </td>
        <td>${esc(langName(item.lang))}</td>
        <td class="oc-vm-plan-cell">${adPlanHtml(item)}</td>
        <td>${bindingHtml(item)}</td>
        <td class="mono">${esc(fmtSize(item.file_size))}</td>
        <td>${esc(fmtDate(item.created_at))}</td>
        <td class="actions">
          <button type="button" class="oc-btn sm ghost" data-bind-item="${esc(item.id)}">${icon('edit', 12)}<span>绑定</span></button>
        </td>
      </tr>
    `;
  }

  function renderVideoTable(data) {
    const host = $('vmList');
    const total = $('vmTotalPill');
    const items = data.items || [];
    if (total) total.textContent = `共 ${data.total || 0} 条素材`;
    if (!items.length) {
      host.innerHTML = `<div class="oc-state"><div class="icon">${icon('film', 28)}</div><p class="title">没有视频素材</p></div>`;
      return;
    }
    host.innerHTML = `
      <table class="oc-table oc-vm-table">
        <thead>
          <tr>
            <th>预览</th>
            <th>产品 ID</th>
            <th>产品</th>
            <th>素材名</th>
            <th>语种</th>
            <th>广告计划</th>
            <th>明空绑定</th>
            <th>大小</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>${items.map(rowHtml).join('')}</tbody>
      </table>
    `;
    host.querySelectorAll('[data-bind-item]').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = state.items.find(row => Number(row.id) === Number(btn.dataset.bindItem));
        if (item) openBindingModal(item);
      });
    });
    host.querySelectorAll('[data-ad-plan-item]').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = state.items.find(row => Number(row.id) === Number(btn.dataset.adPlanItem));
        if (item) openAdPlanDetail(item);
      });
    });
  }

  function renderVideoPager(data) {
    const pagers = [$('vmTopPager'), $('vmPager')].filter(Boolean);
    if (!pagers.length) return;
    const total = Number(data.total || 0);
    const pageSize = Number(data.page_size || state.pageSize);
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const page = Math.min(pages, Math.max(1, Number(data.page || 1)));
    if (pages <= 1) {
      pagers.forEach(pager => {
        pager.innerHTML = '';
        pager.hidden = true;
      });
      return;
    }
    const firstDisabled = page <= 1 ? ' disabled aria-disabled="true"' : '';
    const lastDisabled = page >= pages ? ' disabled aria-disabled="true"' : '';
    const buttons = [
      `<span class="oc-vm-page-summary" data-vm-page-summary>第 ${page} / ${pages} 页 · 共 ${pages} 页</span>`,
      `<button type="button" data-vm-page="1"${firstDisabled}>首页</button>`,
    ];
    for (let p = Math.max(1, page - 2); p <= Math.min(pages, page + 2); p++) {
      buttons.push(`<button type="button" class="${p === page ? 'active' : ''}" data-vm-page="${p}">${p}</button>`);
    }
    buttons.push(`<button type="button" data-vm-page="${pages}"${lastDisabled}>末页</button>`);
    buttons.push(`
      <label class="oc-vm-page-jump">
        <span>去</span>
        <input type="number" min="1" max="${pages}" value="${page}" inputmode="numeric" pattern="[0-9]*" data-vm-page-jump aria-label="跳转到指定页">
        <span>页</span>
      </label>
    `);
    buttons.push(`<span class="oc-vm-page-summary">共 ${total} 条数据</span>`);
    pagers.forEach(pager => {
      pager.hidden = false;
      pager.innerHTML = buttons.join('');
      pager.querySelectorAll('[data-vm-page]').forEach(btn => {
        btn.addEventListener('click', () => {
          state.page = Number(btn.dataset.vmPage || 1);
          loadVideoMaterials();
        });
      });
      const jumpInput = pager.querySelector('[data-vm-page-jump]');
      if (jumpInput) {
        jumpInput.addEventListener('keydown', event => {
          if (event.key === 'Enter') {
            event.preventDefault();
            const requested = Number.parseInt(jumpInput.value, 10);
            if (!Number.isFinite(requested)) {
              jumpInput.value = String(page);
              return;
            }
            const target = Math.min(pages, Math.max(1, requested));
            jumpInput.value = String(target);
            if (target === state.page) return;
            state.page = target;
            loadVideoMaterials();
          }
        });
      }
    });
  }

  function updateStickyOffsetsSoon() {
    if (typeof window.updateMediaStickyOffsets !== 'function') return;
    window.requestAnimationFrame(() => window.updateMediaStickyOffsets());
  }

  function openBindingModal(item) {
    state.bindingItem = item;
    state.mkResults = [];
    const mask = $('vmBindMask');
    const input = $('vmBindKeyword');
    const meta = $('vmBindMeta');
    const results = $('vmBindResults');
    if (!mask || !input || !meta || !results) return;
    meta.textContent = `#${item.id} · ${item.filename}`;
    input.value = item.filename || item.display_name || '';
    results.innerHTML = '<div class="oc-vm-empty">等待搜索</div>';
    mask.hidden = false;
    input.focus();
    searchMkMaterials();
  }

  function closeBindingModal() {
    const mask = $('vmBindMask');
    if (mask) mask.hidden = true;
    state.bindingItem = null;
    state.mkResults = [];
  }

  async function searchMkMaterials() {
    const input = $('vmBindKeyword');
    const results = $('vmBindResults');
    if (!input || !results) return;
    const q = input.value.trim();
    if (!q) {
      results.innerHTML = '<div class="oc-vm-empty">请输入关键词</div>';
      return;
    }
    results.innerHTML = '<div class="oc-vm-empty">搜索中</div>';
    try {
      const data = await fetchJSON('/medias/api/video-materials/mk-search?q=' + encodeURIComponent(q));
      state.mkResults = data.items || [];
      renderMkResults();
    } catch (err) {
      results.innerHTML = `<div class="oc-vm-empty err">${esc(err.message || err)}</div>`;
    }
  }

  function mkRowHtml(item, idx) {
    return `
      <div class="oc-vm-mk-row">
        <div class="oc-vm-mk-body">
          <div class="oc-vm-strong">${esc(item.video_name || item.video_path)}</div>
          <div class="oc-vm-muted">${esc(item.mk_product_name || '')} · ID ${esc(item.mk_product_id || '-')}</div>
          <div class="oc-vm-muted mono">${esc(item.video_path || '')}</div>
          <div class="oc-vm-mk-stats">花费 ${esc(item.video_metadata && item.video_metadata.spends || 0)} · 广告 ${esc(item.video_metadata && item.video_metadata.ads_count || 0)}</div>
        </div>
        <button type="button" class="oc-btn sm primary" data-bind-mk="${idx}">${icon('check', 12)}<span>绑定</span></button>
      </div>
    `;
  }

  function renderMkResults() {
    const results = $('vmBindResults');
    if (!results) return;
    if (!state.mkResults.length) {
      results.innerHTML = '<div class="oc-vm-empty">没有找到明空素材</div>';
      return;
    }
    results.innerHTML = state.mkResults.map(mkRowHtml).join('');
    results.querySelectorAll('[data-bind-mk]').forEach(btn => {
      btn.addEventListener('click', () => bindSelectedMk(Number(btn.dataset.bindMk)));
    });
  }

  async function bindSelectedMk(index) {
    const item = state.bindingItem;
    const mk = state.mkResults[index];
    if (!item || !mk) return;
    try {
      await fetchJSON(`/medias/api/video-materials/${encodeURIComponent(item.id)}/mk-binding`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mk_product_id: mk.mk_product_id,
          mk_product_name: mk.mk_product_name,
          mk_video_path: mk.video_path,
          mk_video_name: mk.video_name,
          mk_video_image_path: mk.video_image_path,
          mk_video_metadata: mk.video_metadata || {},
        }),
      });
      closeBindingModal();
      await loadVideoMaterials();
    } catch (err) {
      alert('绑定失败：' + (err.message || err));
    }
  }

  function initVideoMaterials() {
    const tabs = document.querySelectorAll('[data-media-tab]');
    if (!tabs.length) return;
    tabs.forEach(btn => btn.addEventListener('click', () => {
      if (btn.tagName.toLowerCase() === 'a') return;
      setTab(btn.dataset.mediaTab || 'products');
    }));
    const keyword = $('vmKeyword');
    let timer = null;
    if (keyword) keyword.addEventListener('input', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        state.page = 1;
        loadVideoMaterials();
      }, 250);
    });
    ['vmLang', 'vmAdPlan'].forEach(id => {
      const el = $(id);
      if (el) el.addEventListener('change', () => {
        state.page = 1;
        loadVideoMaterials();
      });
    });
    const searchBtn = $('vmSearchBtn');
    if (searchBtn) searchBtn.addEventListener('click', () => {
      state.page = 1;
      loadVideoMaterials();
    });
    const closeBtn = $('vmBindClose');
    if (closeBtn) closeBtn.addEventListener('click', closeBindingModal);
    const cancelBtn = $('vmBindCancel');
    if (cancelBtn) cancelBtn.addEventListener('click', closeBindingModal);
    const bindSearch = $('vmBindSearchBtn');
    if (bindSearch) bindSearch.addEventListener('click', searchMkMaterials);
    const bindKeyword = $('vmBindKeyword');
    if (bindKeyword) bindKeyword.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        searchMkMaterials();
      }
    });
    setTab(initialTab());
  }

  document.addEventListener('DOMContentLoaded', initVideoMaterials);
})();
