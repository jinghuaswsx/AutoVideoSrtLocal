(function () {
  const root = document.getElementById("voice-selector-multi");
  if (!root) return;
  const taskId = root.dataset.taskId;
  const lang = root.dataset.lang;

  const csrfToken = () => {
    const el = document.querySelector("meta[name=csrf-token]");
    return el ? el.content : "";
  };

  async function loadInitial() {
    try {
      const resp = await fetch(`/api/multi-translate/${taskId}`);
      if (!resp.ok) return;
      const task = await resp.json();
      const state = task.state || task;
      const candidates = state.voice_match_candidates || [];
      const fallbackId = state.voice_match_fallback_voice_id;

      const sampleUrl = state.voice_match_sample_url;
      if (sampleUrl) {
        const audio = document.getElementById("vs-sample-audio");
        if (audio) audio.src = sampleUrl;
      }

      if (candidates.length && candidates[0].similarity < 0.4) {
        document.getElementById("vs-warning").style.display = "block";
      }

      renderCandidates(candidates, fallbackId);
    } catch (err) {
      console.error("[voice-selector] loadInitial failed:", err);
    }
  }

  function renderCandidates(candidates, fallbackId) {
    const box = document.getElementById("vs-candidates");
    box.innerHTML = "";
    if (!candidates.length) {
      box.innerHTML = `<p style="grid-column:span 3;color:var(--text-user-badge);">
        向量库暂无该语言的可匹配音色，系统将使用兜底音色 ${fallbackId || "（未配置）"}</p>`;
      return;
    }
    candidates.forEach((c, i) => {
      const card = document.createElement("div");
      card.className = "vs-card";
      card.dataset.voiceId = c.voice_id;
      const sim = (c.similarity * 100).toFixed(1);
      const gender = c.gender || "";
      const accent = c.accent || "";
      card.innerHTML = `
        <div class="vs-sim">${sim}% 相似</div>
        <div style="font-weight:600;margin:6px 0;">${escapeHtml(c.name || c.voice_id)}</div>
        <div style="font-size:12px;color:var(--text-user-badge);">${gender}${gender && accent ? " · " : ""}${accent}</div>
        ${c.preview_url ? `<audio controls style="width:100%;margin-top:8px;" src="${c.preview_url}"></audio>` : ""}
        <button class="vs-select-btn">${i === 0 ? "使用此音色（推荐）" : "使用此音色"}</button>
      `;
      card.querySelector(".vs-select-btn").addEventListener("click", () =>
        selectVoice(c.voice_id));
      box.appendChild(card);
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  async function selectVoice(voiceId) {
    try {
      const resp = await fetch(`/api/multi-translate/${taskId}/voice`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken(),
        },
        body: JSON.stringify({ voice_id: voiceId }),
      });
      if (!resp.ok) {
        alert("保存音色失败：" + (await resp.text()));
        return;
      }
      document.querySelectorAll(".vs-card").forEach(el =>
        el.classList.toggle("selected", el.dataset.voiceId === voiceId));
    } catch (err) {
      console.error("[voice-selector] selectVoice failed:", err);
      alert("网络错误，保存音色失败");
    }
  }

  document.getElementById("vs-more").addEventListener("click", async () => {
    const box = document.getElementById("vs-full-library");
    if (box.style.display !== "none") {
      box.style.display = "none";
      return;
    }
    box.style.display = "block";
    try {
      const resp = await fetch(`/voice-library/api/list?language=${encodeURIComponent(lang)}&page_size=200`);
      const data = await resp.json();
      const items = data.items || [];
      box.innerHTML = items.map(v => `
        <div class="vs-card" data-voice-id="${v.voice_id}" style="display:inline-block;margin:4px;min-width:200px;vertical-align:top;">
          <div style="font-weight:600;">${escapeHtml(v.name || v.voice_id)}</div>
          <div style="font-size:12px;color:var(--text-user-badge);">${v.gender || ""}${v.gender && v.accent ? " · " : ""}${v.accent || ""}</div>
          ${v.preview_url ? `<audio controls style="width:100%;margin-top:6px;" src="${v.preview_url}"></audio>` : ""}
          <button class="vs-select-btn">选此音色</button>
        </div>`).join("");
      box.querySelectorAll(".vs-select-btn").forEach(btn => {
        btn.addEventListener("click", e => {
          const card = e.target.closest(".vs-card");
          if (card) selectVoice(card.dataset.voiceId);
        });
      });
    } catch (err) {
      console.error("[voice-selector] full library load failed:", err);
      box.innerHTML = `<p style="color:var(--text-user-badge);">加载失败</p>`;
    }
  });

  loadInitial();
})();
