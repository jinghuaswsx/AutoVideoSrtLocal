(function () {
  const state = {
    pollTimer: null,
    taskId: "",
    isSubmitting: false,
    currentTask: null,
    detailIndex: null,
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
    const node = $("linkCheckStatus");
    if (node) {
      node.textContent = text;
    }
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
    if (!node) {
      return;
    }
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
    if (!select) {
      return;
    }
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
        <div class="lc-summary-meta-wide">最终地址：${escapeHtml(task.resolved_url || "-")}</div>
        <div>参考图已匹配：${escapeHtml(summary.reference_matched_count ?? 0)}</div>
      </div>
    `;
  }

  function badge(label, kind) {
    return `<span class="lc-badge${kind ? ` ${kind}` : ""}">${escapeHtml(label)}</span>`;
  }

  function buildMetaField(label, value, options) {
    const settings = options || {};
    const valueClasses = ["lc-meta-value"];
    if (settings.clamp !== false) {
      valueClasses.push("lc-clamp-2");
    }
    if (settings.mono) {
      valueClasses.push("lc-mono");
    }

    return `
      <div class="lc-meta-card${settings.detail ? " lc-meta-card--detail" : ""}">
        <strong class="lc-meta-label">${escapeHtml(label)}</strong>
        <span class="${valueClasses.join(" ")}">${escapeHtml(value)}</span>
      </div>
    `;
  }

  function resolveReferencePreviewUrl(reference, taskId) {
    return reference.reference_id
      ? `/api/link-check/tasks/${taskId}/images/reference/${reference.reference_id}`
      : "";
  }

  function getDecisionClass(decision, itemStatus) {
    if (decision === "pass") {
      return "is-success";
    }
    if (decision === "replace" || itemStatus === "failed") {
      return "is-danger";
    }
    return "";
  }

  function getSameImageValue(sameImage) {
    return sameImage.status === "done"
      ? (sameImage.answer || "-")
      : (sameImageStatusLabels[sameImage.status] || sameImage.status || "-");
  }

  function getItemMetaEntries(item) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const binary = item.binary_quick_check || {};
    const sameImage = item.same_image_llm || {};

    return [
      { label: "图片来源", value: item.source_url || "-", mono: true },
      { label: "最终判定来源", value: decisionSourceLabels[analysis.decision_source] || analysis.decision_source || "-" },
      { label: "识别语言", value: analysis.detected_language || "-" },
      { label: "提取文字", value: analysis.text_summary || "-" },
      { label: "质量分", value: analysis.quality_score ?? "-" },
      { label: "模型说明", value: analysis.quality_reason || item.error || "-" },
      {
        label: "参考图匹配",
        value: `${reference.reference_filename || "-"}${reference.score != null ? `（分数 ${reference.score}）` : ""}`,
      },
      { label: "二值快检结果", value: binaryStatusLabels[binary.status] || binary.status || "-" },
      { label: "二值相似度", value: formatPercent(binary.binary_similarity) },
      { label: "前景重合度", value: formatPercent(binary.foreground_overlap) },
      { label: "当前阈值", value: formatPercent(binary.threshold) },
      { label: "二值快检说明", value: binary.reason || "-" },
      { label: "大模型相同图片判断", value: getSameImageValue(sameImage) },
      { label: "大模型判断通道", value: sameImage.channel_label || "-" },
      { label: "大模型判断模型", value: sameImage.model || "-" },
    ];
  }

  function renderPreviewPanel(title, imageUrl, emptyText) {
    return `
      <div class="lc-preview-panel">
        <div class="lc-preview-label">${escapeHtml(title)}</div>
        <div class="lc-preview-frame">
          ${imageUrl
            ? `<img src="${imageUrl}" alt="${escapeHtml(title)}">`
            : `<div class="lc-preview-empty">${escapeHtml(emptyText)}</div>`}
        </div>
      </div>
    `;
  }

  function renderItem(item, taskId, index) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const decision = analysis.decision || item.status || "-";
    const decisionClass = getDecisionClass(decision, item.status);
    const referencePreview = resolveReferencePreviewUrl(reference, taskId);
    const metaEntries = getItemMetaEntries(item);

    return `
      <article class="lc-result-card">
        <div class="lc-result-head">
          <div class="lc-badges">
            ${badge(item.kind === "detail" ? "详情图" : "轮播图")}
            ${badge(`最终判定：${decisionLabels[decision] || decision}`, decisionClass)}
            ${badge(`参考图：${referenceStatusLabels[reference.status] || reference.status || "未提供"}`)}
          </div>
          <button type="button" class="lc-detail-trigger" data-item-index="${index}">查看任务详情</button>
        </div>
        <div class="lc-result-layout">
          <div class="lc-preview-stack">
            ${renderPreviewPanel("网站抓取图", item.site_preview_url, "暂无网站图")}
            ${renderPreviewPanel("参考图", referencePreview, "未提供参考图")}
          </div>
          <div class="lc-result-side">
            <div class="lc-meta-grid">
              ${metaEntries.map((entry) => buildMetaField(entry.label, entry.value, entry)).join("")}
            </div>
          </div>
        </div>
      </article>
    `;
  }

  function renderDetailDialog(item, taskId) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const binary = item.binary_quick_check || {};
    const sameImage = item.same_image_llm || {};
    const decision = analysis.decision || item.status || "-";
    const decisionClass = getDecisionClass(decision, item.status);
    const referencePreview = resolveReferencePreviewUrl(reference, taskId);
    const detailEntries = [
      { label: "图片类型", value: item.kind === "detail" ? "详情图" : "轮播图", detail: true },
      { label: "任务状态", value: taskStatusLabels[item.status] || item.status || "-", detail: true },
      { label: "最终判定", value: decisionLabels[decision] || decision, detail: true },
      { label: "参考图状态", value: referenceStatusLabels[reference.status] || reference.status || "-", detail: true },
      { label: "二值快检状态", value: binaryStatusLabels[binary.status] || binary.status || "-", detail: true },
      { label: "大模型相同图片判断", value: getSameImageValue(sameImage), detail: true },
      ...getItemMetaEntries(item).map((entry) => ({
        ...entry,
        detail: true,
        clamp: false,
      })),
    ];

    return `
      <div class="lc-detail-content">
        <div class="lc-detail-badges">
          ${badge(item.kind === "detail" ? "详情图" : "轮播图")}
          ${badge(`最终判定：${decisionLabels[decision] || decision}`, decisionClass)}
          ${badge(`参考图：${referenceStatusLabels[reference.status] || reference.status || "未提供"}`)}
        </div>
        <div class="lc-detail-media">
          ${renderPreviewPanel("网站抓取图", item.site_preview_url, "暂无网站图")}
          ${renderPreviewPanel("参考图", referencePreview, "未提供参考图")}
        </div>
        <div class="lc-detail-meta-grid">
          ${detailEntries.map((entry) => buildMetaField(entry.label, entry.value, entry)).join("")}
        </div>
      </div>
    `;
  }

  function closeDetailDialog() {
    const dialog = $("linkCheckDetailDialog");
    const body = $("linkCheckDetailBody");
    if (!dialog || !body) {
      return;
    }
    dialog.hidden = true;
    dialog.setAttribute("aria-hidden", "true");
    document.body.classList.remove("lc-modal-open");
    body.innerHTML = "";
    state.detailIndex = null;
  }

  function renderOpenDetailDialog() {
    const dialog = $("linkCheckDetailDialog");
    const body = $("linkCheckDetailBody");
    if (!dialog || !body || state.detailIndex == null || !state.currentTask) {
      return;
    }

    const item = (state.currentTask.items || [])[state.detailIndex];
    if (!item) {
      closeDetailDialog();
      return;
    }

    body.innerHTML = renderDetailDialog(item, state.currentTask.id);
  }

  function openDetailDialog(index) {
    const dialog = $("linkCheckDetailDialog");
    if (!dialog || !state.currentTask) {
      return;
    }
    state.detailIndex = index;
    renderOpenDetailDialog();
    dialog.hidden = false;
    dialog.setAttribute("aria-hidden", "false");
    document.body.classList.add("lc-modal-open");
  }

  function renderResults(task) {
    const items = task.items || [];
    if (!items.length) {
      closeDetailDialog();
      $("linkCheckResults").innerHTML = '<div class="lc-empty">还没有图片结果，系统正在处理。</div>';
      return;
    }

    $("linkCheckResults").innerHTML = `
      <div class="lc-result-list">
        ${items.map((item, index) => renderItem(item, task.id, index)).join("")}
      </div>
    `;

    if (state.detailIndex != null) {
      renderOpenDetailDialog();
    }
  }

  async function pollTask(taskId) {
    const task = await fetchJSON(`/api/link-check/tasks/${taskId}`);
    state.currentTask = task;
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

    closeDetailDialog();
    state.currentTask = null;
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

  function onResultsClick(event) {
    const trigger = event.target.closest(".lc-detail-trigger");
    if (!trigger) {
      return;
    }

    const itemIndex = Number(trigger.dataset.itemIndex);
    if (!Number.isFinite(itemIndex)) {
      return;
    }

    openDetailDialog(itemIndex);
  }

  function onDialogClick(event) {
    if (event.target.closest("[data-dialog-close]")) {
      closeDetailDialog();
    }
  }

  function onKeyDown(event) {
    const dialog = $("linkCheckDetailDialog");
    if (event.key === "Escape" && dialog && !dialog.hidden) {
      closeDetailDialog();
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
    $("linkCheckResults").addEventListener("click", onResultsClick);
    $("linkCheckDetailDialog").addEventListener("click", onDialogClick);
    document.addEventListener("keydown", onKeyDown);
  });
})();
