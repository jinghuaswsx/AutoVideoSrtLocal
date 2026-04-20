/*
 * 推送管理 - push-module 直连模式。
 *
 * 来源：push-module/frontend/api/materials.js 与两个 JSX 组件的原生 JS 改写。
 * 不经过本项目后端，浏览器直接 fetch：
 *   - AutoVideo OpenAPI  (CFG.autovideoBaseUrl)
 *   - 下游推送服务        (CFG.pushMediasTarget)
 *
 * 依赖 window.PUSH_DIRECT_CONFIG (由 pushes_list.html 注入)。
 */

const CFG = window.PUSH_DIRECT_CONFIG || {};

/* ---------- 工具 ---------- */

function asText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function createEl(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) el.setAttribute(k, "");
    else if (v === false || v === null || v === undefined) continue;
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}

/* ---------- AutoVideo 上游：素材 / 推送载荷 ---------- */

function normalizeMaterialsResponse(raw) {
  const product = raw?.product ?? {};
  const covers = raw?.covers ?? {};
  const copywritings = raw?.copywritings ?? {};
  const items = raw?.items ?? [];
  return {
    product: {
      id: product.id ?? null,
      productCode: asText(product.product_code),
      name: asText(product.name),
      archived: Boolean(product.archived),
      createdAt: product.created_at ?? null,
      updatedAt: product.updated_at ?? null,
    },
    covers: Object.entries(covers).map(([lang, cover]) => ({
      lang,
      objectKey: asText(cover?.object_key),
      downloadUrl: asText(cover?.download_url),
      expiresIn: cover?.expires_in ?? raw?.expires_in ?? null,
    })),
    copywritings: Object.fromEntries(
      Object.entries(copywritings).map(([lang, list]) => [
        lang,
        (list ?? []).map((item, index) => ({
          id: `${lang}-${index}`,
          title: asText(item?.title),
          body: asText(item?.body),
          description: asText(item?.description),
          adCarrier: asText(item?.ad_carrier),
          adCopy: asText(item?.ad_copy),
          adKeywords: asText(item?.ad_keywords),
        })),
      ]),
    ),
    items: items.map((item) => ({
      id: item?.id ?? null,
      lang: asText(item?.lang),
      filename: asText(item?.filename),
      displayName: asText(item?.display_name || item?.filename),
      objectKey: asText(item?.object_key),
      videoDownloadUrl: asText(item?.video_download_url),
      coverObjectKey: asText(item?.cover_object_key),
      videoCoverDownloadUrl: asText(item?.video_cover_download_url),
      durationSeconds: item?.duration_seconds ?? 0,
      fileSize: item?.file_size ?? 0,
      createdAt: item?.created_at ?? null,
    })),
    expiresIn: raw?.expires_in ?? null,
  };
}

function mapUpstreamError(status) {
  if (status === 401) return "接口认证失败，请检查 API Key";
  if (status === 404) return "未找到该产品，请确认 product_code";
  return "查询失败，请稍后重试";
}

async function requestUpstream(url) {
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: { "X-API-Key": CFG.autovideoApiKey || "" },
    });
  } catch (networkError) {
    const error = new Error(
      "网络请求失败：" + (networkError.message ?? "未知错误") +
      "（可能是 CORS 未放行或地址不可达）",
    );
    error.cause = networkError;
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || mapUpstreamError(response.status));
    error.status = response.status;
    error.detail = payload.error ?? "";
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function fetchMaterials(productCode) {
  const url = `${CFG.autovideoBaseUrl}/openapi/materials/${encodeURIComponent(productCode)}`;
  const raw = await requestUpstream(url);
  return normalizeMaterialsResponse(raw);
}

async function fetchPushPayload(productCode, lang) {
  const url =
    `${CFG.autovideoBaseUrl}/openapi/materials/${encodeURIComponent(productCode)}/push-payload` +
    `?lang=${encodeURIComponent(lang)}`;
  return requestUpstream(url);
}

async function pushMedias(payload) {
  let response;
  try {
    response = await fetch(CFG.pushMediasTarget, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (networkError) {
    const error = new Error(
      "推送服务不可达：" + (networkError.message ?? "未知错误") +
      "（可能是 CORS 未放行或地址不可达）",
    );
    error.cause = networkError;
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.message || "推送失败");
    error.status = response.status;
    error.detail = body.detail ?? body ?? "";
    error.payload = body;
    throw error;
  }
  return body;
}

/* ---------- 载荷校验 ---------- */

function validatePayload(payload) {
  const errors = [];
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return ["payload 为空或不是对象"];
  }
  const isStr = (v) => typeof v === "string";
  const isNum = (v) => typeof v === "number" && !Number.isNaN(v);
  const isArr = Array.isArray;

  const check = (key, ok, typeName) => {
    if (!ok(payload[key])) errors.push(`字段 ${key} 必须是 ${typeName}`);
  };
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

  if (isArr(payload.texts)) {
    payload.texts.forEach((t, i) => {
      if (!t || typeof t !== "object") {
        errors.push(`texts[${i}] 不是对象`);
        return;
      }
      ["title", "message", "description"].forEach((k) => {
        if (!isStr(t[k])) errors.push(`texts[${i}].${k} 必须是 string`);
      });
    });
  }
  if (isArr(payload.product_links)) {
    payload.product_links.forEach((l, i) => {
      if (!isStr(l)) errors.push(`product_links[${i}] 必须是 string`);
    });
  }
  if (isArr(payload.platforms)) {
    payload.platforms.forEach((p, i) => {
      if (!isStr(p)) errors.push(`platforms[${i}] 必须是 string`);
    });
  }
  if (isArr(payload.videos)) {
    payload.videos.forEach((v, i) => {
      if (!v || typeof v !== "object") {
        errors.push(`videos[${i}] 不是对象`);
        return;
      }
      ["name", "url", "image_url"].forEach((k) => {
        if (!isStr(v[k])) errors.push(`videos[${i}].${k} 必须是 string`);
      });
      ["size", "width", "height"].forEach((k) => {
        if (!isNum(v[k])) errors.push(`videos[${i}].${k} 必须是 number`);
      });
    });
  }
  return errors;
}

/* ================================================================
 * 推送载荷渲染器（对应 push-module/frontend/components/PushPayload.jsx）
 * ================================================================ */

function renderPushPayload(container) {
  const state = {
    productCode: "3d-curved-screen-magnifier-for-smartphones",
    lang: "de",
    fetching: false,
    errorMessage: "",
    responseText: "",
    videos: [],
    payloadData: null,
    pushing: false,
    pushError: "",
    pushResult: "",
  };

  container.innerHTML = "";
  const root = createEl("section", { class: "push-form-card" });
  container.appendChild(root);

  const inputs = createEl("div", { class: "push-form-grid" });
  const codeGroup = createEl("label", { class: "push-input-group" }, [
    createEl("span", { class: "push-input-label" }, "product_code"),
  ]);
  const codeInput = createEl("input", {
    class: "push-text-input", type: "text", value: state.productCode,
    placeholder: "例如：3d-curved-screen-magnifier-for-smartphones",
  });
  codeInput.addEventListener("input", (e) => { state.productCode = e.target.value; });
  codeGroup.appendChild(codeInput);

  const langGroup = createEl("label", { class: "push-input-group" }, [
    createEl("span", { class: "push-input-label" }, "lang（de/fr/es/it/ja/pt 等）"),
  ]);
  const langInput = createEl("input", {
    class: "push-text-input", type: "text", value: state.lang,
    placeholder: "例如：de",
  });
  langInput.addEventListener("input", (e) => { state.lang = e.target.value; });
  langGroup.appendChild(langInput);

  inputs.appendChild(codeGroup);
  inputs.appendChild(langGroup);
  root.appendChild(inputs);

  const btnRow = createEl("div", {
    class: "push-array-row",
    style: "margin-top: 16px; gap: 12px;",
  });
  const btnFetch = createEl("button", { type: "button", class: "push-btn-primary" }, "加载数据");
  const btnPush = createEl("button", {
    type: "button", class: "push-btn-success", disabled: true,
  }, "推送");
  btnRow.appendChild(btnFetch);
  btnRow.appendChild(btnPush);
  root.appendChild(btnRow);

  const errBanner = createEl("p", { class: "push-error-banner", hidden: true });
  root.appendChild(errBanner);

  const respGroup = createEl("label", {
    class: "push-input-group", style: "margin-top: 16px;",
  }, [createEl("span", { class: "push-input-label" }, "返回报文（JSON）")]);
  const respText = createEl("textarea", {
    class: "push-text-area push-response-text", readonly: true,
    placeholder: "点击“加载数据”后，这里会显示上游返回的完整 JSON 报文",
  });
  respGroup.appendChild(respText);
  root.appendChild(respGroup);

  const pushErrBanner = createEl("pre", {
    class: "push-error-banner", style: "white-space: pre-wrap;", hidden: true,
  });
  root.appendChild(pushErrBanner);

  const pushResultGroup = createEl("label", {
    class: "push-input-group", style: "margin-top: 16px;", hidden: true,
  }, [createEl("span", { class: "push-input-label" }, "推送响应")]);
  const pushResultText = createEl("textarea", {
    class: "push-text-area push-response-text", readonly: true,
  });
  pushResultGroup.appendChild(pushResultText);
  root.appendChild(pushResultGroup);

  const mediaList = createEl("div", { class: "push-media-list", hidden: true });
  root.appendChild(mediaList);

  function syncUI() {
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

    mediaList.innerHTML = "";
    mediaList.hidden = state.videos.length === 0;
    state.videos.forEach((video, i) => {
      const row = createEl("div", { class: "push-media-row" });

      const coverItem = createEl("div", { class: "push-media-item" }, [
        createEl("span", { class: "push-input-label" }, `videos[${i}].image_url`),
      ]);
      if (video.image_url) {
        coverItem.appendChild(createEl("img", {
          class: "push-media-frame", src: video.image_url,
          alt: video.name ?? `cover-${i}`,
        }));
      } else {
        coverItem.appendChild(createEl("div", {
          class: "push-media-frame push-media-empty",
        }, "无封面"));
      }

      const videoItem = createEl("div", { class: "push-media-item" }, [
        createEl("span", { class: "push-input-label" }, `videos[${i}].url`),
      ]);
      if (video.url) {
        videoItem.appendChild(createEl("video", {
          class: "push-media-frame", src: video.url,
          poster: video.image_url || null,
          controls: true, preload: "metadata",
        }));
      } else {
        videoItem.appendChild(createEl("div", {
          class: "push-media-frame push-media-empty",
        }, "无视频"));
      }

      row.appendChild(coverItem);
      row.appendChild(videoItem);
      mediaList.appendChild(row);
    });
  }

  async function handleFetch() {
    const code = state.productCode.trim();
    const lang = state.lang.trim();
    if (!code) { state.errorMessage = "请输入 product_code"; syncUI(); return; }
    if (!lang) { state.errorMessage = "请输入 lang"; syncUI(); return; }

    Object.assign(state, {
      fetching: true, errorMessage: "", responseText: "",
      videos: [], payloadData: null, pushError: "", pushResult: "",
    });
    syncUI();
    try {
      const payload = await fetchPushPayload(code, lang);
      state.responseText = JSON.stringify(payload, null, 2);
      state.videos = Array.isArray(payload?.videos) ? payload.videos : [];
      state.payloadData = payload;
    } catch (error) {
      state.errorMessage = error.message ?? "查询失败，请稍后重试";
      const errPayload = error.payload ?? {
        message: error.message, detail: error.detail, status: error.status,
      };
      state.responseText = JSON.stringify(errPayload, null, 2);
    } finally {
      state.fetching = false;
      syncUI();
    }
  }

  async function handlePush() {
    if (!state.payloadData) {
      state.pushError = "请先点击“加载数据”获取到有效报文再推送";
      state.pushResult = ""; syncUI(); return;
    }
    const errors = validatePayload(state.payloadData);
    if (errors.length > 0) {
      state.pushError = "数据格式校验失败：\n- " + errors.join("\n- ");
      state.pushResult = ""; syncUI(); return;
    }
    Object.assign(state, { pushing: true, pushError: "", pushResult: "" });
    syncUI();
    try {
      const body = await pushMedias(state.payloadData);
      state.pushResult = JSON.stringify(body, null, 2);
    } catch (error) {
      state.pushError = error.message ?? "推送失败";
      const errPayload = error.payload ?? {
        message: error.message, detail: error.detail, status: error.status,
      };
      state.pushResult = JSON.stringify(errPayload, null, 2);
    } finally {
      state.pushing = false;
      syncUI();
    }
  }

  btnFetch.addEventListener("click", handleFetch);
  btnPush.addEventListener("click", handlePush);

  syncUI();
}

/* ================================================================
 * 推送创建渲染器（对应 push-module/frontend/components/PushCreate.jsx）
 * 纯展示 + JSON 预览，无推送按钮。
 * ================================================================ */

function renderPushCreate(container) {
  const defaultForm = {
    mode: "create",
    product_name: "液体慢喂狗碗",
    texts: [
      {
        title: "🐾 Too cold for long walks? Keep them busy indoors.",
        message: "Winter days mean less time outside, but your dog still has energy to burn.",
        description: "Shop Now & Beat Winter Boredom",
      },
    ],
    product_links: [""],
    videos: [
      {
        name: "sample.mp4",
        size: "20539247",
        width: "1440",
        height: "2560",
        url: "",
        image_url: "",
      },
    ],
    source: "0",
    level: "3",
    author: "李文龙",
    push_admin: "陈绍坤",
    roas: "1.55",
    platforms: ["shop"],
    selling_point: "",
    tags: [],
  };
  const emptyText = { title: "", message: "", description: "" };
  const emptyVideo = { name: "", size: "", width: "", height: "", url: "", image_url: "" };

  const state = {
    form: JSON.parse(JSON.stringify(defaultForm)),
    productCode: "",
    fetching: false,
    fetchError: "",
    fetchInfo: "",
    responseText: "",
  };

  container.innerHTML = "";

  const queryCard = createEl("section", { class: "push-form-card" });
  const queryRow = createEl("div", { class: "push-query-row" });
  const codeInput = createEl("input", {
    class: "push-text-input", type: "text",
    placeholder: "输入产品 ID / product_code",
  });
  codeInput.addEventListener("input", (e) => { state.productCode = e.target.value; });
  const btnFetch = createEl("button", { type: "button", class: "push-btn-primary" }, "获取");
  btnFetch.addEventListener("click", handleFetch);
  queryRow.appendChild(codeInput);
  queryRow.appendChild(btnFetch);
  queryCard.appendChild(queryRow);
  const fetchErr = createEl("p", { class: "push-error-banner", hidden: true });
  const fetchInfo = createEl("p", { class: "push-info-banner", hidden: true });
  queryCard.appendChild(fetchErr);
  queryCard.appendChild(fetchInfo);
  const respGroup = createEl("label", {
    class: "push-input-group", style: "margin-top: 16px;",
  }, [createEl("span", { class: "push-input-label" }, "返回报文（JSON）")]);
  const respText = createEl("textarea", {
    class: "push-text-area push-response-text", readonly: true,
    placeholder: "点击“获取”后，这里会显示上游返回的完整 JSON 报文",
  });
  respGroup.appendChild(respText);
  queryCard.appendChild(respGroup);
  container.appendChild(queryCard);

  const basicCard = buildBasicCard();
  const textsCard = buildArrayObjectCard("texts", emptyText, renderTextItem);
  const linksCard = buildArrayStringCard("product_links");
  const videosCard = buildArrayObjectCard("videos", emptyVideo, renderVideoItem);
  const platformsCard = buildArrayStringCard("platforms");
  const tagsCard = buildArrayStringCard("tags");
  const jsonCard = createEl("section", { class: "push-form-card" }, [
    createEl("h3", {}, "JSON 预览"),
  ]);
  const jsonPre = createEl("pre", { class: "push-json-preview" });
  jsonCard.appendChild(jsonPre);
  container.appendChild(basicCard);
  container.appendChild(textsCard);
  container.appendChild(linksCard);
  container.appendChild(videosCard);
  container.appendChild(platformsCard);
  container.appendChild(tagsCard);
  container.appendChild(jsonCard);

  // 跟踪 basic 卡片里的输入框，便于 handleFetch 回填 product_name
  const basicInputs = {};

  function buildBasicCard() {
    const card = createEl("section", { class: "push-form-card" }, [
      createEl("h3", {}, "基本信息"),
    ]);
    const grid = createEl("div", { class: "push-form-grid" });
    ["mode", "product_name", "source", "level", "author", "push_admin", "roas", "selling_point"]
      .forEach((key) => {
        const group = createEl("label", { class: "push-input-group" }, [
          createEl("span", { class: "push-input-label" }, key),
        ]);
        const input = createEl("input", { class: "push-text-input", type: "text" });
        input.value = state.form[key] ?? "";
        input.addEventListener("input", (e) => {
          state.form[key] = e.target.value;
          refreshJson();
        });
        basicInputs[key] = input;
        group.appendChild(input);
        grid.appendChild(group);
      });
    card.appendChild(grid);
    return card;
  }

  function buildArrayObjectCard(key, template, itemRenderer) {
    const card = createEl("section", { class: "push-form-card" });
    const header = createEl("div", { class: "push-array-header" }, [
      createEl("h3", {}, key),
      createEl("button", {
        type: "button", class: "push-btn-ghost",
        onclick: () => {
          state.form[key] = [...state.form[key], JSON.parse(JSON.stringify(template))];
          rerenderArrayCard(card, key, itemRenderer);
          refreshJson();
        },
      }, "添加"),
    ]);
    card.appendChild(header);
    card.appendChild(createEl("div", { dataset: { list: "1" } }));
    rerenderArrayCard(card, key, itemRenderer);
    return card;
  }

  function rerenderArrayCard(card, key, itemRenderer) {
    const list = card.querySelector("[data-list]");
    list.innerHTML = "";
    const items = state.form[key] || [];
    if (items.length === 0) {
      list.appendChild(createEl("p", { class: "push-empty-state" }, "（空）"));
      return;
    }
    items.forEach((_item, index) => {
      const wrapper = createEl("div", { class: "push-array-item" });
      const head = createEl("div", { class: "push-array-item-header" }, [
        createEl("span", { class: "push-array-item-title" }, `${key}[${index}]`),
        createEl("button", {
          type: "button", class: "push-btn-ghost",
          onclick: () => {
            state.form[key] = state.form[key].filter((_, i) => i !== index);
            rerenderArrayCard(card, key, itemRenderer);
            refreshJson();
          },
        }, "删除"),
      ]);
      wrapper.appendChild(head);
      itemRenderer(wrapper, key, index);
      list.appendChild(wrapper);
    });
  }

  function renderTextItem(wrapper, key, index) {
    ["title", "message", "description"].forEach((field) => {
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, field),
      ]);
      const ta = createEl("textarea", { class: "push-text-area short" });
      ta.value = state.form[key][index][field] ?? "";
      ta.addEventListener("input", (e) => {
        state.form[key][index][field] = e.target.value;
        refreshJson();
      });
      group.appendChild(ta);
      wrapper.appendChild(group);
    });
  }

  function renderVideoItem(wrapper, key, index) {
    const grid = createEl("div", { class: "push-form-grid" });
    ["name", "size", "width", "height"].forEach((field) => {
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, field),
      ]);
      const input = createEl("input", { class: "push-text-input", type: "text" });
      input.value = state.form[key][index][field] ?? "";
      input.addEventListener("input", (e) => {
        state.form[key][index][field] = e.target.value;
        refreshJson();
      });
      group.appendChild(input);
      grid.appendChild(group);
    });
    wrapper.appendChild(grid);
    ["url", "image_url"].forEach((field) => {
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, field),
      ]);
      const ta = createEl("textarea", { class: "push-text-area short" });
      ta.value = state.form[key][index][field] ?? "";
      ta.addEventListener("input", (e) => {
        state.form[key][index][field] = e.target.value;
        refreshJson();
      });
      group.appendChild(ta);
      wrapper.appendChild(group);
    });
  }

  function buildArrayStringCard(key) {
    const card = createEl("section", { class: "push-form-card" });
    const header = createEl("div", { class: "push-array-header" }, [
      createEl("h3", {}, key),
      createEl("button", {
        type: "button", class: "push-btn-ghost",
        onclick: () => {
          state.form[key] = [...state.form[key], ""];
          rerenderStringArrayCard(card, key);
          refreshJson();
        },
      }, "添加"),
    ]);
    card.appendChild(header);
    card.appendChild(createEl("div", { dataset: { list: "1" } }));
    rerenderStringArrayCard(card, key);
    return card;
  }

  function rerenderStringArrayCard(card, key) {
    const list = card.querySelector("[data-list]");
    list.innerHTML = "";
    const items = state.form[key] || [];
    if (items.length === 0) {
      list.appendChild(createEl("p", { class: "push-empty-state" }, "（空）"));
      return;
    }
    items.forEach((value, index) => {
      const row = createEl("div", { class: "push-array-row" });
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, `${key}[${index}]`),
      ]);
      const input = createEl("input", { class: "push-text-input", type: "text" });
      input.value = value ?? "";
      input.addEventListener("input", (e) => {
        state.form[key][index] = e.target.value;
        refreshJson();
      });
      group.appendChild(input);
      row.appendChild(group);
      row.appendChild(createEl("button", {
        type: "button", class: "push-btn-ghost",
        onclick: () => {
          state.form[key] = state.form[key].filter((_, i) => i !== index);
          rerenderStringArrayCard(card, key);
          refreshJson();
        },
      }, "删除"));
      list.appendChild(row);
    });
  }

  function refreshJson() {
    jsonPre.textContent = JSON.stringify(state.form, null, 2);
  }

  function syncFetchBanners() {
    fetchErr.hidden = !state.fetchError;
    fetchErr.textContent = state.fetchError;
    fetchInfo.hidden = !state.fetchInfo;
    fetchInfo.textContent = state.fetchInfo;
    respText.value = state.responseText;
    btnFetch.disabled = state.fetching;
    btnFetch.textContent = state.fetching ? "获取中..." : "获取";
    codeInput.disabled = state.fetching;
  }

  async function handleFetch() {
    const trimmed = state.productCode.trim();
    if (!trimmed) {
      state.fetchError = "请输入 product_code";
      state.fetchInfo = "";
      syncFetchBanners();
      return;
    }
    Object.assign(state, {
      fetching: true, fetchError: "", fetchInfo: "", responseText: "",
    });
    syncFetchBanners();
    try {
      const payload = await fetchMaterials(trimmed);
      const name = payload?.product?.name ?? "";
      if (name) {
        state.form.product_name = name;
        if (basicInputs.product_name) basicInputs.product_name.value = name;
        refreshJson();
      }
      state.fetchInfo =
        `已获取：${name || "未命名产品"}（product_code: ${payload?.product?.productCode ?? trimmed}）`;
      state.responseText = JSON.stringify(payload, null, 2);
    } catch (error) {
      state.fetchError = error.message ?? "查询失败，请稍后重试";
      const errPayload = error.payload ?? {
        message: error.message, detail: error.detail, status: error.status,
      };
      state.responseText = JSON.stringify(errPayload, null, 2);
    } finally {
      state.fetching = false;
      syncFetchBanners();
    }
  }

  refreshJson();
}

/* ---------- tab 切换 ---------- */

function initTabs() {
  const tabs = document.querySelectorAll(".push-tab");
  const panels = document.querySelectorAll(".push-tab-panel");
  if (!tabs.length) return;

  let createInit = false;
  let payloadInit = false;

  function activate(name) {
    tabs.forEach((t) => {
      const active = t.dataset.tab === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach((p) => {
      p.hidden = p.dataset.panel !== name;
    });
    if (name === "create" && !createInit) {
      renderPushCreate(document.getElementById("push-create-root"));
      createInit = true;
    }
    if (name === "payload" && !payloadInit) {
      renderPushPayload(document.getElementById("push-payload-root"));
      payloadInit = true;
    }
  }

  tabs.forEach((t) => {
    t.addEventListener("click", () => activate(t.dataset.tab));
  });
}

initTabs();
