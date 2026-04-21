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
      const text = await res.text().catch(() => '');
      let msg = '';
      if (text) {
        try {
          const data = JSON.parse(text);
          msg = data.error || data.message || text;
        } catch {
          msg = text;
        }
      }
      throw new Error(msg || `HTTP ${res.status}`);
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
    const gifGrid    = opts.gifGrid    ? $(opts.gifGrid)    : null;
    const gifBadge   = opts.gifBadge   ? $(opts.gifBadge)   : null;
    const gifPickBtn = opts.gifPickBtn ? $(opts.gifPickBtn) : null;
    const gifInput   = opts.gifInput   ? $(opts.gifInput)   : null;
    const getLang   = opts.getLang   || (() => 'en');
    const ensurePid = opts.ensurePid || (async () => null);
    const onItemsChange = opts.onItemsChange || (() => {});
    let items = [];

    function show() { if (section) section.hidden = false; }
    function hide() { if (section) section.hidden = true; }

    function isGifItem(it) {
      const key = String((it && it.object_key) || '').toLowerCase();
      return key.endsWith('.gif');
    }

    function renderItemHTML(it, idx) {
      return `
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
      `;
    }

    function renderInto(targetGrid, list, emptyText) {
      if (!targetGrid) return;
      if (!list.length) {
        targetGrid.innerHTML = `<div class="oc-detail-images-empty">${escapeHtml(emptyText)}</div>`;
      } else {
        targetGrid.innerHTML = list.map(renderItemHTML).join('');
        targetGrid.querySelectorAll('.oc-detail-image-del').forEach(btn => {
          btn.addEventListener('click', onDelete);
        });
      }
    }

    function renderGrid() {
      if (!grid) return;
      if (gifGrid) {
        const staticList = items.filter(it => !isGifItem(it));
        const gifList = items.filter(isGifItem);
        renderInto(grid, staticList, '尚未上传静态详情图');
        renderInto(gifGrid, gifList, '当前语种暂无 GIF 动图');
        if (badge)    badge.textContent = String(staticList.length);
        if (gifBadge) gifBadge.textContent = String(gifList.length);
      } else {
        renderInto(grid, items, '尚未上传详情图');
        if (badge) badge.textContent = String(items.length);
      }
      onItemsChange(items.slice());
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

    function inferMimeFromName(name) {
      const n = (name || '').toLowerCase().trim();
      if (/\.(jpe?g)(?:\?|#|$)/.test(n)) return 'image/jpeg';
      if (/\.png(?:\?|#|$)/.test(n))      return 'image/png';
      if (/\.webp(?:\?|#|$)/.test(n))     return 'image/webp';
      if (/\.gif(?:\?|#|$)/.test(n))      return 'image/gif';
      return '';
    }
    function resolveMime(f) {
      if (!f) return '';
      // 浏览器有时给出带参数 ("image/png; charset=...") 或非标准 MIME ("image/x-png")；
      // 优先按白名单精确命中，失败则按文件名后缀兜底，再失败则接受任意 image/*。
      const rawMime = (f.type || '').toLowerCase().trim();
      const mime = rawMime.split(';')[0].trim();
      if (/^image\/(jpeg|png|webp|gif)$/.test(mime)) return mime;
      const extMime = inferMimeFromName(f.name);
      if (extMime) return extMime;
      if (mime.startsWith('image/')) {
        if (mime.includes('jpeg') || mime.includes('jpg')) return 'image/jpeg';
        if (mime.includes('png'))  return 'image/png';
        if (mime.includes('webp')) return 'image/webp';
        if (mime.includes('gif'))  return 'image/gif';
        return 'image/jpeg';
      }
      return '';
    }

    async function uploadFiles(rawFiles) {
      if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
      const all = [...(rawFiles || [])];
      const files = all.filter(f => !!resolveMime(f));
      if (!files.length) {
        const debug = all.length
          ? all.map((f, i) => `[${i}] name=${f && f.name || '(空)'} · type=${f && f.type || '(空)'}`).join('\n')
          : '(未选中任何文件)';
        alert('请选择 JPG / PNG / WebP / GIF 图片\n\n调试信息：\n' + debug);
        return;
      }
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
                filename: f.name,
                content_type: resolveMime(f),
                size: f.size,
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
            headers: { 'Content-Type': resolveMime(f) || 'application/octet-stream' },
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
                content_type: resolveMime(files[i]),
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
      // Chromium 里 input.value='' 会 mutate 先前拿到的 FileList 引用 → 长度变 0；
      // 必须先 snapshot 成 Array 再清 value，否则 uploadFiles 永远拿到空列表，
      // 误触发"请选择 JPG / PNG / WebP / GIF 图片"弹窗。
      const files = [...(e.target.files || [])];
      e.target.value = '';
      uploadFiles(files);
    });

    if (gifPickBtn) gifPickBtn.addEventListener('click', () => gifInput && gifInput.click());
    if (gifInput) gifInput.addEventListener('change', (e) => {
      const files = [...(e.target.files || [])];
      e.target.value = '';
      uploadFiles(files);
    });

    return {
      load, reset, show, hide,
      items: () => items.slice(),
      staticItems: () => items.filter(it => !isGifItem(it)),
      gifItems:    () => items.filter(isGifItem),
    };
  }

  // ---------- List ----------
  async function loadList() {
    const kw = $('kw').value.trim();
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
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
          <col style="width:96px">
          <col style="width:60px">
          <col style="width:200px">
          <col style="width:108px">
          <col style="width:300px">
        </colgroup>
        <thead>
          <tr>
            <th>ID</th>
            <th>主图</th>
            <th>产品名称</th>
            <th>产品 ID</th>
            <th>明空 ID</th>
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
    grid.querySelectorAll('td.mk-id-cell').forEach(td =>
      td.addEventListener('click', (e) => { e.stopPropagation(); startMkIdInlineEdit(td); }));
  }

  function rowHTML(p) {
    const count = p.items_count || 0;
    const rawCount = p.raw_sources_count || 0;
    const warnCls = !p.has_en_cover ? ' class="oc-row-warn"' : '';
    const cover = p.cover_thumbnail_url
      ? `<img src="${escapeHtml(p.cover_thumbnail_url)}" alt="" loading="lazy">`
      : `<div class="cover-ph">${icon('film', 16)}</div>`;
    const mkIdText = (p.mk_id === null || p.mk_id === undefined) ? '' : String(p.mk_id);
    const mkIdCell = mkIdText
      ? `<span class="mk-id-text">${escapeHtml(mkIdText)}</span>`
      : `<span class="mk-id-text"><span class="muted">—</span></span>`;
    return `
      <tr${warnCls} data-pid="${p.id}">
        <td class="mono">${p.id}</td>
        <td><div class="oc-thumb-sm">${cover}</div></td>
        <td class="name wrap"><a href="#" data-pid="${p.id}" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a></td>
        <td class="mono wrap" title="${escapeHtml(p.product_code || '')}">${p.product_code ? `<a href="https://newjoyloo.com/products/${encodeURIComponent(p.product_code)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.product_code)}</a>` : '<span class="muted">—</span>'}</td>
        <td class="mono mk-id-cell" data-pid="${p.id}" data-mkid="${escapeHtml(mkIdText)}" title="点击编辑明空 ID">${mkIdCell}</td>
        <td><span class="oc-pill">${count}</span></td>
        <td>${renderLangBar(p.lang_coverage)}</td>
        <td class="muted">${fmtDate(p.updated_at)}</td>
        <td class="actions">
          <div class="oc-row-actions">
            <button class="oc-btn sm ghost" data-edit="${p.id}">${icon('edit', 12)}<span>编辑</span></button>
            <button class="oc-btn sm ghost js-raw-sources" data-pid="${p.id}" data-name="${escapeHtml(p.name)}">原始视频 (${rawCount})</button>
            <button class="bt-row-btn js-translate" data-pid="${p.id}" data-name="${escapeHtml(p.name)}" title="基于原始视频发起多语言翻译">🌐 翻译</button>
          </div>
        </td>
      </tr>`;
  }

  async function startMkIdInlineEdit(td) {
    if (td.dataset.editing === '1') return;
    td.dataset.editing = '1';
    const pid = +td.dataset.pid;
    const original = td.dataset.mkid || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.maxLength = 8;
    input.value = original;
    input.className = 'mk-id-input';
    input.setAttribute('aria-label', '明空 ID');
    td.innerHTML = '';
    td.appendChild(input);
    input.focus();
    input.select();

    let settled = false;

    function restore(value) {
      td.dataset.mkid = value;
      td.dataset.editing = '';
      td.innerHTML = value
        ? `<span class="mk-id-text">${escapeHtml(value)}</span>`
        : `<span class="mk-id-text"><span class="muted">—</span></span>`;
    }

    async function commit() {
      if (settled) return;
      settled = true;
      const raw = input.value.trim();
      if (raw === original) { restore(original); return; }
      if (raw !== '' && !/^\d{1,8}$/.test(raw)) {
        input.classList.add('error');
        input.focus();
        settled = false;
        return;
      }
      input.disabled = true;
      try {
        await fetchJSON('/medias/api/products/' + pid, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mk_id: raw === '' ? null : parseInt(raw, 10) }),
        });
        restore(raw);
      } catch (e) {
        const msg = (e.message || '').toString();
        if (msg.includes('mk_id_conflict') || msg.includes('明空 ID 已被其他产品占用')) {
          alert('明空 ID 已被其他产品占用');
        } else if (msg.includes('mk_id_invalid') || msg.includes('必须是 1-8 位数字')) {
          alert('明空 ID 必须是 1-8 位数字');
        } else {
          alert('保存失败：' + msg);
        }
        input.disabled = false;
        input.classList.add('error');
        input.focus();
        settled = false;
      }
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      else if (e.key === 'Escape') { e.preventDefault(); settled = true; restore(original); }
    });
    input.addEventListener('blur', commit);
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
    // 小语种详情图翻译任务历史（按语种缓存）
    detailTranslateTasks: {},
    linkCheckPollTimer: null,
    linkCheckModalLang: '',
    linkCheckDetailTask: null,
    linkCheckDetailError: '',
  };

  function edShow() { $('edMask').hidden = false; }
  function edHide() {
    edCloseLinkCheckModal();
    edStopLinkCheckPoll();
    $('edMask').hidden = true;
    if ($('edFromUrlMask')) $('edFromUrlMask').hidden = true;
    if ($('edDetailTranslateTaskMask')) $('edDetailTranslateTaskMask').hidden = true;
    edState.current = null;
    edState.activeLang = 'en';
    edState.productData = null;
    edState.detailTranslateTasks = {};
    edState.linkCheckDetailTask = null;
    edState.linkCheckDetailError = '';
    edResetNewItemForm();
  }

  let edDetailImagesCtrl = null;

  function ensureEdDetailImagesCtrl() {
    if (edDetailImagesCtrl) return edDetailImagesCtrl;
    edDetailImagesCtrl = createDetailImagesController({
      section:    'edDetailImagesSection',
      grid:       'edDetailImagesGrid',
      gifGrid:    'edDetailGifImagesGrid',
      input:      'edDetailImagesInput',
      pickBtn:    'edDetailImagesPickBtn',
      gifInput:   'edDetailGifImagesInput',
      gifPickBtn: 'edDetailGifImagesPickBtn',
      badge:      'edDetailImagesBadge',
      gifBadge:   'edDetailGifImagesBadge',
      progress:   'edDetailImagesProgress',
      getLang: () => edState.activeLang,
      ensurePid: async () => {
        const p = edState.productData && edState.productData.product;
        return p ? p.id : null;
      },
      onItemsChange: () => edSyncDetailImagesDownloadZipButton(),
    });
    return edDetailImagesCtrl;
  }

  function edSyncDetailImagesDownloadZipButton() {
    const p = edState.productData && edState.productData.product;
    const ctrl = edDetailImagesCtrl;
    const staticBtn = $('edDetailImagesDownloadZipBtn');
    if (staticBtn) {
      const list = (ctrl && ctrl.staticItems) ? ctrl.staticItems() : [];
      staticBtn.disabled = !(p && p.id && list.length);
    }
    const gifBtn = $('edDetailGifImagesDownloadZipBtn');
    if (gifBtn) {
      const list = (ctrl && ctrl.gifItems) ? ctrl.gifItems() : [];
      gifBtn.disabled = !(p && p.id && list.length);
    }
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
      edStopLinkCheckPoll();
      edCloseLinkCheckModal();
      const data = await fetchJSON('/medias/api/products/' + pid);
      edState.current = data;
      edState.productData = data;
      edState.activeLang = 'en';
      edState.linkCheckDetailTask = null;
      edState.linkCheckDetailError = '';
      $('edName').value = data.product.name || '';
      $('edCode').value = data.product.product_code || '';
      $('edMkId').value = (data.product.mk_id === null || data.product.mk_id === undefined)
        ? '' : String(data.product.mk_id);
      edRenderAdSupportedLangs(data.product.ad_supported_langs || '');
      $('edUploadProgress').innerHTML = '';
      edResetNewItemForm();
      edShow();
      edRenderLangTabs();
      await edRenderActiveLangView();
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
    edStopLinkCheckPoll();
    if (edState.linkCheckModalLang && edState.linkCheckModalLang !== lang) {
      edCloseLinkCheckModal();
    }
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

  function edDetailTranslateStatusLabel(status) {
    switch ((status || '').toLowerCase()) {
      case 'done': return '已完成';
      case 'running': return '进行中';
      case 'queued': return '排队中';
      case 'failed': return '已失败';
      default: return status || '待处理';
    }
  }

  function edDetailTranslateApplyLabel(status) {
    switch ((status || '').toLowerCase()) {
      case 'applied': return '已回填';
      case 'applied_partial': return '部分回填';
      case 'skipped_failed': return '未回填（有失败）';
      case 'apply_error': return '回填失败';
      case 'pending': return '待回填';
      default: return status || '待回填';
    }
  }

  async function edLoadDetailTranslateTasks(pid, lang) {
    if (!pid || !lang || lang === 'en') {
      edState.detailTranslateTasks[lang] = [];
      return [];
    }
    const data = await fetchJSON(`/medias/api/products/${pid}/detail-image-translate-tasks?lang=${encodeURIComponent(lang)}`);
    const tasks = Array.isArray(data.items) ? data.items : [];
    edState.detailTranslateTasks[lang] = tasks;
    return tasks;
  }

  function edRenderDetailTranslateHistory(tasks) {
    const wrap = $('edDetailTranslateHistoryWrap');
    const box = $('edDetailTranslateHistory');
    if (!wrap || !box) return;
    if (edState.activeLang === 'en') {
      wrap.hidden = true;
      box.innerHTML = '';
      return;
    }
    wrap.hidden = false;
    if (!tasks.length) {
      box.innerHTML = '<div class="oc-hint" style="padding:10px 12px;border:1px dashed var(--oc-border);border-radius:10px;">暂无翻译任务记录</div>';
      return;
    }
    box.innerHTML = tasks.map(task => {
      const progress = task.progress || {};
      const detailUrl = escapeHtml(task.detail_url || `/image-translate/${task.task_id}`);
      const taskId = escapeHtml(task.task_id || '');
      const status = escapeHtml(edDetailTranslateStatusLabel(task.status));
      const applyStatus = escapeHtml(edDetailTranslateApplyLabel(task.apply_status));
      const updatedAt = escapeHtml(fmtDate(task.updated_at || task.created_at || ''));
      const progressText = `${progress.done || 0}/${progress.total || 0}`;
      return `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:12px;border:1px solid var(--oc-border);border-radius:10px;background:var(--oc-bg-subtle);margin-top:8px;">
          <div style="display:grid;gap:4px;min-width:0;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <strong style="color:var(--oc-fg);">任务 ${taskId || '-'}</strong>
              <span class="oc-hint">状态：${status}</span>
              <span class="oc-hint">回填：${applyStatus}</span>
              <span class="oc-hint">进度：${progressText}</span>
            </div>
            <div class="oc-hint">更新时间：${updatedAt || '-'}</div>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;">
            <a class="oc-btn ghost sm" href="${detailUrl}" target="_blank" rel="noopener">查看详情</a>
            <button type="button" class="oc-btn ghost sm" data-retranslate-lang="${escapeHtml(edState.activeLang)}">重新翻译</button>
          </div>
        </div>
      `;
    }).join('');
  }

  const LINK_CHECK_STATUS_LABELS = {
    queued: '排队中',
    locking_locale: '锁定目标语种页面',
    downloading: '下载图片中',
    analyzing: '分析图片中',
    review_ready: '待复核',
    done: '已完成',
    failed: '失败',
  };

  const LINK_CHECK_OVERALL_LABELS = {
    running: '检测中',
    done: '通过',
    unfinished: '需复核',
  };

  const LINK_CHECK_DECISION_LABELS = {
    pass: '通过',
    replace: '需替换',
    review: '待复核',
    no_text: '无文字',
    failed: '失败',
  };

  const LINK_CHECK_REFERENCE_LABELS = {
    matched: '已匹配参考图',
    weak_match: '弱匹配',
    not_matched: '未匹配',
    not_provided: '未提供参考图',
  };

  const LINK_CHECK_BINARY_LABELS = {
    pass: '快检通过',
    fail: '快检不通过',
    skipped: '未执行快检',
    error: '快检失败',
  };

  const LINK_CHECK_SAME_IMAGE_LABELS = {
    done: '已完成同图判断',
    skipped: '未执行同图判断',
    error: '同图判断失败',
  };

  function edLinkCheckTasks() {
    if (!edState.productData || !edState.productData.product) return {};
    if (!edState.productData.product.link_check_tasks || typeof edState.productData.product.link_check_tasks !== 'object') {
      edState.productData.product.link_check_tasks = {};
    }
    return edState.productData.product.link_check_tasks;
  }

  function edGetLinkCheckTask(lang) {
    if (!lang) return null;
    return edLinkCheckTasks()[lang] || null;
  }

  function edSetLinkCheckTask(lang, task) {
    if (!lang || !task || !edState.productData || !edState.productData.product) return null;
    const tasks = edLinkCheckTasks();
    tasks[lang] = { ...(tasks[lang] || {}), ...task };
    return tasks[lang];
  }

  function edCurrentLinkUrl(lang) {
    const code = ($('edCode') && $('edCode').value || '').trim();
    const links = (edState.productData && edState.productData.product && edState.productData.product.localized_links) || {};
    if (lang === edState.activeLang) {
      const input = $('edProductUrl');
      const current = input ? (input.value || '').trim() : '';
      if (current) return current;
    }
    return links[lang] || _defaultProductUrl(lang, code) || '';
  }

  function edLinkCheckNeedsPolling(task) {
    if (!task || !task.status) return false;
    return !['done', 'review_ready', 'failed'].includes(task.status);
  }

  function edLinkCheckStatusKind(task) {
    if (!task) return 'info';
    if (task.status === 'failed') return 'danger';
    if (task.status === 'review_ready' || (task.summary || {}).overall_decision === 'unfinished') return 'warning';
    if (task.status === 'done') return 'success';
    return 'info';
  }

  function edLinkCheckStatusText(task) {
    if (!task) return '未检测';
    const summary = task.summary || {};
    if (task.status === 'done' && summary.overall_decision) {
      return LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || LINK_CHECK_STATUS_LABELS[task.status] || task.status;
    }
    return LINK_CHECK_STATUS_LABELS[task.status] || LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || task.status || '未检测';
  }

  function edLinkCheckDecisionText(decision, status) {
    if (status === 'failed') return LINK_CHECK_DECISION_LABELS.failed;
    return LINK_CHECK_DECISION_LABELS[decision] || '待复核';
  }

  function edLinkCheckDecisionKind(decision, status) {
    if (status === 'failed' || decision === 'replace') return 'danger';
    if (decision === 'pass' || decision === 'no_text') return 'success';
    return 'warning';
  }

  function edLinkCheckReferenceText(reference) {
    const status = (reference || {}).status || 'not_provided';
    if (status === 'matched' && reference.reference_filename) {
      return reference.reference_filename;
    }
    return LINK_CHECK_REFERENCE_LABELS[status] || status;
  }

  function edLinkCheckBinaryText(binary) {
    const status = (binary || {}).status || 'skipped';
    return LINK_CHECK_BINARY_LABELS[status] || status;
  }

  function edLinkCheckSameImageText(sameImage) {
    const status = (sameImage || {}).status || 'skipped';
    if (status === 'done' && sameImage.answer) return sameImage.answer;
    return LINK_CHECK_SAME_IMAGE_LABELS[status] || status;
  }

  function edLinkCheckBadge(label, kind) {
    return `<span class="oc-link-check-badge ${kind || 'info'}">${escapeHtml(label)}</span>`;
  }

  function edLinkCheckPercent(task) {
    const progress = (task && task.progress) || {};
    const total = progress.total || 0;
    if (total > 0) {
      const finished = Math.max(progress.analyzed || 0, progress.downloaded || 0);
      return Math.max(8, Math.min(100, Math.round((finished / total) * 100)));
    }
    if (!task) return 0;
    if (task.status === 'queued') return 5;
    if (task.status === 'locking_locale') return 12;
    if (task.status === 'downloading') return 35;
    if (task.status === 'analyzing') return 72;
    if (task.status === 'review_ready' || task.status === 'done') return 100;
    return 0;
  }

  function edRenderLinkCheckSummary(task) {
    const box = $('edLinkCheckSummary');
    const viewBtn = $('edLinkCheckViewBtn');
    const actionBtn = $('edLinkCheckBtn');
    if (!box || !viewBtn || !actionBtn) return;

    actionBtn.innerHTML = `${icon('search', 14)}<span>${task ? '重新检测' : '链接检测'}</span>`;
    if (!task) {
      viewBtn.hidden = true;
      box.innerHTML = '<span class="oc-link-check-empty">当前语种会使用该链接、主图和详情图作为检测输入。</span>';
      return;
    }

    const summary = task.summary || {};
    const currentUrl = edCurrentLinkUrl(edState.activeLang);
    const urlChanged = currentUrl && task.link_url && currentUrl !== task.link_url;
    const parts = [
      edLinkCheckBadge(edLinkCheckStatusText(task), edLinkCheckStatusKind(task)),
    ];
    if (summary.overall_decision) {
      parts.push(edLinkCheckBadge(
        LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || summary.overall_decision,
        summary.overall_decision === 'done' ? 'success' : (summary.overall_decision === 'unfinished' ? 'warning' : 'info'),
      ));
    }
    if (typeof summary.pass_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">通过 ${summary.pass_count}</span>`);
    }
    if (typeof summary.replace_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">替换 ${summary.replace_count}</span>`);
    }
    if (typeof summary.review_count === 'number') {
      parts.push(`<span class="oc-link-check-meta">复核 ${summary.review_count}</span>`);
    }
    if (task.checked_at) {
      parts.push(`<span class="oc-link-check-meta">最近检测 ${escapeHtml(fmtDate(task.checked_at))}</span>`);
    }
    if (task.link_url) {
      parts.push(`<span class="oc-link-check-meta mono">${escapeHtml(task.link_url)}</span>`);
    }
    if (urlChanged) {
      parts.push(edLinkCheckBadge('链接已变更', 'warning'));
    }

    box.innerHTML = parts.join('');
    viewBtn.hidden = !task.task_id;
    viewBtn.textContent = edLinkCheckNeedsPolling(task) ? '查看进度' : '查看结果';
  }

  function edStopLinkCheckPoll() {
    if (edState.linkCheckPollTimer) {
      clearTimeout(edState.linkCheckPollTimer);
      edState.linkCheckPollTimer = null;
    }
  }

  function edRenderLinkCheckModal() {
    const summaryBox = $('edLinkCheckModalSummary');
    const refsBox = $('edLinkCheckRefs');
    const itemsBox = $('edLinkCheckItems');
    if (!summaryBox || !refsBox || !itemsBox) return;

    const lang = edState.linkCheckModalLang || edState.activeLang;
    const summaryTask = edGetLinkCheckTask(lang);
    const detailTask = edState.linkCheckDetailTask;
    const task = { ...(summaryTask || {}), ...(detailTask || {}) };

    if (!task || (!task.task_id && !task.id)) {
      summaryBox.innerHTML = '<div class="oc-detail-images-empty">当前语种还没有链接检测任务</div>';
      refsBox.innerHTML = '<div class="oc-detail-images-empty">暂无参考图</div>';
      itemsBox.innerHTML = '<div class="oc-detail-images-empty">还没有检测结果</div>';
      $('edLinkCheckRefsBadge').textContent = '0';
      $('edLinkCheckItemsBadge').textContent = '0';
      return;
    }

    const summary = task.summary || {};
    const progress = task.progress || {};
    const summaryCards = [
      ['当前状态', edLinkCheckStatusText(task), false],
      ['整体结论', LINK_CHECK_OVERALL_LABELS[summary.overall_decision] || '-', false],
      ['已分析图片', `${progress.analyzed ?? 0} / ${progress.total ?? 0}`, false],
      ['参考图匹配', String(summary.reference_matched_count ?? 0), false],
      ['通过', String(summary.pass_count ?? 0), false],
      ['需替换', String(summary.replace_count ?? 0), false],
      ['待复核', String(summary.review_count ?? 0), false],
      ['最终链接', task.resolved_url || task.link_url || '-', true],
    ];
    summaryBox.innerHTML = summaryCards.map(([label, value, mono]) => `
      <div class="oc-link-check-card">
        <span class="oc-link-check-card-title">${escapeHtml(label)}</span>
        <span class="oc-link-check-card-value${mono ? ' mono' : ''}">${escapeHtml(value)}</span>
      </div>
    `).join('');

    const references = Array.isArray(task.reference_images) ? task.reference_images : [];
    $('edLinkCheckRefsBadge').textContent = String(references.length);
    refsBox.innerHTML = references.length
      ? references.map(ref => `
          <div class="oc-link-check-ref">
            <img src="${escapeHtml(ref.preview_url || '')}" alt="${escapeHtml(ref.filename || '参考图')}" loading="lazy">
            <span title="${escapeHtml(ref.filename || '')}">${escapeHtml(ref.filename || '')}</span>
          </div>
        `).join('')
      : '<div class="oc-detail-images-empty">暂无参考图</div>';

    const items = Array.isArray(task.items) ? task.items : [];
    $('edLinkCheckItemsBadge').textContent = String(items.length);
    if (!items.length) {
      const placeholder = edLinkCheckNeedsPolling(summaryTask)
        ? `链接检测进行中，当前进度 ${edLinkCheckPercent(summaryTask)}%`
        : (edState.linkCheckDetailError || '还没有检测结果');
      itemsBox.innerHTML = `<div class="oc-detail-images-empty">${escapeHtml(placeholder)}</div>`;
      return;
    }

    itemsBox.innerHTML = items.map((item, idx) => {
      const analysis = item.analysis || {};
      const reference = item.reference_match || {};
      const binary = item.binary_quick_check || {};
      const sameImage = item.same_image_llm || {};
      const decision = analysis.decision || '';
      const reason = analysis.quality_reason || analysis.text_summary || item.error || binary.reason || sameImage.reason || '暂无说明';
      const itemLabel = item.kind === 'hero' ? '轮播图' : '详情图';
      const preview = item.site_preview_url
        ? `<img src="${escapeHtml(item.site_preview_url)}" alt="${escapeHtml(itemLabel)}" loading="lazy">`
        : `<div class="oc-detail-images-empty" style="height:100%;margin:0;">暂无预览</div>`;
      return `
        <article class="oc-link-check-item">
          <div class="oc-link-check-item-preview">${preview}</div>
          <div class="oc-link-check-item-body">
            <div class="oc-link-check-item-head">
              <div class="oc-link-check-item-title">${escapeHtml(itemLabel)} #${idx + 1}</div>
              <div class="oc-link-check-item-badges">
                ${edLinkCheckBadge(edLinkCheckDecisionText(decision, item.status), edLinkCheckDecisionKind(decision, item.status))}
                ${edLinkCheckBadge(edLinkCheckReferenceText(reference), reference.status === 'matched' ? 'success' : (reference.status === 'not_matched' ? 'warning' : 'info'))}
              </div>
            </div>
            <div class="oc-link-check-item-url">${escapeHtml(item.source_url || '-')}</div>
            <div class="oc-link-check-item-meta">
              <span><strong>识别语种：</strong>${escapeHtml(analysis.detected_language || '-')}</span>
              <span><strong>页面语种：</strong>${escapeHtml(task.page_language || '-')}</span>
              <span><strong>二值快检：</strong>${escapeHtml(edLinkCheckBinaryText(binary))}</span>
              <span><strong>同图判断：</strong>${escapeHtml(edLinkCheckSameImageText(sameImage))}</span>
            </div>
            <div class="oc-link-check-item-text">${escapeHtml(reason)}</div>
          </div>
        </article>
      `;
    }).join('');
  }

  function edRenderDetailTranslateState(lang, tasks, detailItems) {
    const section = $('edDetailImagesSection');
    const status = $('edDetailTranslateStatus');
    const translateBtn = $('edDetailImagesTranslateBtn');
    const title = section && section.querySelector('.oc-section-title > span');
    const subtitle = section && section.querySelector('.oc-section-title .optional');
    const langName = (LANGUAGES.find(l => l.code === lang) || {}).name_zh || lang.toUpperCase();
    if (section) section.hidden = false;
    if (title) title.textContent = '商品详情图';
    if (subtitle) {
      subtitle.textContent = lang === 'en'
        ? '英文原始版，用于后续图片翻译'
        : `${langName} 版本，可自行上传、从商品链接下载，或从英语版一键翻译`;
    }
    if (translateBtn) translateBtn.hidden = lang === 'en';
    if (!status) return;
    if (lang === 'en') {
      status.hidden = true;
      return;
    }

    const items = Array.isArray(detailItems) ? detailItems : [];
    const appliedImage = items.find(item => item && item.origin_type === 'image_translate');
    const appliedTaskId = appliedImage && appliedImage.image_translate_task_id ? String(appliedImage.image_translate_task_id) : '';
    const appliedTask = appliedTaskId
      ? (tasks.find(task => String(task.task_id || '') === appliedTaskId) || { task_id: appliedTaskId, detail_url: `/image-translate/${encodeURIComponent(appliedTaskId)}` })
      : null;
    const latest = tasks[0] || null;
    let html = '';
    if (appliedTask) {
      const appliedLabel = escapeHtml(edDetailTranslateApplyLabel(appliedTask.apply_status || 'applied'));
      const detailUrl = escapeHtml(appliedTask.detail_url || `/image-translate/${appliedTask.task_id}`);
      html = `当前 ${escapeHtml(langName)} 详情图已由英语版一键翻译回填（${appliedLabel}）。<a href="${detailUrl}" target="_blank" rel="noopener">查看关联任务</a>`;
    } else if (latest) {
      const detailUrl = escapeHtml(latest.detail_url || `/image-translate/${latest.task_id}`);
      html = `最近一次翻译任务：${escapeHtml(edDetailTranslateStatusLabel(latest.status))} / ${escapeHtml(edDetailTranslateApplyLabel(latest.apply_status))}。<a href="${detailUrl}" target="_blank" rel="noopener">查看任务详情</a>`;
    } else {
      html = `当前 ${escapeHtml(langName)} 还没有执行过从英语版一键翻译。`;
    }

    // 当最近一次任务已结束但尚未回填，且存在成功项时，显示"手动回填已成功项"按钮。
    // 仅以 latest 为目标，避免误把过期任务的图覆盖上来。
    const candidate = latest && !appliedTask ? latest : null;
    if (candidate) {
      const applyStatus = String(candidate.apply_status || '').toLowerCase();
      const status_ = String(candidate.status || '').toLowerCase();
      const progress = candidate.progress || {};
      const doneCount = Number(progress.done || 0);
      const totalCount = Number(progress.total || 0);
      const failedCount = Number(progress.failed || 0);
      const canApply =
        (status_ === 'done' || status_ === 'error')
        && applyStatus !== 'applied'
        && applyStatus !== 'applied_partial'
        && doneCount > 0;
      if (canApply) {
        const btnLabel = failedCount > 0
          ? `手动回填已成功项（${doneCount}/${totalCount}，忽略 ${failedCount} 张失败）`
          : `手动回填已成功项（${doneCount}/${totalCount}）`;
        html += ` <button type="button" class="oc-btn primary sm" data-apply-translate-task="${escapeHtml(candidate.task_id)}" data-apply-translate-lang="${escapeHtml(lang)}">${escapeHtml(btnLabel)}</button>`;
      }
    }

    status.hidden = false;
    status.innerHTML = html;
  }

  async function edRefreshDetailImagesPanel(lang) {
    const ctrl = ensureEdDetailImagesCtrl();
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    ctrl.show();
    if (!pid) {
      ctrl.reset();
      edRenderDetailTranslateState(lang, [], []);
      edRenderDetailTranslateHistory([]);
      return;
    }
    await ctrl.load(pid);
    let tasks = [];
    let loadError = null;
    try {
      tasks = await edLoadDetailTranslateTasks(pid, lang);
    } catch (err) {
      loadError = err;
    }
    const detailItems = ctrl.items ? ctrl.items() : [];
    edRenderDetailTranslateState(lang, tasks, detailItems);
    edRenderDetailTranslateHistory(tasks);
    if (loadError) {
      const status = $('edDetailTranslateStatus');
      if (status && lang !== 'en') {
        status.hidden = false;
        status.textContent = '翻译任务记录加载失败：' + (loadError.message || loadError);
      }
    }
  }

  function edOpenDetailTranslateTaskModal() {
    const mask = $('edDetailTranslateTaskMask');
    if (mask) mask.hidden = false;
  }

  function edCloseDetailTranslateTaskModal() {
    const mask = $('edDetailTranslateTaskMask');
    if (mask) mask.hidden = true;
  }

  async function edStartDetailTranslate(langOverride) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const lang = (langOverride || edState.activeLang || '').trim().toLowerCase();
    if (!pid || !lang || lang === 'en') return;

    const langName = (LANGUAGES.find(l => l.code === lang) || {}).name_zh || lang.toUpperCase();
    const msg = $('edDetailTranslateTaskMsg');
    const meta = $('edDetailTranslateTaskMeta');
    const link = $('edDetailTranslateTaskLink');

    edOpenDetailTranslateTaskModal();
    if (msg) msg.textContent = '正在创建翻译任务...';
    if (meta) meta.textContent = `${langName} · 商品详情图`;
    if (link) {
      link.hidden = true;
      link.removeAttribute('href');
      delete link.dataset.taskId;
    }

    try {
      const data = await fetchJSON(`/medias/api/products/${pid}/detail-images/translate-from-en`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lang }),
      });
      if (msg) msg.textContent = '翻译任务已创建，可以留在当前页查看历史记录，也可以打开详情页跟踪进度。';
      if (meta) meta.textContent = `任务 ID：${data.task_id} · ${langName}`;
      if (link) {
        link.href = data.detail_url || `/image-translate/${data.task_id}`;
        link.dataset.taskId = data.task_id || '';
        link.hidden = false;
      }
      await edRefreshDetailImagesPanel(lang);
    } catch (err) {
      if (msg) msg.textContent = '创建翻译任务失败';
      if (meta) meta.textContent = err.message || String(err);
      if (link) {
        link.hidden = true;
        link.removeAttribute('href');
      }
    }
  }

  function edCloseLinkCheckModal() {
    const mask = $('edLinkCheckMask');
    if (mask) mask.hidden = true;
    edState.linkCheckModalLang = '';
    edState.linkCheckDetailTask = null;
    edState.linkCheckDetailError = '';
  }

  function edLoadLinkCheckDetail(lang) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const task = edGetLinkCheckTask(lang);
    if (!pid || !task || !task.task_id) return Promise.resolve();
    return fetchJSON(`/medias/api/products/${pid}/link-check/${encodeURIComponent(lang)}/detail`)
      .then((detail) => {
        edState.linkCheckDetailTask = detail;
        edState.linkCheckDetailError = '';
        if (edState.linkCheckModalLang === lang) {
          edRenderLinkCheckModal();
        }
        return detail;
      })
      .catch((err) => {
        edState.linkCheckDetailTask = null;
        edState.linkCheckDetailError = err.message || String(err);
        if (edState.linkCheckModalLang === lang) {
          edRenderLinkCheckModal();
        }
      });
  }

  function edOpenLinkCheckModal() {
    const lang = edState.activeLang;
    const task = edGetLinkCheckTask(lang);
    if (!task || !task.task_id) return;
    edState.linkCheckModalLang = lang;
    edState.linkCheckDetailTask = null;
    edState.linkCheckDetailError = '';
    $('edLinkCheckMask').hidden = false;
    edRenderLinkCheckModal();
    if (edLinkCheckNeedsPolling(task)) {
      edPollLinkCheck(lang);
    } else {
      edLoadLinkCheckDetail(lang);
    }
  }

  function edPollLinkCheck(lang) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    if (!pid || !lang) return Promise.resolve();
    edStopLinkCheckPoll();
    return fetchJSON(`/medias/api/products/${pid}/link-check/${encodeURIComponent(lang)}`)
      .then((data) => {
        if (data && data.task) {
          edSetLinkCheckTask(lang, data.task);
        }
        if (lang === edState.activeLang) {
          edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
        }
        if (edState.linkCheckModalLang === lang) {
          edRenderLinkCheckModal();
        }
        const task = edGetLinkCheckTask(lang);
        if (task && edState.linkCheckModalLang === lang && !edLinkCheckNeedsPolling(task)) {
          return edLoadLinkCheckDetail(lang);
        }
        if (task && lang === edState.activeLang && edLinkCheckNeedsPolling(task) && !$('edMask').hidden) {
          edState.linkCheckPollTimer = setTimeout(() => edPollLinkCheck(lang), 2000);
        }
        return data;
      })
      .catch((err) => {
        if (lang === edState.activeLang) {
          edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
        }
        if (edState.linkCheckModalLang === lang) {
          edState.linkCheckDetailError = err.message || String(err);
          edRenderLinkCheckModal();
        }
      });
  }

  function edStartLinkCheck() {
    return (async () => {
      const pid = edState.productData && edState.productData.product && edState.productData.product.id;
      if (!pid) return;
      edFlushProductUrl();
      const lang = edState.activeLang;
      const url = edCurrentLinkUrl(lang);
      if (!url || !/^https?:\/\//i.test(url)) {
        alert('请先填写有效的商品链接');
        $('edProductUrl') && $('edProductUrl').focus();
        return;
      }

      const actionBtn = $('edLinkCheckBtn');
      if (actionBtn) {
        actionBtn.disabled = true;
        actionBtn.innerHTML = `${icon('search', 14)}<span>检测中...</span>`;
      }

      try {
        const data = await fetchJSON(`/medias/api/products/${pid}/link-check`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lang, link_url: url }),
        });
        edSetLinkCheckTask(lang, {
          task_id: data.task_id,
          status: data.status || 'queued',
          link_url: url,
          checked_at: new Date().toISOString(),
          summary: {
            overall_decision: 'running',
            pass_count: 0,
            replace_count: 0,
            review_count: 0,
          },
        });
        edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
        edOpenLinkCheckModal();
      } catch (e) {
        alert('链接检测启动失败：' + (e.message || e));
      } finally {
        if (actionBtn) {
          actionBtn.disabled = false;
        }
        edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
      }
    })();
  }

  async function edRenderActiveLangView() {
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
    edRenderLinkCheckSummary(edGetLinkCheckTask(lang));
    if (edGetLinkCheckTask(lang)) {
      edPollLinkCheck(lang);
    } else {
      edStopLinkCheckPoll();
    }

    await edRefreshDetailImagesPanel(lang);

    // EN 主图校验 + 保存按钮
    const hasEn = !!(edState.productData && edState.productData.covers && edState.productData.covers['en']);
    const warn = $('edEnCoverWarn');
    if (warn) warn.hidden = hasEn;
    const saveBtn = $('edSaveBtn');
    if (saveBtn) {
      saveBtn.disabled = !hasEn;
      saveBtn.title = hasEn ? '' : '必须先上传英文主图';
    }
    edSyncDetailImagesDownloadZipButton();
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

    const mkIdRaw = ($('edMkId').value || '').trim();
    if (mkIdRaw && !/^\d{1,8}$/.test(mkIdRaw)) {
      alert('明空 ID 必须是 1-8 位数字');
      $('edMkId').focus();
      return;
    }

    const payload = {
      name,
      product_code: code,
      mk_id: mkIdRaw === '' ? null : parseInt(mkIdRaw, 10),
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
      if (msg.includes('mk_id_conflict') || msg.includes('明空 ID 已被其他产品占用')) {
        alert('明空 ID 已被其他产品占用');
        $('edMkId').focus();
      } else if (msg.includes('mk_id_invalid')) {
        alert('明空 ID 必须是 1-8 位数字');
        $('edMkId').focus();
      } else if (msg.includes('已被占用')) {
        alert('产品 ID 已被占用');
        $('edCode').focus();
      } else {
        alert('保存失败：' + msg);
      }
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
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!$('edLinkCheckMask').hidden) {
        edCloseLinkCheckModal();
        return;
      }
      if (!$('edMask').hidden) edHide();
    });

    // 产品链接：输入变化时 flush 到内存；产品 ID 改了需刷新 placeholder/hint
    const edCodeInput = $('edCode');
    const edUrlInput = $('edProductUrl');
    if (edCodeInput) {
      edCodeInput.addEventListener('input', () => {
        edRenderProductUrl(edState.activeLang);
        edRenderLinkCheckSummary(edGetLinkCheckTask(edState.activeLang));
      });
    }
    if (edUrlInput) {
      edUrlInput.addEventListener('blur', () => {
        edFlushProductUrl();
        edRenderProductUrl(edState.activeLang);  // 刷新 hint（是否已自定义）
        edRenderLinkCheckSummary(edGetLinkCheckTask(edState.activeLang));
      });
    }
    $('edLinkCheckBtn') && $('edLinkCheckBtn').addEventListener('click', edStartLinkCheck);
    $('edLinkCheckViewBtn') && $('edLinkCheckViewBtn').addEventListener('click', edOpenLinkCheckModal);
    $('edLinkCheckClose') && $('edLinkCheckClose').addEventListener('click', edCloseLinkCheckModal);
    $('edLinkCheckDoneBtn') && $('edLinkCheckDoneBtn').addEventListener('click', edCloseLinkCheckModal);
    $('edLinkCheckRefreshBtn') && $('edLinkCheckRefreshBtn').addEventListener('click', () => {
      const lang = edState.linkCheckModalLang || edState.activeLang;
      const task = edGetLinkCheckTask(lang);
      if (!task) return;
      if (edLinkCheckNeedsPolling(task)) {
        edPollLinkCheck(lang);
      } else {
        edLoadLinkCheckDetail(lang);
      }
    });
    $('edLinkCheckMask') && $('edLinkCheckMask').addEventListener('click', (e) => {
      if (e.target.id === 'edLinkCheckMask') edCloseLinkCheckModal();
    });

    // 商品详情图：从商品链接一键下载（后台任务 + 进度弹窗）
    const edFromUrlBtn = $('edDetailImagesFromUrlBtn');
    const edDownloadZipBtn = $('edDetailImagesDownloadZipBtn');
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
        edRefreshDetailImagesPanel(edState.activeLang).catch((err) => {
          console.error('[detail-images] refresh after from-url failed:', err);
        });
      }

      $('edFromUrlClose').addEventListener('click', closeFromUrlModal);
      $('edFromUrlDoneBtn').addEventListener('click', closeFromUrlModal);
      $('edFromUrlMask').addEventListener('click', (e) => {
        if (e.target.id === 'edFromUrlMask') closeFromUrlModal();
      });

      function langDisplayName(code) {
        const l = (LANGUAGES || []).find(x => x && x.code === code);
        return (l && (l.name_zh || l.code)) || (code || '').toUpperCase();
      }

      function awaitFromUrlConfirm(existingCount, langCode) {
        return new Promise((resolve) => {
          const mask = $('edFromUrlConfirmMask');
          const body = $('edFromUrlConfirmBody');
          const okBtn = $('edFromUrlConfirmOkBtn');
          const cancelBtn = $('edFromUrlConfirmCancelBtn');
          const closeBtn = $('edFromUrlConfirmClose');
          if (!mask || !body || !okBtn || !cancelBtn) { resolve(true); return; }
          body.textContent = `即将清空当前【${langDisplayName(langCode)}】语种下 ${existingCount} 张详情图，并重新从商品链接抓取。该操作不可撤销。`;
          mask.hidden = false;
          const cleanup = (val) => {
            mask.hidden = true;
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            if (closeBtn) closeBtn.removeEventListener('click', onCancel);
            mask.removeEventListener('click', onMaskClick);
            resolve(val);
          };
          const onOk = () => cleanup(true);
          const onCancel = () => cleanup(false);
          const onMaskClick = (e) => { if (e.target.id === 'edFromUrlConfirmMask') cleanup(false); };
          okBtn.addEventListener('click', onOk);
          cancelBtn.addEventListener('click', onCancel);
          if (closeBtn) closeBtn.addEventListener('click', onCancel);
          mask.addEventListener('click', onMaskClick);
        });
      }

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

        const ctrl = edDetailImagesCtrl;
        const existingCount = ctrl && ctrl.items ? ctrl.items().length : 0;
        let clearExisting = false;
        if (existingCount > 0) {
          const ok = await awaitFromUrlConfirm(existingCount, lang);
          if (!ok) return;
          clearExisting = true;
        }

        openFromUrlModal();
        try {
          const resp = await fetch(`/medias/api/products/${pid}/detail-images/from-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, lang, clear_existing: clearExisting }),
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

    if (edDownloadZipBtn) {
      edDownloadZipBtn.addEventListener('click', () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        const lang = (edState.activeLang || 'en').trim().toLowerCase();
        if (!pid || edDownloadZipBtn.disabled) return;
        window.location.href = `/medias/api/products/${pid}/detail-images/download-zip?lang=${encodeURIComponent(lang)}&kind=image`;
      });
    }

    const edGifDownloadZipBtn = $('edDetailGifImagesDownloadZipBtn');
    if (edGifDownloadZipBtn) {
      edGifDownloadZipBtn.addEventListener('click', () => {
        const pid = edState.productData && edState.productData.product && edState.productData.product.id;
        const lang = (edState.activeLang || 'en').trim().toLowerCase();
        if (!pid || edGifDownloadZipBtn.disabled) return;
        window.location.href = `/medias/api/products/${pid}/detail-images/download-zip?lang=${encodeURIComponent(lang)}&kind=gif`;
      });
    }

    // edCwAddBtn：按当前 activeLang 添加文案条目
    $('edDetailImagesTranslateBtn') && $('edDetailImagesTranslateBtn').addEventListener('click', () => {
      edStartDetailTranslate().catch((err) => {
        console.error('[detail-images] start translate failed:', err);
      });
    });
    $('edDetailTranslateTaskClose') && $('edDetailTranslateTaskClose').addEventListener('click', edCloseDetailTranslateTaskModal);
    $('edDetailTranslateTaskMask') && $('edDetailTranslateTaskMask').addEventListener('click', (e) => {
      if (e.target.id === 'edDetailTranslateTaskMask') edCloseDetailTranslateTaskModal();
    });
    $('edDetailTranslateHistory') && $('edDetailTranslateHistory').addEventListener('click', (e) => {
      const btn = e.target && e.target.closest('[data-retranslate-lang]');
      if (!btn) return;
      edStartDetailTranslate(btn.getAttribute('data-retranslate-lang') || edState.activeLang).catch((err) => {
        console.error('[detail-images] retranslate failed:', err);
      });
    });
    $('edDetailTranslateStatus') && $('edDetailTranslateStatus').addEventListener('click', async (e) => {
      const btn = e.target && e.target.closest('[data-apply-translate-task]');
      if (!btn) return;
      const taskId = btn.getAttribute('data-apply-translate-task') || '';
      const lang = (btn.getAttribute('data-apply-translate-lang') || edState.activeLang || '').trim().toLowerCase();
      const pid = edState.productData && edState.productData.product && edState.productData.product.id;
      if (!pid || !taskId || !lang) return;
      const orig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '回填中...';
      try {
        const data = await fetchJSON(
          `/medias/api/products/${pid}/detail-images/${encodeURIComponent(lang)}/apply-translate-task/${encodeURIComponent(taskId)}`,
          { method: 'POST' },
        );
        const msg = data.skipped_failed > 0
          ? `已回填 ${data.applied} 张（忽略 ${data.skipped_failed} 张失败）`
          : `已回填 ${data.applied} 张`;
        alert(msg);
        await edRefreshDetailImagesPanel(lang);
      } catch (err) {
        alert('手动回填失败：' + (err && err.message ? err.message : err));
        btn.disabled = false;
        btn.textContent = orig;
      }
    });

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

(function () {
  const $ = (id) => document.getElementById(id);
  const drawerMask = $('rsDrawerMask');
  const summary = $('rsSummary');
  const list = $('rsList');
  const uploadMask = $('rsUploadMask');
  const uploadForm = $('rsUploadForm');
  const uploadSubmit = $('rsUploadSubmit');
  const translateMask = $('rsTranslateMask');
  const translateTitleMeta = $('rstTitleMeta');
  const translateRsList = $('rstRsList');
  const translateLangs = $('rstLangs');
  const translatePreview = $('rstPreview');
  const translateSubmit = $('rstSubmit');
  const uiState = {
    currentPid: null,
    currentName: '',
    translatePid: null,
    translateName: '',
  };

  if (!drawerMask || !list || !uploadMask || !uploadForm || !translateMask || !translateRsList || !translateLangs || !translatePreview || !translateSubmit) {
    return;
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function fmtRawDuration(seconds) {
    const value = Number(seconds || 0);
    if (!value) return '时长 —';
    const mins = value / 60;
    if (mins >= 60) return `时长 ${(mins / 60).toFixed(1)}h`;
    return `时长 ${mins.toFixed(1)}m`;
  }

  function fmtRawSize(bytes) {
    const value = Number(bytes || 0);
    if (!value) return '大小 —';
    return `大小 ${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function requestJSON(url, options) {
    const resp = await fetch(url, options);
    if (resp.ok) return resp.json();
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `${resp.status}`);
  }

  function syncRawSourceCount(pid, count) {
    document.querySelectorAll(`.js-raw-sources[data-pid="${pid}"]`).forEach((btn) => {
      btn.textContent = `原始视频 (${count})`;
    });
  }

  function setSummary(items) {
    const name = uiState.currentName || (uiState.currentPid ? `产品 #${uiState.currentPid}` : '当前产品');
    summary.textContent = `${name} · 共 ${items.length} 条原始去字幕素材`;
  }

  function renderRawSourceRow(it) {
    const thumb = it.cover_url
      ? `<img src="${escapeHtml(it.cover_url)}" alt="${escapeHtml(it.display_name || '原始素材封面')}" loading="lazy">`
      : `<svg width="24" height="24" aria-hidden="true"><use href="#ic-film"/></svg>`;
    const title = escapeHtml(it.display_name || `原始视频 #${it.id}`);
    return `
      <li class="oc-rs-row">
        <div class="oc-rs-thumb">${thumb}</div>
        <div class="oc-rs-meta">
          <div class="oc-rs-title" title="${title}">${title}</div>
          <div class="oc-rs-subtitle">${fmtRawDuration(it.duration_seconds)} · ${fmtRawSize(it.file_size)}</div>
          <div class="oc-rs-links">
            <a class="oc-rs-link" href="${escapeHtml(it.video_url)}" target="_blank" rel="noopener noreferrer">查看视频</a>
            <a class="oc-rs-link" href="${escapeHtml(it.cover_url)}" target="_blank" rel="noopener noreferrer">查看封面</a>
          </div>
        </div>
        <div class="oc-rs-actions">
          <button type="button" class="oc-btn sm ghost js-rs-del" data-rid="${it.id}">删除</button>
        </div>
      </li>`;
  }

  function openRawSourceDrawer(pid, name) {
    uiState.currentPid = String(pid);
    uiState.currentName = name || '';
    drawerMask.hidden = false;
  }

  function closeRawSourceDrawer() {
    drawerMask.hidden = true;
    uiState.currentPid = null;
    uiState.currentName = '';
    list.innerHTML = '';
    summary.textContent = '加载中…';
  }

  function openRawSourceUpload() {
    if (!window.MEDIAS_TOS_READY) {
      alert('TOS 未配置，无法上传原始素材');
      return;
    }
    uploadMask.hidden = false;
    if ($('rsVideoInput')) $('rsVideoInput').focus();
  }

  function closeRawSourceUpload() {
    uploadMask.hidden = true;
    uploadForm.reset();
    uploadSubmit.disabled = false;
  }

  async function refreshRawSourceList(pid) {
    const data = await requestJSON(`/medias/api/products/${pid}/raw-sources`);
    const items = data.items || [];
    setSummary(items);
    list.innerHTML = items.length
      ? items.map(renderRawSourceRow).join('')
      : '<li class="oc-rs-empty">还没有原始去字幕素材，先上传第一条再发起视频翻译。</li>';
    syncRawSourceCount(pid, items.length);
    return items;
  }

  async function submitRawSourceUpload(event) {
    event.preventDefault();
    if (!uiState.currentPid) return;
    const fd = new FormData(uploadForm);
    uploadSubmit.disabled = true;
    try {
      await requestJSON(`/medias/api/products/${uiState.currentPid}/raw-sources`, {
        method: 'POST',
        body: fd,
      });
      closeRawSourceUpload();
      await refreshRawSourceList(uiState.currentPid);
    } catch (err) {
      alert(`上传失败：${err.message || err}`);
      uploadSubmit.disabled = false;
    }
  }

  async function deleteRawSource(del) {
    if (!uiState.currentPid) return;
    if (!confirm('删除后无法恢复，该素材不会再出现在翻译弹窗，但已翻译出来的多语种素材不受影响。确定？')) {
      return;
    }
    try {
      await requestJSON(`/medias/api/raw-sources/${del.dataset.rid}`, { method: 'DELETE' });
      await refreshRawSourceList(uiState.currentPid);
    } catch (err) {
      alert(`删除失败：${err.message || err}`);
    }
  }

  function renderTranslateRawSourceChoice(it) {
    const thumb = it.cover_url
      ? `<img src="${escapeHtml(it.cover_url)}" alt="${escapeHtml(it.display_name || '原始素材封面')}" loading="lazy">`
      : `<div class="ph"><svg width="20" height="20" aria-hidden="true"><use href="#ic-film"/></svg></div>`;
    const title = escapeHtml(it.display_name || `原始视频 #${it.id}`);
    return `
      <li class="oc-rst-choice">
        <label>
          <input type="checkbox" value="${it.id}" checked>
          ${thumb}
          <span class="oc-rst-choice-meta">
            <span class="oc-rst-choice-title" title="${title}">${title}</span>
            <span class="oc-rst-choice-subtitle">${fmtRawDuration(it.duration_seconds)} · ${fmtRawSize(it.file_size)}</span>
          </span>
        </label>
      </li>`;
  }

  function renderTranslateLanguageChoice(lang) {
    const name = escapeHtml(lang.name_zh || lang.code.toUpperCase());
    return `
      <label class="oc-rst-lang">
        <input type="checkbox" value="${escapeHtml(lang.code)}">
        <span>${name}</span>
      </label>`;
  }

  function updateTranslatePreview() {
    const rawCount = translateRsList.querySelectorAll('input[type="checkbox"]:checked').length;
    const langCount = translateLangs.querySelectorAll('input[type="checkbox"]:checked').length;
    if (!rawCount || !langCount) {
      translatePreview.textContent = '请选择至少 1 条原始视频和 1 个目标语言';
      translateSubmit.disabled = true;
      return;
    }
    translatePreview.textContent = `将生成 ${rawCount} × ${langCount} = ${rawCount * langCount} 条多语种素材`;
    translateSubmit.disabled = false;
  }

  function closeTranslateDialog() {
    translateMask.hidden = true;
    uiState.translatePid = null;
    uiState.translateName = '';
    translateRsList.innerHTML = '';
    translateLangs.innerHTML = '';
    translateTitleMeta.textContent = '';
    translatePreview.textContent = '请选择原始视频和目标语言';
    translateSubmit.disabled = true;
    translateSubmit.textContent = '提交翻译';
  }

  async function openTranslateDialog(pid, name) {
    uiState.translatePid = String(pid);
    uiState.translateName = name || '';
    translateMask.hidden = false;
    translateTitleMeta.textContent = uiState.translateName ? ` · ${uiState.translateName}` : '';
    translateRsList.innerHTML = '<li class="oc-rs-empty">加载原始视频中…</li>';
    translateLangs.innerHTML = '<div class="oc-rs-empty">加载语言中…</div>';
    translatePreview.textContent = '加载中…';
    translateSubmit.disabled = true;

    try {
      const [rawData, langData] = await Promise.all([
        requestJSON(`/medias/api/products/${pid}/raw-sources`),
        requestJSON('/medias/api/languages'),
      ]);
      const items = rawData.items || [];
      const languages = (langData.items || langData.languages || []).filter((lang) => lang.code !== 'en');

      translateRsList.innerHTML = items.length
        ? items.map(renderTranslateRawSourceChoice).join('')
        : '<li class="oc-rs-empty">还没有原始去字幕素材，请先上传素材。</li>';
      translateLangs.innerHTML = languages.length
        ? languages.map(renderTranslateLanguageChoice).join('')
        : '<div class="oc-rs-empty">暂无可选目标语言</div>';
      updateTranslatePreview();
    } catch (err) {
      translateRsList.innerHTML = `<li class="oc-rs-empty">加载失败：${escapeHtml(err.message || err)}</li>`;
      translateLangs.innerHTML = '<div class="oc-rs-empty">请稍后重试</div>';
      translatePreview.textContent = '翻译弹窗初始化失败';
      translateSubmit.disabled = true;
    }
  }

  async function submitTranslateTask() {
    const pid = uiState.translatePid;
    if (!pid) return;
    const raw_ids = Array.from(
      translateRsList.querySelectorAll('input[type="checkbox"]:checked'),
      (input) => Number(input.value),
    );
    const target_langs = Array.from(
      translateLangs.querySelectorAll('input[type="checkbox"]:checked'),
      (input) => input.value,
    );
    if (!raw_ids.length || !target_langs.length) {
      updateTranslatePreview();
      return;
    }

    translateSubmit.disabled = true;
    translateSubmit.textContent = '提交中…';
    try {
      const data = await requestJSON(`/medias/api/products/${pid}/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_ids, target_langs }),
      });
      const taskId = data.task_id;
      closeTranslateDialog();
      window.location.href = `/tasks/${taskId}`;
    } catch (err) {
      alert(`提交失败：${err.message || err}`);
      translateSubmit.textContent = '提交翻译';
      updateTranslatePreview();
    }
  }

  translateRsList.addEventListener('change', updateTranslatePreview);
  translateLangs.addEventListener('change', updateTranslatePreview);
  uploadForm.addEventListener('submit', submitRawSourceUpload);
  translateSubmit.addEventListener('click', submitTranslateTask);

  document.addEventListener('click', async (event) => {
    const openBtn = event.target.closest('.js-raw-sources');
    if (openBtn) {
      event.preventDefault();
      openRawSourceDrawer(openBtn.dataset.pid, openBtn.dataset.name || '');
      try {
        await refreshRawSourceList(openBtn.dataset.pid);
      } catch (err) {
        list.innerHTML = `<li class="oc-rs-empty">加载失败：${escapeHtml(err.message || err)}</li>`;
        summary.textContent = '原始素材列表加载失败';
      }
      return;
    }

    const translateBtn = event.target.closest('.js-translate');
    if (translateBtn) {
      event.preventDefault();
      await openTranslateDialog(translateBtn.dataset.pid, translateBtn.dataset.name || '');
      return;
    }

    if (event.target === drawerMask || event.target.closest('#rsDrawerClose')) {
      closeRawSourceDrawer();
      return;
    }

    if (event.target === uploadMask || event.target.closest('#rsUploadClose') || event.target.closest('#rsUploadCancel')) {
      closeRawSourceUpload();
      return;
    }

    if (event.target === translateMask || event.target.closest('#rstClose') || event.target.closest('#rstCancel')) {
      closeTranslateDialog();
      return;
    }

    const del = event.target.closest('.js-rs-del');
    if (del) {
      event.preventDefault();
      await deleteRawSource(del);
      return;
    }

    if (event.target.closest('#rsUploadBtn')) {
      event.preventDefault();
      openRawSourceUpload();
    }
  });

  window.MediasRawSources = {
    escapeHtml,
    refreshRawSourceList,
    syncRawSourceCount,
    openTranslateDialog,
  };
})();
