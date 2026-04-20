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
  };

  const state = { page: 1, pageSize: 20, total: 0 };

  async function fetchJSON(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok && resp.status !== 204) {
      const body = await resp.text();
      throw Object.assign(new Error(`HTTP ${resp.status}`), {
        status: resp.status, body,
      });
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  async function loadLanguages() {
    try {
      const data = await fetchJSON('/medias/api/languages');
      const sel = document.getElementById('f-lang');
      // 保留首项「全部」
      const all = document.createElement('option');
      all.value = '';
      all.textContent = '全部';
      sel.innerHTML = '';
      sel.appendChild(all);
      (data.languages || []).forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.code;
        opt.textContent = `${l.name_zh} (${l.code})`;
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

  // 服务端与内网不通，所有状态下「推送」按钮一律禁用。
  // 状态流转由推送端通过 openapi 回写，这里仅保留展示 + 历史 / 重置。
  const PUSH_DISABLED_TITLE = '服务端与内网不通，暂不可从管理后台发起推送';

  function renderActionCell(it) {
    if (!window.PUSH_IS_ADMIN) return '';
    if (it.status === 'pushed') {
      const date = (it.pushed_at || '').slice(0, 10);
      return `<span class="pushed-text">✓ 已推送 ${date}</span>
              <div class="action-menu">
                <button class="btn-mini" data-action="view-logs" data-id="${it.id}">历史</button>
                <button class="btn-mini" data-action="reset" data-id="${it.id}">重置</button>
              </div>`;
    }
    if (it.status === 'not_ready') {
      const missing = Object.entries(it.readiness)
        .filter(([, v]) => !v).map(([k]) => READINESS_LABELS[k]).join(' / ');
      return `<button class="btn-push" disabled title="缺少：${missing}">推送</button>`;
    }
    const label = it.status === 'failed' ? '× 失败' : '推送';
    return `<button class="btn-push" disabled title="${PUSH_DISABLED_TITLE}">${label}</button>`;
  }

  function renderRow(it) {
    const thumb = it.cover_url
      ? `<img class="thumb" src="${it.cover_url}" alt="">`
      : `<div class="thumb thumb-empty"></div>`;
    const durStr = (typeof it.duration_seconds === 'number') ? it.duration_seconds.toFixed(1) + 's' : '';
    const sizeStr = (it.file_size || 0).toLocaleString() + ' B';
    return `<tr data-id="${it.id}">
      <td>${thumb}</td>
      <td>
        <div class="product-name">${it.product_name || ''}</div>
        <div class="product-code">${it.product_code || ''}</div>
      </td>
      <td>
        <div class="item-name">${it.display_name || it.filename || ''}</div>
        <div class="item-meta">${durStr} · ${sizeStr}</div>
      </td>
      <td><span class="lang-pill">${it.lang || ''}</span></td>
      <td class="ready-cell">${renderReadinessText(it.readiness)}</td>
      <td>${renderStatusBadge(it.status)}</td>
      <td class="time">${(it.created_at || '').replace('T', ' ').slice(0, 16)}</td>
      ${window.PUSH_IS_ADMIN ? `<td>${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  async function load() {
    const tbody = document.getElementById('push-tbody');
    const colspan = window.PUSH_IS_ADMIN ? 8 : 7;
    tbody.innerHTML = `<tr><td colspan="${colspan}">加载中…</td></tr>`;
    try {
      const data = await fetchJSON('/pushes/api/items?' + buildQuery());
      state.total = data.total;
      if (!data.items.length) {
        tbody.innerHTML = `<tr><td colspan="${colspan}">无数据</td></tr>`;
      } else {
        tbody.innerHTML = data.items.map(renderRow).join('');
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

  async function doPush(itemId, btn) {
    btn.disabled = true;
    btn.textContent = '推送中…';
    let payloadData;
    try {
      const data = await fetchJSON(`/pushes/api/items/${itemId}/payload`);
      payloadData = data.payload;
      const pushUrl = data.push_url;
      if (!pushUrl) throw new Error('推送目标未配置');

      let resp;
      try {
        resp = await fetch(pushUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payloadData),
        });
      } catch (e) {
        await fetchJSON(`/pushes/api/items/${itemId}/mark-failed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_payload: payloadData,
            error_message: `网络或 CORS 失败: ${e.message}`,
          }),
        });
        alert(`推送失败（网络/CORS）：${e.message}`);
        return load();
      }
      const body = await resp.text();
      if (resp.ok) {
        await fetchJSON(`/pushes/api/items/${itemId}/mark-pushed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_payload: payloadData, response_body: body }),
        });
        alert('推送成功');
      } else {
        await fetchJSON(`/pushes/api/items/${itemId}/mark-failed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_payload: payloadData,
            response_body: body,
            error_message: `HTTP ${resp.status}`,
          }),
        });
        alert(`推送失败：HTTP ${resp.status}\n${body.slice(0, 200)}`);
      }
    } catch (e) {
      if (e.status === 400 || e.status === 409) {
        let info = '';
        try { info = JSON.parse(e.body).error || ''; } catch (_) {}
        alert(`无法推送：${info || e.message}`);
      } else {
        alert(`推送失败：${e.message}`);
      }
    } finally {
      await load();
    }
  }

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

  document.getElementById('push-tbody').addEventListener('click', ev => {
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = Number(btn.getAttribute('data-id'));
    if (action === 'push') doPush(id, btn);
    else if (action === 'reset') resetPush(id);
    else if (action === 'view-logs') viewLogs(id);
  });

  document.getElementById('drawer-close').addEventListener('click', () => {
    document.getElementById('push-log-drawer').hidden = true;
  });

  window._pushesLoad = load;
  loadLanguages().then(() => { bindFilters(); load(); });
})();
