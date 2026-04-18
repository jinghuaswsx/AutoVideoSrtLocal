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
  const defaultBtn = document.getElementById("vs-use-default");
  const searchInput = document.getElementById("vs-search");
  const genderFilter = document.getElementById("vs-gender-filter");
  const recommendedOnly = document.getElementById("vs-recommended-only");

  // 字幕参数输入
  const subFontEl = document.getElementById("vs-sub-font");
  const subSizeEl = document.getElementById("vs-sub-size");
  const subPosEl = document.getElementById("vs-sub-position");
  const subPosYEl = document.getElementById("vs-sub-position-y");

  const csrfToken = () => {
    const el = document.querySelector("meta[name=csrf-token]");
    return el ? el.content : "";
  };

  let allItems = [];
  let candidatesMap = new Map();
  let selectedVoiceId = null;
  let selectedVoiceName = null;
  let useDefault = false;
  let launched = false;
  let pollHandle = null;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function describePipeline(pipeline) {
    // 返回当前 pipeline 阶段的中文状态，用于 summary 文案
    const { extract, asr, voice_match } = pipeline || {};
    if (voice_match === "waiting" || voice_match === "done") return null;  // ready
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
      selectedVoiceId = data.selected_voice_id || null;

      const n = (data.candidates || []).length;
      const ready = !!data.voice_match_ready;
      const progress = describePipeline(data.pipeline);

      if (!ready) {
        // 管道还没跑到 voice_match，显示进度并继续轮询
        summaryEl.textContent = `${lang.toUpperCase()} 音色库共 ${data.total || 0} 个 · ${progress}`;
        listEl.innerHTML = `<div class="vs-loading">${progress || "等待中..."}<br>
          <small style="color:var(--text-user-badge);">音色库已可浏览，但向量推荐需等 ASR 完成</small></div>`;
        // 先把列表渲染出来（无推荐），用户可以边等边浏览/试听
        setTimeout(() => render(), 0);
        schedulePoll();
      } else {
        // Ready — 停止轮询
        if (pollHandle) { clearTimeout(pollHandle); pollHandle = null; }
        const parts = [`${lang.toUpperCase()} 音色库共 ${data.total || 0} 个`];
        if (n > 0) parts.push(`${n} 个向量匹配推荐置顶`);
        else parts.push("向量匹配未找到相似音色（可手动挑选或用默认）");
        summaryEl.textContent = parts.join(" · ");
        render();
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

  function render() {
    const q = (searchInput.value || "").trim().toLowerCase();
    const gender = genderFilter.value;
    const onlyRec = recommendedOnly.checked;

    const withSort = allItems.map(v => {
      const rec = candidatesMap.get(v.voice_id);
      return { v, rec, sim: rec ? rec.similarity : -1 };
    }).sort((a, b) => {
      if (a.sim !== b.sim) return b.sim - a.sim;
      return (a.v.name || "").localeCompare(b.v.name || "");
    });

    const filtered = withSort.filter(({ v, rec }) => {
      if (onlyRec && !rec) return false;
      if (gender && v.gender !== gender) return false;
      if (q) {
        const hay = [v.name, v.description, v.descriptive, v.accent, v.age]
          .filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = `<div class="vs-loading">没有匹配的音色</div>`;
      return;
    }

    listEl.innerHTML = filtered.map(({ v, rec }) => {
      const isRec = !!rec;
      const isSelected = !useDefault && selectedVoiceId === v.voice_id;
      const classes = ["vs-row"];
      if (isRec) classes.push("recommended");
      if (isSelected) classes.push("selected");
      const simBadge = isRec
        ? `<span class="vs-row-sim">${(rec.similarity * 100).toFixed(1)}% 相似</span>`
        : "";
      const meta = [v.gender, v.accent, v.age, v.description || v.descriptive || ""]
        .filter(Boolean).map(escapeHtml).join(" · ");
      const preview = v.preview_url
        ? `<audio controls preload="none" src="${escapeHtml(v.preview_url)}"></audio>`
        : "";
      return `
        <div class="${classes.join(" ")}" data-voice-id="${escapeHtml(v.voice_id)}"
             data-voice-name="${escapeHtml(v.name || '')}">
          <div class="vs-row-main">
            <div class="vs-row-name">${simBadge}${escapeHtml(v.name || v.voice_id)}</div>
            <div class="vs-row-meta">${meta}</div>
          </div>
          ${preview}
          <button class="vs-row-select-btn" type="button">${isSelected ? "已选" : "选此音色"}</button>
        </div>
      `;
    }).join("");

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
    useDefault = false;
    render();
    updateLaunchState();
  }

  function chooseDefault() {
    if (launched) return;
    useDefault = true;
    selectedVoiceId = null;
    selectedVoiceName = null;
    render();
    updateLaunchState();
  }

  function updateLaunchState() {
    const ready = launched ? false : (useDefault || !!selectedVoiceId);
    launchBtn.disabled = !ready;
    if (launched) {
      selectionText.textContent = "✓ 已提交，pipeline 正在运行";
    } else if (useDefault) {
      selectionText.textContent = `✓ 将使用 ${lang.toUpperCase()} 默认音色`;
    } else if (selectedVoiceId) {
      selectionText.textContent = `✓ 已选：${selectedVoiceName || selectedVoiceId}`;
    } else {
      selectionText.textContent = "请从列表里选音色，或点「使用默认音色」";
    }
  }

  async function launch() {
    if (!launchBtn || launchBtn.disabled) return;
    launchBtn.disabled = true;
    launchBtn.textContent = "提交中...";
    try {
      const body = {
        voice_id: useDefault ? "default" : selectedVoiceId,
        voice_name: useDefault ? null : selectedVoiceName,
        subtitle_font: subFontEl.value,
        subtitle_size: parseInt(subSizeEl.value, 10) || 14,
        subtitle_position: subPosEl.value,
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

  searchInput.addEventListener("input", render);
  genderFilter.addEventListener("change", render);
  recommendedOnly.addEventListener("change", render);
  defaultBtn.addEventListener("click", chooseDefault);
  launchBtn.addEventListener("click", launch);

  loadLibrary();
})();
