// AI 视频分析 — 任务页 step-analysis 区域的状态展示 + Modal。
// 依赖现有 #runAnalysisBtn / #step-analysis / #msg-analysis / #preview-analysis。
// 通过 window.VideoAiReview.init({ taskId, projectType, isAdmin }) 启动。

window.VideoAiReview = (function () {
  const VERDICT_LABEL = {
    recommend: "建议采用",
    usable_with_minor_issues: "可用 · 小瑕疵",
    needs_review: "需复核",
    recommend_redo: "建议重做",
  };
  const VERDICT_TIER = {
    recommend: "tier-top",
    usable_with_minor_issues: "tier-good",
    needs_review: "tier-mid",
    recommend_redo: "tier-low",
  };
  const DIM_LABELS = {
    translation_fidelity:  "翻译忠实度",
    naturalness:           "自然度",
    tts_consistency:       "TTS 一致性",
    visual_text_alignment: "画面契合度",
    product_alignment:     "产品契合度",
  };

  let _state = { taskId: null, isAdmin: false, latest: null, polling: null };

  function _api(path) {
    return `/api/multi-translate/${_state.taskId}${path}`;
  }

  function _csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function _esc(s) {
    return String(s == null ? "" : s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function _fmtBytes(n) {
    if (!n || n <= 0) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  }

  function _fmtElapsed(ms) {
    if (!ms || ms <= 0) return "—";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(1) + " s";
  }

  function init({ taskId, isAdmin }) {
    _state.taskId = taskId;
    _state.isAdmin = !!isAdmin;
    _patchRunButton();
    _ensureDetailButton();
    _ensureModalShell();
    _refresh();
    if (_state.polling) clearInterval(_state.polling);
    _state.polling = setInterval(_refresh, 8000);
  }

  function _patchRunButton() {
    const btn = document.getElementById("runAnalysisBtn");
    if (!btn || btn.dataset.vrPatched === "1") return;
    btn.dataset.vrPatched = "1";
    // 抢先克隆移除旧事件（_task_workbench_scripts.html 里挂的旧 placeholder API）
    const fresh = btn.cloneNode(true);
    btn.parentNode.replaceChild(fresh, btn);
    fresh.addEventListener("click", _triggerRun);
  }

  function _ensureDetailButton() {
    const row = document.querySelector("#step-analysis .step-name-row");
    if (!row) return;
    if (document.getElementById("vrDetailBtn")) return;
    const a = document.createElement("button");
    a.id = "vrDetailBtn";
    a.type = "button";
    a.className = "btn btn-secondary btn-sm";
    a.style.marginLeft = "8px";
    a.textContent = "AI 视频分析结果";
    a.addEventListener("click", _openModal);
    row.appendChild(a);
  }

  async function _triggerRun() {
    const btn = document.getElementById("runAnalysisBtn");
    if (btn) { btn.disabled = true; btn.textContent = "启动中…"; }
    try {
      const resp = await fetch(_api("/video-ai-review/run"), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": _csrf() },
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.status === 409) {
        alert("AI 视频分析已经在跑了 (run #" + (data.in_flight_run_id || "?") + ")");
      } else if (!resp.ok) {
        alert("启动失败：" + (data.error || resp.status));
      }
    } catch (err) {
      alert("启动失败：" + err.message);
    } finally {
      _refresh();
      const btn2 = document.getElementById("runAnalysisBtn");
      if (btn2) { btn2.disabled = false; }
    }
  }

  async function _refresh() {
    if (!_state.taskId) return;
    try {
      const resp = await fetch(_api("/video-ai-review"));
      if (!resp.ok) return;
      const data = await resp.json();
      _state.latest = data.review || null;
      _renderInline();
      _renderModal();
    } catch (e) { /* swallow */ }
  }

  function _renderInline() {
    const r = _state.latest;
    const msg = document.getElementById("msg-analysis");
    const preview = document.getElementById("preview-analysis");
    const btn = document.getElementById("runAnalysisBtn");
    if (!msg) return;
    if (!r) {
      msg.textContent = "尚未运行（点击「运行 AI 分析」开始）";
      if (preview) preview.innerHTML = "";
      if (btn) btn.textContent = "运行 AI 分析";
      return;
    }
    if (r.status === "pending" || r.status === "running") {
      msg.textContent = `分析中… run #${r.run_id} · ${r.channel || ""} · ${r.model || ""}`;
      if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }
      if (preview) preview.innerHTML = "";
      return;
    }
    if (btn) { btn.disabled = false; btn.textContent = "重新分析"; }
    if (r.status === "failed") {
      msg.textContent = `失败：${(r.error_text || "").slice(0, 120)}`;
      if (preview) preview.innerHTML = "";
      return;
    }
    // done
    const verdictText = VERDICT_LABEL[r.verdict] || r.verdict || "";
    const tier = VERDICT_TIER[r.verdict] || "";
    msg.innerHTML = `综合分 <b>${r.overall_score ?? "—"}</b> · ${_esc(verdictText)} · ${_fmtElapsed(r.request_duration_ms)} · ${_esc(r.channel || "")}`;
    if (preview) {
      const dims = (r.dimensions || {});
      const dimRows = Object.keys(DIM_LABELS).map(k => {
        const v = dims[k];
        if (v == null) return "";
        return `<div class="vr-dim"><span class="vr-dim-label">${DIM_LABELS[k]}</span><span class="vr-dim-score">${v}</span></div>`;
      }).filter(Boolean).join("");
      const reason = r.verdict_reason ? `<div class="vr-reason">${_esc(r.verdict_reason)}</div>` : "";
      preview.innerHTML = `<div class="vr-inline ${tier}">${dimRows}${reason}</div>`;
    }
  }

  function _ensureModalShell() {
    if (document.getElementById("vrModal")) return;
    const el = document.createElement("div");
    el.id = "vrModal";
    el.className = "vr-modal hidden";
    el.innerHTML = `
      <div class="vr-modal-backdrop" data-close="1"></div>
      <div class="vr-modal-panel">
        <div class="vr-modal-header">
          <h3>AI 视频分析结果</h3>
          <button type="button" class="vr-modal-close" data-close="1" aria-label="关闭">×</button>
        </div>
        <div class="vr-modal-body" id="vrModalBody">
          <div class="vr-empty">加载中…</div>
        </div>
      </div>
    `;
    document.body.appendChild(el);
    el.addEventListener("click", (ev) => {
      if (ev.target.dataset.close === "1") _closeModal();
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") _closeModal();
    });
  }

  function _openModal() {
    const m = document.getElementById("vrModal");
    if (m) m.classList.remove("hidden");
    _renderModal();
  }
  function _closeModal() {
    const m = document.getElementById("vrModal");
    if (m) m.classList.add("hidden");
  }

  function _renderModal() {
    const body = document.getElementById("vrModalBody");
    if (!body) return;
    const r = _state.latest;
    if (!r) {
      body.innerHTML = `<div class="vr-empty">尚无运行记录</div>`;
      return;
    }
    const verdictText = VERDICT_LABEL[r.verdict] || r.verdict || "";
    const tier = VERDICT_TIER[r.verdict] || "";
    const inputs = r.submitted_inputs || {};
    const submittedFile = (m) => m
      ? `${_esc(m.name)} <span class="vr-meta">${_fmtBytes(m.size_bytes)}</span>`
      : `<span class="vr-meta">—</span>`;
    const dims = r.dimensions || {};
    const dimRows = Object.keys(DIM_LABELS).map(k => {
      const v = dims[k];
      const display = v == null ? "<span class='vr-meta'>跳过</span>" : v;
      return `<tr><td>${DIM_LABELS[k]}</td><td>${display}</td></tr>`;
    }).join("");
    const issuesHtml = (r.issues || []).map(s => `<li>${_esc(s)}</li>`).join("") || "<li class='vr-meta'>—</li>";
    const highlightsHtml = (r.highlights || []).map(s => `<li>${_esc(s)}</li>`).join("") || "<li class='vr-meta'>—</li>";
    const promptShort = (r.prompt_text || "").slice(0, 4000);
    body.innerHTML = `
      <div class="vr-row">
        <div class="vr-kv"><span class="vr-k">状态</span><span class="vr-v">${_esc(r.status)}</span></div>
        <div class="vr-kv"><span class="vr-k">通道</span><span class="vr-v">${_esc(r.channel || "")}</span></div>
        <div class="vr-kv"><span class="vr-k">模型</span><span class="vr-v">${_esc(r.model || "")}</span></div>
        <div class="vr-kv"><span class="vr-k">耗时</span><span class="vr-v">${_fmtElapsed(r.request_duration_ms)}</span></div>
        <div class="vr-kv"><span class="vr-k">触发</span><span class="vr-v">${_esc(r.triggered_by || "")} · run #${r.run_id}</span></div>
      </div>
      ${r.status === "done" ? `
        <div class="vr-row vr-summary ${tier}">
          <div class="vr-score-big">${r.overall_score ?? "—"}</div>
          <div>
            <div class="vr-verdict-text">${_esc(verdictText)}</div>
            <div class="vr-meta">${_esc(r.verdict_reason || "")}</div>
          </div>
        </div>` : ""}
      <h4>提交资料</h4>
      <div class="vr-inputs">
        <div class="vr-kv"><span class="vr-k">源语言</span><span class="vr-v">${_esc(inputs.source_language || "—")}</span></div>
        <div class="vr-kv"><span class="vr-k">目标语言</span><span class="vr-v">${_esc(inputs.target_language || "—")}</span></div>
        <div class="vr-kv"><span class="vr-k">源视频</span><span class="vr-v">${submittedFile(inputs.source_video)}</span></div>
        <div class="vr-kv"><span class="vr-k">目标视频</span><span class="vr-v">${submittedFile(inputs.target_video)}</span></div>
      </div>
      <details class="vr-details"><summary>源文案 (${(inputs.source_text || "").length} 字)</summary><pre class="vr-pre">${_esc(inputs.source_text || "")}</pre></details>
      <details class="vr-details"><summary>目标文案 (${(inputs.target_text || "").length} 字)</summary><pre class="vr-pre">${_esc(inputs.target_text || "")}</pre></details>
      ${r.status === "done" ? `
        <h4>各维度评分</h4>
        <table class="vr-table"><tbody>${dimRows}</tbody></table>
        <div class="vr-cols">
          <div><h4>问题</h4><ul class="vr-list">${issuesHtml}</ul></div>
          <div><h4>亮点</h4><ul class="vr-list">${highlightsHtml}</ul></div>
        </div>` : ""}
      ${r.status === "failed" ? `
        <h4>错误</h4>
        <pre class="vr-pre vr-error">${_esc(r.error_text || "")}</pre>` : ""}
      <details class="vr-details"><summary>提示词 / Prompt</summary><pre class="vr-pre">${_esc(promptShort)}</pre></details>
    `;
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", function () {
  var node = document.getElementById("step-analysis");
  if (!node) return;
  var taskId = node.dataset.taskId;
  if (!taskId) return;
  window.VideoAiReview.init({
    taskId: taskId,
    isAdmin: node.dataset.isAdmin === "1",
  });
});
