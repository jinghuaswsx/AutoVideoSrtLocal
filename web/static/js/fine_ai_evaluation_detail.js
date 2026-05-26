(function () {
  const config = window.FINE_AI_EVALUATION_PAGE || {};
  const body = document.getElementById('fineAiPageBody');
  const refreshBtn = document.getElementById('fineAiRefreshBtn');
  const terminalStatuses = ['completed', 'partially_completed', 'failed', 'interrupted', 'cancelled'];
  let pollTimer = null;
  let elapsedTimer = null;
  let currentPayload = {};

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

  function copyIconSvg() {
    return `<svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="9" width="10" height="10" rx="2"></rect>
      <path d="M7 15H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v1"></path>
    </svg>`;
  }

  async function copyContextText(value, button) {
    const text = String(value || '').trim();
    if (!text) return;
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', 'readonly');
      area.style.position = 'fixed';
      area.style.left = '-9999px';
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    if (button) {
      const oldTitle = button.dataset.oldTitle || button.title || '复制';
      button.dataset.oldTitle = oldTitle;
      button.title = '已复制';
      button.setAttribute('aria-label', '已复制');
      window.setTimeout(() => {
        button.title = button.dataset.oldTitle || oldTitle;
        button.setAttribute('aria-label', button.dataset.oldTitle || oldTitle);
      }, 900);
    }
  }

  function contextRows(payload) {
    const productSnapshot = (payload || {}).product_snapshot || {};
    const metadata = (payload || {}).metadata || {};
    const assetSnapshot = metadata.asset_snapshot || {};
    const externalCardVideo = metadata.external_card_video || {};
    const videos = Array.isArray(assetSnapshot.videos) && assetSnapshot.videos.length
      ? assetSnapshot.videos
      : (Array.isArray(productSnapshot.videos) ? productSnapshot.videos : []);
    const firstVideo = videos[0] || {};
    const productCode = String(
      (payload || {}).product_code
      || productSnapshot.product_code
      || ''
    ).trim();
    const videoName = String(
      (payload || {}).card_video_name
      || externalCardVideo.name
      || externalCardVideo.filename
      || firstVideo.filename
      || firstVideo.display_name
      || firstVideo.object_key
      || ''
    ).trim();
    const productLink = String(
      (payload || {}).product_link
      || (payload || {}).product_url
      || metadata.external_product_link
      || productSnapshot.product_url
      || productSnapshot.landing_page_url
      || ''
    ).trim();
    return [
      {label: 'Product code', value: productCode},
      {label: '视频文件名', value: videoName},
      {label: '商品链接', value: productLink},
    ];
  }

  function renderContextCopyPanel(payload) {
    return `<section class="fine-ai-context-panel" aria-label="精细 AI 评估上下文">
      ${contextRows(payload).map(row => {
        const value = String(row.value || '').trim();
        const display = value || '-';
        return `<div class="fine-ai-context-row">
          <span class="fine-ai-context-label">${escapeHtml(row.label)}:</span>
          <span class="fine-ai-context-value" title="${escapeHtml(display)}">${escapeHtml(display)}</span>
          <button type="button"
              class="fine-ai-context-copy-btn"
              data-fine-ai-context-copy="1"
              data-copy-text="${escapeHtml(value)}"
              title="复制${escapeHtml(row.label)}"
              aria-label="复制${escapeHtml(row.label)}"
              ${value ? '' : 'disabled'}>${copyIconSvg()}</button>
        </div>`;
      }).join('')}
    </section>`;
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
    const labels = {
      running: '正在请求中',
      queued: '等待开始',
      pending: '等待开始',
      waiting: '等待中',
      completed: '评估完成',
      partially_completed: '部分完成',
      failed: '评估失败',
      interrupted: '已中断',
      cancelled: '已取消',
    };
    if (labels[value]) return labels[value];
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
    const labels = {
      running: '执行中',
      waiting: '等待中',
      completed: '已完成',
      failed: '失败',
      interrupted: '已中断',
      skipped: '已跳过',
    };
    if (labels[value]) return labels[value];
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

  function isLlmStep(step) {
    const key = String((step || {}).key || '');
    return key === 'product_fact_extraction' || key.startsWith('country_');
  }

  function stepModelBadge(step) {
    const trace = (step && step.llm_trace) || {};
    const provider = String((step && (step.provider || step.model_provider)) || trace.provider || '').trim();
    const model = String((step && (step.model_id || step.model)) || trace.model_id || trace.model || '').trim();
    if (!provider && !model) return '';
    return `<span class="mki-fine-ai-model-badge" title="${escapeHtml(`${provider || '-'} · ${model || '-'}`)}">${escapeHtml(provider || '-')} · ${escapeHtml(model || '-')}</span>`;
  }

  function hasLlmTrace(step) {
    return Boolean(step && step.llm_trace && typeof step.llm_trace === 'object');
  }

  function renderLlmRequestButton(step) {
    if (!isLlmStep(step)) return '';
    const hasTrace = hasLlmTrace(step);
    return `<button type="button"
        class="mki-fine-ai-llm-btn"
        data-fine-ai-llm-step="${escapeHtml(step.key || '')}"
        ${hasTrace ? '' : 'disabled'}
        title="${hasTrace ? '查看该步骤的大模型请求和结果报文' : '该步骤完成后可查看大模型请求'}">大模型请求</button>`;
  }

  function jsonBlock(value) {
    const text = typeof value === 'string' ? value : JSON.stringify(value || {}, null, 2);
    return `<pre class="ect-modal-json">${escapeHtml(text || '{}')}</pre>`;
  }

  function traceGrid(rows) {
    return `<div class="mki-fine-ai-trace-grid">${rows.map(row => `
      <div class="mki-fine-ai-trace-item">
        <span>${escapeHtml(row.label || '')}</span>
        <strong title="${escapeHtml(String(row.value ?? ''))}">${escapeHtml(String(row.value ?? '-'))}</strong>
      </div>`).join('')}</div>`;
  }

  function renderLlmTraceModal(step) {
    const trace = (step && step.llm_trace) || {};
    const request = trace.request || {};
    const response = trace.response || {};
    const reqSummary = request.summary || {};
    const resSummary = response.summary || {};
    const rows = [
      {label: '步骤', value: step?.title || step?.key || ''},
      {label: 'Provider', value: trace.provider || step?.provider || '-'},
      {label: 'Model', value: trace.model_id || step?.model_id || '-'},
      {label: 'Use Case', value: trace.use_case_code || '-'},
      {label: 'Project', value: trace.project_id || '-'},
      {label: 'Media', value: reqSummary.media_count ?? '-'},
      {label: 'Google Search', value: reqSummary.google_search ? 'on' : 'off'},
      {label: 'URL Context', value: reqSummary.url_context ? 'on' : 'off'},
      {label: 'Input Tokens', value: resSummary.input_tokens || '-'},
      {label: 'Output Tokens', value: resSummary.output_tokens || '-'},
    ];
    const error = trace.error ? `<section class="mki-fine-ai-trace-section"><h4>错误</h4>${jsonBlock(trace.error)}</section>` : '';
    return `<div class="mki-fine-ai-trace-shell">
      <section class="mki-fine-ai-trace-section">
        <h4>大模型调用概览</h4>
        ${traceGrid(rows)}
      </section>
      <section class="mki-fine-ai-trace-section">
        <h4>请求数据</h4>
        <h5>完整 System Prompt</h5>
        ${jsonBlock(request.system_prompt || '')}
        <h5>完整 User Prompt</h5>
        ${jsonBlock(request.prompt || '')}
        <h5>完整请求报文</h5>
        ${jsonBlock(request.payload || {})}
      </section>
      <section class="mki-fine-ai-trace-section">
        <h4>结果数据</h4>
        ${traceGrid([
          {label: 'Has JSON', value: resSummary.has_json ? 'yes' : 'no'},
          {label: 'Has Text', value: resSummary.has_text ? 'yes' : 'no'},
          {label: 'Usage Log ID', value: resSummary.usage_log_id || '-'},
        ])}
        <h5>结构化结果 JSON</h5>
        ${jsonBlock(response.parsed_json || {})}
        <h5>完整结果报文</h5>
        ${jsonBlock(response.raw_payload || {})}
      </section>
      ${error}
    </div>`;
  }

  function stepByKey(stepKey) {
    const steps = (((currentPayload || {}).progress || {}).steps || []);
    return steps.find(step => String(step.key || '') === String(stepKey || '')) || null;
  }

  function openLlmTrace(step) {
    if (!step || !hasLlmTrace(step)) return;
    const overlay = document.createElement('div');
    overlay.className = 'fine-ai-trace-overlay';
    overlay.innerHTML = `<div class="fine-ai-trace-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(`大模型请求 - ${step.title || step.key || ''}`)}">
      <div class="fine-ai-trace-modal-head">
        <strong>${escapeHtml(`大模型请求 - ${step.title || step.key || ''}`)}</strong>
        <button type="button" class="fine-ai-trace-modal-close" aria-label="关闭">×</button>
      </div>
      <div class="fine-ai-trace-modal-body">${renderLlmTraceModal(step)}</div>
    </div>`;
    const close = () => overlay.remove();
    overlay.addEventListener('click', event => {
      if (event.target === overlay) close();
    });
    overlay.querySelector('.fine-ai-trace-modal-close').addEventListener('click', close);
    document.body.appendChild(overlay);
  }

  function countryCodeFromStep(step) {
    const match = String((step || {}).key || '').match(/^country_([A-Z]{2})$/i);
    return match ? match[1].toUpperCase() : '';
  }

  function canRerunStep(step, stepStatus) {
    return Boolean(
      config.rerun_url_template
      && countryCodeFromStep(step)
      && String(stepStatus || '').toLowerCase() === 'failed'
    );
  }

  function renderStepRerunButton(step, stepStatus) {
    if (!canRerunStep(step, stepStatus)) return '';
    const code = countryCodeFromStep(step);
    return `<button type="button"
        class="fine-ai-btn mki-fine-ai-step-rerun"
        data-fine-ai-rerun="${escapeHtml(code)}"
        data-fine-ai-step-rerun="${escapeHtml(step.key || '')}"
        title="${escapeHtml(`重新请求 ${code} 的 AI 评估`)}">重跑AI评估</button>`;
  }

  function markCountryStepRunning(code) {
    const normalized = String(code || '').trim().toUpperCase();
    if (!/^[A-Z]{2}$/.test(normalized)) return;
    const card = body.querySelector(`[data-fine-ai-step="country_${normalized}"]`);
    if (!card) return;
    card.classList.remove('is-failed', 'is-pending', 'is-waiting', 'is-completed', 'is-skipped');
    card.classList.add('is-running');
    const pill = card.querySelector('.mki-fine-ai-status-pill');
    if (pill) {
      pill.className = 'mki-fine-ai-status-pill is-running';
      pill.textContent = stepStatusLabel('running');
    }
    const desc = card.querySelector('.mki-fine-ai-step-desc');
    if (desc) {
      desc.textContent = `${normalized} 正在重新请求 AI 评估`;
    }
    const btn = card.querySelector('[data-fine-ai-rerun]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '请求中';
    }
  }

  function renderProgress(progress, status, resultForSummary = null) {
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
        const showSummaryDecision = String(step.key || '') === 'summary' && resultForSummary;
        const cardClass = stepStatus === 'running' ? 'mki-fine-ai-step-card is-running' : `mki-fine-ai-step-card is-${escapeHtml(stepStatus)}`;
        return `<section class="${cardClass}" data-fine-ai-step="${escapeHtml(step.key || '')}">
          <div class="mki-fine-ai-step-head">
            <div class="mki-fine-ai-step-title">
              <span class="mki-fine-ai-step-dot"></span>
              <strong>${escapeHtml(step.title || step.key || '')}</strong>
              ${stepModelBadge(step)}
            </div>
            <div class="mki-fine-ai-step-actions">
              ${renderLlmRequestButton(step)}
              ${renderStepRerunButton(step, stepStatus)}
              <span class="mki-fine-ai-status-pill is-${escapeHtml(stepStatus)}">${escapeHtml(stepStatusLabel(stepStatus))}</span>
            </div>
          </div>
          <p class="mki-fine-ai-step-desc">${escapeHtml(step.message || step.description || '')}</p>
          ${renderDebug(step.debug)}
          ${renderLogs(step.logs)}
          ${showSummaryDecision ? renderCountryDecisionSummary(resultForSummary) : ''}
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
    if (value === 'HOLD' || value === 'FAILED' || value === 'DANGER') return 'ect-tag-danger';
    return 'ect-tag-info';
  }

  function escapeList(items) {
    const values = Array.isArray(items) ? items : [];
    if (!values.length) return '<span class="ect-muted">-</span>';
    return `<ul class="ect-suggestions">${values.slice(0, 10).map(item => `<li>${escapeHtml(String(item || ''))}</li>`).join('')}</ul>`;
  }

  function countryEntries(result) {
    return Object.entries((result || {}).countries || {})
      .filter(([, country]) => country && typeof country === 'object')
      .map(([code, country]) => [String(country.country_code || code || '').toUpperCase(), country]);
  }

  function countryFinalDecision(country) {
    const status = String((country || {}).status || '').toLowerCase();
    if (status === 'failed') return 'FAILED';
    return String(((country || {}).decision || {}).final_decision || (country || {}).decision || 'HOLD').toUpperCase();
  }

  function countryScore(country) {
    const value = ((country || {}).scores || {}).overall_score;
    return value === null || value === undefined || value === '' ? '-' : value;
  }

  function firstText(values) {
    const rows = Array.isArray(values) ? values : [];
    for (const value of rows) {
      const text = String(value || '').trim();
      if (text) return text;
    }
    return '';
  }

  function countryRisks(country) {
    const risks = (country || {}).risks || {};
    return [
      ...(risks.claim_risks || []),
      ...(risks.compliance_risks || []),
      ...(risks.operational_risks || []),
      ...(risks.trust_risks || []),
      ...(risks.localization_risks || []),
    ];
  }

  function countryReason(country) {
    const decision = (country || {}).decision || {};
    const error = (country || {}).error || {};
    return String(
      decision.one_sentence_reason
      || firstText(decision.why)
      || ((country || {}).recommendations || {}).recommended_positioning
      || error.message
      || (country || {}).error_message
      || '-'
    );
  }

  function countryTopRisk(country) {
    return String(
      firstText(countryRisks(country))
      || firstText((country || {}).missing_data)
      || (((country || {}).error || {}).message)
      || (country || {}).error_message
      || '-'
    );
  }

  function countryNextAction(country) {
    const decision = countryFinalDecision(country);
    const recs = (country || {}).recommendations || {};
    const action = firstText([
      ...(recs.creative_actions || []),
      ...(recs.landing_page_actions || []),
      ...(recs.ad_test_angles || []),
      ...(recs.audience_suggestions || []),
    ]);
    if (decision === 'FAILED') return '重跑AI评估；如果仍失败，补齐落地页、素材或履约数据后再评估。';
    if (decision === 'GO') return action || '准备投放素材、落地页和首轮预算，可以进入执行。';
    if (decision === 'TEST') return action || '先小预算测试，重点观察点击、转化、履约和合规反馈。';
    return action || countryTopRisk(country) || '暂不投入，先处理阻塞风险或补齐关键信息。';
  }

  function resultCountryRows(result) {
    const entries = countryEntries(result);
    const byCode = new Map(entries);
    const preferred = ['DE', 'FR', 'IT', 'ES', 'JP'];
    const rows = preferred
      .filter(code => byCode.has(code))
      .map(code => [code, byCode.get(code)]);
    entries.forEach(([code, country]) => {
      if (!preferred.includes(code)) rows.push([code, country]);
    });
    return rows;
  }

  function decisionClass(country) {
    const decision = countryFinalDecision(country);
    if (decision === 'GO') return 'go';
    if (decision === 'TEST') return 'test';
    if (decision === 'HOLD') return 'hold';
    if (decision === 'FAILED') return 'failed';
    return 'empty';
  }

  function countryScoreNumber(country) {
    const value = Number(((country || {}).scores || {}).overall_score);
    if (!Number.isFinite(value)) return null;
    return Math.max(0, Math.min(100, Math.round(value)));
  }

  function renderResultScoreCell(country) {
    const score = countryScoreNumber(country);
    if (score === null) {
      return '<div class="fine-ai-result-score"><strong>-</strong><small>暂无评分</small><div class="fine-ai-result-score-track"><div class="fine-ai-result-score-fill" style="width:0%"></div></div></div>';
    }
    return `<div class="fine-ai-result-score">
      <strong>${escapeHtml(score)}<small>/100</small></strong>
      <div class="fine-ai-result-score-track"><div class="fine-ai-result-score-fill" style="width:${score}%"></div></div>
    </div>`;
  }

  function countrySubtitle(code, country) {
    return [
      code,
      (country || {}).language,
      (country || {}).currency,
    ].map(item => String(item || '').trim()).filter(Boolean).join(' · ');
  }

  function riskItems(country) {
    const missing = Array.isArray((country || {}).missing_data) ? (country || {}).missing_data : [];
    const items = [
      ...countryRisks(country),
      ...(missing.map(item => '待补充：' + item)),
    ].map(item => String(item || '').trim()).filter(Boolean);
    const error = String((((country || {}).error || {}).message) || (country || {}).error_message || '').trim();
    if (error) items.unshift('评估失败：' + error);
    return items;
  }

  function renderListCell(items) {
    const values = (Array.isArray(items) ? items : [items])
      .map(item => String(item || '').trim())
      .filter(Boolean)
      .slice(0, 3);
    if (!values.length) return '<span class="ect-muted">-</span>';
    return `<ul class="fine-ai-result-list">${values.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
  }

  function renderResultCountryMatrix(result) {
    const rows = resultCountryRows(result);
    if (!rows.length) return '';
    const countryCells = rows.map(([code, country]) => `
      <td><div class="fine-ai-result-country">
        <strong>${escapeHtml(country.country_name_zh || country.country_name || code)}</strong>
        <span>${escapeHtml(countrySubtitle(code, country))}</span>
      </div></td>
    `).join('');
    const scoreCells = rows.map(([, country]) => `<td>${renderResultScoreCell(country)}</td>`).join('');
    const decisionCells = rows.map(([, country]) => {
      const confidence = String(((country.decision || {}).confidence) || country.confidence || '').trim();
      return `<td><div class="fine-ai-result-decision">
        <span class="fine-ai-result-pill is-${escapeHtml(decisionClass(country))}">${escapeHtml(decisionDisplay(countryFinalDecision(country)))}</span>
        ${confidence ? `<small>置信度：${escapeHtml(confidence)}</small>` : ''}
      </div></td>`;
    }).join('');
    const reasonCells = rows.map(([, country]) => `<td>${escapeHtml(countryReason(country))}</td>`).join('');
    const riskCells = rows.map(([, country]) => `<td>${renderListCell(riskItems(country).slice(0, 2))}</td>`).join('');
    const actionCells = rows.map(([, country]) => `<td>${renderListCell(countryNextAction(country))}</td>`).join('');
    return `<div class="fine-ai-result-scroll">
      <table class="fine-ai-result-table">
        <tbody>
          <tr><th>国家</th>${countryCells}</tr>
          <tr><th>AI 评分</th>${scoreCells}</tr>
          <tr><th>评估结果</th>${decisionCells}</tr>
          <tr><th>结论依据</th>${reasonCells}</tr>
          <tr><th>主要风险</th>${riskCells}</tr>
          <tr><th>下一步</th>${actionCells}</tr>
        </tbody>
      </table>
    </div>`;
  }

  function resultMetric(label, value, unit = '') {
    const display = value === null || value === undefined || value === '' ? '-' : value;
    return `<div class="fine-ai-result-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(display))}${unit ? `<small>${escapeHtml(unit)}</small>` : ''}</strong>
    </div>`;
  }

  function detectChannel(result) {
    const directChannel = String((result || {}).channel || '').toLowerCase();
    if (directChannel && directChannel !== 'undefined') return directChannel;
    const metadata = (result || {}).metadata || {};
    const evaluator = String(metadata.evaluator || '').toLowerCase();
    const runId = String((result || {}).evaluation_run_id || '');
    if (evaluator.includes('antigravity') || runId.startsWith('eval_antigravity_')) return 'antigravity';
    const channel = String(metadata.channel || '').toLowerCase();
    if (channel === 'antigravity') return 'antigravity';
    if (channel === 'skill') return 'skill';
    if (channel === 'adc' || channel) return channel || 'adc';
    const provider = String(metadata.provider || '').toLowerCase();
    const model = String(metadata.model || '').toLowerCase();
    if (provider || model) return 'adc';
    return '';
  }

  function channelBadge(result) {
    const ch = detectChannel(result);
    if (!ch) return '';
    const labels = { adc: 'ADC', antigravity: 'Antigravity', skill: 'SKILL', scheduled: 'Auto' };
    const clsMap = { antigravity: 'is-antigravity', adc: 'is-adc', skill: 'is-skill', scheduled: 'is-scheduled' };
    const cls = clsMap[ch] || 'is-unknown';
    const label = labels[ch] || ch.toUpperCase();
    return `<span class="fine-ai-channel-badge ${cls}" title="评估渠道：${escapeHtml(label)}">${escapeHtml(label)}</span>`;
  }

  function renderStructuredResultOverview(result) {
    const matrix = renderResultCountryMatrix(result);
    if (!matrix) return '';
    const summary = (result || {}).summary || {};
    const counts = summary.decision_counts || {};
    const recommendation = String(summary.overall_recommendation || (result || {}).status || '').trim() || '-';
    return `<section class="fine-ai-result-overview" aria-label="AI 精细评估结果总览">
      <div class="fine-ai-result-overview-head">
        <div>
          <h4 class="fine-ai-result-overview-title">AI 精细评估结果总览${channelBadge(result)}</h4>
          <p class="fine-ai-result-overview-subtitle">按国家横向对比评分、投放结论、核心风险和下一步动作。</p>
        </div>
        <div class="fine-ai-result-metrics">
          ${resultMetric('平均分', summary.average_score ?? '', '/100')}
          ${resultMetric('推荐结论', recommendation)}
          ${resultMetric('GO', counts.GO ?? 0)}
          ${resultMetric('TEST', counts.TEST ?? 0)}
          ${resultMetric('HOLD', counts.HOLD ?? 0)}
        </div>
      </div>
      ${matrix}
    </section>`;
  }

  function renderSummaryRerunButton(code, country) {
    if (
      !config.rerun_url_template
      || String((country || {}).status || '').toLowerCase() !== 'failed'
    ) {
      return '';
    }
    return `<button type="button"
        class="fine-ai-btn mki-fine-ai-step-rerun"
        data-fine-ai-rerun="${escapeHtml(code)}"
        data-fine-ai-summary-rerun="${escapeHtml(code)}"
        title="${escapeHtml(`重新请求 ${code} 的 AI 评估`)}">重跑AI评估</button>`;
  }

  function decisionDisplay(decision) {
    if (decision === 'GO') return 'GO / 建议做';
    if (decision === 'TEST') return 'TEST / 先测试';
    if (decision === 'FAILED') return 'FAILED / 需重跑';
    return 'HOLD / 暂不做';
  }

  function renderDecisionRow(code, country) {
    const decision = countryFinalDecision(country);
    const status = String((country || {}).status || '').toLowerCase();
    return `<article class="fine-ai-decision-row">
      <div class="fine-ai-decision-row-head">
        <strong>${escapeHtml(country.country_name_zh || country.country_name || code)} <span>${escapeHtml(code)}</span></strong>
        <div class="fine-ai-decision-row-actions">
          <span class="ect-summary-tag ${severity(decision)}">${escapeHtml(decisionDisplay(decision))}</span>
          <span class="fine-ai-decision-score">分数 ${escapeHtml(String(countryScore(country)))}</span>
          ${renderSummaryRerunButton(code, country)}
        </div>
      </div>
      <div class="fine-ai-decision-grid">
        <div><span>结论依据</span><p>${escapeHtml(countryReason(country))}</p></div>
        <div><span>主要风险</span><p>${escapeHtml(countryTopRisk(country))}</p></div>
        <div><span>下一步</span><p>${escapeHtml(countryNextAction(country))}</p></div>
      </div>
      ${status === 'failed' ? '<p class="fine-ai-decision-note">该国家本轮没有可用结论，需先重跑后再作为投放判断。</p>' : ''}
    </article>`;
  }

  function renderCountryDecisionSummary(result) {
    const entries = countryEntries(result);
    if (!entries.length) return '';
    const groups = [
      {
        key: 'go',
        className: 'fine-ai-decision-group is-go',
        title: '绿色：建议做',
        label: '建议做',
        empty: '暂无明确建议直接做的国家。',
        match: country => countryFinalDecision(country) === 'GO',
      },
      {
        key: 'test',
        className: 'fine-ai-decision-group is-test',
        title: '黄色：先测试 / 需要考虑',
        label: '先测试 / 需要考虑',
        empty: '暂无建议进入小预算测试的国家。',
        match: country => countryFinalDecision(country) === 'TEST',
      },
      {
        key: 'hold',
        className: 'fine-ai-decision-group is-hold',
        title: '红色：暂不做 / 需重跑或补数据',
        label: '暂不做 / 需重跑或补数据',
        empty: '暂无暂缓或失败国家。',
        match: country => ['HOLD', 'FAILED'].includes(countryFinalDecision(country)),
      },
    ];
    return `<div class="fine-ai-decision-summary">
      ${groups.map(group => {
        const rows = entries.filter(([, country]) => group.match(country));
        return `<section class="${group.className}">
          <div class="fine-ai-decision-group-head">
            <h5>${escapeHtml(group.title)}</h5>
            <span>${rows.length} 个国家</span>
          </div>
          ${rows.length ? rows.map(([code, country]) => renderDecisionRow(code, country)).join('') : `<p class="ect-muted">${escapeHtml(group.empty)}</p>`}
        </section>`;
      }).join('')}
    </div>`;
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
    currentPayload = status || {};
    body.innerHTML = `
      ${renderContextCopyPanel(status)}
      ${renderProgress(status.progress || {}, status.status || 'running')}
    `;
    bindRerunButtons();
    startElapsedTimer();
  }

  function renderResult(result) {
    stopElapsedTimer();
    currentPayload = result || {};
    body.innerHTML = `
      ${renderContextCopyPanel(result)}
      ${renderStructuredResultOverview(result)}
      ${renderProgress(result.progress || {}, result.status || '', result)}
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
    body.querySelectorAll('[data-fine-ai-llm-step]').forEach(btn => {
      btn.onclick = () => openLlmTrace(stepByKey(btn.dataset.fineAiLlmStep));
    });
    body.querySelectorAll('[data-fine-ai-rerun]').forEach(btn => {
      btn.onclick = async () => {
        const code = btn.dataset.fineAiRerun || '';
        const url = String(config.rerun_url_template || '').replace('{country}', encodeURIComponent(code));
        if (!url || !code) return;
        const ok = window.confirm(`确认重新请求 ${code} 的 AI 评估？`);
        if (!ok) return;
        stopPoll();
        markCountryStepRunning(code);
        btn.disabled = true;
        btn.textContent = '请求中';
        try {
          await fetchJson(url, {
            method: 'POST',
            headers: csrfHeaders({'Content-Type': 'application/json'}),
            body: JSON.stringify({force_refresh: true, include_assets: true, include_videos: true}),
          });
          await poll();
        } catch (err) {
          btn.textContent = '请求失败';
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

  body.addEventListener('click', event => {
    const btn = event.target.closest('[data-fine-ai-context-copy]');
    if (!btn) return;
    event.preventDefault();
    copyContextText(btn.dataset.copyText || '', btn).catch(() => {
      btn.title = '复制失败';
      btn.setAttribute('aria-label', '复制失败');
    });
  });

  window.addEventListener('beforeunload', () => {
    stopPoll();
    stopElapsedTimer();
  });

  poll();
})();
