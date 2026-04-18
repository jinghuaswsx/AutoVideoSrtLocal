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
    if (!res.ok) {
      let msg;
      try {
        const data = await res.json();
        msg = data.error || data.message || JSON.stringify(data);
      } catch {
        msg = await res.text();
      }
      throw new Error(msg);
    }
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

  // ---------- 商品详情图（通用控制器） ----------
  // 在"添加产品"与"编辑产品"两个弹窗里都复用。
  function createDetailImagesController(opts) {
    const section = $(opts.section);
    const grid    = $(opts.grid);
    const input   = $(opts.input);
    const pickBtn = $(opts.pickBtn);
    const badge   = $(opts.badge);
    const progressBox = $(opts.progress);
    const getLang   = opts.getLang   || (() => 'en');
    const ensurePid = opts.ensurePid || (async () => null);
    let items = [];

    function show() { if (section) section.hidden = false; }
    function hide() { if (section) section.hidden = true; }

    function renderGrid() {
      if (!grid) return;
      if (!items.length) {
        grid.innerHTML = '<div class="oc-detail-images-empty">尚未上传详情图</div>';
      } else {
        grid.innerHTML = items.map((it, idx) => `
          <div class="oc-detail-image" data-id="${it.id}">
            <img src="${escapeHtml(it.thumbnail_url)}" alt="详情图 ${idx + 1}" loading="lazy">
            <span class="oc-detail-image-idx">${idx + 1}</span>
            <button class="oc-detail-image-del" type="button" title="删除这张" aria-label="删除">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
                   stroke-width="1.8" stroke-linecap="round">
                <path d="M3 3l8 8M11 3l-8 8"></path>
              </svg>
            </button>
          </div>
        `).join('');
        grid.querySelectorAll('.oc-detail-image-del').forEach(btn => {
          btn.addEventListener('click', onDelete);
        });
      }
      if (badge) badge.textContent = String(items.length);
    }

    async function onDelete(e) {
      e.stopPropagation();
      const card = e.currentTarget.closest('.oc-detail-image');
      if (!card) return;
      const imgId = parseInt(card.dataset.id, 10);
      if (!imgId) return;
      if (!window.confirm('确定删除这张详情图？')) return;
      const pid = await ensurePid({ allowCreate: false });
      if (!pid) return;
      try {
        await fetchJSON(`/medias/api/products/${pid}/detail-images/${imgId}`, {
          method: 'DELETE',
        });
        items = items.filter(x => x.id !== imgId);
        renderGrid();
      } catch (err) {
        alert('删除失败：' + (err.message || err));
      }
    }

    function setProgress(text) {
      if (!progressBox) return;
      if (!text) { progressBox.hidden = true; progressBox.textContent = ''; return; }
      progressBox.hidden = false;
      progressBox.textContent = text;
    }

    async function uploadFiles(rawFiles) {
      if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
      const files = [...(rawFiles || [])]
        .filter(f => /^image\/(jpeg|png|webp)$/i.test(f.type));
      if (!files.length) { alert('请选择 JPG / PNG / WebP 图片'); return; }
      if (files.length > 20) {
        alert(`单次最多上传 20 张，当前选择了 ${files.length} 张，只取前 20 张`);
        files.length = 20;
      }
      const pid = await ensurePid({ allowCreate: true });
      if (!pid) return;
      const lang = getLang();

      setProgress(`准备上传 ${files.length} 张…`);
      try {
        const boot = await fetchJSON(
          `/medias/api/products/${pid}/detail-images/bootstrap`,
          {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              lang,
              files: files.map(f => ({
                filename: f.name, content_type: f.type, size: f.size,
              })),
            }),
          });
        if (!boot.uploads || boot.uploads.length !== files.length) {
          throw new Error('后端返回的上传位数量不匹配');
        }

        for (let i = 0; i < files.length; i++) {
          const f = files[i];
          const u = boot.uploads[i];
          setProgress(`上传中 ${i + 1} / ${files.length}：${f.name}`);
          const putRes = await fetch(u.upload_url, {
            method: 'PUT',
            headers: { 'Content-Type': f.type || 'image/jpeg' },
            body: f,
          });
          if (!putRes.ok) throw new Error(`TOS 上传失败 (${i + 1}/${files.length})`);
        }

        setProgress('登记中…');
        const done = await fetchJSON(
          `/medias/api/products/${pid}/detail-images/complete`,
          {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              lang,
              images: boot.uploads.map((u, i) => ({
                object_key: u.object_key,
                content_type: files[i].type,
                file_size: files[i].size,
              })),
            }),
          });
        items = items.concat(done.items || []);
        renderGrid();
        setProgress('');
      } catch (err) {
        setProgress('上传失败：' + (err.message || err));
        setTimeout(() => setProgress(''), 3500);
      }
    }

    async function load(pid) {
      items = [];
      renderGrid();
      if (!pid) return;
      try {
        const lang = getLang();
        const data = await fetchJSON(
          `/medias/api/products/${pid}/detail-images?lang=${encodeURIComponent(lang)}`,
        );
        items = data.items || [];
        renderGrid();
      } catch (err) {
        console.error('[detail-images] load failed', err);
      }
    }

    function reset() {
      items = [];
      renderGrid();
      setProgress('');
    }

    if (pickBtn) pickBtn.addEventListener('click', () => input && input.click());
    if (input) input.addEventListener('change', (e) => {
      const files = e.target.files;
      e.target.value = '';
      uploadFiles(files);
    });

    return { load, reset, show, hide };
  }

  // ---------- List ----------
  async function loadList() {
    const kw = $('kw').value.trim();
    const archived = $('archived').checked;
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
    if (archived) params.set('archived', '1');
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
          <col style="width:96px">
          <col style="width:120px">
          <col style="width:120px">
          <col style="width:60px">
          <col style="width:200px">
          <col style="width:108px">
          <col style="width:160px">
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
        <td class="name wrap"><a href="#" data-pid="${p.id}" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a></td>
        <td class="mono wrap" title="${escapeHtml(p.product_code || '')}">${p.product_code ? `<a href="https://newjoyloo.com/products/${encodeURIComponent(p.product_code)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.product_code)}</a>` : '<span class="muted">—</span>'}</td>
        <td><span class="oc-pill">${count}</span></td>
        <td>${renderLangBar(p.lang_coverage)}</td>
        <td class="muted">${fmtDate(p.updated_at)}</td>
        <td class="actions">
          <button class="oc-btn sm ghost" data-edit="${p.id}">${icon('edit', 12)}<span>编辑</span></button>
          <button class="bt-row-btn" data-bt-open="${p.id}" data-bt-name="${escapeHtml(p.name)}" title="一键翻译到多语言">🌐 翻译</button>
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
  let mDetailImagesCtrl = null;

  function ensureMDetailImagesCtrl() {
    if (mDetailImagesCtrl) return mDetailImagesCtrl;
    mDetailImagesCtrl = createDetailImagesController({
      section: 'mDetailImagesSection', // 外层 section 无需 hidden 切换，保持可见即可
      grid:    'mDetailImagesGrid',
      input:   'mDetailImagesInput',
      pickBtn: 'mDetailImagesPickBtn',
      badge:   'mDetailImagesBadge',
      progress:'mDetailImagesProgress',
      getLang: () => 'en',
      ensurePid: async (arg) => {
        const cur = state.current && state.current.product;
        if (cur && cur.id) return cur.id;
        if (arg && arg.allowCreate === false) return null;
        return await ensureProductIdForUpload();
      },
    });
    return mDetailImagesCtrl;
  }

  function showModal() { $('editMask').hidden = false; }
  function hideModal() {
    $('editMask').hidden = true;
    state.current = null;
    if (mDetailImagesCtrl) mDetailImagesCtrl.reset();
  }

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
    const ctrl = ensureMDetailImagesCtrl();
    ctrl.reset();
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
  // 注：添加弹窗已不再有 itemCover 区块，节点可能不存在；做 null 守卫。
  function setItemCover(url) {
    const dz = $('itemCoverDropzone');
    const img = $('itemCoverImg');
    if (!dz || !img) return;
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
          ${(it.thumbnail_url || it.cover_url)
            ? `<img src="${escapeHtml(it.thumbnail_url || it.cover_url)}" loading="lazy" alt="">`
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
    const prevCoverKey = state.current && state.current.product && state.current.product.cover_object_key;
    const full = await fetchJSON('/medias/api/products/' + pid);
    state.current = full;
    if (prevCoverKey && !state.current.product.cover_object_key) {
      state.current.product.cover_object_key = prevCoverKey;
    }
    setCover(full.product.cover_thumbnail_url || null);
    renderItems(full.items);
    loadList();
  }

  async function save() {
    const name = $('mName').value.trim();
    const code = $('mCode').value.trim().toLowerCase();
    if (!name) { alert('产品名称必填'); $('mName').focus(); return; }
    if (!SLUG_RE.test(code)) { alert('产品 ID 必填且需合法（小写字母/数字/连字符，3–64）'); $('mCode').focus(); return; }
    if (!state.current || !state.current.product || (!state.current.product.cover_object_key && !state.current.product.has_en_cover)) {
      alert('请上传商品主图'); return;
    }
    const cw = collectCopywritings();
    if (!cw.length) { alert('请填写文案'); $('cwBody') && $('cwBody').focus(); return; }
    const items = (state.current && state.current.items) || [];
    if (!items.length) { alert('请上传至少一条视频素材'); return; }
    const missingCover = items.filter(it => !it.cover_object_key);
    if (missingCover.length) {
      alert('每条视频素材都必须有视频封面图，请先上传封面再选视频源');
      return;
    }
    const pid = state.current.product.id;
    try {
      await fetchJSON('/medias/api/products/' + pid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, product_code: code,
          cover_object_key: state.current.product.cover_object_key,
          copywritings: { en: cw },
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
  // 添加弹窗：文案改为单 textarea。保留函数名/调用签名，内部改为读写 #cwBody；
  // 老数据若有多条则按空行拼接展示。
  function renderCopywritings(list) {
    const box = $('cwBody');
    if (!box) return;
    const parts = (list || [])
      .map(c => (c && c.body ? String(c.body).trim() : ''))
      .filter(Boolean);
    box.value = parts.join('\n\n');
  }

  function collectCopywritings() {
    const el = $('cwBody');
    const text = el ? (el.value || '').trim() : '';
    return text ? [{ body: text }] : [];
  }

  // ========== Edit Detail Modal ==========
  const edState = {
    current: null, activeLang: 'en', productData: null,
    // 新增素材提交大框 - 待上传的视频封面图 TOS object_key
    pendingItemCover: null,
    // 新增素材提交大框 - 待提交的视频 File 对象
    pendingVideoFile: null,
  };

  function edShow() { $('edMask').hidden = false; }
  function edHide() {
    $('edMask').hidden = true;
    edState.current = null;
    edState.activeLang = 'en';
    edState.productData = null;
    edResetNewItemForm();
  }

  let edDetailImagesCtrl = null;

  function ensureEdDetailImagesCtrl() {
    if (edDetailImagesCtrl) return edDetailImagesCtrl;
    edDetailImagesCtrl = createDetailImagesController({
      section: 'edDetailImagesSection',
      grid:    'edDetailImagesGrid',
      input:   'edDetailImagesInput',
      pickBtn: 'edDetailImagesPickBtn',
      badge:   'edDetailImagesBadge',
      progress:'edDetailImagesProgress',
      getLang: () => edState.activeLang,
      ensurePid: async () => {
        const p = edState.productData && edState.productData.product;
        return p ? p.id : null;
      },
    });
    return edDetailImagesCtrl;
  }

  function edRenderAdSupportedLangs(selected) {
    const box = $('edAdSupportedLangsBox');
    if (!box) return;
    const selectedSet = new Set(
      (selected || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
    );
    const langs = (LANGUAGES || []).filter(l => l.code !== 'en');
    if (!langs.length) {
      box.innerHTML = '<span class="oc-hint">暂无可选语种</span>';
      return;
    }
    box.innerHTML = langs.map(l => {
      const checked = selectedSet.has(l.code) ? 'checked' : '';
      return `<label class="oc-lang-checkbox">`
           + `<input type="checkbox" name="ad_supported_langs" value="${escapeHtml(l.code)}" ${checked}/>`
           + `<span>${escapeHtml(l.name_zh || l.code.toUpperCase())}</span>`
           + `</label>`;
    }).join('');
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
      edRenderAdSupportedLangs(data.product.ad_supported_langs || '');
      $('edUploadProgress').innerHTML = '';
      edResetNewItemForm();
      edShow();
      edRenderLangTabs();
      edRenderActiveLangView();
      // 详情图区块：仅英语加载
      const ctrl = ensureEdDetailImagesCtrl();
      ctrl.reset();
      if (edState.activeLang === 'en') {
        ctrl.show();
        await ctrl.load(pid);
      } else {
        ctrl.hide();
      }
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
    edFlushProductUrl();
    edState.activeLang = lang;
    // 切语言时重置"新增素材"大框的待上传状态
    edResetNewItemForm();
    edRenderLangTabs();
    edRenderActiveLangView();
  }

  // --- 产品链接（按 activeLang）---
  function _defaultProductUrl(lang, code) {
    if (!code) return '';
    if (lang === 'en') return `https://newjoyloo.com/products/${code}`;
    return `https://newjoyloo.com/${lang}/products/${code}`;
  }

  function edRenderProductUrl(lang) {
    const input = $('edProductUrl');
    const hint = $('edProductUrlHint');
    if (!input) return;
    const code = ($('edCode').value || '').trim();
    const links = (edState.productData && edState.productData.product
                   && edState.productData.product.localized_links) || {};
    const override = links[lang];
    const def = _defaultProductUrl(lang, code);
    input.value = override || def || '';
    input.placeholder = def || '留空则用默认模板';
    if (hint) {
      const label = (LANGUAGES.find(l => l.code === lang) || {}).name_zh || lang.toUpperCase();
      hint.textContent = override
        ? `（${label} · 已自定义）`
        : `（${label} · 使用默认：${def || '未设置产品 ID'}）`;
    }
  }

  function edFlushProductUrl() {
    const input = $('edProductUrl');
    if (!input || !edState.productData || !edState.productData.product) return;
    const lang = edState.activeLang;
    const code = ($('edCode').value || '').trim();
    const def = _defaultProductUrl(lang, code);
    const val = (input.value || '').trim();
    if (!edState.productData.product.localized_links) {
      edState.productData.product.localized_links = {};
    }
    const links = edState.productData.product.localized_links;
    // 如果用户输入的就是默认值或留空 → 不保存（避免冗余写入）
    if (!val || val === def) delete links[lang];
    else links[lang] = val;
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
    edRenderProductUrl(lang);

    // 商品详情图：仅英语显示入口；切换到其他语种时隐藏，切回英语时重新加载
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const ctrl = ensureEdDetailImagesCtrl();
    if (lang === 'en') {
      ctrl.show();
      if (pid) ctrl.load(pid);
    } else {
      ctrl.hide();
    }

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

  // ---------- 新增素材大框（封面+视频+提交） ----------

  function _fmtFileSize(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  function edSetItemCover(url) {
    const box = $('edItemCoverBox');
    if (!box) return;
    const dz = $('edItemCoverDropzone');
    const img = $('edItemCoverImg');
    const replace = $('edItemCoverReplace');
    const clear = $('edItemCoverClear');
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

  function edSetPickedVideo(file) {
    edState.pendingVideoFile = file || null;
    const empty = $('edVideoPickEmpty');
    const filled = $('edVideoPickFilled');
    if (!empty || !filled) return;
    if (file) {
      empty.hidden = true;
      filled.hidden = false;
      $('edVideoPickName').textContent = file.name;
      $('edVideoPickSize').textContent = _fmtFileSize(file.size);
    } else {
      empty.hidden = false;
      filled.hidden = true;
      $('edVideoPickName').textContent = '';
      $('edVideoPickSize').textContent = '';
    }
  }

  function edResetNewItemForm() {
    edState.pendingItemCover = null;
    edState.pendingVideoFile = null;
    edSetItemCover(null);
    edSetPickedVideo(null);
    const url = $('edItemCoverUrl'); if (url) url.value = '';
  }

  async function edUploadPendingItemCover(file) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    if (!file.type.startsWith('image/')) { alert('请上传图片文件'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) { alert('产品数据未加载'); return; }
    try {
      const boot = await fetchJSON(`/medias/api/products/${pid}/item-cover/bootstrap`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      });
      const putRes = await fetch(boot.upload_url, { method: 'PUT', body: file });
      if (!putRes.ok) throw new Error('TOS 上传失败');
      edState.pendingItemCover = boot.object_key;
      edSetItemCover(URL.createObjectURL(file));
    } catch (e) {
      alert('视频封面上传失败：' + (e.message || ''));
    }
  }

  async function edImportItemCoverFromUrl() {
    const url = ($('edItemCoverUrl').value || '').trim();
    if (!url) { alert('请粘贴图片 URL'); return; }
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) { alert('产品数据未加载'); return; }
    try {
      const done = await fetchJSON(`/medias/api/products/${pid}/item-cover/from-url`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      edState.pendingItemCover = done.object_key;
      edSetItemCover(url);
      $('edItemCoverUrl').value = '';
    } catch (e) {
      alert('从 URL 导入失败：' + (e.message || ''));
    }
  }

  async function edSubmitNewItem() {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    if (!edState.pendingItemCover) {
      alert('请先上传视频封面图');
      $('edItemCoverDropzone') && $('edItemCoverDropzone').focus();
      return;
    }
    if (!edState.pendingVideoFile) {
      alert('请先选择视频源文件');
      $('edVideoPickBox') && $('edVideoPickBox').focus();
      return;
    }
    const file = edState.pendingVideoFile;
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid) { alert('产品数据未加载'); return; }
    const lang = edState.activeLang;
    const box = $('edUploadProgress');
    const row = document.createElement('div');
    row.className = 'oc-upload-row';
    row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>上传中…</span>`;
    box.appendChild(row);
    const submitBtn = $('edItemSubmitBtn');
    if (submitBtn) submitBtn.disabled = true;
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
          cover_object_key: edState.pendingItemCover,
          lang,
        }),
      });
      row.className = 'oc-upload-row ok';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>完成</span>`;
      edResetNewItemForm();
    } catch (e) {
      row.className = 'oc-upload-row err';
      row.innerHTML = `<span class="fname">${escapeHtml(file.name)}</span><span>失败：${escapeHtml(e.message || '')}</span>`;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
    try {
      const full = await fetchJSON('/medias/api/products/' + pid);
      edState.current = full;
      edState.productData = full;
      edRenderLangTabs();
      edRenderActiveLangView();
      loadList();
    } catch {}
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
    const autoBadge = c && c.auto_translated ? (
      c.manually_edited_at
        ? `<span class="bt-row-btn" title="来自英文自动翻译,已人工修改" style="margin-left:6px;cursor:default">🔗 英文译本 · ✏️</span>`
        : `<span class="bt-row-btn" title="来自英文自动翻译" style="margin-left:6px;cursor:default">🔗 英文译本</span>`
    ) : '';
    d.innerHTML = `
      <button class="oc-icon-btn rm" type="button" aria-label="删除该条">${icon('close', 14)}</button>
      <div class="idx">#${idx}${autoBadge}</div>
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

    // 保存前 flush 当前语种文案 + 产品链接
    edFlushCopywritings();
    edFlushProductUrl();

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

    const adSupportedLangs = [...document.querySelectorAll(
      '#edAdSupportedLangsBox input[name="ad_supported_langs"]:checked'
    )].map(i => i.value).join(',');

    const payload = {
      name,
      product_code: code,
      copywritings: cwDict,
      localized_links: edState.productData.product.localized_links || {},
      ad_supported_langs: adSupportedLangs,
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
    // 注：添加弹窗新版已移除这块 UI，节点不存在时直接跳过绑定。
    const icdz = $('itemCoverDropzone');
    if (icdz) {
      icdz.addEventListener('click', () => $('itemCoverInput').click());
      icdz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('itemCoverInput').click(); } });
      icdz.addEventListener('dragover', (e) => { e.preventDefault(); icdz.classList.add('drag'); });
      icdz.addEventListener('dragleave', () => icdz.classList.remove('drag'));
      icdz.addEventListener('drop', (e) => {
        e.preventDefault(); icdz.classList.remove('drag');
        const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
        if (f) uploadItemCover(f);
      });
      icdz.addEventListener('paste', (e) => {
        const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
        if (item) { e.preventDefault(); uploadItemCover(item.getAsFile()); }
      });
    }
    $('itemCoverReplace') && $('itemCoverReplace').addEventListener('click', () => $('itemCoverInput').click());
    $('itemCoverClear') && $('itemCoverClear').addEventListener('click', clearItemCover);
    $('itemCoverInput') && $('itemCoverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) uploadItemCover(f);
    });
    $('itemCoverFromUrlBtn') && $('itemCoverFromUrlBtn').addEventListener('click', importItemCoverFromUrl);
    $('itemCoverUrl') && $('itemCoverUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); importItemCoverFromUrl(); } });

    // 添加弹窗的视频拖拽上传区：模板也已隐藏；仅在节点存在时绑定，避免影响事件链后续注册
    const dz = $('dropzone');
    if (dz) {
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
    }
    $('fileInput') && $('fileInput').addEventListener('change', (e) => {
      const file = e.target.files[0]; e.target.value = '';
      if (file) uploadVideo(file);
    });

    // ---- Edit detail modal wiring ----
    $('edClose').addEventListener('click', edHide);
    $('edCancelBtn').addEventListener('click', edHide);
    $('edSaveBtn').addEventListener('click', edSave);
    $('edMask').addEventListener('click', (e) => { if (e.target.id === 'edMask') edHide(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('edMask').hidden) edHide(); });

    // 产品链接：输入变化时 flush 到内存；产品 ID 改了需刷新 placeholder/hint
    const edCodeInput = $('edCode');
    const edUrlInput = $('edProductUrl');
    if (edCodeInput) {
      edCodeInput.addEventListener('input', () => edRenderProductUrl(edState.activeLang));
    }
    if (edUrlInput) {
      edUrlInput.addEventListener('blur', () => {
        edFlushProductUrl();
        edRenderProductUrl(edState.activeLang);  // 刷新 hint（是否已自定义）
      });
    }

    // 商品详情图：从商品链接一键下载（后台任务 + 进度弹窗）
    const edFromUrlBtn = $('edDetailImagesFromUrlBtn');
    if (edFromUrlBtn) {
      let pollHandle = null;

      function renderFromUrlProgress(task) {
        const msg = $('edFromUrlMsg');
        const bar = $('edFromUrlBar');
        const sub = $('edFromUrlSub');
        const imgGrid = $('edFromUrlImages');
        const doneBtn = $('edFromUrlDoneBtn');
        if (!msg || !bar || !sub || !imgGrid || !doneBtn) return;

        msg.textContent = task.message || task.status;
        const total = task.total || 0;
        const progress = task.progress || 0;
        const percent = total ? Math.round((progress / total) * 100) : (task.status === 'fetching' ? 5 : 0);
        bar.style.width = percent + '%';
        if (task.current_url) {
          sub.textContent = `正在下载：${task.current_url}`;
        } else if (total) {
          sub.textContent = `${progress} / ${total}`;
        } else {
          sub.textContent = '';
        }

        const inserted = task.inserted || [];
        if (inserted.length) {
          imgGrid.innerHTML = inserted.map((it, i) => `
            <div style="border:1px solid var(--oc-border);border-radius:8px;overflow:hidden;">
              <img src="${escapeHtml(it.thumbnail_url)}" alt="图 ${i+1}" loading="lazy"
                   style="width:100%;height:120px;object-fit:cover;display:block;">
              <div style="padding:4px 6px;font-size:11px;color:var(--oc-fg-muted);text-align:center;">#${i + 1}</div>
            </div>
          `).join('');
        } else if (task.status === 'failed') {
          imgGrid.innerHTML = `<div class="oc-detail-images-empty" style="grid-column:1/-1;color:var(--danger-color,#dc2626);">${escapeHtml(task.error || '抓取失败')}</div>`;
        } else if (task.status === 'done' && !inserted.length) {
          imgGrid.innerHTML = `<div class="oc-detail-images-empty" style="grid-column:1/-1;">未下载到任何图片</div>`;
        }

        if (task.status === 'done' || task.status === 'failed') {
          doneBtn.disabled = false;
          doneBtn.textContent = task.status === 'failed' ? '关闭' : '完成，关闭并刷新';
        }
      }

      async function pollFromUrlTask(pid, taskId) {
        try {
          const resp = await fetch(`/medias/api/products/${pid}/detail-images/from-url/status/${taskId}`);
          if (!resp.ok) throw new Error(await resp.text());
          const task = await resp.json();
          renderFromUrlProgress(task);
          if (task.status === 'done' || task.status === 'failed') {
            pollHandle = null;
            return;
          }
        } catch (e) {
          console.error('[from-url] poll failed:', e);
        }
        pollHandle = setTimeout(() => pollFromUrlTask(pid, taskId), 1000);
      }

      function openFromUrlModal() {
        $('edFromUrlMask').hidden = false;
        $('edFromUrlMsg').textContent = '正在启动任务...';
        $('edFromUrlBar').style.width = '0%';
        $('edFromUrlSub').textContent = '';
        $('edFromUrlImages').innerHTML = '<div class="oc-detail-images-empty" style="grid-column:1/-1;">等待开始...</div>';
        $('edFromUrlDoneBtn').disabled = true;
        $('edFromUrlDoneBtn').textContent = '关闭（下载完成后可关）';
      }

      function closeFromUrlModal() {
        if (pollHandle) { clearTimeout(pollHandle); pollHandle = null; }
        $('edFromUrlMask').hidden = true;
        // 关闭时强制刷新素材编辑页的详情图
        if (edDetailImagesCtrl) edDetailImagesCtrl.load();
      }

      $('edFromUrlClose').addEventListener('click', closeFromUrlModal);
      $('edFromUrlDoneBtn').addEventListener('click', closeFromUrlModal);
      $('edFromUrlMask').addEventListener('click', (e) => {
        if (e.target.id === 'edFromUrlMask') closeFromUrlModal();
      });

      edFromUrlBtn.addEventListener('click', async () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        if (!pid) return;
        edFlushProductUrl();
        const lang = edState.activeLang;
        const links = (edState.productData.product.localized_links) || {};
        const override = links[lang];
        const code = ($('edCode').value || '').trim();
        const def = _defaultProductUrl(lang, code);
        const url = override || def;
        if (!url) { alert('请先填写产品 ID 或产品链接'); return; }

        openFromUrlModal();
        try {
          const resp = await fetch(`/medias/api/products/${pid}/detail-images/from-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, lang }),
          });
          const data = await resp.json();
          if (!resp.ok) {
            renderFromUrlProgress({
              status: 'failed',
              error: data.error || ('HTTP ' + resp.status),
              message: data.error || ('启动失败：HTTP ' + resp.status),
            });
            return;
          }
          pollFromUrlTask(pid, data.task_id);
        } catch (e) {
          renderFromUrlProgress({
            status: 'failed',
            error: e.message || String(e),
            message: '网络错误：' + (e.message || e),
          });
        }
      });
    }

    // edCwAddBtn：按当前 activeLang 添加文案条目
    $('edCwAddBtn').addEventListener('click', () => {
      $('edCwList').appendChild(edCwCard({ lang: edState.activeLang }, $('edCwList').children.length + 1));
      $('edCwBadge').textContent = $('edCwList').children.length;
    });

    // 编辑弹窗封面事件由 edRenderCoverBlock() 动态绑定，此处不再静态绑定

    // ===== 新增素材大框：视频封面图 =====
    const edIcDz = $('edItemCoverDropzone');
    if (edIcDz) {
      edIcDz.addEventListener('click', () => $('edItemCoverInput').click());
      edIcDz.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('edItemCoverInput').click(); }
      });
      edIcDz.addEventListener('dragover', (e) => { e.preventDefault(); edIcDz.classList.add('drag'); });
      edIcDz.addEventListener('dragleave', () => edIcDz.classList.remove('drag'));
      edIcDz.addEventListener('drop', (e) => {
        e.preventDefault(); edIcDz.classList.remove('drag');
        const f = [...(e.dataTransfer.files || [])].find(x => x.type.startsWith('image/'));
        if (f) edUploadPendingItemCover(f);
      });
    }
    $('edItemCoverReplace') && $('edItemCoverReplace').addEventListener('click', () => $('edItemCoverInput').click());
    $('edItemCoverClear') && $('edItemCoverClear').addEventListener('click', () => {
      edState.pendingItemCover = null;
      edSetItemCover(null);
    });
    $('edItemCoverInput') && $('edItemCoverInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) edUploadPendingItemCover(f);
    });
    $('edItemCoverFromUrlBtn') && $('edItemCoverFromUrlBtn').addEventListener('click', edImportItemCoverFromUrl);
    $('edItemCoverUrl') && $('edItemCoverUrl').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); edImportItemCoverFromUrl(); }
    });

    // ===== 新增素材大框：视频源（只选不传） =====
    const edVpBox = $('edVideoPickBox');
    if (edVpBox) {
      edVpBox.addEventListener('click', (e) => {
        // 点 "清空" 按钮时不触发 file picker
        if (e.target && e.target.closest('#edVideoPickClear')) return;
        $('edVideoInput').click();
      });
      edVpBox.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('edVideoInput').click(); }
      });
      edVpBox.addEventListener('dragover', (e) => { e.preventDefault(); edVpBox.classList.add('drag'); });
      edVpBox.addEventListener('dragleave', () => edVpBox.classList.remove('drag'));
      edVpBox.addEventListener('drop', (e) => {
        e.preventDefault(); edVpBox.classList.remove('drag');
        const file = [...(e.dataTransfer.files || [])]
          .find(f => f.type.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/i.test(f.name));
        if (file) edSetPickedVideo(file);
      });
    }
    $('edVideoInput') && $('edVideoInput').addEventListener('change', (e) => {
      const f = e.target.files[0]; e.target.value = '';
      if (f) edSetPickedVideo(f);
    });
    $('edVideoPickClear') && $('edVideoPickClear').addEventListener('click', (e) => {
      e.stopPropagation();
      edSetPickedVideo(null);
    });

    // ===== 新增素材大框：提交按钮 =====
    $('edItemSubmitBtn') && $('edItemSubmitBtn').addEventListener('click', edSubmitNewItem);

    loadList();
  });
})();
