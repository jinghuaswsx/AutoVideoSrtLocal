(function () {
  const config = window.FINE_AI_EVALUATION_PAGE || {};
  const body = document.getElementById('fineAiPageBody');
  const refreshBtn = document.getElementById('fineAiRefreshBtn');
  const terminalStatuses = ['completed', 'partially_completed', 'failed', 'cancelled'];
  let pollTimer = null;
  let elapsedTimer = null;

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function csrfHeaders(extra = {}) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
    return token ? {...extra, 'X-CSRFToken': token} : extra;
  }

  async function fetchJson(url, options = {}) {
    const resp = await fetch(url, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.success === false || data.error) {
      const err = data.error || {};
      throw new Error(err.message || data.message || `请求失败: ${resp.status}`);
    }
    return data.data || data;
  }

  function defaultProgress() {
    return {
      total_steps: 8,
      completed_steps: 0,
      current_step: 'queued',
      current_country: '',
      elapsed_seconds: 0,
      countries: {DE: 'pending', FR: 'pending', IT: 'pending', ES: 'pending', JP: 'pending'},
      steps: [
        {key: 'data_preparation', title: '数据准备', status: 'pending', message: '等待后端创建任务', logs: [], debug: []},
        {key: 'product_fact_extraction', title: '商品事实整理', status: 'pending', message: '等待请求大模型', logs: [], debug: []},
        ...['DE', 'FR', 'IT', 'ES', 'JP'].map(code => ({key: `country_${code}`, title: `${code} 国家评估`, status: 'pending', message: '等待前序国家完成', logs: [], debug: []})),
        {key: 'summary', title: '汇总结果', status: 'pending', message: '等待五国评估完成', logs: [], debug: []},
      ],
      events: [],
    };
  }

  function statusLabel(status) {
    const value = String(status || '').toLowerCase();
    if (value === 'running') return '正在请求中';
    if (value === 'queued' || value === 'pending') return '等待开始';
    if (value === 'completed') return '评估完成';
    if (value === 'partially_completed') return '部分完成';
    if (value === 'failed') return '评估失败';
    if (value === 'cancelled') return '已取消';
    return status || '未知状态';
  }

  function effectiveStatus(progress, status) {
    const raw = String(status || '').toLowerCase();
    const currentStep = String((progress || {}).current_step || '').toLowerCase();
    const completedSteps = Number((progress || {}).completed_steps || 0);
    if ((raw === 'queued' || raw === 'pending') && (completedSteps > 0 || (currentStep && currentStep !== 'queued'))) {
      return 'running';
    }
    return raw || currentStep || 'queued';
  }

  function stepStatusLabel(status) {
    const value = String(status || 'pending').toLowerCase();
    if (value === 'running') return '执行中';
    if (value === 'completed') return '已完成';
    if (value === 'failed') return '失败';
    if (value === 'skipped') return '已跳过';
    return '等待';
  }

  function secondsSince(startedAt) {
    if (!startedAt) return 0;
    const started = new Date(startedAt);
    if (Number.isNaN(started.getTime())) return 0;
    return Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
  }

  function elapsedLabel(progress, status) {
    const payload = progress || {};
    const isFinal = terminalStatuses.includes(String(status || '').toLowerCase());
    let seconds = Math.max(0, Number(payload.elapsed_seconds || 0));
    if (!isFinal && payload.started_at) {
      seconds = Math.max(seconds, secondsSince(payload.started_at));
    }
    const minutes = Math.floor(seconds / 60);
    const remain = seconds % 60;
    const text = minutes > 0 ? `${minutes} 分 ${remain} 秒` : `${remain} 秒`;
    return isFinal ? `总耗时 ${text}` : `已请求 ${text}`;
  }

  function formatTs(ts) {
    if (!ts) return '-';
    const date = new Date(ts);
    if (Number.isNaN(date.getTime())) return String(ts);
    return date.toLocaleTimeString('zh-CN', {hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'});
  }

  function renderDebug(debug) {
    const items = Array.isArray(debug) ? debug : [];
    if (!items.length) return '';
    return `<div class="mki-fine-ai-debug-grid">${items.slice(0, 12).map(item => `
      <div class="mki-fine-ai-debug-item">
        <span>${escapeHtml(item.label || '')}</span>
        <strong title="${escapeHtml(String(item.value ?? ''))}">${escapeHtml(String(item.value ?? '-'))}</strong>
      </div>`).join('')}</div>`;
  }

  function renderLogs(logs) {
    const rows = Array.isArray(logs) ? logs.slice(-5) : [];
    if (!rows.length) return '';
    return `<div class="mki-fine-ai-log-list">${rows.map(row => `
      <div class="mki-fine-ai-log-row">
        <span>${escapeHtml(formatTs(row.ts))}</span>
        <strong>${escapeHtml(row.level || 'info')}</strong>
        <span>${escapeHtml(row.message || '')}</span>
      </div>`).join('')}</div>`;
  }

  function renderProgress(progress, status) {
    const p = {...defaultProgress(), ...(progress || {})};
    const steps = Array.isArray(p.steps) && p.steps.length ? p.steps : defaultProgress().steps;
    const total = Math.max(1, Number(p.total_steps || steps.length || 8));
    const done = Math.max(0, Math.min(total, Number(p.completed_steps || 0)));
    const pct = Math.round((done / total) * 100);
    const current = p.current_country || String(p.current_step || '').replace('country_evaluation_', '').replace('country_', '');
    const displayStatus = effectiveStatus(p, status);
    return `
      <section class="mki-fine-ai-progress-header" aria-live="polite">
        <div class="mki-fine-ai-progress-top">
          <div class="mki-fine-ai-progress-title">${escapeHtml(statusLabel(displayStatus))}</div>
          <div class="mki-fine-ai-progress-time"
              data-fine-ai-elapsed
              data-started-at="${escapeHtml(p.started_at || '')}"
              data-elapsed-seconds="${escapeHtml(String(p.elapsed_seconds || 0))}"
              data-status="${escapeHtml(displayStatus)}">${escapeHtml(elapsedLabel(p, displayStatus))}</div>
        </div>
        <div class="mki-fine-ai-progress-track" aria-label="精细 AI 评估进度 ${pct}%">
          <div class="mki-fine-ai-progress-fill" style="width:${pct}%"></div>
        </div>
        <div class="mki-fine-ai-progress-meta">
          <span>${done}/${total} 步</span>
          <span>${current ? `当前位置：${escapeHtml(current)}` : '等待后端调度'}</span>
          <span>${escapeHtml(p.current_step || 'queued')}</span>
        </div>
      </section>
      <div class="mki-fine-ai-step-list">${steps.map(step => {
        const stepStatus = String(step.status || 'pending').toLowerCase();
        const cardClass = stepStatus === 'running' ? 'mki-fine-ai-step-card is-running' : `mki-fine-ai-step-card is-${escapeHtml(stepStatus)}`;
        return `<section class="${cardClass}" data-fine-ai-step="${escapeHtml(step.key || '')}">
          <div class="mki-fine-ai-step-head">
            <div class="mki-fine-ai-step-title">
              <span class="mki-fine-ai-step-dot"></span>
              <strong>${escapeHtml(step.title || step.key || '')}</strong>
            </div>
            <span class="mki-fine-ai-status-pill is-${escapeHtml(stepStatus)}">${escapeHtml(stepStatusLabel(stepStatus))}</span>
          </div>
          <p class="mki-fine-ai-step-desc">${escapeHtml(step.message || step.description || '')}</p>
          ${renderDebug(step.debug)}
          ${renderLogs(step.logs)}
        </section>`;
      }).join('')}</div>
      ${renderExecutionLog(p)}
    `;
  }

  function renderExecutionLog(progress) {
    const events = Array.isArray((progress || {}).events) ? progress.events.slice(-24).reverse() : [];
    if (!events.length) return '';
    return `<section class="mki-fine-ai-execution-log">
      <h4>执行明细</h4>
      <div class="mki-fine-ai-log-list">${events.map(row => `
        <div class="mki-fine-ai-log-row">
          <span>${escapeHtml(formatTs(row.ts))}</span>
          <strong>${escapeHtml(row.level || 'info')}</strong>
          <span>${escapeHtml(row.step_key || '')} · ${escapeHtml(row.message || '')}</span>
        </div>`).join('')}</div>
    </section>`;
  }

  function severity(decision) {
    const value = String(decision || '').toUpperCase();
    if (value === 'GO' || value === 'SUCCESS') return 'ect-tag-success';
    if (value === 'TEST' || value === 'WARNING') return 'ect-tag-warning';
    if (value === 'HOLD' || value === 'DANGER') return 'ect-tag-danger';
    return 'ect-tag-info';
  }

  function escapeList(items) {
    const values = Array.isArray(items) ? items : [];
    if (!values.length) return '<span class="ect-muted">-</span>';
    return `<ul class="ect-suggestions">${values.slice(0, 10).map(item => `<li>${escapeHtml(String(item || ''))}</li>`).join('')}</ul>`;
  }

  function renderCountryResults(result) {
    const entries = Object.entries((result || {}).countries || {});
    if (!entries.length) return '';
    return `<section class="fine-ai-result-section">
      <h4>国家评估结果</h4>
      <div class="fine-ai-country-list">${entries.map(([code, country]) => {
        const decision = (country.decision || {}).final_decision || country.decision || '';
        const score = (country.scores || {}).overall_score ?? '-';
        return `<article class="fine-ai-country-card">
          <div class="fine-ai-country-head">
            <h4>${escapeHtml(country.country_name_zh || code)} · ${escapeHtml(country.status || '')}</h4>
            <button type="button" class="fine-ai-btn" data-fine-ai-rerun="${escapeHtml(code)}">重跑该国家</button>
          </div>
          <p><strong>总分：</strong>${escapeHtml(String(score))} <span class="ect-summary-tag ${severity(decision)}">${escapeHtml(decision || '-')}</span></p>
          ${country.error ? `<p class="ect-muted">失败原因：${escapeHtml((country.error || {}).message || '')}</p>` : ''}
          <h5>机会</h5>${escapeList((country.decision || {}).why || (country.recommendations || {}).ad_test_angles)}
          <h5>风险</h5>${escapeList([...(country.risks?.claim_risks || []), ...(country.risks?.compliance_risks || []), ...(country.risks?.operational_risks || []), ...(country.risks?.trust_risks || []), ...(country.risks?.localization_risks || [])])}
          <h5>素材审计</h5>${escapeList([...(country.creative_fit?.cover_image_audit?.issues || []), ...(country.creative_fit?.product_image_audit?.issues || []), ...(country.creative_fit?.video_audit?.proof_gaps || [])])}
          <h5>待补充数据</h5>${escapeList(country.missing_data || [])}
        </article>`;
      }).join('')}</div>
    </section>`;
  }

  function renderSummary(result) {
    const summary = result.summary || {};
    const frontend = result.frontend || {};
    const cards = Array.isArray(frontend.cards) ? frontend.cards : [];
    return `<section class="fine-ai-result-section">
      <h4>汇总结论</h4>
      <p><span class="ect-summary-tag ${severity(summary.overall_recommendation || result.status)}">${escapeHtml(summary.overall_recommendation || result.status || '-')}</span></p>
      <p class="ect-muted">Run: ${escapeHtml(result.evaluation_run_id || config.evaluation_run_id || '')}</p>
      ${cards.length ? `<div class="mki-fine-ai-debug-grid">${cards.map(card => `
        <div class="mki-fine-ai-debug-item">
          <span>${escapeHtml(card.title || '')}</span>
          <strong>${escapeHtml(String(card.value ?? '-'))}${escapeHtml(card.unit || '')}</strong>
        </div>`).join('')}</div>` : ''}
    </section>`;
  }

  function tickElapsedLabels() {
    body.querySelectorAll('[data-fine-ai-elapsed]').forEach(node => {
      node.textContent = elapsedLabel({
        started_at: node.dataset.startedAt || '',
        elapsed_seconds: Number(node.dataset.elapsedSeconds || 0),
      }, node.dataset.status || '');
    });
  }

  function startElapsedTimer() {
    stopElapsedTimer();
    tickElapsedLabels();
    elapsedTimer = window.setInterval(tickElapsedLabels, 1000);
  }

  function stopElapsedTimer() {
    if (elapsedTimer) {
      window.clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function renderStatus(status) {
    body.innerHTML = renderProgress(status.progress || {}, status.status || 'running');
    startElapsedTimer();
  }

  function renderResult(result) {
    stopElapsedTimer();
    body.innerHTML = `
      ${renderProgress(result.progress || {}, result.status || '')}
      ${renderSummary(result)}
      ${renderCountryResults(result)}
    `;
    bindRerunButtons();
    tickElapsedLabels();
  }

  async function loadOnce() {
    const status = await fetchJson(config.status_url);
    renderStatus(status);
    if (terminalStatuses.includes(String(status.status || '').toLowerCase())) {
      const result = await fetchJson(config.result_url);
      renderResult(result);
      return false;
    }
    return true;
  }

  async function poll() {
    try {
      const shouldContinue = await loadOnce();
      if (shouldContinue) {
        pollTimer = window.setTimeout(poll, 2000);
      }
    } catch (err) {
      body.innerHTML = `<div class="fine-ai-loading">加载失败：${escapeHtml(err.message || err)}</div>`;
    }
  }

  function stopPoll() {
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function bindRerunButtons() {
    body.querySelectorAll('[data-fine-ai-rerun]').forEach(btn => {
      btn.onclick = async () => {
        const code = btn.dataset.fineAiRerun || '';
        const url = String(config.rerun_url_template || '').replace('{country}', encodeURIComponent(code));
        btn.disabled = true;
        btn.textContent = '重跑中';
        try {
          await fetchJson(url, {
            method: 'POST',
            headers: csrfHeaders({'Content-Type': 'application/json'}),
            body: JSON.stringify({force_refresh: true, include_assets: true, include_videos: true}),
          });
          stopPoll();
          await poll();
        } catch (err) {
          btn.textContent = '重跑失败';
          btn.title = err.message || String(err);
        } finally {
          btn.disabled = false;
        }
      };
    });
  }

  refreshBtn.onclick = () => {
    stopPoll();
    poll();
  };

  window.addEventListener('beforeunload', () => {
    stopPoll();
    stopElapsedTimer();
  });

  poll();
})();
