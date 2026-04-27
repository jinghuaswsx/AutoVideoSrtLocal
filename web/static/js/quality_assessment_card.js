// Translation Quality Assessment card.
// Initialise via: QualityAssessmentCard.init({ taskId, projectType, isAdmin })

window.QualityAssessmentCard = (function () {
  const VERDICT_CLASS = {
    recommend: "verdict-recommend",
    usable_with_minor_issues: "verdict-usable",
    needs_review: "verdict-needs-review",
    recommend_redo: "verdict-redo",
  };
  const VERDICT_LABEL = {
    recommend: "建议采用",
    usable_with_minor_issues: "可用（有小瑕疵）",
    needs_review: "需要复核",
    recommend_redo: "建议重做",
  };
  const DIM_LABELS = {
    completeness: "完整度",
    naturalness: "自然度",
    semantic_fidelity: "语义忠实度",
    pronunciation_fidelity: "发音准确度",
    rhythm_match: "节奏契合度",
    text_recall: "文本召回",
    accuracy: "准确度",
    fluency: "流畅度",
    style_match: "风格契合度",
  };
  const ICON_ISSUE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
  const ICON_HIGHLIGHT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

  function init({ taskId, projectType, isAdmin }) {
    const root = document.getElementById("quality-assessment-card");
    if (!root) return;
    root.dataset.taskId = taskId;
    root.dataset.projectType = projectType;
    root.dataset.isAdmin = isAdmin ? "1" : "0";
    refresh(root);
    setInterval(() => refresh(root), 8000);
    const btn = root.querySelector("[data-action='rerun']");
    if (btn) btn.addEventListener("click", () => triggerRun(root));
  }

  async function refresh(root) {
    const { taskId, projectType } = root.dataset;
    const apiBase = projectType === "omni_translate" ? "/api/omni-translate" : "/api/multi-translate";
    try {
      const resp = await fetch(`${apiBase}/${taskId}/quality-assessments`);
      if (!resp.ok) return;
      const data = await resp.json();
      render(root, data.assessments || []);
    } catch (err) { /* swallow */ }
  }

  async function triggerRun(root) {
    const { taskId, projectType } = root.dataset;
    const apiBase = projectType === "omni_translate" ? "/api/omni-translate" : "/api/multi-translate";
    const resp = await fetch(`${apiBase}/${taskId}/quality-assessments/run`, { method: "POST" });
    if (resp.status === 409) {
      alert("评估已经在跑");
      return;
    }
    if (!resp.ok) {
      alert("触发失败：" + (await resp.text()));
      return;
    }
    refresh(root);
  }

  function tierByScore(score) {
    const n = Number(score) || 0;
    if (n >= 90) return "tier-top";
    if (n >= 75) return "tier-good";
    if (n >= 60) return "tier-mid";
    return "tier-low";
  }

  function render(root, list) {
    const isAdmin = root.dataset.isAdmin === "1";
    const latest = list[0];
    const body = root.querySelector(".qa-body");
    if (!latest) {
      body.innerHTML = `<div class="qa-empty">尚无评估记录${isAdmin ? "（点「重跑评估」生成）" : ""}</div>`;
      return;
    }
    if (latest.status === "pending" || latest.status === "running") {
      body.innerHTML = `<div class="qa-loading">评估中… (run #${latest.run_id})</div>`;
      return;
    }
    if (latest.status === "failed") {
      body.innerHTML = `<div class="qa-failed">评估失败：${escapeHtml(latest.error_text || "")}</div>`;
      return;
    }
    const ts = Number(latest.translation_score) || 0;
    const ttsS = Number(latest.tts_score) || 0;
    const verdictClass = VERDICT_CLASS[latest.verdict] || "";
    const verdictText = VERDICT_LABEL[latest.verdict] || latest.verdict || "";
    const reasonText = latest.verdict_reason || "";

    const summaryRow = (verdictText || reasonText) ? `
      <div class="qa-summary-row">
        ${verdictText ? `<span class="qa-verdict-pill ${verdictClass}">${escapeHtml(verdictText)}</span>` : ""}
        ${reasonText ? `<span class="qa-summary-text">${escapeHtml(reasonText)}</span>` : ""}
      </div>` : "";

    const scoreGrid = `
      <div class="qa-score-grid">
        ${renderScoreBlock("翻译质量", ts, latest.translation_dimensions)}
        ${renderScoreBlock("TTS 还原度", ttsS, latest.tts_dimensions)}
      </div>`;

    const lists = [
      renderList("翻译问题", latest.translation_issues, "issue"),
      renderList("翻译亮点", latest.translation_highlights, "highlight"),
      renderList("TTS 问题", latest.tts_issues, "issue"),
      renderList("TTS 亮点", latest.tts_highlights, "highlight"),
    ].filter(Boolean).join("");
    const listsGrid = lists ? `<div class="qa-lists-grid">${lists}</div>` : "";

    const history = list.length > 1
      ? `<div class="qa-history">历史评估 ${list.length} 次 · 最新 run #${latest.run_id}</div>`
      : "";

    body.innerHTML = summaryRow + scoreGrid + listsGrid + history;
  }

  function renderScoreBlock(name, score, dims) {
    const tier = tierByScore(score);
    return `
      <div class="qa-score-block ${tier}">
        <div class="qa-score-head">
          <span class="qa-score-name">${escapeHtml(name)}</span>
          <span class="qa-score-num">${score}<span class="qa-score-max">/100</span></span>
        </div>
        ${renderDims(dims)}
      </div>`;
  }

  function renderDims(dims) {
    if (!dims || typeof dims !== "object") return "";
    const rows = Object.entries(dims).map(([k, v]) => {
      const label = DIM_LABELS[k] || k;
      const val = Math.max(0, Math.min(100, Number(v) || 0));
      return `
        <div class="qa-dim-row">
          <div class="qa-dim-head">
            <span class="qa-dim-name">${escapeHtml(label)}</span>
            <span class="qa-dim-val">${val}</span>
          </div>
          <div class="qa-dim-bar"><span style="width:${val}%"></span></div>
        </div>`;
    }).join("");
    return rows ? `<div class="qa-dims">${rows}</div>` : "";
  }

  function renderList(title, items, kind) {
    if (!items || !items.length) return "";
    const icon = kind === "issue" ? ICON_ISSUE : ICON_HIGHLIGHT;
    const lis = items.slice(0, 5).map(s => `<li>${escapeHtml(s)}</li>`).join("");
    return `
      <div class="qa-list-card qa-list-card--${kind}">
        <div class="qa-list-card__title">${icon}<span>${escapeHtml(title)}</span></div>
        <ul>${lis}</ul>
      </div>`;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }

  return { init };
})();

// 自动初始化：把 #quality-assessment-card 的 data-* 属性映射到 init() 参数。
// 由模板（_task_workbench.html 等）渲染好 div 即可生效；外部仍可用
// window.QualityAssessmentCard.init() 显式初始化（向后兼容）。
(function () {
  function autoInit() {
    var card = document.getElementById("quality-assessment-card");
    if (!card) return;
    if (card.dataset.qaInitialized === "1") return;
    var taskId = card.dataset.taskId;
    if (!taskId) return;
    window.QualityAssessmentCard.init({
      taskId: taskId,
      projectType: card.dataset.projectType || "omni_translate",
      isAdmin: card.dataset.isAdmin === "1",
    });
    card.dataset.qaInitialized = "1";
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    autoInit();
  }
})();
