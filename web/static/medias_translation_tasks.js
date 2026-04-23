(function () {
  'use strict';

  const POLL_INTERVAL_MS = 5000;
  const TERMINAL_TASK_STATUSES = new Set(['done', 'failed', 'error', 'cancelled', 'skipped', 'interrupted', 'paused']);

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function newTabAttrs(url) {
    return `href="${esc(url || '#')}" target="_blank" rel="noopener noreferrer"`;
  }

  function fmtTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function fmtTaskStartTime(value) {
    return value ? fmtTime(value) : '未知时间';
  }

  function percent(progress) {
    const total = Number(progress?.total || 0);
    const done = Number(progress?.done || 0) + Number(progress?.skipped || 0);
    if (!total) return 0;
    return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  }

  function taskBadgeClass(status) {
    if (status === 'done') return 'bt-plan-item__status--done';
    if (status === 'failed' || status === 'error') return 'bt-plan-item__status--error';
    if (status === 'awaiting_voice' || status === 'waiting_manual') return 'bt-plan-item__status--running';
    if (status === 'interrupted') return 'bt-plan-item__status--paused';
    if (status === 'running' || status === 'dispatching' || status === 'syncing_result') return 'bt-plan-item__status--running';
    return 'bt-plan-item__status--pending';
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    if (response.ok) return response.json();
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `${response.status}`);
  }

  function renderTaskCard(task, compact) {
    const progress = task.progress || {};
    const pct = percent(progress);
    const actions = [];
    if (task.can_resume) {
      actions.push(`<button type="button" class="bt-btn bt-btn--primary" data-task-action="resume" data-task-id="${task.id}">重新启动</button>`);
    }
    if (task.can_retry_failed) {
      actions.push(`<button type="button" class="bt-btn bt-btn--ghost" data-task-action="retry-failed" data-task-id="${task.id}">重跑失败项</button>`);
    }
    actions.push(`<a class="bt-btn bt-btn--ghost" ${newTabAttrs(task.detail_url)}>父任务详情</a>`);

    return `
      <section class="mtt-card">
        <header class="mtt-card__head">
          <div class="mtt-card__main">
            <div class="mtt-card__title-row">
              <h3 class="mtt-card__title">批量翻译任务</h3>
              <span class="mtt-card__start">启动 ${esc(fmtTaskStartTime(task.created_at))}</span>
              <span class="bt-plan-item__status ${taskBadgeClass(task.status)}">${esc(task.status_label || task.status)}</span>
            </div>
            <div class="mtt-card__meta">
              <span>任务 ID: <code>${esc(task.id.slice(0, 8))}</code></span>
              <span>语言: ${esc((task.target_lang_labels || []).join(' / ') || '—')}</span>
              <span>范围: ${esc((task.content_type_labels || []).join(' / ') || '—')}</span>
              <span>更新时间: ${esc(fmtTime(task.updated_at || task.created_at))}</span>
            </div>
          </div>
          <div class="mtt-card__actions">${actions.join('')}</div>
        </header>

        <div class="mtt-card__summary">
          <div class="mtt-card__progress">
            <div class="mtt-card__progress-fill" style="width:${pct}%"></div>
          </div>
          <div class="mtt-card__stats">
            <span><strong>${pct}%</strong> (${Number(progress.done || 0) + Number(progress.skipped || 0)}/${Number(progress.total || 0)})</span>
            <span>执行中 ${Number(progress.running || 0) + Number(progress.dispatching || 0) + Number(progress.syncing_result || 0)}</span>
            <span>等待选声音 ${Number(task.waiting_voice_count || 0)}</span>
            <span>失败 ${Number(task.failed_count || 0)}</span>
          </div>
        </div>

        <div class="mtt-items${compact ? ' compact' : ''}">
          ${(task.items || []).map((item) => renderTaskItem(task, item)).join('')}
        </div>
      </section>
    `;
  }

  function renderTaskItem(task, item) {
    const actions = [];
    if (item.detail_url) {
      actions.push(`<a class="bt-btn bt-btn--ghost" ${newTabAttrs(item.detail_url)}>${item.manual_step === 'voice_selection' ? '去选声音' : '查看详情'}</a>`);
    }
    if (item.retryable) {
      actions.push(`<button type="button" class="bt-btn bt-btn--ghost" data-task-action="retry-item" data-task-id="${task.id}" data-item-idx="${item.idx}">重新启动</button>`);
    }
    return `
      <article class="mtt-item">
        <div class="mtt-item__main">
          <div class="mtt-item__title-row">
            <span class="bt-plan-item__status ${taskBadgeClass(item.status)}">${esc(item.status_label || item.status)}</span>
            <strong>${esc(item.kind_label)}</strong>
            <span class="mtt-item__lang">${esc(item.lang_label || item.lang || '—')}</span>
          </div>
          <div class="mtt-item__meta">
            <span>${esc(item.summary || '')}</span>
            ${item.child_task_id ? `<span>子任务 <code>${esc(item.child_task_id.slice(0, 8))}</code></span>` : ''}
            ${item.manual_step === 'voice_selection' ? '<span class="mtt-item__manual">卡在选择声音</span>' : ''}
          </div>
          ${item.error ? `<div class="mtt-item__error">${esc(item.error)}</div>` : ''}
        </div>
        <div class="mtt-item__actions">${actions.join('')}</div>
      </article>
    `;
  }

  function emptyState(message) {
    return `<div class="mtt-empty">${esc(message)}</div>`;
  }

  function hasUnfinishedTasks(items) {
    return (items || []).some((task) => !isTaskComplete(task));
  }

  function isTaskComplete(task) {
    const progress = task?.progress || {};
    const total = Number(progress.total || 0);
    if (total > 0) {
      const completed = Number(progress.done || 0) + Number(progress.skipped || 0);
      return completed >= total;
    }
    const subItems = task?.items || [];
    if (subItems.length) {
      return subItems.every((item) => TERMINAL_TASK_STATUSES.has(item.status));
    }
    return TERMINAL_TASK_STATUSES.has(task?.status);
  }

  function injectStyles() {
    if (document.getElementById('mttStyles')) return;
    const style = document.createElement('style');
    style.id = 'mttStyles';
    style.textContent = `
      .mtt-root { display:flex; flex-direction:column; gap:16px; }
      .mtt-empty {
        padding: 28px 20px;
        border: 1px dashed oklch(84% 0.015 230);
        border-radius: 14px;
        background: oklch(97% 0.006 230);
        color: oklch(48% 0.018 230);
        text-align: center;
      }
      .mtt-card {
        border: 1px solid oklch(91% 0.012 230);
        border-radius: 16px;
        background: oklch(99% 0.004 230);
        padding: 18px;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .mtt-card__head {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 14px;
        flex-wrap: wrap;
      }
      .mtt-card__main {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .mtt-card__title-row {
        display: flex;
        gap: 10px;
        align-items: center;
        flex-wrap: wrap;
      }
      .mtt-card__title {
        margin: 0;
        color: var(--accent-active, oklch(45% 0.16 230));
        font-size: var(--text-lg, 18px);
        font-weight: 700;
        line-height: 1.3;
      }
      .mtt-card__start {
        color: var(--fg-muted, oklch(48% 0.018 230));
        font-size: 12px;
        font-weight: 600;
        line-height: 1.4;
      }
      .mtt-card__meta,
      .mtt-card__stats,
      .mtt-item__meta {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        color: oklch(48% 0.018 230);
        font-size: 12px;
        line-height: 1.6;
      }
      .mtt-card__actions,
      .mtt-item__actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .mtt-card__summary {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .mtt-card__progress {
        height: 8px;
        border-radius: 999px;
        overflow: hidden;
        background: oklch(94% 0.01 230);
      }
      .mtt-card__progress-fill {
        height: 100%;
        background: oklch(56% 0.16 230);
      }
      .mtt-items {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .mtt-item {
        border: 1px solid oklch(91% 0.012 230);
        border-radius: 12px;
        background: oklch(97% 0.006 230);
        padding: 12px 14px;
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: flex-start;
        flex-wrap: wrap;
      }
      .mtt-item__main {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .mtt-item__title-row {
        display: flex;
        gap: 10px;
        align-items: center;
        flex-wrap: wrap;
      }
      .mtt-item__lang,
      .mtt-item__manual {
        display: inline-flex;
        align-items: center;
        min-height: 22px;
        padding: 0 8px;
        border-radius: 999px;
        background: oklch(94% 0.04 225);
        color: oklch(56% 0.16 230);
        font-size: 11px;
        font-weight: 600;
      }
      .mtt-item__manual {
        background: oklch(96% 0.05 85);
        color: oklch(42% 0.1 60);
      }
      .mtt-item__error {
        color: oklch(42% 0.14 25);
        font-size: 12px;
        line-height: 1.55;
      }
      .mtt-root.compact .mtt-card {
        border-radius: 14px;
        padding: 14px;
      }
      .mtt-root.compact .mtt-item {
        padding: 10px 12px;
      }
      @media (max-width: 860px) {
        .mtt-card__actions,
        .mtt-item__actions {
          width: 100%;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function mount(container, productId, options) {
    injectStyles();
    const compact = !!options?.compact;
    let timer = null;
    let refreshInFlight = false;
    let destroyed = false;

    async function load() {
      container.innerHTML = `<div class="mtt-root${compact ? ' compact' : ''}">${emptyState('翻译任务加载中…')}</div>`;
      try {
        const payload = await fetchJSON(`/medias/api/products/${productId}/translation-tasks`);
        const items = payload.items || [];
        render(items);
        updatePolling(items);
      } catch (error) {
        container.innerHTML = `<div class="mtt-root${compact ? ' compact' : ''}">${emptyState(`加载失败：${error.message || error}`)}</div>`;
      }
    }

    function render(items) {
      if (!items.length) {
        container.innerHTML = `<div class="mtt-root${compact ? ' compact' : ''}">${emptyState('当前产品还没有翻译任务')}</div>`;
        return;
      }
      container.innerHTML = `
        <div class="mtt-root${compact ? ' compact' : ''}">
          ${items.map((task) => renderTaskCard(task, compact)).join('')}
        </div>
      `;
    }

    function stopPolling() {
      if (timer) {
        window.clearInterval(timer);
        timer = null;
      }
    }

    function updatePolling(items) {
      if (destroyed || !hasUnfinishedTasks(items)) {
        stopPolling();
        return;
      }
      if (!timer) {
        timer = window.setInterval(refresh, POLL_INTERVAL_MS);
      }
    }

    async function trigger(action, taskId, itemIdx) {
      const confirmMap = {
        resume: '重新启动后会从中断点继续，确定继续吗？',
        'retry-failed': '会把当前父任务中失败的子项重新拉起，确定继续吗？',
        'retry-item': '会从该失败子项开始重新继续，确定继续吗？',
      };
      if (confirmMap[action] && !window.confirm(confirmMap[action])) {
        return;
      }
      let url = '';
      let payload = undefined;
      if (action === 'resume') {
        url = `/api/bulk-translate/${taskId}/resume`;
      } else if (action === 'retry-failed') {
        url = `/api/bulk-translate/${taskId}/retry-failed`;
      } else if (action === 'retry-item') {
        url = `/api/bulk-translate/${taskId}/retry-item`;
        payload = { idx: Number(itemIdx) };
      } else {
        return;
      }
      await fetchJSON(url, {
        method: 'POST',
        headers: payload ? { 'Content-Type': 'application/json' } : undefined,
        body: payload ? JSON.stringify(payload) : undefined,
      });
      await refresh();
    }

    async function refresh() {
      if (refreshInFlight) return;
      refreshInFlight = true;
      try {
        const payload = await fetchJSON(`/medias/api/products/${productId}/translation-tasks`);
        const items = payload.items || [];
        render(items);
        updatePolling(items);
      } catch (error) {
        console.error('[medias_translation_tasks] refresh failed', error);
      } finally {
        refreshInFlight = false;
      }
    }

    container.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-task-action]');
      if (!button) return;
      event.preventDefault();
      try {
        await trigger(button.dataset.taskAction, button.dataset.taskId, button.dataset.itemIdx);
      } catch (error) {
        window.alert(error.message || error);
      }
    });

    load();

    return {
      refresh,
      destroy() {
        destroyed = true;
        stopPolling();
      },
    };
  }

  window.MediasTranslationTasks = { mount };

  const pageRoot = document.getElementById('mediasTranslationTasksPage');
  if (pageRoot) {
    const productId = pageRoot.dataset.productId;
    const mountNode = document.getElementById('mediasTranslationTasksMount');
    if (productId && mountNode) {
      mount(mountNode, productId, { compact: false });
    }
  }
})();
