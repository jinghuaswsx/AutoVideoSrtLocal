/* bulk_translate 前端交互 · 弹窗 + 气泡 + SocketIO 进度。
   设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md */
(function () {
  'use strict';

  // 目标语言固定列表(本期 media_languages 启用的集合)
  const ALL_LANGS = [
    { code: 'de', label: '🇩🇪 德语' },
    { code: 'fr', label: '🇫🇷 法语' },
    { code: 'es', label: '🇪🇸 西班牙语' },
    { code: 'it', label: '🇮🇹 意大利语' },
    { code: 'ja', label: '🇯🇵 日语' },
    { code: 'pt', label: '🇵🇹 葡萄牙语' },
    { code: 'nl', label: '🇳🇱 荷兰语' },
    { code: 'sv', label: '🇸🇪 瑞典语' },
    { code: 'fi', label: '🇫🇮 芬兰语' },
  ];

  let dialog;
  let bubble;
  let currentCtx = null;
  const activeTasks = new Map();  // task_id -> { productName, status, progress, cost_actual }
  let estimateTimer = null;

  // =========================
  // 弹窗
  // =========================
  function initDialog() {
    dialog = document.getElementById('bt-dialog');
    if (!dialog) return;

    dialog.querySelectorAll('[data-bt-close]').forEach(el => {
      el.addEventListener('click', closeDialog);
    });
    dialog.querySelector('[data-bt-start]').addEventListener('click', onStart);
    dialog.querySelectorAll('[data-bt-content], [data-bt-force]').forEach(el => {
      el.addEventListener('change', scheduleEstimate);
    });
  }

  function openDialog(ctx) {
    if (!dialog) return;
    currentCtx = ctx;
    dialog.querySelector('[data-bt-product-name]').textContent = ctx.productName;

    // 渲染目标语言 chips
    const box = dialog.querySelector('[data-bt-langs]');
    const singleMode = ctx.mode === 'single-lang';
    box.innerHTML = '';
    if (singleMode) {
      const span = document.createElement('span');
      span.className = 'bt-badge bt-badge--primary';
      span.textContent = flagOf(ctx.fixedLang) + ' (固定)';
      box.appendChild(span);
      box.dataset.fixedLang = ctx.fixedLang;
    } else {
      delete box.dataset.fixedLang;
      ALL_LANGS.forEach(l => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'bt-chip bt-chip--active';
        chip.dataset.lang = l.code;
        chip.textContent = l.label;
        chip.addEventListener('click', () => {
          chip.classList.toggle('bt-chip--active');
          scheduleEstimate();
        });
        box.appendChild(chip);
      });
    }

    // 勾选重置为默认(copy + detail + video,cover 不勾)
    dialog.querySelectorAll('[data-bt-content]').forEach(el => {
      el.checked = ['copy', 'detail', 'video'].includes(el.dataset.btContent);
    });
    dialog.querySelector('[data-bt-force]').checked = false;

    dialog.classList.remove('hidden');
    scheduleEstimate();
  }

  function closeDialog() {
    if (!dialog) return;
    dialog.classList.add('hidden');
    currentCtx = null;
  }

  function collectForm() {
    const ctx = currentCtx;
    const box = dialog.querySelector('[data-bt-langs]');
    let targetLangs;
    if (box.dataset.fixedLang) {
      targetLangs = [box.dataset.fixedLang];
    } else {
      targetLangs = Array.from(box.querySelectorAll('.bt-chip--active'))
        .map(c => c.dataset.lang);
    }
    const contentTypes = Array.from(
      dialog.querySelectorAll('[data-bt-content]:checked'),
    ).map(c => c.dataset.btContent);
    const force = dialog.querySelector('[data-bt-force]').checked;
    return {
      product_id: ctx.productId,
      target_langs: targetLangs,
      content_types: contentTypes,
      force_retranslate: force,
    };
  }

  function scheduleEstimate() {
    if (estimateTimer) clearTimeout(estimateTimer);
    estimateTimer = setTimeout(doEstimate, 300);
  }

  async function doEstimate() {
    const box = dialog.querySelector('[data-bt-estimate] .bt-estimate__body');
    const form = collectForm();

    // 本地校验
    if (!form.target_langs.length) {
      box.innerHTML = '<span class="bt-warn">⚠️ 请至少选择一个目标语言</span>';
      return;
    }
    if (!form.content_types.length) {
      box.innerHTML = '<span class="bt-warn">⚠️ 请至少选择一种内容类型</span>';
      return;
    }

    box.innerHTML = '计算中…';
    try {
      const resp = await fetch('/api/bulk-translate/estimate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        box.innerHTML = `<span class="bt-warn">预估失败: ${err.error || resp.status}</span>`;
        return;
      }
      const d = await resp.json();
      const sk = d.skipped || {};
      const anySkip = sk.copy || sk.cover || sk.detail || sk.video;

      // 视频 × 非 de/fr 警告
      let warn = '';
      const nonDeFr = form.target_langs.filter(l => l !== 'de' && l !== 'fr');
      if (form.content_types.includes('video') && nonDeFr.length) {
        warn = `<div class="bt-warn">⚠️ 视频翻译仅支持德/法,${nonDeFr.join(',')} 会自动跳过</div>`;
      }

      box.innerHTML = warn + `
        文案 tokens: ${(d.copy_tokens || 0).toLocaleString()}<br>
        图片张数: ${d.image_count || 0}<br>
        视频分钟: ${d.video_minutes || 0}<br>
        ${anySkip ? `<span style="color:oklch(48% 0.018 230)">跳过(已翻译): 文案 ${sk.copy || 0} · 详情图 ${sk.detail || 0} · 视频 ${sk.video || 0}</span><br>` : ''}
        <strong>预估费用 ≈ ¥${d.estimated_cost_cny || 0}</strong>
      `;
    } catch (e) {
      box.innerHTML = `<span class="bt-warn">网络错误: ${e.message}</span>`;
    }
  }

  async function onStart() {
    const btn = dialog.querySelector('[data-bt-start]');
    const form = collectForm();
    if (!form.target_langs.length || !form.content_types.length) {
      alert('请先选择目标语言和内容类型');
      return;
    }

    // 预估+二次确认
    let est;
    try {
      const r = await fetch('/api/bulk-translate/estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      est = await r.json();
    } catch (e) {
      if (!confirm('预估失败,仍要启动任务吗?')) return;
    }
    const cost = est ? est.estimated_cost_cny : '未知';
    if (!confirm(`将创建翻译任务,预估费用 ¥${cost}。确认开始?`)) return;

    btn.disabled = true;
    btn.querySelector('.bt-btn__spinner').classList.remove('hidden');
    btn.querySelector('.bt-btn__text').textContent = '创建任务中…';

    try {
      const createResp = await fetch('/api/bulk-translate/create', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, video_params: {} }),
      });
      if (!createResp.ok) throw new Error((await createResp.json()).error || 'create failed');
      const { task_id } = await createResp.json();

      const startResp = await fetch(`/api/bulk-translate/${task_id}/start`, {
        method: 'POST',
      });
      if (!startResp.ok) throw new Error((await startResp.json()).error || 'start failed');

      // 注册到气泡
      activeTasks.set(task_id, {
        productName: currentCtx.productName,
        status: 'running',
        progress: { total: 1, done: 0, skipped: 0, failed: 0, running: 1, pending: 0 },
      });
      renderBubble();
      closeDialog();
      btn.querySelector('.bt-btn__spinner').classList.add('hidden');
      btn.querySelector('.bt-btn__text').textContent = '▶ 开始翻译';
      btn.disabled = false;

      // 加入 socket.io 房间以收到进度推送
      if (window._btSocket) window._btSocket.emit('join', { room: task_id });
    } catch (e) {
      alert('启动失败: ' + e.message);
      btn.disabled = false;
      btn.querySelector('.bt-btn__spinner').classList.add('hidden');
      btn.querySelector('.bt-btn__text').textContent = '▶ 开始翻译';
    }
  }

  // =========================
  // 气泡
  // =========================
  function initBubble() {
    bubble = document.getElementById('bt-bubble');
    if (!bubble) return;
    bubble.querySelector('[data-bt-bubble-compact]').addEventListener('click', () => toggleBubble(true));
    bubble.querySelector('[data-bt-bubble-min]').addEventListener('click', () => toggleBubble(false));
  }

  function toggleBubble(expand) {
    bubble.querySelector('[data-bt-bubble-compact]').classList.toggle('hidden', expand);
    bubble.querySelector('[data-bt-bubble-expanded]').classList.toggle('hidden', !expand);
  }

  function renderBubble() {
    if (!bubble) return;
    if (activeTasks.size === 0) {
      bubble.classList.add('hidden');
      return;
    }
    bubble.classList.remove('hidden');
    bubble.classList.remove('bt-bubble--error');

    let total = 0, done = 0, hasError = false;
    activeTasks.forEach(t => {
      const p = t.progress || {};
      total += p.total || 0;
      done += (p.done || 0) + (p.skipped || 0);
      if (t.status === 'error') hasError = true;
    });
    if (hasError) bubble.classList.add('bt-bubble--error');

    const pct = total > 0 ? Math.round(100 * done / total) : 0;
    bubble.querySelector('[data-bt-bubble-summary]').textContent =
      `${activeTasks.size} 个任务 · ${pct}%`;

    const list = bubble.querySelector('[data-bt-bubble-list]');
    list.innerHTML = Array.from(activeTasks.entries()).map(([tid, t]) => {
      const p = t.progress || { total: 0, done: 0, skipped: 0 };
      const d = (p.done || 0) + (p.skipped || 0);
      const bar = barOf(d, p.total);
      const statusBadge = t.status === 'error' ? ' <span style="color:oklch(58% 0.18 25)">失败</span>' :
                         t.status === 'done' ? ' <span style="color:oklch(38% 0.09 165)">✓ 完成</span>' : '';
      return `
        <div class="bt-bubble-task">
          <div class="bt-bubble-task__name">📦 ${escapeHtml(t.productName || '任务 ' + tid.slice(0, 8))}${statusBadge}</div>
          <div class="bt-bubble-task__progress">${bar} ${d}/${p.total || 0}</div>
          <a class="bt-bubble-task__link" href="/tasks/${tid}">查看详情 →</a>
        </div>
      `;
    }).join('');
  }

  function barOf(done, total) {
    total = Math.max(total, 1);
    const filled = Math.round(10 * done / total);
    return '■'.repeat(Math.min(filled, 10)) + '□'.repeat(Math.max(10 - filled, 0));
  }

  // =========================
  // SocketIO 订阅
  // =========================
  function initSocket() {
    if (!window.io) return;
    const socket = window.io();
    window._btSocket = socket;

    socket.on('bulk_translate_progress', p => {
      const t = activeTasks.get(p.task_id);
      if (t) {
        t.progress = p.progress;
        t.status = p.status;
        renderBubble();
      }
      // 同时触发详情页的更新 hook
      window.dispatchEvent(new CustomEvent('bt-progress', { detail: p }));
    });

    socket.on('bulk_translate_done', p => {
      const t = activeTasks.get(p.task_id);
      if (t) {
        t.progress = p.progress;
        t.status = p.status;
        renderBubble();
        // 10s 后自动从气泡清除
        setTimeout(() => {
          activeTasks.delete(p.task_id);
          renderBubble();
        }, 10000);
      }
      window.dispatchEvent(new CustomEvent('bt-done', { detail: p }));
    });
  }

  // 页面加载时,补齐活跃任务到气泡(仅展示,不触发调度)
  async function hydrateActiveTasks() {
    try {
      const r = await fetch('/api/bulk-translate/list?status=running');
      if (!r.ok) return;
      const rows = await r.json();
      rows.forEach(row => {
        if (!activeTasks.has(row.id)) {
          activeTasks.set(row.id, {
            productName: `产品 #${row.product_id}`,
            status: row.status,
            progress: row.progress || {},
          });
        }
      });
      // 同时加载 error 任务(用户可能要处理)
      const r2 = await fetch('/api/bulk-translate/list?status=error');
      if (r2.ok) {
        const errRows = await r2.json();
        errRows.forEach(row => {
          if (!activeTasks.has(row.id)) {
            activeTasks.set(row.id, {
              productName: `产品 #${row.product_id}`,
              status: 'error',
              progress: row.progress || {},
            });
          }
        });
      }
      renderBubble();
    } catch (e) { /* ignore */ }
  }

  // =========================
  // 工具
  // =========================
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, m => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
  }
  function flagOf(code) {
      const m = { de: '🇩🇪 德语', fr: '🇫🇷 法语', es: '🇪🇸 西班牙语',
                   it: '🇮🇹 意大利语', ja: '🇯🇵 日语', pt: '🇵🇹 葡萄牙语',
                   nl: '🇳🇱 荷兰语', sv: '🇸🇪 瑞典语', fi: '🇫🇮 芬兰语' };
    return m[code] || code;
  }

  // =========================
  // Init
  // =========================
  window.BulkTranslate = {
    open: openDialog,
    close: closeDialog,
    registerTask(tid, ctx) {
      activeTasks.set(tid, { productName: ctx.productName, status: 'running', progress: {} });
      renderBubble();
    },
  };

  document.addEventListener('DOMContentLoaded', () => {
    initDialog();
    initBubble();
    initSocket();
    hydrateActiveTasks();
  });
})();
