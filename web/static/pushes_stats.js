(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const fromInput = $('stats-from');
  const toInput = $('stats-to');
  const tbody = $('stats-tbody');
  const tfoot = $('stats-tfoot');
  const errorBox = $('stats-error');
  const applyBtn = $('stats-apply');
  const resetBtn = $('stats-reset');
  const quickButtons = document.querySelectorAll('.stats-quick-row button[data-range]');

  // ---------- 日期工具 ----------
  function pad(n) { return String(n).padStart(2, '0'); }
  function fmt(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function todayDate() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }
  function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }

  function startOfWeek(d) {
    // 中国习惯：周一为一周的第一天
    const r = new Date(d);
    const day = r.getDay(); // 0=Sun ... 6=Sat
    const offset = day === 0 ? 6 : day - 1;
    r.setDate(r.getDate() - offset);
    r.setHours(0, 0, 0, 0);
    return r;
  }
  function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
  function endOfMonth(d) { return new Date(d.getFullYear(), d.getMonth() + 1, 0); }

  function rangeFor(key) {
    const today = todayDate();
    switch (key) {
      case 'today':       return [today, today];
      case 'yesterday':   { const y = addDays(today, -1); return [y, y]; }
      case 'this-week':   return [startOfWeek(today), today];
      case 'last-week':   { const lwStart = addDays(startOfWeek(today), -7); return [lwStart, addDays(lwStart, 6)]; }
      case 'this-month':  return [startOfMonth(today), today];
      case 'last-month':  { const lm = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                            return [lm, endOfMonth(lm)]; }
      default:            return [startOfMonth(today), today];
    }
  }

  function setRange(key) {
    const [from, to] = rangeFor(key);
    fromInput.value = fmt(from);
    toInput.value = fmt(to);
    quickButtons.forEach((b) => {
      b.classList.toggle('is-active', b.dataset.range === key);
    });
  }

  // ---------- 渲染 ----------
  function clearTbody() { tbody.innerHTML = ''; }
  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
  }
  function clearError() {
    errorBox.textContent = '';
    errorBox.hidden = true;
  }
  function renderLoading() {
    clearError();
    tfoot.hidden = true;
    tbody.innerHTML = '<tr class="stats-row-loading"><td colspan="5">加载中…</td></tr>';
  }
  function renderEmpty() {
    tfoot.hidden = true;
    tbody.innerHTML = '<tr class="stats-row-empty"><td colspan="5">该区间内暂无提交记录，试试其他时间范围</td></tr>';
  }

  function fmtRate(rate) {
    if (rate === null || rate === undefined) return '—';
    return (rate * 100).toFixed(1) + '%';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderRows(rows) {
    clearTbody();
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(r.name)}</td>
        <td class="num">${r.submitted}</td>
        <td class="num">${r.pushed}</td>
        <td class="num">${r.unpushed}</td>
        <td class="num">${fmtRate(r.push_rate)}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderTotals(t) {
    $('stats-total-submitted').textContent = t.submitted;
    $('stats-total-pushed').textContent = t.pushed;
    $('stats-total-unpushed').textContent = t.unpushed;
    $('stats-total-rate').textContent = fmtRate(t.push_rate);
    tfoot.hidden = false;
  }

  // ---------- 拉数据 ----------
  async function fetchStats() {
    const df = (fromInput.value || '').trim();
    const dt = (toInput.value || '').trim();
    if (df && dt && df > dt) {
      showError('起始日期不能晚于截止日期');
      tbody.innerHTML = '';
      tfoot.hidden = true;
      return;
    }
    renderLoading();
    const params = new URLSearchParams();
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    const url = '/pushes/api/stats' + (params.toString() ? `?${params}` : '');
    try {
      const resp = await fetch(url, { credentials: 'same-origin' });
      if (!resp.ok) {
        let detail;
        try { detail = (await resp.json()).detail || resp.statusText; }
        catch (_) { detail = resp.statusText; }
        showError(`加载失败：${detail}`);
        clearTbody();
        return;
      }
      const data = await resp.json();
      if (!data.rows || data.rows.length === 0) {
        renderEmpty();
        return;
      }
      renderRows(data.rows);
      renderTotals(data.totals);
    } catch (err) {
      showError(`网络错误：${err && err.message ? err.message : err}`);
      clearTbody();
    }
  }

  // ---------- 事件绑定 ----------
  quickButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      setRange(btn.dataset.range);
      fetchStats();
    });
  });
  applyBtn.addEventListener('click', () => {
    quickButtons.forEach((b) => b.classList.remove('is-active'));
    fetchStats();
  });
  resetBtn.addEventListener('click', () => {
    setRange('this-month');
    fetchStats();
  });

  // URL query 优先；否则默认本月
  (function init() {
    const params = new URLSearchParams(location.search);
    const df = params.get('date_from');
    const dt = params.get('date_to');
    if (df && dt) {
      fromInput.value = df;
      toInput.value = dt;
    } else {
      setRange('this-month');
    }
    fetchStats();
  })();
})();
