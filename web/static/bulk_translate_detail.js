/* /tasks/<id> 任务详情页交互。 */
(function () {
  'use strict';

  const root = document.querySelector('.bt-detail');
  if (!root) return;
  const taskId = root.dataset.taskId;
  let currentTask = null;

  async function loadTask() {
    try {
      const r = await fetch(`/api/bulk-translate/${taskId}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        root.querySelector('[data-bt-meta]').innerHTML =
          `<span class="bt-warn">加载失败: ${err.error || r.status}</span>`;
        return;
      }
      currentTask = await r.json();
      render();
    } catch (e) {
      console.error(e);
    }
  }

  function render() {
    const t = currentTask;
    const s = t.state;
    const ini = s.initiator || {};

    // Meta
    root.querySelector('[data-bt-meta]').innerHTML = `
      <span>📅 <strong>${t.created_at || '—'}</strong></span>
      <span>🧑 <strong>${esc(ini.user_name || ini.user_id || '—')}</strong></span>
      <span>🌐 IP: <strong>${esc(ini.ip || '—')}</strong></span>
      <span>🆔 <code>${esc(t.id.slice(0, 8))}</code></span>
      <span>🏷️ 状态: <strong style="color:${statusColor(t.status)}">${statusLabel(t.status)}</strong></span>
    `;

    // Summary
    const p = s.progress || {};
    const total = p.total || 1;
    const done = (p.done || 0) + (p.skipped || 0);
    const pct = Math.round(100 * done / total);
    root.querySelector('.bt-detail__progress-fill').style.width = pct + '%';
    const cost = s.cost_tracking || { estimate: {}, actual: {} };
    root.querySelector('[data-bt-stats]').innerHTML = `
      <span><strong>${pct}%</strong> (${done}/${total})</span>
      <span>✅ <strong>${p.done || 0}</strong> 完成</span>
      <span>🔄 <strong>${p.running || 0}</strong> 运行</span>
      <span>❌ <strong>${p.failed || 0}</strong> 失败</span>
      <span>⏩ <strong>${p.skipped || 0}</strong> 跳过</span>
      <span>⏳ <strong>${p.pending || 0}</strong> 待跑</span>
      <span>💰 预估 <strong>¥${cost.estimate.estimated_cost_cny || 0}</strong> / 实际 <strong>¥${cost.actual.actual_cost_cny || 0}</strong></span>
    `;

    // Actions
    renderActions(t.status);

    // Plan grouped by lang
    renderPlan(s.plan || []);

    // Audit events
    renderAudit(s.audit_events || []);
  }

  function renderActions(status) {
    const box = root.querySelector('[data-bt-actions]');
    const btns = [];
    if (status === 'error' || status === 'paused') {
      btns.push(`<button class="bt-btn bt-btn--primary" data-act="resume">▶ 继续执行</button>`);
    }
    if (status === 'running') {
      btns.push(`<button class="bt-btn bt-btn--ghost" data-act="pause">⏸ 暂停</button>`);
    }
    if (status === 'error') {
      btns.push(`<button class="bt-btn bt-btn--ghost" data-act="retry-failed">🔁 重跑所有失败项</button>`);
    }
    if (status === 'running' || status === 'paused') {
      btns.push(`<button class="bt-btn bt-btn--danger" data-act="cancel">🚫 取消</button>`);
    }
    box.innerHTML = btns.join('');
    box.querySelectorAll('[data-act]').forEach(b => {
      b.addEventListener('click', () => doAction(b.dataset.act));
    });
  }

  async function doAction(action) {
    let url, label;
    if (action === 'resume') { url = `/api/bulk-translate/${taskId}/resume`; label = '继续执行'; }
    else if (action === 'pause') { url = `/api/bulk-translate/${taskId}/pause`; label = '暂停'; }
    else if (action === 'cancel') {
      if (!confirm('确认取消任务?已完成的子任务结果会保留。')) return;
      url = `/api/bulk-translate/${taskId}/cancel`; label = '取消';
    }
    else if (action === 'retry-failed') {
      if (!confirm('把所有失败项重置为 pending 并重跑?')) return;
      url = `/api/bulk-translate/${taskId}/retry-failed`; label = '重跑失败';
    }
    else return;

    try {
      const r = await fetch(url, { method: 'POST' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(`${label}失败: ${err.error || r.status}`);
        return;
      }
      await loadTask();
    } catch (e) { alert('网络错误: ' + e.message); }
  }

  function renderPlan(plan) {
    const groups = {};
    plan.forEach(item => {
      groups[item.lang] = groups[item.lang] || [];
      groups[item.lang].push(item);
    });
    const langs = Object.keys(groups).sort();
    const box = root.querySelector('[data-bt-plan]');
    if (!langs.length) { box.innerHTML = '<p style="color:var(--fg-muted,#64748b)">本任务没有任何 plan 项。</p>'; return; }

    box.innerHTML = langs.map(lang => {
      const items = groups[lang];
      const doneCnt = items.filter(i => ['done', 'skipped'].includes(i.status)).length;
      const total = items.length;
      return `
        <div class="bt-lang-group">
          <div class="bt-lang-group__header">
            <span>${flagOf(lang)}</span>
            <span style="font-family:monospace;color:var(--fg-muted,#64748b)">${doneCnt}/${total}</span>
          </div>
          ${items.map(it => renderItem(it)).join('')}
        </div>
      `;
    }).join('');

    // Bind retry-item buttons
    box.querySelectorAll('[data-retry-idx]').forEach(b => {
      b.addEventListener('click', async () => {
        const idx = parseInt(b.dataset.retryIdx, 10);
        if (!confirm(`重跑 #${idx} 这一项?`)) return;
        const r = await fetch(`/api/bulk-translate/${taskId}/retry-item`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ idx }),
        });
        if (!r.ok) { const e = await r.json().catch(() => ({})); alert('失败: ' + (e.error || r.status)); return; }
        await loadTask();
      });
    });
  }

  function renderItem(it) {
    const sub = it.sub_task_id ? `<small>sub=${it.sub_task_id.slice(0, 8)}</small>` : '';
    const err = it.error ? `<span class="bt-plan-item__error">· ${esc(it.error)}</span>` : '';
    const retry = (it.status === 'error' || it.status === 'done')
      ? `<button class="bt-btn bt-btn--ghost" style="height:24px;padding:0 10px;font-size:12px" data-retry-idx="${it.idx}">🔁 重跑</button>`
      : '';
    return `
      <div class="bt-plan-item">
        <span class="bt-plan-item__status bt-plan-item__status--${it.status}">${statusLabel(it.status)}</span>
        <span class="bt-plan-item__kind">
          <strong>${kindLabel(it.kind)}</strong> ${refHint(it)} ${sub} ${err}
        </span>
        ${retry}
      </div>
    `;
  }

  function refHint(it) {
    const r = it.ref || {};
    if (r.source_copy_id) return `<small>#${r.source_copy_id}</small>`;
    if (r.source_item_id) return `<small>#${r.source_item_id}</small>`;
    if (r.source_detail_ids) return `<small>${r.source_detail_ids.length} 张详情图</small>`;
    if (r.source_cover_ids) return `<small>${r.source_cover_ids.length} 张主图</small>`;
    return '';
  }

  function renderAudit(events) {
    const box = root.querySelector('[data-bt-audit]');
    if (!events.length) { box.innerHTML = '无'; return; }
    box.innerHTML = events.slice().reverse().map(e => `
      <div class="bt-audit-event">
        [${e.ts}] 用户 ${e.user_id} · <strong>${e.action}</strong>
        ${e.detail && Object.keys(e.detail).length ? '· ' + esc(JSON.stringify(e.detail)) : ''}
      </div>
    `).join('');
  }

  function statusLabel(s) {
    return {
      planning: '待启动', running: '运行中', paused: '已暂停',
      done: '✅ 完成', error: '❌ 失败', cancelled: '已取消',
      skipped: '⏩ 跳过', pending: '待执行',
    }[s] || s;
  }
  function statusColor(s) {
    return {
      running: 'oklch(56% 0.16 230)', done: 'oklch(38% 0.09 165)',
      error: 'oklch(58% 0.18 25)', paused: 'oklch(48% 0.018 230)',
      cancelled: 'oklch(48% 0.018 230)',
    }[s] || '';
  }
  function kindLabel(k) {
    return { copy: '文案', cover: '商品主图', detail: '详情图(批量)', video: '视频' }[k] || k;
  }
  function flagOf(code) {
    return { de: '🇩🇪 德语', fr: '🇫🇷 法语', es: '🇪🇸 西班牙语',
             it: '🇮🇹 意大利语', ja: '🇯🇵 日语', pt: '🇵🇹 葡萄牙语' }[code] || code;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  }

  // SocketIO 推送时同步刷新
  window.addEventListener('bt-progress', e => { if (e.detail.task_id === taskId) loadTask(); });
  window.addEventListener('bt-done', e => { if (e.detail.task_id === taskId) loadTask(); });

  loadTask();
})();
