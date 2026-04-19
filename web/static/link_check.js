(function () {
  const state = {
    currentTask: null,
    pollTimer: null,
  };

  const TERMINAL_STATUSES = new Set(["done", "failed", "review_ready", "deleted"]);

  const taskStatusLabels = {
    queued: "排队中",
    locking_locale: "锁定目标语言页面",
    downloading: "下载图片中",
    analyzing: "分析图片中",
    review_ready: "待复核",
    done: "已完成",
    failed: "失败",
    deleted: "已删除",
  };

  const overallDecisionLabels = {
    running: "检查中",
    done: "已完成",
    unfinished: "未完成",
    pass: "通过",
    review: "待复核",
    replace: "需替换",
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
    fail: "未通过",
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

  function getBootstrappedTask() {
    if (window.__LINK_CHECK_TASK__ && typeof window.__LINK_CHECK_TASK__ === "object") {
      return window.__LINK_CHECK_TASK__;
    }

    const node = $("linkCheckInitialTask");
    if (!node || !node.textContent) {
      return null;
    }

    try {
      return JSON.parse(node.textContent);
    } catch (_error) {
      return null;
    }
  }

  function formatPercent(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }
    return `${(value * 100).toFixed(1)}%`;
  }

  function formatValue(value) {
    if (value == null || value === "") {
      return "-";
    }
    return value;
  }

  function summaryCard(label, value) {
    return `
      <div class="lc-summary-card">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(formatValue(value))}</span>
      </div>
    `;
  }

  function badge(label, kind) {
    return `<span class="lc-badge${kind ? ` ${kind}` : ""}">${escapeHtml(label)}</span>`;
  }

  function isLowQuality(score) {
    return typeof score === "number" && score < 60;
  }

  function isForegroundOverlapBelowThreshold(binary) {
    return (
      typeof binary.foreground_overlap === "number" &&
      typeof binary.threshold === "number" &&
      binary.foreground_overlap < binary.threshold
    );
  }

  function isBinaryCheckError(binary) {
    return binary.status === "error";
  }

  function isSameImageRejected(sameImage) {
    if (sameImage.status !== "done") {
      return false;
    }
    const answer = String(sameImage.answer || "").trim().toLowerCase();
    return ["不是", "否", "no", "false", "different"].includes(answer);
  }

  function isSameImageError(sameImage) {
    return sameImage.status === "error";
  }

  function collectIssueSummary(item, task) {
    const issues = [];
    const analysis = item.analysis || {};
    const binary = item.binary_quick_check || {};
    const sameImage = item.same_image_llm || {};

    if (item.status === "failed" || analysis.decision === "failed") {
      issues.push("图片处理失败");
    }
    if (analysis.decision === "replace") {
      issues.push("最终判定需替换");
    }
    if (analysis.language_match === false) {
      issues.push("识别语言与目标语言不匹配");
    } else if (
      analysis.detected_language &&
      task &&
      task.target_language &&
      analysis.detected_language !== task.target_language
    ) {
      issues.push("识别语言与目标语言不匹配");
    }
    if (isLowQuality(analysis.quality_score)) {
      issues.push("质量分过低");
    }
    if (binary.status === "fail") {
      issues.push("二值快检未通过");
    }
    if (isBinaryCheckError(binary)) {
      issues.push("二值快检执行失败");
    }
    if (isForegroundOverlapBelowThreshold(binary)) {
      issues.push("前景重合度低于阈值");
    }
    if (isSameImageRejected(sameImage)) {
      issues.push("大模型判定不是同图");
    }
    if (isSameImageError(sameImage)) {
      issues.push("大模型同图判断执行失败");
    }

    return issues;
  }

  function buildMetaField(label, value, options) {
    const settings = options || {};
    const cardClasses = ["lc-meta-card"];
    const valueClasses = ["lc-meta-value"];

    if (settings.isAlert) {
      cardClasses.push("lc-meta-card--alert");
      valueClasses.push("lc-meta-value--alert");
    }
    if (settings.mono) {
      valueClasses.push("lc-mono");
    }
    if (settings.clamp !== false) {
      valueClasses.push("lc-clamp-2");
    }

    return `
      <div class="${cardClasses.join(" ")}">
        <strong class="lc-meta-label">${escapeHtml(label)}</strong>
        <span class="${valueClasses.join(" ")}">${escapeHtml(formatValue(value))}</span>
      </div>
    `;
  }

  function getDecisionClass(decision, itemStatus) {
    if (decision === "pass") {
      return "is-success";
    }
    if (decision === "replace" || decision === "failed" || itemStatus === "failed") {
      return "is-danger";
    }
    return "";
  }

  function getSameImageValue(sameImage) {
    return sameImage.status === "done"
      ? formatValue(sameImage.answer)
      : (sameImageStatusLabels[sameImage.status] || sameImage.status || "-");
  }

  function resolveSitePreviewUrl(task, item) {
    if (item.site_preview_url) {
      return item.site_preview_url;
    }
    if (task && task.id && item.id) {
      return `/api/link-check/tasks/${task.id}/images/site/${item.id}`;
    }
    return "";
  }

  function resolveReferencePreviewUrl(task, reference) {
    if (reference.preview_url) {
      return reference.preview_url;
    }
    if (task && task.id && reference.reference_id) {
      return `/api/link-check/tasks/${task.id}/images/reference/${reference.reference_id}`;
    }
    if (task && task.id && reference.id) {
      return `/api/link-check/tasks/${task.id}/images/reference/${reference.id}`;
    }
    return "";
  }

  function shouldShowReferencePreview(reference, previewUrl) {
    return reference.status === "matched" && Boolean(previewUrl);
  }

  function getReferenceMatchValue(reference) {
    const scoreSuffix = reference.score != null ? `（分数 ${reference.score}）` : "";
    if (reference.status === "matched") {
      return `${reference.reference_filename || reference.filename || "-"}${scoreSuffix}`;
    }
    if (reference.status === "weak_match") {
      return `存在弱匹配候选${scoreSuffix}`;
    }
    if (reference.status === "not_matched") {
      return "未匹配到参考图";
    }
    if (reference.status === "not_provided") {
      return "未提供参考图";
    }
    return referenceStatusLabels[reference.status] || "-";
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

  function buildPreviewStack(task, item) {
    const reference = item.reference_match || {};
    const referencePreview = resolveReferencePreviewUrl(task, reference);
    const panels = [
      renderPreviewPanel("网站抓取图", resolveSitePreviewUrl(task, item), "暂未生成网站抓取图"),
    ];

    if (shouldShowReferencePreview(reference, referencePreview)) {
      panels.push(renderPreviewPanel("参考图", referencePreview, "未提供参考图"));
    }

    const stackClass = panels.length === 1
      ? "lc-preview-stack lc-preview-stack--single"
      : "lc-preview-stack";

    return `<div class="${stackClass}">${panels.join("")}</div>`;
  }

  function getItemMetaEntries(item, task) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const binary = item.binary_quick_check || {};
    const sameImage = item.same_image_llm || {};
    const languageMismatch = analysis.language_match === false
      || (
        analysis.detected_language &&
        task &&
        task.target_language &&
        analysis.detected_language !== task.target_language
      );

    return [
      { label: "图片来源", value: item.source_url || "-", mono: true },
      {
        label: "最终判定来源",
        value: decisionSourceLabels[analysis.decision_source] || analysis.decision_source || "-",
      },
      {
        label: "识别语言",
        value: analysis.detected_language || "-",
        isAlert: languageMismatch,
      },
      { label: "提取文字", value: analysis.text_summary || "-" },
      {
        label: "质量分",
        value: analysis.quality_score ?? "-",
        isAlert: isLowQuality(analysis.quality_score),
      },
      { label: "模型说明", value: analysis.quality_reason || item.error || "-" },
      { label: "参考图匹配", value: getReferenceMatchValue(reference) },
      {
        label: "二值快检结果",
        value: binaryStatusLabels[binary.status] || binary.status || "-",
        isAlert: binary.status === "fail" || isBinaryCheckError(binary),
      },
      { label: "二值相似度", value: formatPercent(binary.binary_similarity) },
      {
        label: "前景重合度",
        value: formatPercent(binary.foreground_overlap),
        isAlert: isForegroundOverlapBelowThreshold(binary),
      },
      {
        label: "当前阈值",
        value: formatPercent(binary.threshold),
        isAlert: isForegroundOverlapBelowThreshold(binary),
      },
      {
        label: "二值快检说明",
        value: binary.reason || "-",
        isAlert: binary.status === "fail" || isBinaryCheckError(binary),
      },
      {
        label: "大模型同图判断",
        value: getSameImageValue(sameImage),
        isAlert: isSameImageRejected(sameImage) || isSameImageError(sameImage),
      },
      { label: "大模型判断通道", value: sameImage.channel_label || "-" },
      { label: "大模型模型", value: sameImage.model || "-" },
    ];
  }

  function renderSummary(task) {
    const summary = task.summary || {};
    const progress = task.progress || {};
    $("linkCheckSummary").innerHTML = `
      <div class="lc-panel-head">
        <span class="lc-kicker">Summary</span>
        <h2>任务摘要</h2>
        <p>详情页直接使用已保存任务状态渲染，并在任务仍活跃时继续轮询。</p>
      </div>
      <div class="lc-summary-grid">
        ${summaryCard("抓取图片", progress.total ?? 0)}
        ${summaryCard("已分析", progress.analyzed ?? 0)}
        ${summaryCard("参考图比对", progress.compared ?? 0)}
        ${summaryCard("二值快检", progress.binary_checked ?? 0)}
        ${summaryCard("同图大模型", progress.same_image_llm_done ?? 0)}
        ${summaryCard("整体结论", overallDecisionLabels[summary.overall_decision] || taskStatusLabels[task.status] || "-")}
      </div>
      <div class="lc-summary-meta">
        <div class="lc-meta-chip"><strong>目标语言</strong><span>${escapeHtml(formatValue(task.target_language_name || task.target_language))}</span></div>
        <div class="lc-meta-chip"><strong>页面语言</strong><span>${escapeHtml(formatValue(task.page_language))}</span></div>
        <div class="lc-meta-chip"><strong>任务状态</strong><span>${escapeHtml(taskStatusLabels[task.status] || task.status || "-")}</span></div>
        <div class="lc-meta-chip lc-meta-chip--wide"><strong>链接</strong><span class="lc-clamp-2 lc-mono">${escapeHtml(formatValue(task.resolved_url || task.link_url))}</span></div>
      </div>
    `;
  }

  function renderItem(item, task, index) {
    const analysis = item.analysis || {};
    const reference = item.reference_match || {};
    const decision = analysis.decision || item.status || "-";
    const decisionClass = getDecisionClass(decision, item.status);
    const issues = collectIssueSummary(item, task);
    const cardClasses = ["lc-result-card"];
    const metaEntries = getItemMetaEntries(item, task);

    if (issues.length) {
      cardClasses.push("lc-result-card--alert");
    }

    return `
      <article class="${cardClasses.join(" ")}" data-item-index="${index}">
        <div class="lc-result-head">
          <div class="lc-badges">
            ${badge(item.kind === "detail" ? "详情图" : "轮播图")}
            ${badge(`最终判定：${decisionLabels[decision] || decision}`, decisionClass)}
            ${badge(`参考图：${referenceStatusLabels[reference.status] || reference.status || "未提供"}`)}
          </div>
        </div>
        ${issues.length ? `
          <div class="lc-issue-summary" aria-label="问题摘要">
            ${issues.map((issue) => `<span class="lc-issue-pill">${escapeHtml(issue)}</span>`).join("")}
          </div>
        ` : ""}
        <div class="lc-result-layout">
          ${buildPreviewStack(task, item)}
          <div class="lc-result-side">
            <div class="lc-meta-grid">
              ${metaEntries.map((entry) => buildMetaField(entry.label, entry.value, entry)).join("")}
            </div>
          </div>
        </div>
      </article>
    `;
  }

  function renderResults(task) {
    const items = task.items || [];
    const container = $("linkCheckResults");

    if (!items.length) {
      container.innerHTML = `
        <div class="lc-panel-head">
          <span class="lc-kicker">Results</span>
          <h2>图片结果</h2>
          <p>任务已保存，但目前还没有可展示的图片结果。</p>
        </div>
        <div class="lc-empty lc-empty--panel">
          <strong>还没有图片结果</strong>
          <span>系统会在抓取和分析完成后持续更新这里。</span>
        </div>
      `;
      return;
    }

    container.innerHTML = `
      <div class="lc-panel-head">
        <span class="lc-kicker">Results</span>
        <h2>图片结果</h2>
        <p>失败项会以红色告警卡片和字段强调，方便直接定位问题依据。</p>
      </div>
      <div class="lc-result-list">
        ${items.map((item, index) => renderItem(item, task, index)).join("")}
      </div>
    `;
  }

  function renderTask(task) {
    state.currentTask = task;
    renderSummary(task);
    renderResults(task);
    showError(task.error || "");
    setStatus(`当前状态：${taskStatusLabels[task.status] || task.status || "-"}`);
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollTask(taskId) {
    const task = await fetchJSON(`/api/link-check/tasks/${taskId}`);
    renderTask(task);
    if (TERMINAL_STATUSES.has(task.status)) {
      stopPolling();
    }
  }

  function startPollingIfNeeded(task) {
    if (!task || !task.id || TERMINAL_STATUSES.has(task.status)) {
      return;
    }

    stopPolling();
    state.pollTimer = window.setInterval(() => {
      pollTask(task.id).catch((error) => {
        stopPolling();
        showError(error.message || "轮询失败");
        setStatus("任务状态获取失败");
      });
    }, 1500);
  }

  document.addEventListener("DOMContentLoaded", function () {
    const page = $("linkCheckDetailPage");
    if (!page) {
      return;
    }

    const task = getBootstrappedTask();
    if (!task || !task.id) {
      showError("初始化任务数据缺失");
      setStatus("初始化失败");
      return;
    }

    renderTask(task);
    startPollingIfNeeded(task);
  });
})();
