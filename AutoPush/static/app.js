/*
 * AutoPush 前端（原生 JS，单文件模块）。
 *
 * 页面结构由 index.html 定义。本文件负责：
 *   - tab 切换
 *   - 三个视图的懒渲染（列表 / 创建 / 载荷）
 *   - 调用本地同源 FastAPI 代理（/api/...）
 */

/* ---------- DOM 工具 ---------- */

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "style") node.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) node.setAttribute(k, "");
    else if (v === false || v === null || v === undefined) continue;
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/* ---------- API 调用（同源代理，走 FastAPI） ---------- */

async function apiJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    const e = new Error(`网络请求失败：${err.message}`);
    e.cause = err;
    throw e;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail ?? body;
    const msg = typeof detail === "string" ? detail : JSON.stringify(detail);
    const e = new Error(msg || `HTTP ${response.status}`);
    e.status = response.status;
    e.payload = body;
    throw e;
  }
  return body;
}

const api = {
  config: () => apiJson("/api/config"),
  list: ({ page, pageSize, q, archived }) => {
    const p = new URLSearchParams();
    p.set("page", String(page));
    p.set("page_size", String(pageSize));
    if (q) p.set("q", q);
    if (archived) p.set("archived", archived);
    return apiJson(`/api/materials?${p.toString()}`);
  },
  fetchPushPayload: (code, lang) =>
    apiJson(`/api/materials/${encodeURIComponent(code)}/push-payload?lang=${encodeURIComponent(lang)}`),
  fetchMaterials: (code) =>
    apiJson(`/api/materials/${encodeURIComponent(code)}`),
  push: (payload) =>
    apiJson("/api/push/medias", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
};

/* ---------- 载荷校验（同 push-module 源版） ---------- */

function validatePayload(p) {
  const errs = [];
  if (!p || typeof p !== "object" || Array.isArray(p)) return ["payload 为空或不是对象"];
  const isStr = (v) => typeof v === "string";
  const isNum = (v) => typeof v === "number" && !Number.isNaN(v);
  const isArr = Array.isArray;
  const check = (k, ok, t) => { if (!ok(p[k])) errs.push(`字段 ${k} 必须是 ${t}`); };
  check("mode", isStr, "string");
  check("product_name", isStr, "string");
  check("texts", isArr, "array");
  check("product_links", isArr, "array");
  check("videos", isArr, "array");
  check("source", isNum, "number");
  check("level", isNum, "number");
  check("author", isStr, "string");
  check("push_admin", isStr, "string");
  check("roas", isNum, "number");
  check("platforms", isArr, "array");
  check("selling_point", isStr, "string");
  check("tags", isArr, "array");
  if (isArr(p.texts)) p.texts.forEach((t, i) => {
    if (!t || typeof t !== "object") { errs.push(`texts[${i}] 不是对象`); return; }
    ["title", "message", "description"].forEach((k) => {
      if (!isStr(t[k])) errs.push(`texts[${i}].${k} 必须是 string`);
    });
  });
  if (isArr(p.product_links)) p.product_links.forEach((l, i) => {
    if (!isStr(l)) errs.push(`product_links[${i}] 必须是 string`);
  });
  if (isArr(p.platforms)) p.platforms.forEach((v, i) => {
    if (!isStr(v)) errs.push(`platforms[${i}] 必须是 string`);
  });
  if (isArr(p.videos)) p.videos.forEach((v, i) => {
    if (!v || typeof v !== "object") { errs.push(`videos[${i}] 不是对象`); return; }
    ["name", "url", "image_url"].forEach((k) => {
      if (!isStr(v[k])) errs.push(`videos[${i}].${k} 必须是 string`);
    });
    ["size", "width", "height"].forEach((k) => {
      if (!isNum(v[k])) errs.push(`videos[${i}].${k} 必须是 number`);
    });
  });
  return errs;
}

/* ================================================================
 * 视图 1：推送列表
 * ================================================================ */

function renderList(container) {
  const state = { page: 1, pageSize: 20, q: "", archived: "0", total: 0, items: [], loading: false, error: "" };

  clear(container);

  const toolbar = el("div", { class: "ap-toolbar" });
  const qGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "关键词（产品名 / product_code）"),
  ]);
  const qInput = el("input", { class: "ap-input", type: "text", placeholder: "输入后回车或点查询" });
  qInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  qGroup.appendChild(qInput);

  const archivedGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "归档状态"),
  ]);
  const archivedSelect = el("select", { class: "ap-input" });
  [["0", "未归档"], ["1", "已归档"], ["all", "全部"]].forEach(([v, t]) => {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = t;
    archivedSelect.appendChild(opt);
  });
  archivedGroup.appendChild(archivedSelect);

  const btnSearch = el("button", { type: "button", class: "ap-btn-primary" }, "查询");
  btnSearch.addEventListener("click", doSearch);
  const btnReload = el("button", { type: "button", class: "ap-btn-ghost" }, "刷新");
  btnReload.addEventListener("click", () => { load(); });

  toolbar.appendChild(qGroup);
  toolbar.appendChild(archivedGroup);
  toolbar.appendChild(btnSearch);
  toolbar.appendChild(btnReload);

  const tableBox = el("div", { class: "ap-card" });
  const table = el("table", { class: "ap-table" });
  const thead = el("thead", {}, [
    el("tr", {}, [
      el("th", {}, "Product Code"),
      el("th", {}, "名称"),
      el("th", { style: "width: 180px;" }, "主图语种"),
      el("th", { style: "width: 180px;" }, "文案语种"),
      el("th", { style: "width: 200px;" }, "视频素材"),
      el("th", { style: "width: 180px;" }, "操作"),
    ]),
  ]);
  const tbody = el("tbody");
  table.appendChild(thead);
  table.appendChild(tbody);
  tableBox.appendChild(table);

  const pager = el("div", { class: "ap-pagination" });
  const errBanner = el("p", { class: "ap-error", hidden: true });

  container.appendChild(toolbar);
  container.appendChild(errBanner);
  container.appendChild(tableBox);
  container.appendChild(pager);

  function doSearch() {
    state.q = qInput.value.trim();
    state.archived = archivedSelect.value;
    state.page = 1;
    load();
  }

  async function load() {
    state.loading = true;
    errBanner.hidden = true;
    clear(tbody);
    tbody.appendChild(el("tr", {}, [el("td", { colspan: 6, class: "ap-empty" }, "加载中…")]));

    try {
      const data = await api.list({
        page: state.page, pageSize: state.pageSize,
        q: state.q, archived: state.archived,
      });
      state.total = data.total || 0;
      state.items = Array.isArray(data.items) ? data.items : [];
    } catch (err) {
      state.error = err.message || "加载失败";
      errBanner.textContent = state.error;
      errBanner.hidden = false;
      state.items = [];
      state.total = 0;
    } finally {
      state.loading = false;
      renderBody();
      renderPager();
    }
  }

  function renderLangs(list, muted = false) {
    if (!list || list.length === 0) return el("span", { class: "ap-code" }, "—");
    const frag = document.createDocumentFragment();
    list.forEach((l) => {
      frag.appendChild(el("span", { class: `ap-langpill${muted ? " muted" : ""}` }, l));
    });
    return frag;
  }

  function renderItemLangs(langMap) {
    if (!langMap || Object.keys(langMap).length === 0) {
      return el("span", { class: "ap-code" }, "—");
    }
    const frag = document.createDocumentFragment();
    Object.entries(langMap).forEach(([lang, count]) => {
      frag.appendChild(el("span", { class: "ap-langpill" }, `${lang} × ${count}`));
    });
    return frag;
  }

  function renderBody() {
    clear(tbody);
    if (state.items.length === 0 && !state.error) {
      tbody.appendChild(el("tr", {}, [el("td", { colspan: 6, class: "ap-empty" }, "暂无数据")]));
      return;
    }
    state.items.forEach((item) => {
      const row = el("tr");

      row.appendChild(el("td", {}, [
        el("div", { class: "ap-code" }, item.product_code || ""),
      ]));
      row.appendChild(el("td", {}, [
        el("div", {}, item.name || ""),
        item.archived ? el("span", { class: "ap-langpill muted" }, "已归档") : null,
      ]));

      const coverCell = el("td", {});
      coverCell.appendChild(renderLangs(item.cover_langs));
      row.appendChild(coverCell);

      const copyCell = el("td", {});
      copyCell.appendChild(renderLangs(item.copywriting_langs));
      row.appendChild(copyCell);

      const itemsCell = el("td", {});
      itemsCell.appendChild(renderItemLangs(item.item_langs));
      row.appendChild(itemsCell);

      // 操作列：一个 lang 下拉 + 跳转按钮
      const actionCell = el("td", {});
      const langSelect = el("select", { class: "ap-lang-select" });
      const availableLangs = Object.keys(item.item_langs || {});
      // 默认选项集合：item 语种 ∪ ad_supported_langs
      const extra = (item.ad_supported_langs || "").split(",").map((s) => s.trim()).filter(Boolean);
      const allLangs = Array.from(new Set([...availableLangs, ...extra]));
      if (allLangs.length === 0) allLangs.push("en");
      allLangs.forEach((l) => {
        const opt = document.createElement("option");
        opt.value = l; opt.textContent = l;
        langSelect.appendChild(opt);
      });
      const btnGo = el("button", { type: "button", class: "ap-btn-primary" }, "去载荷");
      btnGo.addEventListener("click", () => {
        activate("payload");
        window.__AP_PREFILL_PAYLOAD__ = { code: item.product_code, lang: langSelect.value };
        renderPayload(document.getElementById("ap-payload-root"));
      });
      actionCell.appendChild(langSelect);
      actionCell.appendChild(btnGo);
      row.appendChild(actionCell);

      tbody.appendChild(row);
    });
  }

  function renderPager() {
    clear(pager);
    const totalPages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize));
    pager.appendChild(el("span", {}, `共 ${state.total} 条，第 ${state.page} / ${totalPages} 页`));
    const btnPrev = el("button", { type: "button" }, "上一页");
    btnPrev.disabled = state.page <= 1;
    btnPrev.addEventListener("click", () => { state.page--; load(); });
    const btnNext = el("button", { type: "button" }, "下一页");
    btnNext.disabled = state.page >= totalPages;
    btnNext.addEventListener("click", () => { state.page++; load(); });
    pager.appendChild(btnPrev);
    pager.appendChild(btnNext);
  }

  load();
}

/* ================================================================
 * 视图 2：推送创建
 * ================================================================ */

function renderCreate(container) {
  const defaultForm = {
    mode: "create",
    product_name: "",
    texts: [{ title: "", message: "", description: "" }],
    product_links: [""],
    videos: [{ name: "", size: "", width: "", height: "", url: "", image_url: "" }],
    source: "0",
    level: "3",
    author: "",
    push_admin: "",
    roas: "1.6",
    platforms: ["tiktok"],
    selling_point: "",
    tags: [],
  };
  const state = {
    form: JSON.parse(JSON.stringify(defaultForm)),
    productCode: "",
    fetching: false, fetchError: "", fetchInfo: "", responseText: "",
  };

  clear(container);

  const queryCard = el("section", { class: "ap-card" });
  const queryRow = el("div", { class: "ap-query-row" });
  const codeInput = el("input", {
    class: "ap-input", type: "text",
    placeholder: "输入产品 ID / product_code",
  });
  codeInput.addEventListener("input", (e) => { state.productCode = e.target.value; });
  const btnFetch = el("button", { type: "button", class: "ap-btn-primary" }, "获取");
  btnFetch.addEventListener("click", handleFetch);
  queryRow.appendChild(codeInput);
  queryRow.appendChild(btnFetch);
  queryCard.appendChild(queryRow);
  const fetchErr = el("p", { class: "ap-error", hidden: true });
  const fetchInfo = el("p", { class: "ap-info", hidden: true });
  queryCard.appendChild(fetchErr);
  queryCard.appendChild(fetchInfo);
  const respGroup = el("label", { class: "ap-input-group", style: "margin-top: 16px;" }, [
    el("span", { class: "ap-input-label" }, "返回报文（JSON）"),
  ]);
  const respText = el("textarea", {
    class: "ap-textarea ap-response-text", readonly: true,
    placeholder: "点击「获取」后，这里会显示上游返回的完整 JSON 报文",
  });
  respGroup.appendChild(respText);
  queryCard.appendChild(respGroup);
  container.appendChild(queryCard);

  const basicInputs = {};

  const basicCard = buildBasicCard();
  const textsCard = buildArrayObjCard("texts", { title: "", message: "", description: "" }, renderTextItem);
  const linksCard = buildArrayStrCard("product_links");
  const videosCard = buildArrayObjCard("videos", { name: "", size: "", width: "", height: "", url: "", image_url: "" }, renderVideoItem);
  const platformsCard = buildArrayStrCard("platforms");
  const tagsCard = buildArrayStrCard("tags");
  const jsonCard = el("section", { class: "ap-card" }, [el("h3", {}, "JSON 预览")]);
  const jsonPre = el("pre", { class: "ap-json" });
  jsonCard.appendChild(jsonPre);

  container.appendChild(basicCard);
  container.appendChild(textsCard);
  container.appendChild(linksCard);
  container.appendChild(videosCard);
  container.appendChild(platformsCard);
  container.appendChild(tagsCard);
  container.appendChild(jsonCard);

  function buildBasicCard() {
    const card = el("section", { class: "ap-card" }, [el("h3", {}, "基本信息")]);
    const grid = el("div", { class: "ap-grid" });
    ["mode", "product_name", "source", "level", "author", "push_admin", "roas", "selling_point"]
      .forEach((k) => {
        const g = el("label", { class: "ap-input-group" }, [
          el("span", { class: "ap-input-label" }, k),
        ]);
        const input = el("input", { class: "ap-input", type: "text" });
        input.value = state.form[k] ?? "";
        input.addEventListener("input", (e) => { state.form[k] = e.target.value; refreshJson(); });
        basicInputs[k] = input;
        g.appendChild(input);
        grid.appendChild(g);
      });
    card.appendChild(grid);
    return card;
  }

  function buildArrayObjCard(key, template, itemRenderer) {
    const card = el("section", { class: "ap-card" });
    card.appendChild(el("div", { class: "ap-array-header" }, [
      el("h3", {}, key),
      el("button", {
        type: "button", class: "ap-btn-ghost",
        onclick: () => {
          state.form[key].push(JSON.parse(JSON.stringify(template)));
          redrawObjCard(card, key, itemRenderer);
          refreshJson();
        },
      }, "添加"),
    ]));
    card.appendChild(el("div", { dataset: { list: "1" } }));
    redrawObjCard(card, key, itemRenderer);
    return card;
  }

  function redrawObjCard(card, key, itemRenderer) {
    const list = card.querySelector("[data-list]");
    clear(list);
    const items = state.form[key] || [];
    if (items.length === 0) { list.appendChild(el("p", { class: "ap-empty" }, "（空）")); return; }
    items.forEach((_, index) => {
      const wrap = el("div", { class: "ap-array-item" });
      wrap.appendChild(el("div", { class: "ap-array-header" }, [
        el("span", { class: "ap-input-label" }, `${key}[${index}]`),
        el("button", {
          type: "button", class: "ap-btn-ghost",
          onclick: () => {
            state.form[key].splice(index, 1);
            redrawObjCard(card, key, itemRenderer);
            refreshJson();
          },
        }, "删除"),
      ]));
      itemRenderer(wrap, key, index);
      list.appendChild(wrap);
    });
  }

  function renderTextItem(wrap, key, index) {
    ["title", "message", "description"].forEach((f) => {
      const g = el("label", { class: "ap-input-group" }, [
        el("span", { class: "ap-input-label" }, f),
      ]);
      const ta = el("textarea", { class: "ap-textarea short" });
      ta.value = state.form[key][index][f] ?? "";
      ta.addEventListener("input", (e) => {
        state.form[key][index][f] = e.target.value; refreshJson();
      });
      g.appendChild(ta);
      wrap.appendChild(g);
    });
  }

  function renderVideoItem(wrap, key, index) {
    const grid = el("div", { class: "ap-grid" });
    ["name", "size", "width", "height"].forEach((f) => {
      const g = el("label", { class: "ap-input-group" }, [
        el("span", { class: "ap-input-label" }, f),
      ]);
      const input = el("input", { class: "ap-input", type: "text" });
      input.value = state.form[key][index][f] ?? "";
      input.addEventListener("input", (e) => { state.form[key][index][f] = e.target.value; refreshJson(); });
      g.appendChild(input);
      grid.appendChild(g);
    });
    wrap.appendChild(grid);
    ["url", "image_url"].forEach((f) => {
      const g = el("label", { class: "ap-input-group" }, [
        el("span", { class: "ap-input-label" }, f),
      ]);
      const ta = el("textarea", { class: "ap-textarea short" });
      ta.value = state.form[key][index][f] ?? "";
      ta.addEventListener("input", (e) => { state.form[key][index][f] = e.target.value; refreshJson(); });
      g.appendChild(ta);
      wrap.appendChild(g);
    });
  }

  function buildArrayStrCard(key) {
    const card = el("section", { class: "ap-card" });
    card.appendChild(el("div", { class: "ap-array-header" }, [
      el("h3", {}, key),
      el("button", {
        type: "button", class: "ap-btn-ghost",
        onclick: () => {
          state.form[key].push("");
          redrawStrCard(card, key);
          refreshJson();
        },
      }, "添加"),
    ]));
    card.appendChild(el("div", { dataset: { list: "1" } }));
    redrawStrCard(card, key);
    return card;
  }

  function redrawStrCard(card, key) {
    const list = card.querySelector("[data-list]");
    clear(list);
    const items = state.form[key] || [];
    if (items.length === 0) { list.appendChild(el("p", { class: "ap-empty" }, "（空）")); return; }
    items.forEach((v, index) => {
      const row = el("div", { class: "ap-array-row" });
      const g = el("label", { class: "ap-input-group" }, [
        el("span", { class: "ap-input-label" }, `${key}[${index}]`),
      ]);
      const input = el("input", { class: "ap-input", type: "text" });
      input.value = v ?? "";
      input.addEventListener("input", (e) => { state.form[key][index] = e.target.value; refreshJson(); });
      g.appendChild(input);
      row.appendChild(g);
      row.appendChild(el("button", {
        type: "button", class: "ap-btn-ghost",
        onclick: () => {
          state.form[key].splice(index, 1);
          redrawStrCard(card, key);
          refreshJson();
        },
      }, "删除"));
      list.appendChild(row);
    });
  }

  function refreshJson() { jsonPre.textContent = JSON.stringify(state.form, null, 2); }

  async function handleFetch() {
    const code = state.productCode.trim();
    if (!code) {
      state.fetchError = "请输入 product_code";
      syncFetchUI();
      return;
    }
    state.fetching = true;
    state.fetchError = ""; state.fetchInfo = ""; state.responseText = "";
    syncFetchUI();
    try {
      const payload = await api.fetchMaterials(code);
      const name = payload?.product?.name ?? "";
      if (name) {
        state.form.product_name = name;
        if (basicInputs.product_name) basicInputs.product_name.value = name;
      }
      state.fetchInfo = `已获取：${name || "未命名产品"}（product_code: ${payload?.product?.product_code ?? code}）`;
      state.responseText = JSON.stringify(payload, null, 2);
      refreshJson();
    } catch (err) {
      state.fetchError = err.message || "查询失败";
      state.responseText = JSON.stringify(err.payload || { message: err.message }, null, 2);
    } finally {
      state.fetching = false;
      syncFetchUI();
    }
  }

  function syncFetchUI() {
    btnFetch.disabled = state.fetching;
    btnFetch.textContent = state.fetching ? "获取中..." : "获取";
    codeInput.disabled = state.fetching;
    fetchErr.hidden = !state.fetchError; fetchErr.textContent = state.fetchError;
    fetchInfo.hidden = !state.fetchInfo; fetchInfo.textContent = state.fetchInfo;
    respText.value = state.responseText;
  }

  refreshJson();
}

/* ================================================================
 * 视图 3：推送载荷
 * ================================================================ */

function renderPayload(container) {
  const prefill = window.__AP_PREFILL_PAYLOAD__ || {};
  window.__AP_PREFILL_PAYLOAD__ = null;

  const state = {
    productCode: prefill.code || "",
    lang: prefill.lang || "de",
    fetching: false,
    errorMessage: "",
    responseText: "",
    videos: [],
    payloadData: null,
    pushing: false,
    pushError: "",
    pushResult: "",
  };

  clear(container);
  const card = el("section", { class: "ap-card" });
  container.appendChild(card);

  const inputs = el("div", { class: "ap-grid" });
  const codeGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "product_code"),
  ]);
  const codeInput = el("input", {
    class: "ap-input", type: "text", value: state.productCode,
    placeholder: "例如：3d-curved-screen-magnifier-for-smartphones",
  });
  codeInput.addEventListener("input", (e) => { state.productCode = e.target.value; });
  codeGroup.appendChild(codeInput);

  const langGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "lang（de/fr/es/it/ja/pt 等）"),
  ]);
  const langInput = el("input", {
    class: "ap-input", type: "text", value: state.lang, placeholder: "例如：de",
  });
  langInput.addEventListener("input", (e) => { state.lang = e.target.value; });
  langGroup.appendChild(langInput);

  inputs.appendChild(codeGroup);
  inputs.appendChild(langGroup);
  card.appendChild(inputs);

  const btnRow = el("div", { class: "ap-array-row", style: "margin-top: 16px; gap: 12px;" });
  const btnFetch = el("button", { type: "button", class: "ap-btn-primary" }, "加载数据");
  const btnPush = el("button", { type: "button", class: "ap-btn-success", disabled: true }, "推送");
  btnRow.appendChild(btnFetch);
  btnRow.appendChild(btnPush);
  card.appendChild(btnRow);

  const errBanner = el("p", { class: "ap-error", hidden: true });
  card.appendChild(errBanner);

  const respGroup = el("label", { class: "ap-input-group", style: "margin-top: 16px;" }, [
    el("span", { class: "ap-input-label" }, "返回报文（JSON）"),
  ]);
  const respText = el("textarea", {
    class: "ap-textarea ap-response-text", readonly: true,
    placeholder: "点击「加载数据」后，这里会显示上游返回的完整 JSON 报文",
  });
  respGroup.appendChild(respText);
  card.appendChild(respGroup);

  const pushErrBanner = el("pre", { class: "ap-error", style: "white-space: pre-wrap;", hidden: true });
  card.appendChild(pushErrBanner);

  const pushResultGroup = el("label", {
    class: "ap-input-group", style: "margin-top: 16px;", hidden: true,
  }, [el("span", { class: "ap-input-label" }, "推送响应")]);
  const pushResultText = el("textarea", { class: "ap-textarea ap-response-text", readonly: true });
  pushResultGroup.appendChild(pushResultText);
  card.appendChild(pushResultGroup);

  const mediaList = el("div", { class: "ap-media-list", hidden: true });
  card.appendChild(mediaList);

  function sync() {
    btnFetch.disabled = state.fetching;
    btnFetch.textContent = state.fetching ? "加载中..." : "加载数据";
    btnPush.disabled = state.pushing || !state.payloadData;
    btnPush.textContent = state.pushing ? "推送中..." : "推送";
    codeInput.disabled = state.fetching;
    langInput.disabled = state.fetching;

    errBanner.hidden = !state.errorMessage;
    errBanner.textContent = state.errorMessage;
    respText.value = state.responseText;

    pushErrBanner.hidden = !state.pushError;
    pushErrBanner.textContent = state.pushError;
    pushResultGroup.hidden = !state.pushResult;
    pushResultText.value = state.pushResult;

    clear(mediaList);
    mediaList.hidden = state.videos.length === 0;
    state.videos.forEach((v, i) => {
      const row = el("div", { class: "ap-media-row" });

      const coverItem = el("div", { class: "ap-media-item" }, [
        el("span", { class: "ap-input-label" }, `videos[${i}].image_url`),
      ]);
      if (v.image_url) {
        coverItem.appendChild(el("img", {
          class: "ap-media-frame", src: v.image_url, alt: v.name ?? `cover-${i}`,
        }));
      } else {
        coverItem.appendChild(el("div", { class: "ap-media-frame ap-media-empty" }, "无封面"));
      }

      const videoItem = el("div", { class: "ap-media-item" }, [
        el("span", { class: "ap-input-label" }, `videos[${i}].url`),
      ]);
      if (v.url) {
        videoItem.appendChild(el("video", {
          class: "ap-media-frame", src: v.url,
          poster: v.image_url || null, controls: true, preload: "metadata",
        }));
      } else {
        videoItem.appendChild(el("div", { class: "ap-media-frame ap-media-empty" }, "无视频"));
      }
      row.appendChild(coverItem);
      row.appendChild(videoItem);
      mediaList.appendChild(row);
    });
  }

  async function doFetch() {
    const code = state.productCode.trim();
    const lang = state.lang.trim();
    if (!code) { state.errorMessage = "请输入 product_code"; sync(); return; }
    if (!lang) { state.errorMessage = "请输入 lang"; sync(); return; }
    Object.assign(state, {
      fetching: true, errorMessage: "", responseText: "",
      videos: [], payloadData: null, pushError: "", pushResult: "",
    });
    sync();
    try {
      const payload = await api.fetchPushPayload(code, lang);
      state.responseText = JSON.stringify(payload, null, 2);
      state.videos = Array.isArray(payload?.videos) ? payload.videos : [];
      state.payloadData = payload;
    } catch (err) {
      state.errorMessage = err.message || "查询失败";
      state.responseText = JSON.stringify(err.payload || { message: err.message }, null, 2);
    } finally {
      state.fetching = false;
      sync();
    }
  }

  async function doPush() {
    if (!state.payloadData) {
      state.pushError = "请先点击「加载数据」拿到有效报文"; state.pushResult = ""; sync(); return;
    }
    const errs = validatePayload(state.payloadData);
    if (errs.length > 0) {
      state.pushError = "数据格式校验失败：\n- " + errs.join("\n- ");
      state.pushResult = ""; sync(); return;
    }
    Object.assign(state, { pushing: true, pushError: "", pushResult: "" });
    sync();
    try {
      const body = await api.push(state.payloadData);
      state.pushResult = JSON.stringify(body, null, 2);
    } catch (err) {
      state.pushError = err.message || "推送失败";
      state.pushResult = JSON.stringify(err.payload || { message: err.message }, null, 2);
    } finally {
      state.pushing = false;
      sync();
    }
  }

  btnFetch.addEventListener("click", doFetch);
  btnPush.addEventListener("click", doPush);

  sync();

  // 如果是从列表 tab 带入 prefill，自动执行一次 fetch
  if (prefill.code && prefill.lang) {
    doFetch();
  }
}

/* ---------- tab 切换 ---------- */

let listInit = false;
let createInit = false;
let payloadInit = false;

function activate(name) {
  document.querySelectorAll(".ap-tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".ap-panel").forEach((p) => {
    p.hidden = p.dataset.panel !== name;
  });
  if (name === "list" && !listInit) {
    renderList(document.getElementById("ap-list-root"));
    listInit = true;
  }
  if (name === "create" && !createInit) {
    renderCreate(document.getElementById("ap-create-root"));
    createInit = true;
  }
  if (name === "payload" && !payloadInit) {
    renderPayload(document.getElementById("ap-payload-root"));
    payloadInit = true;
  }
}

async function init() {
  document.querySelectorAll(".ap-tab").forEach((t) => {
    t.addEventListener("click", () => activate(t.dataset.tab));
  });
  try {
    const cfg = await api.config();
    const hint = document.getElementById("ap-config-hint");
    if (hint) {
      hint.textContent = `upstream=${cfg.autovideoBaseUrl} · target=${cfg.pushMediasTarget}`;
    }
  } catch (_) { /* 配置读不到不阻塞界面 */ }
  activate("list");
}

init();
