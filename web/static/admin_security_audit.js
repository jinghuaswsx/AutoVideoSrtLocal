(function () {
  const root = document.querySelector('[data-security-audit]');
  if (!root) return;

  const form = root.querySelector('[data-audit-filters]');
  const state = root.querySelector('[data-audit-state]');
  const table = root.querySelector('[data-audit-table]');
  const tabs = Array.from(root.querySelectorAll('[data-tab]'));
  let activeTab = 'logs';

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function paramsForRequest() {
    const data = new FormData(form);
    const params = new URLSearchParams();
    for (const [key, value] of data.entries()) {
      const text = String(value || '').trim();
      if (text) params.set(key, text);
    }
    params.set('page_size', '100');
    return params.toString();
  }

  function endpoint() {
    const path = activeTab === 'downloads'
      ? '/admin/security-audit/api/media-downloads'
      : '/admin/security-audit/api/logs';
    const query = paramsForRequest();
    return query ? `${path}?${query}` : path;
  }

  function detailText(row) {
    const detail = row.detail_json;
    if (!detail) return '-';
    if (typeof detail === 'string') return detail;
    try {
      return JSON.stringify(detail);
    } catch (_err) {
      return String(detail);
    }
  }

  function renderLogs(items) {
    table.innerHTML = `
      <thead>
        <tr>
          <th>时间</th><th>账号</th><th>模块</th><th>动作</th>
          <th>对象</th><th>状态</th><th>IP</th><th>路径</th><th>详情</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((row) => `
          <tr>
            <td class="audit-code">${escapeHtml(row.created_at || '-')}</td>
            <td>${escapeHtml(row.actor_username || row.actor_user_id || '-')}</td>
            <td><span class="audit-badge">${escapeHtml(row.module || '-')}</span></td>
            <td class="audit-code">${escapeHtml(row.action || '-')}</td>
            <td>${escapeHtml(row.target_label || row.target_id || '-')}</td>
            <td>${escapeHtml(row.status || '-')}</td>
            <td class="audit-code">${escapeHtml(row.ip_address || '-')}</td>
            <td class="audit-code">${escapeHtml(row.request_path || '-')}</td>
            <td class="audit-code">${escapeHtml(detailText(row))}</td>
          </tr>
        `).join('')}
      </tbody>
    `;
  }

  function renderDownloads(items) {
    table.innerHTML = `
      <thead>
        <tr>
          <th>时间</th><th>账号</th><th>动作</th><th>素材</th>
          <th>对象 ID</th><th>IP</th><th>路径</th><th>详情</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((row) => `
          <tr>
            <td class="audit-code">${escapeHtml(row.created_at || '-')}</td>
            <td>${escapeHtml(row.actor_username || row.actor_user_id || '匿名')}</td>
            <td class="audit-code">${escapeHtml(row.action || '-')}</td>
            <td>${escapeHtml(row.target_label || '-')}</td>
            <td class="audit-code">${escapeHtml(row.target_id || '-')}</td>
            <td class="audit-code">${escapeHtml(row.ip_address || '-')}</td>
            <td class="audit-code">${escapeHtml(row.request_path || '-')}</td>
            <td class="audit-code">${escapeHtml(detailText(row))}</td>
          </tr>
        `).join('')}
      </tbody>
    `;
  }

  async function load() {
    state.classList.remove('error');
    state.textContent = '加载中...';
    table.innerHTML = '';
    try {
      const response = await fetch(endpoint(), { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const items = payload.items || [];
      state.textContent = items.length
        ? `共 ${payload.total || items.length} 条记录`
        : '当前筛选条件下暂无审计记录';
      if (activeTab === 'downloads') renderDownloads(items);
      else renderLogs(items);
    } catch (err) {
      state.classList.add('error');
      state.textContent = `加载失败：${err.message || err}`;
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      activeTab = tab.dataset.tab;
      tabs.forEach((item) => item.classList.toggle('active', item === tab));
      load();
    });
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    load();
  });

  load();
})();
