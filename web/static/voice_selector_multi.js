(function () {
  const root = document.getElementById("voice-selector-multi");
  if (!root) return;
  const taskId = root.dataset.taskId;
  const lang = root.dataset.lang;

  const summaryEl = document.getElementById("vs-summary");
  const listEl = document.getElementById("vs-list");
  const footerEl = document.getElementById("vs-footer");
  const searchInput = document.getElementById("vs-search");
  const genderFilter = document.getElementById("vs-gender-filter");
  const recommendedOnly = document.getElementById("vs-recommended-only");

  const csrfToken = () => {
    const el = document.querySelector("meta[name=csrf-token]");
    return el ? el.content : "";
  };

  let allItems = [];
  let candidatesMap = new Map();      // voice_id -> {similarity, ...}
  let fallbackVoiceId = null;
  let selectedVoiceId = null;
  let selectedVoiceName = null;
  let confirmed = false;              // 是否已经点过"确认并开始翻译"

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
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
      fallbackVoiceId = data.fallback_voice_id || null;
      selectedVoiceId = data.selected_voice_id || null;

      updateSummary(data.total || 0, (data.candidates || []).length);
      render();
    } catch (err) {
      console.error("[voice-selector] load failed:", err);
      listEl.innerHTML = `<div class="vs-loading">网络错误</div>`;
    }
  }

  function updateSummary(total, nCandidates) {
    const parts = [`${lang.toUpperCase()} 音色库共 ${total} 个`];
    if (nCandidates > 0) parts.push(`${nCandidates} 个向量匹配推荐置顶`);
    else parts.push("暂无向量匹配推荐（可能该语种音色尚未生成 embedding）");
    summaryEl.textContent = parts.join(" · ");
  }

  function render() {
    const q = (searchInput.value || "").trim().toLowerCase();
    const gender = genderFilter.value;
    const onlyRec = recommendedOnly.checked;

    // 排序：推荐项在前（按相似度降序），其余按 name
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
      footerEl.textContent = "";
      return;
    }

    listEl.innerHTML = filtered.map(({ v, rec }) => {
      const isRec = !!rec;
      const isSelected = selectedVoiceId === v.voice_id;
      const classes = ["vs-row"];
      if (isRec) classes.push("recommended");
      if (isSelected) classes.push("selected");
      const simBadge = isRec
        ? `<span class="vs-row-sim">${(rec.similarity * 100).toFixed(1)}% 相似</span>`
        : "";
      const meta = [
        v.gender,
        v.accent,
        v.age,
        v.description || v.descriptive || "",
      ].filter(Boolean).map(escapeHtml).join(" · ");
      const preview = v.preview_url
        ? `<audio controls preload="none" src="${escapeHtml(v.preview_url)}"></audio>`
        : "";
      return `
        <div class="vs-row ${classes.join(" ")}" data-voice-id="${escapeHtml(v.voice_id)}"
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

    // 行级点击选中（避免 audio 控件点击冒泡）
    listEl.querySelectorAll(".vs-row").forEach(row => {
      row.addEventListener("click", e => {
        if (e.target.tagName === "AUDIO") return;
        if (e.target.closest("audio")) return;
        selectVoice(row.dataset.voiceId, row.dataset.voiceName);
      });
    });

    footerEl.innerHTML = selectedVoiceId
      ? `已选：${escapeHtml(selectedVoiceName || selectedVoiceId)}
         <button id="vs-confirm-btn" style="margin-left:12px;padding:6px 16px;
           background:oklch(56% 0.16 230);color:#fff;border:none;
           border-radius:6px;cursor:pointer;font-weight:600;">
           ${confirmed ? "✓ 已确认" : "确认并开始翻译"}
         </button>`
      : `请从列表里点选一个音色`;

    const confirmBtn = document.getElementById("vs-confirm-btn");
    if (confirmBtn && !confirmed) {
      confirmBtn.addEventListener("click", confirmSelection);
    }
  }

  function selectVoice(voiceId, voiceName) {
    if (confirmed) return;
    selectedVoiceId = voiceId;
    selectedVoiceName = voiceName;
    render();
  }

  async function confirmSelection() {
    if (!selectedVoiceId || confirmed) return;
    const btn = document.getElementById("vs-confirm-btn");
    if (btn) { btn.disabled = true; btn.textContent = "提交中..."; }
    try {
      const resp = await fetch(`/api/multi-translate/${taskId}/confirm-voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({
          voice_id: selectedVoiceId,
          voice_name: selectedVoiceName || undefined,
        }),
      });
      if (!resp.ok) {
        alert("确认失败：" + (await resp.text()));
        if (btn) { btn.disabled = false; btn.textContent = "确认并开始翻译"; }
        return;
      }
      confirmed = true;
      render();
      // 刷新页面让工作台进入下一步
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      console.error("[voice-selector] confirm failed:", err);
      alert("网络错误");
      if (btn) { btn.disabled = false; btn.textContent = "确认并开始翻译"; }
    }
  }

  // 事件绑定
  searchInput.addEventListener("input", render);
  genderFilter.addEventListener("change", render);
  recommendedOnly.addEventListener("change", render);

  loadLibrary();
})();
