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
    taskModal: document.getElementById('aimsTaskModal'),
    taskModalTitle: document.getElementById('aimsTaskModalTitle'),
    taskModalBody: document.getElementById('aimsTaskModalBody'),
    taskModalClose: document.getElementById('aimsTaskModalClose'),
    taskModalBackdrop: document.getElementById('aimsTaskModalBackdrop'),
    llmModal: document.getElementById('aimsLlmModal'),
    llmModalTitle: document.getElementById('aimsLlmModalTitle'),
    llmModalBody: document.getElementById('aimsLlmModalBody'),
    llmModalClose: document.getElementById('aimsLlmModalClose'),
    llmModalBackdrop: document.getElementById('aimsLlmModalBackdrop'),
    llmTabVisual: document.getElementById('aimsLlmTabVisual'),
    llmTabPrompt: document.getElementById('aimsLlmTabPrompt'),
    llmTabPayload: document.getElementById('aimsLlmTabPayload'),
    llmTabButtons: document.querySelectorAll('[data-llm-tab]'),
  };
  const DEFAULT_COUNTRY_CODES = ['EN', 'DE', 'FR', 'IT', 'ES', 'JP', 'SE', 'NL', 'PT'];
  const PROJECT_TOP_N = 30;

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
    if (status === 'interrupted') return '中断';
    return '运行中';
  }

  function progressStepLabel(status) {
    const map = {
      pending: '等待中',
      running: '运行中',
      done: '已完成',
      failed: '失败',
      interrupted: '中断',
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

  function checkpointResumeReasonLabel(reason) {
    const map = {
      terminal_status: '保留已完成产品结果，继续未完成分析',
      stale_heartbeat: '运行心跳已超过 10 分钟未推进，可接管续跑',
      stale_scheduled: '恢复排队长时间未启动，可重新排队',
    };
    return map[reason] || '保留已有断点继续执行';
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

  function getRoasStatus(roasVal, breakevenRoas) {
    if (roasVal === null || roasVal === undefined || roasVal === '' || roasVal === 0) return 'zero';
    const r = Number(roasVal);
    if (!Number.isFinite(r) || r <= 0) return 'zero';
    const be = Number(breakevenRoas || 0);
    if (be > 0) {
      if (r >= be) return 'green';
      if (r >= be * 0.8 || r >= 1.2) return 'orange';
      return 'red';
    }
    if (r >= 1.5) return 'green';
    if (r >= 1.2) return 'orange';
    return 'red';
  }

  function getSpendGreenLevelClass(spendVal, roasStatus) {
    if (roasStatus !== 'green') return '';
    const val = Number(spendVal || 0);
    if (val < 100) return 'green-level-1';
    if (val < 500) return 'green-level-2';
    if (val < 1000) return 'green-level-3';
    if (val < 3000) return 'green-level-4';
    if (val < 10000) return 'green-level-5';
    return 'green-level-6';
  }

  function getSpendStyle(spendVal) {
    const val = Number(spendVal || 0);
    if (val > 1000) {
      return 'color: #15803d; font-size: 22px; font-weight: 800; line-height: 1.1;';
    } else if (val >= 300) {
      return 'color: #22c55e; font-size: 16px; font-weight: 700; line-height: 1.2;';
    }
    return 'color: #000000; font-size: 11px; font-weight: normal;';
  }

  function renderRecommendationBadge(act) {
    if (!act) return '';
    const label = act.duplicate_suppressed ? '已有任务' : actionLabel(act.action);
    const cls = esc(act.action || '');
    return `<span class="aims-rec-badge ${cls}">${esc(label)}</span>`;
  }

  function renderTaskCountLink(item, productIndex) {
    const tasks = collectProductTasks(item);
    if (!tasks.length) {
      return '<span class="aims-muted" style="font-size: 11px;">无任务</span>';
    }
    return `<button type="button" class="aims-task-count-btn" data-show-tasks="${productIndex}">${tasks.length} 个任务</button>`;
  }

  function collectCountryTasks(country, act) {
    const seen = new Set();
    const tasks = [];
    function add(task) {
      const id = Number(task && (task.task_id || task.id) || 0);
      if (!id || seen.has(id)) return;
      seen.add(id);
      tasks.push(task);
    }
    if (country) {
      (country.tasks || []).forEach(add);
      add(country.blocking_task);
      add(country.cancelled_task);
    }
    if (act) {
      add(act.existing_task);
      add(act.cancelled_task);
    }
    return tasks.sort((a, b) => {
      const rank = taskRank(a) - taskRank(b);
      if (rank !== 0) return rank;
      return Number(b.task_id || b.id || 0) - Number(a.task_id || a.id || 0);
    });
  }

  function renderCountryTaskCountLink(country, act, productIndex, countryCode) {
    const tasks = collectCountryTasks(country, act);
    if (!tasks.length) return '—';
    return `<button type="button" class="aims-task-count-btn" data-show-country-tasks="${productIndex}:${countryCode}">${tasks.length} 个任务</button>`;
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

  function renderModalTaskRows(tasks) {
    if (!tasks.length) return '<div class="aims-empty">暂无任务</div>';
    return tasks.map((task) => {
      const id = Number(task.task_id || task.id || 0);
      const url = task.task_url || task.url || ('/tasks/detail/' + id);
      const label = taskStatusLabel(task);
      const taskType = task.type_label || task.task_type || '翻译/处理任务';
      const statusGroup = task.status_group || '';
      const linkHtml = state.publicMode
        ? `<span class="aims-task-link ${esc(statusGroup)}">任务 #${id}</span>`
        : `<a class="aims-task-link ${esc(statusGroup)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" style="font-weight:800;font-size:13px;">任务 #${id}</a>`;
      return `
        <div class="aims-modal-task-item">
          <div class="aims-modal-task-info">
            <div class="aims-modal-task-title">${esc(taskType)}</div>
            <div class="aims-modal-task-meta">状态: <span class="aims-status-text ${esc(statusGroup)}">${esc(label)}</span></div>
          </div>
          <div>${linkHtml}</div>
        </div>
      `;
    }).join('');
  }

  function showTasksModal(productIndex) {
    const item = (state.activeProject?.products || [])[productIndex];
    if (!item) return;
    if (els.taskModalTitle) {
      els.taskModalTitle.textContent = `#${item.rank_no} ${item.product_code || item.product_name} 任务清单`;
    }
    if (els.taskModalBody) {
      els.taskModalBody.innerHTML = renderModalTaskRows(collectProductTasks(item));
    }
    if (els.taskModal) {
      els.taskModal.hidden = false;
    }
  }

  function showCountryTasksModal(productIndex, countryCode) {
    const item = (state.activeProject?.products || [])[productIndex];
    if (!item) return;
    const code = String(countryCode || '').toUpperCase();
    const country = (item.country_summary || []).find((row) => {
      return String(row.country_code || row.lang || '').toUpperCase().trim() === code;
    }) || null;
    const action = ((item.ai_result || {}).country_actions || []).find((row) => {
      return String(row.country_code || row.lang || '').toUpperCase().trim() === code;
    }) || null;
    if (els.taskModalTitle) {
      els.taskModalTitle.textContent = `#${item.rank_no} ${item.product_code || item.product_name} - ${code} 任务清单`;
    }
    if (els.taskModalBody) {
      els.taskModalBody.innerHTML = renderModalTaskRows(collectCountryTasks(country, action));
    }
    if (els.taskModal) {
      els.taskModal.hidden = false;
    }
  }

  function hideTasksModal() {
    if (els.taskModal) {
      els.taskModal.hidden = true;
    }
  }

  function hideLlmModal() {
    if (els.llmModal) {
      els.llmModal.hidden = true;
    }
  }

  function renderProjLlmVisual(rankingResult) {
    const ranking = rankingResult || {};
    const batches = ranking.batch_results || [];
    const batchCards = batches.map((batch, idx) => {
      const inputProducts = (batch.input && batch.input.products) || [];
      const outputProducts = (batch.output && batch.output.ranked_products) || [];
      const logBtn = batch.usage_log_id ? `<button type="button" class="aims-btn" data-llm-log-id="${batch.usage_log_id}" style="height:20px;line-height:1;padding:0 6px;font-size:10px;">查看该批次报文</button>` : '';
      return `
        <div class="aims-llm-visual-card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <h4>第 ${idx + 1} 批 AI 复评 (输入 ${inputProducts.length} 个，输出 ${outputProducts.length} 个)</h4>
            ${logBtn}
          </div>
          <div class="aims-llm-grid">
            <div class="aims-llm-param-section">
              <div class="aims-llm-param-title">输入候选产品</div>
              <div class="aims-llm-param-body">${inputProducts.map((p) => `<div>${esc(p.product_code)} · ${fmtUsd(p.spend_30d)} · ${fmtNumber(p.score, 1)}</div>`).join('') || '—'}</div>
            </div>
            <div class="aims-llm-param-section">
              <div class="aims-llm-param-title">AI 选择产品</div>
              <div class="aims-llm-param-body">${outputProducts.map((p) => `<div>#${esc(p.rank)} ${esc(p.product_code)} · ${esc(p.why_selected || '')}</div>`).join('') || '—'}</div>
            </div>
          </div>
        </div>
      `;
    }).join('');
    const finalLogBtn = ranking.final_usage_log_id ? `<button type="button" class="aims-btn" data-llm-log-id="${ranking.final_usage_log_id}" style="height:20px;line-height:1;padding:0 6px;font-size:10px;">查看决赛报文</button>` : '';
    const finalProducts = ((ranking.final_output || {}).ranked_products || []);
    const finalCard = ranking.final_input ? `
      <div class="aims-llm-visual-card" style="border-color:#bfdbfe;background:#eff6ff;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <h4 style="color:#1e3a8a;">最终决赛 Top ${PROJECT_TOP_N} AI 复评</h4>
          ${finalLogBtn}
        </div>
        <div class="aims-llm-param-body">${finalProducts.map((p) => `<div>#${esc(p.rank)} ${esc(p.product_code)} · ${esc(p.why_selected || '')}</div>`).join('') || '—'}</div>
      </div>
    ` : '';
    return `<div style="display:flex;flex-direction:column;gap:16px;">${batchCards}${finalCard || '<div class="aims-empty">暂无可视化内容</div>'}</div>`;
  }

  function renderProductLlmVisual(item) {
    const ai = item.ai_result || {};
    const countries = ai.country_actions || [];
    const materials = ai.material_actions || [];
    return `
      <div style="display:flex;flex-direction:column;gap:16px;">
        <div class="aims-llm-visual-card">
          <h4>分析研判总结</h4>
          <p style="font-size:13px;line-height:1.6;color:#1e293b;margin:6px 0 0;">${esc(ai.overall_judgement || '暂无研判结论')}</p>
          <div style="display:flex;gap:8px;margin-top:10px;">
            <span class="aims-chip ${String(ai.priority || '').toLowerCase()}">${esc(ai.priority || 'P3')}</span>
            <span class="aims-chip">${esc(actionLabel(ai.primary_action))}</span>
          </div>
        </div>
        <div class="aims-llm-grid">
          <div class="aims-llm-param-section">
            <div class="aims-llm-param-title">AI 国家建议</div>
            <div class="aims-llm-param-body">
              ${countries.map((country) => `<div style="margin-bottom:8px;"><strong>${esc(country.country_code || country.lang)}</strong> ${renderRecommendationBadge(country)}<br><span style="color:#64748b;">${esc(country.reason || '')}</span></div>`).join('') || '—'}
            </div>
          </div>
          <div class="aims-llm-param-section">
            <div class="aims-llm-param-title">AI 明空素材建议</div>
            <div class="aims-llm-param-body">
              ${materials.map((material) => `<div style="margin-bottom:8px;"><strong>${esc(material.material_key || material.video_name || '素材')}</strong><br><span style="color:#64748b;">${esc(material.reason || '')}</span></div>`).join('') || '—'}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  async function loadLlmPayload(logId) {
    if (!els.llmTabPayload) return;
    if (state.publicMode) {
      els.llmTabPayload.innerHTML = '<div class="aims-llm-badge">公开分享模式下无法查看原始报文</div>';
      return;
    }
    els.llmTabPayload.innerHTML = '<div class="aims-loading">正在从服务器读取原始 JSON 报文...</div>';
    try {
      const res = await fetchJson('/medias/api/ai-material-strategist/llm-payload/' + logId);
      const reqJson = JSON.stringify((res.payload || {}).request_data || {}, null, 2);
      const respJson = JSON.stringify((res.payload || {}).response_data || {}, null, 2);
      els.llmTabPayload.innerHTML = `
        <div class="aims-llm-badge">已拉取 usage log #${esc(logId)} 的原始报文</div>
        <div class="aims-llm-grid">
          <div><h4>Request</h4><pre class="aims-llm-code-block">${esc(reqJson)}</pre></div>
          <div><h4>Response</h4><pre class="aims-llm-code-block">${esc(respJson)}</pre></div>
        </div>
      `;
    } catch (err) {
      els.llmTabPayload.innerHTML = `<div class="aims-llm-badge" style="background:#fee2e2;color:#dc2626;border-color:#fecaca;">读取原始报文失败: ${esc(err.message)}</div>`;
    }
  }

  async function showLlmModal(title, provider, model, usageLogId, debugInfo) {
    if (els.llmModalTitle) els.llmModalTitle.textContent = title;
    if (els.llmTabVisual) {
      els.llmTabVisual.innerHTML = debugInfo && debugInfo.type === 'ranking'
        ? renderProjLlmVisual(debugInfo.rankingResult || {})
        : renderProductLlmVisual((debugInfo && debugInfo.productItem) || {});
    }
    if (els.llmTabPrompt) {
      els.llmTabPrompt.innerHTML = `
        <div class="aims-llm-badge">供应商: ${esc(provider)} | 模型: ${esc(model)}</div>
        <pre class="aims-llm-code-block">${esc((debugInfo && debugInfo.prompt) || '暂无本地保存的完整提示词')}</pre>
      `;
    }
    if (els.llmTabPayload) {
      els.llmTabPayload.innerHTML = `
        <div class="aims-llm-badge">本地保存的响应摘要</div>
        <pre class="aims-llm-code-block">${esc((debugInfo && debugInfo.response_text) || '暂无本地保存的响应文本')}</pre>
      `;
    }
    switchLlmTab('visual');
    if (els.llmModal) els.llmModal.hidden = false;
    if (usageLogId) await loadLlmPayload(usageLogId);
  }

  function switchLlmTab(tabName) {
    const contents = [els.llmTabVisual, els.llmTabPrompt, els.llmTabPayload];
    (els.llmTabButtons || []).forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-llm-tab') === tabName);
    });
    contents.forEach((content) => {
      if (!content) return;
      const expected = content.id.replace('aimsLlmTab', '').toLowerCase();
      content.classList.toggle('active', expected === tabName);
    });
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
      const err = new Error(data.detail || data.message || data.error_message || data.error || ('HTTP ' + res.status));
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

  async function resumeFromStep(stepKey) {
    if (state.publicMode || !state.activeProjectId || !stepKey) return;
    const project = state.activeProject || {};
    const step = ((project.progress || {}).steps || []).find((item) => item.key === stepKey) || {};
    const label = step.label || stepKey;
    if (!window.confirm(`确定从「${label}」起点继续吗？后续断点会被清理并重新执行。`)) {
      return;
    }
    setBusy(true);
    try {
      const data = await fetchJson('/medias/api/ai-material-strategist/projects/' + encodeURIComponent(state.activeProjectId) + '/resume-from-step', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ step_key: stepKey, run_ai: true }),
      });
      showToast('已从指定步骤重新排队');
      state.activeProject = data.project;
      await loadProject(state.activeProjectId);
    } catch (err) {
      if (err.data && err.data.project) {
        state.activeProject = err.data.project;
        await loadProject(state.activeProjectId);
      }
      showToast(err.message || '从此步继续失败');
    } finally {
      setBusy(false);
    }
  }

  async function resumeCheckpoint() {
    if (state.publicMode || !state.activeProjectId) return;
    const project = state.activeProject || {};
    const reason = checkpointResumeReasonLabel(project.resume_checkpoint_reason);
    if (!window.confirm(`确定继续未完成项目吗？${reason}，不会清理已完成产品结果。`)) {
      return;
    }
    setBusy(true);
    try {
      const data = await fetchJson('/medias/api/ai-material-strategist/projects/' + encodeURIComponent(state.activeProjectId) + '/resume-checkpoint', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ run_ai: true }),
      });
      showToast('已继续未完成项目');
      state.activeProject = data.project;
      await loadProject(state.activeProjectId);
    } catch (err) {
      if (err.data && err.data.project) {
        state.activeProject = err.data.project;
        await loadProject(state.activeProjectId);
      }
      showToast(err.message || '继续未完成失败');
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
    const canResumeCheckpoint = !state.publicMode && project && project.can_resume_checkpoint;
    const resumeReason = checkpointResumeReasonLabel(project && project.resume_checkpoint_reason);
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
        ${canResumeCheckpoint ? `
          <div class="aims-run-actions">
            <button type="button" class="aims-step-resume" data-resume-checkpoint>继续未完成</button>
            <span class="aims-run-note">${esc(resumeReason)}</span>
          </div>
        ` : ''}
        ${steps.length ? renderProgressSteps(project, steps) : ''}
        ${renderProgressLogs(progress.logs || [])}
      </section>
    `;
  }

  function renderProgressSteps(project, steps) {
    const canResume = !state.publicMode && project && project.status !== 'running';
    return `
      <div class="aims-step-grid">
        ${steps.map((step) => `
          <article class="aims-step-card ${esc(step.status || 'pending')}">
            <div class="aims-step-top">
              <strong>${esc(step.label || step.key)}</strong>
              <span>${esc(progressStepLabel(step.status))}</span>
            </div>
            <p>${esc(step.message || step.description || '')}</p>
            ${canResume ? `<button type="button" class="aims-step-resume" data-resume-step="${esc(step.key)}">从此步继续</button>` : ''}
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
    const ranking = project.ranking_result || {};
    const llmBtn = (!state.publicMode && project.status === 'success' && (ranking.mode === 'ai' || (ranking.batch_results || []).length))
      ? '<button type="button" class="aims-btn" id="aimsProjLlmBtn" data-show-project-llm>提示词 & 报文</button>'
      : '';
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
          ${llmBtn}
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
    const countries = countryCodesForMatrix(products);
    const head = ['产品'].concat(countries).map((label) => `<div class="aims-country-cell head">${esc(label)}</div>`).join('');
    const rows = products.slice(0, 20).map((item) => {
      const byCode = {};
      (item.country_summary || []).forEach((country) => {
        const code = String(country.country_code || country.lang || '').trim().toUpperCase();
        if (code) byCode[code] = country;
      });
      const cells = countries.map((code) => {
        const country = byCode[code] || {};
        const spend = Number(country.ad_spend_usd || 0);
        const roas = Number(country.ad_roas || 0);
        const cls = country.delivery_status || 'never';
        const isZero = spend === 0;
        const roasStatus = getRoasStatus(country.ad_roas, item.effective_breakeven_roas);
        const spendLevelClass = getSpendGreenLevelClass(spend, roasStatus);
        const cellCls = `${cls} ${isZero ? 'is-zero' : ''} ${spendLevelClass}`;
        const spendClass = isZero ? 'aims-cell-spend zero' : 'aims-cell-spend';
        const roasClass = `aims-cell-roas roas-${roasStatus}`;
        const task = country.blocking_task || country.cancelled_task;
        const taskTitle = task ? ` · 任务 #${task.task_id} ${taskStatusLabel(task)}` : '';
        return `
          <div class="aims-country-cell ${esc(cellCls)}" title="${esc(code)} ${fmtUsd(spend)} ROAS ${fmtRoas(country.ad_roas)}${esc(taskTitle)}">
            <span class="${spendClass}">${fmtUsd(spend)}</span>
            <span class="${roasClass}">${roas > 0 ? `R ${fmtRoas(country.ad_roas)}` : '—'}</span>
            ${task ? renderTaskLink(task, true) : ''}
          </div>
        `;
      }).join('');
      return `<div class="aims-country-cell head">#${esc(item.rank_no)} ${esc(item.product_code)}</div>${cells}`;
    }).join('');
    return `<div class="aims-country-grid" style="grid-template-columns:120px repeat(${countries.length}, minmax(74px, 1fr));">${head}${rows}</div>`;
  }

  function countryCodesForMatrix(products) {
    const seen = new Set();
    const codes = [];
    function add(codeValue) {
      const code = String(codeValue || '').trim().toUpperCase();
      if (!code || seen.has(code)) return;
      seen.add(code);
      codes.push(code);
    }
    DEFAULT_COUNTRY_CODES.forEach(add);
    (products || []).forEach((item) => {
      (item.country_summary || []).forEach((country) => add(country.country_code || country.lang));
    });
    return codes;
  }

  function renderProductsTable(products) {
    const rows = products.map((item, index) => {
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
          <td class="aims-table-spend">${fmtUsd(m.spend_30d)}</td>
          <td>${fmtNumber(m.orders_30d)}</td>
          <td class="aims-table-roas ${esc(getRoasColorClass(m.true_roas_30d, item.effective_breakeven_roas))}">${fmtRoas(m.true_roas_30d)}</td>
          <td class="aims-table-spend">${fmtUsd(m.spend_yesterday)}</td>
          <td><span class="aims-chip ${String(ai.priority || '').toLowerCase()}">${esc(ai.priority || 'P3')}</span></td>
          <td>${esc(actionLabel(ai.primary_action))}</td>
          <td>${mk.video_name ? `${esc(mk.video_name)}<br><span>${fmtUsd(mk.cumulative_90_spend)} · 广告 ${fmtNumber(mk.video_ads_count)}</span>` : '—'}</td>
          <td>${renderTaskCountLink(item, index)}</td>
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
    const debug = ai._prompt_debug || ai.prompt_debug || {};
    const provider = debug.provider || 'openrouter';
    const model = debug.model || 'google/gemini-3.5-flash';
    const debugBadge = (!state.publicMode && ai.mode === 'ai') ? `
      <div style="display:flex; align-items:center; gap:8px; margin: 6px 0 8px;">
        <span class="aims-llm-badge" style="margin:0; font-size:10px;">AI评估: ${esc(provider)} / ${esc(model)}</span>
        <button type="button" class="aims-btn" data-show-product-llm="${productIndex}" style="height:20px; line-height:1; padding:0 6px; font-size:10px; font-weight:normal;">提示词 & 报文</button>
      </div>
    ` : '';
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
            ${debugBadge}
            <div class="aims-task-list" style="margin:0 0 12px;">${renderTaskCountLink(item, productIndex)}</div>
            <div class="aims-material-grid">${materials.map((material, materialIndex) => renderMaterial(item, material, productIndex, materialIndex)).join('') || '<div class="aims-empty" style="min-height:48px;">暂无明空候选</div>'}</div>
          </div>
          <div class="aims-band">
            <div class="aims-band-title">国家反馈</div>
            <div class="aims-bars">${renderCountryBars(item.country_summary || [], item.effective_breakeven_roas, ai, productIndex)}</div>
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

  function renderCountryBars(countries, breakevenRoas, aiResult, productIndex) {
    const actions = (aiResult && aiResult.country_actions) || [];
    const actionLookup = {};
    actions.forEach((act) => {
      const code = String(act.country_code || act.lang || '').toUpperCase().trim();
      if (code) actionLookup[code] = act;
    });
    return countries.map((country) => {
      const code = String(country.country_code || country.lang || '').toUpperCase().trim();
      const act = actionLookup[code] || null;
      const spendStyle = getSpendStyle(country.ad_spend_usd);
      const taskHtml = renderCountryTaskCountLink(country, act, productIndex, code);
      return `
        <div class="aims-bar-row" style="grid-template-columns:32px 70px 85px 44px minmax(80px, auto) minmax(80px, auto); align-items: center; gap: 8px;">
          <span>${esc(country.country_code || country.lang)}</span>
          <span>${renderDeliveryStatusBadge(country.delivery_status)}</span>
          <strong class="aims-bar-spend-val" style="${spendStyle}">${fmtUsd(country.ad_spend_usd)}</strong>
          <span class="${esc(getRoasColorClass(country.ad_roas, breakevenRoas))}">${fmtRoas(country.ad_roas)}</span>
          <span>${act ? renderRecommendationBadge(act) : '—'}</span>
          <span>${taskHtml}</span>
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
      const checkpointButton = event.target.closest('[data-resume-checkpoint]');
      if (checkpointButton) {
        resumeCheckpoint();
        return;
      }
      const resumeButton = event.target.closest('[data-resume-step]');
      if (resumeButton) {
        resumeFromStep(resumeButton.getAttribute('data-resume-step'));
        return;
      }
      const importButton = event.target.closest('[data-import-action]');
      if (importButton) {
        importMaterial(importButton);
        return;
      }
      const showTasksButton = event.target.closest('[data-show-tasks]');
      if (showTasksButton) {
        showTasksModal(Number(showTasksButton.getAttribute('data-show-tasks')));
        return;
      }
      const showCountryTasksButton = event.target.closest('[data-show-country-tasks]');
      if (showCountryTasksButton) {
        const parts = String(showCountryTasksButton.getAttribute('data-show-country-tasks') || '').split(':');
        showCountryTasksModal(Number(parts[0]), parts[1]);
        return;
      }
      const projectLlmButton = event.target.closest('[data-show-project-llm], #aimsProjLlmBtn');
      if (projectLlmButton && state.activeProject) {
        const ranking = state.activeProject.ranking_result || {};
        showLlmModal(
          `项目 #${state.activeProject.id} AI复评决策详情`,
          ranking.provider || state.activeProject.provider_code || 'openrouter',
          ranking.model || state.activeProject.model_id || 'google/gemini-3.5-flash',
          ranking.final_usage_log_id,
          {
            type: 'ranking',
            rankingResult: ranking,
            prompt: ranking.final_prompt,
            response_text: ranking.final_response_text,
          },
        ).catch((err) => showToast(err.message || '打开报文失败'));
        return;
      }
      const productLlmButton = event.target.closest('[data-show-product-llm]');
      if (productLlmButton) {
        const productIndex = Number(productLlmButton.getAttribute('data-show-product-llm'));
        const product = (state.activeProject && state.activeProject.products || [])[productIndex];
        if (!product) return;
        const ai = product.ai_result || {};
        const debug = ai._prompt_debug || ai.prompt_debug || {};
        showLlmModal(
          `#${product.rank_no} ${product.product_code || product.product_name} AI评估研判详情`,
          debug.provider || 'openrouter',
          debug.model || 'google/gemini-3.5-flash',
          debug.usage_log_id,
          {
            type: 'product',
            productItem: product,
            prompt: debug.prompt,
            response_text: debug.response_text,
          },
        ).catch((err) => showToast(err.message || '打开报文失败'));
      }
    });
  }
  if (els.taskModalClose) els.taskModalClose.addEventListener('click', hideTasksModal);
  if (els.taskModalBackdrop) els.taskModalBackdrop.addEventListener('click', hideTasksModal);
  if (els.llmModalClose) els.llmModalClose.addEventListener('click', hideLlmModal);
  if (els.llmModalBackdrop) els.llmModalBackdrop.addEventListener('click', hideLlmModal);
  if (els.llmTabButtons) {
    els.llmTabButtons.forEach((button) => {
      button.addEventListener('click', (event) => {
        switchLlmTab(event.currentTarget.getAttribute('data-llm-tab'));
      });
    });
  }
  if (els.llmModalBody) {
    els.llmModalBody.addEventListener('click', (event) => {
      const logButton = event.target.closest('[data-llm-log-id]');
      if (!logButton) return;
      loadLlmPayload(Number(logButton.getAttribute('data-llm-log-id'))).catch((err) => showToast(err.message || '读取报文失败'));
      switchLlmTab('payload');
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
