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
  const subSizeEl = document.getElementById("vs-sub-size");
  const subPosYEl = document.getElementById("vs-sub-position-y");

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
    const { badge, pinClass, isSelected } = opts;
    const classes = ["vs-row"];
    if (pinClass) classes.push(pinClass);
    if (isSelected) classes.push("selected");
    const meta = [v.gender, v.accent, v.age, v.description || v.descriptive || ""]
      .filter(Boolean).map(escapeHtml).join(" · ");
    const preview = v.preview_url
      ? `<audio controls preload="none" src="${escapeHtml(v.preview_url)}"></audio>`
      : "";
    return `
      <div class="${classes.join(" ")}" data-voice-id="${escapeHtml(v.voice_id)}"
           data-voice-name="${escapeHtml(v.name || '')}">
        <div class="vs-row-main">
          <div class="vs-row-name">${badge || ""}${escapeHtml(v.name || v.voice_id)}</div>
          <div class="vs-row-meta">${meta}</div>
        </div>
        ${preview}
        <button class="vs-row-select-btn" type="button">${isSelected ? "已选" : "选此音色"}</button>
      </div>
    `;
  }

  function render(waitingProgress) {
    const q = (searchInput.value || "").trim().toLowerCase();
    const gender = genderFilter.value;
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

    // 1. 默认音色置顶（永远显示，除非用户手动用了"只看推荐"过滤掉 → 其实默认音色也视为"系统推荐"，所以保留）
    if (defaultVoice && applyFilter(defaultVoice)) {
      const isSelDefault = selectedVoiceId === defaultVoice.voice_id;
      const badge = `<span class="vs-row-sim" style="background:#4b5563;">默认</span>`;
      html += rowHtml(defaultVoice, {
        badge, pinClass: "pinned-default", isSelected: isSelDefault,
      });
    }

    // 2. 向量推荐 + 全库（withSort 已按 similarity 排序）
    //    但要排除 defaultVoice，避免重复出现
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
        selectVoice(row.dataset.voiceId, row.dataset.voiceName);
      });
    });
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
        subtitle_size: parseInt(subSizeEl.value, 10) || 14,
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
  genderFilter.addEventListener("change", () => render());
  recommendedOnly.addEventListener("change", () => render());
  launchBtn.addEventListener("click", launch);

  loadLibrary();
})();
