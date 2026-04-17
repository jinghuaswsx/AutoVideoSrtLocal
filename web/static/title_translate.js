(function () {
  "use strict";

  var SOURCE_FORMAT_ERROR = "请按“标题/文案/描述”三行格式输入，每行使用英文冒号，例如 `标题: ...`。";

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function requestJson(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.credentials = options.credentials || "same-origin";
    if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
      if (!options.headers["Content-Type"] && !(options.headers instanceof Headers)) {
        options.headers["Content-Type"] = "application/json";
      }
      options.body = JSON.stringify(options.body);
    }

    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (err) {
          data = null;
        }
        if (!response.ok) {
          var error = new Error((data && data.error) || ("HTTP " + response.status));
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  function fallbackCopy(text) {
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "readonly");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } catch (err) {
      // ignore
    }
    document.body.removeChild(textarea);
    return Promise.resolve();
  }

  function validateSourceText(rawText) {
    var text = String(rawText || "").replace(/\r\n?/g, "\n").trim();
    if (!text) {
      return { ok: false, message: SOURCE_FORMAT_ERROR };
    }

    var lines = text.split("\n");
    if (lines.length !== 3) {
      return { ok: false, message: SOURCE_FORMAT_ERROR };
    }

    var labels = ["标题", "文案", "描述"];
    var normalized = [];

    for (var i = 0; i < labels.length; i += 1) {
      var label = labels[i];
      var line = lines[i].trim();
      var match = line.match(new RegExp("^" + label + ":\\s*(.+)$"));
      if (!match) {
        return { ok: false, message: SOURCE_FORMAT_ERROR };
      }

      var value = (match[1] || "").trim();
      if (!value) {
        return { ok: false, message: SOURCE_FORMAT_ERROR };
      }

      normalized.push(label + ": " + value);
    }

    return {
      ok: true,
      value: normalized.join("\n"),
      lines: normalized,
    };
  }

  function createWorkbench(root) {
    var state = {
      languages: [],
      selectedLanguageCode: "",
      currentResultText: "",
      resultMode: "empty",
      busy: false,
      initialized: false,
    };

    var urls = {
      languages: root.getAttribute("data-languages-url"),
      translate: root.getAttribute("data-translate-url"),
    };

    var pillsEl = $("#titleTranslateLangPills", root);
    var sourceEl = $("#titleTranslateSource", root);
    var sourceErrorEl = $("#titleTranslateSourceError", root);
    var translateBtn = $("#titleTranslateTranslateBtn", root);
    var resultEl = $("#titleTranslateResult", root);

    function setSourceError(message) {
      if (!sourceErrorEl) return;
      sourceErrorEl.textContent = message || "";
    }

    function setBusy(busy) {
      state.busy = !!busy;
      if (translateBtn) {
        translateBtn.disabled = state.busy;
        translateBtn.textContent = state.busy ? "翻译中..." : "翻译";
      }
      if (pillsEl) {
        var pills = pillsEl.querySelectorAll(".title-translate-pill");
        Array.prototype.forEach.call(pills, function (pill) {
          pill.classList.toggle("is-disabled", state.busy);
          pill.disabled = state.busy;
        });
      }
    }

    function setControlsDisabled(disabled) {
      if (translateBtn) {
        translateBtn.disabled = !!disabled;
        if (!disabled && !state.busy) {
          translateBtn.textContent = "翻译";
        }
      }
      if (pillsEl) {
        var pills = pillsEl.querySelectorAll(".title-translate-pill");
        Array.prototype.forEach.call(pills, function (pill) {
          pill.disabled = !!disabled;
          pill.classList.toggle("is-disabled", !!disabled);
        });
      }
    }

    function setResultState(mode, payload) {
      state.resultMode = mode;
      if (!resultEl) return;
      resultEl.setAttribute("data-state", mode);

      if (mode === "empty") {
        resultEl.innerHTML = '<div class="title-translate-result-empty">结果将显示在这里</div>';
        return;
      }

      if (mode === "loading") {
        resultEl.innerHTML = (
          '<div class="title-translate-state title-translate-state--loading">' +
          '正在使用 Claude Sonnet 翻译…' +
          '</div>'
        );
        return;
      }

      if (mode === "error") {
        resultEl.innerHTML = (
          '<div class="title-translate-state title-translate-state--error">' +
          escapeHtml(payload || "接口返回错误，请稍后重试。") +
          '</div>'
        );
        return;
      }

      if (mode === "success") {
        var data = payload || {};
        var result = data.result || {};
        var language = data.language || {};
        var languageLabel = language.name_zh || language.code || "";
        var model = data.model || "Claude Sonnet";

        state.currentResultText = [
          "标题: " + (result.title || ""),
          "文案: " + (result.body || ""),
          "描述: " + (result.description || ""),
        ].join("\n");

        resultEl.innerHTML = (
          '<div class="title-translate-result-head">' +
          '  <div>' +
          '    <div class="title-translate-panel-title">翻译结果</div>' +
          '    <div class="title-translate-result-meta">目标语种：' + escapeHtml(languageLabel) + ' · ' + escapeHtml(model) + '</div>' +
          '  </div>' +
          '  <button type="button" class="title-translate-copy-btn" data-copy-all="true">复制全部</button>' +
          '</div>' +
          '<div class="title-translate-block">' +
          '  <div class="title-translate-block-label">标题</div>' +
          '  <div class="title-translate-block-value">' + escapeHtml(result.title || "") + '</div>' +
          '</div>' +
          '<div class="title-translate-block">' +
          '  <div class="title-translate-block-label">文案</div>' +
          '  <div class="title-translate-block-value">' + escapeHtml(result.body || "") + '</div>' +
          '</div>' +
          '<div class="title-translate-block">' +
          '  <div class="title-translate-block-label">描述</div>' +
          '  <div class="title-translate-block-value">' + escapeHtml(result.description || "") + '</div>' +
          '</div>'
        );
        return;
      }
    }

    function getSelectedLanguage() {
      for (var i = 0; i < state.languages.length; i += 1) {
        if (state.languages[i].code === state.selectedLanguageCode) {
          return state.languages[i];
        }
      }
      return null;
    }

    function renderPills() {
      if (!pillsEl) return;

      if (!state.languages.length) {
        pillsEl.innerHTML = '<span class="title-translate-pill is-disabled">暂无可用语种</span>';
        return;
      }

      pillsEl.innerHTML = state.languages.map(function (language) {
        var isActive = language.code === state.selectedLanguageCode;
        return (
          '<button type="button" class="title-translate-pill' + (isActive ? " is-active" : "") + '" ' +
          'data-language-code="' + escapeHtml(language.code || "") + '" ' +
          'aria-pressed="' + (isActive ? "true" : "false") + '">' +
          escapeHtml(language.name_zh || language.code || "") +
          '</button>'
        );
      }).join("");
    }

    function loadLanguages() {
      if (!urls.languages) {
        setResultState("error", "未配置语种接口地址。");
        return;
      }

      pillsEl.innerHTML = '<span class="title-translate-pill is-disabled">正在加载语种…</span>';

      requestJson(urls.languages, { method: "GET" })
        .then(function (data) {
          var list = Array.isArray(data && data.languages) ? data.languages.slice() : [];
          list = list
            .filter(function (item) {
              return item && item.code;
            })
            .sort(function (a, b) {
              var ao = Number(a.sort_order || 0);
              var bo = Number(b.sort_order || 0);
              if (ao !== bo) return ao - bo;
              return String(a.code).localeCompare(String(b.code));
            });

          state.languages = list;
          state.selectedLanguageCode = list.length ? list[0].code : "";
          renderPills();
          if (!list.length) {
            setResultState("error", "暂无可用语种，请先在后台启用目标语种。");
            setControlsDisabled(true);
            return;
          }
          setControlsDisabled(false);
          setResultState("empty");
        })
        .catch(function (err) {
          pillsEl.innerHTML = '<span class="title-translate-pill is-disabled">语种加载失败</span>';
          setResultState("error", "语种列表加载失败： " + (err && err.message ? err.message : "请刷新页面重试"));
          setControlsDisabled(true);
        });
    }

    function handleLanguageClick(event) {
      var target = event.target.closest("[data-language-code]");
      if (!target || target.disabled || state.busy) return;
      var code = target.getAttribute("data-language-code");
      if (!code || code === state.selectedLanguageCode) return;
      state.selectedLanguageCode = code;
      renderPills();
    }

    function handleTranslate() {
      if (state.busy) return;

      setSourceError("");
      var selectedLanguage = getSelectedLanguage();
      if (!selectedLanguage) {
        setResultState("error", "暂无可用语种，请先在后台启用目标语种。");
        return;
      }

      var validation = validateSourceText(sourceEl ? sourceEl.value : "");
      if (!validation.ok) {
        setSourceError(validation.message);
        if (sourceEl && sourceEl.focus) {
          sourceEl.focus();
        }
        return;
      }

      setBusy(true);
      setResultState("loading");

      requestJson(urls.translate, {
        method: "POST",
        body: {
          language: selectedLanguage.code,
          source_text: validation.value,
        },
      })
        .then(function (data) {
          setSourceError("");
          setResultState("success", data || {});
        })
        .catch(function (err) {
          setResultState("error", (err && err.message) ? err.message : "翻译失败，请稍后重试。");
        })
        .then(function () {
          setBusy(false);
        });
    }

    function copyCurrentResult() {
      if (!state.currentResultText) return;
      var copyBtn = resultEl ? resultEl.querySelector("[data-copy-all]") : null;
      var originalText = copyBtn ? copyBtn.textContent : "";

      var copyPromise;
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        copyPromise = navigator.clipboard.writeText(state.currentResultText);
      } else {
        copyPromise = fallbackCopy(state.currentResultText);
      }

      copyPromise.then(function () {
        if (copyBtn) {
          copyBtn.textContent = "已复制";
          setTimeout(function () {
            copyBtn.textContent = originalText || "复制全部";
          }, 1200);
        }
      });
    }

    function handleResultClick(event) {
      var copyBtn = event.target.closest("[data-copy-all]");
      if (!copyBtn) return;
      event.preventDefault();
      copyCurrentResult();
    }

    function bindEvents() {
      if (pillsEl) {
        pillsEl.addEventListener("click", handleLanguageClick);
      }
      if (translateBtn) {
        translateBtn.addEventListener("click", handleTranslate);
      }
      if (sourceEl) {
        sourceEl.addEventListener("input", function () {
          if (sourceErrorEl && sourceErrorEl.textContent) {
            setSourceError("");
          }
        });
      }
      if (resultEl) {
        resultEl.addEventListener("click", handleResultClick);
      }
    }

    function init() {
      if (state.initialized) return;
      state.initialized = true;
      bindEvents();
      setResultState("empty");
      loadLanguages();
    }

    return {
      init: init,
      validateSourceText: validateSourceText,
    };
  }

  function init() {
    var root = document.getElementById("titleTranslateApp");
    if (!root || root.__titleTranslateInitialized) return;
    root.__titleTranslateInitialized = true;
    var app = createWorkbench(root);
    app.init();
  }

  window.TitleTranslateWorkbench = {
    init: init,
    validateSourceText: validateSourceText,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
