(function() {
  const state = { page: 1, current: null };
  const $ = (id) => document.getElementById(id);

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    return d.toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-');
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function loadList() {
    const kw = $('kw').value.trim();
    const archived = $('archived').checked;
    const scopeAll = window.MEDIAS_IS_ADMIN && $('scopeAll') && $('scopeAll').checked;
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
    if (archived) params.set('archived', '1');
    if (scopeAll) params.set('scope', 'all');
    const data = await fetchJSON('/medias/api/products?' + params);
    renderRows(data.items);
    renderPager(data.total, data.page, data.page_size);
  }

  function renderRows(items) {
    const tb = $('tbody');
    tb.innerHTML = items.map(p => `
      <tr>
        <td>${p.id}</td>
        <td><div>${escapeHtml(p.name)}</div><div style="color:#9ca3af;font-size:12px">色号人: ${escapeHtml(p.color_people || '-')}</div></td>
        <td><span class="medias-count">${p.items_count || 0}</span></td>
        <td>${p.source ? `<span class="medias-pill">${escapeHtml(p.source)}</span>` : '-'}</td>
        <td>${fmtDate(p.created_at)}</td>
        <td>${fmtDate(p.updated_at)}</td>
        <td>
          <button class="btn btn-ghost btn-sm" data-edit="${p.id}">编辑</button>
          <button class="btn btn-sm" style="background:#fee2e2;color:#dc2626" data-del="${p.id}">删除</button>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="7" style="text-align:center;padding:40px;color:#9ca3af">暂无产品</td></tr>';
    tb.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => openEdit(+b.dataset.edit)));
    tb.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => deleteProduct(+b.dataset.del)));
  }

  function renderPager(total, page, pageSize) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const p = $('pager');
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
    if (!confirm('确认删除该产品及其所有素材？')) return;
    await fetch('/medias/api/products/' + pid, { method: 'DELETE' });
    loadList();
  }

  async function openEdit(pid) {
    const data = await fetchJSON('/medias/api/products/' + pid);
    state.current = data;
    $('modalTitle').textContent = '编辑素材';
    $('mName').value = data.product.name || '';
    $('mColor').value = data.product.color_people || '';
    $('mSource').value = data.product.source || '';
    renderCopywritings(data.copywritings);
    renderItems(data.items);
    $('uploadProgress').innerHTML = '';
    $('editMask').style.display = 'flex';
  }

  function openCreate() {
    state.current = { product: null, copywritings: [], items: [] };
    $('modalTitle').textContent = '添加产品素材';
    $('mName').value = ''; $('mColor').value = ''; $('mSource').value = '';
    renderCopywritings([]);
    renderItems([]);
    $('uploadProgress').innerHTML = '';
    $('editMask').style.display = 'flex';
  }

  function closeModal() { $('editMask').style.display = 'none'; state.current = null; }

  function renderCopywritings(list) {
    const box = $('cwList');
    box.innerHTML = '';
    list.forEach((c, i) => box.appendChild(cwCard(c, i + 1)));
  }

  function cwCard(c, idx) {
    const d = document.createElement('div');
    d.className = 'medias-cw-card';
    d.innerHTML = `
      <div class="medias-cw-index">#${idx}</div>
      <button class="medias-cw-remove" type="button">×</button>
      <input data-field="title" placeholder="标题" value="${escapeHtml(c.title || '')}">
      <textarea data-field="body" placeholder="正文">${escapeHtml(c.body || '')}</textarea>
      <input data-field="description" placeholder="描述" value="${escapeHtml(c.description || '')}">
      <input data-field="ad_carrier" placeholder="广告媒体库" value="${escapeHtml(c.ad_carrier || '')}">
      <textarea data-field="ad_copy" placeholder="广告文案">${escapeHtml(c.ad_copy || '')}</textarea>
      <input data-field="ad_keywords" placeholder="广告词" value="${escapeHtml(c.ad_keywords || '')}">
    `;
    d.querySelector('.medias-cw-remove').addEventListener('click', () => { d.remove(); reindexCw(); });
    return d;
  }

  function reindexCw() {
    [...document.querySelectorAll('.medias-cw-card .medias-cw-index')].forEach((e, i) => e.textContent = '#' + (i + 1));
  }

  function collectCopywritings() {
    return [...document.querySelectorAll('.medias-cw-card')].map(card => {
      const o = {};
      card.querySelectorAll('[data-field]').forEach(el => { o[el.dataset.field] = el.value || null; });
      return o;
    });
  }

  function renderItems(items) {
    const g = $('itemsGrid');
    g.innerHTML = items.map(it => `
      <div class="medias-item-card" data-item="${it.id}">
        ${it.thumbnail_url ? `<img src="${it.thumbnail_url}">` : `<div style="aspect-ratio:16/9;background:#1f2937;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:12px">视频</div>`}
        <div class="medias-item-name">${escapeHtml(it.display_name || it.filename)}</div>
        <button class="medias-item-remove" type="button" title="删除">×</button>
      </div>
    `).join('');
    g.querySelectorAll('[data-item]').forEach(card => {
      card.querySelector('.medias-item-remove').addEventListener('click', () => removeItem(+card.dataset.item, card));
    });
  }

  async function removeItem(itemId, card) {
    if (!confirm('确认删除该素材？')) return;
    await fetch('/medias/api/items/' + itemId, { method: 'DELETE' });
    card.remove();
  }

  async function ensureProductIdForUpload() {
    if (state.current && state.current.product && state.current.product.id) return state.current.product.id;
    const name = $('mName').value.trim();
    if (!name) { alert('请先填写产品名称'); return null; }
    const res = await fetchJSON('/medias/api/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, color_people: $('mColor').value || null, source: $('mSource').value || null,
      }),
    });
    const full = await fetchJSON('/medias/api/products/' + res.id);
    state.current = full;
    $('modalTitle').textContent = '编辑素材';
    return res.id;
  }

  async function uploadFiles(files) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    const box = $('uploadProgress');
    for (const f of files) {
      const row = document.createElement('div'); row.textContent = `${f.name} · 上传中…`; box.appendChild(row);
      try {
        const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: f.name }),
        });
        const putRes = await fetch(boot.upload_url, { method: 'PUT', body: f });
        if (!putRes.ok) throw new Error('TOS 上传失败');
        await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ object_key: boot.object_key, filename: f.name, file_size: f.size }),
        });
        row.textContent = `${f.name} · 完成`;
      } catch (e) {
        row.textContent = `${f.name} · 失败：${e.message}`;
      }
    }
    const full = await fetchJSON('/medias/api/products/' + pid);
    state.current = full;
    renderItems(full.items);
    loadList();
  }

  async function save() {
    const name = $('mName').value.trim();
    if (!name) { alert('产品名称必填'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    await fetchJSON('/medias/api/products/' + pid, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, color_people: $('mColor').value || null, source: $('mSource').value || null,
        copywritings: collectCopywritings(),
      }),
    });
    closeModal();
    loadList();
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('searchBtn').addEventListener('click', () => { state.page = 1; loadList(); });
    $('kw').addEventListener('keydown', (e) => { if (e.key === 'Enter') { state.page = 1; loadList(); } });
    $('archived').addEventListener('change', () => { state.page = 1; loadList(); });
    if ($('scopeAll')) $('scopeAll').addEventListener('change', () => { state.page = 1; loadList(); });
    $('createBtn').addEventListener('click', openCreate);
    $('modalClose').addEventListener('click', closeModal);
    $('cancelBtn').addEventListener('click', closeModal);
    $('saveBtn').addEventListener('click', save);
    $('cwAddBtn').addEventListener('click', () => {
      $('cwList').appendChild(cwCard({}, $('cwList').children.length + 1));
    });
    $('uploadBtn').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', (e) => {
      const files = [...e.target.files];
      e.target.value = '';
      if (files.length) uploadFiles(files);
    });
    loadList();
  });
})();
