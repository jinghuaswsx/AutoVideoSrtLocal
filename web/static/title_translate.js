(function () {
  "use strict";

  var SOURCE_EMPTY_MESSAGE = "请输入要翻译的文案。";
  var PROMPT_PLACEHOLDER = "选择语种并输入原文后，这里会显示即将发送给模型的完整提示词。";

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

  function copyText(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(text);
    }
    return fallbackCopy(text);
  }

  function validateSourceText(rawText) {
    var text = String(rawText || "").replace(/\r\n?/g, "\n").trim();
    if (!text) {
      return { ok: false, message: SOURCE_EMPTY_MESSAGE };
    }
    return { ok: true, value: text };
  }

  function renderPromptPreview(template, sourceText) {
    var tpl = typeof template === "string" ? template : "";
    if (!tpl) return "";
    return tpl.split("{{SOURCE_TEXT}}").join(sourceText || "");
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
    var resultBodyEl = $("#titleTranslateResultBody", root);
    var resultMetaEl = $("#titleTranslateResultMeta", root);
    var resultCopyBtn = $("#titleTranslateCopyBtn", root);
    var promptBodyEl = $("#titleTranslatePromptBody", root);
    var promptMetaEl = $("#titleTranslatePromptMeta", root);
    var promptCopyBtn = $("#titleTranslatePromptCopyBtn", root);

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

    function getSelectedLanguage() {
      for (var i = 0; i < state.languages.length; i += 1) {
        if (state.languages[i].code === state.selectedLanguageCode) {
          return state.languages[i];
        }
      }
      return null;
    }

    function setResultState(mode, payload) {
      state.resultMode = mode;
      if (!resultEl) return;
      resultEl.setAttribute("data-state", mode);

      var language = getSelectedLanguage();
      var languageLabel = language ? (language.name_zh || language.code || "") : "";

      if (mode === "empty") {
        state.currentResultText = "";
        if (resultBodyEl) {
          resultBodyEl.textContent = "结果将显示在这里";
          resultBodyEl.classList.add("title-translate-result-body--empty");
        }
        if (resultMetaEl) resultMetaEl.textContent = "等待翻译";
        if (resultCopyBtn) resultCopyBtn.disabled = true;
        return;
      }

      if (mode === "loading") {
        state.currentResultText = "";
        if (resultBodyEl) {
          resultBodyEl.textContent = "正在使用 Claude Sonnet 翻译…";
          resultBodyEl.classList.remove("title-translate-result-body--empty");
        }
        if (resultMetaEl) {
          resultMetaEl.textContent = languageLabel ? ("目标语种：" + languageLabel + " · 翻译中") : "翻译中";
        }
        if (resultCopyBtn) resultCopyBtn.disabled = true;
        return;
      }

      if (mode === "error") {
        state.currentResultText = "";
        if (resultBodyEl) {
          resultBodyEl.textContent = payload || "接口返回错误，请稍后重试。";
          resultBodyEl.classList.remove("title-translate-result-body--empty");
        }
        if (resultMetaEl) resultMetaEl.textContent = "翻译失败";
        if (resultCopyBtn) resultCopyBtn.disabled = true;
        return;
      }

      if (mode === "success") {
        var data = payload || {};
        var resultText = typeof data.result === "string" ? data.result : "";
        var respLanguage = data.language || {};
        var respLabel = respLanguage.name_zh || respLanguage.code || languageLabel;
        var model = data.model || "Claude Sonnet";

        state.currentResultText = resultText;
        if (resultBodyEl) {
          resultBodyEl.textContent = resultText || "(模型未返回内容)";
          resultBodyEl.classList.remove("title-translate-result-body--empty");
        }
        if (resultMetaEl) {
          resultMetaEl.textContent = "目标语种：" + respLabel + " · " + model;
        }
        if (resultCopyBtn) resultCopyBtn.disabled = !resultText;
        return;
      }
    }

    function renderPromptPanel() {
      if (!promptBodyEl) return;
      var language = getSelectedLanguage();
      var template = language && typeof language.prompt === "string" ? language.prompt : "";
      var sourceText = sourceEl ? sourceEl.value : "";
      var trimmedSource = String(sourceText || "").replace(/\r\n?/g, "\n");

      if (!template) {
        promptBodyEl.textContent = PROMPT_PLACEHOLDER;
        promptBodyEl.classList.add("title-translate-prompt-body--empty");
        if (promptMetaEl) {
          promptMetaEl.textContent = language
            ? "该语种暂未配置提示词模板。"
            : "根据左侧输入和选中语种实时生成。";
        }
        if (promptCopyBtn) promptCopyBtn.disabled = true;
        return;
      }

      var effectiveSource = trimmedSource.trim() ? trimmedSource : "(此处将替换为左侧输入的原文)";
      var rendered = renderPromptPreview(template, effectiveSource);
      promptBodyEl.textContent = rendered;
      promptBodyEl.classList.remove("title-translate-prompt-body--empty");
      if (promptMetaEl) {
        var label = language.name_zh || language.code || "";
        promptMetaEl.textContent = "目标语种：" + label + "，将原文替换到 {{SOURCE_TEXT}}。";
      }
      if (promptCopyBtn) promptCopyBtn.disabled = false;
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
            renderPromptPanel();
            return;
          }
          setControlsDisabled(false);
          setResultState("empty");
          renderPromptPanel();
        })
        .catch(function (err) {
          pillsEl.innerHTML = '<span class="title-translate-pill is-disabled">语种加载失败</span>';
          setResultState("error", "语种列表加载失败： " + (err && err.message ? err.message : "请刷新页面重试"));
          setControlsDisabled(true);
          renderPromptPanel();
        });
    }

    function handleLanguageClick(event) {
      var target = event.target.closest("[data-language-code]");
      if (!target || target.disabled || state.busy) return;
      var code = target.getAttribute("data-language-code");
      if (!code || code === state.selectedLanguageCode) return;
      state.selectedLanguageCode = code;
      renderPills();
      renderPromptPanel();
      if (state.resultMode !== "success") {
        setResultState("empty");
      } else if (resultMetaEl) {
        var lang = getSelectedLanguage();
        var label = lang ? (lang.name_zh || lang.code || "") : "";
        resultMetaEl.textContent = "目标语种：" + label + " · 点击翻译刷新结果";
      }
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

    function flashCopyButton(button, originalText) {
      if (!button) return;
      button.textContent = "已复制";
      setTimeout(function () {
        button.textContent = originalText || "复制";
      }, 1200);
    }

    function handleResultCopy() {
      if (!state.currentResultText) return;
      var original = resultCopyBtn ? resultCopyBtn.textContent : "复制";
      copyText(state.currentResultText).then(function () {
        flashCopyButton(resultCopyBtn, original);
      });
    }

    function handlePromptCopy() {
      if (!promptBodyEl) return;
      if (promptBodyEl.classList.contains("title-translate-prompt-body--empty")) return;
      var text = promptBodyEl.textContent || "";
      if (!text) return;
      var original = promptCopyBtn ? promptCopyBtn.textContent : "复制";
      copyText(text).then(function () {
        flashCopyButton(promptCopyBtn, original);
      });
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
          renderPromptPanel();
        });
      }
      if (resultCopyBtn) {
        resultCopyBtn.addEventListener("click", handleResultCopy);
      }
      if (promptCopyBtn) {
        promptCopyBtn.addEventListener("click", handlePromptCopy);
      }
    }

    function init() {
      if (state.initialized) return;
      state.initialized = true;
      bindEvents();
      setResultState("empty");
      renderPromptPanel();
      loadLanguages();
    }

    return {
      init: init,
      validateSourceText: validateSourceText,
      renderPromptPreview: renderPromptPreview,
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
    renderPromptPreview: renderPromptPreview,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
