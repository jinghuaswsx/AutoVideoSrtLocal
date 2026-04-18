(function () {
  const state = {
    pollTimer: null,
    taskId: "",
  };
  const overallDecisionLabels = {
    running: "检查中",
    done: "已完成",
    unfinished: "未完成",
  };
  const taskStatusLabels = {
    queued: "排队中",
    locking_locale: "锁定语种页面",
    downloading: "下载图片中",
    analyzing: "分析图片中",
    review_ready: "待人工复核",
    done: "已完成",
    failed: "失败",
  };
  const decisionLabels = {
    pass: "通过",
    review: "待复核",
    replace: "需替换",
    no_text: "无文字",
    failed: "处理失败",
  };
  const referenceStatusLabels = {
    matched: "已匹配",
    weak_match: "弱匹配",
    not_matched: "未匹配",
    not_provided: "未提供",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(text) {
    $("linkCheckStatus").textContent = text;
  }

  function showError(message) {
    const node = $("linkCheckError");
    if (!message) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = message;
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "请求失败");
    }
    return payload;
  }

  async function loadLanguages() {
    const data = await fetchJSON("/medias/api/languages");
    const select = $("targetLanguage");
    select.innerHTML = '<option value="">请选择语言</option>';
    for (const item of data.items || []) {
      const option = document.createElement("option");
      option.value = item.code;
      option.textContent = item.name_zh || item.code;
      select.appendChild(option);
    }
  }

  function summaryCard(label, value) {
    return `
      <div class="lc-summary-card">
        <strong>${label}</strong>
        <span>${value}</span>
      </div>
    `;
  }

  function renderSummary(task) {
    const summary = task.summary || {};
    const progress = task.progress || {};
    $("linkCheckSummary").innerHTML = `
      <div class="lc-summary-grid">
        ${summaryCard("抓取图片", progress.total ?? 0)}
        ${summaryCard("已分析", progress.analyzed ?? 0)}
        ${summaryCard("比对参考图", progress.compared ?? 0)}
        ${summaryCard("异常图片", progress.failed ?? 0)}
        ${summaryCard("整体判断", overallDecisionLabels[summary.overall_decision] || taskStatusLabels[task.status] || "-")}
      </div>
      <div class="lc-summary-meta">
        <div>目标语言：${task.target_language_name || task.target_language || "-"}</div>
        <div>页面语言：${task.page_language || "-"}</div>
        <div>最终地址：${task.resolved_url || "-"}</div>
      </div>
    `;
  }

  function badge(label, kind) {
    return `<span class="lc-badge${kind ? ` ${kind}` : ""}">${label}</span>`;
  }

  function renderItem(item, taskId) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const decision = analysis.decision || item.status || "-";
    const decisionClass = decision === "pass" ? "is-success" : (decision === "replace" || item.status === "failed" ? "is-danger" : "");
    const referencePreview = reference.reference_id
      ? `/api/link-check/tasks/${taskId}/images/reference/${reference.reference_id}`
      : "";
    return `
      <article class="lc-result-card">
        <div class="lc-result-head">
          <div class="lc-badges">
            ${badge(item.kind === "detail" ? "详情图" : "轮播图")}
            ${badge(`判断：${decisionLabels[decision] || decision}`, decisionClass)}
            ${badge(`参考图：${referenceStatusLabels[reference.status] || reference.status || "未提供"}`)}
          </div>
        </div>
        <div class="lc-result-grid">
          <div class="lc-preview-grid">
            <div class="lc-preview-card">
              <h3>网站抓取图</h3>
              <img src="${item.site_preview_url}" alt="site preview">
            </div>
            <div class="lc-preview-card">
              <h3>参考图</h3>
              ${referencePreview
                ? `<img src="${referencePreview}" alt="reference preview">`
                : '<div class="lc-preview-empty">未提供参考图</div>'}
            </div>
          </div>
          <div class="lc-result-meta">
            <div class="lc-meta-row">
              <strong>图片来源</strong>
              <span>${item.source_url || "-"}</span>
            </div>
            <div class="lc-meta-row">
              <strong>识别语言</strong>
              <span>${analysis.detected_language || "-"}</span>
            </div>
            <div class="lc-meta-row">
              <strong>提取文字</strong>
              <span>${analysis.text_summary || "-"}</span>
            </div>
            <div class="lc-meta-row">
              <strong>质量分</strong>
              <span>${analysis.quality_score ?? "-"}</span>
            </div>
            <div class="lc-meta-row">
              <strong>模型说明</strong>
              <span>${analysis.quality_reason || item.error || "-"}</span>
            </div>
            <div class="lc-meta-row">
              <strong>参考图匹配</strong>
              <span>${reference.reference_filename || "-"} ${reference.score != null ? `(分数 ${reference.score})` : ""}</span>
            </div>
          </div>
        </div>
      </article>
    `;
  }

  function renderResults(task) {
    const items = task.items || [];
    if (!items.length) {
      $("linkCheckResults").innerHTML = '<div class="lc-empty">还没有图片结果，系统正在处理中。</div>';
      return;
    }
    $("linkCheckResults").innerHTML = `
      <div class="lc-result-list">
        ${items.map((item) => renderItem(item, task.id)).join("")}
      </div>
    `;
  }

  async function pollTask(taskId) {
    const task = await fetchJSON(`/api/link-check/tasks/${taskId}`);
    renderSummary(task);
    renderResults(task);
    setStatus(`当前状态：${taskStatusLabels[task.status] || task.status}`);
    if (task.error) {
      showError(task.error);
    }
    if (["done", "failed", "review_ready"].includes(task.status)) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    showError("");
    setStatus("正在提交任务...");
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }

    try {
      const payload = await fetchJSON("/api/link-check/tasks", {
        method: "POST",
        body: new FormData($("linkCheckForm")),
      });
      state.taskId = payload.task_id;
      setStatus("任务已创建，开始抓取页面...");
      await pollTask(state.taskId);
      state.pollTimer = window.setInterval(() => {
        pollTask(state.taskId).catch((error) => {
          window.clearInterval(state.pollTimer);
          state.pollTimer = null;
          showError(error.message || "轮询失败");
          setStatus("任务状态获取失败");
        });
      }, 1500);
    } catch (error) {
      showError(error.message || "创建任务失败");
      setStatus("提交失败");
    }
  }

  document.addEventListener("DOMContentLoaded", async function () {
    try {
      await loadLanguages();
    } catch (error) {
      showError(error.message || "语言列表加载失败");
      setStatus("初始化失败");
      return;
    }
    $("linkCheckForm").addEventListener("submit", onSubmit);
  });
})();
