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

  async function bootstrap() {
    const opts = await fetchJSON("/voice-library/api/filters");
    if (!opts.languages.length) {
      $("#vl-empty").hidden = false;
      $("#vl-empty").textContent = "系统尚未配置任何启用的小语种";
      return;
    }
    state.language = opts.languages[0].code;
    await refreshFiltersAndList();
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
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncTabFromHash();
    bindEvents();
    bootstrap();
  });
})();
