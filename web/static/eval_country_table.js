/* AI 评估详情可视化表格（素材管理 / 推送管理共享）
 *
 * 用法：
 *   const html = window.EvalCountryTable.render(detailRawOrObj);
 *   const detail = window.EvalCountryTable.parse(detailRawOrObj);
 *   if (detail && detail.countries && detail.countries.length) { ... 表格 ... }
 *   else { ... fallback 到原 JSON pre ... }
 *
 * 数据契约（见 appcore/material_evaluation.py）：
 *   detail = {
 *     schema_version, use_case, evaluated_at,
 *     product_id, product_url,
 *     cover_object_key, video_item_id, video_object_key,
 *     ai_score, ai_evaluation_result,   // 顶部摘要会用，缺失则回退
 *     countries: [{ lang, language, country, is_suitable, score, risk_level, decision, reason, suggestions }]
 *   }
 *
 * 信息分层：
 *   Tier 1 摘要条：综合评分、国家数、整体结论 tag、评估时间、商品链接
 *   Tier 2 主表 3 行：AI 评分、评估结果、详细说明
 *   Tier 3 可折叠：风险等级 + 建议（同样按国家分列）
 *   Tier 4 可折叠：评估元信息（时间、链接、object key、video item id 等）
 */
(function () {
  if (window.EvalCountryTable) return;

  const STYLE_ID = 'eval-country-table-style';
  const CSS = `
    .ect-empty { padding: var(--oc-sp-5, 20px); color: var(--oc-fg-subtle, #94a3b8); text-align: center; font-size: 13px; }

    .ect-summary { display:flex; align-items:center; justify-content:space-between; gap: var(--oc-sp-4, 16px); padding: var(--oc-sp-3, 12px) var(--oc-sp-4, 16px); border:1px solid var(--oc-border); border-radius: var(--oc-r-md, 8px); background: var(--oc-bg-subtle); margin-bottom: var(--oc-sp-4, 16px); flex-wrap: wrap; }
    .ect-summary-left { display:flex; align-items:baseline; gap: var(--oc-sp-3, 12px); flex-wrap: wrap; }
    .ect-summary-right { display:flex; align-items:center; gap: var(--oc-sp-3, 12px); flex-wrap: wrap; }
    .ect-summary-label { color: var(--oc-fg-muted); font-size: 12px; }
    .ect-summary-score { font-family: var(--font-mono, "JetBrains Mono", ui-monospace, Consolas, monospace); font-size: 24px; font-weight: 600; color: var(--oc-fg); font-variant-numeric: tabular-nums; line-height: 1; }
    .ect-summary-score-suffix { font-size: 13px; color: var(--oc-fg-subtle); margin-left: 2px; font-weight: 400; }
    .ect-summary-tag { display: inline-flex; align-items: center; padding: 4px 12px; border-radius: var(--oc-r-full, 9999px); font-size: 12px; font-weight: 500; }
    .ect-tag-success { background: var(--oc-success-bg); color: var(--oc-success-fg); }
    .ect-tag-warning { background: var(--oc-warning-bg); color: var(--oc-warning-fg); }
    .ect-tag-danger  { background: var(--oc-danger-bg);  color: var(--oc-danger-fg); }
    .ect-tag-info    { background: var(--oc-accent-subtle); color: var(--oc-accent); }
    .ect-evaluated-at { color: var(--oc-fg-muted); font-size: 12px; cursor: help; }
    .ect-link-btn { display:inline-flex; align-items:center; gap:6px; padding: 4px 10px; border:1px solid var(--oc-border-strong); border-radius: var(--oc-r-md, 8px); color: var(--oc-fg-muted); font-size: 12px; text-decoration: none; line-height: 1.4; background: var(--oc-bg); transition: color var(--oc-dur-fast, 120ms) var(--oc-ease, ease), border-color var(--oc-dur-fast, 120ms) var(--oc-ease, ease); }
    .ect-link-btn:hover { color: var(--oc-accent); border-color: var(--oc-accent); }
    .ect-link-btn:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--oc-accent-ring, oklch(56% 0.16 230 / 0.22)); }

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
    .ect-thead-language { font-size: 11px; color: var(--oc-fg-subtle); font-weight: 400; }

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

    .ect-collapsible { margin-top: var(--oc-sp-4, 16px); border: 1px solid var(--oc-border); border-radius: var(--oc-r-md, 8px); background: var(--oc-bg); }
    .ect-collapsible > summary { list-style: none; padding: var(--oc-sp-3, 12px) var(--oc-sp-4, 16px); cursor: pointer; display: flex; align-items: center; gap: var(--oc-sp-2, 8px); color: var(--oc-fg-muted); font-size: 13px; font-weight: 500; user-select: none; transition: color var(--oc-dur-fast, 120ms) var(--oc-ease, ease), background var(--oc-dur-fast, 120ms) var(--oc-ease, ease); border-radius: var(--oc-r-md, 8px); }
    .ect-collapsible > summary::-webkit-details-marker { display: none; }
    .ect-collapsible > summary::before { content: ''; display: inline-block; width: 0; height: 0; border-left: 5px solid currentColor; border-top: 4px solid transparent; border-bottom: 4px solid transparent; transition: transform var(--oc-dur, 180ms) var(--oc-ease-out, ease-out); flex-shrink: 0; }
    .ect-collapsible[open] > summary::before { transform: rotate(90deg); }
    .ect-collapsible > summary:hover { color: var(--oc-accent); background: var(--oc-bg-subtle); }
    .ect-collapsible[open] > summary { border-bottom: 1px solid var(--oc-border); border-radius: var(--oc-r-md, 8px) var(--oc-r-md, 8px) 0 0; color: var(--oc-fg); }
    .ect-collapsible-body { padding: var(--oc-sp-4, 16px); }
    .ect-collapsible-body .ect-scroll { border-radius: var(--oc-r, 6px); }
    .ect-summary-count { font-size: 11px; color: var(--oc-fg-subtle); font-weight: 400; margin-left: auto; }

    .ect-meta-list { display: grid; grid-template-columns: 140px 1fr; gap: 10px var(--oc-sp-4, 16px); margin: 0; font-size: 13px; align-items: start; }
    .ect-meta-list dt { color: var(--oc-fg-muted); font-weight: 500; }
    .ect-meta-list dd { margin: 0; color: var(--oc-fg); word-break: break-all; line-height: 1.55; }
    .ect-meta-list dd code { font-family: var(--font-mono, "JetBrains Mono", ui-monospace, Consolas, monospace); font-size: 12px; background: var(--oc-bg-subtle); border: 1px solid var(--oc-border); border-radius: var(--oc-r-sm, 4px); padding: 1px 6px; color: var(--oc-fg); }
    .ect-meta-list dd a { color: var(--oc-accent); text-decoration: none; word-break: break-all; }
    .ect-meta-list dd a:hover { text-decoration: underline; }
    .ect-meta-list dd.ect-muted { color: var(--oc-fg-subtle); }

    @media (max-width: 768px) {
      .ect-meta-list { grid-template-columns: 100px 1fr; }
    }
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

  function externalLinkSvg() {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
  }

  function parseDate(value) {
    if (!value) return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatRelative(value) {
    const d = parseDate(value);
    if (!d) return '';
    const diffMs = Date.now() - d.getTime();
    const sec = Math.round(diffMs / 1000);
    if (sec < 0) return '刚刚';
    if (sec < 30) return '刚刚';
    if (sec < 60) return `${sec} 秒前`;
    const min = Math.round(sec / 60);
    if (min < 60) return `${min} 分钟前`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr} 小时前`;
    const day = Math.round(hr / 24);
    if (day < 30) return `${day} 天前`;
    return formatAbsolute(d);
  }

  function formatAbsolute(value) {
    const d = parseDate(value);
    if (!d) return '';
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
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

  function evaluatedAtHtml(detail) {
    const raw = detail && detail.evaluated_at;
    const d = parseDate(raw);
    if (!d) return '';
    const rel = formatRelative(d);
    const abs = formatAbsolute(d);
    return `<span class="ect-evaluated-at" title="${escapeHtml(abs)}（原始：${escapeHtml(raw)}）">${escapeHtml(rel)}评估</span>`;
  }

  function productLinkHtml(detail) {
    const url = String((detail && detail.product_url) || '').trim();
    if (!url) return '';
    return `<a class="ect-link-btn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${externalLinkSvg()}<span>商品链接</span></a>`;
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
    const rightParts = [productLinkHtml(detail), evaluatedAtHtml(detail), summaryTagHtml(detail)].filter(Boolean);
    return `<div class="ect-summary">
      <div class="ect-summary-left">
        <span class="ect-summary-label">综合评分</span>
        ${avgHtml}
        <span class="ect-summary-label">·</span>
        <span class="ect-summary-label">${escapeHtml(countLabel)}</span>
      </div>
      <div class="ect-summary-right">${rightParts.join('')}</div>
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

  function buildHeaderCells(countries) {
    return countries.map(c => {
      const country = String(c.country || c.language || c.lang || '—').trim();
      const lang = String(c.lang || '').trim();
      const language = String(c.language || '').trim();
      const showLanguage = !!language && language !== country && language.toLowerCase() !== lang.toLowerCase();
      let subline = '';
      if (lang && showLanguage) {
        subline = `<span class="ect-thead-lang">${escapeHtml(lang)} · <span class="ect-thead-language">${escapeHtml(language)}</span></span>`;
      } else if (lang) {
        subline = `<span class="ect-thead-lang">${escapeHtml(lang)}</span>`;
      } else if (showLanguage) {
        subline = `<span class="ect-thead-language">${escapeHtml(language)}</span>`;
      }
      return `<th class="ect-cell"><div class="ect-thead-country"><span class="ect-thead-name">${escapeHtml(country)}</span>${subline}</div></th>`;
    }).join('');
  }

  function mainTableHtml(countries) {
    const headerCells = buildHeaderCells(countries);
    const scoreRow = countries.map(c => `<td class="ect-cell">${scoreCellHtml(c.score)}</td>`).join('');
    const verdictRow = countries.map(c => `<td class="ect-cell">${verdictCellHtml(c)}</td>`).join('');
    const reasonRow = countries.map(c => `<td class="ect-cell">${reasonCellHtml(c)}</td>`).join('');
    return `<div class="ect-scroll">
      <table class="ect-table">
        <thead><tr><th class="ect-row-label">国家</th>${headerCells}</tr></thead>
        <tbody>
          <tr><th class="ect-row-label">AI 评分</th>${scoreRow}</tr>
          <tr><th class="ect-row-label">评估结果</th>${verdictRow}</tr>
          <tr><th class="ect-row-label">详细说明</th>${reasonRow}</tr>
        </tbody>
      </table>
    </div>`;
  }

  function extraTableHtml(countries) {
    const headerCells = buildHeaderCells(countries);
    const riskRow = countries.map(c => `<td class="ect-cell">${riskCellHtml(c)}</td>`).join('');
    const suggestionRow = countries.map(c => `<td class="ect-cell">${suggestionsCellHtml(c)}</td>`).join('');
    return `<div class="ect-scroll">
      <table class="ect-table">
        <thead><tr><th class="ect-row-label">国家</th>${headerCells}</tr></thead>
        <tbody>
          <tr><th class="ect-row-label">风险等级</th>${riskRow}</tr>
          <tr><th class="ect-row-label">建议</th>${suggestionRow}</tr>
        </tbody>
      </table>
    </div>`;
  }

  function extraSectionHtml(countries) {
    const totalSuggestions = countries.reduce((sum, c) => sum + (Array.isArray(c.suggestions) ? c.suggestions.filter(s => String(s || '').trim()).length : 0), 0);
    const note = totalSuggestions > 0 ? `（含 ${totalSuggestions} 条建议）` : '';
    return `<details class="ect-collapsible" open>
      <summary>风险等级与建议${note ? `<span class="ect-summary-count">${escapeHtml(note)}</span>` : ''}</summary>
      <div class="ect-collapsible-body">${extraTableHtml(countries)}</div>
    </details>`;
  }

  function metaSectionHtml(detail) {
    const items = [];
    const evaluatedAt = parseDate(detail.evaluated_at);
    if (evaluatedAt) {
      const rel = formatRelative(evaluatedAt);
      const abs = formatAbsolute(evaluatedAt);
      items.push({ label: '评估时间', value: `${escapeHtml(abs)} <span style="color:var(--oc-fg-subtle)">（${escapeHtml(rel)}）</span>` });
    }
    const productUrl = String(detail.product_url || '').trim();
    if (productUrl) {
      items.push({ label: '商品链接', value: `<a href="${escapeHtml(productUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(productUrl)}</a>` });
    }
    const cover = String(detail.cover_object_key || '').trim();
    if (cover) items.push({ label: '主图 object key', value: `<code>${escapeHtml(cover)}</code>` });
    const video = String(detail.video_object_key || '').trim();
    if (video) items.push({ label: '视频 object key', value: `<code>${escapeHtml(video)}</code>` });
    const videoItemId = detail.video_item_id;
    if (videoItemId != null && videoItemId !== '') items.push({ label: '视频 item ID', value: `<code>${escapeHtml(videoItemId)}</code>` });
    const useCase = String(detail.use_case || '').trim();
    if (useCase) items.push({ label: 'Use case', value: `<code>${escapeHtml(useCase)}</code>` });
    const schemaVersion = detail.schema_version;
    if (schemaVersion != null && schemaVersion !== '') items.push({ label: 'Schema version', value: `<code>${escapeHtml(schemaVersion)}</code>` });

    if (!items.length) return '';
    const dlHtml = items.map(it => `<dt>${escapeHtml(it.label)}</dt><dd>${it.value}</dd>`).join('');
    return `<details class="ect-collapsible">
      <summary>评估元信息<span class="ect-summary-count">（${items.length} 项）</span></summary>
      <div class="ect-collapsible-body">
        <dl class="ect-meta-list">${dlHtml}</dl>
      </div>
    </details>`;
  }

  function render(rawDetail) {
    ensureStyle();
    const detail = parse(rawDetail);
    const countries = extractCountries(detail);
    if (!countries.length) {
      return `<div class="ect-empty">暂无评估详情</div>`;
    }
    return [
      summaryHtml(detail, countries),
      mainTableHtml(countries),
      extraSectionHtml(countries),
      metaSectionHtml(detail),
    ].filter(Boolean).join('');
  }

  window.EvalCountryTable = { render: render, parse: parse };
})();
