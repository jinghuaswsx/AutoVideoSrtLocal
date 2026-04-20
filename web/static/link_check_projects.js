(function () {
  const DEFAULT_TARGET_LANGUAGE = "en";
  const state = {
    enabledLanguages: new Set(),
    manuallySelectedLanguage: false,
    isSubmitting: false,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(text) {
    const node = $("linkCheckStatus");
    if (node) {
      node.textContent = text;
    }
  }

  function showError(message) {
    const node = $("linkCheckError");
    if (!node) {
      return;
    }
    if (!message) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = message;
  }

  function setLanguageHint(text) {
    const node = $("linkCheckLanguageHint");
    if (node) {
      node.textContent = text;
    }
  }

  function setSubmitting(isSubmitting) {
    state.isSubmitting = isSubmitting;
    const submitButton = $("linkCheckSubmit");
    if (!submitButton) {
      return;
    }
    submitButton.disabled = isSubmitting;
    submitButton.classList.toggle("is-loading", isSubmitting);
    submitButton.setAttribute("aria-busy", isSubmitting ? "true" : "false");
    submitButton.textContent = isSubmitting ? "创建中..." : "创建项目";
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      throw new Error(payload.error || "请求失败");
    }
    return payload;
  }

  function normalizeLocaleSegment(segment) {
    return String(segment || "").trim().toLowerCase();
  }

  function detectTargetLanguageFromUrl(url, enabledLanguages) {
    if (!url) {
      return "";
    }

    try {
      const parsed = new URL(url);
      const segments = parsed.pathname
        .split("/")
        .map(normalizeLocaleSegment)
        .filter(Boolean);
      const stopIndex = segments.indexOf("products");
      const candidates = stopIndex >= 0 ? segments.slice(0, stopIndex) : segments;

      for (const segment of candidates) {
        if (enabledLanguages.has(segment)) {
          return segment;
        }
        if (segment.includes("-")) {
          const primary = segment.split("-", 1)[0];
          if (enabledLanguages.has(primary)) {
            return primary;
          }
        }
      }
    } catch (_error) {
      return "";
    }

    return "";
  }

  function applyDetectedLanguage(languageCode) {
    const select = $("targetLanguage");
    if (!select || !languageCode) {
      return false;
    }
    const option = Array.from(select.options).find(function (item) {
      return item.value === languageCode;
    });
    if (!option) {
      return false;
    }

    select.value = languageCode;
    select.dataset.autoDetected = "true";
    setLanguageHint(`已根据链接自动识别为 ${option.textContent}。`);
    return true;
  }

  function applyDefaultLanguage() {
    const select = $("targetLanguage");
    if (!select || !state.enabledLanguages.has(DEFAULT_TARGET_LANGUAGE)) {
      return false;
    }
    const option = Array.from(select.options).find(function (item) {
      return item.value === DEFAULT_TARGET_LANGUAGE;
    });
    if (!option) {
      return false;
    }

    select.value = DEFAULT_TARGET_LANGUAGE;
    select.dataset.autoDetected = "false";
    return true;
  }

  function syncLanguageFromUrl() {
    const linkInput = $("linkUrl");
    const select = $("targetLanguage");
    if (!linkInput || !select) {
      return;
    }

    if (state.manuallySelectedLanguage && select.value) {
      return;
    }

    const detected = detectTargetLanguageFromUrl(linkInput.value, state.enabledLanguages);
    if (!detected) {
      if (applyDefaultLanguage()) {
        setLanguageHint("未从链接识别到语言，已默认选择英语，可手动覆盖。");
        return;
      }
      if (!select.value || select.dataset.autoDetected === "true") {
        select.value = "";
        select.dataset.autoDetected = "false";
      }
      setLanguageHint("未从链接识别到语言，可手动选择。");
      return;
    }

    if (applyDetectedLanguage(detected)) {
      state.manuallySelectedLanguage = false;
    }
  }

  async function loadLanguages() {
    const data = await fetchJSON("/medias/api/languages");
    const select = $("targetLanguage");
    if (!select) {
      return;
    }

    select.innerHTML = '<option value="">自动识别或手动选择</option>';
    state.enabledLanguages = new Set();
    for (const item of data.items || []) {
      const code = normalizeLocaleSegment(item.code);
      if (!code) {
        continue;
      }
      state.enabledLanguages.add(code);
      const option = document.createElement("option");
      option.value = code;
      option.textContent = item.name_zh || code;
      select.appendChild(option);
    }

    applyDefaultLanguage();
    syncLanguageFromUrl();
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (state.isSubmitting) {
      return;
    }

    const form = $("linkCheckProjectForm");
    if (!form) {
      return;
    }

    showError("");
    setSubmitting(true);
    setStatus("正在创建项目...");

    try {
      const payload = await fetchJSON("/api/link-check/tasks", {
        method: "POST",
        body: new FormData(form),
      });
      setStatus("创建成功，正在跳转详情页...");
      window.location.assign(payload.detail_url);
    } catch (error) {
      showError(error.message || "创建项目失败");
      setStatus("创建失败");
      setSubmitting(false);
    }
  }

  function onLanguageChange() {
    const select = $("targetLanguage");
    if (!select) {
      return;
    }
    state.manuallySelectedLanguage = Boolean(select.value);
    select.dataset.autoDetected = "false";
    if (select.value) {
      setLanguageHint("已手动选择目标语言，后续不会再自动覆盖。");
    } else {
      state.manuallySelectedLanguage = false;
      syncLanguageFromUrl();
    }
  }

  function bindEvents() {
    const form = $("linkCheckProjectForm");
    const linkInput = $("linkUrl");
    const select = $("targetLanguage");

    if (form) {
      form.addEventListener("submit", onSubmit);
    }
    if (linkInput) {
      linkInput.addEventListener("input", syncLanguageFromUrl);
      linkInput.addEventListener("blur", syncLanguageFromUrl);
    }
    if (select) {
      select.addEventListener("change", onLanguageChange);
    }
  }

  document.addEventListener("DOMContentLoaded", async function () {
    bindEvents();

    try {
      await loadLanguages();
      setStatus("等待创建");
    } catch (error) {
      showError(error.message || "语言列表加载失败");
      setStatus("初始化失败");
    }
  });

  window.detectTargetLanguageFromUrl = detectTargetLanguageFromUrl;

  // Task 1 locale detection examples: /fr/, /fr-fr/, /en-de/
})();
