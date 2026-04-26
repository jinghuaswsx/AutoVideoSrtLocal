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
    usable_with_minor_issues: "可用 (有小瑕疵)",
    needs_review: "需要复核",
    recommend_redo: "建议重做",
  };

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

  function render(root, list) {
    const isAdmin = root.dataset.isAdmin === "1";
    const latest = list[0];
    const body = root.querySelector(".qa-body");
    if (!latest) {
      body.innerHTML = `<div class="qa-empty">尚无评估记录${isAdmin ? "（点「重跑」生成）" : ""}</div>`;
      return;
    }
    if (latest.status === "pending" || latest.status === "running") {
      body.innerHTML = `<div class="qa-loading">评估中… (run #${latest.run_id})</div>`;
      return;
    }
    if (latest.status === "failed") {
      body.innerHTML = `
        <div class="qa-failed">
          <div class="qa-error-text">评估失败：${escapeHtml(latest.error_text || "")}</div>
        </div>`;
      return;
    }
    const ts = latest.translation_score || 0;
    const ttsS = latest.tts_score || 0;
    const verdictClass = VERDICT_CLASS[latest.verdict] || "";
    const verdictText = VERDICT_LABEL[latest.verdict] || latest.verdict || "";
    body.innerHTML = `
      <div class="qa-scores">
        <div class="qa-ring qa-ring-translation" style="--score:${ts}">
          <div class="qa-ring-inner"><div class="qa-ring-num">${ts}</div><div class="qa-ring-label">翻译质量</div></div>
        </div>
        <div class="qa-ring qa-ring-tts" style="--score:${ttsS}">
          <div class="qa-ring-inner"><div class="qa-ring-num">${ttsS}</div><div class="qa-ring-label">TTS 还原度</div></div>
        </div>
      </div>
      <div class="qa-verdict ${verdictClass}">${verdictText}</div>
      <div class="qa-reason">${escapeHtml(latest.verdict_reason || "")}</div>
      ${renderDimensions("翻译细分", latest.translation_dimensions)}
      ${renderDimensions("TTS 细分", latest.tts_dimensions)}
      ${renderList("翻译问题", latest.translation_issues, "qa-issues")}
      ${renderList("翻译亮点", latest.translation_highlights, "qa-highlights")}
      ${renderList("TTS 问题", latest.tts_issues, "qa-issues")}
      ${renderList("TTS 亮点", latest.tts_highlights, "qa-highlights")}
      ${list.length > 1 ? `<div class="qa-history">历史评估 ${list.length} 次（最新 run #${latest.run_id}）</div>` : ""}
    `;
  }

  function renderDimensions(title, dims) {
    if (!dims || typeof dims !== "object") return "";
    const items = Object.entries(dims).map(([k, v]) =>
      `<li><span class="qa-dim-label">${k}</span><span class="qa-dim-bar"><span class="qa-dim-fill" style="width:${v}%"></span></span><span class="qa-dim-value">${v}</span></li>`
    ).join("");
    return `<div class="qa-dimensions"><div class="qa-dim-title">${title}</div><ul>${items}</ul></div>`;
  }

  function renderList(title, items, className) {
    if (!items || !items.length) return "";
    const lis = items.slice(0, 3).map(s => `<li>${escapeHtml(s)}</li>`).join("");
    return `<div class="${className}"><div class="qa-list-title">${title}</div><ul>${lis}</ul></div>`;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }

  return { init };
})();
