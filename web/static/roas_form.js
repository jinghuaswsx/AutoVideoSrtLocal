(function () {
  'use strict';

  const ROAS_FIELDS = [
    'purchase_1688_url',
    'purchase_price',
    'packet_cost_estimated',
    'packet_cost_actual',
    'package_length_cm',
    'package_width_cm',
    'package_height_cm',
    'tk_sea_cost',
    'tk_air_cost',
    'tk_sale_price',
    'standalone_price',
    'standalone_shipping_fee',
  ];
  const DEBOUNCE_MS = 600;
  const FEE_RATE = 0.1;

  function numberOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function currentRmbPerUsd() {
    const parsed = Number(window.MATERIAL_ROAS_RMB_PER_USD || 6.83);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 6.83;
  }

  function formatRoas(value) {
    if (value === null || value === undefined || !Number.isFinite(value)) return '—';
    return Number(value).toFixed(2);
  }

  function formatTime(d) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  class RoasFormController {
    constructor(rootEl, opts) {
      if (!rootEl) throw new Error('RoasFormController: rootEl required');
      this.root = rootEl;
      this.productId = (opts && opts.productId) || null;
      this.statusBarEl = (opts && opts.statusBarEl) || null;
      this.onAfterSave = (opts && opts.onAfterSave) || null;
      this._debounceTimer = null;
      this._inFlight = false;
      this._pendingPayload = null;
      this._setStatus('idle');
      this.bind();
    }

    bind() {
      ROAS_FIELDS.forEach((field) => {
        const input = this.root.querySelector(`[data-roas-field="${field}"]`);
        if (!input) return;
        input.addEventListener('input', () => {
          this.renderResult();
          this._scheduleAutoSave();
        });
      });
      const calcBtn = this.root.querySelector('#roasCalculateBtn');
      if (calcBtn) {
        calcBtn.addEventListener('click', () => {
          this.renderResult();
          this.save({ immediate: true });
        });
      }
      const avgInput = this.root.querySelector('#roasAverageShippingInput');
      // roasAverageShippingTool is exported by medias.js — present in the modal context
      // (medias_list.html loads both scripts). On the standalone page, medias.js is not
      // loaded; this branch silently no-ops, which is the intended behavior.
      if (avgInput && window.roasAverageShippingTool) {
        avgInput.addEventListener('input', window.roasAverageShippingTool.updateView);
      }
      const retry = this.statusBarEl && this.statusBarEl.querySelector('.oc-roas-status-retry');
      if (retry) {
        retry.addEventListener('click', () => this.save({ immediate: true }));
      }
      const suggestBtn = this.root.querySelector('#roasParcelSuggestBtn');
      if (suggestBtn) {
        suggestBtn.addEventListener('click', () => this._fetchParcelCostSuggestion());
      }
      const xmycBtn = this.root.querySelector('#xmycMatchOpenBtn');
      if (xmycBtn) {
        xmycBtn.addEventListener('click', () => this._openXmycMatch());
      }
    }

    _openXmycMatch() {
      if (!window.XmycMatchModal || !this.productId) return;
      const nameEl = this.root.querySelector('#roasProductName');
      const codeEl = this.root.querySelector('#roasProductEnglish');
      window.XmycMatchModal.open({
        productId: this.productId,
        productName: nameEl ? (nameEl.textContent || '').trim() : '',
        productCode: codeEl ? (codeEl.textContent || '').trim() : '',
        onSaved: (result) => {
          if (result && result.purchasePrice !== null && result.purchasePrice !== undefined) {
            const input = this.root.querySelector('[data-roas-field="purchase_price"]');
            if (input) {
              input.value = result.purchasePrice;
              input.dispatchEvent(new Event('input', { bubbles: true }));
            }
          }
        },
      });
    }

    resetParcelSuggestPanel() {
      const result = this.root.querySelector('#roasParcelSuggestResult');
      const btn = this.root.querySelector('#roasParcelSuggestBtn');
      if (result) {
        result.hidden = true;
        result.innerHTML = '';
        result.classList.remove('is-error', 'is-loading');
      }
      if (btn) btn.disabled = false;
    }

    _renderParcelSuggestResult(suggestion) {
      const result = this.root.querySelector('#roasParcelSuggestResult');
      if (!result) return;
      const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      if (!suggestion || !Number.isFinite(Number(suggestion.median))) {
        result.classList.remove('is-loading');
        result.classList.add('is-error');
        result.innerHTML = `时间窗 ${esc(suggestion?.window_start || '')}–${esc(suggestion?.window_end || '')} 内未找到 SKU ${esc(suggestion?.sku || '')} 的有效物流费记录。`;
        result.hidden = false;
        return;
      }
      const median = Number(suggestion.median).toFixed(2);
      const mean = Number(suggestion.mean).toFixed(2);
      const min = Number(suggestion.min).toFixed(2);
      const max = Number(suggestion.max).toFixed(2);
      const sample = suggestion.sample_size || 0;
      const window = `${suggestion.window_start} ~ ${suggestion.window_end}`;
      result.classList.remove('is-error', 'is-loading');
      result.innerHTML = `
        <div>SKU <strong>${esc(suggestion.sku)}</strong> · 店小秘 shop ${esc(String(suggestion.dxm_shop_id || ''))}</div>
        <div>时间窗 ${esc(window)} · 命中 <span class="stat">${sample}</span> 单</div>
        <div>均值 <span class="stat">${mean} RMB</span> · 中位数 <span class="stat">${median} RMB</span> · 范围 ${min} ~ ${max} RMB</div>
        <div class="oc-roas-suggest-note">实际小包成本和预估小包成本由系统每日自动更新</div>
      `;
      result.hidden = false;
    }

    async _fetchParcelCostSuggestion() {
      if (!this.productId) return;
      const btn = this.root.querySelector('#roasParcelSuggestBtn');
      const result = this.root.querySelector('#roasParcelSuggestResult');
      if (btn) btn.disabled = true;
      if (result) {
        result.hidden = false;
        result.classList.remove('is-error');
        result.classList.add('is-loading');
        result.textContent = '正在查询本地物流费数据…';
      }
      try {
        const resp = await fetch(`/medias/api/products/${this.productId}/parcel-cost-suggest`, {
          credentials: 'same-origin',
        });
        if (!resp.ok) {
          let msg = '拉取建议失败';
          try { const d = await resp.json(); msg = d.message || d.error || msg; } catch (e) {}
          throw new Error(msg);
        }
        const data = await resp.json();
        this._renderParcelSuggestResult(data && data.suggestion);
      } catch (err) {
        if (result) {
          result.classList.remove('is-loading');
          result.classList.add('is-error');
          result.textContent = (err && err.message) ? err.message : '拉取建议失败';
          result.hidden = false;
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    fillFromProduct(product) {
      if (!product) return;
      this.resetParcelSuggestPanel();
      ROAS_FIELDS.forEach((field) => {
        const input = this.root.querySelector(`[data-roas-field="${field}"]`);
        if (!input) return;
        const value = product[field] !== null && product[field] !== undefined ? product[field] : '';
        input.value = value;
      });
      const idEl = this.root.querySelector('#roasProductId');
      if (idEl) idEl.textContent = product.id || '—';
      const nameEl = this.root.querySelector('#roasProductName');
      if (nameEl) nameEl.textContent = product.name || '—';
      const codeEl = this.root.querySelector('#roasProductEnglish');
      if (codeEl) codeEl.textContent = product.product_code || '—';
      const cover = this.root.querySelector('#roasProductCover');
      if (cover) {
        cover.innerHTML = product.cover_thumbnail_url
          ? `<img src="${String(product.cover_thumbnail_url).replace(/"/g, '&quot;')}" alt="">`
          : '<svg width="24" height="24"><use href="#ic-package"/></svg>';
      }
      this.renderResult();
    }

    collectPayload() {
      const payload = {};
      ROAS_FIELDS.forEach((field) => {
        const input = this.root.querySelector(`[data-roas-field="${field}"]`);
        if (!input) return;
        const raw = String(input.value || '').trim();
        payload[field] = raw || null;
      });
      return payload;
    }

    computeRoas() {
      const values = this.collectPayload();
      const price = numberOrNull(values.standalone_price);
      const shipping = numberOrNull(values.standalone_shipping_fee) || 0;
      const purchase = numberOrNull(values.purchase_price);
      const estimatedPacket = numberOrNull(values.packet_cost_estimated);
      const actualPacket = numberOrNull(values.packet_cost_actual);
      const rmbPerUsd = currentRmbPerUsd();
      const revenue = price === null ? null : price + shipping;
      const calc = (packet) => {
        if (revenue === null || purchase === null || packet === null) return null;
        const available = revenue * (1 - FEE_RATE) - purchase / rmbPerUsd - packet / rmbPerUsd;
        if (available <= 0) return null;
        return revenue / available;
      };
      const estimated = calc(estimatedPacket);
      const actual = calc(actualPacket);
      const useActual = actualPacket !== null;
      return {
        estimated_roas: estimated,
        actual_roas: actual,
        effective_basis: useActual ? 'actual' : 'estimated',
        effective_roas: useActual ? actual : estimated,
        rmb_per_usd: rmbPerUsd,
      };
    }

    renderResult() {
      const result = this.computeRoas();
      const payload = this.collectPayload();
      const set = (id, text) => {
        const el = this.root.querySelector(id);
        if (el) el.textContent = text;
      };
      set('#roasEstimatedValue', formatRoas(result.estimated_roas));
      set(
        '#roasActualValue',
        numberOrNull(payload.packet_cost_actual) === null ? '待回填' : formatRoas(result.actual_roas)
      );
      set('#roasEffectiveValue', formatRoas(result.effective_roas));
      set('#roasEffectiveBasis', result.effective_basis === 'actual' ? '实际' : '预估');
      const estBox = this.root.querySelector('#roasEstimatedBox');
      const actBox = this.root.querySelector('#roasActualBox');
      if (estBox) estBox.classList.toggle('active', result.effective_basis === 'estimated');
      if (actBox) actBox.classList.toggle('active', result.effective_basis === 'actual');
    }

    _scheduleAutoSave() {
      if (this._debounceTimer) clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(() => {
        this._debounceTimer = null;
        this.save({ immediate: false });
      }, DEBOUNCE_MS);
    }

    async save(opts = {}) {
      // opts.immediate is informational only — debounce timer is cleared
      // unconditionally below; behavior is identical for immediate vs. debounced paths.
      void opts;
      const payload = this.collectPayload();
      if (this._inFlight) {
        this._pendingPayload = payload;
        return;
      }
      if (this._debounceTimer) {
        clearTimeout(this._debounceTimer);
        this._debounceTimer = null;
      }
      this._inFlight = true;
      this._setStatus('saving');
      try {
        const resp = await fetch('/medias/api/products/' + this.productId, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(payload),
        });
        if (!resp.ok) {
          let msg = '保存失败';
          try {
            const data = await resp.json();
            msg = data.error || data.message || msg;
          } catch (e) {}
          throw new Error(msg);
        }
        this._setStatus('saved');
        if (this.onAfterSave) this.onAfterSave(payload);
      } catch (e) {
        this._setStatus('error', e.message || '保存失败');
      } finally {
        this._inFlight = false;
        if (this._pendingPayload) {
          this._pendingPayload = null;
          this.save({ immediate: true });
        }
      }
    }

    _setStatus(state, message) {
      if (!this.statusBarEl) return;
      this.statusBarEl.dataset.state = state;
      const text = this.statusBarEl.querySelector('.oc-roas-status-text');
      const retry = this.statusBarEl.querySelector('.oc-roas-status-retry');
      if (text) {
        if (state === 'saving') text.textContent = '保存中…';
        else if (state === 'saved') text.textContent = `已保存 ✓ ${formatTime(new Date())}`;
        else if (state === 'error') text.textContent = `保存失败：${message || ''}`;
        else text.textContent = '尚未编辑';
      }
      if (retry) retry.hidden = state !== 'error';
    }
  }

  window.RoasFormController = RoasFormController;
})();
