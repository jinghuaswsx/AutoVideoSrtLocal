(function() {
  const state = { page: 1, current: null, pendingItemCover: null };
  const $ = (id) => document.getElementById(id);

  let LANGUAGES = [];

  async function ensureLanguages() {
    if (LANGUAGES.length) return LANGUAGES;
    const data = await fetchJSON('/medias/api/languages');
    LANGUAGES = data.items || [];
    return LANGUAGES;
  }

  function renderLangBar(coverage) {
    if (!LANGUAGES.length) return '';
    return `<div class="oc-lang-bar">` + LANGUAGES.map(l => {
      const c = (coverage || {})[l.code] || { items: 0, copy: 0, cover: false };
      const filled = c.items > 0;
      const cls = filled ? 'filled' : 'empty';
      const title = `${l.name_zh}: ${c.items} 视频 / ${c.copy} 文案 / ${c.cover ? '有主图' : '无主图'}`;
      return `<span class="oc-lang-chip ${cls}" title="${escapeHtml(title)}">`
           + `${l.code.toUpperCase()}${filled ? `<span class="count">${c.items}</span>` : ''}`
           + `</span>`;
    }).join('') + `</div>`;
  }

  function icon(name, size = 14) {
    return `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#ic-${name}"/></svg>`;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ---------- List ----------
  async function loadList() {
    const kw = $('kw').value.trim();
    const archived = $('archived').checked;
    const scopeAll = window.MEDIAS_IS_ADMIN && $('scopeAll') && $('scopeAll').checked;
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
    if (archived) params.set('archived', '1');
    if (scopeAll) params.set('scope', 'all');
    renderSkeleton();
    try {
      await ensureLanguages();
      const data = await fetchJSON('/medias/api/products?' + params);
      renderGrid(data.items);
      renderPager(data.total, data.page, data.page_size);
      const pill = $('totalPill');
      if (pill) pill.textContent = `共 ${data.total} 个产品`;
    } catch (e) {
      $('grid').innerHTML = `
        <div class="oc-state">
          <div class="icon">${icon('alert', 28)}</div>
          <p class="title">加载失败</p>
          <p class="desc">${escapeHtml(e.message || '请稍后重试')}</p>
          <button class="oc-btn ghost" onclick="location.reload()">刷新页面</button>
        </div>`;
    }
  }

  function renderSkeleton() {
    $('grid').innerHTML = Array.from({ length: 8 }, () => '<div class="oc-skel"></div>').join('');
  }

  function renderGrid(items) {
    const grid = $('grid');
    if (!items || !items.length) {
      grid.innerHTML = `
        <div class="oc-state">
          <div class="icon">${icon('package', 28)}</div>
          <p class="title">还没有产品素材</p>
          <p class="desc">创建你的第一个产品素材库，统一管理文案与视频资源</p>
          <button class="oc-btn primary" id="emptyCreate">
            ${icon('plus', 14)}<span>添加产品素材</span>
          </button>
        </div>`;
      const ec = $('emptyCreate');
      if (ec) ec.addEventListener('click', () => $('createBtn').click());
      return;
    }
    grid.innerHTML = `
      <table class="oc-table" style="table-layout:fixed;">
        <colgroup>
          <col style="width:48px">
          <col style="width:128px">
          <col>
          <col style="width:200px">
          <col style="width:64px">
          <col style="width:240px">
          <col style="width:120px">
          <col style="width:88px">
        </colgroup>
        <thead>
          <tr>
            <th>ID</th>
            <th>主图</th>
            <th>产品名称</th>
            <th>产品 ID</th>
            <th>素材数</th>
            <th>语种覆盖</th>
            <th>修改时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${items.map(rowHTML).join('')}
        </tbody>
      </table>`;
    grid.querySelectorAll('[data-edit]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); openEdit(+b.dataset.edit); }));
    grid.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', (e) => { e.stopPropagation(); deleteProduct(+b.dataset.del); }));
    grid.querySelectorAll('tr[data-pid] .name a').forEach(a =>
      a.addEventListener('click', (e) => { e.preventDefault(); openEdit(+a.dataset.pid); }));
  }

  function rowHTML(p) {
    const count = p.items_count || 0;
    const warnCls = !p.has_en_cover ? ' class="oc-row-warn"' : '';
    const cover = p.cover_thumbnail_url
      ? `<img src="${escapeHtml(p.cover_thumbnail_url)}" alt="" loading="lazy">`
      : `<div class="cover-ph">${icon('film', 16)}</div>`;
    return `
      <tr${warnCls} data-pid="${p.id}">
        <td class="mono">${p.id}</td>
        <td><div class="oc-thumb-sm">${cover}</div></td>
        <td class="name"><a href="#" data-pid="${p.id}" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a></td>
        <td class="mono ellipsis" title="${escapeHtml(p.product_code || '')}">${p.product_code ? escapeHtml(p.product_code) : '<span class="muted">—</span>'}</td>
        <td><span class="oc-pill">${count}</span></td>
        <td>${renderLangBar(p.lang_coverage)}</td>
        <td class="muted">${fmtDate(p.updated_at)}</td>
        <td class="actions">
          <button class="oc-btn sm ghost" data-edit="${p.id}">${icon('edit', 12)}<span>编辑</span></button>
        </td>
      </tr>`;
  }

  function closeAllMenus() {
    document.querySelectorAll('.oc-menu-pop.open').forEach(m => m.classList.remove('open'));
  }
  document.addEventListener('click', closeAllMenus);

  function renderPager(total, page, pageSize) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const p = $('pager');
    if (pages <= 1) { p.innerHTML = ''; return; }
    let html = '';
    for (let i = 1; i <= pages; i++) {
      html += `<button class="${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`;
    }
    p.innerHTML = html;
    p.querySelectorAll('[data-page]').forEach(b => b.addEventListener('click', () => {
      state.page = +b.dataset.page; loadList();
    }));
  }

  async function deleteProduct(pid) {
    if (!confirm('确认删除该产品及其所有素材？此操作不可恢复。')) return;
    await fetch('/medias/api/products/' + pid, { method: 'DELETE' });
    loadList();
  }

  // ---------- Modal ----------
  function showModal() { $('editMask').hidden = false; }
  function hideModal() { $('editMask').hidden = true; state.current = null; }

  function openCreate() {
    state.current = { product: null, copywritings: [], items: [] };
    state.pendingItemCover = null;
    $('modalTitle').textContent = '添加产品素材';
    $('mName').value = '';
    $('mCode').value = '';
    setCover(null);
    setItemCover(null);
    renderCopywritings([]);
    renderItems([]);
    $('uploadProgress').innerHTML = '';
    showModal();
    setTimeout(() => $('mName').focus(), 80);
  }

  async function openEdit(pid) {
    return openEditDetail(pid);
  }

  // ---------- Cover ----------
  const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

  function setCover(url) {
    const dz = $('coverDropzone');
    const img = $('coverImg');
    const replace = $('coverReplace');
    if (url) {
      img.src = url; img.hidden = false; dz.hidden = true;
      if (replace) replace.hidden = false;
    } else {
      img.removeAttribute('src'); img.hidden = true; dz.hidden = false;
      if (replace) replace.hidden = true;
    }
  }

  // ---- Item cover (add modal, pending) ----
  function setItemCover(url) {
    const dz = $('itemCoverDropzone');
    const img = $('itemCoverImg');
    const replace = $('itemCoverReplace');
    const clear = $('itemCoverClear');
    if (url) {
      img.src = url; img.hidden = false; dz.hidden = true;
      if (replace) replace.hidden = false;
      if (clear) clear.hidden = false;
    } else {
      img.removeAttribute('src'); img.hidden = true; dz.hidden = false;
      if (replace) replace.hidden = true;
      if (clear) clear.hidden = true;
    }
  }

  async function importCoverFromUrl() {
    const url = $('coverUrl').value.trim();
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const done = await fetchJSON(`/medias/api/products/${pid}/cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      state.current.product.cover_object_key = done.object_key;
      setCover(done.cover_url + `?_=${Date.now()}`);
      $('coverUrl').value = '';
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function importItemCoverFromUrl() {
    const url = $('itemCoverUrl').value.trim();
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const done = await fetchJSON(`/medias/api/products/${pid}/item-cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      state.pendingItemCover = done.object_key;
      setItemCover(url);  // 先展示原 URL 预览
      $('itemCoverUrl').value = '';
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function uploadItemCover(file) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/item-cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      state.pendingItemCover = boot.object_key;
      const blobUrl = URL.createObjectURL(file);
      setItemCover(blobUrl);
    } catch (e) {
      alert('视频封面上传失败：' + (e.message || ''));
    }
  }

  function clearItemCover() {
    state.pendingItemCover = null;
    setItemCover(null);
  }

  async function uploadCover(file) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      const done = await fetchJSON(`/medias/api/products/${pid}/cover/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key }),
      });
      state.current.product.cover_object_key = boot.object_key;
      setCover(done.cover_url + `?_=${Date.now()}`);
    } catch (e) {
      alert('封面上传失败：' + (e.message || ''));
    }
  }

  // ---------- Items ----------
  function renderItems(items) {
    const g = $('itemsGrid');
    g.innerHTML = items.map(it => `
      <div class="oc-item" data-item="${it.id}">
        <div class="thumb">
          ${it.thumbnail_url
            ? `<img src="${escapeHtml(it.thumbnail_url)}" loading="lazy" alt="">`
            : `<div class="thumb-ph">${icon('film', 20)}</div>`}
          <button class="rm" type="button" aria-label="删除">${icon('close', 12)}</button>
        </div>
        <div class="name" title="${escapeHtml(it.display_name || it.filename)}">${escapeHtml(it.display_name || it.filename)}</div>
      </div>
    `).join('');
    g.querySelectorAll('[data-item]').forEach(card => {
      card.querySelector('.rm').addEventListener('click', () => removeItem(+card.dataset.item, card));
    });
    $('itemsBadge').textContent = items.length;
  }

  async function removeItem(itemId, card) {
    if (!confirm('确认删除该素材？')) return;
    await fetch('/medias/api/items/' + itemId, { method: 'DELETE' });
    card.remove();
    $('itemsBadge').textContent = document.querySelectorAll('.oc-item').length;
  }

  async function ensureProductIdForUpload() {
    if (state.current && state.current.product && state.current.product.id) return state.current.product.id;
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) { alert('请先填写产品名称'); $('mName').focus(); return null; }
    if (!SLUG_RE.test(code)) { alert('请先填写合法的产品 ID（小写字母/数字/连字符，3–64）'); $('mCode').focus(); return null; }
    try {
      const res = await fetchJSON('/medias/api/products', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, product_code: code }),
      });
      const full = await fetchJSON('/medias/api/products/' + res.id);
      state.current = full;
      $('modalTitle').textContent = '编辑产品素材';
      return res.id;
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('mCode').focus(); }
      else alert('创建失败：' + msg);
      return null;
    }
  }

  async function uploadVideo(file) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    const box = $('uploadProgress');
    const row = document.createElement('div');
    row.className = 'oc-upload-row';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>上传中…</span>`;
    box.appendChild(row);
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          object_key: boot.object_key,
          filename: file.name,
          file_size: file.size,
          cover_object_key: state.pendingItemCover || null,
        }),
      });
      state.pendingItemCover = null;
      setItemCover(null);
      row.className = 'oc-upload-row ok';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>完成</span>`;
    } catch (e) {
      row.className = 'oc-upload-row err';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>失败：${escapeHtml(e.message || '')}</span>`;
    }
    const full = await fetchJSON('/medias/api/products/' + pid);
    state.current = full;
    renderItems(full.items);
    loadList();
  }

  async function save() {
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) { alert('产品名称必填'); $('mName').focus(); return; }
    if (!SLUG_RE.test(code)) { alert('产品 ID 必填且需合法（小写字母/数字/连字符，3–64）'); $('mCode').focus(); return; }
    if (!state.current || !state.current.product || !state.current.product.cover_object_key) {
      alert('请上传封面图'); return;
    }
    if (!document.querySelectorAll('.oc-item').length) {
      alert('请至少上传 1 条视频素材'); return;
    }
    const pid = state.current.product.id;
    try {
      await fetchJSON('/medias/api/products/' + pid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, product_code: code,
          cover_object_key: state.current.product.cover_object_key,
          copywritings: collectCopywritings(),
        }),
      });
      hideModal();
      loadList();
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('mCode').focus(); }
      else alert('保存失败：' + msg);
    }
  }

  // ---------- Copywritings ----------
  function renderCopywritings(list) {
    const box = $('cwList');
    box.innerHTML = '';
    list.forEach((c, i) => box.appendChild(cwCard(c, i + 1)));
    updateCwBadge();
  }

  function updateCwBadge() {
    $('cwBadge').textContent = document.querySelectorAll('.oc-cw').length;
  }

  function cwCard(c, idx) {
    const d = document.createElement('div');
    d.className = 'oc-cw';
    d.innerHTML = `
      <button class="oc-icon-btn rm" type="button" aria-label="删除该条">${icon('close', 14)}</button>
      <div class="idx">#${idx}</div>
      <div class="stack">
        <textarea class="oc-textarea" data-field="body" placeholder="请输入文案"></textarea>
      </div>
    `;
    d.querySelector('[data-field="body"]').value = c.body || '';
    d.querySelector('.rm').addEventListener('click', () => { d.remove(); reindexCw(); });
    return d;
  }

  function reindexCw() {
    [...document.querySelectorAll('.oc-cw .idx')].forEach((e, i) => e.textContent = '#' + (i + 1));
    updateCwBadge();
  }

  function collectCopywritings() {
    return [...document.querySelectorAll('.oc-cw')].map(card => {
      const o = {};
      card.querySelectorAll('[data-field]').forEach(el => { o[el.dataset.field] = el.value || null; });
      return o;
    });
  }

  // ========== Edit Detail Modal ==========
  const edState = { current: null, activeLang: 'en', productData: null };

  function edShow() { $('edMask').hidden = false; }
  function edHide() {
    $('edMask').hidden = true;
    edState.current = null;
    edState.activeLang = 'en';
    edState.productData = null;
  }

  async function openEditDetail(pid) {
    try {
      await ensureLanguages();
      const data = await fetchJSON('/medias/api/products/' + pid);
      edState.current = data;
      edState.productData = data;
      edState.activeLang = 'en';
      $('edName').value = data.product.name || '';
      $('edCode').value = data.product.product_code || '';
      $('edUploadProgress').innerHTML = '';
      edShow();
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('加载失败：' + (e.message || e));
    }
  }

  // --- 语种 tallies（用于 badge） ---
  function edLangTallies(lang) {
    const d = edState.productData;
    if (!d) return { items: 0, copy: 0, cover: false };
    const items = (d.items || []).filter(it => it.lang === lang).length;
    const copyList = d.copywritings;
    let copy = 0;
    if (Array.isArray(copyList)) {
      copy = copyList.filter(c => c.lang === lang).length;
    } else if (copyList && typeof copyList === 'object') {
      copy = (copyList[lang] || []).length;
    }
    const cover = !!(d.covers && d.covers[lang]);
    return { items, copy, cover };
  }

  function edRenderLangTabs() {
    const box = $('edLangTabs');
    if (!box) return;
    box.innerHTML = LANGUAGES.map(l => {
      const t = edLangTallies(l.code);
      // badge: 视频数 0 → 红色；>0 → 绿色；所有语种统一显示
      const badgeCls = t.items > 0 ? 'badge has' : 'badge';
      const badgeHtml = `<span class="${badgeCls}">${t.items}</span>`;
      const active = edState.activeLang === l.code ? ' active' : '';
      return `<button class="oc-lang-tab${active}" data-lang="${escapeHtml(l.code)}" title="${escapeHtml(l.name_zh || l.code)}">`
           + `${l.code.toUpperCase()}${badgeHtml}`
           + `</button>`;
    }).join('');
    box.querySelectorAll('[data-lang]').forEach(btn => {
      btn.addEventListener('click', () => edSwitchLang(btn.dataset.lang));
    });
  }

  function edSwitchLang(lang) {
    // 切换前保存当前语种文案到 productData（从 DOM 读取）
    edFlushCopywritings();
    edState.activeLang = lang;
    edRenderLangTabs();
    edRenderActiveLangView();
  }

  function edRenderActiveLangView() {
    const lang = edState.activeLang;
    // 更新语种标签提示
    const cwLabel = $('edCwLangLabel');
    const itemsLabel = $('edItemsLangLabel');
    const langName = (LANGUAGES.find(l => l.code === lang) || {}).name_zh || lang.toUpperCase();
    if (cwLabel) cwLabel.textContent = `(${langName})`;
    if (itemsLabel) itemsLabel.textContent = `(${langName})`;

    edRenderCoverBlock(lang);
    edRenderItemsBlock(lang);
    edRenderCopyBlock(lang);

    // EN 主图校验 + 保存按钮
    const hasEn = !!(edState.productData && edState.productData.covers && edState.productData.covers['en']);
    const warn = $('edEnCoverWarn');
    if (warn) warn.hidden = hasEn;
    const saveBtn = $('edSaveBtn');
    if (saveBtn) {
      saveBtn.disabled = !hasEn;
      saveBtn.title = hasEn ? '' : '必须先上传英文主图';
    }
  }

  // --- 主图块（按语种渲染） ---
  function edRenderCoverBlock(lang) {
    const block = $('edCoverBlock');
    if (!block) return;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const covers = (edState.productData && edState.productData.covers) || {};
    const hasKey = !!covers[lang];
    const coverUrl = hasKey ? `/medias/cover/${pid}?lang=${lang}&_=${Date.now()}` : null;
    const isEn = lang === 'en';
    const deleteBtn = isEn ? '' :
      `<button type="button" class="oc-btn text sm" id="edCoverDeleteBtn" style="color:var(--oc-danger-fg)">删除此语种主图</button>`;
    const fallbackHint = !isEn && !hasKey
      ? `<p class="oc-cover-fallback-hint">未上传时将展示 EN 主图</p>`
      : '';

    block.innerHTML = `
      <div class="oc-cover-row">
        <div id="edCoverBox" class="oc-cover-square-480">
          <div id="edCoverDropzone" class="cover-dz" tabindex="0" role="button" aria-label="上传产品主图">
            <div class="dz-icon"><svg width="18" height="18"><use href="#ic-upload"/></svg></div>
            <div class="dz-title">点击或拖拽上传</div>
            <div class="dz-hint">JPG / PNG / WebP</div>
          </div>
          <img id="edCoverImg" alt="主图" ${coverUrl ? `src="${escapeHtml(coverUrl)}"` : 'hidden'}>
        </div>
        <div class="oc-cover-actions">
          <button type="button" class="oc-btn ghost sm" id="edCoverReplace">更换主图</button>
          ${deleteBtn}
          <div class="oc-url-row" style="margin-top:var(--oc-sp-2)">
            <input type="url" id="edCoverUrl" class="oc-input sm" placeholder="粘贴图片 URL 导入…">
            <button type="button" class="oc-btn ghost sm" id="edCoverFromUrlBtn">从 URL 导入</button>
          </div>
          <input type="file" id="edCoverInput" accept="image/*" hidden>
          ${fallbackHint}
        </div>
      </div>`;

    // 同步显示状态
    if (coverUrl) {
      const dz = $('edCoverDropzone');
      if (dz) dz.hidden = true;
    }

    // 重新绑定事件
    const coverDropzone = $('edCoverDropzone');
    const coverInput = $('edCoverInput');
    const coverReplace = $('edCoverReplace');
    const coverFromUrl = $('edCoverFromUrlBtn');
    const coverDelete = $('edCoverDeleteBtn');

    if (coverDropzone) {
      coverDropzone.addEventListener('click', () => coverInput && coverInput.click());
      coverDropzone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); coverInput && coverInput.click(); } });
      coverDropzone.addEventListener('dragover', (e) => { e.preventDefault(); coverDropzone.classList.add('drag'); });
      coverDropzone.addEventListener('dragleave', () => coverDropzone.classList.remove('drag'));
      coverDropzone.addEventListener('drop', (e) => {
        e.preventDefault(); coverDropzone.classList.remove('drag');
        const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
        if (f) edUploadCover(f, lang);
      });
      coverDropzone.addEventListener('paste', (e) => {
        const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
        if (item) { e.preventDefault(); edUploadCover(item.getAsFile(), lang); }
      });
    }
    if (coverReplace) coverReplace.addEventListener('click', () => coverInput && coverInput.click());
    if (coverInput) {
      coverInput.addEventListener('change', (e) => {
        const f = e.target.files[0]; e.target.value = '';
        if (f) edUploadCover(f, lang);
      });
    }
    if (coverFromUrl) coverFromUrl.addEventListener('click', () => edImportCoverFromUrl(lang));
    const urlInput = $('edCoverUrl');
    if (urlInput) urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); edImportCoverFromUrl(lang); } });
    if (coverDelete) coverDelete.addEventListener('click', () => edDeleteCover(lang));
  }

  function edSetCoverUI(url) {
    const dz = $('edCoverDropzone');
    const img = $('edCoverImg');
    if (!dz || !img) return;
    if (url) { img.src = url; img.hidden = false; dz.hidden = true; }
    else { img.removeAttribute('src'); img.hidden = true; dz.hidden = false; }
  }

  async function edUploadCover(file, lang) {
    lang = lang || edState.activeLang;
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, lang }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      await fetchJSON(`/medias/api/products/${pid}/cover/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key, lang }),
      });
      // 重拉数据刷新视图
      const fresh = await fetchJSON('/medias/api/products/' + pid);
      edState.current = fresh;
      edState.productData = fresh;
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('封面上传失败：' + (e.message || ''));
    }
  }

  async function edImportCoverFromUrl(lang) {
    lang = lang || edState.activeLang;
    const urlInput = $('edCoverUrl');
    const url = urlInput ? urlInput.value.trim() : '';
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      await fetchJSON(`/medias/api/products/${pid}/cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, lang }),
      });
      if (urlInput) urlInput.value = '';
      const fresh = await fetchJSON('/medias/api/products/' + pid);
      edState.current = fresh;
      edState.productData = fresh;
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function edDeleteCover(lang) {
    if (lang === 'en') { alert('EN 主图不可删除'); return; }
    if (!confirm(`确认删除 ${lang.toUpperCase()} 语种主图？`)) return;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      await fetchJSON(`/medias/api/products/${pid}/cover?lang=${lang}`, { method: 'DELETE' });
      const fresh = await fetchJSON('/medias/api/products/' + pid);
      edState.current = fresh;
      edState.productData = fresh;
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('删除失败：' + (e.message || ''));
    }
  }

  // --- 视频素材块（按 activeLang 过滤） ---
  function edRenderItemsBlock(lang) {
    const allItems = (edState.productData && edState.productData.items) || [];
    const filtered = allItems.filter(it => it.lang === lang);
    edRenderItems(filtered);
  }

  // --- 文案块（按 activeLang 过滤） ---
  function edRenderCopyBlock(lang) {
    const raw = (edState.productData && edState.productData.copywritings) || [];
    let list = [];
    if (Array.isArray(raw)) {
      list = raw.filter(c => c.lang === lang);
    } else if (raw && typeof raw === 'object') {
      list = (raw[lang] || []);
    }
    edRenderCopywritings(list);
  }

  // 切换语种前把当前 DOM 文案写回 productData
  function edFlushCopywritings() {
    const lang = edState.activeLang;
    const items = [...$('edCwList').children].map(card => ({
      lang,
      body: card.querySelector('[data-field="body"]').value || null,
    }));
    // 确保 productData.copywritings 是 array 格式（按 lang 存储）
    if (!edState.productData) return;
    const raw = edState.productData.copywritings || [];
    let arr = Array.isArray(raw) ? raw : [];
    // 移除当前语种旧数据，写入新数据
    arr = arr.filter(c => c.lang !== lang);
    arr = arr.concat(items);
    edState.productData.copywritings = arr;
  }

  async function edUploadVideo(file) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    const lang = edState.activeLang;
    const box = $('edUploadProgress');
    const row = document.createElement('div');
    row.className = 'oc-upload-row';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>上传中…</span>`;
    box.appendChild(row);
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key, filename: file.name, file_size: file.size, lang }),
      });
      row.className = 'oc-upload-row ok';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>完成</span>`;
    } catch (e) {
      row.className = 'oc-upload-row err';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>失败：${escapeHtml(e.message || '')}</span>`;
    }
    const full = await fetchJSON('/medias/api/products/' + pid);
    edState.current = full;
    edState.productData = full;
    edRenderLangTabs();
    edRenderActiveLangView();
    loadList();
  }

  function edRenderCopywritings(list) {
    const box = $('edCwList');
    box.innerHTML = '';
    (list || []).forEach((c, i) => box.appendChild(edCwCard(c, i + 1)));
    $('edCwBadge').textContent = box.children.length;
  }

  function edCwCard(c, idx) {
    const d = document.createElement('div');
    d.className = 'oc-cw';
    d.innerHTML = `
      <button class="oc-icon-btn rm" type="button" aria-label="删除该条">${icon('close', 14)}</button>
      <div class="idx">#${idx}</div>
      <div class="stack">
        <textarea class="oc-textarea" data-field="body" placeholder="请输入文案"></textarea>
      </div>
    `;
    d.querySelector('[data-field="body"]').value = (c && c.body) || '';
    d.querySelector('.rm').addEventListener('click', () => {
      d.remove();
      [...$('edCwList').children].forEach((e, i) => {
        const el = e.querySelector('.idx'); if (el) el.textContent = '#' + (i + 1);
      });
      $('edCwBadge').textContent = $('edCwList').children.length;
    });
    return d;
  }

  function edCollectCopywritings() {
    return [...$('edCwList').children].map(card => ({
      body: card.querySelector('[data-field="body"]').value || null,
    }));
  }

  function edRenderItems(items) {
    const g = $('edItemsGrid');
    g.innerHTML = (items || []).map(it => {
      const cover = it.cover_url || it.thumbnail_url;
      const name = escapeHtml(it.display_name || it.filename);
      const imgTag = cover
        ? `<img src="${escapeHtml(cover)}?_=${Date.now()}" loading="lazy" alt="">`
        : `<div class="thumb-ph">${icon('film', 20)}</div>`;
      return `
      <div class="oc-vitem" data-item="${it.id}">
        <div class="vname" title="${name}">${name}</div>
        <div class="vtabs">
          <button type="button" class="vtab active" data-tab="img">图片</button>
          <button type="button" class="vtab" data-tab="video">视频</button>
        </div>
        <div class="vbody">
          <div class="vpane active" data-pane="img">${imgTag}</div>
          <div class="vpane" data-pane="video">
            <div class="vvideo-ph">点击"视频"标签后加载播放</div>
          </div>
        </div>
        <div class="vactions">
          <button class="oc-btn text sm" data-act="cover">${icon('edit', 12)}<span>换封面</span></button>
          <button class="oc-btn text sm danger-txt" data-act="del">${icon('trash', 12)}<span>删除</span></button>
        </div>
      </div>`;
    }).join('');
    g.querySelectorAll('[data-item]').forEach(card => {
      const id = +card.dataset.item;
      const tabs = card.querySelectorAll('.vtab');
      const panes = card.querySelectorAll('.vpane');
      tabs.forEach(t => t.addEventListener('click', () => {
        tabs.forEach(x => x.classList.toggle('active', x === t));
        panes.forEach(p => p.classList.toggle('active', p.dataset.pane === t.dataset.tab));
        if (t.dataset.tab === 'video') edEnsureVideoLoaded(card, id);
      }));
      card.querySelector('[data-act="del"]').addEventListener('click', () => edRemoveItem(id, card));
      card.querySelector('[data-act="cover"]').addEventListener('click', () => edPickItemCover(id));
    });
    $('edItemsBadge').textContent = (items || []).length;
  }

  async function edEnsureVideoLoaded(card, itemId) {
    const pane = card.querySelector('[data-pane="video"]');
    if (pane.dataset.loaded === '1') return;
    pane.innerHTML = `<div class="vvideo-ph">加载中…</div>`;
    try {
      const r = await fetchJSON(`/medias/api/items/${itemId}/play_url`);
      pane.innerHTML = `<video controls preload="metadata" src="${escapeHtml(r.url)}"></video>`;
      pane.dataset.loaded = '1';
    } catch (e) {
      pane.innerHTML = `<div class="vvideo-ph err">加载失败：${escapeHtml(e.message || '')}</div>`;
    }
  }

  function edPickItemCover(itemId) {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = 'image/*';
    picker.onchange = (e) => {
      const f = e.target.files[0];
      if (f) edUploadItemCover(itemId, f);
    };
    picker.click();
  }

  async function edUploadItemCover(itemId, file) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) return;
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/item-cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      await fetchJSON(`/medias/api/items/${itemId}/cover/set`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_key: boot.object_key }),
      });
      const full = await fetchJSON('/medias/api/products/' + pid);
      edState.current = full;
      edState.productData = full;
      edRenderLangTabs();
      edRenderActiveLangView();
    } catch (e) {
      alert('视频封面上传失败：' + (e.message || ''));
    }
  }

  async function edRemoveItem(itemId, card) {
    if (!confirm('确认删除该素材？')) return;
    await fetch('/medias/api/items/' + itemId, { method: 'DELETE' });
    card.remove();
    $('edItemsBadge').textContent = $('edItemsGrid').children.length;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (pid) {
      const full = await fetchJSON('/medias/api/products/' + pid);
      edState.current = full;
      edState.productData = full;
      edRenderLangTabs();
      edRenderActiveLangView();
    }
  }

  async function edSave() {
    const name = $('edName').value.trim();
    const code = $('edCode').value.trim().toLowerCase();
    if (!name) { alert('产品名称必填'); $('edName').focus(); return; }
    if (!SLUG_RE.test(code)) { alert('产品 ID 必填且需合法（小写字母/数字/连字符，3–64）'); $('edCode').focus(); return; }

    // EN 主图硬校验
    const hasEn = !!(edState.productData && edState.productData.covers && edState.productData.covers['en']);
    if (!hasEn) { alert('必须先上传英文（EN）产品主图才能保存'); return; }

    // 保存前 flush 当前语种文案
    edFlushCopywritings();

    const pid = edState.productData.product.id;

    // 将 copywritings array 转为 dict {lang: [{body},...]}，保存前过滤空 body
    const rawList = (Array.isArray(edState.productData.copywritings)
      ? edState.productData.copywritings
      : []).filter(c => (c.body || '').trim());
    const cwDict = {};
    rawList.forEach(c => {
      if (!c.lang || !c.body) return;
      if (!cwDict[c.lang]) cwDict[c.lang] = [];
      cwDict[c.lang].push({ body: c.body });
    });

    const payload = {
      name,
      product_code: code,
      copywritings: cwDict,
    };
    try {
      await fetchJSON('/medias/api/products/' + pid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      edHide();
      loadList();
    } catch (e) {
      const msg = (e.message || '').toString();
      if (msg.includes('已被占用')) { alert('产品 ID 已被占用'); $('edCode').focus(); }
      else alert('保存失败：' + msg);
    }
  }

  // ---------- Events ----------
  document.addEventListener('DOMContentLoaded', () => {
    $('searchBtn').addEventListener('click', () => { state.page = 1; loadList(); });
    $('kw').addEventListener('keydown', (e) => { if (e.key === 'Enter') { state.page = 1; loadList(); } });

    const syncChip = (chipId, inputId) => {
      const chip = $(chipId), inp = $(inputId);
      if (!chip || !inp) return;
      const sync = () => chip.classList.toggle('on', inp.checked);
      inp.addEventListener('change', () => { sync(); state.page = 1; loadList(); });
      sync();
    };
    syncChip('chipArchived', 'archived');
    syncChip('chipScope', 'scopeAll');

    $('createBtn').addEventListener('click', openCreate);
    $('modalClose').addEventListener('click', hideModal);
    $('cancelBtn').addEventListener('click', hideModal);
    $('saveBtn').addEventListener('click', save);
    $('editMask').addEventListener('click', (e) => { if (e.target.id === 'editMask') hideModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('editMask').hidden) hideModal(); });

    $('cwAddBtn').addEventListener('click', () => {
      $('cwList').appendChild(cwCard({}, $('cwList').children.length + 1));
      updateCwBadge();
    });

    const cdz = $('coverDropzone');
    cdz.addEventListener('click', () => $('coverInput').click());
    cdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('coverInput').click(); } });
    cdz.addEventListener('dragover', (e) => { e.preventDefault(); cdz.classList.add('drag'); });
    cdz.addEventListener('dragleave', () => cdz.classList.remove('drag'));
    cdz.addEventListener('drop', (e) => {
      e.preventDefault(); cdz.classList.remove('drag');
      const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
      if (f) uploadCover(f);
    });
    $('coverReplace').addEventListener('click', () => $('coverInput').click());
    $('coverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) uploadCover(f);
    });

    $('coverFromUrlBtn').addEventListener('click', importCoverFromUrl);
    $('coverUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); importCoverFromUrl(); } });

    // 粘贴图片到 产品主图 dropzone
    cdz.addEventListener('paste', (e) => {
      const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
      if (item) { e.preventDefault(); uploadCover(item.getAsFile()); }
    });

    // 视频封面图（add modal, 等待 /items/complete 时带过去）
    const icdz = $('itemCoverDropzone');
    icdz.addEventListener('click', () => $('itemCoverInput').click());
    icdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('itemCoverInput').click(); } });
    icdz.addEventListener('dragover', (e) => { e.preventDefault(); icdz.classList.add('drag'); });
    icdz.addEventListener('dragleave', () => icdz.classList.remove('drag'));
    icdz.addEventListener('drop', (e) => {
      e.preventDefault(); icdz.classList.remove('drag');
      const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
      if (f) uploadItemCover(f);
    });
    $('itemCoverReplace').addEventListener('click', () => $('itemCoverInput').click());
    $('itemCoverClear').addEventListener('click', clearItemCover);
    $('itemCoverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) uploadItemCover(f);
    });
    $('itemCoverFromUrlBtn').addEventListener('click', importItemCoverFromUrl);
    $('itemCoverUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); importItemCoverFromUrl(); } });
    icdz.addEventListener('paste', (e) => {
      const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
      if (item) { e.preventDefault(); uploadItemCover(item.getAsFile()); }
    });

    const dz = $('dropzone');
    dz.addEventListener('click', () => $('fileInput').click());
    dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('fileInput').click(); } });
    dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
    dz.addEventListener('drop', (e) => {
      e.preventDefault(); dz.classList.remove('drag');
      const file = [...(e.dataTransfer.files || [])]
        .find(f => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(f.name));
      if (file) uploadVideo(file);
    });
    $('fileInput').addEventListener('change', (e) => {
      const file = e.target.files[0]; e.target.value = '';
      if (file) uploadVideo(file);
    });

    // ---- Edit detail modal wiring ----
    $('edClose').addEventListener('click', edHide);
    $('edCancelBtn').addEventListener('click', edHide);
    $('edSaveBtn').addEventListener('click', edSave);
    $('edMask').addEventListener('click', (e) => { if (e.target.id === 'edMask') edHide(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('edMask').hidden) edHide(); });

    // edCwAddBtn：按当前 activeLang 添加文案条目
    $('edCwAddBtn').addEventListener('click', () => {
      $('edCwList').appendChild(edCwCard({ lang: edState.activeLang }, $('edCwList').children.length + 1));
      $('edCwBadge').textContent = $('edCwList').children.length;
    });

    // 编辑弹窗封面事件由 edRenderCoverBlock() 动态绑定，此处不再静态绑定

    const edVdz = $('edVideoDropzone');
    edVdz.addEventListener('click', () => $('edVideoInput').click());
    edVdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('edVideoInput').click(); } });
    edVdz.addEventListener('dragover', (e) => { e.preventDefault(); edVdz.classList.add('drag'); });
    edVdz.addEventListener('dragleave', () => edVdz.classList.remove('drag'));
    edVdz.addEventListener('drop', (e) => {
      e.preventDefault(); edVdz.classList.remove('drag');
      const file = [...(e.dataTransfer.files || [])]
        .find(f => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(f.name));
      if (file) edUploadVideo(file);
    });
    $('edVideoInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) edUploadVideo(f);
    });

    loadList();
  });
})();
