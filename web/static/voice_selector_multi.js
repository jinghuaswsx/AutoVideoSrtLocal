(function () {
  const root = document.getElementById("voice-selector-multi");
  if (!root) return;
  const config = window.TASK_WORKBENCH_CONFIG || {};
  const taskId = root.dataset.taskId || config.taskId;
  const lang = root.dataset.lang || config.voiceLanguage || "en";
  const apiBase = ((window.TASK_WORKBENCH_CONFIG || {}).apiBase || '/api/multi-translate').replace(/\/$/, '');
  const detailMode = config.detailMode || root.dataset.detailMode || "multi";
  const subtitlePreviewUrl = `${apiBase}/${taskId}/subtitle-preview`;
  const sourceVideoArtifactUrl = `${apiBase}/${taskId}/artifact/source_video`;
  const hardVideoArtifactUrl = `${apiBase}/${taskId}/artifact/hard_video`;
  void detailMode;

  function safeMediaSrc(url, opts) {
    const raw = String(url == null ? "" : url).trim();
    if (!raw) return "";
    if (opts && opts.allowBlob && raw.startsWith("blob:")) return raw;
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return parsed.href;
      return parsed.pathname + parsed.search + parsed.hash;
    } catch (_) {
      return "";
    }
  }

  // 把音色选择器挪到 ASR 步骤卡之后，跟业务顺序（上传→提取→ASR→选音色→后续）一致
  function repositionAfterAsr() {
    const anchor = document.getElementById("step-asr");
    if (!anchor || !anchor.parentNode) return;
    if (anchor.nextSibling !== root) {
      anchor.parentNode.insertBefore(root, anchor.nextSibling);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", repositionAfterAsr);
  } else {
    repositionAfterAsr();
  }

  const summaryEl = document.getElementById("vs-summary");
  const listEl = document.getElementById("vs-list");
  const selectionText = document.getElementById("vs-selection-text");
  const launchBtn = document.getElementById("vs-launch-btn");
  const searchInput = document.getElementById("vs-search");
  const voiceSelect = document.getElementById("vs-voice-select");
  const openModalBtn = document.getElementById("vs-open-modal-btn");
  const modalEl = document.getElementById("vs-voice-modal");
  const modalListEl = document.getElementById("vs-modal-list");
  const modalCountEl = document.getElementById("vs-modal-count");
  const modalCloseBtn = document.getElementById("vs-modal-close-btn");
  const genderFilter = document.getElementById("vs-gender-filter");
  const recommendedOnly = document.getElementById("vs-recommended-only");
  const aiRankDebugBtn = document.getElementById("vs-ai-rank-debug-btn");
  const aiRankModalEl = document.getElementById("vs-ai-rank-modal");
  const aiRankCloseBtn = document.getElementById("vs-ai-rank-close-btn");
  const aiRankModalStatus = document.getElementById("vs-ai-rank-modal-status");
  const aiRankRequestVisual = document.getElementById("vs-ai-rank-request-visual");
  const aiRankRequestRaw = document.getElementById("vs-ai-rank-request-raw");
  const aiRankResultVisual = document.getElementById("vs-ai-rank-result-visual");
  const aiRankResultRaw = document.getElementById("vs-ai-rank-result-raw");

  // 字幕参数输入
  const subFontEl = document.getElementById("vs-sub-font");
  const subFontPreview = document.getElementById("vs-sub-font-preview");
  const subSizeGroup = document.getElementById("vs-size-group");
  const subPosYEl = document.getElementById("vs-sub-position-y");
  const subPosHint = document.getElementById("vs-sub-pos-hint");
  const previewFrame = document.getElementById("vsPreviewFrame");
  const previewVideo = document.getElementById("vsPreviewVideo");
  const previewSubtitle = document.getElementById("vsPreviewSubtitle");
  const previewNote = document.getElementById("vsPreviewNote");

  // 结果视频（右侧对比框）
  const resultVideo = document.getElementById("vsResultVideo");
  const resultPlaceholder = document.getElementById("vsResultPlaceholder");

  // 拖拽/点击加载
  const fileInput = document.getElementById("vsFileInput");
  const frameHint = document.getElementById("vsFrameHint");

  let subSize = 14;  // 字号状态（由按钮组驱动）

  // 字体预览：下拉变化 → 预览文字换字体
  let previewDragging = false;
  let previewFallbackTimer = null;
  let previewPayloadVideoUrl = "";

  const FONT_FAMILIES = {
    "Impact": 'Impact, Haettenschweiler, "Arial Narrow Bold", sans-serif',
    "Oswald Bold": '"Oswald", Impact, "Arial Narrow Bold", sans-serif',
    "Bebas Neue": '"Bebas Neue", Impact, "Arial Narrow Bold", sans-serif',
    "Montserrat ExtraBold": '"Montserrat", "Arial Black", sans-serif',
    "Poppins Bold": '"Poppins", "Arial Black", sans-serif',
    "Anton": '"Anton", Impact, sans-serif',
  };

  function coerceSubtitleSize(value) {
    const next = parseInt(value, 10);
    return Number.isFinite(next) ? next : 14;
  }

  function coerceSubtitlePositionY(value) {
    const next = parseFloat(value);
    if (!Number.isFinite(next)) return 0.68;
    return Math.max(0.12, Math.min(0.92, next));
  }

  function setSubtitleSize(value) {
    subSize = coerceSubtitleSize(value);
    subSizeGroup.querySelectorAll("button[data-size]").forEach(btn => {
      btn.classList.toggle("active", coerceSubtitleSize(btn.dataset.size) === subSize);
    });
    syncSubtitlePreview();
  }

  function setSubtitlePositionY(value) {
    subPosYEl.value = String(coerceSubtitlePositionY(value));
    updatePosHint();
  }

  function setSubtitleFont(value) {
    const next = value || "Impact";
    const option = Array.from(subFontEl.options || []).find(opt => opt.value === next);
    if (option) subFontEl.value = option.value;
    updateFontPreview();
  }

  function updateFontPreview() {
    const val = subFontEl.value || "Impact";
    const weight = subFontEl.selectedOptions[0]?.dataset?.weight || "700";
    subFontPreview.style.fontFamily = `"${val}", sans-serif`;
    subFontPreview.style.fontWeight = weight;
    syncSubtitlePreview();
  }
  subFontEl.addEventListener("change", updateFontPreview);
  updateFontPreview();

  // 字号按钮组：点击切换 active
  subSizeGroup.addEventListener("click", e => {
    const btn = e.target.closest("button[data-size]");
    if (!btn) return;
    setSubtitleSize(btn.dataset.size);
  });

  // 位置滑块：实时回显百分比
  function updatePosHint() {
    const v = parseFloat(subPosYEl.value) || 0;
    subPosHint.textContent = `${Math.round(v * 100)}%`;
    syncSubtitlePreview();
  }
  subPosYEl.addEventListener("input", updatePosHint);
  updatePosHint();

  function syncSubtitlePreview() {
    if (!previewSubtitle) return;
    const value = subFontEl.value || "Impact";
    previewSubtitle.style.fontFamily = FONT_FAMILIES[value] || FONT_FAMILIES.Impact;
    previewSubtitle.style.fontSize = `${subSize}px`;
    previewSubtitle.style.top = `${(parseFloat(subPosYEl.value) || 0.68) * 100}%`;
  }

  function setPreviewNote(message, mode) {
    if (!previewNote) return;
    previewNote.textContent = message;
    previewNote.dataset.mode = mode || "note";
  }

  function tryAttachPreviewVideo() {
    if (!previewVideo) return false;
    const sourceVideo = document.querySelector("#preview-extract video.media-player, #preview-asr video.media-player, video.media-player");
    const videoSrc = safeMediaSrc(sourceVideo && sourceVideo.getAttribute("src"), { allowBlob: true });
    if (!videoSrc) {
      setPreviewNote("当前还没有可复用的视频预览，等原视频预览加载后这里会自动同步。", "note");
      return false;
    }
    if (previewVideo.getAttribute("src") === videoSrc) {
      return true;
    }
    previewVideo.src = videoSrc;
    previewVideo.load();
    markVideoLoaded();
    setPreviewNote("已复用当前任务的原始视频预览，字幕会直接叠加在真实画面上。", "success");
    return true;
  }

  function markVideoLoaded() {
    if (previewFrame) previewFrame.classList.add("video-loaded");
  }

  function attachPreviewVideo(src, message) {
    const videoSrc = safeMediaSrc(src, { allowBlob: true });
    if (!previewVideo || !videoSrc) return false;
    if (previewVideo.getAttribute("src") === videoSrc) return true;
    previewPayloadVideoUrl = videoSrc;
    previewVideo.preload = "metadata";
    previewVideo.src = videoSrc;
    previewVideo.load();
    markVideoLoaded();
    if (message) setPreviewNote(message, "success");
    return true;
  }

  // 尝试从 artifact 端点加载源视频（最可靠的方式）
  function tryLoadSourceVideo() {
    if (!previewVideo) return false;
    if (previewVideo.getAttribute("src")) return true; // 已经有视频了
    var artifactUrl = sourceVideoArtifactUrl;
    return fetch(artifactUrl, { method: "HEAD" }).then(function (res) {
      if (res.ok) {
        attachPreviewVideo(artifactUrl, "已加载上传的源视频，可直接检查字幕位置和字号。");
        return true;
      }
      return false;
    }).catch(function () { return false; });
  }

  function schedulePreviewReuseFallback() {
    if (previewFallbackTimer) return;
    if (tryAttachPreviewVideo()) return;
    // 先尝试从 artifact 端点加载，失败后再轮询 DOM
    tryLoadSourceVideo().then(function (ok) {
      if (ok) return;
      let previewRetries = 0;
      previewFallbackTimer = setInterval(() => {
        previewRetries += 1;
        if (tryAttachPreviewVideo() || previewRetries >= 20) {
          clearInterval(previewFallbackTimer);
          previewFallbackTimer = null;
        }
      }, 1000);
    });
  }

  function applySubtitlePreviewPayload(payload) {
    const data = payload || {};
    if (data.subtitle_font) setSubtitleFont(data.subtitle_font);
    if (data.subtitle_size !== undefined && data.subtitle_size !== null) {
      setSubtitleSize(data.subtitle_size);
    }
    if (data.subtitle_position_y !== undefined && data.subtitle_position_y !== null) {
      setSubtitlePositionY(data.subtitle_position_y);
    }

    const videoUrl = safeMediaSrc(data.video_url || "", { allowBlob: true });
    if (videoUrl) {
      attachPreviewVideo(videoUrl, "已加载当前任务的英文原版视频，字幕样式会在这里实时预览。");
      return;
    }
    schedulePreviewReuseFallback();
  }

  async function loadSubtitlePreviewPayload() {
    setPreviewNote("正在加载英文原版视频预览...", "note");
    try {
      const resp = await fetch(subtitlePreviewUrl, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      applySubtitlePreviewPayload(await resp.json());
    } catch (err) {
      console.error("[voice-selector] subtitle preview payload failed:", err);
      setPreviewNote("英文原版视频预览加载失败，正在尝试复用页面里的原始视频预览。", "error");
      schedulePreviewReuseFallback();
    }
  }

  function updatePreviewPosition(clientY) {
    if (!previewFrame) return;
    const rect = previewFrame.getBoundingClientRect();
    if (!rect.height) return;
    const ratio = Math.max(0.12, Math.min(0.92, (clientY - rect.top) / rect.height));
    subPosYEl.value = String(ratio);
    updatePosHint();
  }

  if (previewFrame && previewSubtitle) {
    previewSubtitle.addEventListener("pointerdown", (event) => {
      previewDragging = true;
      previewSubtitle.setPointerCapture(event.pointerId);
      updatePreviewPosition(event.clientY);
    });
    previewSubtitle.addEventListener("pointermove", (event) => {
      if (!previewDragging) return;
      updatePreviewPosition(event.clientY);
    });
    const endPreviewDrag = (event) => {
      previewDragging = false;
      try {
        previewSubtitle.releasePointerCapture(event.pointerId);
      } catch (_error) {
        // ignore
      }
    };
    previewSubtitle.addEventListener("pointerup", endPreviewDrag);
    previewSubtitle.addEventListener("pointercancel", endPreviewDrag);
  }

  if (previewVideo) {
    previewVideo.addEventListener("loadeddata", () => {
      if (previewPayloadVideoUrl && previewVideo.getAttribute("src") === previewPayloadVideoUrl) {
        setPreviewNote("英文原版视频已加载，可直接检查字幕位置和字号。", "success");
      }
    });
    previewVideo.addEventListener("error", () => {
      if (!previewPayloadVideoUrl || previewVideo.getAttribute("src") !== previewPayloadVideoUrl) return;
      setPreviewNote("英文原版视频加载失败，正在等待页面里的原始视频预览同步。", "error");
      schedulePreviewReuseFallback();
    });
  }

  syncSubtitlePreview();
  loadSubtitlePreviewPayload();

  // ── 结果视频加载（右侧对比框） ──
  let resultVideoLoaded = false;

  function loadResultVideo(src) {
    const videoSrc = safeMediaSrc(src);
    if (!resultVideo || !videoSrc || resultVideoLoaded) return;
    resultVideoLoaded = true;
    resultVideo.src = videoSrc;
    resultVideo.style.display = "block";
    resultVideo.pause();
    resultVideo.currentTime = 0;
    resultVideo.load();
    if (resultPlaceholder) resultPlaceholder.style.display = "none";
  }

  function checkResultVideo() {
    if (resultVideoLoaded) return;
    // 直接尝试 artifact URL（如果 compose 已完成，文件已存在）
    var testUrl = hardVideoArtifactUrl;
    fetch(testUrl, { method: "HEAD" }).then(function (res) {
      if (res.ok) loadResultVideo(testUrl);
    }).catch(function () {});
  }

  // 页面加载后延迟检查一次（compose 可能早已完成）
  setTimeout(checkResultVideo, 1500);

  // 监听 workbench 的 socket 事件（socket 是全局变量，由 _task_workbench_scripts.html 初始化）
  if (typeof socket !== "undefined" && socket) {
    socket.on("step_update", function (data) {
      if (data && data.step === "compose" && data.status === "done") {
        // compose 刚完成，等一小段时间让 preview_files 写入后再拉
        setTimeout(checkResultVideo, 800);
      }
    });
    socket.on("pipeline_done", function () {
      setTimeout(checkResultVideo, 500);
    });
  }

  // ── 拖拽 / 点击加载视频到预览框 ──
  if (previewFrame && previewVideo) {
    // 点击预览框 → 打开文件选择
    previewFrame.addEventListener("click", function (e) {
      // 避免和字幕拖拽、视频控件冲突
      if (e.target.closest(".vs-preview-subtitle") || e.target.closest("video") && e.target.controls) return;
      if (fileInput) fileInput.click();
    });

    // 文件选择后加载
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        var file = fileInput.files && fileInput.files[0];
        if (!file) return;
        var url = URL.createObjectURL(file);
        attachPreviewVideo(url, "已加载本地视频文件：" + file.name);
        fileInput.value = "";
      });
    }

    // 拖拽事件
    previewFrame.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.stopPropagation();
      previewFrame.classList.add("drag-over");
    });
    previewFrame.addEventListener("dragleave", function (e) {
      e.preventDefault();
      e.stopPropagation();
      previewFrame.classList.remove("drag-over");
    });
    previewFrame.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      previewFrame.classList.remove("drag-over");
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      var file = files[0];
      if (!file.type.startsWith("video/")) {
        setPreviewNote("请拖入视频文件（mp4/mov/webm 等）。", "error");
        return;
      }
      var url = URL.createObjectURL(file);
      attachPreviewVideo(url, "已加载拖入的视频文件：" + file.name);
    });
  }

  const csrfToken = () => {
    const el = document.querySelector("meta[name=csrf-token]");
    return el ? el.content : "";
  };

  let allItems = [];
  let candidatesMap = new Map();
  let candidatesRankMap = new Map();
  let similarityRankMap = new Map();
  let voiceAiRankDebug = null;
  let voiceAiRankStatus = "";
  let selectedVoiceId = null;
  let selectedVoiceName = null;
  let launched = false;
  let pollHandle = null;
  let pollDelay = 3000;
  let pollStartTime = 0;
  let voiceMatchReadyFrozen = false;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;
  let activeGender = null;       // null | "male" | "female"（由胶囊按钮驱动）
  let rematching = false;
  let modalTriggerEl = null;
  let pendingReloadState = null;
  let libraryRequestSeq = 0;
  let loadedVoiceIds = new Set();
  let nextVoicePage = 1;
  let voiceTotal = 0;
  let voiceHasMore = true;
  let voicePageLoading = false;
  let searchReloadTimer = null;
  const VOICE_PAGE_SIZE = 30;
  const RELOAD_STATE_KEY = `voice-selector-state:${taskId || "unknown"}`;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function prettyJson(value) {
    try {
      return JSON.stringify(value == null ? {} : value, null, 2);
    } catch (_err) {
      return String(value == null ? "" : value);
    }
  }

  function findActiveVoiceElement() {
    const active = document.activeElement;
    if (!active || typeof active.closest !== "function") return null;
    const row = active.closest(".vs-row[data-voice-id]");
    if (!row) return null;
    if (listEl && listEl.contains(row)) return row;
    if (modalListEl && modalListEl.contains(row)) return row;
    return null;
  }

  function cssEscapeValue(value) {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(String(value));
    }
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function captureRenderState() {
    const activeVoiceElement = findActiveVoiceElement();
    return {
      listScrollTop: listEl ? listEl.scrollTop : 0,
      modalScrollTop: modalListEl ? modalListEl.scrollTop : 0,
      activeVoiceId: activeVoiceElement ? activeVoiceElement.dataset.voiceId : null,
      activeInModal: !!(activeVoiceElement && modalListEl && modalListEl.contains(activeVoiceElement)),
    };
  }

  function focusVoiceRow(container, voiceId) {
    if (!container || !voiceId) return false;
    const row = container.querySelector(`.vs-row[data-voice-id="${cssEscapeValue(voiceId)}"]`);
    if (!row) return false;
    try {
      row.focus({ preventScroll: true });
    } catch (_err) {
      row.focus();
    }
    return true;
  }

  function restoreRenderState(state) {
    if (!state) return;
    requestAnimationFrame(() => {
      if (listEl) listEl.scrollTop = state.listScrollTop || 0;
      if (modalListEl) modalListEl.scrollTop = state.modalScrollTop || 0;
      const focusContainer = state.activeInModal ? modalListEl : listEl;
      focusVoiceRow(focusContainer, state.activeVoiceId);
    });
  }

  function updateGenderPills() {
    if (!genderFilter) return;
    genderFilter.querySelectorAll(".vs-pill").forEach(b => {
      const on = b.dataset.gender === activeGender;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });
  }

  function currentModalOpen() {
    return !!(modalEl && !modalEl.hidden);
  }

  function currentAiRankModalOpen() {
    return !!(aiRankModalEl && !aiRankModalEl.hidden);
  }

  function updateVoiceAiRankDebugButton(status) {
    voiceAiRankStatus = status || voiceAiRankStatus || "";
    if (!aiRankDebugBtn) return;
    const hasDebug = !!voiceAiRankDebug;
    aiRankDebugBtn.hidden = !hasDebug;
    aiRankDebugBtn.disabled = !hasDebug;
  }

  function setAiRankTab(name) {
    if (!aiRankModalEl) return;
    aiRankModalEl.querySelectorAll("[data-ai-rank-tab]").forEach(btn => {
      const active = btn.dataset.aiRankTab === name;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    aiRankModalEl.querySelectorAll("[data-ai-rank-panel]").forEach(panel => {
      panel.classList.toggle("active", panel.dataset.aiRankPanel === name);
    });
  }

  function debugCards(items) {
    const html = (items || []).map(item => `
      <div class="vs-ai-rank-card">
        <strong>${escapeHtml(item[0])}</strong>
        <span>${escapeHtml(item[1] == null || item[1] === "" ? "-" : item[1])}</span>
      </div>
    `).join("");
    return html ? `<div class="vs-ai-rank-grid">${html}</div>` : `<div class="vs-loading">暂无数据</div>`;
  }

  function voiceAiRankAudioHtml(item) {
    const rel = item && item.relative_path ? String(item.relative_path) : "";
    if (!rel) return "";
    const src = safeMediaSrc(`${apiBase}/${taskId}/artifact-path?path=${encodeURIComponent(rel)}`);
    return src ? `<audio controls preload="none" src="${escapeHtml(src)}"></audio>` : "";
  }

  function renderVoiceAiRankRequestVisual(debug) {
    const request = (debug && debug.request) || {};
    const visual = request.visual || {};
    const media = Array.isArray(visual.media) ? visual.media : [];
    const candidates = Array.isArray(visual.candidates) ? visual.candidates : [];
    const mediaHtml = media.length ? media.map((item, index) => `
      <div class="vs-ai-rank-card">
        <strong>${index + 1}. ${escapeHtml(item.role || "audio")}</strong>
        <span>${escapeHtml(item.voice_id || item.filename || "-")}</span>
        <span>${escapeHtml(item.relative_path || item.path || "")}</span>
        <span>${escapeHtml(item.source || "")}${item.bytes ? ` · ${escapeHtml(item.bytes)} bytes` : ""}</span>
        ${voiceAiRankAudioHtml(item)}
      </div>
    `).join("") : `<div class="vs-loading">暂无音频信息</div>`;
    const candidateHtml = candidates.length ? candidates.map(item => `
      <div class="vs-ai-rank-card">
        <strong>#${escapeHtml(item.match_order || "-")} ${escapeHtml(item.name || item.voice_id || "-")}</strong>
        <span>${escapeHtml(item.voice_id || "-")}</span>
        <span>相似度 ${escapeHtml(item.similarity || "-")} · 语速 ${escapeHtml(item.speed_match_score || "-")}</span>
        <span>${escapeHtml(item.audio_ref || "")}</span>
      </div>
    `).join("") : `<div class="vs-loading">暂无候选信息</div>`;
    return `
      ${debugCards([
        ["Provider", debug && debug.provider],
        ["Model", debug && debug.model],
        ["状态", debug && debug.status],
        ["目标语言", visual.target_lang],
      ])}
      <div class="vs-ai-rank-section-title">音频文件</div>
      <div class="vs-ai-rank-grid">${mediaHtml}</div>
      <div class="vs-ai-rank-section-title">候选音色</div>
      <div class="vs-ai-rank-grid">${candidateHtml}</div>
    `;
  }

  function renderVoiceAiRankResultVisual(debug) {
    const result = (debug && debug.result) || {};
    const visual = result.visual || {};
    const rankings = Array.isArray(visual.rankings) ? visual.rankings : [];
    if (!rankings.length) return `<div class="vs-loading">暂无排名结果</div>`;
    return `<div class="vs-ai-rank-grid">${rankings.map(row => `
      <div class="vs-ai-rank-card">
        <strong>AI #${escapeHtml(row.llm_rank || "-")} · ${escapeHtml(row.voice_id || "-")}</strong>
        <span>${escapeHtml(row.reason_summary || "")}</span>
      </div>
    `).join("")}</div>`;
  }

  function renderVoiceAiRankDebugModal(tab) {
    const debug = voiceAiRankDebug || {};
    if (aiRankModalStatus) aiRankModalStatus.textContent = voiceAiRankStatus || debug.status || "done";
    if (aiRankRequestVisual) aiRankRequestVisual.innerHTML = renderVoiceAiRankRequestVisual(debug);
    if (aiRankRequestRaw) aiRankRequestRaw.textContent = prettyJson((debug.request || {}).raw || {});
    if (aiRankResultVisual) aiRankResultVisual.innerHTML = renderVoiceAiRankResultVisual(debug);
    if (aiRankResultRaw) aiRankResultRaw.textContent = prettyJson((debug.result || {}).raw || {});
    setAiRankTab(tab || "request");
  }

  function openVoiceAiRankModal(tab) {
    if (!aiRankModalEl || !voiceAiRankDebug) return;
    renderVoiceAiRankDebugModal(tab || "request");
    aiRankModalEl.hidden = false;
    document.body.classList.add("vs-ai-rank-modal-open");
  }

  function closeVoiceAiRankModal() {
    if (!aiRankModalEl) return;
    aiRankModalEl.hidden = true;
    document.body.classList.remove("vs-ai-rank-modal-open");
  }

  function saveReloadState() {
    try {
      sessionStorage.setItem(RELOAD_STATE_KEY, JSON.stringify({
        scrollY: window.scrollY || window.pageYOffset || 0,
        listScrollTop: listEl ? listEl.scrollTop : 0,
        modalScrollTop: modalListEl ? modalListEl.scrollTop : 0,
        selectedVoiceId,
        selectedVoiceName,
        activeGender,
        search: searchInput ? searchInput.value : "",
        recommendedOnly: recommendedOnly ? recommendedOnly.checked : false,
        modalOpen: currentModalOpen(),
        activeVoiceId: (findActiveVoiceElement() || {}).dataset?.voiceId || selectedVoiceId,
      }));
    } catch (_err) {
      // sessionStorage may be unavailable in private or restricted browser contexts.
    }
  }

  function restoreReloadState() {
    let saved = null;
    try {
      const raw = sessionStorage.getItem(RELOAD_STATE_KEY);
      if (!raw) return null;
      sessionStorage.removeItem(RELOAD_STATE_KEY);
      saved = JSON.parse(raw) || null;
    } catch (_err) {
      return null;
    }
    if (!saved) return null;
    if (searchInput && typeof saved.search === "string") searchInput.value = saved.search;
    if (recommendedOnly) recommendedOnly.checked = !!saved.recommendedOnly;
    activeGender = saved.activeGender === "male" || saved.activeGender === "female" ? saved.activeGender : null;
    selectedVoiceId = saved.selectedVoiceId || selectedVoiceId;
    selectedVoiceName = saved.selectedVoiceName || selectedVoiceName;
    updateGenderPills();
    return saved;
  }

  function applyPendingReloadState() {
    const saved = pendingReloadState;
    if (!saved) return;
    pendingReloadState = null;
    if (saved.modalOpen) openVoiceModal({ restoreFocus: false });
    requestAnimationFrame(() => {
      window.scrollTo({ top: saved.scrollY || 0, left: 0, behavior: "auto" });
      if (listEl) listEl.scrollTop = saved.listScrollTop || 0;
      if (modalListEl) modalListEl.scrollTop = saved.modalScrollTop || 0;
      const focusContainer = saved.modalOpen ? modalListEl : listEl;
      focusVoiceRow(focusContainer, saved.activeVoiceId || saved.selectedVoiceId);
    });
  }

  function describePipeline(pipeline) {
    const { extract, asr, voice_match } = pipeline || {};
    if (voice_match === "waiting" || voice_match === "done") return null;
    if (voice_match === "running") return "正在向量匹配中…";
    if (asr === "running") return "🎙️ 语音识别中（ASR）…";
    if (asr === "done") return "ASR 完成，等待向量匹配启动…";
    if (extract === "running") return "🔈 音频提取中…";
    if (extract === "done") return "音频提取完成，等 ASR 启动…";
    return "管道等待启动…";
  }

  function mergeVoiceItems(target, incoming, seen) {
    (incoming || []).forEach(item => {
      const id = String(item && item.voice_id || "").trim();
      if (!id || seen.has(id)) return;
      target.push(item);
      seen.add(id);
    });
  }

  function markVoiceMatchReadyFrozen() {
    voiceMatchReadyFrozen = true;
    if (pollHandle) {
      clearTimeout(pollHandle);
      pollHandle = null;
    }
    pollDelay = 3000;
    pollStartTime = 0;
  }

  function shouldSkipAutomaticLibraryRefresh() {
    return voiceMatchReadyFrozen;
  }

  function normalizeSimilarityRank(value) {
    const rank = Number(value);
    if (!Number.isFinite(rank)) return null;
    const normalized = Math.trunc(rank);
    return normalized >= 1 && normalized <= 20 ? normalized : null;
  }

  function seedSimilarityRankFallbacks(candidates) {
    (candidates || [])
      .map((candidate, index) => ({
        voiceId: String(candidate && candidate.voice_id || "").trim(),
        similarity: Number(candidate && candidate.similarity),
        index,
      }))
      .filter(row => row.voiceId && Number.isFinite(row.similarity))
      .sort((a, b) => {
        if (b.similarity !== a.similarity) return b.similarity - a.similarity;
        return a.index - b.index;
      })
      .slice(0, 20)
      .forEach((row, index) => {
        similarityRankMap.set(row.voiceId, index + 1);
      });
  }

  function setVoiceMatchCandidates(candidates) {
    candidatesMap.clear();
    candidatesRankMap.clear();
    similarityRankMap.clear();
    seedSimilarityRankFallbacks(candidates);
    (candidates || []).forEach((candidate, index) => {
      const voiceId = String(candidate && candidate.voice_id || "").trim();
      if (!voiceId) return;
      candidatesMap.set(voiceId, candidate);
      candidatesRankMap.set(voiceId, index);
      const similarityRank = normalizeSimilarityRank(candidate.similarity_rank);
      if (similarityRank !== null) {
        similarityRankMap.set(voiceId, similarityRank);
      }
    });
  }

  function currentVoiceSearch() {
    return (searchInput && searchInput.value || "").trim();
  }

  function resetVoicePaging() {
    allItems = [];
    loadedVoiceIds = new Set();
    nextVoicePage = 1;
    voiceTotal = 0;
    voiceHasMore = true;
  }

  async function fetchVoiceLibraryPage(page) {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(VOICE_PAGE_SIZE),
    });
    if (activeGender) params.set("gender", activeGender);
    const q = currentVoiceSearch();
    if (q) params.set("q", q);
    const resp = await fetch(`${apiBase}/${taskId}/voice-library?${params.toString()}`);
    if (!resp.ok) {
      const detail = escapeHtml(await resp.text());
      listEl.innerHTML = `<div class="vs-loading">加载失败：${detail}</div>`;
      if (modalListEl) modalListEl.innerHTML = `<div class="vs-loading">加载失败：${detail}</div>`;
      return null;
    }
    return resp.json();
  }

  async function loadVoicePage(options = {}) {
    const reset = !!options.reset;
    if (voicePageLoading && !reset) return;
    if (!voiceHasMore && !reset) return;
    if (reset) {
      resetVoicePaging();
      libraryRequestSeq += 1;
      render("音色库加载中...");
    } else if (!libraryRequestSeq) {
      libraryRequestSeq = 1;
    }
    const seq = libraryRequestSeq;
    const pageToLoad = nextVoicePage;
    voicePageLoading = true;
    try {
      const data = await fetchVoiceLibraryPage(pageToLoad);
      if (!data || seq !== libraryRequestSeq) return;
      setVoiceMatchCandidates(data.candidates || []);
      voiceAiRankDebug = data.voice_ai_rank_debug || null;
      updateVoiceAiRankDebugButton(data.voice_ai_rank_status || "");
      mergeVoiceItems(allItems, data.items || [], loadedVoiceIds);
      selectedVoiceId = data.selected_voice_id || null;
      if (selectedVoiceId && !selectedVoiceName) {
        const selected = allItems.find(v => v.voice_id === selectedVoiceId);
        selectedVoiceName = selected ? (selected.name || selected.voice_id) : null;
      }
      voiceTotal = Number(data.total || 0);
      const responsePage = Number(data.page || pageToLoad);
      const responsePageSize = Number(data.page_size || VOICE_PAGE_SIZE);
      nextVoicePage = responsePage + 1;
      voiceHasMore = responsePage * responsePageSize < voiceTotal;

      const n = (data.candidates || []).length;
      const ready = !!data.voice_match_ready;
      const progress = describePipeline(data.pipeline);

      if (!ready) {
        summaryEl.textContent = `${lang.toUpperCase()} 音色库共 ${voiceTotal || 0} 个 · 已加载 ${allItems.length} 个 · ${progress}`;
        setTimeout(() => render(progress), 0);
        schedulePoll();
      } else {
        markVoiceMatchReadyFrozen();
        const parts = [`${lang.toUpperCase()} 音色库共 ${voiceTotal || 0} 个`, `已加载 ${allItems.length} 个`];
        if (n > 0) parts.push(`${n} 个向量匹配推荐`);
        else parts.push("向量匹配未找到相似音色");
        summaryEl.textContent = parts.join(" · ");
        render(null);
      }

      updateLaunchState();
      applyPendingReloadState();
    } catch (err) {
      console.error("[voice-selector] load failed:", err);
      listEl.innerHTML = `<div class="vs-loading">网络错误，5s 后重试</div>`;
      if (modalListEl) modalListEl.innerHTML = `<div class="vs-loading">网络错误，5s 后重试</div>`;
      schedulePoll(5000);
    } finally {
      if (seq === libraryRequestSeq) voicePageLoading = false;
    }
  }

  async function loadLibrary(options = {}) {
    if (options.automatic) {
      if (shouldSkipAutomaticLibraryRefresh()) return;
    } else {
      voiceMatchReadyFrozen = false;
    }
    return loadVoicePage({ reset: true });
  }

  function reloadVoiceLibrarySoon() {
    if (searchReloadTimer) clearTimeout(searchReloadTimer);
    searchReloadTimer = setTimeout(() => {
      searchReloadTimer = null;
      loadLibrary();
    }, 250);
  }

  function maybeLoadMoreVoices(event) {
    if (launched || voicePageLoading || !voiceHasMore) return;
    if (recommendedOnly && recommendedOnly.checked) return;
    const el = event && event.currentTarget;
    if (!el || !el.scrollHeight) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 240) {
      loadVoicePage();
    }
  }

  function schedulePoll(delay) {
    if (launched) return;
    if (!pollStartTime) pollStartTime = Date.now();
    if (Date.now() - pollStartTime >= POLL_TIMEOUT_MS) {
      if (pollHandle) { clearTimeout(pollHandle); pollHandle = null; }
      summaryEl.textContent += " · 轮询已超时，请刷新页面或等待任务完成";
      return;
    }
    if (delay === undefined) {
      delay = pollDelay;
      pollDelay = Math.min(pollDelay + 1000, 10000);
    }
    if (pollHandle) clearTimeout(pollHandle);
    pollHandle = setTimeout(() => loadLibrary({ automatic: true }), delay);
  }

  function voiceAiRankBadgeHtml(rec) {
    if (!rec) return "";
    const rank = Number(rec.llm_rank);
    const reason = String(rec.llm_reason_summary || "").trim();
    if (Number.isFinite(rank) && rank > 0) {
      const label = `AI #${Math.trunc(rank)}${reason ? ` · ${reason}` : ""}`;
      return `<button type="button" class="vs-row-ai-rank" title="${escapeHtml(reason || "大模型推荐排名")}">${escapeHtml(label)}</button>`;
    }
    return "";
  }

  function rowHtml(v, opts) {
    const { badge, pinClass, isSelected, rec } = opts;
    const classes = ["vs-row"];
    if (pinClass) classes.push(pinClass);
    if (isSelected) classes.push("selected");
    const meta = [v.gender, v.accent, v.age, v.description || v.descriptive || ""]
      .filter(Boolean).map(escapeHtml).join(" · ");
    const previewUrl = safeMediaSrc(v.preview_url);
    const preview = previewUrl
      ? `<audio controls preload="none" src="${escapeHtml(previewUrl)}"></audio>`
      : "";
    const speedMeta = voiceSpeedMetaHtml(rec);
    return `
      <div class="${classes.join(" ")}" data-voice-id="${escapeHtml(v.voice_id)}"
           data-voice-name="${escapeHtml(v.name || '')}" tabindex="0"
           aria-selected="${isSelected ? "true" : "false"}">
        <div class="vs-row-main">
          <div class="vs-row-name">${badge || ""}${escapeHtml(v.name || v.voice_id)}</div>
          <div class="vs-row-meta">${meta}</div>
          ${speedMeta}
        </div>
        ${preview}
        <button class="vs-row-select-btn" type="button">${isSelected ? "已选" : "选此音色"}</button>
      </div>
    `;
  }

  function fmtRate(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? n.toFixed(2) : null;
  }

  function fmtScore(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(0)}%` : null;
  }

  function voiceSpeedMetaHtml(rec) {
    if (!rec) return "";
    const status = String(rec.voice_speed_status || "");
    const sourceRate = fmtRate(rec.source_words_per_second);
    const previewRate = fmtRate(rec.preview_words_per_second);
    const speedScore = fmtScore(rec.speed_match_score);
    if (!previewRate || status === "missing_preview_rate") {
      const sourceText = sourceRate ? `原视频 ${escapeHtml(sourceRate)} 词/秒 · ` : "";
      return `<div class="vs-row-speed vs-row-speed-missing">${sourceText}语速未维护，已按音色排序</div>`;
    }
    return `
      <div class="vs-row-speed">
        ${sourceRate ? `<span>原视频 ${escapeHtml(sourceRate)} 词/秒</span>` : ""}
        <span>Preview ${escapeHtml(previewRate)} 词/秒</span>
        ${speedScore ? `<span class="vs-speed-match-pill"><span class="vs-speed-match-label">语速匹配</span><span class="vs-speed-match-value">${escapeHtml(speedScore)}</span></span>` : ""}
      </div>
    `;
  }

  function sortedVoiceRows() {
    return allItems.map(v => {
      const rec = candidatesMap.get(v.voice_id);
      const voiceMatchRank = rec ? (candidatesRankMap.get(v.voice_id) ?? Number.MAX_SAFE_INTEGER) : Number.MAX_SAFE_INTEGER;
      const voiceMatchSimilarityRank = rec ? similarityRankMap.get(v.voice_id) : null;
      return { v, rec, sim: rec ? rec.similarity : -1, voiceMatchRank, voiceMatchSimilarityRank };
    }).sort((a, b) => {
      if (a.rec && b.rec && a.voiceMatchRank !== b.voiceMatchRank) {
        return a.voiceMatchRank - b.voiceMatchRank;
      }
      if (!!a.rec !== !!b.rec) return a.rec ? -1 : 1;
      return (a.v.name || "").localeCompare(b.v.name || "");
    });
  }

  function filteredVoiceRows() {
    const q = (searchInput.value || "").trim().toLowerCase();
    const gender = activeGender;
    const onlyRec = recommendedOnly.checked;

    const applyFilter = (v) => {
      if (gender && v.gender !== gender) return false;
      if (q) {
        const hay = [v.name, v.description, v.descriptive, v.accent, v.age]
          .filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    };

    return sortedVoiceRows().filter(({ v, rec }) => {
      if (onlyRec && !rec) return false;
      return applyFilter(v);
    });
  }

  function voiceOptionLabel(v, rec) {
    const name = v.name || v.voice_id || "";
    if (rec) {
      return `${(rec.similarity * 100).toFixed(1)}% 相似 · ${name}`;
    }
    return name;
  }

  function syncVoiceSelectOptions(rows, waitingProgress) {
    if (!voiceSelect) return;

    const selectedRow = selectedVoiceId
      ? sortedVoiceRows().find(({ v }) => v.voice_id === selectedVoiceId)
      : null;
    const optionRows = rows.slice();
    if (
      selectedRow &&
      !optionRows.some(({ v }) => v.voice_id === selectedVoiceId)
    ) {
      optionRows.push(selectedRow);
    }

    voiceSelect.innerHTML = "";
    const placeholder = new Option(
      waitingProgress ? "音色库加载中..." : "请选择音色",
      "",
    );
    placeholder.disabled = true;
    voiceSelect.appendChild(placeholder);

    optionRows.forEach(({ v, rec }) => {
      if (!v.voice_id) return;
      const option = new Option(voiceOptionLabel(v, rec), v.voice_id);
      option.dataset.voiceName = v.name || v.voice_id;
      voiceSelect.appendChild(option);
    });

    voiceSelect.disabled = launched || optionRows.length === 0;
    if (selectedVoiceId && optionRows.some(({ v }) => v.voice_id === selectedVoiceId)) {
      voiceSelect.value = selectedVoiceId;
    } else {
      voiceSelect.value = "";
    }
  }

  function bindVoiceRows(container) {
    if (!container) return;
    container.querySelectorAll(".vs-row").forEach(row => {
      row.addEventListener("click", e => {
        if (e.target.closest(".vs-row-ai-rank")) {
          e.preventDefault();
          e.stopPropagation();
          openVoiceAiRankModal("result");
          return;
        }
        if (e.target.tagName === "AUDIO" || e.target.closest("audio")) return;
        try {
          row.focus({ preventScroll: true });
        } catch (_err) {
          row.focus();
        }
        selectVoice(row.dataset.voiceId, row.dataset.voiceName, {
          closeModal: container === modalListEl,
        });
      });
      row.addEventListener("keydown", e => {
        if (e.key !== "Enter" && e.key !== " ") return;
        if (e.target.tagName === "AUDIO" || e.target.closest("audio")) return;
        e.preventDefault();
        selectVoice(row.dataset.voiceId, row.dataset.voiceName, {
          closeModal: container === modalListEl,
        });
      });
    });
  }

  function rowsHtml(rows, waitingProgress) {
    let html = "";
    rows.forEach(({ v, rec, voiceMatchSimilarityRank: rawVoiceMatchSimilarityRank }) => {
      const isRec = !!rec;
      const voiceMatchSimilarityRank = isRec ? rawVoiceMatchSimilarityRank : null;
      const isSelected = selectedVoiceId === v.voice_id;
      const classes = [];
      if (isRec) classes.push("recommended");
      const simBadge = isRec
        ? `<span class="vs-row-sim">${(rec.similarity * 100).toFixed(1)}% 相似</span>`
        : "";
      const rankBadge = isRec && Number.isFinite(voiceMatchSimilarityRank)
        ? `<span class="vs-row-rank">#${voiceMatchSimilarityRank}</span>`
        : "";
      const aiRankBadge = isRec ? voiceAiRankBadgeHtml(rec) : "";
      const badge = `${simBadge}${rankBadge}${aiRankBadge}`;
      html += rowHtml(v, {
        badge, pinClass: classes.join(" "), isSelected, rec,
      });
    });

    if (!html) {
      html = `<div class="vs-loading">${waitingProgress || "没有匹配的音色"}</div>`;
    } else if (waitingProgress) {
      html = `<div class="vs-waiting-banner">等待 ${escapeHtml(waitingProgress)}
        <small style="color:var(--text-user-badge);">可先浏览和试听；向量推荐将在 ASR 完成后自动出现</small></div>` + html;
    }
    return html;
  }

  function renderRowsInto(container, rows, waitingProgress) {
    if (!container) return;
    container.innerHTML = rowsHtml(rows, waitingProgress);
    bindVoiceRows(container);
  }

  function renderVoiceModal(waitingProgress) {
    if (!modalListEl) return;
    const filtered = filteredVoiceRows();
    if (modalCountEl) {
      modalCountEl.textContent = `${filtered.length}/${allItems.length}`;
    }
    renderRowsInto(modalListEl, filtered, waitingProgress);
  }

  function renderVoiceModalIfOpen(waitingProgress) {
    if (!currentModalOpen()) {
      if (modalCountEl) modalCountEl.textContent = `${allItems.length}/${voiceTotal || allItems.length}`;
      return;
    }
    renderVoiceModal(waitingProgress);
  }

  function render(waitingProgress) {
    const renderState = captureRenderState();
    const filtered = filteredVoiceRows();
    syncVoiceSelectOptions(filtered, waitingProgress);
    renderRowsInto(listEl, filtered, waitingProgress);
    renderVoiceModalIfOpen(waitingProgress);
    restoreRenderState(renderState);
    saveReloadState();
  }

  function selectVoice(voiceId, voiceName, opts) {
    if (launched) return;
    selectedVoiceId = voiceId;
    selectedVoiceName = voiceName;
    render();
    updateLaunchState();
    if (opts && opts.closeModal) closeVoiceModal();
  }

  function selectVoiceFromControl() {
    if (!voiceSelect) return;
    const option = voiceSelect.selectedOptions && voiceSelect.selectedOptions[0];
    if (!voiceSelect.value || !option) return;
    selectVoice(voiceSelect.value, option.dataset.voiceName || option.textContent);
  }

  function updateLaunchState() {
    const ready = launched ? false : !!selectedVoiceId;
    launchBtn.disabled = !ready;
    if (launched) {
      selectionText.textContent = "✓ 已提交，pipeline 正在运行";
    } else if (selectedVoiceId) {
      const label = selectedVoiceName || selectedVoiceId;
      selectionText.textContent = `✓ 已选：${label}`;
    } else {
      selectionText.textContent = "请从列表里选择一个音色";
    }
  }

  function openVoiceModal() {
    const options = arguments[0] && Object.prototype.hasOwnProperty.call(arguments[0], "restoreFocus")
      ? arguments[0]
      : null;
    if (!modalEl) return;
    modalTriggerEl = document.activeElement;
    modalEl.hidden = false;
    document.body.classList.add("vs-modal-open");
    renderVoiceModal();
    requestAnimationFrame(() => {
      const selectedRow = selectedVoiceId
        ? modalListEl && modalListEl.querySelector(`.vs-row[data-voice-id="${cssEscapeValue(selectedVoiceId)}"]`)
        : null;
      const focusTarget = selectedRow || (modalListEl && modalListEl.querySelector(".vs-row")) || modalCloseBtn;
      if (selectedRow) selectedRow.scrollIntoView({ block: "center" });
      if (focusTarget && (!options || options.restoreFocus !== false)) {
        try {
          focusTarget.focus({ preventScroll: true });
        } catch (_err) {
          focusTarget.focus();
        }
      }
      saveReloadState();
    });
  }

  function closeVoiceModal() {
    if (!modalEl) return;
    modalEl.hidden = true;
    document.body.classList.remove("vs-modal-open");
    if (modalTriggerEl && typeof modalTriggerEl.focus === "function") {
      try {
        modalTriggerEl.focus({ preventScroll: true });
      } catch (_err) {
        modalTriggerEl.focus();
      }
    }
    saveReloadState();
  }

  async function launch() {
    if (!launchBtn || launchBtn.disabled) return;
    launchBtn.disabled = true;
    launchBtn.textContent = "提交中...";
    try {
      const body = {
        voice_id: selectedVoiceId,
        voice_name: selectedVoiceName,
        subtitle_font: subFontEl.value,
        subtitle_size: subSize,
        subtitle_position_y: parseFloat(subPosYEl.value) || 0.68,
      };
      const resp = await fetch(`${apiBase}/${taskId}/confirm-voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        alert("启动失败：" + (await resp.text()));
        launchBtn.disabled = false;
        launchBtn.textContent = "开始处理";
        return;
      }
      launched = true;
      launchBtn.textContent = "✓ 已启动";
      updateLaunchState();
      saveReloadState();
      setTimeout(() => window.location.reload(), 800);
    } catch (err) {
      console.error("[voice-selector] launch failed:", err);
      alert("网络错误");
      launchBtn.disabled = false;
      launchBtn.textContent = "开始处理";
    }
  }

  searchInput.addEventListener("input", () => {
    reloadVoiceLibrarySoon();
    saveReloadState();
  });
  recommendedOnly.addEventListener("change", () => {
    render();
    saveReloadState();
  });
  if (voiceSelect) voiceSelect.addEventListener("change", selectVoiceFromControl);
  if (openModalBtn) openModalBtn.addEventListener("click", openVoiceModal);
  if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeVoiceModal);
  if (aiRankDebugBtn) aiRankDebugBtn.addEventListener("click", () => openVoiceAiRankModal("request"));
  if (aiRankCloseBtn) aiRankCloseBtn.addEventListener("click", closeVoiceAiRankModal);
  if (modalEl) {
    modalEl.addEventListener("click", e => {
      if (e.target && e.target.hasAttribute("data-vs-modal-close")) closeVoiceModal();
    });
  }
  if (aiRankModalEl) {
    aiRankModalEl.addEventListener("click", e => {
      if (e.target && e.target.hasAttribute("data-ai-rank-modal-close")) closeVoiceAiRankModal();
      const tab = e.target && e.target.closest("[data-ai-rank-tab]");
      if (tab) setAiRankTab(tab.dataset.aiRankTab || "request");
    });
  }
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && currentModalOpen()) closeVoiceModal();
    if (e.key === "Escape" && currentAiRankModalOpen()) closeVoiceAiRankModal();
  });
  if (listEl) listEl.addEventListener("scroll", maybeLoadMoreVoices, { passive: true });
  if (listEl) listEl.addEventListener("scroll", saveReloadState, { passive: true });
  if (modalListEl) modalListEl.addEventListener("scroll", maybeLoadMoreVoices, { passive: true });
  if (modalListEl) modalListEl.addEventListener("scroll", saveReloadState, { passive: true });
  window.addEventListener("beforeunload", saveReloadState);

  // 性别胶囊：toggle + 触发后端重算 top-10（不重新 embed，走 /rematch）
  async function onGenderPillClick(btn) {
    if (rematching) return;
    const clicked = btn.dataset.gender;
    activeGender = (activeGender === clicked) ? null : clicked;

    updateGenderPills();

    loadLibrary();

    const hint = document.getElementById("vs-rematching");
    rematching = true;
    genderFilter.querySelectorAll(".vs-pill").forEach(b => { b.disabled = true; });
    if (hint) hint.style.display = "inline-flex";
    try {
      const resp = await fetch(`${apiBase}/${taskId}/rematch`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ gender: activeGender }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setVoiceMatchCandidates(data.candidates || []);
        // 把后端给的候选完整行 merge 进当前动态页集合。
        // 候选可能不在已加载的 30 个普通音色里，但仍要置顶可选。
        voiceAiRankDebug = null;
        updateVoiceAiRankDebugButton("stale_after_rematch");
        mergeVoiceItems(allItems, data.extra_items || [], loadedVoiceIds);
        render();
      } else if (resp.status !== 409) {
        console.warn("rematch failed:", await resp.text());
      }
      // 409 = voice_match 尚未完成，静默忽略
    } catch (err) {
      console.error("rematch error:", err);
    } finally {
      rematching = false;
      genderFilter.querySelectorAll(".vs-pill").forEach(b => { b.disabled = false; });
      if (hint) hint.style.display = "none";
    }
  }

  genderFilter.querySelectorAll(".vs-pill").forEach(btn => {
    btn.addEventListener("click", () => onGenderPillClick(btn));
  });
  launchBtn.addEventListener("click", launch);

  pendingReloadState = restoreReloadState();
  loadLibrary();
})();
