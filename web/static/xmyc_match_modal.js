(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatPrice(value) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(2) : String(value);
  }

  const state = {
    productId: null,
    productName: '',
    productCode: '',
    items: [],
    selected: new Set(),
    onSaved: null,
  };

  function open(opts) {
    state.productId = opts.productId;
    state.productName = opts.productName || '';
    state.productCode = opts.productCode || '';
    state.onSaved = opts.onSaved || null;
    state.selected = new Set();
    state.items = [];
    const mask = $('xmycMatchModalMask');
    if (!mask) {
      console.warn('xmycMatchModalMask not in DOM');
      return;
    }
    if ($('xmycMatchProductName')) $('xmycMatchProductName').textContent = state.productName || '—';
    if ($('xmycMatchProductCode')) $('xmycMatchProductCode').textContent = state.productCode || '';
    if ($('xmycMatchSearch')) $('xmycMatchSearch').value = '';
    if ($('xmycMatchFilter')) $('xmycMatchFilter').value = 'all';
    if ($('xmycMatchSummary')) $('xmycMatchSummary').textContent = '尚未选择';
    mask.hidden = false;
    fetchAndRender();
  }

  function close() {
    const mask = $('xmycMatchModalMask');
    if (mask) mask.hidden = true;
    state.productId = null;
    state.items = [];
    state.selected = new Set();
  }

  async function fetchAndRender() {
    const tbody = $('xmycMatchTbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" class="oc-xmyc-match-empty">加载中…</td></tr>';
    const keyword = ($('xmycMatchSearch') || {}).value || '';
    const matched = ($('xmycMatchFilter') || {}).value || 'all';
    const params = new URLSearchParams();
    if (keyword.trim()) params.set('keyword', keyword.trim());
    params.set('matched', matched);
    params.set('limit', '500');
    try {
      const resp = await fetch('/medias/api/xmyc-skus?' + params.toString(), { credentials: 'same-origin' });
      const body = await resp.json();
      if (!resp.ok || !body.ok) {
        const msg = (body && body.message) || (body && body.error) || ('HTTP ' + resp.status);
        throw new Error(msg);
      }
      state.items = Array.isArray(body.items) ? body.items : [];
      state.selected = new Set();
      for (const it of state.items) {
        if (Number(it.product_id) === Number(state.productId)) {
          state.selected.add(it.sku);
        }
      }
      renderRows();
      updateSummary();
    } catch (err) {
      tbody.innerHTML = '<tr><td colspan="6" class="oc-xmyc-match-empty">加载失败：' + escapeHtml(err.message || '') + '</td></tr>';
    }
  }

  function renderRows() {
    const tbody = $('xmycMatchTbody');
    if (!tbody) return;
    if (!state.items.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="oc-xmyc-match-empty">没有匹配的 SKU</td></tr>';
      return;
    }
    const html = state.items.map((it) => {
      const checked = state.selected.has(it.sku) ? 'checked' : '';
      let owner = '';
      if (it.product_id == null) {
        owner = '<span class="muted">—</span>';
      } else if (Number(it.product_id) === Number(state.productId)) {
        const tag = it.match_type === 'manual' ? '当前产品（手动）' : '当前产品（自动）';
        owner = '<span class="matched-self">' + escapeHtml(tag) + '</span>';
      } else {
        owner = '<span class="matched-other">已属 #' + escapeHtml(String(it.product_id)) +
                (it.product_name ? ' ' + escapeHtml(it.product_name) : '') + '</span>';
      }
      return (
        '<tr data-sku="' + escapeHtml(it.sku) + '">' +
          '<td class="w-checkbox"><input type="checkbox" data-xmyc-sku="' + escapeHtml(it.sku) + '" ' + checked + '></td>' +
          '<td><div><strong>' + escapeHtml(it.sku || '') + '</strong></div>' +
            '<div class="muted" style="font-size:11px">' + escapeHtml(it.sku_code || '') + '</div></td>' +
          '<td>' + escapeHtml(it.goods_name || '') + '</td>' +
          '<td class="ta-r">' + formatPrice(it.unit_price) + '</td>' +
          '<td class="ta-r">' + escapeHtml(String(it.stock_available ?? '—')) + '</td>' +
          '<td>' + owner + '</td>' +
        '</tr>'
      );
    }).join('');
    tbody.innerHTML = html;
    tbody.querySelectorAll('input[type="checkbox"][data-xmyc-sku]').forEach((cb) => {
      cb.addEventListener('change', () => {
        const sku = cb.getAttribute('data-xmyc-sku');
        if (cb.checked) state.selected.add(sku);
        else state.selected.delete(sku);
        updateSummary();
      });
    });
  }

  function updateSummary() {
    const el = $('xmycMatchSummary');
    if (!el) return;
    const n = state.selected.size;
    if (n === 0) { el.textContent = '尚未选择'; return; }
    const prices = [];
    for (const it of state.items) {
      if (state.selected.has(it.sku)) {
        const p = Number(it.unit_price);
        if (Number.isFinite(p)) prices.push(p);
      }
    }
    if (!prices.length) {
      el.textContent = '已选 ' + n + ' 个 SKU（无单价数据）';
    } else {
      const min = Math.min.apply(null, prices).toFixed(2);
      const max = Math.max.apply(null, prices).toFixed(2);
      el.textContent = '已选 ' + n + ' 个 SKU · 单价 ' + min + (min === max ? '' : ' ~ ' + max) + ' RMB';
    }
  }

  async function save() {
    if (!state.productId) return;
    const btn = $('xmycMatchSaveBtn');
    if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
    try {
      const resp = await fetch('/medias/api/products/' + encodeURIComponent(state.productId) + '/xmyc-skus', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ skus: Array.from(state.selected) }),
      });
      const body = await resp.json();
      if (!resp.ok || !body.ok) {
        const msg = (body && body.message) || (body && body.error) || ('HTTP ' + resp.status);
        throw new Error(msg);
      }
      const onSaved = state.onSaved;
      const newPrice = body.purchase_price;
      close();
      if (onSaved) {
        try { onSaved({ purchasePrice: newPrice, attached: body.attached, cleared: body.cleared }); }
        catch (e) { console.error('xmyc onSaved callback failed', e); }
      }
    } catch (err) {
      alert('保存匹配失败：' + (err.message || err));
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '保存匹配'; }
    }
  }

  function bindOnce() {
    if (window._xmycMatchModalBound) return;
    window._xmycMatchModalBound = true;
    if ($('xmycMatchCloseBtn')) $('xmycMatchCloseBtn').addEventListener('click', close);
    if ($('xmycMatchCancelBtn')) $('xmycMatchCancelBtn').addEventListener('click', close);
    if ($('xmycMatchSaveBtn')) $('xmycMatchSaveBtn').addEventListener('click', save);
    if ($('xmycMatchRefreshBtn')) $('xmycMatchRefreshBtn').addEventListener('click', fetchAndRender);
    if ($('xmycMatchSearch')) {
      let t = null;
      $('xmycMatchSearch').addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(fetchAndRender, 350);
      });
    }
    if ($('xmycMatchFilter')) $('xmycMatchFilter').addEventListener('change', fetchAndRender);
    const mask = $('xmycMatchModalMask');
    if (mask) mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && mask && !mask.hidden) close();
    });
  }

  document.addEventListener('DOMContentLoaded', bindOnce);
  if (document.readyState !== 'loading') bindOnce();

  window.XmycMatchModal = { open, close };
})();
