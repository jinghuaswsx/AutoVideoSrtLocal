(function() {
  const state = { page: 1, editId: null };
  const $ = (id) => document.getElementById(id);
  const isAdmin = !!window.PL_IS_ADMIN;

  function icon(name, size = 14) {
    return `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#ic-${name}"/></svg>`;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { /* noop */ }
    if (!res.ok) throw new Error((data && data.error) || text || `HTTP ${res.status}`);
    return data;
  }

  // ---------- Toast ----------
  let toastTimer = null;
  function toast(msg, kind = 'ok') {
    const t = $('toast');
    $('toastText').textContent = msg;
    t.className = 'oc-toast show ' + kind;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'oc-toast'; }, 2200);
  }

  async function copyToClipboard(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast('已复制到剪贴板', 'ok');
    } catch (e) {
      toast('复制失败：' + (e.message || e), 'err');
    }
  }

  // ---------- List ----------
  async function loadList() {
    const kw = $('kw').value.trim();
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
    renderSkeleton();
    try {
      const data = await fetchJSON('/prompt-library/api/items?' + params);
      renderGrid(data.items);
      renderPager(data.total, data.page, data.page_size);
      $('totalPill').textContent = `共 ${data.total} 条`;
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
    $('grid').innerHTML = Array.from({ length: 6 }, () => '<div class="oc-skel"></div>').join('');
  }

  function renderGrid(items) {
    const grid = $('grid');
    if (!items || !items.length) {
      const cta = isAdmin
        ? `<button class="oc-btn primary" id="emptyCreate">${icon('plus', 14)}<span>新建提示词</span></button>`
        : '';
      grid.innerHTML = `
        <div class="oc-state">
          <div class="icon">${icon('book', 28)}</div>
          <p class="title">${isAdmin ? '还没有提示词' : '暂无提示词'}</p>
          <p class="desc">${isAdmin ? '从 AI 生成开始，或手动录入一条常用提示词' : '请联系管理员录入常用提示词'}</p>
          ${cta}
        </div>`;
      const ec = $('emptyCreate');
      if (ec) ec.addEventListener('click', () => openCreate());
      return;
    }
    grid.innerHTML = items.map(cardHTML).join('');
    grid.querySelectorAll('[data-pid]').forEach(el => {
      const pid = +el.dataset.pid;
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-stop]')) return;
        openView(pid);
      });
    });
    grid.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      const pid = +b.dataset.copy;
      const full = await fetchJSON('/prompt-library/api/items/' + pid);
      copyToClipboard(full.content || '');
    }));
    grid.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation(); openEdit(+b.dataset.edit);
    }));
    grid.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation(); deleteItem(+b.dataset.del, b.dataset.name || '');
    }));
  }

  function cardHTML(p) {
    const desc = p.description ? escapeHtml(p.description) : '<span style="color:var(--oc-fg-subtle)">无描述</span>';
    const author = p.updated_by_name || p.created_by_name || '—';
    const adminActions = isAdmin ? `
      <button class="oc-icon-btn" data-stop data-edit="${p.id}" title="编辑">${icon('edit', 14)}</button>
      <button class="oc-icon-btn danger" data-stop data-del="${p.id}" data-name="${escapeHtml(p.name)}" title="删除">${icon('trash', 14)}</button>
    ` : '';
    return `
      <article class="oc-card" data-pid="${p.id}" tabindex="0">
        <div class="top">
          <div class="name">${escapeHtml(p.name)}</div>
        </div>
        <div class="desc">${desc}</div>
        <div class="meta">
          <span class="left">${icon('user', 11)} ${escapeHtml(author)} · ${icon('clock', 11)} ${fmtDate(p.updated_at)}</span>
          <span class="actions">
            <button class="oc-icon-btn" data-stop data-copy="${p.id}" title="复制全文">${icon('copy', 14)}</button>
            ${adminActions}
          </span>
        </div>
      </article>`;
  }

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

  // ---------- View modal ----------
  async function openView(id) {
    try {
      const p = await fetchJSON('/prompt-library/api/items/' + id);
      $('viewTitle').textContent = p.name || '提示词详情';
      $('viewDesc').textContent = p.description || '';
      const meta = [
        ['创建人', p.created_by_name || '—'],
        ['最近修改人', p.updated_by_name || '—'],
        ['创建时间', fmtDate(p.created_at)],
        ['更新时间', fmtDate(p.updated_at)],
      ];
      $('viewMeta').innerHTML = meta.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join('');
      $('viewContent').textContent = p.content || '';
      $('viewCopyBtn').onclick = () => copyToClipboard(p.content || '');
      $('viewMask').hidden = false;
    } catch (e) {
      toast('加载失败：' + (e.message || e), 'err');
    }
  }
  function closeView() { $('viewMask').hidden = true; }

  // ---------- Create / Edit ----------
  function openCreate() {
    state.editId = null;
    $('editTitle').textContent = '新建提示词';
    $('aiReq').value = '';
    $('pName').value = '';
    $('pDesc').value = '';
    $('pContent').value = '';
    $('editMask').hidden = false;
    setTimeout(() => $('aiReq').focus(), 60);
  }

  async function openEdit(id) {
    try {
      const p = await fetchJSON('/prompt-library/api/items/' + id);
      state.editId = id;
      $('editTitle').textContent = '编辑提示词';
      $('aiReq').value = '';
      $('pName').value = p.name || '';
      $('pDesc').value = p.description || '';
      $('pContent').value = p.content || '';
      $('editMask').hidden = false;
      setTimeout(() => $('pName').focus(), 60);
    } catch (e) {
      toast('加载失败：' + (e.message || e), 'err');
    }
  }

  function closeEdit() { $('editMask').hidden = true; state.editId = null; }

  async function saveEdit() {
    const name = $('pName').value.trim();
    const description = $('pDesc').value.trim();
    const content = $('pContent').value.trim();
    if (!name) { toast('请填写名称', 'err'); $('pName').focus(); return; }
    if (!content) { toast('请填写提示词正文', 'err'); $('pContent').focus(); return; }

    const payload = { name, description, content };
    try {
      if (state.editId) {
        await fetchJSON('/prompt-library/api/items/' + state.editId, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        toast('已保存');
      } else {
        await fetchJSON('/prompt-library/api/items', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        toast('已创建');
      }
      closeEdit();
      loadList();
    } catch (e) {
      toast('保存失败：' + (e.message || e), 'err');
    }
  }

  async function generateAI() {
    const req = $('aiReq').value.trim();
    if (!req) { toast('请先描述你的需求', 'err'); $('aiReq').focus(); return; }
    const btn = $('aiGenBtn');
    const iconEl = $('aiGenIcon');
    const textEl = $('aiGenText');
    btn.disabled = true;
    iconEl.innerHTML = '<use href="#ic-loader"/>';
    iconEl.style.animation = 'spin 1s linear infinite';
    textEl.textContent = '生成中…';
    try {
      const data = await fetchJSON('/prompt-library/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requirement: req }),
      });
      if (data.name && !$('pName').value.trim()) $('pName').value = data.name;
      if (data.description && !$('pDesc').value.trim()) $('pDesc').value = data.description;
      if (data.content) $('pContent').value = data.content;
      toast('已填入下方字段');
    } catch (e) {
      toast('生成失败：' + (e.message || e), 'err');
    } finally {
      btn.disabled = false;
      iconEl.innerHTML = '<use href="#ic-sparkles"/>';
      iconEl.style.animation = '';
      textEl.textContent = '生成';
    }
  }

  async function deleteItem(id, name) {
    if (!confirm(`确认删除提示词「${name}」？此操作不可恢复。`)) return;
    try {
      await fetchJSON('/prompt-library/api/items/' + id, { method: 'DELETE' });
      toast('已删除');
      loadList();
    } catch (e) {
      toast('删除失败：' + (e.message || e), 'err');
    }
  }

  // ---------- Events ----------
  document.addEventListener('DOMContentLoaded', () => {
    const styleEl = document.createElement('style');
    styleEl.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
    document.head.appendChild(styleEl);

    $('searchBtn').addEventListener('click', () => { state.page = 1; loadList(); });
    $('kw').addEventListener('keydown', (e) => { if (e.key === 'Enter') { state.page = 1; loadList(); } });

    if (isAdmin) {
      const createBtn = $('createBtn');
      if (createBtn) createBtn.addEventListener('click', openCreate);
      $('editClose').addEventListener('click', closeEdit);
      $('editCancel').addEventListener('click', closeEdit);
      $('editSave').addEventListener('click', saveEdit);
      $('aiGenBtn').addEventListener('click', generateAI);
      $('editMask').addEventListener('click', (e) => { if (e.target.id === 'editMask') closeEdit(); });
    }

    $('viewClose').addEventListener('click', closeView);
    $('viewCloseBtn').addEventListener('click', closeView);
    $('viewMask').addEventListener('click', (e) => { if (e.target.id === 'viewMask') closeView(); });

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (isAdmin && !$('editMask').hidden) closeEdit();
      else if (!$('viewMask').hidden) closeView();
    });

    loadList();
  });
})();
