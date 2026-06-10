(function () {
  'use strict';

  const state = {
    projects: [],
    activeProjectId: window.AIMS_INITIAL_PROJECT_ID || null,
    activeProject: null,
    pollTimer: null,
    publicMode: Boolean(window.AIMS_PUBLIC_MODE),
    shareToken: window.AIMS_SHARE_TOKEN || '',
  };

  const els = {
    list: document.getElementById('aimsProjectList'),
    detail: document.getElementById('aimsDetail'),
    create: document.getElementById('aimsCreateBtn'),
    refresh: document.getElementById('aimsRefreshBtn'),
    share: document.getElementById('aimsShareBtn'),
    note: document.getElementById('aimsPageNote'),
    count: document.getElementById('aimsProjectCount'),
    windowText: document.getElementById('aimsWindowText'),
    qualityText: document.getElementById('aimsQualityText'),
    toast: document.getElementById('aimsToast'),
  };

  function fmtNumber(value, digits) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return '0';
    return n.toLocaleString('zh-CN', {
      maximumFractionDigits: digits == null ? 0 : digits,
      minimumFractionDigits: 0,
    });
  }

  function fmtUsd(value, digits) {
    return '$' + fmtNumber(value, digits == null ? 0 : digits);
  }

  function fmtRoas(value) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value || 0);
    if (!Number.isFinite(n) || n <= 0) return '—';
    return n.toFixed(2);
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function statusLabel(status) {
    if (status === 'success') return '完成';
    if (status === 'failed') return '失败';
    return '运行中';
  }

  function progressStepLabel(status) {
    const map = {
      pending: '等待中',
      running: '运行中',
      done: '已完成',
      failed: '失败',
      skipped: '已跳过',
    };
    return map[status] || status || '等待中';
  }

  function runningProject() {
    return (state.projects || []).find((project) => project.status === 'running') || null;
  }

  function actionLabel(action) {
    const map = {
      expand_country: '扩国家',
      same_country_new_material: '同国补新素材',
      weak_country_retest: '弱国复测',
      hold: '暂缓',
      investigate: '排查',
    };
    return map[action] || action || '—';
  }

  function taskStatusLabel(task) {
    if (!task) return '';
    if (task.status_label) return task.status_label;
    const group = task.status_group || task.display_high_level || task.status;
    const map = {
      in_progress: '进行中',
      pending: '待处理',
      completed: '已完成',
      cancelled: '已取消',
      done: '已完成',
      all_done: '已完成',
      blocked: '待处理',
      assigned: '进行中',
      review: '进行中',
    };
    return map[group] || group || '';
  }

  function taskRank(task) {
    const map = { in_progress: 0, pending: 1, completed: 2, cancelled: 3 };
    return map[task && task.status_group] == null ? 9 : map[task.status_group];
  }

  function collectProductTasks(item) {
    const seen = new Set();
    const tasks = [];
    function add(task) {
      const id = Number(task && (task.task_id || task.id) || 0);
      if (!id || seen.has(id)) return;
      seen.add(id);
      tasks.push(task);
    }
    (item.country_summary || []).forEach((country) => {
      (country.tasks || []).forEach(add);
      add(country.blocking_task);
      add(country.cancelled_task);
    });
    (item.action_items || []).forEach((action) => {
      if (action.type === 'view_task') add(action.task || action);
    });
    return tasks.sort((a, b) => {
      const rank = taskRank(a) - taskRank(b);
      if (rank !== 0) return rank;
      return Number(b.task_id || b.id || 0) - Number(a.task_id || a.id || 0);
    });
  }

  function renderDeliveryStatusBadge(status) {
    const s = String(status || 'never').toLowerCase().trim();
    if (s === 'active') {
      return `<span class="aims-status-badge active">active</span>`;
    } else if (s === 'stopped') {
      return `<span class="aims-status-badge stopped">stopped</span>`;
    } else {
      return `<span class="aims-status-badge never">未做的</span>`;
    }
  }

  function getRoasColorClass(roasVal, breakevenRoas) {
    if (roasVal === null || roasVal === undefined || roasVal === '' || roasVal === 0) return '';
    const r = Number(roasVal);
    if (!Number.isFinite(r) || r <= 0) return '';
    const be = Number(breakevenRoas || 0);
    if (be > 0) {
      if (r >= be) {
        return 'aims-roas-green';
      } else if (r >= 1.2) {
        return 'aims-roas-orange';
      } else {
        return 'aims-roas-red';
      }
    } else {
      if (r < 1.2) {
        return 'aims-roas-red';
      }
      return '';
    }
  }

  function renderTaskLink(task, compact) {
    const id = Number(task && (task.task_id || task.id) || 0);
    if (!id) return '';
    const url = task.task_url || task.url || ('/tasks/detail/' + id);
    const label = taskStatusLabel(task);
    const text = compact ? ('#' + id) : ('任务 #' + id);
    if (state.publicMode) {
      return `<span class="aims-task-link ${esc(task.status_group || '')}">${esc(text)}${label ? ` · ${esc(label)}` : ''}</span>`;
    }
    return `<a class="aims-task-link ${esc(task.status_group || '')}" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}${label ? ` · ${esc(label)}` : ''}</a>`;
  }

  function renderTaskBadges(item, limit) {
    const tasks = collectProductTasks(item);
    if (!tasks.length) return '—';
    const shown = tasks.slice(0, limit || 3).map((task) => renderTaskLink(task, false)).join('');
    const extra = tasks.length > (limit || 3) ? `<span class="aims-chip">+${esc(tasks.length - (limit || 3))}</span>` : '';
    return `<div class="aims-task-list">${shown}${extra}</div>`;
  }

  function showToast(message) {
    if (!els.toast) return;
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
      els.toast.hidden = true;
    }, 2800);
  }

  function csrfHeaders(extra) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
    return token ? { ...(extra || {}), 'X-CSRFToken': token } : (extra || {});
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, options || {});
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
      const err = new Error(data.message || data.error_message || data.error || ('HTTP ' + res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function setBusy(isBusy) {
    const activeRunning = runningProject();
    if (els.create) {
      els.create.disabled = state.publicMode || isBusy || Boolean(activeRunning);
      els.create.title = activeRunning ? '已有 AI素材军师项目正在运行' : '';
    }
    if (els.refresh) els.refresh.disabled = isBusy;
    if (els.share) els.share.disabled = state.publicMode || isBusy || !state.activeProjectId;
  }

  async function loadProjects() {
    if (state.publicMode) {
      await loadSharedProject();
      return;
    }
    const data = await fetchJson('/medias/api/ai-material-strategist/projects');
    state.projects = data.projects || [];
    if (els.count) els.count.textContent = String(state.projects.length);
    if (!state.activeProjectId && state.projects.length) {
      state.activeProjectId = state.projects[0].id;
    }
    setBusy(false);
    renderProjects();
    if (state.activeProjectId) {
      await loadProject(state.activeProjectId);
    } else {
      renderEmpty();
    }
  }

  async function loadSharedProject() {
    if (!state.shareToken) {
      renderEmpty();
      return;
    }
    const data = await fetchJson('/medias/api/ai-material-strategist/share/' + encodeURIComponent(state.shareToken));
    state.activeProject = data.project;
    state.activeProjectId = data.project && data.project.id;
    state.projects = data.project ? [data.project] : [];
    setBusy(false);
    renderProject(data.project);
    syncPolling(data.project);
  }

  async function loadProject(projectId) {
    if (state.publicMode) {
      await loadSharedProject();
      return;
    }
    state.activeProjectId = Number(projectId);
    const data = await fetchJson('/medias/api/ai-material-strategist/projects/' + encodeURIComponent(projectId));
    state.activeProject = data.project;
    renderProjects();
    renderProject(data.project);
    syncPolling(data.project);
    const targetPath = '/medias/ai-material-strategist/projects/' + data.project.id;
    if (window.location.pathname !== targetPath) {
      history.replaceState(null, '', targetPath);
    }
  }

  function syncPolling(project) {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (project && project.status === 'running') {
      const loader = state.publicMode ? loadSharedProject : () => loadProject(project.id);
      state.pollTimer = setTimeout(() => loader().catch(console.error), 2000);
    }
  }

  async function createProject() {
    if (state.publicMode) return;
    const activeRunning = runningProject();
    if (activeRunning) {
      showToast('已有项目正在运行，已切换到运行页');
      await loadProject(activeRunning.id);
      return;
    }
    setBusy(true);
    try {
      const name = 'AI素材军师 ' + new Date().toLocaleString('zh-CN', { hour12: false });
      const data = await fetchJson('/medias/api/ai-material-strategist/projects', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ project_name: name, run_ai: true }),
      });
      state.activeProjectId = data.project.id;
      showToast('项目已开始运行');
      await loadProjects();
    } catch (err) {
      const running = err && err.status === 409 && err.data && (err.data.running_project || err.data.project);
      if (running && running.id) {
        showToast(err.message || '已有项目正在运行');
        await loadProject(running.id);
        return;
      }
      showToast(err.message || '创建失败');
    } finally {
      setBusy(false);
    }
  }

  async function shareProject() {
    if (state.publicMode || !state.activeProjectId) return;
    setBusy(true);
    try {
      const data = await fetchJson('/medias/api/ai-material-strategist/projects/' + encodeURIComponent(state.activeProjectId) + '/share', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({}),
      });
      const shareUrl = data.share && data.share.share_url;
      if (!shareUrl) throw new Error('分享链接生成失败');
      await copyText(shareUrl);
      showToast('分享链接已复制');
      if (state.activeProject) {
        state.activeProject.has_share = true;
        state.activeProject.share_enabled_at = data.share.share_enabled_at || state.activeProject.share_enabled_at;
      }
    } catch (err) {
      showToast(err.message || '分享失败');
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject(projectId) {
    const project = (state.projects || []).find((item) => Number(item.id) === Number(projectId));
    if (!project) return;
    if (project.status === 'running') {
      showToast('运行中的项目不能删除');
      return;
    }
    const name = project.project_name || ('项目 #' + project.id);
    if (!window.confirm(`确定删除「${name}」吗？删除后不可恢复。`)) {
      return;
    }
    setBusy(true);
    try {
      await fetchJson('/medias/api/ai-material-strategist/projects/' + encodeURIComponent(projectId), {
        method: 'DELETE',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      });
      if (Number(state.activeProjectId) === Number(projectId)) {
        state.activeProjectId = null;
        state.activeProject = null;
      }
      showToast('项目已删除');
      await loadProjects();
    } catch (err) {
      showToast(err.message || '删除失败');
    } finally {
      setBusy(false);
    }
  }

  function renderProjects() {
    if (!els.list) return;
    if (!state.projects.length) {
      els.list.innerHTML = '<div class="aims-empty" style="min-height:180px;">暂无项目</div>';
      return;
    }
    els.list.innerHTML = state.projects.map((project) => {
      const active = Number(project.id) === Number(state.activeProjectId) ? ' active' : '';
      const summary = project.summary || {};
      const topCount = summary.top_product_count || 0;
      const progress = project.progress || {};
      const pct = Number(progress.percent || 0);
      const canDelete = project.status !== 'running';
      return `
        <div class="aims-project-row${active}">
          <button type="button" class="aims-project-item" data-project-id="${esc(project.id)}">
            <span class="aims-project-name">${esc(project.project_name || ('项目 #' + project.id))}</span>
            <span class="aims-project-meta">
              <span class="aims-status ${esc(project.status)}">${statusLabel(project.status)}</span>
              <span>Top ${esc(topCount)}</span>
              ${project.status === 'running' ? `<span>${esc(pct)}%</span>` : ''}
              <span>${esc((project.created_at || '').slice(0, 16))}</span>
            </span>
            ${project.status === 'running' ? `<span class="aims-mini-progress"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></span>` : ''}
          </button>
          <button
            type="button"
            class="aims-project-delete"
            data-delete-project-id="${esc(project.id)}"
            ${canDelete ? '' : 'disabled'}
            title="${canDelete ? '删除项目' : '运行中的项目不能删除'}"
            aria-label="删除项目"
          >×</button>
        </div>
      `;
    }).join('');
  }

  function renderEmpty() {
    if (!els.detail) return;
    els.detail.innerHTML = '<div class="aims-empty">暂无项目</div>';
    if (els.windowText) els.windowText.textContent = '数据窗口：—';
    if (els.qualityText) els.qualityText.textContent = '数据新鲜度：—';
  }

  function renderProject(project) {
    if (!els.detail) return;
    const products = project.products || [];
    const summary = project.summary || {};
    const windowInfo = project.data_window || summary.data_window || {};
    const quality = summary.data_quality || (project.data_snapshot || {}).data_quality || {};
    if (els.note) els.note.textContent = `${statusLabel(project.status)} · Top ${products.length}`;
    if (els.windowText) {
      els.windowText.textContent = `数据窗口：${windowInfo.last_30d_from || '—'} 至 ${windowInfo.current_meta_business_date || '—'}`;
    }
    if (els.qualityText) {
      els.qualityText.textContent = `数据新鲜度：广告 ${quality.meta_realtime_max_snapshot_at || quality.meta_daily_max_business_date || '—'}，明空 ${quality.mingkong_max_snapshot_at || '—'}`;
    }
    if (project.status === 'running') {
      els.detail.innerHTML = `
        ${renderHeader(project, products)}
        ${renderRunProgress(project)}
      `;
      return;
    }
    const failedNotice = project.status === 'failed'
      ? `<div class="aims-error-box">项目失败：${esc(project.error_message || '')}</div>`
      : '';

    els.detail.innerHTML = `
      ${renderHeader(project, products)}
      ${renderRunProgress(project)}
      ${failedNotice}
      ${renderMetrics(project, products)}
      ${renderVisuals(products)}
      ${renderCountryMatrix(products)}
      ${renderProductsTable(products)}
      ${renderProductSections(products)}
    `;
  }

  function renderRunProgress(project) {
    const progress = project.progress || {};
    const steps = progress.steps || [];
    if (!Object.keys(progress).length && project.status === 'success') return '';
    const pct = Math.max(0, Math.min(100, Number(progress.percent || (project.status === 'success' ? 100 : 0))));
    const pp = progress.product_progress || {};
    const currentProduct = pp.current_product_code || pp.current_product_name || '';
    const productLine = Number(pp.total || 0) > 0
      ? `产品 ${esc(pp.current_index || 0)} / ${esc(pp.total)}${currentProduct ? ` · ${esc(currentProduct)}` : ''}`
      : '产品分析待开始';
    return `
      <section class="aims-run-card ${esc(project.status)}">
        <div class="aims-run-head">
          <div>
            <div class="aims-run-title">${esc(progress.current_step_label || statusLabel(project.status))}</div>
            <div class="aims-run-message">${esc(progress.message || '')}</div>
          </div>
          <span class="aims-status ${esc(project.status)}">${statusLabel(project.status)}</span>
        </div>
        <div class="aims-run-progress">
          <span class="aims-run-track"><span class="aims-run-fill" style="width:${pct}%"></span></span>
          <strong>${esc(pct)}%</strong>
        </div>
        <div class="aims-run-meta">
          <span>${productLine}</span>
          <span>更新 ${esc((progress.updated_at || project.updated_at || '').slice(0, 19))}</span>
        </div>
        ${steps.length ? renderProgressSteps(steps) : ''}
        ${renderProgressLogs(progress.logs || [])}
      </section>
    `;
  }

  function renderProgressSteps(steps) {
    return `
      <div class="aims-step-grid">
        ${steps.map((step) => `
          <article class="aims-step-card ${esc(step.status || 'pending')}">
            <div class="aims-step-top">
              <strong>${esc(step.label || step.key)}</strong>
              <span>${esc(progressStepLabel(step.status))}</span>
            </div>
            <p>${esc(step.message || step.description || '')}</p>
          </article>
        `).join('')}
      </div>
    `;
  }

  function renderProgressLogs(logs) {
    if (!logs.length) return '';
    return `
      <div class="aims-progress-logs">
        ${logs.slice(-6).map((log) => `
          <div class="aims-progress-log ${esc(log.level || 'info')}">
            <span>${esc((log.time || '').slice(11, 19) || '')}</span>
            <strong>${esc(log.message || '')}</strong>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderHeader(project, products) {
    const summary = project.summary || {};
    const p0 = (summary.priority_counts || {}).P0 || 0;
    const p1 = (summary.priority_counts || {}).P1 || 0;
    return `
      <div class="aims-title-row">
        <div>
          <h2>${esc(project.project_name || ('项目 #' + project.id))}</h2>
          <div class="aims-subline">
            <span class="aims-status ${esc(project.status)}">${statusLabel(project.status)}</span>
            <span>Provider ${esc(project.provider_code || 'openrouter')}</span>
            <span>Model ${esc(project.model_id || 'google/gemini-3.5-flash')}</span>
            <span>开始 ${esc((project.started_at || '').slice(0, 16))}</span>
            ${project.finished_at ? `<span>完成 ${esc(project.finished_at.slice(0, 16))}</span>` : ''}
          </div>
        </div>
        <div class="aims-actions">
          <span class="aims-chip p0">P0 ${esc(p0)}</span>
          <span class="aims-chip p1">P1 ${esc(p1)}</span>
          <span class="aims-chip">Top ${esc(products.length)}</span>
        </div>
      </div>
    `;
  }

  function renderMetrics(project, products) {
    const totals = products.reduce((acc, item) => {
      const m = item.metrics || {};
      acc.spend += Number(m.spend_30d || 0);
      acc.orders += Number(m.orders_30d || 0);
      acc.revenue += Number(m.revenue_30d || 0);
      acc.profit += Number(m.profit_30d || 0);
      acc.mk += (item.mingkong_materials || []).length;
      return acc;
    }, { spend: 0, orders: 0, revenue: 0, profit: 0, mk: 0 });
    const roas = totals.spend > 0 ? totals.revenue / totals.spend : 0;
    return `
      <div class="aims-metrics">
        <div class="aims-metric"><span>Top产品</span><strong>${esc(products.length)}</strong></div>
        <div class="aims-metric"><span>30天消耗</span><strong>${fmtUsd(totals.spend)}</strong></div>
        <div class="aims-metric"><span>30天订单</span><strong>${fmtNumber(totals.orders)}</strong></div>
        <div class="aims-metric"><span>真实ROAS</span><strong>${fmtRoas(roas)}</strong></div>
        <div class="aims-metric"><span>明空候选</span><strong>${fmtNumber(totals.mk)}</strong></div>
      </div>
    `;
  }

  function renderVisuals(products) {
    const top = products.slice(0, 10);
    const maxSpend = Math.max(1, ...top.map((item) => Number((item.metrics || {}).spend_30d || 0)));
    const bars = top.map((item) => {
      const m = item.metrics || {};
      const width = Math.max(4, Math.round((Number(m.spend_30d || 0) / maxSpend) * 100));
      return `
        <div class="aims-bar-row">
          <span>#${esc(item.rank_no)}</span>
          <span title="${esc(item.product_name)}">${esc(item.product_code || item.product_name)}</span>
          <span class="aims-bar-track"><span class="aims-bar-fill" style="width:${width}%"></span></span>
          <span>${fmtUsd(m.spend_30d)}</span>
        </div>
      `;
    }).join('');
    const maxLogSpend = Math.max(1, ...products.map((item) => Math.log1p(Number((item.metrics || {}).spend_30d || 0))));
    const dots = products.map((item) => {
      const m = item.metrics || {};
      const x = Math.max(6, Math.min(94, (Math.log1p(Number(m.spend_30d || 0)) / maxLogSpend) * 90 + 5));
      const roas = Math.max(0, Math.min(4, Number(m.true_roas_30d || 0)));
      const y = Math.max(8, Math.min(92, 92 - (roas / 4) * 84));
      return `<span class="aims-dot" style="left:${x}%; top:${y}%;" title="#${esc(item.rank_no)} ${esc(item.product_name)} ROAS ${fmtRoas(m.true_roas_30d)}">${esc(item.rank_no)}</span>`;
    }).join('');
    return `
      <div class="aims-visuals">
        <section class="aims-band">
          <div class="aims-band-title">Top10 30天消耗</div>
          <div class="aims-bars">${bars || '<div class="aims-empty" style="min-height:120px;">—</div>'}</div>
        </section>
        <section class="aims-band">
          <div class="aims-band-title">消耗 × 真实ROAS</div>
          <div class="aims-scatter">${dots}</div>
        </section>
      </div>
    `;
  }

  function renderCountryMatrix(products) {
    const countries = ['DE', 'FR', 'IT', 'ES', 'JP', 'SE', 'NL', 'PT'];
    const head = ['产品'].concat(countries).map((label) => `<div class="aims-country-cell head">${esc(label)}</div>`).join('');
    const rows = products.slice(0, 20).map((item) => {
      const byCode = {};
      (item.country_summary || []).forEach((country) => { byCode[country.country_code] = country; });
      const cells = countries.map((code) => {
        const country = byCode[code] || {};
        const cls = country.delivery_status || 'never';
        const task = country.blocking_task || country.cancelled_task;
        const taskTitle = task ? ` · 任务 #${task.task_id} ${taskStatusLabel(task)}` : '';
        return `
          <div class="aims-country-cell ${esc(cls)}" title="${esc(code)} ${fmtUsd(country.ad_spend_usd)} ROAS ${fmtRoas(country.ad_roas)}${esc(taskTitle)}">
            <strong>${fmtUsd(country.ad_spend_usd)}</strong><br>
            <span class="${esc(getRoasColorClass(country.ad_roas, item.effective_breakeven_roas))}">R ${fmtRoas(country.ad_roas)}</span>
            ${task ? `<br>${renderTaskLink(task, true)}` : ''}
          </div>
        `;
      }).join('');
      return `<div class="aims-country-cell head">#${esc(item.rank_no)} ${esc(item.product_code)}</div>${cells}`;
    }).join('');
    return `<div class="aims-country-grid">${head}${rows}</div>`;
  }

  function renderProductsTable(products) {
    const rows = products.map((item) => {
      const m = item.metrics || {};
      const ai = item.ai_result || {};
      const mk = (item.mingkong_materials || [])[0] || {};
      const title = item.product_name || item.product_code;
      const productNode = state.publicMode
        ? `<span class="aims-product-link plain">${esc(title)}</span>`
        : `<a class="aims-product-link" href="/medias/${encodeURIComponent(item.product_code || '')}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`;
      return `
        <tr>
          <td>#${esc(item.rank_no)}</td>
          <td>
            ${productNode}
            <div>${esc(item.product_code)}</div>
          </td>
          <td>${fmtUsd(m.spend_30d)}</td>
          <td>${fmtNumber(m.orders_30d)}</td>
          <td class="${esc(getRoasColorClass(m.true_roas_30d, item.effective_breakeven_roas))}">${fmtRoas(m.true_roas_30d)}</td>
          <td>${fmtUsd(m.spend_yesterday)}</td>
          <td><span class="aims-chip ${String(ai.priority || '').toLowerCase()}">${esc(ai.priority || 'P3')}</span></td>
          <td>${esc(actionLabel(ai.primary_action))}</td>
          <td>${mk.video_name ? `${esc(mk.video_name)}<br><span>${fmtUsd(mk.cumulative_90_spend)} · 广告 ${fmtNumber(mk.video_ads_count)}</span>` : '—'}</td>
          <td>${renderTaskBadges(item, 3)}</td>
          ${state.publicMode ? '' : `<td><div class="aims-actions">${renderInlineActions(item)}</div></td>`}
        </tr>
      `;
    }).join('');
    const actionHeader = state.publicMode ? '' : '<th>入口</th>';
    return `
      <div class="aims-table-wrap">
        <table class="aims-table">
          <thead><tr><th>排名</th><th>产品</th><th>30天消耗</th><th>30天订单</th><th>真实ROAS</th><th>昨日消耗</th><th>优先级</th><th>动作</th><th>明空候选</th><th>任务</th>${actionHeader}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderInlineActions(item) {
    if (state.publicMode) return '';
    const actions = (item.action_items || []).filter((action) => {
      return ['supplement_workbench', 'translation_tasks', 'product_materials'].includes(action.type);
    });
    return actions.map((action) => {
      let url = action.url;
      let label = action.label;
      if (action.type === 'supplement_workbench') {
        if (label === '补素材工作台') {
          label = '素材工作台';
        }
        if (url && url.includes('/medias/product/addvideo/')) {
          url = url.replace('/medias/product/addvideo/', '/medias/product/video_workbench/');
        }
      }
      return `<a class="aims-btn primary" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
    }).join('');
  }

  function renderProductSections(products) {
    return `
      <div class="aims-product-sections">
        ${products.map((item, productIndex) => renderProductSection(item, productIndex)).join('')}
      </div>
    `;
  }

  function renderProductSection(item, productIndex) {
    const ai = item.ai_result || {};
    const m = item.metrics || {};
    const materials = (item.mingkong_materials || []).slice(0, 3);
    return `
      <section class="aims-product-section">
        <div class="aims-product-head">
          <div>
            <h3>#${esc(item.rank_no)} ${esc(item.product_name || item.product_code)}</h3>
            <div class="aims-subline">
              <span>${esc(item.product_code)}</span>
              <span>30天消耗 ${fmtUsd(m.spend_30d)}</span>
              <span>订单 ${fmtNumber(m.orders_30d)}</span>
              <span class="${esc(getRoasColorClass(m.true_roas_30d, item.effective_breakeven_roas))}">ROAS ${fmtRoas(m.true_roas_30d)}</span>
            </div>
          </div>
          ${state.publicMode ? '' : `<div class="aims-actions">${renderInlineActions(item)}</div>`}
        </div>
        <div class="aims-product-body">
          <div>
            <p class="aims-rec">${esc(ai.overall_judgement || '')}</p>
            ${renderCountryActions(ai)}
            <div class="aims-task-list" style="margin:0 0 12px;">${renderTaskBadges(item, 5)}</div>
            <div class="aims-material-grid">${materials.map((material, materialIndex) => renderMaterial(item, material, productIndex, materialIndex)).join('') || '<div class="aims-empty" style="min-height:48px;">暂无明空候选</div>'}</div>
          </div>
          <div class="aims-band">
            <div class="aims-band-title">国家反馈</div>
            <div class="aims-bars">${renderCountryBars(item.country_summary || [], item.effective_breakeven_roas)}</div>
          </div>
        </div>
      </section>
    `;
  }

  function renderCountryActions(ai) {
    const actions = ai.country_actions || [];
    if (!actions.length) return '';
    return `
      <div class="aims-actions" style="margin:8px 0 12px;">
        ${actions.map((action) => {
          const task = action.existing_task || action.cancelled_task;
          const label = action.duplicate_suppressed ? '已有任务' : actionLabel(action.action);
          return `<span class="aims-chip">${esc(action.country_code || action.lang)} · ${esc(label)}</span>${task ? renderTaskLink(task, false) : ''}`;
        }).join('')}
      </div>
    `;
  }

  function renderMaterial(item, material, productIndex, materialIndex) {
    const actionIndex = (item.action_items || []).findIndex((action) => {
      return action.type === 'import_mk_video' && action.material_key === material.material_key;
    });
    const importButton = !state.publicMode && actionIndex >= 0
      ? `<button type="button" class="aims-btn teal" data-import-action data-product-index="${productIndex}" data-action-index="${actionIndex}">加入素材库</button>`
      : '';
    const videoNode = material.video_url
      ? `<video controls preload="metadata" src="${esc(material.video_url)}"></video>`
      : '';
    const videoLink = material.video_url
      ? `<a class="aims-btn" href="${esc(material.video_url)}" target="_blank" rel="noopener noreferrer">看视频</a>`
      : '';
    return `
      <article class="aims-material">
        ${videoNode}
        <div class="aims-material-title" title="${esc(material.video_name || material.video_path)}">${esc(material.video_name || material.video_path || '明空素材')}</div>
        <div class="aims-material-meta">
          <span>90天 ${fmtUsd(material.cumulative_90_spend)}</span>
          <span>广告 ${fmtNumber(material.video_ads_count)}</span>
          <span>昨日 ${fmtUsd(material.yesterday_spend_delta)}</span>
        </div>
        <div class="aims-actions">
          ${videoLink}
          ${importButton}
        </div>
      </article>
    `;
  }

  function renderCountryBars(countries, breakevenRoas) {
    const maxSpend = Math.max(1, ...countries.map((country) => Number(country.ad_spend_usd || 0)));
    return countries.map((country) => {
      const width = Math.max(3, Math.round((Number(country.ad_spend_usd || 0) / maxSpend) * 100));
      return `
        <div class="aims-bar-row" style="grid-template-columns:32px minmax(64px, auto) minmax(60px, 1fr) minmax(50px, auto) 44px minmax(80px, auto); align-items: center; gap: 8px;">
          <span>${esc(country.country_code || country.lang)}</span>
          <span>${renderDeliveryStatusBadge(country.delivery_status)}</span>
          <span class="aims-bar-track"><span class="aims-bar-fill" style="width:${width}%"></span></span>
          <strong style="color: var(--aims-muted); font-size: 11px;">${fmtUsd(country.ad_spend_usd)}</strong>
          <span class="${esc(getRoasColorClass(country.ad_roas, breakevenRoas))}">${fmtRoas(country.ad_roas)}</span>
          <span>${country.blocking_task ? renderTaskLink(country.blocking_task, true) : (country.cancelled_task ? renderTaskLink(country.cancelled_task, true) : '')}</span>
        </div>
      `;
    }).join('');
  }

  async function importMaterial(button) {
    if (state.publicMode) return;
    const productIndex = Number(button.getAttribute('data-product-index'));
    const actionIndex = Number(button.getAttribute('data-action-index'));
    const product = (state.activeProject && state.activeProject.products || [])[productIndex];
    const action = product && (product.action_items || [])[actionIndex];
    if (!action || !action.url || !action.payload) return;
    button.disabled = true;
    try {
      await fetchJson(action.url, {
        method: action.method || 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(action.payload),
      });
      showToast('已提交加入素材库');
    } catch (err) {
      showToast(err.message || '加入素材库失败');
    } finally {
      button.disabled = false;
    }
  }

  if (els.list) {
    els.list.addEventListener('click', (event) => {
      const deleteButton = event.target.closest('[data-delete-project-id]');
      if (deleteButton) {
        event.preventDefault();
        event.stopPropagation();
        if (!deleteButton.disabled) {
          deleteProject(deleteButton.getAttribute('data-delete-project-id'));
        }
        return;
      }
      const button = event.target.closest('[data-project-id]');
      if (!button) return;
      loadProject(button.getAttribute('data-project-id')).catch((err) => showToast(err.message));
    });
  }
  if (els.detail) {
    els.detail.addEventListener('click', (event) => {
      const button = event.target.closest('[data-import-action]');
      if (!button) return;
      importMaterial(button);
    });
  }
  if (els.create) els.create.addEventListener('click', createProject);
  if (els.refresh) els.refresh.addEventListener('click', () => loadProjects().catch((err) => showToast(err.message)));
  if (els.share) els.share.addEventListener('click', shareProject);

  loadProjects().catch((err) => {
    renderEmpty();
    showToast(err.message || '加载失败');
  });
})();
