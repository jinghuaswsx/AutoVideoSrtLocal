(function () {
  const state = {
    tab: "browse",
    language: "", gender: "", q: "",
    use_case: [], accent: [], age: [], descriptive: [],
    page: 1, pageSize: 48,
  };
  const audio = new Audio();
  let playingVoiceId = null;

  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return [...document.querySelectorAll(sel)]; }

  function setActiveTab(name) {
    state.tab = name;
    $all(".oc-tab").forEach(el => el.classList.toggle("is-active", el.getAttribute("href") === "#" + name));
    $("#tab-browse").hidden = name !== "browse";
    $("#tab-match").hidden = name !== "match";
  }

  function syncTabFromHash() {
    const want = (location.hash || "#browse").slice(1);
    setActiveTab(want === "match" ? "match" : "browse");
  }

  async function fetchJSON(url) {
    const resp = await fetch(url, {credentials: "same-origin"});
    if (!resp.ok) throw new Error(await resp.text());
    return resp.json();
  }

  function buildListUrl() {
    const q = new URLSearchParams();
    q.set("language", state.language);
    if (state.gender) q.set("gender", state.gender);
    if (state.q) q.set("q", state.q);
    for (const k of ["use_case", "accent", "age", "descriptive"]) {
      if (state[k].length) q.set(k, state[k].join(","));
    }
    q.set("page", String(state.page));
    q.set("page_size", String(state.pageSize));
    return "/voice-library/api/list?" + q.toString();
  }

  function renderLangPills(langs) {
    const box = $("#vl-browse-languages");
    box.innerHTML = "";
    langs.forEach(l => {
      const b = document.createElement("button");
      b.className = "vl-pill";
      b.textContent = l.name_zh;
      b.dataset.code = l.code;
      if (l.code === state.language) b.classList.add("is-active");
      b.addEventListener("click", () => {
        state.language = l.code;
        state.page = 1;
        refreshFiltersAndList();
      });
      box.appendChild(b);
    });
  }

  function renderGenderPills() {
    const box = $("#vl-browse-gender");
    box.innerHTML = "";
    [["", "全部"], ["male", "男"], ["female", "女"]].forEach(([val, label]) => {
      const b = document.createElement("button");
      b.className = "vl-pill";
      b.textContent = label;
      if (val === state.gender) b.classList.add("is-active");
      b.addEventListener("click", () => {
        state.gender = val; state.page = 1; loadList();
        renderGenderPills();
      });
      box.appendChild(b);
    });
  }

  function renderMultiSelect(id, values, stateKey) {
    const sel = $("#" + id);
    sel.innerHTML = "";
    values.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v; opt.textContent = v;
      if (state[stateKey].includes(v)) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = () => {
      state[stateKey] = [...sel.selectedOptions].map(o => o.value);
      state.page = 1; loadList();
    };
  }

  function renderCard(v) {
    const card = document.createElement("article");
    card.className = "vl-card";
    const chips = [];
    if (v.accent) chips.push(["accent", v.accent]);
    if (v.age) chips.push(["age", v.age]);
    if (v.descriptive) chips.push(["descriptive", v.descriptive]);
    if (v.use_case) chips.push(["use_case", v.use_case]);
    const chipsHtml = chips.map(([k, val]) =>
      `<span class="vl-chip">${escapeHtml(val)}</span>`).join("");
    card.innerHTML = `
      <div class="vl-card-title">
        <span>${escapeHtml(v.name)}</span>
        <span class="vl-chip ${v.gender === "female" ? "is-female" : ""}">${v.gender || ""}</span>
      </div>
      <div class="vl-chip-row">${chipsHtml}</div>
      <div class="vl-desc">${escapeHtml((v.description || "").slice(0, 80))}</div>
      <button class="vl-play-btn" data-voice="${v.voice_id}" data-url="${v.preview_url || ""}">▶ 试听</button>
    `;
    card.querySelector(".vl-play-btn").addEventListener("click", (e) => togglePlay(e.currentTarget));
    return card;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  function togglePlay(btn) {
    const url = btn.dataset.url;
    const vid = btn.dataset.voice;
    if (!url) return;
    if (playingVoiceId === vid) {
      audio.pause(); audio.src = "";
      btn.classList.remove("is-playing");
      btn.textContent = "▶ 试听";
      playingVoiceId = null;
      return;
    }
    $all(".vl-play-btn.is-playing").forEach(b => {
      b.classList.remove("is-playing");
      b.textContent = "▶ 试听";
    });
    audio.src = url; audio.play();
    btn.classList.add("is-playing"); btn.textContent = "■ 停止";
    playingVoiceId = vid;
  }

  async function loadList() {
    const data = await fetchJSON(buildListUrl());
    const grid = $("#vl-grid"); grid.innerHTML = "";
    $("#vl-empty").hidden = data.total > 0;
    if (data.total === 0) $("#vl-empty").textContent = "没有匹配当前筛选的音色";
    data.items.forEach(v => grid.appendChild(renderCard(v)));
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    $("#vl-page-info").textContent = `第 ${data.page} / ${pages} 页（共 ${data.total}）`;
    $("#vl-prev").disabled = data.page <= 1;
    $("#vl-next").disabled = data.page >= pages;
  }

  async function refreshFiltersAndList() {
    if (!state.language) return;
    const opts = await fetchJSON("/voice-library/api/filters?language=" + encodeURIComponent(state.language));
    renderLangPills(opts.languages);
    renderGenderPills();
    renderMultiSelect("vl-use-case", opts.use_cases, "use_case");
    renderMultiSelect("vl-accent", opts.accents, "accent");
    renderMultiSelect("vl-age", opts.ages, "age");
    renderMultiSelect("vl-descriptive", opts.descriptives, "descriptive");
    await loadList();
  }

  const match = { language: "", gender: "", taskId: null, pollTimer: null };

  function renderMatchLangs(langs) {
    const box = $("#vl-match-languages");
    box.innerHTML = "";
    langs.forEach(l => {
      const b = document.createElement("button");
      b.className = "vl-pill"; b.textContent = l.name_zh;
      if (l.code === match.language) b.classList.add("is-active");
      b.addEventListener("click", () => {
        match.language = l.code; renderMatchLangs(langs);
      });
      box.appendChild(b);
    });
  }

  function renderMatchGender() {
    const box = $("#vl-match-gender"); box.innerHTML = "";
    [["male", "男"], ["female", "女"]].forEach(([v, t]) => {
      const b = document.createElement("button");
      b.className = "vl-pill"; b.textContent = t;
      if (v === match.gender) b.classList.add("is-active");
      b.addEventListener("click", () => { match.gender = v; renderMatchGender(); });
      box.appendChild(b);
    });
  }

  function setProgress(pct, label) {
    $("#vl-progress").hidden = false;
    $("#vl-progress-fill").style.width = pct + "%";
    $("#vl-progress-label").textContent = label;
  }

  async function uploadViaSignedPut(file) {
    const pre = await fetch("/voice-library/api/match/upload-url", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({filename: file.name, content_type: file.type || "video/mp4"}),
    }).then(r => r.json());
    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", pre.upload_url);
      xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) setProgress((e.loaded / e.total) * 90, `上传中 ${Math.round(e.loaded/e.total*100)}%`);
      };
      xhr.onload = () => xhr.status < 300 ? resolve() : reject(new Error("upload " + xhr.status));
      xhr.onerror = () => reject(new Error("upload network error"));
      xhr.send(file);
    });
    return pre.object_key;
  }

  const PHASE_LABEL = {
    pending: "等待中", sampling: "采样音频",
    embedding: "计算声纹", matching: "匹配声音库",
    done: "完成", failed: "失败",
  };

  async function pollMatchStatus() {
    try {
      const resp = await fetch(`/voice-library/api/match/status/${match.taskId}`,
        {credentials: "same-origin"});
      if (!resp.ok) throw new Error("status " + resp.status);
      const t = await resp.json();
      setProgress(t.progress || 90, PHASE_LABEL[t.status] || t.status);
      if (t.status === "done") { clearInterval(match.pollTimer); match.pollTimer = null; renderMatchResult(t.result); }
      else if (t.status === "failed") { clearInterval(match.pollTimer); match.pollTimer = null; setProgress(100, "失败：" + (t.error || "未知错误")); }
    } catch (e) {
      clearInterval(match.pollTimer); match.pollTimer = null;
      setProgress(100, "网络错误：" + e.message);
    }
  }

  function renderMatchResult(result) {
    $("#vl-match-step3").hidden = false;
    $("#vl-sample-audio").src = result.sample_audio_url;
    const grid = $("#vl-result-grid"); grid.innerHTML = "";
    (result.candidates || []).forEach(v => {
      const card = renderCard(v);
      const tag = document.createElement("span");
      tag.className = "vl-similarity";
      tag.textContent = `相似度 ${(v.similarity * 100).toFixed(1)}%`;
      card.querySelector(".vl-card-title").appendChild(tag);
      grid.appendChild(card);
    });
  }

  async function startMatch(file) {
    if (!match.language || !match.gender) {
      alert("请先选择目标语种和性别"); return;
    }
    setProgress(0, "准备上传");
    try {
      const objectKey = await uploadViaSignedPut(file);
      setProgress(92, "任务启动中");
      const r = await fetch("/voice-library/api/match/start", {
        method: "POST", headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify({object_key: objectKey, language: match.language, gender: match.gender}),
      });
      if (!r.ok) throw new Error("start " + r.status);
      match.taskId = (await r.json()).task_id;
      match.pollTimer = setInterval(pollMatchStatus, 1500);
      setProgress(95, "采样中");
    } catch (e) {
      setProgress(100, "失败：" + e.message);
    }
  }

  function bindMatchEvents() {
    $("#vl-upload-pick").addEventListener("click", () => $("#vl-upload-input").click());
    $("#vl-upload-input").addEventListener("change", (e) => {
      if (e.target.files.length) startMatch(e.target.files[0]);
    });
    const zone = $("#vl-upload-zone");
    zone.addEventListener("dragover", e => { e.preventDefault(); });
    zone.addEventListener("drop", e => {
      e.preventDefault();
      if (e.dataTransfer.files.length) startMatch(e.dataTransfer.files[0]);
    });
    $("#vl-match-reset").addEventListener("click", () => {
      match.taskId = null;
      if (match.pollTimer) { clearInterval(match.pollTimer); match.pollTimer = null; }
      $("#vl-match-step3").hidden = true;
      $("#vl-progress").hidden = true;
      $("#vl-upload-input").value = "";
    });
  }

  async function bootstrap() {
    const opts = await fetchJSON("/voice-library/api/filters");
    if (!opts.languages.length) {
      $("#vl-empty").hidden = false;
      $("#vl-empty").textContent = "系统尚未配置任何启用的小语种";
      return;
    }
    state.language = opts.languages[0].code;
    await refreshFiltersAndList();
    renderMatchLangs(opts.languages);
    renderMatchGender();
    if (opts.languages.length) match.language = opts.languages[0].code;
  }

  function bindEvents() {
    $all(".oc-tab").forEach(a => a.addEventListener("click", () => {
      history.replaceState(null, "", a.getAttribute("href"));
      syncTabFromHash();
    }));
    window.addEventListener("hashchange", syncTabFromHash);
    $("#vl-prev").addEventListener("click", () => { state.page--; loadList(); });
    $("#vl-next").addEventListener("click", () => { state.page++; loadList(); });
    $("#vl-reset-filters").addEventListener("click", () => {
      state.gender = ""; state.q = "";
      state.use_case = []; state.accent = []; state.age = []; state.descriptive = [];
      state.page = 1;
      refreshFiltersAndList();
    });
    const qInput = $("#vl-q");
    let qTimer = null;
    qInput.addEventListener("input", () => {
      clearTimeout(qTimer);
      qTimer = setTimeout(() => { state.q = qInput.value.trim(); state.page = 1; loadList(); }, 300);
    });
    bindMatchEvents();
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncTabFromHash();
    bindEvents();
    bootstrap();
  });
})();
