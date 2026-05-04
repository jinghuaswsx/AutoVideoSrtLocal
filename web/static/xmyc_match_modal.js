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
    tbody.innerHTML = '<tr><td colspan="9" class="oc-xmyc-match-empty">加载中…</td></tr>';
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
      tbody.innerHTML = '<tr><td colspan="9" class="oc-xmyc-match-empty">加载失败：' + escapeHtml(err.message || '') + '</td></tr>';
    }
  }

  function formatRoas(value) {
    if (value === null || value === undefined) return '—';
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(2) : '无法保本';
  }

  function editableCell(value, field, skuId) {
    const display = formatPrice(value);
    return (
      '<td class="ta-r editable-cell" data-field="' + escapeHtml(field) +
      '" data-sku-id="' + escapeHtml(String(skuId)) + '" title="点击编辑">' +
      display + '</td>'
    );
  }

  function startEdit(cell) {
    if (cell.classList.contains('editing')) return;
    const skuId = cell.getAttribute('data-sku-id');
    const field = cell.getAttribute('data-field');
    if (!skuId || !field) return;
    const original = cell.textContent.trim();
    const val = original === '—' ? '' : original;
    cell.classList.add('editing');
    cell.innerHTML = (
      '<input type="number" min="0" step="0.01" value="' + escapeHtml(val) + '" ' +
      'class="oc-xmyc-edit-input" data-original="' + escapeHtml(val) + '">'
    );
    const input = cell.querySelector('input');
    if (!input) return;
    input.focus();
    input.select();

    function cancel() {
      cleanup();
      cell.textContent = original;
      cell.classList.remove('editing', 'saving', 'saved');
    }

    async function commit() {
      const raw = input.value.trim();
      if (raw === (input.getAttribute('data-original') || '')) { cancel(); return; }
      cleanup();
      cell.classList.add('saving');
      cell.innerHTML = '<span class="oc-xmyc-saving">…</span>';
      try {
        const body = {};
        body[field] = raw === '' ? null : raw;
        const resp = await fetch('/medias/api/xmyc-skus/' + encodeURIComponent(skuId), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        });
        const json = await resp.json();
        if (!resp.ok || !json.ok) {
          throw new Error((json && json.message) || ('HTTP ' + resp.status));
        }
        const item = json.item;
        const newVal = item ? item[field] : null;
        cell.textContent = formatPrice(newVal);
        cell.classList.remove('saving');
        cell.classList.add('saved');
      } catch (err) {
        cell.textContent = original;
        cell.classList.remove('saving');
        cell.classList.add('saved');
        console.error('xmyc inline edit failed', err);
      }
      cell.classList.remove('editing');
    }

    function cleanup() {
      input.removeEventListener('blur', onBlur);
      input.removeEventListener('keydown', onKey);
    }

    function onBlur() { commit(); }
    function onKey(e) {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    }

    input.addEventListener('blur', onBlur);
    input.addEventListener('keydown', onKey);
  }

  function bindRowEditing(tr) {
    tr.querySelectorAll('.editable-cell').forEach((cell) => {
      cell.addEventListener('click', () => startEdit(cell));
    });
  }

  function renderRows() {
    const tbody = $('xmycMatchTbody');
    if (!tbody) return;
    if (!state.items.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="oc-xmyc-match-empty">没有匹配的 SKU</td></tr>';
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
      const roas = it.roas || {};
      var roasClass = '';
      if (!roas.can_compute) {
        roasClass = ' row-roas-missing';
      } else if (Number(roas.effective_roas) <= 0) {
        roasClass = ' row-roas-danger';
      }
      const roasCell = roas.can_compute
        ? (Number(roas.effective_roas) <= 0
            ? '<strong class="roas-negative">无法保本</strong>'
            : '<strong>' + formatRoas(roas.effective_roas) + '</strong>')
        : '<span class="muted" title="缺少售价/采购价/物流费 中的一项">—</span>';
      return (
        '<tr data-sku="' + escapeHtml(it.sku) + '" class="' + roasClass + '">' +
          '<td class="w-checkbox"><input type="checkbox" data-xmyc-sku="' + escapeHtml(it.sku) + '" ' + checked + '></td>' +
          '<td><div><strong>' + escapeHtml(it.sku || '') + '</strong></div>' +
            '<div class="muted" style="font-size:11px">' + escapeHtml(it.sku_code || '') + '</div></td>' +
          '<td>' + escapeHtml(it.goods_name || '') + '</td>' +
          '<td class="ta-r">' + formatPrice(it.unit_price) + '</td>' +
          editableCell(it.standalone_price_sku, 'standalone_price_sku', it.id) +
          editableCell(it.standalone_shipping_fee_sku, 'standalone_shipping_fee_sku', it.id) +
          editableCell(it.packet_cost_actual_sku, 'packet_cost_actual_sku', it.id) +
          '<td class="ta-r">' + roasCell + '</td>' +
          '<td>' + owner + '</td>' +
        '</tr>'
      );
    }).join('');
    tbody.innerHTML = html;
    tbody.querySelectorAll('tr[data-sku]').forEach(bindRowEditing);
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
