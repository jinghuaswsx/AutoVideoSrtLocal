(function() {
  const urlParams = new URLSearchParams(window.location.search);
  const initialPage = parseInt(urlParams.get('page') || '1', 10);

  const state = {
    page: isNaN(initialPage) ? 1 : initialPage,
    pageSize: 100,
    loaded: false,
    languages: [],
    items: [],
    bindingItem: null,
    mkResults: [],
  };

  const MARKET_LABELS = {
    DE: '德(DE)',
    FR: '法(FR)',
    IT: '意(IT)',
    ES: '西(ES)',
    PT: '葡(PT)',
    NL: '荷(NL)',
    SV: '瑞(SV)',
    SE: '瑞(SE)',
    FI: '芬(FI)',
    EN: '英(EN)',
    GB: '英(GB)',
    UK: '英(UK)',
    US: '美(US)',
    CA: '加(CA)',
    AU: '澳(AU)',
    MULTI: '多国',
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

  function syncVideoPageToAddressBar() {
    if (!window.history || !window.history.replaceState || !window.location) return;
    const url = new URL(window.location.href);
    if (state.page > 1) {
      url.searchParams.set('page', state.page);
    } else {
      url.searchParams.delete('page');
    }
    window.history.replaceState(null, '', url);
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
      syncVideoPageToAddressBar();
      await ensureLanguages();
      const data = await fetchJSON('/medias/api/video-materials?' + videoParams().toString());
      state.items = data.items || [];
      renderVideoTable(data);
      renderVideoPager(data);
      const scrollHost = $('vmListContainer') || host;
      scrollHost.scrollTop = 0;
      scrollHost.scrollLeft = 0;
      window.scrollTo(0, 0);
      updateStickyOffsetsSoon();
    } catch (err) {
      host.innerHTML = `<div class="oc-state"><div class="icon">${icon('alert', 28)}</div><p class="title">加载失败</p><p class="desc">${esc(err.message || err)}</p></div>`;
      updateStickyOffsetsSoon();
    }
  }

  function previewHtml(item) {
    const src = item.preview_cover_url || '';
    const videoUrl = item.video_url || '';
    const preview = src
      ? `<img src="${esc(src)}" alt="">`
      : `<span>${icon('film', 24)}</span>`;
    if (videoUrl) {
      return `
        <button type="button" class="oc-vm-preview-btn" data-video-item="${esc(item.id)}" title="播放视频：${esc(item.display_name || item.filename || '')}">
          ${preview}
          <span class="oc-vm-play" aria-hidden="true"></span>
        </button>
      `;
    }
    return preview;
  }

  function openVideoPlayer(item) {
    const mask = $('vmPlayerMask');
    const video = $('vmPlayerVideo');
    const title = $('vmPlayerTitle');
    if (!mask || !video || !item || !item.video_url) return;
    if (title) title.textContent = item.display_name || item.filename || '视频预览';
    video.pause();
    video.removeAttribute('src');
    video.load();
    video.src = item.video_url;
    mask.hidden = false;
    video.focus();
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise.catch(() => {});
    }
  }

  function closeVideoPlayer() {
    const mask = $('vmPlayerMask');
    const video = $('vmPlayerVideo');
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
    if (mask) mask.hidden = true;
  }

  function bindVideoPlayerShell() {
    const mask = $('vmPlayerMask');
    if (!mask || mask.dataset.bound === '1') return;
    mask.dataset.bound = '1';
    mask.querySelectorAll('[data-vm-player-close]').forEach(btn => {
      btn.addEventListener('click', closeVideoPlayer);
    });
    mask.addEventListener('click', event => {
      if (event.target === mask) closeVideoPlayer();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !mask.hidden) closeVideoPlayer();
    });
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

  function fmtAdSpend(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num) || num <= 0) return '$0.00';
    return '$' + num.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function fmtAdRoas(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toFixed(2);
  }

  function fmtAdResultCount(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num) || num <= 0) return '0';
    return Math.round(num).toLocaleString('en-US');
  }

  function marketDisplayName(code) {
    const upper = String(code || '').trim().toUpperCase();
    if (!upper) return '-';
    return MARKET_LABELS[upper] || upper;
  }

  function adSpendCell(value) {
    return `<span class="oc-vm-spend-value">${esc(fmtAdSpend(value))}</span>`;
  }

  function adRoasCell(value) {
    return `<span class="oc-vm-spend-value roas">${esc(fmtAdRoas(value))}</span>`;
  }

  function adResultCell(value) {
    return `<span class="oc-vm-spend-value">${esc(fmtAdResultCount(value))}</span>`;
  }

  function adSpendHtml(item) {
    const perf = (item && item.ad_performance) || {};
    return `
      <div class="oc-vm-spend-table" role="presentation">
        <span class="oc-vm-spend-corner">说明</span>
        <span class="oc-vm-spend-head">今天</span>
        <span class="oc-vm-spend-head">昨天</span>
        <span class="oc-vm-spend-head">7天</span>
        <span class="oc-vm-spend-head">30天</span>
        <span class="oc-vm-spend-head">总消耗</span>
        <span class="oc-vm-spend-label">广告消耗</span>
        ${adSpendCell(perf.today_spend_usd)}
        ${adSpendCell(perf.yesterday_spend_usd)}
        ${adSpendCell(perf.last_7d_spend_usd)}
        ${adSpendCell(perf.last_30d_spend_usd)}
        ${adSpendCell(perf.total_spend_usd)}
        <span class="oc-vm-spend-label">ROAS</span>
        ${adRoasCell(perf.today_roas)}
        ${adRoasCell(perf.yesterday_roas)}
        ${adRoasCell(perf.last_7d_roas)}
        ${adRoasCell(perf.last_30d_roas)}
        ${adRoasCell(perf.roas)}
        <span class="oc-vm-spend-label">订单量</span>
        ${adResultCell(perf.today_result_count)}
        ${adResultCell(perf.yesterday_result_count)}
        ${adResultCell(perf.last_7d_result_count)}
        ${adResultCell(perf.last_30d_result_count)}
        ${adResultCell(perf.total_result_count)}
      </div>
    `;
  }

  function adRoasHtml(item) {
    const perf = (item && item.ad_performance) || {};
    return `
      <span class="oc-vm-roas-inline">
        <span class="oc-lang-label">总体ROAS</span>
        <strong>${esc(fmtAdRoas(perf.roas))}</strong>
      </span>
    `;
  }

  function adCountryHtml(item) {
    const perf = (item && item.ad_performance) || {};
    const countries = Array.isArray(perf.countries) ? perf.countries : [];
    if (!countries.length) {
      return '<span class="oc-vm-muted">-</span>';
    }
    return `<div class="oc-vm-country-list oc-country-metrics-bar">` + countries.map(country => `
      <div class="oc-vm-country-row oc-country-metrics-line" title="${esc(marketDisplayName(country.country) + ' 消耗 ' + fmtAdSpend(country.spend_usd) + ' ROAS ' + fmtAdRoas(country.roas))}">
        <span class="oc-vm-country-code">${esc(marketDisplayName(country.country))}</span>
        <span class="oc-vm-country-values">
          <span class="oc-vm-country-metric"><span class="oc-lang-label">消耗</span><strong>${esc(fmtAdSpend(country.spend_usd))}</strong></span>
          <span class="oc-vm-country-metric"><span class="oc-lang-label">ROAS</span><strong>${esc(fmtAdRoas(country.roas))}</strong></span>
        </span>
      </div>
    `).join('') + `</div>`;
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
        <td class="oc-vm-ad-spend-cell">${adSpendHtml(item)}</td>
        <td class="oc-vm-ad-roas-cell">${adRoasHtml(item)}</td>
        <td class="oc-vm-country-cell">${adCountryHtml(item)}</td>
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
            <th>总消耗</th>
            <th>ROAS</th>
            <th>国家情况</th>
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
    host.querySelectorAll('[data-video-item]').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = state.items.find(row => Number(row.id) === Number(btn.dataset.videoItem));
        if (item) openVideoPlayer(item);
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
    const pagers = [$('vmTopPager'), $('vmBottomPager')].filter(Boolean);
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
    const prevPage = Math.max(1, page - 1);
    const nextPage = Math.min(pages, page + 1);
    const buttons = [
      `<span class="oc-vm-page-summary">每页 <strong style="color: #2563eb; font-weight: bold;">${pageSize}</strong> 条数据</span>`,
      `<span class="oc-vm-page-summary" data-vm-page-summary>第 ${page} / ${pages} 页 · 共 ${pages} 页</span>`,
      `<button type="button" data-vm-page="1"${firstDisabled}>首页</button>`,
      `<button type="button" data-vm-page="${prevPage}"${firstDisabled}>上一页</button>`,
    ];
    for (let p = Math.max(1, page - 2); p <= Math.min(pages, page + 2); p++) {
      buttons.push(`<button type="button" class="${p === page ? 'active' : ''}" data-vm-page="${p}">${p}</button>`);
    }
    buttons.push(`<button type="button" data-vm-page="${nextPage}"${lastDisabled}>下一页</button>`);
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
    bindVideoPlayerShell();
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
