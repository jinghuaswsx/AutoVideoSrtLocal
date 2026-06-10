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

  function getRoasStatus(roasVal, breakevenRoas) {
    if (roasVal === null || roasVal === undefined || roasVal === '' || roasVal === 0) return 'zero';
    const r = Number(roasVal);
    if (!Number.isFinite(r) || r <= 0) return 'zero';
    const be = Number(breakevenRoas || 0);
    if (be > 0) {
      if (r >= be) return 'green';
      if (r >= be * 0.8 || r >= 1.2) return 'orange';
      return 'red';
    } else {
      if (r >= 1.5) return 'green';
      if (r >= 1.2) return 'orange';
      return 'red';
    }
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
    } else {
      return 'color: #000000; font-size: 11px; font-weight: normal;';
    }
  }

  function renderRecommendationBadge(act) {
    if (!act) return '';
    const label = act.duplicate_suppressed ? '已有任务' : actionLabel(act.action);
    const cls = esc(act.action || '');
    return `<span class="aims-rec-badge ${cls}">${esc(label)}</span>`;
  }

  function renderTaskCountLink(item, productIndex) {
    const tasks = collectProductTasks(item);
    const count = tasks.length;
    if (count === 0) {
      return '<span class="aims-muted" style="font-size: 11px;">无任务</span>';
    }
    return `<button type="button" class="aims-task-count-btn" data-show-tasks="${productIndex}">${count} 个任务</button>`;
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
    const count = tasks.length;
    if (count === 0) {
      return '—';
    }
    return `<button type="button" class="aims-task-count-btn" data-show-country-tasks="${productIndex}:${countryCode}">${count} 个任务</button>`;
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

  function showTasksModal(productIndex) {
    const products = state.activeProject?.products || [];
    const item = products[productIndex];
    if (!item) return;
    const tasks = collectProductTasks(item);
    
    if (els.taskModalTitle) {
      els.taskModalTitle.textContent = `#${item.rank_no} ${item.product_code || item.product_name} 任务清单`;
    }
    
    if (els.taskModalBody) {
      if (tasks.length === 0) {
        els.taskModalBody.innerHTML = '<div class="aims-empty">暂无任务</div>';
      } else {
        els.taskModalBody.innerHTML = tasks.map(task => {
          const id = Number(task.task_id || task.id || 0);
          const url = task.task_url || task.url || ('/tasks/detail/' + id);
          const label = taskStatusLabel(task);
          const taskType = task.type_label || task.task_type || '翻译/处理任务';
          const statusGroup = task.status_group || '';
          
          let linkHtml = '';
          if (state.publicMode) {
            linkHtml = `<span class="aims-task-link ${esc(statusGroup)}">任务 #${id}</span>`;
          } else {
            linkHtml = `<a class="aims-task-link ${esc(statusGroup)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" style="font-weight: 800; font-size: 13px;">任务 #${id}</a>`;
          }
          
          return `
            <div class="aims-modal-task-item">
              <div class="aims-modal-task-info">
                <div class="aims-modal-task-title">${esc(taskType)}</div>
                <div class="aims-modal-task-meta">状态: <span class="aims-status-text ${esc(statusGroup)}">${esc(label)}</span></div>
              </div>
              <div>
                ${linkHtml}
              </div>
            </div>
          `;
        }).join('');
      }
    }
    
    if (els.taskModal) {
      els.taskModal.hidden = false;
    }
  }

  function showCountryTasksModal(productIndex, countryCode) {
    const products = state.activeProject?.products || [];
    const item = products[productIndex];
    if (!item) return;
    
    const country = (item.country_summary || []).find(c => {
      return String(c.country_code || c.lang || '').toUpperCase().trim() === countryCode;
    });
    
    const actions = (item.ai_result && item.ai_result.country_actions) || [];
    const act = actions.find(a => {
      return String(a.country_code || a.lang || '').toUpperCase().trim() === countryCode;
    }) || null;
    
    const tasks = collectCountryTasks(country, act);
    
    if (els.taskModalTitle) {
      els.taskModalTitle.textContent = `#${item.rank_no} ${item.product_code || item.product_name} - ${countryCode} 任务清单`;
    }
    
    if (els.taskModalBody) {
      if (tasks.length === 0) {
        els.taskModalBody.innerHTML = '<div class="aims-empty">暂无任务</div>';
      } else {
        els.taskModalBody.innerHTML = tasks.map(task => {
          const id = Number(task.task_id || task.id || 0);
          const url = task.task_url || task.url || ('/tasks/detail/' + id);
          const label = taskStatusLabel(task);
          const taskType = task.type_label || task.task_type || '翻译/处理任务';
          const statusGroup = task.status_group || '';
          
          let linkHtml = '';
          if (state.publicMode) {
            linkHtml = `<span class="aims-task-link ${esc(statusGroup)}">任务 #${id}</span>`;
          } else {
            linkHtml = `<a class="aims-task-link ${esc(statusGroup)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" style="font-weight: 800; font-size: 13px;">任务 #${id}</a>`;
          }
          
          return `
            <div class="aims-modal-task-item">
              <div class="aims-modal-task-info">
                <div class="aims-modal-task-title">${esc(taskType)}</div>
                <div class="aims-modal-task-meta">状态: <span class="aims-status-text ${esc(statusGroup)}">${esc(label)}</span></div>
              </div>
              <div>
                ${linkHtml}
              </div>
            </div>
          `;
        }).join('');
      }
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
    let html = `<div style="display:flex; flex-direction:column; gap:16px;">`;
    const batches = rankingResult.batch_results || [];
    batches.forEach((b, idx) => {
      const inCount = (b.input && b.input.products && b.input.products.length) || 0;
      const outCount = (b.output && b.output.ranked_products && b.output.ranked_products.length) || 0;
      const logBtn = b.usage_log_id ? `<button type="button" class="aims-btn" data-llm-log-id="${b.usage_log_id}" style="height:20px; line-height:1; padding:0 6px; font-size:10px;">查看该批次报文</button>` : '';
      html += `
        <div class="aims-llm-visual-card">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <h4>第 ${idx + 1} 批 AI 复评 (输入 ${inCount} 个, 输出 ${outCount} 个)</h4>
            ${logBtn}
          </div>
          <div class="aims-llm-grid">
            <div class="aims-llm-param-section">
              <div class="aims-llm-param-title">输入候选产品</div>
              <div class="aims-llm-param-body" style="max-height:150px; overflow-y:auto; padding:8px;">
                <table style="width:100%; font-size:11px; border-collapse:collapse;">
                  <thead><tr style="text-align:left; border-bottom:1px solid #ddd;"><th>Code</th><th>得分</th><th>消耗</th></tr></thead>
                  <tbody>
                    ${(b.input.products || []).map(p => `<tr><td>${esc(p.product_code)}</td><td>${fmtNumber(p.score, 1)}</td><td>${fmtUsd(p.spend_30d)}</td></tr>`).join('')}
                  </tbody>
                </table>
              </div>
            </div>
            <div class="aims-llm-param-section">
              <div class="aims-llm-param-title">AI 选择产品</div>
              <div class="aims-llm-param-body" style="max-height:150px; overflow-y:auto; padding:8px;">
                <ol style="margin:0; padding-left:16px;">
                  ${(b.output.ranked_products || []).map(p => `<li><strong>${esc(p.product_code)}</strong> (Rank ${esc(p.rank)}): ${esc(p.why_selected || '')}</li>`).join('')}
                </ol>
              </div>
            </div>
          </div>
        </div>
      `;
    });

    if (rankingResult.final_input) {
      const finalIn = (rankingResult.final_input.products && rankingResult.final_input.products.length) || 0;
      const finalOut = (rankingResult.final_output && rankingResult.final_output.ranked_products && rankingResult.final_output.ranked_products.length) || 0;
      const finalLogBtn = rankingResult.final_usage_log_id ? `<button type="button" class="aims-btn" data-llm-log-id="${rankingResult.final_usage_log_id}" style="height:20px; line-height:1; padding:0 6px; font-size:10px;">查看决赛报文</button>` : '';
      html += `
        <div class="aims-llm-visual-card" style="border-color:#bfdbfe; background:#eff6ff;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <h4 style="color:#1e3a8a;">最终决赛 Top 20 AI 复评 (输入 ${finalIn} 个, 输出 ${finalOut} 个)</h4>
            ${finalLogBtn}
          </div>
          <div class="aims-llm-grid">
            <div class="aims-llm-param-section">
              <div class="aims-llm-param-title">决赛输入产品</div>
              <div class="aims-llm-param-body" style="max-height:180px; overflow-y:auto; padding:8px;">
                <table style="width:100%; font-size:11px; border-collapse:collapse;">
                  <thead><tr style="text-align:left; border-bottom:1px solid #ddd;"><th>Code</th><th>得分</th><th>消耗</th></tr></thead>
                  <tbody>
                    ${(rankingResult.final_input.products || []).map(p => `<tr><td>${esc(p.product_code)}</td><td>${fmtNumber(p.score, 1)}</td><td>${fmtUsd(p.spend_30d)}</td></tr>`).join('')}
                  </tbody>
                </table>
              </div>
            </div>
            <div class="aims-llm-param-section">
              <div class="aims-llm-param-title">决赛决定 Top 20</div>
              <div class="aims-llm-param-body" style="max-height:180px; overflow-y:auto; padding:8px;">
                <ol style="margin:0; padding-left:16px;">
                  ${(rankingResult.final_output.ranked_products || []).map(p => `<li><strong>${esc(p.product_code)}</strong> (Rank ${esc(p.rank)}): ${esc(p.why_selected || '')}</li>`).join('')}
                </ol>
              </div>
            </div>
          </div>
        </div>
      `;
    }
    
    html += `</div>`;
    return html;
  }

  function renderProductLlmVisual(item) {
    const ai = item.ai_result || {};
    const countries = ai.country_actions || [];
    const materials = ai.material_actions || [];
    return `
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div class="aims-llm-visual-card">
          <h4>分析研判总结</h4>
          <p style="font-size:13px; line-height:1.6; color:#1e293b; margin:6px 0 0 0;">${esc(ai.overall_judgement || '暂无研判结论')}</p>
          <div style="display:flex; gap:8px; margin-top:10px;">
            <span class="aims-chip ${String(ai.priority || '').toLowerCase()}">优先级：${esc(ai.priority || 'P3')}</span>
            <span class="aims-chip">建议首选动作：${esc(actionLabel(ai.primary_action))}</span>
          </div>
        </div>
        
        <div class="aims-llm-grid">
          <div class="aims-llm-param-section">
            <div class="aims-llm-param-title">AI 国家拓展建议 (输入与反馈)</div>
            <div class="aims-llm-param-body" style="padding:8px;">
              ${countries.length === 0 ? '<div class="aims-muted">无建议</div>' : `
                <table style="width:100%; border-collapse:collapse; font-size:11px;">
                  <thead><tr style="text-align:left; border-bottom:1px solid #ddd;"><th>国家</th><th>建议操作</th><th>优先级</th><th>原因</th></tr></thead>
                  <tbody>
                    ${countries.map(c => `
                      <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:6px 0;"><strong>${esc(c.country_code || c.lang)}</strong></td>
                        <td>${esc(actionLabel(c.action))}</td>
                        <td>${esc(c.priority)}</td>
                        <td style="color:#64748b;">${esc(c.reason)}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              `}
            </div>
          </div>
          
          <div class="aims-llm-param-section">
            <div class="aims-llm-param-title">AI 明空素材补充建议</div>
            <div class="aims-llm-param-body" style="padding:8px;">
              ${materials.length === 0 ? '<div class="aims-muted">无建议</div>' : `
                <div style="display:flex; flex-direction:column; gap:8px;">
                  ${materials.map(m => `
                    <div style="padding:8px; border:1px solid #e2e8f0; border-radius:4px; background:#f8fafc;">
                      <div style="font-weight:700; color:#0f172a; margin-bottom:4px;">${esc(m.action === 'import_or_translate' ? '导入或翻译明空素材' : m.action)}</div>
                      <div style="font-size:11px; color:#475569; margin-bottom:4px;">素材路径: <code style="background:#e2e8f0; padding:2px 4px; border-radius:3px;">${esc(m.video_path || m.material_key)}</code></div>
                      <div style="font-size:11px; color:#475569; margin-bottom:4px;">目标语种: ${esc((m.target_langs || []).join(', ') || '无')}</div>
                      <div style="font-size:11px; color:#64748b;">原因: ${esc(m.reason)}</div>
                    </div>
                  `).join('')}
                </div>
              `}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  async function showLlmModal(title, provider, model, usageLogId, debugInfo) {
    if (els.llmModalTitle) {
      els.llmModalTitle.textContent = title;
    }
    
    switchLlmTab('visual');
    
    if (els.llmTabVisual) {
      if (debugInfo.type === 'ranking') {
        els.llmTabVisual.innerHTML = renderProjLlmVisual(debugInfo.rankingResult);
      } else if (debugInfo.type === 'product') {
        els.llmTabVisual.innerHTML = renderProductLlmVisual(debugInfo.productItem);
      } else {
        els.llmTabVisual.innerHTML = '<div class="aims-empty">暂无可视化内容</div>';
      }
    }
    
    if (els.llmTabPrompt) {
      const promptText = debugInfo.prompt || '未找到发送的完整 Prompt';
      els.llmTabPrompt.innerHTML = `
        <div class="aims-llm-badge">供应商: ${esc(provider)} | 模型: ${esc(model)}</div>
        <pre class="aims-llm-code-block">${esc(promptText)}</pre>
      `;
    }
    
    if (els.llmTabPayload) {
      if (!usageLogId || state.publicMode) {
        const respText = debugInfo.response_text || '未找到大模型响应报文';
        els.llmTabPayload.innerHTML = `
          <div class="aims-llm-badge">仅展示本地保存的响应结果 (无 usage_log_id / 处于公开分享模式)</div>
          <h4 style="margin:10px 0 6px;">大模型返回原始文本</h4>
          <pre class="aims-llm-code-block">${esc(respText)}</pre>
        `;
      } else {
        fetchLlmPayload(usageLogId).catch(console.error);
      }
    }
    
    if (els.llmModal) {
      els.llmModal.hidden = false;
    }
  }

  async function fetchLlmPayload(logId) {
    if (!els.llmTabPayload) return;
    if (state.publicMode) {
      els.llmTabPayload.innerHTML = '<div class="aims-llm-badge">公开分享模式下无法查看原始报文</div>';
      return;
    }
    els.llmTabPayload.innerHTML = '<div class="aims-loading">正在从服务器读取原始 JSON 报文...</div>';
    try {
      const res = await fetchJson('/medias/api/ai-material-strategist/llm-payload/' + logId);
      const reqJson = JSON.stringify(res.payload.request_data, null, 2);
      const respJson = JSON.stringify(res.payload.response_data, null, 2);
      els.llmTabPayload.innerHTML = `
        <div class="aims-llm-badge">已连接 usage_log_payloads 数据表，拉取到 log_id #${logId} 的原始报文</div>
        <div class="aims-llm-grid">
          <div>
            <h4 style="margin:0 0 6px 0;">Request Payload (请求报文)</h4>
            <pre class="aims-llm-code-block" style="max-height: 400px;">${esc(reqJson)}</pre>
          </div>
          <div>
            <h4 style="margin:0 0 6px 0;">Response Payload (返回报文)</h4>
            <pre class="aims-llm-code-block" style="max-height: 400px;">${esc(respJson)}</pre>
          </div>
        </div>
      `;
    } catch (err) {
      els.llmTabPayload.innerHTML = `
        <div class="aims-llm-badge" style="background:#fee2e2; color:#dc2626; border-color:#fecaca;">读取原始报文失败: ${esc(err.message)}</div>
      `;
    }
  }

  function switchLlmTab(tabName) {
    const btns = document.querySelectorAll('[data-llm-tab]');
    btns.forEach(btn => {
      if (btn.getAttribute('data-llm-tab') === tabName) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    
    const contents = [els.llmTabVisual, els.llmTabPrompt, els.llmTabPayload];
    contents.forEach(c => {
      if (!c) return;
      if (c.id === 'aimsLlmTab' + tabName.charAt(0).toUpperCase() + tabName.slice(1)) {
        c.classList.add('active');
      } else {
        c.classList.remove('active');
      }
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
      return `
        <button type="button" class="aims-project-item${active}" data-project-id="${esc(project.id)}">
          <span class="aims-project-name">${esc(project.project_name || ('项目 #' + project.id))}</span>
          <span class="aims-project-meta">
            <span class="aims-status ${esc(project.status)}">${statusLabel(project.status)}</span>
            <span>Top ${esc(topCount)}</span>
            ${project.status === 'running' ? `<span>${esc(pct)}%</span>` : ''}
            <span>${esc((project.created_at || '').slice(0, 16))}</span>
          </span>
          ${project.status === 'running' ? `<span class="aims-mini-progress"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></span>` : ''}
        </button>
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
    const isAi = project.ranking_result && project.ranking_result.mode === 'ai';
    const llmBtn = (project.status === 'success' && isAi) 
      ? `<button type="button" class="aims-btn" id="aimsProjLlmBtn" style="height:22px; line-height:1; padding:0 8px; font-size:11px; margin-left: 8px;">AI复评详情</button>`
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
            ${llmBtn}
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
    const countries = countryCodesForMatrix(products);
    const head = ['产品'].concat(countries).map((label) => `<div class="aims-country-cell head">${esc(label)}</div>`).join('');
    const rows = products.slice(0, 20).map((item) => {
      const byCode = {};
      (item.country_summary || []).forEach((country) => { byCode[country.country_code] = country; });
      const cells = countries.map((code) => {
        const country = byCode[code] || {};
        const spend = Number(country.ad_spend_usd || 0);
        const roas = Number(country.ad_roas || 0);
        
        const cls = country.delivery_status || 'never';
        const isZero = spend === 0;
        
        const roasStatus = getRoasStatus(country.ad_roas, item.effective_breakeven_roas);
        const spendLevelClass = getSpendGreenLevelClass(spend, roasStatus);
        const cellCls = `${cls} ${isZero ? 'is-zero' : ''} ${spendLevelClass}`;
        
        const spendClass = isZero 
          ? 'aims-cell-spend zero' 
          : 'aims-cell-spend';
        const roasClass = `aims-cell-roas roas-${roasStatus}`;
        
        const task = country.blocking_task || country.cancelled_task;
        const taskTitle = task ? ` · 任务 #${task.task_id} ${taskStatusLabel(task)}` : '';
        
        return `
          <div class="aims-country-cell ${esc(cellCls)}" title="${esc(code)} ${fmtUsd(spend)} ROAS ${fmtRoas(country.ad_roas)}${esc(taskTitle)}">
            <span class="${spendClass}">${fmtUsd(spend)}</span>
            <span class="${roasClass}">${roas > 0 ? `R ${fmtRoas(country.ad_roas)}` : '—'}</span>
            ${task ? `${renderTaskLink(task, true)}` : ''}
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
    (products || []).forEach((item) => {
      (item.country_summary || []).forEach((country) => {
        const code = String(country.country_code || country.lang || '').trim().toUpperCase();
        if (!code || seen.has(code)) return;
        seen.add(code);
        codes.push(code);
      });
    });
    return codes.length ? codes : DEFAULT_COUNTRY_CODES;
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
      return `<a class="aims-btn primary" href="${esc(action.url)}" target="_blank" rel="noopener noreferrer">${esc(action.label)}</a>`;
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
    const debug = ai._prompt_debug || {};
    const provider = debug.provider || 'openrouter';
    const model = debug.model || 'google/gemini-3.5-flash';
    const debugBadge = ai.mode === 'ai' ? `
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
      if (code) {
        actionLookup[code] = act;
      }
    });

    return countries.map((country) => {
      const code = String(country.country_code || country.lang || '').toUpperCase().trim();
      const act = actionLookup[code] || null;
      
      const spendStyle = getSpendStyle(country.ad_spend_usd);
      const taskHtml = renderCountryTaskCountLink(country, act, productIndex, code);

      return `
        <div class="aims-bar-row" style="grid-template-columns: 32px 70px 85px 44px minmax(80px, auto) minmax(80px, auto); align-items: center; gap: 8px;">
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
      const button = event.target.closest('[data-project-id]');
      if (!button) return;
      loadProject(button.getAttribute('data-project-id')).catch((err) => showToast(err.message));
    });
  }
  if (els.detail) {
    els.detail.addEventListener('click', (event) => {
      const importBtn = event.target.closest('[data-import-action]');
      if (importBtn) {
        importMaterial(importBtn);
        return;
      }

      const showTasksBtn = event.target.closest('[data-show-tasks]');
      if (showTasksBtn) {
        const productIndex = Number(showTasksBtn.getAttribute('data-show-tasks'));
        showTasksModal(productIndex);
        return;
      }

      const showCountryTasksBtn = event.target.closest('[data-show-country-tasks]');
      if (showCountryTasksBtn) {
        const val = showCountryTasksBtn.getAttribute('data-show-country-tasks');
        const parts = val.split(':');
        const productIndex = Number(parts[0]);
        const countryCode = parts[1];
        showCountryTasksModal(productIndex, countryCode);
        return;
      }

      const projLlmBtn = event.target.closest('#aimsProjLlmBtn');
      if (projLlmBtn && state.activeProject) {
        const ranking = state.activeProject.ranking_result || {};
        const provider = ranking.provider || state.activeProject.provider_code || 'openrouter';
        const model = ranking.model || state.activeProject.model_id || 'google/gemini-3.5-flash';
        const usageLogId = ranking.final_usage_log_id;
        showLlmModal(
          `项目 #${state.activeProject.id} AI复评决策详情`,
          provider,
          model,
          usageLogId,
          {
            type: 'ranking',
            rankingResult: ranking,
            prompt: ranking.final_prompt,
            response_text: ranking.final_response_text
          }
        ).catch(console.error);
        return;
      }

      const showProductLlmBtn = event.target.closest('[data-show-product-llm]');
      if (showProductLlmBtn) {
        const productIndex = Number(showProductLlmBtn.getAttribute('data-show-product-llm'));
        const product = (state.activeProject && state.activeProject.products || [])[productIndex];
        if (product) {
          const ai = product.ai_result || {};
          const debug = ai._prompt_debug || {};
          const provider = debug.provider || 'openrouter';
          const model = debug.model || 'google/gemini-3.5-flash';
          showLlmModal(
            `#${product.rank_no} ${product.product_code || product.product_name} AI评估研判详情`,
            provider,
            model,
            debug.usage_log_id,
            {
              type: 'product',
              productItem: product,
              prompt: debug.prompt,
              response_text: debug.response_text
            }
          ).catch(console.error);
        }
        return;
      }
    });
  }

  if (els.taskModalClose) {
    els.taskModalClose.addEventListener('click', hideTasksModal);
  }
  if (els.taskModalBackdrop) {
    els.taskModalBackdrop.addEventListener('click', hideTasksModal);
  }

  if (els.llmModalClose) {
    els.llmModalClose.addEventListener('click', hideLlmModal);
  }
  if (els.llmModalBackdrop) {
    els.llmModalBackdrop.addEventListener('click', hideLlmModal);
  }
  if (els.llmTabButtons) {
    els.llmTabButtons.forEach(btn => {
      btn.addEventListener('click', (event) => {
        const tab = event.currentTarget.getAttribute('data-llm-tab');
        if (tab) {
          switchLlmTab(tab);
        }
      });
    });
  }
  if (els.llmModalBody) {
    els.llmModalBody.addEventListener('click', (event) => {
      const logBtn = event.target.closest('[data-llm-log-id]');
      if (!logBtn) return;
      const logId = Number(logBtn.getAttribute('data-llm-log-id'));
      if (logId) {
        switchLlmTab('payload');
        fetchLlmPayload(logId).catch(console.error);
      }
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
