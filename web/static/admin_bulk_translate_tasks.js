/* 管理员批量翻译任务总览 */
(function () {
  'use strict';

  const root = document.querySelector('[data-admin-bulk-tasks]');
  if (!root) return;

  const statsBox = root.querySelector('[data-admin-stats]');
  const listBox = root.querySelector('[data-admin-task-list]');
  const emptyBox = root.querySelector('[data-admin-empty]');

  async function loadTasks() {
    try {
      const r = await fetch('/api/bulk-translate/admin/list');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const payload = await r.json();
      const items = sortTaskItems(payload.items || []);
      renderStats(payload.stats || summarize(items));
      renderList(items);
    } catch (error) {
      statsBox.innerHTML = '<div class="bt-empty-state bt-empty-state--error"><strong>统计加载失败</strong><span>请稍后刷新页面。</span></div>';
      listBox.innerHTML = `<div class="bt-empty-state bt-empty-state--error"><strong>任务列表加载失败</strong><span>${esc(error.message || error)}</span></div>`;
      emptyBox.classList.add('hidden');
    }
  }

  function renderStats(stats) {
    const cards = [
      ['卡住', stats.stuck || 0, '需要人工处理或重跑'],
      ['进行中', stats.running || 0, '正在排队、派发或执行'],
      ['已完成', stats.done || 0, '已结束的父任务'],
      ['总任务', stats.total || 0, '当前可见任务数量'],
    ];
    statsBox.innerHTML = cards.map(([label, value, hint]) => `
      <div class="bt-admin-stat">
        <span>${esc(label)}</span>
        <strong>${esc(value)}</strong>
        <small>${esc(hint)}</small>
      </div>
    `).join('');
  }

  function renderList(items) {
    emptyBox.classList.toggle('hidden', items.length > 0);
    if (!items.length) {
      listBox.innerHTML = '';
      return;
    }

    listBox.innerHTML = items.map(renderTaskItem).join('');
  }

  function renderTaskItem(task) {
    const progress = task.progress || {};
    const pct = clampPercent(progress.pct);
    const product = task.product || {};
    const productCode = product.product_code ? ` · ${product.product_code}` : '';
    const typeText = (task.content_type_labels || task.content_types || []).join(' / ') || '未记录类型';
    const langText = (task.target_lang_labels || task.target_langs || []).join('、') || '未记录语言';
    const costText = task.group === 'done'
      ? `实际消费 ¥${formatMoney(task.cost_actual)}`
      : `预估 ¥${formatMoney(task.cost_estimate)} / 已花费 ¥${formatMoney(task.cost_actual)}`;
    const intervention = task.intervention_count > 0
      ? `<span class="bt-admin-task-item__alert">${task.intervention_count} 项需要介入</span>`
      : '';

    return `
      <article class="bt-admin-task-item bt-admin-task-item--${esc(task.group || 'running')}">
        <div class="bt-admin-task-item__main">
          <div class="bt-admin-task-item__title">
            <span class="bt-admin-task-item__group">${esc(task.group_label || '任务')}</span>
            <h3>${esc(product.name || `商品 #${task.product_id || '—'}`)}${esc(productCode)}</h3>
          </div>
          <div class="bt-admin-task-item__meta">
            <span>创建人：${esc(task.creator && task.creator.name || '—')}</span>
            <span>任务类型：${esc(typeText)}</span>
            <span>目标语言：${esc(langText)}</span>
            <span>创建时间：${esc(formatDate(task.created_at))}</span>
          </div>
          <div class="bt-admin-task-item__progress">
            <div class="bt-detail__progress" aria-label="任务进度 ${pct}%">
              <div class="bt-detail__progress-fill" style="width:${pct}%"></div>
            </div>
            <strong>${pct}%</strong>
            <span>${esc(progress.completed || 0)}/${esc(progress.total || 0)} 已处理</span>
          </div>
        </div>
        <div class="bt-admin-task-item__side">
          ${intervention}
          <span class="bt-admin-task-item__cost">${esc(costText)}</span>
          <a class="bt-btn bt-btn--primary" href="${esc(task.detail_url || `/tasks/${task.id}`)}">查看详情</a>
        </div>
      </article>
    `;
  }

  function sortTaskItems(items) {
    const order = { stuck: 0, running: 1, done: 2 };
    return [...items].sort((a, b) => (order[a.group] ?? 9) - (order[b.group] ?? 9));
  }

  function summarize(items) {
    return {
      stuck: items.filter(item => item.group === 'stuck').length,
      running: items.filter(item => item.group === 'running').length,
      done: items.filter(item => item.group === 'done').length,
      total: items.length,
    };
  }

  function clampPercent(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(100, Math.round(n)));
  }

  function formatMoney(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return '0';
    return n.toFixed(2).replace(/\.00$/, '');
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString('zh-CN', { hour12: false });
    }
    return String(value).replace('T', ' ');
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  }

  loadTasks();
  window.setInterval(loadTasks, 15000);
})();
