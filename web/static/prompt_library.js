(function() {
  const state = { page: 1, editId: null, current: null };
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
    if (!text) { toast('内容为空', 'err'); return; }
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
          <p class="desc">${isAdmin ? '录入你的第一条常用提示词' : '请联系管理员录入常用提示词'}</p>
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
    grid.querySelectorAll('[data-copy-zh]').forEach(b => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      const full = await fetchJSON('/prompt-library/api/items/' + b.dataset.copyZh);
      copyToClipboard(full.content_zh || full.content_en || '');
    }));
    grid.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation(); openEdit(+b.dataset.edit);
    }));
    grid.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation(); deleteItem(+b.dataset.del, b.dataset.name || '');
    }));
  }

  function langBadges(p) {
    const out = [];
    if (p.content_zh) out.push('<span class="lang-dot zh" title="已有中文版">中</span>');
    if (p.content_en) out.push('<span class="lang-dot en" title="已有英文版">EN</span>');
    return out.join('');
  }

  function cardHTML(p) {
    const desc = p.description ? escapeHtml(p.description) : '<span style="color:var(--oc-fg-subtle)">无描述</span>';
    const author = p.updated_by_name || p.created_by_name || '—';
    const badges = langBadges(p);
    const adminActions = isAdmin ? `
      <button class="oc-icon-btn" data-stop data-edit="${p.id}" title="编辑">${icon('edit', 14)}</button>
      <button class="oc-icon-btn danger" data-stop data-del="${p.id}" data-name="${escapeHtml(p.name)}" title="删除">${icon('trash', 14)}</button>
    ` : '';
    return `
      <article class="oc-card" data-pid="${p.id}" tabindex="0">
        <div class="top">
          <div class="name">${escapeHtml(p.name)}</div>
          <div class="lang-badges">${badges}</div>
        </div>
        <div class="desc">${desc}</div>
        <div class="meta">
          <span class="left">${icon('user', 11)} ${escapeHtml(author)} · ${icon('clock', 11)} ${fmtDate(p.updated_at)}</span>
          <span class="actions">
            <button class="oc-icon-btn" data-stop data-copy-zh="${p.id}" title="复制">${icon('copy', 14)}</button>
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
      state.current = p;
      $('viewTitle').textContent = p.name || '提示词详情';
      $('viewDesc').textContent = p.description || '';
      const meta = [
        ['创建人', p.created_by_name || '—'],
        ['最近修改人', p.updated_by_name || '—'],
        ['创建时间', fmtDate(p.created_at)],
        ['更新时间', fmtDate(p.updated_at)],
      ];
      $('viewMeta').innerHTML = meta.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join('');
      renderViewPanes(p);
      $('viewMask').hidden = false;
    } catch (e) {
      toast('加载失败：' + (e.message || e), 'err');
    }
  }

  function renderViewPanes(p) {
    const zhBox = $('viewContentZh');
    const enBox = $('viewContentEn');
    if (p.content_zh) {
      zhBox.classList.remove('empty'); zhBox.textContent = p.content_zh;
    } else {
      zhBox.classList.add('empty');
      zhBox.innerHTML = `<div><p>暂无中文版本</p><small>${isAdmin && p.content_en ? '点击中间按钮用 EN →&nbsp;中 生成' : isAdmin ? '编辑中录入' : '请等待管理员补齐'}</small></div>`;
    }
    if (p.content_en) {
      enBox.classList.remove('empty'); enBox.textContent = p.content_en;
    } else {
      enBox.classList.add('empty');
      enBox.innerHTML = `<div><p>No English version yet</p><small>${isAdmin && p.content_zh ? '点击中间按钮用 中 →&nbsp;EN 生成' : isAdmin ? '编辑中录入' : '请等待管理员补齐'}</small></div>`;
    }

    const hasZh = !!p.content_zh, hasEn = !!p.content_en;
    // 转换按钮：仅 admin 可点；条件 = 源有 + 目标空；两个都有则整列隐藏
    const zh2en = $('zh2enBtn'), en2zh = $('en2zhBtn');
    const col = $('convertCol');
    if (!isAdmin || (hasZh && hasEn)) {
      col.style.display = 'none';
      zh2en.hidden = true; en2zh.hidden = true;
    } else {
      col.style.display = '';
      zh2en.hidden = !(hasZh && !hasEn);
      en2zh.hidden = !(hasEn && !hasZh);
    }

    $('copyZhBtn').onclick = () => copyToClipboard(p.content_zh || '');
    $('copyEnBtn').onclick = () => copyToClipboard(p.content_en || '');
  }

  async function translate(direction) {
    if (!state.current) return;
    const btn = direction === 'zh2en' ? $('zh2enBtn') : $('en2zhBtn');
    const origHTML = btn.innerHTML;
    btn.classList.add('busy');
    btn.innerHTML = `${icon('loader', 12)}<span>${direction === 'zh2en' ? '翻译中…' : '翻译中…'}</span>`;
    btn.firstChild.style.animation = 'spin 1s linear infinite';
    try {
      const r = await fetchJSON(`/prompt-library/api/items/${state.current.id}/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction }),
      });
      if (r.lang === 'en') state.current.content_en = r.content;
      else state.current.content_zh = r.content;
      renderViewPanes(state.current);
      toast('已生成', 'ok');
      loadList();
    } catch (e) {
      btn.classList.remove('busy');
      btn.innerHTML = origHTML;
      toast('翻译失败：' + (e.message || e), 'err');
    }
  }

  function closeView() { $('viewMask').hidden = true; state.current = null; }

  // ---------- Create / Edit ----------
  function openCreate() {
    state.editId = null;
    $('editTitle').textContent = '新建提示词';
    $('aiReq').value = '';
    $('pName').value = '';
    $('pDesc').value = '';
    $('pContentZh').value = '';
    $('pContentEn').value = '';
    $('editMask').hidden = false;
    setTimeout(() => $('pName').focus(), 60);
  }

  async function openEdit(id) {
    try {
      const p = await fetchJSON('/prompt-library/api/items/' + id);
      state.editId = id;
      $('editTitle').textContent = '编辑提示词';
      $('aiReq').value = '';
      $('pName').value = p.name || '';
      $('pDesc').value = p.description || '';
      $('pContentZh').value = p.content_zh || '';
      $('pContentEn').value = p.content_en || '';
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
    const content_zh = $('pContentZh').value.trim();
    const content_en = $('pContentEn').value.trim();
    if (!name) { toast('请填写名称', 'err'); $('pName').focus(); return; }
    if (!content_zh && !content_en) {
      toast('中文或英文至少填一个', 'err'); $('pContentZh').focus(); return;
    }

    const payload = { name, description, content_zh, content_en };
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
      if (data.content) $('pContentZh').value = data.content;
      toast('已填入中文版，可在保存后再用详情页的 中→EN 生成英文版');
    } catch (e) {
      toast('生成失败：' + (e.message || e), 'err');
    } finally {
      btn.disabled = false;
      iconEl.innerHTML = '<use href="#ic-sparkles"/>';
      iconEl.style.animation = '';
      textEl.textContent = '生成并回填';
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
    styleEl.textContent = `
      @keyframes spin { to { transform: rotate(360deg); } }
      .lang-badges { display:inline-flex; gap:4px; flex-shrink:0; }
      .lang-dot { display:inline-flex; align-items:center; justify-content:center; min-width:22px; height:18px; padding:0 6px; border-radius:9999px; font-size:10px; font-weight:700; letter-spacing:0.3px; }
      .lang-dot.zh { background:var(--oc-accent-subtle); color:var(--oc-accent); }
      .lang-dot.en { background:var(--oc-cyan-subtle); color:var(--oc-cyan); }
    `;
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

      $('zh2enBtn').addEventListener('click', () => translate('zh2en'));
      $('en2zhBtn').addEventListener('click', () => translate('en2zh'));
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
