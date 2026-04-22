(function () {
  const state = {
    tab: "browse",
    language: "", gender: "", q: "",
    use_case: [], accent: [], age: [], descriptive: [],
    page: 1, pageSize: 48,
  };
  const audio = new Audio();
  let playingVoiceId = null;

  const GENDER_LABEL = { male: "男声", female: "女声", neutral: "中性" };

  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return [...document.querySelectorAll(sel)]; }
  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[c]));
  }

  // ─────────── Tabs ───────────
  function setActiveTab(name) {
    state.tab = name;
    $all(".oc-tab").forEach(el => el.classList.toggle("active", el.dataset.tab === name));
    $all("[data-panel]").forEach(el => el.hidden = el.dataset.panel !== name);
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

  // ─────────── Pill rendering ───────────
  function makePill(label, isActive, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "vl-pill" + (isActive ? " active" : "");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function renderLangPills(langs) {
    const box = $("#vl-browse-languages");
    box.innerHTML = "";
    langs.forEach(l => {
      box.appendChild(makePill(l.name_zh, l.code === state.language, () => {
        state.language = l.code; state.page = 1;
        refreshFiltersAndList();
      }));
    });
  }

  function renderGenderPills() {
    const box = $("#vl-browse-gender");
    box.innerHTML = "";
    [["", "全部"], ["male", "男"], ["female", "女"]].forEach(([val, label]) => {
      box.appendChild(makePill(label, val === state.gender, () => {
        state.gender = val; state.page = 1;
        renderGenderPills();
        loadList();
      }));
    });
  }

  function renderMultiPills(containerId, values, stateKey) {
    const box = $("#" + containerId);
    box.innerHTML = "";
    if (!values || !values.length) {
      const span = document.createElement("span");
      span.className = "vl-field-empty";
      span.style.cssText = "font-size:11px;color:var(--oc-fg-subtle);font-style:italic";
      span.textContent = "暂无可选项";
      box.appendChild(span);
      return;
    }
    values.forEach(v => {
      const isOn = state[stateKey].includes(v);
      box.appendChild(makePill(v, isOn, () => {
        if (isOn) {
          state[stateKey] = state[stateKey].filter(x => x !== v);
        } else {
          state[stateKey] = [...state[stateKey], v];
        }
        state.page = 1;
        renderMultiPills(containerId, values, stateKey);
        loadList();
      }));
    });
  }

  // ─────────── Card rendering ───────────
  function splitName(raw) {
    const s = String(raw || "").trim();
    // "Name - Subtitle" or "Name – Subtitle" or "Name | Subtitle"
    const m = s.match(/^(.+?)\s+[-–|]\s+(.+)$/);
    if (m) return { name: m[1].trim(), subtitle: m[2].trim() };
    return { name: s, subtitle: "" };
  }

  function renderCard(v) {
    const card = document.createElement("article");
    card.className = "vl-card";

    const { name, subtitle } = splitName(v.name);
    const gender = (v.gender || "").toLowerCase();
    const genderLabel = GENDER_LABEL[gender] || (gender || "—");
    const genderCls = gender === "female" ? "vl-gender-female"
                    : gender === "male"   ? "vl-gender-male"
                    : "vl-gender-neutral";

    const tagDefs = [
      { k: "accent",      cls: "" },
      { k: "age",         cls: "" },
      { k: "descriptive", cls: "cyan" },
      { k: "use_case",    cls: "primary" },
    ];
    const tagsHtml = tagDefs
      .filter(t => v[t.k])
      .map(t => `<span class="vl-tag ${t.cls}">${escapeHtml(v[t.k])}</span>`)
      .join("");

    const subtitleHtml = subtitle
      ? `<p class="vl-desc" style="min-height:0;margin:0;-webkit-line-clamp:1;color:var(--oc-fg-subtle);font-size:11px;">${escapeHtml(subtitle)}</p>`
      : "";

    card.innerHTML = `
      <div class="vl-card-head">
        <h3 class="vl-card-name" title="${escapeHtml(v.name)}">${escapeHtml(name)}</h3>
        <span class="vl-gender ${genderCls}">${escapeHtml(genderLabel)}</span>
      </div>
      ${subtitleHtml}
      <div class="vl-tag-row">${tagsHtml}</div>
      <p class="vl-desc">${escapeHtml((v.description || "").slice(0, 120))}</p>
      <div class="vl-card-foot">
        <button class="vl-play-btn" type="button">
          <svg class="icon" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
            <polygon points="2,1 11,6 2,11"></polygon>
          </svg>
          <span class="vl-play-label">试听</span>
        </button>
      </div>
    `;

    const playBtn = card.querySelector(".vl-play-btn");
    playBtn.dataset.voice = v.voice_id || "";
    playBtn.dataset.url = v.preview_url || "";
    playBtn.addEventListener("click", (e) => togglePlay(e.currentTarget));
    return card;
  }

  function setPlayVisual(btn, playing) {
    const label = btn.querySelector(".vl-play-label");
    const icon  = btn.querySelector(".icon");
    if (playing) {
      btn.classList.add("playing");
      if (label) label.textContent = "停止";
      if (icon)  icon.innerHTML = '<rect x="2" y="2" width="8" height="8" rx="1"></rect>';
    } else {
      btn.classList.remove("playing");
      if (label) label.textContent = "试听";
      if (icon)  icon.innerHTML = '<polygon points="2,1 11,6 2,11"></polygon>';
    }
  }

  function togglePlay(btn) {
    const url = btn.dataset.url;
    const vid = btn.dataset.voice;
    if (!url) return;
    if (playingVoiceId === vid) {
      audio.pause(); audio.src = "";
      setPlayVisual(btn, false);
      playingVoiceId = null;
      return;
    }
    $all(".vl-play-btn.playing").forEach(b => setPlayVisual(b, false));
    audio.src = url;
    audio.play().catch(() => {});
    setPlayVisual(btn, true);
    playingVoiceId = vid;
  }

  audio.addEventListener("ended", () => {
    $all(".vl-play-btn.playing").forEach(b => setPlayVisual(b, false));
    playingVoiceId = null;
  });

  // ─────────── List ───────────
  async function loadList() {
    if (!state.language) return;
    const data = await fetchJSON(buildListUrl());
    const grid = $("#vl-grid"); grid.innerHTML = "";
    const empty = $("#vl-empty");
    $("#vl-count").innerHTML = `共 <strong>${data.total}</strong> 条音色`;

    if (data.total === 0) {
      empty.hidden = false;
      $("#vl-empty-title").textContent = "没有匹配的音色";
      $("#vl-empty-desc").textContent = "换一组筛选条件，或重置后再试。";
      $("#vl-page-info").textContent = "—";
      $("#vl-prev").disabled = true;
      $("#vl-next").disabled = true;
      return;
    }
    empty.hidden = true;
    data.items.forEach(v => grid.appendChild(renderCard(v)));
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    $("#vl-page-info").textContent = `第 ${data.page} / ${pages} 页`;
    $("#vl-prev").disabled = data.page <= 1;
    $("#vl-next").disabled = data.page >= pages;
  }

  async function refreshFiltersAndList() {
    if (!state.language) return;
    const opts = await fetchJSON("/voice-library/api/filters?language=" + encodeURIComponent(state.language));
    renderLangPills(opts.languages);
    renderGenderPills();
    renderMultiPills("vl-use-case",    opts.use_cases,    "use_case");
    renderMultiPills("vl-accent",      opts.accents,      "accent");
    renderMultiPills("vl-age",         opts.ages,         "age");
    renderMultiPills("vl-descriptive", opts.descriptives, "descriptive");
    await loadList();
  }

  // ─────────── Match tab ───────────
  const match = { language: "", gender: "", taskId: null, pollTimer: null };

  function renderMatchLangs(langs) {
    const box = $("#vl-match-languages");
    box.innerHTML = "";
    langs.forEach(l => {
      box.appendChild(makePill(l.name_zh, l.code === match.language, () => {
        match.language = l.code; renderMatchLangs(langs);
      }));
    });
  }

  function renderMatchGender() {
    const box = $("#vl-match-gender");
    box.innerHTML = "";
    [["male", "男"], ["female", "女"]].forEach(([v, t]) => {
      box.appendChild(makePill(t, v === match.gender, () => {
        match.gender = v; renderMatchGender();
      }));
    });
  }

  function setProgress(pct, label) {
    $("#vl-progress").hidden = false;
    $("#vl-progress-fill").style.width = pct + "%";
    $("#vl-progress-label").textContent = label;
  }

  async function uploadViaLocalPut(file) {
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
    return pre.upload_token;
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
      card.querySelector(".vl-card-foot").prepend(tag);
      grid.appendChild(card);
    });
  }

  async function startMatch(file) {
    if (!match.language || !match.gender) {
      alert("请先选择目标语种和性别"); return;
    }
    setProgress(0, "准备上传");
    try {
      const uploadToken = await uploadViaLocalPut(file);
      setProgress(92, "任务启动中");
      const r = await fetch("/voice-library/api/match/start", {
        method: "POST", headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify({upload_token: uploadToken, language: match.language, gender: match.gender}),
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
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", e => {
      e.preventDefault();
      zone.classList.remove("dragover");
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
      const empty = $("#vl-empty");
      empty.hidden = false;
      $("#vl-empty-title").textContent = "尚未配置启用的小语种";
      $("#vl-empty-desc").textContent = "请让管理员在后台启用至少一个小语种，再回到这里浏览音色。";
      $("#vl-count").textContent = "—";
      return;
    }
    state.language = opts.languages[0].code;
    await refreshFiltersAndList();
    renderMatchLangs(opts.languages);
    renderMatchGender();
    match.language = opts.languages[0].code;
  }

  function bindEvents() {
    $all(".oc-tab").forEach(btn => btn.addEventListener("click", () => {
      const name = btn.dataset.tab;
      history.replaceState(null, "", "#" + name);
      setActiveTab(name);
    }));
    window.addEventListener("hashchange", syncTabFromHash);
    $("#vl-prev").addEventListener("click", () => { state.page--; loadList(); });
    $("#vl-next").addEventListener("click", () => { state.page++; loadList(); });
    $("#vl-reset-filters").addEventListener("click", () => {
      state.gender = ""; state.q = "";
      state.use_case = []; state.accent = []; state.age = []; state.descriptive = [];
      state.page = 1;
      $("#vl-q").value = "";
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
