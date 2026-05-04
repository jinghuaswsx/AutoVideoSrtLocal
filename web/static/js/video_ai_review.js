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

  let _state = {
    taskId: null,
    isAdmin: false,
    projectType: "multi_translate",
    latest: null,
    polling: null,        // 后台 8s 轮询（卡片状态）
    modalPoll: null,      // Modal 打开期间 2.5s 轮询
    ticker: null,         // Modal "已耗时" 1s 跳秒
    activeTab: "request",
    refreshFn: null,      // 当前应该被 modalPoll 调用的 refresh 函数
  };

  // 三个翻译型详情页（multi / omni / av_sync）共用同一套 service / DB 表，
  // 但 API base 不同：multi → /api/multi-translate；omni → /api/omni-translate；
  // av_sync → /api/tasks（task.py 蓝图）。projectType 由模板按 request.path
  // 推断后写到 #step-analysis data-project-type。
  function _apiBase() {
    switch (_state.projectType) {
      case "omni_translate": return "/api/omni-translate";
      case "av_sync":        return "/api/tasks";
      default:               return "/api/multi-translate";
    }
  }

  function _api(path) {
    return `${_apiBase()}/${_state.taskId}${path}`;
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

  function init({ taskId, isAdmin, projectType }) {
    _state.taskId = taskId;
    _state.isAdmin = !!isAdmin;
    _state.projectType = projectType || "multi_translate";
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
    // 先把 Modal 弹出来，让用户立刻看到状态栏。
    _state.refreshFn = _refresh;
    _openModal();
    try {
      const resp = await fetch(_api("/video-ai-review/run"), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": _csrf() },
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.status === 409) {
        // 已经在跑——不弹 alert，刷新一次让 Modal 反映 in-flight run。
        await _refresh();
      } else if (!resp.ok) {
        alert("启动失败：" + (data.error || resp.status));
      }
    } catch (err) {
      alert("启动失败：" + err.message);
    } finally {
      await _refresh();
      _startModalPolling(_refresh);
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
          <h3>AI 视频分析</h3>
          <button type="button" class="vr-modal-close" data-close="1" aria-label="关闭">×</button>
        </div>
        <div class="vr-status-bar" id="vrStatusBar">
          <div class="vr-empty">尚未运行</div>
        </div>
        <div class="vr-tabs" role="tablist">
          <button type="button" class="vr-tab active" data-tab="request">请求</button>
          <button type="button" class="vr-tab" data-tab="result">结果</button>
        </div>
        <div class="vr-modal-body" id="vrModalBody">
          <div class="vr-tab-pane active" data-pane="request"></div>
          <div class="vr-tab-pane" data-pane="result"></div>
        </div>
      </div>
    `;
    document.body.appendChild(el);
    el.addEventListener("click", (ev) => {
      if (ev.target.dataset.close === "1") _closeModal();
      const tabBtn = ev.target.closest(".vr-tab");
      if (tabBtn && tabBtn.dataset.tab) _switchTab(tabBtn.dataset.tab);
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !document.getElementById("vrModal").classList.contains("hidden")) {
        _closeModal();
      }
    });
  }

  function _switchTab(tab) {
    _state.activeTab = tab;
    document.querySelectorAll("#vrModal .vr-tab").forEach(b => {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    document.querySelectorAll("#vrModal .vr-tab-pane").forEach(p => {
      p.classList.toggle("active", p.dataset.pane === tab);
    });
  }

  function _openModal() {
    const m = document.getElementById("vrModal");
    if (m) m.classList.remove("hidden");
    _switchTab(_state.activeTab || "request");
    _renderModal();
    _startTicker();
    // 进入 Modal 时，如果当前还没结果或者正在跑，就立刻起 polling。
    const r = _state.latest;
    const fn = _state.refreshFn || _refresh;
    if (!r || r.status === "pending" || r.status === "running") {
      _startModalPolling(fn);
    }
  }

  function _closeModal() {
    const m = document.getElementById("vrModal");
    if (m) m.classList.add("hidden");
    _stopTicker();
    _stopModalPolling();
  }

  // 每秒刷新一次"已耗时"显示——不需要重新 fetch，本地 clock 算就行。
  function _startTicker() {
    if (_state.ticker) return;
    _state.ticker = setInterval(() => _renderStatusBar(), 1000);
  }
  function _stopTicker() {
    if (_state.ticker) { clearInterval(_state.ticker); _state.ticker = null; }
  }

  // 真正 poll 后端拉最新结果，每 2.5s。pending/running 才 poll，done/failed 立即停。
  function _startModalPolling(refreshFn) {
    if (_state.modalPoll) clearInterval(_state.modalPoll);
    _state.modalPoll = setInterval(() => {
      const r = _state.latest;
      if (r && (r.status === "done" || r.status === "failed" || r.status === "cancelled")) {
        _stopModalPolling();
        return;
      }
      refreshFn();
    }, 2500);
  }
  function _stopModalPolling() {
    if (_state.modalPoll) { clearInterval(_state.modalPoll); _state.modalPoll = null; }
  }

  function _elapsedSeconds(r) {
    if (!r) return 0;
    if (r.status === "done" || r.status === "failed" || r.status === "cancelled") {
      if (r.request_duration_ms) return r.request_duration_ms / 1000;
      if (r.started_at && r.completed_at) {
        return (new Date(r.completed_at).getTime() - new Date(r.started_at).getTime()) / 1000;
      }
      return 0;
    }
    // running / pending
    const startStr = r.started_at || r.created_at;
    if (!startStr) return 0;
    return Math.max(0, (Date.now() - new Date(startStr).getTime()) / 1000);
  }

  function _statusChip(r) {
    if (!r) return `<span class="vr-chip vr-chip-idle">尚未运行</span>`;
    const map = {
      pending:   `<span class="vr-chip vr-chip-pending">等待开始</span>`,
      running:   `<span class="vr-chip vr-chip-running">分析中</span>`,
      done:      `<span class="vr-chip vr-chip-done">已完成</span>`,
      failed:    `<span class="vr-chip vr-chip-failed">失败</span>`,
      cancelled: `<span class="vr-chip vr-chip-cancelled">已取消</span>`,
    };
    return map[r.status] || `<span class="vr-chip">${_esc(r.status)}</span>`;
  }

  function _renderStatusBar() {
    const bar = document.getElementById("vrStatusBar");
    if (!bar) return;
    const r = _state.latest;
    if (!r) {
      bar.innerHTML = `<div class="vr-empty">尚未运行 · 点击「AI 视频分析」按钮触发</div>`;
      return;
    }
    const elapsed = _elapsedSeconds(r);
    const elapsedTxt = elapsed >= 1 ? `${elapsed.toFixed(1)}s` : `${Math.round(elapsed * 1000)}ms`;
    const dataReady = (r.status === "done")
      ? `<span class="vr-chip vr-chip-done">结果已生成</span>`
      : (r.status === "failed")
        ? `<span class="vr-chip vr-chip-failed">无结果</span>`
        : `<span class="vr-chip vr-chip-pending">等待结果…</span>`;
    bar.innerHTML = `
      ${_statusChip(r)}
      <span class="vr-meta-pill">耗时 <b>${elapsedTxt}</b></span>
      ${dataReady}
      <span class="vr-meta-pill">通道 ${_esc(r.channel || "—")}</span>
      <span class="vr-meta-pill">模型 ${_esc(r.model || "—")}</span>
      <span class="vr-meta-pill">${_esc(r.triggered_by || "")} · run #${r.run_id}</span>
    `;
  }

  function _renderModal() {
    _renderStatusBar();
    _renderRequestPane();
    _renderResultPane();
  }

  function _renderRequestPane() {
    const pane = document.querySelector('#vrModal .vr-tab-pane[data-pane="request"]');
    if (!pane) return;
    const r = _state.latest;
    if (!r) {
      pane.innerHTML = `<div class="vr-empty">尚无提交记录</div>`;
      return;
    }
    const inputs = r.submitted_inputs || {};
    const fileRow = (m) => m
      ? `${_esc(m.name)} <span class="vr-meta">${_fmtBytes(m.size_bytes)}</span>`
      : `<span class="vr-meta">—</span>`;
    const productInfoText = inputs.product_info
      ? Object.entries(inputs.product_info)
          .map(([k, v]) => `${_esc(k)}: ${_esc(v)}`).join("\n")
      : "";
    pane.innerHTML = `
      <h4>提交资料</h4>
      <div class="vr-inputs">
        <div class="vr-kv"><span class="vr-k">源语言</span><span class="vr-v">${_esc(inputs.source_language || "—")}</span></div>
        <div class="vr-kv"><span class="vr-k">目标语言</span><span class="vr-v">${_esc(inputs.target_language || "—")}</span></div>
        <div class="vr-kv"><span class="vr-k">源视频</span><span class="vr-v">${fileRow(inputs.source_video)}</span></div>
        <div class="vr-kv"><span class="vr-k">目标视频</span><span class="vr-v">${fileRow(inputs.target_video)}</span></div>
      </div>
      <details class="vr-details" open><summary>源文案 (${(inputs.source_text || "").length} 字)</summary><pre class="vr-pre">${_esc(inputs.source_text || "—")}</pre></details>
      <details class="vr-details" open><summary>目标文案 (${(inputs.target_text || "").length} 字)</summary><pre class="vr-pre">${_esc(inputs.target_text || "—")}</pre></details>
      ${productInfoText ? `<details class="vr-details"><summary>产品信息</summary><pre class="vr-pre">${_esc(productInfoText)}</pre></details>` : ""}
      <details class="vr-details"><summary>提示词 / Prompt（system + user 拼接）</summary><pre class="vr-pre">${_esc((r.prompt_text || "").slice(0, 6000))}</pre></details>
    `;
  }

  function _renderResultPane() {
    const pane = document.querySelector('#vrModal .vr-tab-pane[data-pane="result"]');
    if (!pane) return;
    const r = _state.latest;
    if (!r) {
      pane.innerHTML = `<div class="vr-empty">尚无运行记录</div>`;
      return;
    }
    if (r.status === "pending" || r.status === "running") {
      pane.innerHTML = `<div class="vr-empty">分析进行中，结果还没回来…<br><span class="vr-meta">完成后这里会自动出现各维度评分 / verdict / 问题 / 亮点</span></div>`;
      return;
    }
    if (r.status === "failed") {
      pane.innerHTML = `
        <h4>错误</h4>
        <pre class="vr-pre vr-error">${_esc(r.error_text || "")}</pre>
      `;
      return;
    }
    // done
    const verdictText = VERDICT_LABEL[r.verdict] || r.verdict || "";
    const tier = VERDICT_TIER[r.verdict] || "";
    const dims = r.dimensions || {};
    const dimRows = Object.keys(DIM_LABELS).map(k => {
      const v = dims[k];
      const display = v == null ? "<span class='vr-meta'>跳过 / 无数据</span>" : `<b>${v}</b>`;
      return `<tr><td>${DIM_LABELS[k]}</td><td>${display}</td></tr>`;
    }).join("");
    const issuesHtml = (r.issues || []).map(s => `<li>${_esc(s)}</li>`).join("") || "<li class='vr-meta'>—</li>";
    const highlightsHtml = (r.highlights || []).map(s => `<li>${_esc(s)}</li>`).join("") || "<li class='vr-meta'>—</li>";
    pane.innerHTML = `
      <div class="vr-summary ${tier}">
        <div class="vr-score-big">${r.overall_score ?? "—"}</div>
        <div>
          <div class="vr-verdict-text">${_esc(verdictText)}</div>
          <div class="vr-meta">${_esc(r.verdict_reason || "")}</div>
        </div>
      </div>
      <h4>各维度评分</h4>
      <table class="vr-table"><tbody>${dimRows}</tbody></table>
      <div class="vr-cols">
        <div><h4>问题</h4><ul class="vr-list">${issuesHtml}</ul></div>
        <div><h4>亮点</h4><ul class="vr-list">${highlightsHtml}</ul></div>
      </div>
    `;
  }

  // ---- 素材管理编辑页：每个视频卡片底部两个按钮的入口 ----
  // 不走 step-analysis 自动 init，由 medias.js 显式调用。每次调用都用 mediaItemId
  // 临时切换 _state，请求 /medias/api/items/<id>/video-ai-review[/run]，渲染共享
  // 同一个 _renderModal 视图。
  function _setMediaState(mediaItemId) {
    _state.taskId = String(mediaItemId);
    _state.projectType = "media_item";
    _state.isAdmin = false;
  }

  async function triggerForMediaItem(mediaItemId) {
    _setMediaState(mediaItemId);
    _ensureModalShell();
    // 重置上次的结果，避免拿到别的视频的旧数据。
    _state.latest = null;
    _state.refreshFn = () => _refreshMediaItem(mediaItemId);
    _openModal();
    try {
      const resp = await fetch(
        `/medias/api/items/${mediaItemId}/video-ai-review/run`,
        { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": _csrf() } },
      );
      const data = await resp.json().catch(() => ({}));
      if (resp.status === 409) {
        // 已在运行中——直接走 polling，让 Modal 自然反映正在跑的那一次。
      } else if (!resp.ok) {
        alert("启动失败：" + (data.error || resp.status));
      }
    } catch (err) {
      alert("启动失败：" + err.message);
    }
    await _refreshMediaItem(mediaItemId);
    _startModalPolling(() => _refreshMediaItem(mediaItemId));
  }

  async function openModalForMediaItem(mediaItemId) {
    _setMediaState(mediaItemId);
    _ensureModalShell();
    _state.latest = null;
    _state.refreshFn = () => _refreshMediaItem(mediaItemId);
    _openModal();
    await _refreshMediaItem(mediaItemId);
    const r = _state.latest;
    if (!r || r.status === "pending" || r.status === "running") {
      _startModalPolling(() => _refreshMediaItem(mediaItemId));
    }
  }

  async function _refreshMediaItem(mediaItemId) {
    try {
      const resp = await fetch(`/medias/api/items/${mediaItemId}/video-ai-review`);
      if (!resp.ok) {
        _state.latest = null;
        _renderModal();
        return;
      }
      const data = await resp.json();
      _state.latest = data.review || null;
      _renderModal();
    } catch (e) {
      _state.latest = null;
      _renderModal();
    }
  }

  return { init, triggerForMediaItem, openModalForMediaItem };
})();

document.addEventListener("DOMContentLoaded", function () {
  var node = document.getElementById("step-analysis");
  if (!node) return;
  var taskId = node.dataset.taskId;
  if (!taskId) return;
  window.VideoAiReview.init({
    taskId: taskId,
    isAdmin: node.dataset.isAdmin === "1",
    projectType: node.dataset.projectType || "multi_translate",
  });
});
