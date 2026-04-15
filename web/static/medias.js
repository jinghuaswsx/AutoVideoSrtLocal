(function() {
  const state = { page: 1, current: null };
  const $ = (id) => document.getElementById(id);

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
    grid.innerHTML = items.map(cardHTML).join('');
    grid.querySelectorAll('[data-pid]').forEach(el => {
      const pid = +el.dataset.pid;
      el.addEventListener('click', (e) => {
        if (e.target.closest('.menu-btn') || e.target.closest('.oc-menu-pop')) return;
        openEdit(pid);
      });
    });
    grid.querySelectorAll('.menu-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        closeAllMenus();
        const pop = btn.nextElementSibling;
        if (pop) pop.classList.add('open');
      });
    });
    grid.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); closeAllMenus(); openEdit(+b.dataset.edit); }));
    grid.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); closeAllMenus(); deleteProduct(+b.dataset.del); }));
  }

  function cardHTML(p) {
    const cover = p.cover_thumbnail_url
      ? `<img src="${escapeHtml(p.cover_thumbnail_url)}" alt="" loading="lazy">`
      : `<div class="cover-ph">${icon('film', 32)}</div>`;
    const tags = [];
    if (p.source) tags.push(`<span class="oc-tag on-cover">${escapeHtml(p.source)}</span>`);
    tags.push(`<span class="oc-tag on-cover">${icon('film', 11)} ${p.items_count || 0}</span>`);

    return `
      <article class="oc-card" data-pid="${p.id}" tabindex="0">
        <div class="cover">
          ${cover}
          <div class="cover-tags">${tags.join('')}</div>
          <button class="menu-btn" type="button" aria-label="更多操作">${icon('more', 16)}</button>
          <div class="oc-menu-pop">
            <button data-edit="${p.id}">${icon('edit', 14)}<span>编辑</span></button>
            <button class="danger" data-del="${p.id}">${icon('trash', 14)}<span>删除</span></button>
          </div>
        </div>
        <div class="info">
          <div class="name">${escapeHtml(p.name)}</div>
          <div class="color">${p.color_people ? escapeHtml(p.color_people) : '未设置色号 / 代言人'}</div>
          <div class="meta">
            <span>${fmtDate(p.created_at)}</span>
            <span>#${p.id}</span>
          </div>
        </div>
      </article>`;
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
    $('modalTitle').textContent = '添加产品素材';
    $('mName').value = '';
    $('mCode').value = '';
    setCover(null);
    renderCopywritings([]);
    renderItems([]);
    $('uploadProgress').innerHTML = '';
    showModal();
    setTimeout(() => $('mName').focus(), 80);
  }

  async function openEdit(pid) {
    try {
      const data = await fetchJSON('/medias/api/products/' + pid);
      state.current = data;
      $('modalTitle').textContent = '编辑产品素材';
      $('mName').value = data.product.name || '';
      $('mCode').value = data.product.product_code || '';
      setCover(data.product.cover_object_key ? `/medias/cover/${pid}?_=${Date.now()}` : null);
      renderCopywritings(data.copywritings);
      renderItems(data.items);
      $('uploadProgress').innerHTML = '';
      showModal();
    } catch (e) {
      alert('加载失败：' + (e.message || e));
    }
  }

  // ---------- Cover ----------
  const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

  function setCover(url) {
    const dz = $('coverDropzone');
    const pv = $('coverPreview');
    if (url) {
      $('coverImg').src = url;
      pv.hidden = false; dz.hidden = true;
    } else {
      $('coverImg').removeAttribute('src');
      pv.hidden = true; dz.hidden = false;
    }
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
        body: JSON.stringify({ object_key: boot.object_key, filename: file.name, file_size: file.size }),
      });
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
        <input class="oc-input" data-field="title" placeholder="标题">
        <textarea class="oc-textarea" data-field="body" placeholder="正文"></textarea>
        <input class="oc-input" data-field="description" placeholder="描述">
        <input class="oc-input" data-field="ad_carrier" placeholder="广告媒体库">
        <textarea class="oc-textarea" data-field="ad_copy" placeholder="广告文案"></textarea>
        <input class="oc-input" data-field="ad_keywords" placeholder="广告词">
      </div>
    `;
    d.querySelector('[data-field="title"]').value = c.title || '';
    d.querySelector('[data-field="body"]').value = c.body || '';
    d.querySelector('[data-field="description"]').value = c.description || '';
    d.querySelector('[data-field="ad_carrier"]').value = c.ad_carrier || '';
    d.querySelector('[data-field="ad_copy"]').value = c.ad_copy || '';
    d.querySelector('[data-field="ad_keywords"]').value = c.ad_keywords || '';
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

    loadList();
  });
})();
