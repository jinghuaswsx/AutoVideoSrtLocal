/** Product Research - Frontend JS */
(function () {
  "use strict";

  const C = window.PR_CONFIG || {};
  let pollTimer = null;
  let currentRunId = "";
  let researchResult = null;
  let uploadedAssets = {};

  // ── File Selection ──────────────────────────────────
  function onFileSelected(input, assetKey) {
    const file = input.files[0];
    const nameEl = document.getElementById(assetKey === "main_image" ? "prMainImageName" : "prVideoName");
    if (file) {
      nameEl.textContent = file.name + " (" + (file.size / 1024 / 1024).toFixed(1) + " MB)";
      uploadedAssets[assetKey] = file;
    } else {
      nameEl.textContent = "";
      delete uploadedAssets[assetKey];
    }
  }
  window.onFileSelected = onFileSelected;

  // ── Build input payload ──────────────────────────────
  function buildInputPayload() {
    const productUrl = document.getElementById("prProductUrl").value.trim();
    const productName = document.getElementById("prProductName").value.trim();
    const productNameEn = document.getElementById("prProductNameEn").value.trim();
    const notes = document.getElementById("prNotes").value.trim();
    const googleSearchEnabled = document.getElementById("prGoogleSearchToggle").checked;
    const delaySeconds = parseInt(document.getElementById("prCountryDelay").value, 10) || 0;

    // Selected countries
    var selectedCountries = [];
    var checkboxes = document.querySelectorAll("#prCountryCheckboxes input[type=checkbox]:checked");
    for (var i = 0; i < checkboxes.length; i++) {
      selectedCountries.push(checkboxes[i].value);
    }

    return {
      product_url: productUrl,
      product_name: productName,
      product_name_en: productNameEn,
      main_image: uploadedAssets.main_image ? {} : {},
      short_video: uploadedAssets.short_video ? {} : {},
      notes: notes,
      google_search_enabled: googleSearchEnabled,
      selected_countries: selectedCountries,
      country_delay_seconds: Math.max(0, Math.min(120, delaySeconds)),
    };
  }

  // ── Validate ─────────────────────────────────────────
  function validateInput(payload) {
    var errors = [];
    if (!payload.product_url) errors.push("请填写产品链接");
    if (!uploadedAssets.main_image) errors.push("请选择主图");
    if (!uploadedAssets.short_video) errors.push("请选择短视频");
    if (!payload.selected_countries || payload.selected_countries.length < 1) errors.push("请至少选择 1 个调研国家");
    return errors;
  }

  // ── Upload asset ─────────────────────────────────────
  async function uploadAsset(file, assetType) {
    var fd = new FormData();
    fd.append("file", file);
    fd.append("asset_type", assetType);
    var resp = await fetch(C.uploadUrl, { method: "POST", body: fd });
    if (!resp.ok) {
      var errData = await resp.json().catch(function () { return {}; });
      throw new Error((errData.error || {}).message || "Upload failed");
    }
    var data = await resp.json();
    return data.data;
  }

  // ── Start Research ───────────────────────────────────
  async function startResearch() {
    var payload = buildInputPayload();
    var errors = validateInput(payload);
    if (errors.length) {
      alert(errors.join("\n"));
      return;
    }

    var body = document.getElementById("prBody");
    body.innerHTML = "<div class=\"pr-loading\"><div class=\"pr-loading-spinner\"></div><p>正在上传素材并创建调研任务...</p></div>";
    setButtonsState("running");

    try {
      // Upload assets first
      if (uploadedAssets.main_image) {
        var imgResult = await uploadAsset(uploadedAssets.main_image, "image");
        payload.main_image = { asset_id: imgResult.asset_id, url: imgResult.url, local_path: imgResult.local_path };
      }
      if (uploadedAssets.short_video) {
        var vidResult = await uploadAsset(uploadedAssets.short_video, "video");
        payload.short_video = { asset_id: vidResult.asset_id, url: vidResult.url, local_path: vidResult.local_path };
      }

      // Create run
      var resp = await fetch(C.createUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        var errData = await resp.json().catch(function () { return {}; });
        body.innerHTML = "<div class=\"pr-loading\">创建失败：" + escapeHtml((errData.error || {}).message || "Unknown error") + "</div>";
        setButtonsState("idle");
        return;
      }
      var data = await resp.json();
      currentRunId = data.data.research_run_id;
      poll();
    } catch (err) {
      body.innerHTML = "<div class=\"pr-loading\">请求失败：" + escapeHtml(err.message || err) + "</div>";
      setButtonsState("idle");
    }
  }
  window.startResearch = startResearch;

  // ── Poll ─────────────────────────────────────────────
  function poll() {
    pollTimer = window.setTimeout(async function () {
      try {
        var shouldContinue = await loadStatus();
        if (shouldContinue) {
          poll();
        }
      } catch (err) {
        document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">加载失败：" + escapeHtml(err.message || err) + "</div>";
        setButtonsState("idle");
      }
    }, 2000);
  }

  async function loadStatus() {
    var url = C.statusUrlTemplate.replace("{id}", currentRunId);
    var resp = await fetch(url);
    if (!resp.ok) throw new Error("Status fetch failed");
    var json = await resp.json();
    var data = json.data;
    renderProgress(data);

    var terminal = ["completed", "partially_completed", "failed", "cancelled"];
    if (terminal.indexOf(data.status) >= 0) {
      await loadResult();
      setButtonsState("done");
      return false;
    }
    return true;
  }

  async function loadResult() {
    var url = C.resultUrlTemplate.replace("{id}", currentRunId);
    var resp = await fetch(url);
    if (!resp.ok) throw new Error("Result fetch failed");
    var json = await resp.json();
    researchResult = json.data;
    renderResult(researchResult);
  }

  // ── Render Progress ──────────────────────────────────
  function renderProgress(data) {
    var progress = data.progress || {};
    var cards = progress.step_cards || [];
    var completed = progress.completed_steps || 0;
    var total = progress.total_steps || cards.length || 13;
    var pct = Math.round((completed / Math.max(1, total)) * 100);
    var currentStep = progress.current_step || "";

    var html = "";
    // Progress bar
    html += "<section class=\"pr-progress-header\">";
    html += "<div class=\"pr-progress-track\"><div class=\"pr-progress-fill\" style=\"width:" + pct + "%\"></div></div>";
    html += "<div class=\"pr-progress-meta\">";
    html += "<span>" + completed + "/" + total + " 步完成</span>";
    html += "<span>" + (statusLabel(data.status) + (currentStep ? " — " + currentStep : "")) + "</span>";
    html += "</div></section>";

    // Cards
    html += "<div class=\"pr-step-list\">";
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var st = card.status || "pending";
      html += "<div class=\"pr-step-card is-" + st + "\">";
      html += "<div class=\"pr-step-head\">";
      html += "<div class=\"pr-step-title\"><span class=\"pr-step-dot\"></span><strong>" + escapeHtml(card.title) + "</strong></div>";
      html += "<span class=\"pr-status-pill is-" + st + "\">" + stepStatusLabel(st) + "</span>";
      html += "</div>";
      if (card.result_summary) {
        html += "<div class=\"pr-step-subtitle\">" + escapeHtml(card.result_summary || "") + "</div>";
      }
      if (card.error) {
        html += "<div class=\"pr-step-error\">" + escapeHtml(String(card.error).substring(0, 200)) + "</div>";
      }
      if (card.result && Object.keys(card.result).length > 0 && st === "completed") {
        html += "<details class=\"pr-step-result\"><summary>查看详情</summary><pre style=\"white-space:pre-wrap;font-size:11px\">" + escapeHtml(JSON.stringify(card.result, null, 2).substring(0, 2000)) + "</pre></details>";
      }
      html += "</div>";
    }
    html += "</div>";

    document.getElementById("prBody").innerHTML = html;
  }

  // ── Render Full Result ───────────────────────────────
  function renderResult(result) {
    var html = "";

    // Pipeline cards (keep the progress section visible)
    var cards = result.pipeline_cards || [];
    html += "<section class=\"pr-progress-header\">";
    html += "<div class=\"pr-progress-track\"><div class=\"pr-progress-fill\" style=\"width:100%\"></div></div>";
    html += "<div class=\"pr-progress-meta\"><span>评估完成</span><span>" + statusLabel(result.status) + "</span></div>";
    html += "</section>";

    // Summary cards
    var summary = result.summary || {};
    var frontend = result.frontend || {};
    var fc = frontend.cards || [];
    html += "<div class=\"pr-summary\">";
    for (var i = 0; i < fc.length; i++) {
      var c = fc[i];
      html += "<div class=\"pr-summary-card is-" + (c.severity || "neutral") + "\">";
      html += "<div class=\"pr-summary-value\">" + (c.value !== undefined ? c.value : "-") + "</div>";
      html += "<div class=\"pr-summary-label\">" + escapeHtml(c.title) + "</div>";
      html += "</div>";
    }
    html += "</div>";

    // Best / Worst
    html += "<div style=\"display:flex;gap:12px;margin-bottom:16px;font-size:13px\">";
    html += "<span>最佳国家：<strong>" + escapeHtml(summary.best_country_zh || "-") + "</strong> (" + (summary.ranking && summary.ranking[0] ? summary.ranking[0].overall_score : "") + "分)</span>";
    html += "<span>最差国家：<strong>" + escapeHtml(summary.worst_country_zh || "-") + "</strong></span>";
    html += "</div>";

    // Country Table
    var overview = (frontend.tables || {}).country_overview || [];
    html += "<div class=\"pr-table-wrap\"><table class=\"pr-table\"><thead><tr>";
    html += "<th>国家</th><th>总分</th><th>决策</th><th>置信度</th><th>视频适配</th><th>主要风险</th><th>操作</th>";
    html += "</tr></thead><tbody>";
    for (var j = 0; j < overview.length; j++) {
      var row = overview[j];
      var dec = row.decision || "HOLD";
      var tagClass = dec === "GO" ? "pr-tag--success" : (dec === "TEST" ? "pr-tag--warning" : "pr-tag--danger");
      html += "<tr>";
      html += "<td><strong>" + escapeHtml(row.country_name_zh) + "</strong></td>";
      html += "<td>" + (row.overall_score || 0) + "</td>";
      html += "<td><span class=\"pr-tag " + tagClass + "\">" + escapeHtml(dec) + "</span></td>";
      html += "<td>" + escapeHtml(row.confidence || "low") + "</td>";
      html += "<td>" + escapeHtml(row.video_decision || "-") + "</td>";
      html += "<td style=\"max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap\">" + escapeHtml(row.top_risk || "-") + "</td>";
      html += "<td><button class=\"pr-btn pr-btn--ghost\" style=\"padding:2px 8px;font-size:11px\" onclick=\"window._rerunCountry('" + row.country_code + "')\">重跑</button></td>";
      html += "</tr>";
    }
    html += "</tbody></table></div>";

    // Charts
    html += renderCharts(frontend.charts || {});

    // Country detail tabs
    html += renderCountryTabs(result.countries || {}, overview);

    // Action items
    var actions = frontend.action_items || [];
    if (actions.length) {
      html += "<div class=\"pr-country-detail\"><h3>行动项</h3>";
      for (var k = 0; k < actions.length; k++) {
        var a = actions[k];
        html += "<div style=\"padding:6px 0;border-bottom:1px solid var(--border-main);font-size:12px\">";
        html += "<span class=\"pr-tag " + (a.priority === "high" ? "pr-tag--danger" : "pr-tag--warning") + "\">" + escapeHtml(a.priority) + "</span> ";
        html += "<strong>" + escapeHtml(a.country_code) + "</strong> [" + escapeHtml(a.type) + "] ";
        html += escapeHtml(a.title);
        html += "</div>";
      }
      html += "</div>";
    }

    document.getElementById("prBody").innerHTML = html;
    window._currentCountries = result.countries || {};
  }

  // ── Charts ───────────────────────────────────────────
  function renderCharts(charts) {
    var html = "<div class=\"pr-charts\">";

    // Score bar chart
    var barData = charts.country_score_bar || [];
    html += "<div class=\"pr-chart-card\"><h4>各国总分对比</h4>";
    for (var i = 0; i < barData.length; i++) {
      var d = barData[i];
      var w = Math.max(2, d.overall_score || 0);
      var cls = w >= 75 ? "pr-bar-fill--high" : (w >= 60 ? "pr-bar-fill--mid" : "pr-bar-fill--low");
      html += "<div class=\"pr-bar\"><span class=\"pr-bar-label\">" + escapeHtml(d.country_name_zh) + "</span>";
      html += "<div class=\"pr-bar-track\"><div class=\"pr-bar-fill " + cls + "\" style=\"width:" + w + "%\">" + w + "</div></div>";
      html += "</div>";
    }
    html += "</div>";

    html += "</div>";
    return html;
  }

  // ── Country Tabs ─────────────────────────────────────
  function renderCountryTabs(countries, overview) {
    var codes = Object.keys(countries);
    if (!codes.length) return "";

    var html = "<div class=\"pr-country-detail\"><h3>国家详情</h3>";
    html += "<div class=\"pr-tabs\" id=\"prCountryTabs\">";
    for (var i = 0; i < codes.length; i++) {
      var code = codes[i];
      var cdata = countries[code] || {};
      html += "<button class=\"pr-tab" + (i === 0 ? " active" : "") + "\" onclick=\"window._switchCountryTab('" + code + "')\" data-pr-tab=\"" + code + "\">";
      html += escapeHtml(cdata.country_name_zh || code);
      html += "</button>";
    }
    html += "</div>";
    html += "<div id=\"prCountryTabContent\"></div></div>";

    // Render first country
    window._countryTabTimeout = window.setTimeout(function () {
      if (codes.length > 0) renderCountryDetail(codes[0]);
    }, 50);

    return html;
  }

  function renderCountryDetail(code) {
    var cdata = (window._currentCountries || {})[code] || {};
    var decision = cdata.decision || {};
    var scores = cdata.scores || {};
    var marketFit = cdata.market_fit || {};
    var competitor = cdata.competitor_pricing || {};
    var pricing = cdata.pricing_strategy || {};
    var videoFit = cdata.short_video_fit || {};
    var imageFit = cdata.main_image_fit || {};
    var landing = cdata.landing_page_localization || {};
    var risks = cdata.risks || {};
    var recommendations = cdata.recommendations || {};
    var sources = cdata.sources || [];
    var status = cdata.status || "unknown";

    if (status === "failed") {
      document.getElementById("prCountryTabContent").innerHTML = "<p style=\"color:#dc2626\">该国家评估失败：" + escapeHtml((cdata.error || {}).message || "Unknown") + "</p>";
      return;
    }

    var dec = decision.final_decision || "HOLD";
    var tagClass = dec === "GO" ? "pr-tag--success" : (dec === "TEST" ? "pr-tag--warning" : "pr-tag--danger");

    var html = "";
    html += "<div style=\"margin-bottom:12px\"><span class=\"pr-tag " + tagClass + "\">" + escapeHtml(dec) + "</span>";
    html += " 置信度：" + escapeHtml(decision.confidence || "low");
    html += " · 总分：" + (scores.overall_score || 0);
    html += " · " + escapeHtml(decision.one_sentence_reason || "");
    html += "</div>";

    html += "<div class=\"pr-detail-grid\">";

    // Market fit
    html += "<div class=\"pr-detail-section\"><h4>市场适配</h4>";
    html += "<p><strong>定位：</strong>" + escapeHtml(marketFit.local_positioning || "-") + "</p>";
    html += "<p><strong>目标客群：</strong>" + escapeHtml((marketFit.target_segments || []).join("、")) + "</p>";
    html += "<p><strong>需求：</strong>" + escapeHtml(marketFit.demand_summary || "-") + "</p></div>";

    // Competitor pricing
    html += "<div class=\"pr-detail-section\"><h4>竞品定价</h4>";
    var comps = competitor.competitors || [];
    html += "<p>" + escapeHtml(competitor.summary || "-") + "</p>";
    for (var i = 0; i < Math.min(comps.length, 5); i++) {
      var comp = comps[i];
      html += "<p style=\"font-size:11px\">" + escapeHtml(comp.name) + " (" + escapeHtml(comp.platform) + ") — " + (comp.price != null ? comp.price + " " + (comp.currency || "") : "N/A") + "</p>";
    }
    html += "</div>";

    // Pricing strategy (only show if recommendation exists)
    if (pricing.recommended_price && (pricing.recommended_price.amount != null)) {
      html += "<div class=\"pr-detail-section\"><h4>定价参考</h4>";
      html += "<p><strong>推荐售价：</strong>" + pricing.recommended_price.amount + " " + (pricing.recommended_price.currency || "") + "</p>";
      html += "<p><strong>置信度：</strong>" + escapeHtml(pricing.pricing_confidence || "low") + "</p></div>";
    }

    // Video fit
    html += "<div class=\"pr-detail-section\"><h4>短视频适配</h4>";
    html += "<p><strong>决策：</strong>" + escapeHtml(videoFit.final_video_decision || "-") + "</p>";
    html += "<p><strong>Hook 适配：</strong>" + escapeHtml(videoFit.hook_fit || "-") + "</p>";
    html += "<p><strong>语言适配：</strong>" + escapeHtml(videoFit.local_language_fit || "-") + "</p>";
    html += "<p><strong>文化适配：</strong>" + escapeHtml(videoFit.cultural_fit || "-") + "</p></div>";

    // Main image fit
    html += "<div class=\"pr-detail-section\"><h4>主图适配</h4>";
    html += "<p><strong>决策：</strong>" + escapeHtml(imageFit.decision || "-") + "</p>";
    html += "<p>" + escapeHtml((imageFit.issues || []).join("; ")) + "</p></div>";

    // Landing page
    html += "<div class=\"pr-detail-section\"><h4>落地页本地化</h4>";
    html += "<p><strong>难度：</strong>" + (landing.localization_difficulty || 0) + "/100</p>";
    html += "<p><strong>Hero 方向：</strong>" + escapeHtml(landing.hero_direction || "-") + "</p></div>";

    // Risks
    html += "<div class=\"pr-detail-section\"><h4>风险</h4>";
    html += "<p><strong>Claim：</strong>" + escapeHtml((risks.claim_risks || []).join("；")) + "</p>";
    html += "<p><strong>合规：</strong>" + escapeHtml((risks.compliance_risks || []).join("；")) + "</p>";
    html += "<p><strong>履约：</strong>" + escapeHtml((risks.operational_risks || []).join("；")) + "</p></div>";

    html += "</div>";

    // Recommendations
    html += "<div class=\"pr-detail-section\"><h4>推荐动作</h4>";
    html += "<p>" + escapeHtml(recommendations.recommended_positioning || "") + "</p>";
    html += "<p><strong>广告测试角度：</strong>" + escapeHtml((recommendations.ad_test_angles || []).join("；")) + "</p>";
    if (recommendations.first_30_day_test_plan) {
      var plan = recommendations.first_30_day_test_plan;
      html += "<p><strong>30天测试：</strong>优先级 " + escapeHtml(plan.test_priority || "medium") + "</p>";
    }
    html += "</div>";

    // Sources
    if (sources.length) {
      html += "<div class=\"pr-detail-section\"><h4>来源链接 (" + sources.length + ")</h4>";
      for (var s = 0; s < Math.min(sources.length, 10); s++) {
        var src = sources[s];
        html += "<p style=\"font-size:11px\"><a href=\"" + escapeHtml(src.url || "#") + "\" target=\"_blank\" rel=\"noopener\">" + escapeHtml(src.title || src.url) + "</a></p>";
      }
      html += "</div>";
    }

    document.getElementById("prCountryTabContent").innerHTML = html;
  }

  window._switchCountryTab = function (code) {
    var tabs = document.querySelectorAll("#prCountryTabs .pr-tab");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle("active", tabs[i].getAttribute("data-pr-tab") === code);
    }
    renderCountryDetail(code);
  };

  // ── Rerun country ────────────────────────────────────
  window._rerunCountry = async function (code) {
    if (!currentRunId) return;
    if (!confirm("确定重新评估 " + code + " 吗？")) return;
    try {
      var url = C.rerunUrlTemplate.replace("{id}", currentRunId).replace("{code}", code);
      var resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force_refresh: true }) });
      if (!resp.ok) {
        var errData = await resp.json().catch(function () { return {}; });
        alert((errData.error || {}).message || "Rerun failed");
        return;
      }
      document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\"><div class=\"pr-loading-spinner\"></div><p>重新评估 " + code + " 中...</p></div>";
      setButtonsState("running");
      poll();
    } catch (err) {
      alert("请求失败：" + err.message);
    }
  };

  // ── Cancel ───────────────────────────────────────────
  async function cancelResearch() {
    if (!currentRunId) return;
    try {
      var url = C.cancelUrlTemplate.replace("{id}", currentRunId);
      await fetch(url, { method: "POST" });
      if (pollTimer) { window.clearTimeout(pollTimer); pollTimer = null; }
      setButtonsState("idle");
      document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">任务已取消</div>";
    } catch (err) {
      alert("取消失败：" + err.message);
    }
  }
  window.cancelResearch = cancelResearch;

  // ── Refresh ──────────────────────────────────────────
  async function refreshResult() {
    if (!currentRunId) return;
    document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\"><div class=\"pr-loading-spinner\"></div><p>刷新中...</p></div>";
    try {
      await loadResult();
    } catch (err) {
      document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">刷新失败：" + escapeHtml(err.message || err) + "</div>";
    }
  }
  window.refreshResult = refreshResult;

  // ── UI helpers ───────────────────────────────────────
  function setButtonsState(state) {
    var startBtn = document.getElementById("prStartBtn");
    var cancelBtn = document.getElementById("prCancelBtn");
    var refreshBtn = document.getElementById("prRefreshBtn");
    var newBtn = document.getElementById("prNewBtn");
    if (state === "running") {
      if (newBtn) newBtn.style.display = "none";
      startBtn.style.display = "none";
      cancelBtn.style.display = "";
      refreshBtn.style.display = "none";
    } else if (state === "done") {
      if (newBtn) newBtn.style.display = "";
      startBtn.style.display = "none";
      cancelBtn.style.display = "none";
      refreshBtn.style.display = "";
    } else {
      // idle
      if (newBtn) newBtn.style.display = "";
      startBtn.style.display = "none";
      cancelBtn.style.display = "none";
      refreshBtn.style.display = "none";
    }
  }

  function statusLabel(s) {
    var map = { queued: "排队中", running: "执行中", completed: "已完成", partially_completed: "部分完成", failed: "失败", cancelled: "已取消" };
    return map[s] || s;
  }

  function stepStatusLabel(s) {
    var map = { pending: "等待", running: "执行中", completed: "完成", failed: "失败", skipped: "跳过" };
    return map[s] || s;
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Project List ──────────────────────────────────────
  async function loadProjectList() {
    var container = document.getElementById("prProjectList");
    if (!container) return;
    try {
      var resp = await fetch(C.createUrl + "?limit=50");
      if (!resp.ok) throw new Error("List fetch failed");
      var json = await resp.json();
      var items = (json.data || {}).items || [];
      renderProjectList(container, items);
    } catch (err) {
      container.innerHTML = "<div style=\"padding:12px;color:var(--text-user-badge);font-size:13px\">加载项目列表失败</div>";
    }
  }

  function renderProjectList(container, items) {
    if (!items.length) {
      container.innerHTML = "<div style=\"padding:20px;text-align:center;color:var(--text-user-badge);font-size:13px\">暂无调研项目，点击上方按钮开始第一个调研</div>";
      return;
    }
    var html = "<div class=\"pr-table-wrap\"><table class=\"pr-table\"><thead><tr>";
    html += "<th>项目名称</th><th>状态</th><th>国家数</th><th>进度</th><th>平均分</th><th>GO/TEST/HOLD</th><th>创建时间</th><th>操作</th>";
    html += "</tr></thead><tbody>";
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var st = item.status || "queued";
      var isStalled = st === "stalled";
      var canResume = st === "failed" || st === "partially_completed" || st === "stalled" || st === "running";
      var isTerminal = st === "completed" || st === "partially_completed" || st === "failed" || st === "cancelled";
      var pct = item.total_steps ? Math.round((item.completed_steps || 0) / Math.max(1, item.total_steps) * 100) : 0;
      html += "<tr style=\"cursor:pointer\" onclick=\"window._viewProject('" + item.research_run_id + "')\">";
      html += "<td><strong>" + escapeHtml(item.display_name) + "</strong></td>";
      html += "<td><span class=\"pr-tag " + (st === "completed" || st === "partially_completed" ? "pr-tag--success" : st === "running" ? "pr-tag--warning" : "pr-tag--danger") + "\">" + escapeHtml(statusLabel(st) + (isStalled ? " (可续跑)" : "")) + "</span></td>";
      html += "<td>" + (item.country_count || 0) + "</td>";
      html += "<td><div class=\"pr-progress-track\" style=\"height:4px;width:80px\"><div class=\"pr-progress-fill\" style=\"width:" + pct + "%\"></div></div><span style=\"font-size:11px\">" + (item.completed_steps || 0) + "/" + (item.total_steps || 0) + "</span></td>";
      html += "<td>" + (item.average_score != null ? item.average_score + "%" : "-") + "</td>";
      html += "<td>" + (item.go_count || 0) + "/" + (item.test_count || 0) + "/" + (item.hold_count || 0) + "</td>";
      html += "<td style=\"font-size:11px\">" + (item.created_at ? item.created_at.substring(0, 10) : "-") + "</td>";
      html += "<td onclick=\"event.stopPropagation()\">";
      if (canResume) {
        html += "<button class=\"pr-btn pr-btn--primary\" style=\"padding:2px 10px;font-size:11px;margin-right:4px\" onclick=\"window._resumeProject('" + item.research_run_id + "')\">续跑</button>";
      }
      html += "<button class=\"pr-btn pr-btn--ghost\" style=\"padding:2px 8px;font-size:11px\" onclick=\"window._viewProject('" + item.research_run_id + "')\">查看</button>";
      html += "</td>";
      html += "</tr>";
    }
    html += "</tbody></table></div>";
    container.innerHTML = html;
  }

  // ── View Project ──────────────────────────────────────
  window._viewProject = async function (runId) {
    currentRunId = runId;
    document.getElementById("prForm").style.display = "none";
    document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\"><div class=\"pr-loading-spinner\"></div><p>正在加载调研结果...</p></div>";
    setButtonsState("done");
    document.getElementById("prRefreshBtn").style.display = "";
    document.getElementById("prStartBtn").style.display = "none";
    document.getElementById("prNewBtn").style.display = "";
    try {
      var url = C.resultUrlTemplate.replace("{id}", runId);
      var resp = await fetch(url);
      if (!resp.ok) throw new Error("Load failed");
      var json = await resp.json();
      researchResult = json.data;
      renderResult(researchResult);
    } catch (err) {
      document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">加载失败：" + escapeHtml(err.message || err) + "</div>";
    }
  };

  // ── Resume Project ────────────────────────────────────
  window._resumeProject = async function (runId) {
    if (!confirm("确定续跑此调研？将从上次中断处继续执行。")) return;
    currentRunId = runId;
    document.getElementById("prForm").style.display = "none";
    document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\"><div class=\"pr-loading-spinner\"></div><p>正在续跑调研任务...</p></div>";
    setButtonsState("running");
    try {
      var resp = await fetch(C.resumeUrlTemplate.replace("{id}", runId), { method: "POST" });
      if (!resp.ok) {
        var errData = await resp.json().catch(function () { return {}; });
        document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">续跑失败：" + escapeHtml((errData.error || {}).message || "Unknown error") + "</div>";
        setButtonsState("idle");
        return;
      }
      poll();
    } catch (err) {
      document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">续跑请求失败：" + escapeHtml(err.message || err) + "</div>";
      setButtonsState("idle");
    }
  };

  // ── New Project / Show Form ───────────────────────────
  function showNewForm() {
    currentRunId = "";
    researchResult = null;
    uploadedAssets = {};
    document.getElementById("prForm").style.display = "";
    document.getElementById("prBody").innerHTML = "<div class=\"pr-loading\">填写产品信息后点击\"开始 AI 调研\"</div>";
    document.getElementById("prMainImageName").textContent = "";
    document.getElementById("prVideoName").textContent = "";
    document.getElementById("prProductUrl").value = "";
    document.getElementById("prProductName").value = "";
    document.getElementById("prProductNameEn").value = "";
    document.getElementById("prNotes").value = "";
    // Show start button for form submission, hide new/list buttons
    document.getElementById("prStartBtn").style.display = "";
    document.getElementById("prCancelBtn").style.display = "none";
    document.getElementById("prRefreshBtn").style.display = "none";
    document.getElementById("prNewBtn").style.display = "none";
  }
  window._showNewForm = showNewForm;

  // ── Init ──────────────────────────────────────────────
  function init() {
    loadProjectList();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();