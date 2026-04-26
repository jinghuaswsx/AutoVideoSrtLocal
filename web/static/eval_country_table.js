/* AI 评估详情可视化表格（素材管理 / 推送管理共享）
 *
 * 用法：
 *   const html = window.EvalCountryTable.render(detailRawOrObj);
 *   const detail = window.EvalCountryTable.parse(detailRawOrObj);
 *   if (detail && detail.countries && detail.countries.length) { ... 用表格 ... }
 *   else { ... fallback 到原 JSON pre ... }
 *
 * 数据契约（见 appcore/material_evaluation.py）：
 *   detail.countries[i] = { lang, language, country, is_suitable, score, risk_level, decision, reason, suggestions }
 *   detail.ai_score, detail.ai_evaluation_result 可选（顶部摘要用）
 */
(function () {
  if (window.EvalCountryTable) return;

  const STYLE_ID = 'eval-country-table-style';
  const CSS = `
    .ect-empty { padding: var(--oc-sp-5, 20px); color: var(--oc-fg-subtle, #94a3b8); text-align: center; font-size: 13px; }
    .ect-summary { display:flex; align-items:center; justify-content:space-between; gap: var(--oc-sp-4, 16px); padding: var(--oc-sp-3, 12px) var(--oc-sp-4, 16px); border:1px solid var(--oc-border); border-radius: var(--oc-r-md, 8px); background: var(--oc-bg-subtle); margin-bottom: var(--oc-sp-4, 16px); flex-wrap: wrap; }
    .ect-summary-left { display:flex; align-items:baseline; gap: var(--oc-sp-3, 12px); flex-wrap: wrap; }
    .ect-summary-label { color: var(--oc-fg-muted); font-size: 12px; }
    .ect-summary-score { font-family: var(--font-mono, "JetBrains Mono", ui-monospace, Consolas, monospace); font-size: 24px; font-weight: 600; color: var(--oc-fg); font-variant-numeric: tabular-nums; line-height: 1; }
    .ect-summary-score-suffix { font-size: 13px; color: var(--oc-fg-subtle); margin-left: 2px; font-weight: 400; }
    .ect-summary-tag { display: inline-flex; align-items: center; padding: 4px 12px; border-radius: var(--oc-r-full, 9999px); font-size: 12px; font-weight: 500; }
    .ect-tag-success { background: var(--oc-success-bg); color: var(--oc-success-fg); }
    .ect-tag-warning { background: var(--oc-warning-bg); color: var(--oc-warning-fg); }
    .ect-tag-danger  { background: var(--oc-danger-bg);  color: var(--oc-danger-fg); }
    .ect-tag-info    { background: var(--oc-accent-subtle); color: var(--oc-accent); }
    .ect-scroll { width: 100%; overflow-x: auto; border: 1px solid var(--oc-border); border-radius: var(--oc-r-md, 8px); background: var(--oc-bg); }
    .ect-table { border-collapse: separate; border-spacing: 0; width: 100%; }
    .ect-table th, .ect-table td { padding: var(--oc-sp-3, 12px); border-bottom: 1px solid var(--oc-border); vertical-align: top; font-size: 13px; line-height: 1.55; text-align: left; }
    .ect-table tr:last-child > th, .ect-table tr:last-child > td { border-bottom: none; }
    .ect-table th.ect-row-label { position: sticky; left: 0; z-index: 2; background: var(--oc-bg-subtle); border-right: 1px solid var(--oc-border); color: var(--oc-fg-muted); font-weight: 500; min-width: 96px; white-space: nowrap; }
    .ect-table thead th { background: var(--oc-bg-subtle); color: var(--oc-fg); font-weight: 600; }
    .ect-table thead th.ect-row-label { z-index: 3; }
    .ect-cell { min-width: 168px; max-width: 240px; word-break: break-word; }
    .ect-thead-country { display: flex; flex-direction: column; gap: 2px; }
    .ect-thead-name { font-size: 13px; color: var(--oc-fg); font-weight: 600; }
    .ect-thead-lang { font-size: 11px; color: var(--oc-fg-subtle); font-family: var(--font-mono, "JetBrains Mono", ui-monospace, Consolas, monospace); letter-spacing: 0.04em; text-transform: uppercase; }
    .ect-score-num { font-family: var(--font-mono, "JetBrains Mono", ui-monospace, Consolas, monospace); font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.2; }
    .ect-score-suffix { font-size: 12px; color: var(--oc-fg-subtle); font-weight: 400; margin-left: 2px; }
    .ect-score-bar { margin-top: 6px; height: 4px; background: var(--oc-bg-muted); border-radius: var(--oc-r-full, 9999px); overflow: hidden; }
    .ect-score-bar-fill { height: 100%; border-radius: inherit; transition: width var(--oc-dur, 180ms) var(--oc-ease-out, ease-out); }
    .ect-score-good .ect-score-num { color: var(--oc-success-fg); }
    .ect-score-good .ect-score-bar-fill { background: var(--oc-success); }
    .ect-score-mid .ect-score-num { color: var(--oc-warning-fg); }
    .ect-score-mid .ect-score-bar-fill { background: var(--oc-warning); }
    .ect-score-bad .ect-score-num { color: var(--oc-danger-fg); }
    .ect-score-bad .ect-score-bar-fill { background: var(--oc-danger); }
    .ect-score-na { color: var(--oc-fg-subtle); font-family: var(--font-mono, "JetBrains Mono", ui-monospace, Consolas, monospace); font-size: 14px; }
    .ect-verdict { display: flex; align-items: flex-start; gap: var(--oc-sp-2, 8px); }
    .ect-verdict-icon { width: 28px; height: 28px; border-radius: var(--oc-r-full, 9999px); display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .ect-verdict-yes { background: var(--oc-success-bg); color: var(--oc-success-fg); }
    .ect-verdict-no  { background: var(--oc-danger-bg);  color: var(--oc-danger-fg); }
    .ect-verdict-text { font-size: 13px; color: var(--oc-fg); font-weight: 500; line-height: 1.4; }
    .ect-verdict-decision { font-size: 12px; color: var(--oc-fg-muted); margin-top: 2px; line-height: 1.4; }
    .ect-reason { color: var(--oc-fg); font-size: 13px; line-height: 1.55; white-space: pre-wrap; }
    .ect-reason.ect-muted { color: var(--oc-fg-subtle); }
    .ect-risk { display: inline-flex; padding: 2px 10px; border-radius: var(--oc-r-full, 9999px); font-size: 12px; font-weight: 500; }
    .ect-risk-low { background: var(--oc-success-bg); color: var(--oc-success-fg); }
    .ect-risk-medium { background: var(--oc-warning-bg); color: var(--oc-warning-fg); }
    .ect-risk-high { background: var(--oc-danger-bg); color: var(--oc-danger-fg); }
    .ect-risk-na { color: var(--oc-fg-subtle); font-size: 12px; }
    .ect-suggestions { margin: 0; padding-left: 18px; color: var(--oc-fg-muted); font-size: 12px; line-height: 1.55; }
    .ect-suggestions li { margin: 0; }
    .ect-suggestions-empty { color: var(--oc-fg-subtle); font-size: 12px; }
  `;

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function parse(raw) {
    if (raw && typeof raw === 'object') return raw;
    const text = String(raw == null ? '' : raw).trim();
    if (!text) return null;
    try { return JSON.parse(text); } catch (_) { return null; }
  }

  function extractCountries(detail) {
    if (!detail || typeof detail !== 'object') return [];
    if (Array.isArray(detail.countries)) return detail.countries;
    return [];
  }

  function scoreClass(score) {
    if (typeof score !== 'number' || Number.isNaN(score)) return 'ect-score-na';
    if (score >= 80) return 'ect-score-good';
    if (score >= 60) return 'ect-score-mid';
    return 'ect-score-bad';
  }

  function riskClass(level) {
    const k = String(level || '').toLowerCase().trim();
    if (!k) return null;
    if (k === 'low' || k === '低' || k === 'safe') return 'ect-risk-low';
    if (k === 'medium' || k === 'mid' || k === '中' || k === 'moderate') return 'ect-risk-medium';
    if (k === 'high' || k === '高' || k === 'critical') return 'ect-risk-high';
    return null;
  }

  function riskLabel(level) {
    const k = String(level || '').trim();
    if (!k) return '';
    const map = { low: '低', medium: '中', mid: '中', moderate: '中', high: '高', critical: '严重' };
    return map[k.toLowerCase()] || k;
  }

  function checkSvg() {
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
  }

  function crossSvg() {
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  }

  function summaryTagHtml(detail) {
    const result = String((detail && detail.ai_evaluation_result) || '').trim();
    if (!result) return '';
    const cls = result.includes('不适合') ? 'ect-tag-danger'
              : result.includes('需人工') ? 'ect-tag-warning'
              : result.includes('适合')   ? 'ect-tag-success'
              : 'ect-tag-info';
    return `<span class="ect-summary-tag ${cls}">${escapeHtml(result)}</span>`;
  }

  function summaryHtml(detail, countries) {
    let avg = (typeof detail.ai_score === 'number' && !Number.isNaN(detail.ai_score)) ? detail.ai_score : null;
    if (avg == null && countries.length) {
      const nums = countries.map(c => Number(c.score)).filter(n => Number.isFinite(n));
      if (nums.length) avg = Math.round((nums.reduce((s, n) => s + n, 0) / nums.length) * 10) / 10;
    }
    const avgHtml = avg != null
      ? `<span class="ect-summary-score">${escapeHtml(avg)}<span class="ect-summary-score-suffix">/100</span></span>`
      : `<span class="ect-summary-score" style="color:var(--oc-fg-subtle)">—</span>`;
    const countLabel = countries.length ? `${countries.length} 个国家` : '无国家数据';
    return `<div class="ect-summary">
      <div class="ect-summary-left">
        <span class="ect-summary-label">综合评分</span>
        ${avgHtml}
        <span class="ect-summary-label">·</span>
        <span class="ect-summary-label">${escapeHtml(countLabel)}</span>
      </div>
      <div class="ect-summary-right">${summaryTagHtml(detail)}</div>
    </div>`;
  }

  function scoreCellHtml(rawScore) {
    const score = typeof rawScore === 'number' ? rawScore : Number(rawScore);
    if (!Number.isFinite(score)) return `<div class="ect-score-na">—</div>`;
    const pct = Math.max(0, Math.min(100, score));
    return `<div class="${scoreClass(score)}">
      <div class="ect-score-num">${escapeHtml(score)}<span class="ect-score-suffix">/100</span></div>
      <div class="ect-score-bar"><div class="ect-score-bar-fill" style="width:${pct}%"></div></div>
    </div>`;
  }

  function verdictCellHtml(c) {
    const ok = !!c.is_suitable;
    const decision = String(c.decision || '').trim();
    return `<div class="ect-verdict">
      <span class="ect-verdict-icon ${ok ? 'ect-verdict-yes' : 'ect-verdict-no'}" aria-label="${ok ? '适合' : '不适合'}">${ok ? checkSvg() : crossSvg()}</span>
      <div>
        <div class="ect-verdict-text">${ok ? '适合' : '不适合'}</div>
        ${decision ? `<div class="ect-verdict-decision">${escapeHtml(decision)}</div>` : ''}
      </div>
    </div>`;
  }

  function reasonCellHtml(c) {
    const r = String(c.reason || '').trim();
    return r
      ? `<div class="ect-reason">${escapeHtml(r)}</div>`
      : `<div class="ect-reason ect-muted">—</div>`;
  }

  function riskCellHtml(c) {
    const cls = riskClass(c.risk_level);
    if (!cls) {
      const raw = String(c.risk_level || '').trim();
      return raw ? `<span class="ect-risk-na">${escapeHtml(raw)}</span>` : `<span class="ect-risk-na">—</span>`;
    }
    return `<span class="ect-risk ${cls}">${escapeHtml(riskLabel(c.risk_level))}</span>`;
  }

  function suggestionsCellHtml(c) {
    const list = Array.isArray(c.suggestions) ? c.suggestions.map(s => String(s || '').trim()).filter(Boolean) : [];
    if (!list.length) return `<div class="ect-suggestions-empty">—</div>`;
    return `<ul class="ect-suggestions">${list.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ul>`;
  }

  function render(rawDetail) {
    ensureStyle();
    const detail = parse(rawDetail);
    const countries = extractCountries(detail);
    if (!countries.length) {
      return `<div class="ect-empty">暂无评估详情</div>`;
    }

    const headerCells = countries.map(c => {
      const country = String(c.country || c.language || c.lang || '—').trim();
      const lang = String(c.lang || '').trim();
      return `<th class="ect-cell"><div class="ect-thead-country">
        <span class="ect-thead-name">${escapeHtml(country)}</span>
        ${lang ? `<span class="ect-thead-lang">${escapeHtml(lang)}</span>` : ''}
      </div></th>`;
    }).join('');

    const scoreRow = countries.map(c => `<td class="ect-cell">${scoreCellHtml(c.score)}</td>`).join('');
    const verdictRow = countries.map(c => `<td class="ect-cell">${verdictCellHtml(c)}</td>`).join('');
    const reasonRow = countries.map(c => `<td class="ect-cell">${reasonCellHtml(c)}</td>`).join('');
    const riskRow = countries.map(c => `<td class="ect-cell">${riskCellHtml(c)}</td>`).join('');
    const suggestionRow = countries.map(c => `<td class="ect-cell">${suggestionsCellHtml(c)}</td>`).join('');

    return `${summaryHtml(detail, countries)}
      <div class="ect-scroll">
        <table class="ect-table">
          <thead><tr><th class="ect-row-label">国家</th>${headerCells}</tr></thead>
          <tbody>
            <tr><th class="ect-row-label">AI 评分</th>${scoreRow}</tr>
            <tr><th class="ect-row-label">评估结果</th>${verdictRow}</tr>
            <tr><th class="ect-row-label">详细说明</th>${reasonRow}</tr>
            <tr><th class="ect-row-label">风险等级</th>${riskRow}</tr>
            <tr><th class="ect-row-label">建议</th>${suggestionRow}</tr>
          </tbody>
        </table>
      </div>`;
  }

  window.EvalCountryTable = { render: render, parse: parse };
})();
