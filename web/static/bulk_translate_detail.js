/* /tasks/<id> 任务详情页交互。 */
(function () {
  'use strict';

  const root = document.querySelector('.bt-detail');
  if (!root) return;
  const taskId = root.dataset.taskId;
  const adminScope = root.dataset.adminScope === '1';
  let currentTask = null;

  async function loadTask() {
    try {
      const r = await fetch(apiUrl(`/api/bulk-translate/${taskId}`));
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderLoadError(err.error || r.status);
        return;
      }
      currentTask = await r.json();
      render();
    } catch (e) {
      console.error(e);
      renderLoadError(e.message || '网络错误');
    }
  }

  function renderLoadError(message) {
    const safeMessage = esc(message || '未知错误');
    root.querySelector('[data-bt-status-panel]').innerHTML = `
      <div class="bt-empty-state bt-empty-state--error">
        <strong>任务状态加载失败</strong>
        <span>${safeMessage}</span>
      </div>
    `;
    root.querySelector('[data-bt-meta]').innerHTML =
      `<span class="bt-warn">加载失败: ${safeMessage}</span>`;
  }

  function render() {
    const t = currentTask || {};
    const s = t.state || {};
    const plan = s.plan || [];
    const progress = normalizeProgress(s.progress || {}, plan);

    renderStatusPanel(t, progress);
    renderMeta(t, s);
    renderSummary(s, progress);
    renderActions(t.status);
    renderPlan(plan);
    renderAudit(s.audit_events || []);
  }

  function renderStatusPanel(task, progress) {
    const status = normalizeStatus(task.status);
    const insight = buildStatusInsight(status, progress);
    const state = task.state || {};
    const estimate = buildProgressEstimate(progress, state.plan || [], task);
    const panel = root.querySelector('[data-bt-status-panel]');
    panel.dataset.status = status;
    panel.innerHTML = `
      <div class="bt-status-hero__body">
        <span class="bt-status-kicker">任务当前状态</span>
        <div class="bt-status-hero__title">
          <span class="bt-status-pill bt-status-pill--${statusTone(status)}">${statusLabel(status)}</span>
          <strong>${esc(insight.title)}</strong>
        </div>
        <p>${esc(insight.detail)}</p>
        <div class="bt-status-next">
          <span>下一步</span>
          <strong>${esc(insight.next)}</strong>
        </div>
      </div>
      <div class="bt-status-hero__meter" aria-label="整体进度 ${progress.pct}%">
        <strong>${progress.pct}%</strong>
        <span>整体进度</span>
        <div class="bt-status-meter-bar" aria-hidden="true">
          <i style="width:${progress.pct}%"></i>
        </div>
        <small>${progress.completed}/${progress.total} 已处理</small>
        <small class="bt-status-eta">${esc(estimate)}</small>
      </div>
    `;
  }

  function buildProgressEstimate(progress, plan, task) {
    if (!progress.total) return '暂无可估算的子任务';
    const remaining = Math.max(0, progress.total - progress.completed);
    if (remaining <= 0) return '已全部完成';

    const completedDurations = (plan || [])
      .map(item => {
        const startedAt = parseTimestamp(
          item.started_at || item.start_at || item.dispatched_at || item.created_at
        );
        const finishedAt = parseTimestamp(
          item.finished_at || item.completed_at || item.done_at || item.updated_at
        );
        return startedAt && finishedAt && finishedAt > startedAt
          ? finishedAt - startedAt
          : 0;
      })
      .filter(duration => duration > 0);

    if (completedDurations.length) {
      const avgMs = completedDurations.reduce((sum, value) => sum + value, 0) / completedDurations.length;
      return `预计还需约 ${formatDuration(avgMs * remaining)}（剩余 ${remaining} 个）`;
    }

    const createdAt = parseTimestamp(task.created_at);
    if (createdAt && progress.completed > 0) {
      const elapsed = Date.now() - createdAt;
      if (elapsed > 0) {
        return `预计还需约 ${formatDuration((elapsed / progress.completed) * remaining)}（剩余 ${remaining} 个）`;
      }
    }

    return `剩余 ${remaining} 个，等待更多完成样本`;
  }

  function buildStatusInsight(status, progress) {
    if (status === 'planning') {
      return {
        title: '任务已创建，还没有开始派发子任务',
        detail: `系统已经生成 ${progress.total} 个子任务，当前全部等待启动。`,
        next: '确认配置无误后，点击“开始执行”。',
      };
    }
    if (status === 'running') {
      const activeText = progress.active > 0
        ? `正在处理 ${progress.active} 个子任务`
        : '任务正在排队推进';
      return {
        title: activeText,
        detail: `已完成 ${progress.done} 个，跳过 ${progress.skipped} 个，失败 ${progress.failed} 个，待执行 ${progress.pending} 个。`,
        next: progress.failed > 0
          ? '先等当前轮次结束，再在“需要处理”里重跑失败项。'
          : '等待系统继续派发后续子任务，页面会自动刷新进度。',
      };
    }
    if (status === 'waiting_manual') {
      return {
        title: '任务暂停在人工确认步骤',
        detail: `还有 ${progress.pending + progress.active} 个子任务未完成，通常需要先处理子任务里的人工选择。`,
        next: '打开正在等待的子任务，完成必要选择后再回到这里观察进度。',
      };
    }
    if (status === 'paused') {
      return {
        title: '任务已暂停，不会继续派发新子任务',
        detail: `当前已处理 ${progress.completed}/${progress.total} 个子任务。`,
        next: '确认可以继续后点击“整个任务重新启动”，只恢复中断项，已完成结果会保留。',
      };
    }
    if (status === 'interrupted') {
      return {
        title: '任务因服务重启或后台中断已停止',
        detail: `当前已处理 ${progress.completed}/${progress.total} 个子任务，中断项不会在服务启动时自动重跑。`,
        next: '可点击“整个任务重新启动”恢复中断项，或用“重跑失败项”只处理失败/中断项。',
      };
    }
    if (status === 'failed' || status === 'error') {
      return {
        title: `有 ${progress.failed || '部分'} 个子任务失败`,
        detail: `失败项会停在“需要处理”分区，已完成结果会保留。`,
        next: '先查看失败原因，再选择“单个重新启动”或“重跑失败项”。',
      };
    }
    if (status === 'done') {
      return {
        title: '全部子任务已经处理完成',
        detail: `本次任务处理了 ${progress.total} 个子任务，其中 ${progress.skipped} 个因已有译本被跳过。`,
        next: '检查目标语种素材是否已回填，必要时可对单项重新翻译。',
      };
    }
    if (status === 'cancelled') {
      return {
        title: '任务已取消',
        detail: `取消前已处理 ${progress.completed}/${progress.total} 个子任务，已完成结果会保留。`,
        next: '如需继续，请重新创建任务或重跑指定子任务。',
      };
    }
    return {
      title: '任务状态需要进一步确认',
      detail: `当前状态为 ${status || '未知'}，已处理 ${progress.completed}/${progress.total} 个子任务。`,
      next: '查看子任务分区和操作记录，确认是否需要重跑。',
    };
  }

  function renderMeta(task, state) {
    const initiator = state.initiator || {};
    const meta = [
      ['创建时间', formatDate(task.created_at)],
      ['更新时间', formatDate(task.updated_at)],
      ['创建人', initiator.user_name || initiator.user_id || '—'],
      ['来源 IP', initiator.ip || '—'],
      ['任务 ID', task.id ? task.id.slice(0, 8) : '—'],
    ];
    root.querySelector('[data-bt-meta]').innerHTML = meta.map(([label, value]) => `
      <div class="bt-meta-item">
        <span>${esc(label)}</span>
        <strong>${esc(value)}</strong>
      </div>
    `).join('');
  }

  function renderSummary(state, progress) {
    root.querySelector('.bt-detail__progress-fill').style.width = `${progress.pct}%`;
    root.querySelector('[data-bt-progress-label]').textContent = `${progress.pct}%`;

    const cost = state.cost_tracking || { estimate: {}, actual: {} };
    const estimate = cost.estimate || {};
    const actual = cost.actual || {};
    const stats = [
      ['已处理', `${progress.completed}/${progress.total}`, `${progress.done} 完成 · ${progress.skipped} 跳过`],
      ['正在处理', progress.active, activeBreakdown(progress)],
      ['待执行', progress.pending, '等待系统派发'],
      ['失败', progress.failed, progress.failed ? '需要处理' : '暂无失败'],
      ['费用', `¥${formatMoney(actual.actual_cost_cny || 0)}`, `预估 ¥${formatMoney(estimate.estimated_cost_cny || 0)}`],
    ];

    root.querySelector('[data-bt-stats]').innerHTML = stats.map(([label, value, hint]) => `
      <div class="bt-stat-card">
        <span>${esc(label)}</span>
        <strong>${esc(value)}</strong>
        <small>${esc(hint)}</small>
      </div>
    `).join('');
  }

  function renderActions(status) {
    const box = root.querySelector('[data-bt-actions]');
    const normalized = normalizeStatus(status);
    const btns = [];
    if (normalized === 'planning') {
      btns.push(`<button class="bt-btn bt-btn--primary" data-act="start">开始执行</button>`);
    }
    if (normalized === 'paused' || normalized === 'interrupted') {
      btns.push(`<button class="bt-btn bt-btn--primary" data-act="resume" title="重新启动整个批量任务：只恢复中断的子项，已完成项不会重复执行。">整个任务重新启动</button>`);
    }
    if (normalized === 'running') {
      btns.push(`<button class="bt-btn bt-btn--ghost" data-act="pause">暂停</button>`);
    }
    if (normalized === 'failed' || normalized === 'error' || normalized === 'interrupted') {
      btns.push(`<button class="bt-btn bt-btn--ghost" data-act="retry-failed" title="只重跑失败或中断的子项：已完成项不会重复执行。">重跑失败项</button>`);
    }
    if (normalized === 'running' || normalized === 'paused') {
      btns.push(`<button class="bt-btn bt-btn--danger" data-act="cancel">取消任务</button>`);
    }

    box.innerHTML = btns.length
      ? btns.join('')
      : '<span class="bt-actions-note">当前状态无需人工操作。</span>';
    box.querySelectorAll('[data-act]').forEach(b => {
      b.addEventListener('click', () => doAction(b.dataset.act));
    });
  }

  async function doAction(action) {
    let url;
    let label;
    if (action === 'start') {
      url = `/api/bulk-translate/${taskId}/start`;
      label = '开始执行';
    } else if (action === 'resume') {
      if (!confirm('将重新启动整个批量任务，只恢复中断的子项，已完成项不会重复执行。确定继续吗？')) return;
      url = `/api/bulk-translate/${taskId}/resume`;
      label = '整个任务重新启动';
    } else if (action === 'pause') {
      url = `/api/bulk-translate/${taskId}/pause`;
      label = '暂停';
    } else if (action === 'cancel') {
      if (!confirm('确认取消任务? 已完成的子任务结果会保留。')) return;
      url = `/api/bulk-translate/${taskId}/cancel`;
      label = '取消';
    } else if (action === 'retry-failed') {
      if (!confirm('将只重跑失败或中断的子项，已完成项不会重复执行。确定继续吗？')) return;
      url = `/api/bulk-translate/${taskId}/retry-failed`;
      label = '重跑失败项';
    } else {
      return;
    }

    try {
      const r = await fetch(apiUrl(url), { method: 'POST' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(`${label}失败: ${err.error || r.status}`);
        return;
      }
      await loadTask();
    } catch (e) {
      alert('网络错误: ' + e.message);
    }
  }

  function renderPlan(plan) {
    const box = root.querySelector('[data-bt-plan]');
    if (!plan.length) {
      box.innerHTML = `
        <div class="bt-empty-state">
          <strong>暂无子任务</strong>
          <span>这个任务还没有生成可执行的翻译计划。</span>
        </div>
      `;
      return;
    }

    const split = splitTaskCards(plan);
    const interventionSummary = split.intervention.length
      ? `${split.intervention.length} 个任务需要你处理`
      : '当前没有需要人工介入的任务';
    const normalSummary = `${split.normal.length} 个任务正在运行、等待执行或已经完成`;

    box.innerHTML = `
      <div class="bt-plan-list">
        <section class="bt-task-zone bt-intervention-zone${split.intervention.length ? '' : ' bt-task-zone--empty'}">
          <div class="bt-task-zone__header">
            <div>
              <span>优先处理</span>
              <h3>需要人工干预</h3>
              <p>${esc(interventionSummary)}</p>
            </div>
            <strong>${split.intervention.length}</strong>
          </div>
          <div class="bt-task-zone__cards">
            ${split.intervention.length
              ? split.intervention.map(item => renderTaskCard(item, { intervention: true })).join('')
              : renderNoInterventionState()}
          </div>
        </section>

        <section class="bt-task-zone">
          <div class="bt-task-zone__header">
            <div>
              <span>其他任务</span>
              <h3>正常运行 / 等待执行 / 已完成</h3>
              <p>${esc(normalSummary)}</p>
            </div>
            <strong>${split.normal.length}</strong>
          </div>
          <div class="bt-task-zone__cards">
            ${split.normal.map(item => renderTaskCard(item, { intervention: false })).join('')}
          </div>
        </section>
      </div>
    `;

    box.querySelectorAll('[data-retry-idx]').forEach(b => {
      b.addEventListener('click', async () => {
        const idx = parseInt(b.dataset.retryIdx, 10);
        if (!confirm(`将只重新启动 #${idx} 这一项，其他子项保持当前状态。确定继续吗？`)) return;
        const r = await fetch(apiUrl(`/api/bulk-translate/${taskId}/retry-item`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ idx }),
        });
        if (!r.ok) {
          const e = await r.json().catch(() => ({}));
          alert('失败: ' + (e.error || r.status));
          return;
        }
        await loadTask();
      });
    });
  }

  function splitTaskCards(plan) {
    const intervention = [];
    const normal = [];
    (plan || []).forEach(item => {
      if (needsHumanIntervention(item)) {
        intervention.push(item);
      } else {
        normal.push(item);
      }
    });
    return { intervention, normal };
  }

  function renderNoInterventionState() {
    return `
      <div class="bt-empty-state bt-empty-state--compact">
        <strong>暂时不用处理</strong>
        <span>失败、等待选声音、已中断的任务会自动出现在这里。</span>
      </div>
    `;
  }

  function renderTaskCard(item, options) {
    const opts = options || {};
    const status = normalizeStatus(item.status);
    const retry = isRetryableItem(item)
      ? `<button class="bt-btn bt-btn--ghost bt-task-card__button" data-retry-idx="${item.idx}" title="只重新启动这一项：其他子项保持当前状态。">单个重新启动</button>`
      : '';
    const ref = refHintText(item);
    const childTaskId = item.child_task_id || item.sub_task_id;
    const childTaskType = item.child_task_type ? ` · ${childTypeLabel(item.child_task_type)}` : '';
    const child = childTaskId
      ? `子任务 ${String(childTaskId).slice(0, 8)}${childTaskType}`
      : '尚未创建子任务';
    const childUrl = childDetailUrl(item.child_task_type, childTaskId);
    const openChild = childUrl
      ? `<a class="bt-btn bt-btn--ghost bt-task-card__button" href="${esc(childUrl)}" target="_blank" rel="noopener noreferrer">${status === 'awaiting_voice' ? '去选声音' : '打开子任务'}</a>`
      : '';
    const statusHelp = taskStatusHelp(item);
    const intervention = opts.intervention
      ? `<div class="bt-task-card__notice">${esc(interventionReason(item))}</div>`
      : '';
    const error = item.error
      ? `<div class="bt-task-card__error">失败原因: ${esc(item.error)}</div>`
      : '';
    const actions = [openChild, retry].filter(Boolean).join('');

    return `
      <article class="bt-task-card bt-task-card--${statusTone(status)}">
        <div class="bt-task-card__head">
          <div>
            <span class="bt-task-card__eyebrow">任务 #${esc(item.idx == null ? '-' : item.idx)}</span>
            <h4>${esc(taskCardTitle(item))}</h4>
          </div>
          <span class="bt-status-pill bt-status-pill--${statusTone(status)}">${esc(statusLabel(status))}</span>
        </div>

        <div class="bt-task-card__body">
          <div class="bt-task-card__state">
            <span>当前状态</span>
            <strong>${esc(statusHelp)}</strong>
          </div>
          <div class="bt-task-card__meta">
            <span>${esc(ref || '无素材引用')}</span>
            <span>${esc(child)}</span>
          </div>
          ${intervention}
          ${error}
        </div>

        <div class="bt-task-card__actions">
          ${actions || '<span class="bt-task-card__no-action">暂无可操作按钮</span>'}
        </div>
      </article>
    `;
  }

  function taskCardTitle(item) {
    return `${languageLabel(item.lang)} · ${kindLabel(item.kind)}`;
  }

  function refHintText(item) {
    const r = item.ref || {};
    if (r.source_copy_id) return `文案 #${r.source_copy_id}`;
    if (r.source_item_id) return `素材 #${r.source_item_id}`;
    if (r.source_raw_id) return `原始素材 #${r.source_raw_id}`;
    if (r.source_raw_ids) return `${r.source_raw_ids.length} 个原始素材`;
    if (r.source_detail_ids) return `${r.source_detail_ids.length} 张详情图`;
    if (r.source_cover_ids) return `${r.source_cover_ids.length} 张主图`;
    return '';
  }

  function needsHumanIntervention(item) {
    const status = normalizeStatus(item.status);
    return ['failed', 'error', 'interrupted', 'awaiting_voice', 'waiting_manual'].includes(status);
  }

  function taskStatusHelp(item) {
    const status = normalizeStatus(item.status);
    if (status === 'failed') return '执行失败，需要查看失败原因后重跑';
    if (status === 'interrupted') return '任务被中断，需要重新启动';
    if (status === 'awaiting_voice') return '等待人工选声音，完成后流程会继续';
    if (status === 'waiting_manual') return '等待人工确认';
    if (status === 'running') return '正在执行中';
    if (status === 'dispatching') return '正在创建或派发子任务';
    if (status === 'syncing_result') return '子任务已完成，正在同步回填结果';
    if (status === 'pending') return '排队中，等待系统执行';
    if (status === 'done') return '已完成并回填结果';
    if (status === 'skipped') return '已跳过，通常是因为已有译本';
    return '等待进一步状态更新';
  }

  function interventionReason(item) {
    const status = normalizeStatus(item.status);
    if (status === 'awaiting_voice') return '等待人工选声音';
    if (status === 'failed') return '失败任务，需要处理';
    if (status === 'interrupted') return '中断任务，需要重新启动';
    if (status === 'waiting_manual') return '等待人工确认';
    return '需要人工干预';
  }

  function childDetailUrl(taskType, childTaskId) {
    if (!taskType || !childTaskId) return '';
    if (taskType === 'multi_translate') return `/multi-translate/${childTaskId}`;
    if (taskType === 'image_translate') return `/image-translate/${childTaskId}`;
    if (taskType === 'translate_lab') return `/translate-lab/${childTaskId}`;
    if (taskType === 'copywriting_translate') return `/copywriting/${childTaskId}`;
    return '';
  }

  function renderAudit(events) {
    const box = root.querySelector('[data-bt-audit]');
    if (!events.length) {
      box.innerHTML = `
        <div class="bt-empty-state">
          <strong>暂无操作记录</strong>
          <span>任务启动、暂停、重跑等动作会显示在这里。</span>
        </div>
      `;
      return;
    }
    const shown = events.slice().reverse().slice(0, 10);
    const hiddenCount = Math.max(0, events.length - shown.length);
    box.innerHTML = `
      ${hiddenCount ? `<div class="bt-audit-note">仅显示最近 10 条，另有 ${hiddenCount} 条历史记录。</div>` : ''}
      ${shown.map(e => {
        const detail = e.detail && Object.keys(e.detail).length
          ? `<code>${esc(JSON.stringify(e.detail))}</code>`
          : '';
        return `
          <div class="bt-audit-event">
            <time>${esc(formatDate(e.ts))}</time>
            <div>
              <strong>${esc(actionLabel(e.action))}</strong>
              <span>用户 ${esc(e.user_id || '—')}</span>
              ${detail}
            </div>
          </div>
        `;
      }).join('')}
    `;
  }

  function normalizeProgress(rawProgress, plan) {
    const p = rawProgress || {};
    const total = Math.max(toNumber(p.total, plan.length), plan.length);
    const done = toNumber(p.done, countByStatus(plan, ['done']));
    const skipped = toNumber(p.skipped, countByStatus(plan, ['skipped']));
    const dispatching = toNumber(p.dispatching, countByStatus(plan, ['dispatching']));
    const running = toNumber(p.running, countByStatus(plan, ['running']));
    const syncingResult = toNumber(p.syncing_result, countByStatus(plan, ['syncing_result']));
    const awaitingVoice = toNumber(p.awaiting_voice, countByStatus(plan, ['awaiting_voice']));
    const failed = toNumber(p.failed, countByStatus(plan, ['failed', 'error']));
    const interrupted = toNumber(p.interrupted, countByStatus(plan, ['interrupted']));
    const active = dispatching + running + syncingResult + awaitingVoice;
    const pending = toNumber(
      p.pending,
      Math.max(0, total - done - skipped - active - failed - interrupted)
    );
    const completed = done + skipped;
    const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    return {
      total,
      done,
      skipped,
      dispatching,
      running,
      syncingResult,
      awaitingVoice,
      active,
      failed: failed + interrupted,
      pending,
      completed,
      pct,
    };
  }

  function activeBreakdown(progress) {
    const parts = [];
    if (progress.dispatching) parts.push(`${progress.dispatching} 派发中`);
    if (progress.running) parts.push(`${progress.running} 运行中`);
    if (progress.syncingResult) parts.push(`${progress.syncingResult} 同步结果`);
    if (progress.awaitingVoice) parts.push(`${progress.awaitingVoice} 等待选音`);
    return parts.length ? parts.join(' · ') : '暂无运行项';
  }

  function countByStatus(plan, statuses) {
    const wanted = new Set(statuses);
    return (plan || []).filter(item => wanted.has(normalizeStatus(item.status))).length;
  }

  function isActiveItem(item) {
    return ['dispatching', 'running', 'syncing_result', 'awaiting_voice']
      .includes(normalizeStatus(item.status));
  }

  function isFailedItem(item) {
    return ['failed', 'error', 'interrupted'].includes(normalizeStatus(item.status));
  }

  function isRetryableItem(item) {
    return ['failed', 'error', 'interrupted', 'done'].includes(normalizeStatus(item.status));
  }

  function normalizeStatus(status) {
    const raw = String(status || '').trim();
    if (raw === 'error') return 'failed';
    return raw || 'pending';
  }

  function statusLabel(status) {
    return {
      planning: '待启动',
      running: '运行中',
      paused: '已暂停',
      waiting_manual: '等待人工确认',
      done: '已完成',
      failed: '失败',
      cancelled: '已取消',
      skipped: '已跳过',
      pending: '待执行',
      dispatching: '派发中',
      syncing_result: '同步结果',
      awaiting_voice: '等待选音',
      interrupted: '已中断',
    }[status] || status;
  }

  function statusTone(status) {
    if (['done', 'skipped'].includes(status)) return 'success';
    if (['failed', 'error', 'interrupted'].includes(status)) return 'danger';
    if (['paused', 'waiting_manual'].includes(status)) return 'warning';
    if (['running', 'dispatching', 'syncing_result', 'awaiting_voice'].includes(status)) return 'accent';
    return 'muted';
  }

  function kindLabel(kind) {
    return {
      copy: '商品文案',
      copywriting: '商品文案',
      cover: '商品主图',
      video_covers: '视频封面',
      detail: '商品详情图',
      detail_images: '商品详情图',
      video: '视频素材',
      videos: '视频素材',
    }[kind] || kind || '未知类型';
  }

  function childTypeLabel(type) {
    return {
      copywriting_translate: '文案翻译',
      image_translate: '图片翻译',
      translate_lab: '视频翻译',
      multi_translate: '多语种视频翻译',
    }[type] || type;
  }

  function languageLabel(code) {
    return {
      de: '德语',
      fr: '法语',
      es: '西班牙语',
      it: '意大利语',
      ja: '日语',
      pt: '葡萄牙语',
      nl: '荷兰语',
      sv: '瑞典语',
      fi: '芬兰语',
    }[code] || String(code || '未知语种').toUpperCase();
  }

  function actionLabel(action) {
    return {
      create: '创建任务',
      start: '开始执行',
      pause: '暂停任务',
      resume: '整个任务重新启动',
      cancel: '取消任务',
      retry_item: '单个重新启动',
      retry_failed: '重跑失败项',
    }[action] || action || '系统记录';
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString('zh-CN', { hour12: false });
    }
    return String(value).replace('T', ' ');
  }

  function parseTimestamp(value) {
    if (!value) return 0;
    const ts = new Date(value).getTime();
    return Number.isFinite(ts) ? ts : 0;
  }

  function formatDuration(ms) {
    const minutes = Math.max(1, Math.ceil(Number(ms || 0) / 60000));
    if (minutes < 60) return `${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    const restMinutes = minutes % 60;
    if (hours < 24) {
      return restMinutes ? `${hours} 小时 ${restMinutes} 分钟` : `${hours} 小时`;
    }
    const days = Math.floor(hours / 24);
    const restHours = hours % 24;
    return restHours ? `${days} 天 ${restHours} 小时` : `${days} 天`;
  }

  function formatMoney(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return '0';
    return n.toFixed(2).replace(/\.00$/, '');
  }

  function toNumber(value, fallback) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
    const f = Number(fallback);
    return Number.isFinite(f) ? f : 0;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  }

  function apiUrl(path) {
    if (!adminScope) return path;
    const joiner = path.includes('?') ? '&' : '?';
    return `${path}${joiner}scope=admin`;
  }

  window.addEventListener('bt-progress', e => { if (e.detail.task_id === taskId) loadTask(); });
  window.addEventListener('bt-done', e => { if (e.detail.task_id === taskId) loadTask(); });

  loadTask();
})();
