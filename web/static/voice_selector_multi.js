(function () {
  const root = document.getElementById("voice-selector-multi");
  if (!root) return;
  const taskId = root.dataset.taskId;
  const lang = root.dataset.lang;

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
  const genderFilter = document.getElementById("vs-gender-filter");
  const recommendedOnly = document.getElementById("vs-recommended-only");

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
    const src = sourceVideo && sourceVideo.getAttribute("src");
    if (!src) {
      setPreviewNote("当前还没有可复用的视频预览，等原视频预览加载后这里会自动同步。", "note");
      return false;
    }
    if (previewVideo.getAttribute("src") === src) {
      return true;
    }
    previewVideo.src = src;
    previewVideo.load();
    previewVideo.play().catch(() => {});
    setPreviewNote("已复用当前任务的原始视频预览，字幕会直接叠加在真实画面上。", "success");
    return true;
  }

  function attachPreviewVideo(src, message) {
    if (!previewVideo || !src) return false;
    if (previewVideo.getAttribute("src") === src) return true;
    previewPayloadVideoUrl = src;
    previewVideo.preload = "metadata";
    previewVideo.src = src;
    previewVideo.load();
    previewVideo.play().catch(() => {});
    if (message) setPreviewNote(message, "success");
    return true;
  }

  function schedulePreviewReuseFallback() {
    if (previewFallbackTimer) return;
    if (tryAttachPreviewVideo()) return;
    let previewRetries = 0;
    previewFallbackTimer = setInterval(() => {
      previewRetries += 1;
      if (tryAttachPreviewVideo() || previewRetries >= 20) {
        clearInterval(previewFallbackTimer);
        previewFallbackTimer = null;
      }
    }, 1000);
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

    const videoUrl = String(data.video_url || "").trim();
    if (videoUrl) {
      attachPreviewVideo(videoUrl, "已加载当前任务的英文原版视频，字幕样式会在这里实时预览。");
      return;
    }
    schedulePreviewReuseFallback();
  }

  async function loadSubtitlePreviewPayload() {
    setPreviewNote("正在加载英文原版视频预览...", "note");
    try {
      const resp = await fetch(`/api/multi-translate/${taskId}/subtitle-preview`, { cache: "no-store" });
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

  const csrfToken = () => {
    const el = document.querySelector("meta[name=csrf-token]");
    return el ? el.content : "";
  };

  let allItems = [];
  let candidatesMap = new Map();
  let defaultVoice = null;       // {voice_id, name, preview_url, gender, accent, description}
  let selectedVoiceId = null;
  let selectedVoiceName = null;
  let launched = false;
  let pollHandle = null;
  let activeGender = null;       // null | "male" | "female"（由胶囊按钮驱动）
  let rematching = false;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
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

  async function loadLibrary() {
    try {
      const resp = await fetch(`/api/multi-translate/${taskId}/voice-library`);
      if (!resp.ok) {
        listEl.innerHTML = `<div class="vs-loading">加载失败：${await resp.text()}</div>`;
        return;
      }
      const data = await resp.json();
      allItems = data.items || [];
      candidatesMap.clear();
      (data.candidates || []).forEach(c => candidatesMap.set(c.voice_id, c));
      defaultVoice = data.default_voice || null;
      selectedVoiceId = data.selected_voice_id || null;

      const n = (data.candidates || []).length;
      const ready = !!data.voice_match_ready;
      const progress = describePipeline(data.pipeline);

      if (!ready) {
        summaryEl.textContent = `${lang.toUpperCase()} 音色库共 ${data.total || 0} 个 · ${progress}`;
        setTimeout(() => render(progress), 0);
        schedulePoll();
      } else {
        if (pollHandle) { clearTimeout(pollHandle); pollHandle = null; }
        const parts = [`${lang.toUpperCase()} 音色库共 ${data.total || 0} 个`];
        if (n > 0) parts.push(`${n} 个向量匹配推荐`);
        else parts.push("向量匹配未找到相似音色");
        summaryEl.textContent = parts.join(" · ");
        render(null);
      }

      updateLaunchState();
    } catch (err) {
      console.error("[voice-selector] load failed:", err);
      listEl.innerHTML = `<div class="vs-loading">网络错误，5s 后重试</div>`;
      schedulePoll(5000);
    }
  }

  function schedulePoll(delay = 3000) {
    if (launched) return;
    if (pollHandle) clearTimeout(pollHandle);
    pollHandle = setTimeout(loadLibrary, delay);
  }

  function rowHtml(v, opts) {
    const { badge, pinClass, isSelected, isCurrentDefault } = opts;
    const classes = ["vs-row"];
    if (pinClass) classes.push(pinClass);
    if (isSelected) classes.push("selected");
    const meta = [v.gender, v.accent, v.age, v.description || v.descriptive || ""]
      .filter(Boolean).map(escapeHtml).join(" · ");
    const preview = v.preview_url
      ? `<audio controls preload="none" src="${escapeHtml(v.preview_url)}"></audio>`
      : "";
    const setDefaultBtn = isCurrentDefault
      ? `<button class="vs-row-default-btn is-current" type="button" disabled>默认</button>`
      : `<button class="vs-row-default-btn" type="button" title="把此音色设为 ${lang.toUpperCase()} 的默认">设为默认</button>`;
    return `
      <div class="${classes.join(" ")}" data-voice-id="${escapeHtml(v.voice_id)}"
           data-voice-name="${escapeHtml(v.name || '')}">
        <div class="vs-row-main">
          <div class="vs-row-name">${badge || ""}${escapeHtml(v.name || v.voice_id)}</div>
          <div class="vs-row-meta">${meta}</div>
        </div>
        ${preview}
        ${setDefaultBtn}
        <button class="vs-row-select-btn" type="button">${isSelected ? "已选" : "选此音色"}</button>
      </div>
    `;
  }

  function render(waitingProgress) {
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

    const withSort = allItems.map(v => {
      const rec = candidatesMap.get(v.voice_id);
      return { v, rec, sim: rec ? rec.similarity : -1 };
    }).sort((a, b) => {
      if (a.sim !== b.sim) return b.sim - a.sim;
      return (a.v.name || "").localeCompare(b.v.name || "");
    });

    const filtered = withSort.filter(({ v, rec }) => {
      if (onlyRec && !rec) return false;
      return applyFilter(v);
    });

    let html = "";
    const currentDefaultId = defaultVoice ? defaultVoice.voice_id : null;
    const showPinnedDefault = !!defaultVoice
      && applyFilter(defaultVoice)
      && (!onlyRec || candidatesMap.has(defaultVoice.voice_id));

    // 1. 默认音色置顶
    if (showPinnedDefault) {
      const isSelDefault = selectedVoiceId === defaultVoice.voice_id;
      const badge = `<span class="vs-row-sim" style="background:#4b5563;">默认</span>`;
      html += rowHtml(defaultVoice, {
        badge, pinClass: "pinned-default", isSelected: isSelDefault,
        isCurrentDefault: true,
      });
    }

    // 2. 向量推荐 + 全库
    const rest = filtered.filter(({ v }) =>
      !defaultVoice || v.voice_id !== defaultVoice.voice_id);
    rest.forEach(({ v, rec }) => {
      const isRec = !!rec;
      const isSelected = selectedVoiceId === v.voice_id;
      const classes = [];
      if (isRec) classes.push("recommended");
      const badge = isRec
        ? `<span class="vs-row-sim">${(rec.similarity * 100).toFixed(1)}% 相似</span>`
        : "";
      html += rowHtml(v, {
        badge, pinClass: classes.join(" "), isSelected,
        isCurrentDefault: v.voice_id === currentDefaultId,
      });
    });

    if (!html) {
      html = `<div class="vs-loading">${waitingProgress || "没有匹配的音色"}</div>`;
    } else if (waitingProgress) {
      // 等待期提示条
      html = `<div class="vs-waiting-banner">⏳ ${waitingProgress}
        <small style="color:var(--text-user-badge);">（可先浏览/试听；向量推荐将在 ASR 完成后自动出现）</small></div>` + html;
    }

    listEl.innerHTML = html;

    listEl.querySelectorAll(".vs-row").forEach(row => {
      row.addEventListener("click", e => {
        if (e.target.tagName === "AUDIO" || e.target.closest("audio")) return;
        // "设为默认" 按钮的点击不触发选中
        if (e.target.classList.contains("vs-row-default-btn")) return;
        selectVoice(row.dataset.voiceId, row.dataset.voiceName);
      });
      const defBtn = row.querySelector(".vs-row-default-btn");
      if (defBtn && !defBtn.disabled) {
        defBtn.addEventListener("click", async e => {
          e.stopPropagation();
          await setAsDefault(row.dataset.voiceId, row.dataset.voiceName);
        });
      }
    });
  }

  async function setAsDefault(voiceId, voiceName) {
    try {
      const resp = await fetch(`/api/multi-translate/user-default-voice`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ lang, voice_id: voiceId, voice_name: voiceName }),
      });
      if (!resp.ok) {
        alert("设置默认失败：" + (await resp.text()));
        return;
      }
      // 重新拉库 → 新的默认音色置顶 + 其他行的"设为默认"重新出现
      loadLibrary();
    } catch (err) {
      console.error("[voice-selector] setAsDefault failed:", err);
      alert("网络错误");
    }
  }

  function selectVoice(voiceId, voiceName) {
    if (launched) return;
    selectedVoiceId = voiceId;
    selectedVoiceName = voiceName;
    render();
    updateLaunchState();
  }

  function updateLaunchState() {
    const ready = launched ? false : !!selectedVoiceId;
    launchBtn.disabled = !ready;
    if (launched) {
      selectionText.textContent = "✓ 已提交，pipeline 正在运行";
    } else if (selectedVoiceId) {
      const isDefault = defaultVoice && selectedVoiceId === defaultVoice.voice_id;
      const label = selectedVoiceName || selectedVoiceId;
      selectionText.textContent = isDefault
        ? `✓ 已选默认音色：${label}`
        : `✓ 已选：${label}`;
    } else {
      selectionText.textContent = "请从列表里选一个音色（可选默认，也可从推荐里选）";
    }
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
      const resp = await fetch(`/api/multi-translate/${taskId}/confirm-voice`, {
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
      setTimeout(() => window.location.reload(), 800);
    } catch (err) {
      console.error("[voice-selector] launch failed:", err);
      alert("网络错误");
      launchBtn.disabled = false;
      launchBtn.textContent = "开始处理";
    }
  }

  searchInput.addEventListener("input", () => render());
  recommendedOnly.addEventListener("change", () => render());

  // 性别胶囊：toggle + 触发后端重算 top-10（不重新 embed，走 /rematch）
  async function onGenderPillClick(btn) {
    if (rematching) return;
    const clicked = btn.dataset.gender;
    activeGender = (activeGender === clicked) ? null : clicked;

    genderFilter.querySelectorAll(".vs-pill").forEach(b => {
      const on = b.dataset.gender === activeGender;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });

    render();  // 先本地渲染一次（按 activeGender 过滤）

    const hint = document.getElementById("vs-rematching");
    rematching = true;
    genderFilter.querySelectorAll(".vs-pill").forEach(b => { b.disabled = true; });
    if (hint) hint.style.display = "inline-flex";
    try {
      const resp = await fetch(`/api/multi-translate/${taskId}/rematch`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ gender: activeGender }),
      });
      if (resp.ok) {
        const data = await resp.json();
        candidatesMap.clear();
        (data.candidates || []).forEach(c => candidatesMap.set(c.voice_id, c));
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

  loadLibrary();
})();
