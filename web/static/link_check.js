(function () {
  const state = {
    pollTimer: null,
    taskId: "",
    isSubmitting: false,
  };

  const overallDecisionLabels = {
    running: "检查中",
    done: "已完成",
    unfinished: "未完成",
  };

  const taskStatusLabels = {
    queued: "排队中",
    locking_locale: "锁定目标语种页面",
    downloading: "下载图片中",
    analyzing: "分析图片中",
    review_ready: "待复核",
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

  const binaryStatusLabels = {
    pass: "通过",
    fail: "不通过",
    skipped: "未执行",
    error: "执行失败",
  };

  const sameImageStatusLabels = {
    done: "已完成",
    skipped: "未执行",
    error: "执行失败",
  };

  const decisionSourceLabels = {
    binary_quick_check: "二值快检",
    gemini_language_check: "语言 Gemini",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function setStatus(text) {
    $("linkCheckStatus").textContent = text;
  }

  function setSubmitting(isSubmitting, statusText, buttonText) {
    state.isSubmitting = isSubmitting;
    const submitButton = $("linkCheckSubmit");
    if (!submitButton) {
      return;
    }
    submitButton.disabled = isSubmitting;
    submitButton.classList.toggle("is-loading", isSubmitting);
    submitButton.setAttribute("aria-busy", isSubmitting ? "true" : "false");
    submitButton.textContent = buttonText || (isSubmitting ? "检查中..." : "开始检查");
    if (statusText) {
      setStatus(statusText);
    }
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

  function formatPercent(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }
    return `${(value * 100).toFixed(1)}%`;
  }

  function summaryCard(label, value) {
    return `
      <div class="lc-summary-card">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(value)}</span>
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
        ${summaryCard("参考图比对", progress.compared ?? 0)}
        ${summaryCard("二值快检", progress.binary_checked ?? 0)}
        ${summaryCard("同图大模型", progress.same_image_llm_done ?? 0)}
        ${summaryCard("整体结论", overallDecisionLabels[summary.overall_decision] || taskStatusLabels[task.status] || "-")}
      </div>
      <div class="lc-summary-meta">
        <div>目标语言：${escapeHtml(task.target_language_name || task.target_language || "-")}</div>
        <div>页面语言：${escapeHtml(task.page_language || "-")}</div>
        <div>最终地址：${escapeHtml(task.resolved_url || "-")}</div>
        <div>参考图已匹配：${escapeHtml(summary.reference_matched_count ?? 0)}</div>
      </div>
    `;
  }

  function badge(label, kind) {
    return `<span class="lc-badge${kind ? ` ${kind}` : ""}">${escapeHtml(label)}</span>`;
  }

  function buildMetaRow(label, value) {
    return `
      <div class="lc-meta-row">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(value)}</span>
      </div>
    `;
  }

  function renderItem(item, taskId) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const binary = item.binary_quick_check || {};
    const sameImage = item.same_image_llm || {};
    const decision = analysis.decision || item.status || "-";
    const decisionClass = decision === "pass"
      ? "is-success"
      : (decision === "replace" || item.status === "failed" ? "is-danger" : "");
    const referencePreview = reference.reference_id
      ? `/api/link-check/tasks/${taskId}/images/reference/${reference.reference_id}`
      : "";
    const sameImageValue = sameImage.status === "done"
      ? (sameImage.answer || "-")
      : (sameImageStatusLabels[sameImage.status] || sameImage.status || "-");

    return `
      <article class="lc-result-card">
        <div class="lc-result-head">
          <div class="lc-badges">
            ${badge(item.kind === "detail" ? "详情图" : "轮播图")}
            ${badge(`最终判定：${decisionLabels[decision] || decision}`, decisionClass)}
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
            ${buildMetaRow("图片来源", item.source_url || "-")}
            ${buildMetaRow("最终判定来源", decisionSourceLabels[analysis.decision_source] || analysis.decision_source || "-")}
            ${buildMetaRow("识别语言", analysis.detected_language || "-")}
            ${buildMetaRow("提取文字", analysis.text_summary || "-")}
            ${buildMetaRow("质量分", analysis.quality_score ?? "-")}
            ${buildMetaRow("模型说明", analysis.quality_reason || item.error || "-")}
            ${buildMetaRow("参考图匹配", `${reference.reference_filename || "-"}${reference.score != null ? `（分数 ${reference.score}）` : ""}`)}
            ${buildMetaRow("二值快检结果", binaryStatusLabels[binary.status] || binary.status || "-")}
            ${buildMetaRow("二值相似度", formatPercent(binary.binary_similarity))}
            ${buildMetaRow("前景重合度", formatPercent(binary.foreground_overlap))}
            ${buildMetaRow("当前阈值", formatPercent(binary.threshold))}
            ${buildMetaRow("二值快检说明", binary.reason || "-")}
            ${buildMetaRow("大模型相同图片判断", sameImageValue)}
            ${buildMetaRow("大模型判断通道", sameImage.channel_label || "-")}
            ${buildMetaRow("大模型判断模型", sameImage.model || "-")}
          </div>
        </div>
      </article>
    `;
  }

  function renderResults(task) {
    const items = task.items || [];
    if (!items.length) {
      $("linkCheckResults").innerHTML = '<div class="lc-empty">还没有图片结果，系统正在处理。</div>';
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
    if (state.isSubmitting) {
      return;
    }
    showError("");
    setSubmitting(true, "正在创建任务...", "检查中...");
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
      setSubmitting(true, "正在获取首批进度...", "检查中...");
      await pollTask(state.taskId);
      setSubmitting(false, "", "开始检查");
      state.pollTimer = window.setInterval(() => {
        pollTask(state.taskId).catch((error) => {
          window.clearInterval(state.pollTimer);
          state.pollTimer = null;
          setSubmitting(false, "", "开始检查");
          showError(error.message || "轮询失败");
          setStatus("任务状态获取失败");
        });
      }, 1500);
    } catch (error) {
      setSubmitting(false, "", "开始检查");
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
