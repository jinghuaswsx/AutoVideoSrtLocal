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
  fetchByKeys: ({ productId, lang, filename }) => {
    const p = new URLSearchParams();
    p.set("product_id", String(productId));
    p.set("lang", lang);
    p.set("filename", filename);
    return apiJson(`/api/push-items/by-keys?${p.toString()}`);
  },
  fetchMaterials: (code) =>
    apiJson(`/api/materials/${encodeURIComponent(code)}`),
  pushItems: ({ page, pageSize, q, status, lang }) => {
    const p = new URLSearchParams();
    p.set("page", String(page));
    p.set("page_size", String(pageSize));
    if (q) p.set("q", q);
    if (status) p.set("status", status);
    if (lang) p.set("lang", lang);
    return apiJson(`/api/push-items?${p.toString()}`);
  },
  getPushItem: (itemId) => apiJson(`/api/push-items/${itemId}`),
  pushItem: (itemId, payload) =>
    apiJson(`/api/push-items/${itemId}/push`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  // 手动推送（无 item_id，不写回主项目）
  push: (payload) =>
    apiJson("/api/push/medias", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  pushLocalizedTexts: (mkId, payload) =>
    apiJson(`/api/marketing/medias/${encodeURIComponent(mkId)}/texts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
};

const runtimeConfig = {
  autovideoBaseUrl: "",
  pushMediasTarget: "",
  pushLocalizedTextsBaseUrl: "",
};
const LOCALIZED_LABEL_TO_FIELD = {
  "标题": "title",
  "文案": "message",
  "描述": "description",
};

function buildLocalizedPushTargetUrl(mkId) {
  const base = (runtimeConfig.pushLocalizedTextsBaseUrl || "").replace(/\/+$/, "");
  if (!base || !mkId) return "";
  return `${base}/api/marketing/medias/${encodeURIComponent(mkId)}/texts`;
}

function parseLocalizedTaggedText(text) {
  const source = String(text || "");
  const matches = Array.from(source.matchAll(/(标题|文案|描述)\s*[:：]\s*/g));
  if (!matches.length) return null;

  const fields = {};
  matches.forEach((match, index) => {
    const field = LOCALIZED_LABEL_TO_FIELD[match[1]];
    const start = (match.index ?? 0) + match[0].length;
    const end = index + 1 < matches.length ? (matches[index + 1].index ?? source.length) : source.length;
    fields[field] = source.slice(start, end).trim();
  });

  const required = ["title", "message", "description"];
  if (required.some((key) => !fields[key])) return null;
  return fields;
}

function normalizeLocalizedTextCandidate(candidate, fallbackLang = "") {
  if (!candidate || typeof candidate !== "object") return null;
  const rawTitle = String(candidate.title || "").trim();
  const rawMessage = String(candidate.message ?? candidate.body ?? "").trim();
  const rawDescription = String(candidate.description || "").trim();
  const lang = String(candidate.lang || fallbackLang || "").trim();

  const parsed = parseLocalizedTaggedText(rawMessage);
  if (parsed) {
    return { ...parsed, lang };
  }
  if (!(rawTitle || rawMessage || rawDescription)) return null;
  return {
    title: rawTitle,
    message: rawMessage,
    description: rawDescription,
    lang,
  };
}

function buildLocalizedDisplayText(pushContext) {
  const rawTexts = Array.isArray(pushContext?.localizedTextsRequest?.texts)
    ? pushContext.localizedTextsRequest.texts
    : [];
  const fallbackLang = pushContext?.localizedText?.lang || rawTexts[0]?.lang || "";
  const candidates = [
    pushContext?.localizedText,
    ...rawTexts,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeLocalizedTextCandidate(candidate, fallbackLang);
    if (normalized && normalized.title && normalized.message && normalized.description) {
      return normalized;
    }
  }
  return null;
}

function buildLocalizedPushRequest(pushContext) {
  const normalized = buildLocalizedDisplayText(pushContext);
  if (!normalized) return { texts: [] };
  return {
    texts: [{
      title: normalized.title,
      message: normalized.message,
      description: normalized.description,
      lang: normalized.lang || "",
    }],
  };
}

/* ---------- 状态文案映射 ---------- */

const STATUS_LABELS = {
  not_ready: { text: "制作中", cls: "ap-badge-gray" },
  pending:   { text: "待推送", cls: "ap-badge-blue" },
  pushed:    { text: "已推送", cls: "ap-badge-green" },
  failed:    { text: "推送失败", cls: "ap-badge-red" },
};

const PUSH_MODAL_MODES = {
  CONFIRM: "confirm",
  JSON: "json",
  LOCALIZED_TEXT: "localized-text",
  LOCALIZED_JSON: "localized-json",
};

function renderStatusBadge(status) {
  const s = STATUS_LABELS[status] || { text: status || "-", cls: "" };
  return el("span", { class: `ap-badge ${s.cls}` }, s.text);
}

/* 把 push-payload JSON 结构化成表格 + 视频预览，便于人工复核。 */
function openPushModal(item, opts = {}) {
  const onPushed = opts.onPushed || (() => {});

  const overlay = el("div", { class: "ap-modal-overlay" });
  const modal = el("div", { class: "ap-modal" });
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const btnClose = el("button", {
    type: "button", class: "ap-modal-close", "aria-label": "关闭",
  }, "×");
  const header = el("div", { class: "ap-modal-header" });
  const pillRow = el("div", { class: "ap-modal-pills" });
  const headerButtons = [
    { mode: PUSH_MODAL_MODES.CONFIRM, label: "推送确认" },
    { mode: PUSH_MODAL_MODES.JSON, label: "JSON 预览" },
    { mode: PUSH_MODAL_MODES.LOCALIZED_TEXT, label: "推送小语种文案" },
    { mode: PUSH_MODAL_MODES.LOCALIZED_JSON, label: "小语种文案JSON预览" },
  ].map(({ mode, label }) => el("button", {
    type: "button",
    class: "ap-modal-pill",
    dataset: { mode },
  }, label));
  headerButtons.forEach((btn) => pillRow.appendChild(btn));
  header.appendChild(pillRow);
  header.appendChild(btnClose);
  modal.appendChild(header);

  const body = el("div", { class: "ap-modal-body" });
  modal.appendChild(body);

  const infoSection = el("section", { class: "ap-modal-section" }, [
    el("h4", {}, "素材信息"),
  ]);
  const kv = el("div", { class: "ap-kv" });
  const itemIdValue = el("span", { class: "v" }, String(item.item_id || "-"));
  const mkIdValue = el("span", { class: "v" }, "-");
  const addKV = (k, v) => {
    kv.appendChild(el("span", { class: "k" }, k));
    if (v instanceof Node) {
      kv.appendChild(v);
      return;
    }
    kv.appendChild(el("span", { class: "v" }, v));
  };
  addKV("产品", `${item.product_name || ""}  ·  ${item.product_code || ""}`);
  addKV("语种", item.lang || "-");
  addKV("文件", item.display_name || item.filename || "-");
  addKV("item_id", itemIdValue);
  addKV("mk_id", mkIdValue);
  addKV("状态", STATUS_LABELS[item.status]?.text || item.status);
  infoSection.appendChild(kv);
  body.appendChild(infoSection);

  const contentSection = el("section", { class: "ap-modal-section" }, [
    el("h4", {}, "推送内容"),
  ]);
  const payloadStatus = el("p", { class: "ap-empty" }, "加载中...");
  const confirmPane = el("div", { class: "ap-modal-pane" });
  const payloadBox = el("div", {});
  confirmPane.appendChild(payloadBox);
  const jsonPre = el("pre", { class: "ap-json ap-modal-json-full ap-modal-pane", hidden: true });
  const localizedPane = el("div", { class: "ap-modal-pane", hidden: true });
  const localizedJsonPre = el("pre", { class: "ap-json ap-modal-json-full ap-modal-pane", hidden: true });
  contentSection.appendChild(payloadStatus);
  contentSection.appendChild(confirmPane);
  contentSection.appendChild(jsonPre);
  contentSection.appendChild(localizedPane);
  contentSection.appendChild(localizedJsonPre);
  body.appendChild(contentSection);

  const actions = el("div", { class: "ap-modal-actions" });
  const btnPush = el("button", {
    type: "button", class: "ap-pill-btn primary", disabled: true,
  }, "推送");
  const btnCancel = el("button", {
    type: "button", class: "ap-pill-btn secondary",
  }, "取消");
  actions.appendChild(btnPush);
  actions.appendChild(btnCancel);
  modal.appendChild(actions);

  const respWrap = el("div", { class: "ap-modal-response", hidden: true });
  const respTitle = el("h4", {}, "推送响应");
  const respPre = el("pre", { class: "ap-json" });
  respWrap.appendChild(respTitle);
  respWrap.appendChild(respPre);
  modal.appendChild(respWrap);

  const footer = el("div", { class: "ap-modal-footer" });
  const btnFooterClose = el("button", {
    type: "button", class: "ap-btn-ghost",
  }, "关闭");
  footer.appendChild(btnFooterClose);
  modal.appendChild(footer);

  let activeMode = PUSH_MODAL_MODES.CONFIRM;
  let pushContext = {
    itemId: item.item_id || null,
    mkId: null,
    payload: null,
    localizedText: null,
    localizedTextsRequest: { texts: [] },
    localizedTargetUrl: "",
  };
  let anyPushSucceeded = false;
  let materialPushed = false;
  let localizedTextPushed = false;

  function close() {
    if (!overlay.parentNode) return;
    overlay.parentNode.removeChild(overlay);
    document.removeEventListener("keydown", onEsc);
    if (anyPushSucceeded) onPushed();
  }
  function onEsc(e) { if (e.key === "Escape") close(); }
  document.addEventListener("keydown", onEsc);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  btnClose.addEventListener("click", close);
  btnCancel.addEventListener("click", close);
  btnFooterClose.addEventListener("click", close);

  function currentLocalizedTexts() {
    return buildLocalizedPushRequest(pushContext).texts;
  }

  function isLocalizedMode(mode = activeMode) {
    return mode === PUSH_MODAL_MODES.LOCALIZED_TEXT
      || mode === PUSH_MODAL_MODES.LOCALIZED_JSON;
  }

  function getLocalizedTextError() {
    if (!pushContext.mkId) return "缺少 mk_id，暂不能推送小语种文案";
    if (!currentLocalizedTexts().length) return "当前语种暂无可推送文案";
    return "";
  }

  function syncPushButton() {
    if (isLocalizedMode()) {
      const localizedError = getLocalizedTextError();
      btnPush.disabled = localizedTextPushed || !pushContext.payload || Boolean(localizedError);
      btnPush.textContent = localizedTextPushed
        ? "文案已推送"
        : localizedError
          ? "无法推送"
          : "推送";
      return;
    }
    btnPush.disabled = materialPushed || !pushContext.payload;
    btnPush.textContent = materialPushed ? "素材已推送" : "推送";
  }

  function setMode(mode) {
    activeMode = mode;
    headerButtons.forEach((btn) => {
      const isActive = btn.dataset.mode === mode;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    confirmPane.hidden = mode !== PUSH_MODAL_MODES.CONFIRM;
    jsonPre.hidden = mode !== PUSH_MODAL_MODES.JSON;
    localizedPane.hidden = mode !== PUSH_MODAL_MODES.LOCALIZED_TEXT;
    localizedJsonPre.hidden = mode !== PUSH_MODAL_MODES.LOCALIZED_JSON;
    syncPushButton();
  }

  function showResponse(obj, isError, titleText) {
    respWrap.hidden = false;
    respTitle.textContent = titleText || "推送响应";
    respPre.textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
    respPre.style.color = isError ? "var(--oc-danger-fg)" : "var(--oc-fg)";
    respPre.style.borderColor = isError ? "var(--oc-danger)" : "var(--oc-border)";
  }

  function renderLocalizedPane() {
    clear(localizedPane);
    const errorMessage = getLocalizedTextError();
    const localizedText = buildLocalizedDisplayText(pushContext);
    const targetInfo = renderLocalizedTargetInfo(pushContext.mkId, pushContext.localizedTargetUrl);
    if (targetInfo) {
      localizedPane.appendChild(targetInfo);
    }
    if (errorMessage) {
      localizedPane.appendChild(el("p", { class: "ap-error" }, errorMessage));
    }
    if (!localizedText) {
      localizedPane.appendChild(el("p", { class: "ap-empty" }, "当前语种暂无可推送文案"));
      return;
    }
    localizedPane.appendChild(renderLocalizedTextView(localizedText));
  }

  headerButtons.forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  (async () => {
    try {
      let payload;
      let context = null;
      if (item.product_id && item.lang && item.filename) {
        const resp = await api.fetchByKeys({
          productId: item.product_id,
          lang: item.lang,
          filename: item.filename,
        });
        context = resp;
        payload = resp.payload;
        if (resp.item_id) item.item_id = resp.item_id;
      } else {
        payload = await api.fetchPushPayload(item.product_code, item.lang);
      }
      pushContext = {
        itemId: context?.item_id || item.item_id || null,
        mkId: context?.mk_id ?? null,
        payload,
        localizedText: context?.localized_text || null,
        localizedTextsRequest: context?.localized_texts_request || { texts: [] },
        localizedTargetUrl: buildLocalizedPushTargetUrl(context?.mk_id ?? null),
      };
      itemIdValue.textContent = String(pushContext.itemId || "-");
      mkIdValue.textContent = String(pushContext.mkId || "-");
      jsonPre.textContent = JSON.stringify(payload, null, 2);
      localizedJsonPre.textContent = JSON.stringify(buildLocalizedRequestPreview(pushContext), null, 2);
      clear(payloadBox);
      payloadBox.appendChild(renderPayloadView(payload));
      renderLocalizedPane();
      payloadStatus.remove();
      syncPushButton();
      setMode(PUSH_MODAL_MODES.CONFIRM);
    } catch (err) {
      payloadStatus.replaceWith(el("p", { class: "ap-error" }, err.message || "载荷加载失败"));
    }
  })();

  btnPush.addEventListener("click", async () => {
    if (!pushContext.payload) return;
    btnPush.disabled = true;
    btnPush.textContent = "推送中...";
    btnCancel.disabled = true;
    try {
      if (isLocalizedMode()) {
        const localizedError = getLocalizedTextError();
        if (localizedError) {
          throw Object.assign(new Error(localizedError), {
            payload: { message: localizedError },
          });
        }
        const body = await api.pushLocalizedTexts(pushContext.mkId, buildLocalizedPushRequest(pushContext));
        showResponse(body, false, "小语种文案推送响应");
        localizedTextPushed = true;
        anyPushSucceeded = true;
      } else {
        const errs = validatePayload(pushContext.payload);
        if (errs.length > 0) {
          showResponse({ error: "校验失败", details: errs }, true, "素材推送响应");
          syncPushButton();
          return;
        }
        const body = pushContext.itemId
          ? await api.pushItem(pushContext.itemId, pushContext.payload)
          : await api.push(pushContext.payload);
        showResponse(body, false, "素材推送响应");
        materialPushed = true;
        anyPushSucceeded = true;
      }
    } catch (err) {
      showResponse(
        err.payload || { message: err.message },
        true,
        isLocalizedMode() ? "小语种文案推送响应" : "素材推送响应",
      );
    } finally {
      btnCancel.disabled = false;
      syncPushButton();
    }
  });
}

function renderLocalizedTextView(localizedText) {
  const root = el("div", { class: "ap-localized-text-card" });
  const kv = el("div", { class: "ap-kv" });
  const pairs = [
    ["语种", localizedText.lang || ""],
    ["标题", localizedText.title || ""],
    ["文案", localizedText.message || ""],
    ["描述", localizedText.description || ""],
  ];
  pairs.forEach(([k, v]) => {
    kv.appendChild(el("span", { class: "k" }, k));
    kv.appendChild(el("span", { class: "v" }, v));
  });
  root.appendChild(kv);
  return root;
}

function buildLocalizedRequestPreview(pushContext) {
  return {
    mk_id: pushContext.mkId ?? null,
    target_url: pushContext.localizedTargetUrl || "",
    texts: currentLocalizedTextsForPreview(pushContext),
  };
}

function currentLocalizedTextsForPreview(pushContext) {
  return buildLocalizedPushRequest(pushContext).texts;
}

function renderLocalizedTargetInfo(mkId, targetUrl) {
  const root = el("div", { class: "ap-localized-text-card" });
  const kv = el("div", { class: "ap-kv" });
  const pairs = [
    ["mk_id", mkId ? String(mkId) : "-"],
    ["推送地址", targetUrl || "(待确定)"],
  ];
  pairs.forEach(([k, v]) => {
    kv.appendChild(el("span", { class: "k" }, k));
    if (k === "推送地址") {
      kv.appendChild(el("span", { class: "v" }, [
        el("code", { class: "ap-code" }, v),
      ]));
      return;
    }
    kv.appendChild(el("span", { class: "v" }, v));
  });
  root.appendChild(kv);
  return root;
}

function renderPayloadView(payload) {
  const root = el("div", {});

  // 基本字段 K/V
  const kv = el("div", { class: "ap-kv" });
  const pairs = [
    ["mode", payload.mode],
    ["product_name", payload.product_name],
    ["author", payload.author],
    ["push_admin", payload.push_admin],
    ["level", payload.level],
    ["roas", payload.roas],
    ["source", payload.source],
    ["platforms", JSON.stringify(payload.platforms || [])],
    ["selling_point", payload.selling_point || "(空)"],
    ["tags", JSON.stringify(payload.tags || [])],
  ];
  pairs.forEach(([k, v]) => {
    kv.appendChild(el("span", { class: "k" }, k));
    const vWrap = el("span", { class: "v" });
    vWrap.appendChild(el("code", {}, String(v ?? "")));
    kv.appendChild(vWrap);
  });
  root.appendChild(kv);

  // product_links 子列表
  if (Array.isArray(payload.product_links) && payload.product_links.length) {
    const sub = el("div", { style: "margin-top: 12px;" }, [
      el("div", { class: "ap-input-label" }, `product_links (${payload.product_links.length})`),
    ]);
    const ul = el("ul", { style: "margin: 4px 0 0; padding-left: 20px; font-size: 12px; word-break: break-all;" });
    payload.product_links.forEach((link) => {
      ul.appendChild(el("li", {}, [el("code", {}, link)]));
    });
    sub.appendChild(ul);
    root.appendChild(sub);
  }

  // texts 子列表
  if (Array.isArray(payload.texts) && payload.texts.length) {
    const sub = el("div", { style: "margin-top: 12px;" }, [
      el("div", { class: "ap-input-label" }, `texts (${payload.texts.length})`),
    ]);
    payload.texts.forEach((t, i) => {
      const tkv = el("div", { class: "ap-kv", style: "margin-top: 4px;" });
      const add = (k, v) => {
        tkv.appendChild(el("span", { class: "k" }, `[${i}] ${k}`));
        tkv.appendChild(el("span", { class: "v" }, [el("code", {}, String(v || ""))]));
      };
      add("title", t.title);
      add("message", t.message);
      add("description", t.description);
      sub.appendChild(tkv);
    });
    root.appendChild(sub);
  }

  // videos 子列表 + 预览
  if (Array.isArray(payload.videos) && payload.videos.length) {
    const sub = el("div", { style: "margin-top: 16px;" }, [
      el("div", { class: "ap-input-label" }, `videos (${payload.videos.length})`),
    ]);
    payload.videos.forEach((v, i) => {
      const vkv = el("div", { class: "ap-kv", style: "margin-top: 8px;" });
      ["name", "size", "width", "height"].forEach((k) => {
        vkv.appendChild(el("span", { class: "k" }, `[${i}] ${k}`));
        vkv.appendChild(el("span", { class: "v" }, [el("code", {}, String(v[k] ?? ""))]));
      });
      sub.appendChild(vkv);

      const preview = el("div", { class: "ap-video-preview", style: "margin-top: 8px;" });
      if (v.image_url) {
        preview.appendChild(el("img", { class: "ap-thumb", src: v.image_url, alt: `cover-${i}` }));
      }
      if (v.url) {
        preview.appendChild(el("video", {
          class: "ap-thumb", src: v.url, poster: v.image_url || null,
          controls: true, preload: "metadata",
        }));
      }
      sub.appendChild(preview);
    });
    root.appendChild(sub);
  }

  return root;
}

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
 * 视图 1：推送列表（素材 × 语种扁平表）
 * ================================================================ */

function renderList(container) {
  const state = {
    page: 1, pageSize: 20, q: "", status: "pending", lang: "",
    total: 0, items: [], loading: false, error: "",
  };

  clear(container);

  // ---- 筛选工具条 ----
  const toolbar = el("div", { class: "ap-toolbar" });

  const qGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "关键词（产品名 / product_code）"),
  ]);
  const qInput = el("input", { class: "ap-input", type: "text", placeholder: "回车或点查询" });
  qInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  qGroup.appendChild(qInput);

  const statusGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "状态"),
  ]);
  const statusSelect = el("select", { class: "ap-input" });
  [
    ["", "全部"],
    ["pending", "待推送"],
    ["pushed", "已推送"],
    ["failed", "推送失败"],
    ["not_ready", "制作中"],
  ].forEach(([v, t]) => {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = t;
    if (v === state.status) opt.selected = true;
    statusSelect.appendChild(opt);
  });
  statusGroup.appendChild(statusSelect);

  const langGroup = el("label", { class: "ap-input-group" }, [
    el("span", { class: "ap-input-label" }, "语种（可用逗号分隔多个）"),
  ]);
  const langInput = el("input", { class: "ap-input", type: "text", placeholder: "de,fr,ja …" });
  langInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  langGroup.appendChild(langInput);

  const btnSearch = el("button", { type: "button", class: "ap-btn-primary" }, "查询");
  btnSearch.addEventListener("click", doSearch);
  const btnReload = el("button", { type: "button", class: "ap-btn-ghost" }, "刷新");
  btnReload.addEventListener("click", () => { load(); });

  toolbar.appendChild(qGroup);
  toolbar.appendChild(statusGroup);
  toolbar.appendChild(langGroup);
  toolbar.appendChild(btnSearch);
  toolbar.appendChild(btnReload);

  // ---- 表格 ----
  const tableBox = el("div", { class: "ap-card" });
  const table = el("table", { class: "ap-table" });
  const thead = el("thead", {}, [
    el("tr", {}, [
      el("th", { style: "width: 120px;" }, "封面"),
      el("th", {}, "产品 / 素材"),
      el("th", { style: "width: 70px;" }, "语种"),
      el("th", { style: "width: 100px;" }, "状态"),
      el("th", { style: "width: 140px;" }, "时间"),
      el("th", { style: "width: 140px;" }, "操作"),
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
    state.status = statusSelect.value;
    state.lang = langInput.value.trim();
    state.page = 1;
    load();
  }

  async function load() {
    state.loading = true;
    errBanner.hidden = true;
    clear(tbody);
    tbody.appendChild(el("tr", {}, [el("td", { colspan: 6, class: "ap-empty" }, "加载中…")]));

    try {
      const data = await api.pushItems({
        page: state.page, pageSize: state.pageSize,
        q: state.q, status: state.status, lang: state.lang,
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

  function renderBody() {
    clear(tbody);
    if (state.items.length === 0 && !state.error) {
      tbody.appendChild(el("tr", {}, [el("td", { colspan: 6, class: "ap-empty" }, "暂无数据")]));
      return;
    }
    state.items.forEach((item) => {
      const row = el("tr");

      // 封面
      const coverTd = el("td");
      if (item.cover_url) {
        coverTd.appendChild(el("img", {
          class: "ap-list-thumb", src: item.cover_url, alt: item.filename || "",
        }));
      } else {
        coverTd.appendChild(el("div", { class: "ap-list-thumb ap-media-empty" }, "无"));
      }
      row.appendChild(coverTd);

      // 产品 / 素材
      const nameTd = el("td");
      nameTd.appendChild(el("div", { class: "ap-row-name" }, item.product_name || ""));
      nameTd.appendChild(el("div", { class: "ap-code" }, item.product_code || ""));
      nameTd.appendChild(el("div", { class: "ap-row-sub" }, item.display_name || item.filename || ""));
      row.appendChild(nameTd);

      // 语种
      row.appendChild(el("td", {}, [el("span", { class: "ap-langpill" }, item.lang || "en")]));

      // 状态
      const statusTd = el("td");
      statusTd.appendChild(renderStatusBadge(item.status));
      row.appendChild(statusTd);

      // 时间（pushed_at 优先，其次 created_at）
      const timeTxt = (item.pushed_at || item.created_at || "")
        .replace("T", " ").slice(0, 16);
      row.appendChild(el("td", { class: "ap-row-time" }, timeTxt || "—"));

      // 操作
      const actionTd = el("td");
      actionTd.appendChild(buildActionButton(item));
      row.appendChild(actionTd);

      tbody.appendChild(row);
    });
  }

  function buildActionButton(item) {
    if (item.status === "not_ready") {
      const missing = Object.entries(item.readiness || {})
        .filter(([, v]) => !v)
        .map(([k]) => ({
          has_object: "素材", has_cover: "封面",
          has_copywriting: "文案", lang_supported: "链接适配",
        })[k] || k)
        .join(" / ");
      return el("button", {
        type: "button", class: "ap-btn-ghost", disabled: true,
        title: `缺少：${missing}`,
      }, "制作中");
    }
    if (item.status === "pushed") {
      return el("button", {
        type: "button", class: "ap-btn-ghost",
        onclick: () => openPayloadFor(item),
      }, "查看/重推");
    }
    // pending / failed：可推送
    const label = item.status === "failed" ? "重推" : "去推送";
    return el("button", {
      type: "button", class: "ap-btn-primary",
      onclick: () => openPayloadFor(item),
    }, label);
  }

  function openPayloadFor(item) {
    openPushModal(item, { onPushed: () => load() });
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
 * 视图 3：推送载荷
 * ================================================================ */

function renderPayload(container) {
  const prefill = window.__AP_PREFILL_PAYLOAD__ || {};
  window.__AP_PREFILL_PAYLOAD__ = null;

  const state = {
    productCode: prefill.code || "",
    lang: prefill.lang || "de",
    itemId: prefill.item_id || null,
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
      const body = state.itemId
        ? await api.pushItem(state.itemId, state.payloadData)
        : await api.push(state.payloadData);
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
    Object.assign(runtimeConfig, cfg || {});
    const hint = document.getElementById("ap-config-hint");
    if (hint) {
      hint.textContent = `upstream=${cfg.autovideoBaseUrl} · target=${cfg.pushMediasTarget}`;
    }
  } catch (_) { /* 配置读不到不阻塞界面 */ }
  activate("list");
}

init();
