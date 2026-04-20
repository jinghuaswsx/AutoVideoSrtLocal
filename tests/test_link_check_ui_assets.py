import json
import os
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path


def test_link_check_projects_template_only_exposes_task5_surface():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")

    assert 'id="linkCheckProjectForm"' in template
    assert 'id="linkCheckProjectList"' in template
    assert 'id="linkCheckError"' in template
    assert 'id="linkCheckStatus"' in template
    assert "link_check_projects.js" in template

    assert 'id="linkCheckSummary"' not in template
    assert 'id="linkCheckResults"' not in template
    assert 'id="linkCheckDetailDialog"' not in template


def test_link_check_projects_template_prefills_english_before_language_list_loads():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")

    assert '<option value="en" selected>' in template


def test_link_check_projects_script_includes_locale_detection_and_redirect():
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert "function detectTargetLanguageFromUrl" in script
    assert "window.location.assign" in script
    assert 'form.addEventListener("submit", onSubmit)' in script
    assert 'linkInput.addEventListener("input", syncLanguageFromUrl)' in script


def test_link_check_projects_script_checks_full_segment_before_primary_subtag_fallback():
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert "if (enabledLanguages.has(segment))" in script
    assert 'if (segment.includes("-"))' in script
    assert 'const primary = segment.split("-", 1)[0];' in script
    assert "if (enabledLanguages.has(primary))" in script


def test_link_check_projects_script_does_not_restrict_locale_detection_with_length_regex():
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert "isLocaleCode" not in script
    assert "isLanguageCountryPair" not in script
    assert "^[a-z]{2,3}$" not in script
    assert "^[a-z]{2,3}-[a-z]{2,3}$" not in script


def _run_link_check_projects_harness(scenario: dict) -> dict:
    script_path = Path("web/static/link_check_projects.js").resolve()
    harness = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");

        const scenario = JSON.parse(process.env.LINK_CHECK_PROJECTS_SCENARIO);
        const scriptPath = process.env.LINK_CHECK_PROJECTS_SCRIPT_PATH;
        const scriptSource = fs.readFileSync(scriptPath, "utf8");

        function createBaseElement(id) {
          return {
            id,
            textContent: "",
            hidden: false,
            dataset: {},
            attributes: {},
            listeners: {},
            disabled: false,
            classList: {
              add() {},
              remove() {},
              toggle() {},
            },
            setAttribute(name, value) {
              this.attributes[name] = String(value);
            },
            getAttribute(name) {
              return this.attributes[name];
            },
            addEventListener(type, handler) {
              this.listeners[type] = handler;
            },
            dispatch(type) {
              if (this.listeners[type]) {
                this.listeners[type]({ target: this, preventDefault() {} });
              }
            },
          };
        }

        function createSelectElement(id) {
          const element = createBaseElement(id);
          element.options = [];
          element.value = Object.prototype.hasOwnProperty.call(scenario, "initialSelectValue")
            ? scenario.initialSelectValue
            : "";
          element._innerHTML = "";
          element.appendChild = function (option) {
            this.options.push(option);
            return option;
          };
          Object.defineProperty(element, "innerHTML", {
            get() {
              return this._innerHTML;
            },
            set(value) {
              this._innerHTML = String(value);
              const match = this._innerHTML.match(/<option value="([^"]*)">([\\s\\S]*?)<\\/option>/i);
              this.options = [];
              if (match) {
                this.options.push({ value: match[1], textContent: match[2] });
              }
              this.value = this.value || "";
            },
          });
          return element;
        }

        const elements = {
          linkCheckProjectForm: createBaseElement("linkCheckProjectForm"),
          linkUrl: createBaseElement("linkUrl"),
          targetLanguage: createSelectElement("targetLanguage"),
          linkCheckError: createBaseElement("linkCheckError"),
          linkCheckStatus: createBaseElement("linkCheckStatus"),
          linkCheckSubmit: createBaseElement("linkCheckSubmit"),
          linkCheckLanguageHint: createBaseElement("linkCheckLanguageHint"),
        };

        elements.linkUrl.value = scenario.linkUrl || "";

        const document = {
          getElementById(id) {
            return elements[id] || null;
          },
          createElement(tagName) {
            if (String(tagName).toLowerCase() === "option") {
              return { value: "", textContent: "" };
            }
            return createBaseElement(tagName);
          },
          addEventListener(type, handler) {
            if (type === "DOMContentLoaded") {
              this._domReady = handler;
            }
          },
        };

        const fetchQueue = Array.isArray(scenario.fetchQueue) ? scenario.fetchQueue.slice() : [];

        async function flushMicrotasks() {
          await Promise.resolve();
          await new Promise((resolve) => setImmediate(resolve));
          await Promise.resolve();
        }

        global.window = global;
        global.document = document;
        global.FormData = function FormData(form) {
          this.form = form;
        };
        global.fetch = function () {
          if (!fetchQueue.length) {
            return Promise.reject(new Error("fetch queue exhausted"));
          }
          const next = fetchQueue.shift();
          return Promise.resolve({
            ok: next.ok !== false,
            json: async () => next.payload || {},
          });
        };

        vm.runInThisContext(scriptSource, { filename: scriptPath });

        (async function () {
          if (typeof document._domReady === "function") {
            await document._domReady();
            await flushMicrotasks();
          }

          for (const action of scenario.actions || []) {
            if (action.type === "setLinkUrl") {
              elements.linkUrl.value = action.value || "";
            }
            if (action.type === "dispatchInput") {
              elements.linkUrl.dispatch("input");
              await flushMicrotasks();
            }
          }

          process.stdout.write(JSON.stringify({
            selectValue: elements.targetLanguage.value,
            optionValues: elements.targetLanguage.options.map((item) => item.value),
            statusText: elements.linkCheckStatus.textContent,
            hintText: elements.linkCheckLanguageHint.textContent,
          }));
        })().catch((error) => {
          console.error(error && error.stack ? error.stack : String(error));
          process.exit(1);
        });
        """
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as handle:
        handle.write(harness)
        harness_path = Path(handle.name)

    try:
        completed = subprocess.run(
            ["node", str(harness_path)],
            cwd=Path.cwd(),
            env={
                **os.environ,
                "LINK_CHECK_PROJECTS_SCENARIO": json.dumps(scenario, ensure_ascii=False),
                "LINK_CHECK_PROJECTS_SCRIPT_PATH": str(script_path),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    finally:
        harness_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise AssertionError(
            f"Node harness failed with code {completed.returncode}\\nSTDOUT:\\n{completed.stdout}\\nSTDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)


def test_link_check_projects_script_defaults_to_english_after_language_list_load():
    result = _run_link_check_projects_harness(
        {
            "linkUrl": "",
            "fetchQueue": [
                {
                    "payload": {
                        "items": [
                            {"code": "en", "name_zh": "英语"},
                            {"code": "fr", "name_zh": "法语"},
                        ]
                    }
                }
            ],
        }
    )

    assert result["optionValues"] == ["", "en", "fr"]
    assert result["selectValue"] == "en"


def test_link_check_detail_script_includes_locale_and_download_evidence_renderers():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "function renderLocaleAttemptRow" in script
    assert "function renderLocaleEvidence" in script
    assert "function renderDownloadEvidence" in script
    assert "locale_evidence" in script
    assert "download_evidence" in script


def test_link_check_projects_css_focuses_on_create_and_list_page():
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert ".lc-project-list" in style
    assert ".lc-project-card" in style
    assert ".lc-form-grid" in style
    assert ".lc-panel-tip" in style

    assert ".lc-result-card--alert" in style
    assert ".lc-meta-card--alert" in style
    assert ".lc-issue-summary" in style


def test_link_check_detail_css_includes_evidence_layouts():
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert ".lc-evidence-grid" in style
    assert ".lc-attempt-table" in style
    assert ".lc-attempt-table-wrap" in style
    assert ".lc-evidence-block" in style


def test_link_check_detail_template_bootstraps_persisted_task_for_detail_page():
    template = Path("web/templates/link_check_detail.html").read_text(encoding="utf-8")

    assert 'id="linkCheckDetailPage"' in template
    assert "__LINK_CHECK_TASK__" in template
    assert 'id="linkCheckSummary"' in template
    assert 'id="linkCheckResults"' in template
    assert "link_check.css" in template
    assert "link_check.js" in template


def _run_link_check_detail_harness(scenario: dict) -> dict:
    script_path = Path("web/static/link_check.js").resolve()
    harness = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");

        const scenario = JSON.parse(process.env.LINK_CHECK_SCENARIO);
        const scriptPath = process.env.LINK_CHECK_SCRIPT_PATH;
        const scriptSource = fs.readFileSync(scriptPath, "utf8");

        function createElement(id) {
          return {
            id,
            innerHTML: "",
            textContent: "",
            hidden: false,
            dataset: {},
            attributes: {},
            listeners: {},
            classList: {
              add() {},
              remove() {},
              toggle() {},
            },
            setAttribute(name, value) {
              this.attributes[name] = String(value);
            },
            getAttribute(name) {
              return this.attributes[name];
            },
            addEventListener(type, handler) {
              this.listeners[type] = handler;
            },
            closest() {
              return null;
            },
          };
        }

        const elements = {
          linkCheckDetailPage: createElement("linkCheckDetailPage"),
          linkCheckInitialTask: createElement("linkCheckInitialTask"),
          linkCheckSummary: createElement("linkCheckSummary"),
          linkCheckResults: createElement("linkCheckResults"),
          linkCheckStatus: createElement("linkCheckStatus"),
          linkCheckError: createElement("linkCheckError"),
        };

        elements.linkCheckDetailPage.dataset.taskId = scenario.pageTaskId || "task-under-test";
        elements.linkCheckInitialTask.textContent = scenario.scriptTaskText || "";

        const document = {
          body: {
            classList: {
              add() {},
              remove() {},
              toggle() {},
            },
          },
          getElementById(id) {
            return elements[id] || null;
          },
          addEventListener(type, handler) {
            if (type === "DOMContentLoaded") {
              this._domReady = handler;
            }
          },
          dispatchEvent() {},
        };

        const intervals = new Map();
        const clearCalls = [];
        let nextIntervalId = 1;

        function activeIntervalIds() {
          return [...intervals.entries()]
            .filter(([, entry]) => entry.active)
            .map(([id]) => id);
        }

        async function flushMicrotasks() {
          await Promise.resolve();
          await new Promise((resolve) => setImmediate(resolve));
          await Promise.resolve();
        }

        const fetchQueue = Array.isArray(scenario.fetchQueue) ? scenario.fetchQueue.slice() : [];

        global.window = global;
        global.window.__LINK_CHECK_TASK__ = scenario.windowTask || null;
        global.document = document;
        global.fetch = function () {
          if (!fetchQueue.length) {
            return Promise.reject(new Error("fetch queue exhausted"));
          }
          const next = fetchQueue.shift();
          if (next.type === "throw") {
            return Promise.reject(new Error(next.message || "network error"));
          }
          const payload = next.payload || {};
          const ok = next.ok !== false;
          return Promise.resolve({
            ok,
            json: async () => payload,
          });
        };
        global.setInterval = function (handler, timeout) {
          const id = nextIntervalId++;
          intervals.set(id, { handler, timeout, active: true });
          return id;
        };
        global.clearInterval = function (id) {
          const entry = intervals.get(id);
          if (entry) {
            entry.active = false;
          }
          clearCalls.push(id);
        };

        vm.runInThisContext(scriptSource, { filename: scriptPath });

        async function runDomReady() {
          if (typeof document._domReady === "function") {
            document._domReady();
            await flushMicrotasks();
          }
        }

        async function tickLatestInterval() {
          const ids = activeIntervalIds();
          if (!ids.length) {
            return;
          }
          const id = ids[ids.length - 1];
          const entry = intervals.get(id);
          entry.handler();
          await flushMicrotasks();
          await flushMicrotasks();
        }

        function countOccurrences(text, pattern) {
          if (!pattern) {
            return 0;
          }
          return String(text).split(pattern).length - 1;
        }

        function extractAlertLabels(resultsHtml, matchers) {
          const labels = [];
          const regex = /<div class="lc-meta-card lc-meta-card--alert">[\\s\\S]*?<strong class="lc-meta-label">([^<]+)<\\/strong>[\\s\\S]*?<span class="[^"]*">([^<]*)<\\/span>[\\s\\S]*?<\\/div>/g;
          let found;
          while ((found = regex.exec(resultsHtml)) !== null) {
            const label = found[1];
            if (matchers.some((matcher) => label.includes(matcher))) {
              labels.push(label);
            }
          }
          return labels;
        }

        (async function () {
          await runDomReady();

          for (const action of scenario.actions || []) {
            if (action === "tickLatestInterval") {
              await tickLatestInterval();
            }
          }

          const summaryHtml = elements.linkCheckSummary.innerHTML;
          const resultsHtml = elements.linkCheckResults.innerHTML;

          process.stdout.write(JSON.stringify({
            summaryHtml,
            resultsHtml,
            statusText: elements.linkCheckStatus.textContent,
            errorText: elements.linkCheckError.textContent,
            errorHidden: elements.linkCheckError.hidden,
            activeIntervalIds: activeIntervalIds(),
            clearCalls,
            intervalCount: intervals.size,
            resultAlertCount: countOccurrences(resultsHtml, "lc-result-card--alert"),
            metaAlertCount: countOccurrences(resultsHtml, "lc-meta-card--alert"),
            binaryErrorAlertLabels: extractAlertLabels(resultsHtml, ["二值快检结果", "二值快检说明"]),
            sameImageErrorAlertLabels: extractAlertLabels(resultsHtml, ["大模型同图判断"]),
          }));
        })().catch((error) => {
          console.error(error && error.stack ? error.stack : String(error));
          process.exit(1);
        });
        """
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as handle:
      handle.write(harness)
      harness_path = Path(handle.name)

    try:
        completed = subprocess.run(
            ["node", str(harness_path)],
            cwd=Path.cwd(),
            env={
                **os.environ,
                "LINK_CHECK_SCENARIO": json.dumps(scenario, ensure_ascii=False),
                "LINK_CHECK_SCRIPT_PATH": str(script_path),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    finally:
        harness_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise AssertionError(
            f"Node harness failed with code {completed.returncode}\\nSTDOUT:\\n{completed.stdout}\\nSTDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)


def _extract_result_meta_value(results_html: str, label: str) -> str | None:
    pattern = re.compile(
        rf'<div class="lc-meta-card(?: lc-meta-card--alert)?">[\s\S]*?<strong class="lc-meta-label">{re.escape(label)}</strong>[\s\S]*?<span class="[^"]*">([^<]*)</span>',
    )
    match = pattern.search(results_html)
    if not match:
        return None
    return match.group(1)


def _extract_summary_card_value(summary_html: str, label: str) -> str | None:
    pattern = re.compile(
        rf'<div class="lc-summary-card">[\s\S]*?<strong>{re.escape(label)}</strong>[\s\S]*?<span>([^<]*)</span>',
    )
    match = pattern.search(summary_html)
    if not match:
        return None
    return match.group(1)


def test_link_check_detail_bootstrap_prefers_window_task_before_json_fallback():
    window_task = {
        "id": "window-task",
        "status": "done",
        "target_language": "fr",
        "target_language_name": "法语",
        "summary": {"overall_decision": "review"},
        "progress": {},
        "items": [
            {
                "id": "item-window",
                "kind": "detail",
                "source_url": "window-source",
                "analysis": {"decision": "review", "decision_source": "gemini_language_check"},
                "reference_match": {"status": "not_provided"},
                "binary_quick_check": {"status": "skipped"},
                "same_image_llm": {"status": "skipped"},
                "status": "done",
            }
        ],
    }
    script_task = {
        "id": "script-task",
        "status": "done",
        "target_language": "de",
        "target_language_name": "德语",
        "summary": {"overall_decision": "pass"},
        "progress": {},
        "items": [],
    }

    preferred = _run_link_check_detail_harness(
        {
            "pageTaskId": "window-task",
            "windowTask": window_task,
            "scriptTaskText": json.dumps(script_task, ensure_ascii=False),
        }
    )
    fallback = _run_link_check_detail_harness(
        {
            "pageTaskId": "script-task",
            "scriptTaskText": json.dumps(script_task, ensure_ascii=False),
        }
    )

    assert "法语" in preferred["summaryHtml"]
    assert "window-source" in preferred["resultsHtml"]
    assert "script-task" not in preferred["summaryHtml"]
    assert "德语" in fallback["summaryHtml"]
    assert fallback["activeIntervalIds"] == []


def test_link_check_detail_polling_retries_transient_failures_and_stops_on_threshold_or_terminal():
    running_task = {
        "id": "poll-task",
        "status": "queued",
        "target_language": "fr",
        "target_language_name": "法语",
        "summary": {"overall_decision": "running"},
        "progress": {},
        "items": [],
    }
    done_task = {
        **running_task,
        "status": "done",
        "summary": {"overall_decision": "done"},
    }

    transient_then_stop = _run_link_check_detail_harness(
        {
            "pageTaskId": "poll-task",
            "windowTask": running_task,
            "actions": [
                "tickLatestInterval",
                "tickLatestInterval",
                "tickLatestInterval",
            ],
            "fetchQueue": [
                {"ok": False, "payload": {"error": "temporary-1"}},
                {"ok": False, "payload": {"error": "temporary-2"}},
                {"ok": False, "payload": {"error": "temporary-3"}},
            ],
        }
    )
    terminal_stop = _run_link_check_detail_harness(
        {
            "pageTaskId": "poll-task",
            "windowTask": running_task,
            "actions": ["tickLatestInterval"],
            "fetchQueue": [
                {"ok": True, "payload": done_task},
            ],
        }
    )

    assert transient_then_stop["intervalCount"] == 1
    assert transient_then_stop["clearCalls"] == [1]
    assert transient_then_stop["activeIntervalIds"] == []
    assert "temporary-3" in transient_then_stop["errorText"]
    assert "停止自动轮询" in transient_then_stop["statusText"]

    assert terminal_stop["intervalCount"] == 1
    assert terminal_stop["clearCalls"] == [1]
    assert terminal_stop["activeIntervalIds"] == []
    assert terminal_stop["errorHidden"] is True


def test_link_check_detail_renders_alerts_for_key_non_pass_states():
    task = {
        "id": "alert-task",
        "status": "done",
        "target_language": "fr",
        "target_language_name": "法语",
        "summary": {"overall_decision": "done"},
        "progress": {},
        "items": [
            {
                "id": "review-item",
                "kind": "detail",
                "source_url": "review-source",
                "analysis": {"decision": "review", "decision_source": "gemini_language_check"},
                "reference_match": {"status": "not_provided"},
                "binary_quick_check": {"status": "skipped"},
                "same_image_llm": {"status": "skipped"},
                "status": "done",
            },
            {
                "id": "no-text-item",
                "kind": "detail",
                "source_url": "no-text-source",
                "analysis": {"decision": "no_text", "decision_source": "gemini_language_check"},
                "reference_match": {"status": "not_provided"},
                "binary_quick_check": {"status": "skipped"},
                "same_image_llm": {"status": "skipped"},
                "status": "done",
            },
            {
                "id": "failed-item",
                "kind": "detail",
                "source_url": "failed-source",
                "analysis": {"decision": "failed", "decision_source": "gemini_language_check"},
                "reference_match": {"status": "not_provided"},
                "binary_quick_check": {"status": "error", "reason": "binary down"},
                "same_image_llm": {"status": "error"},
                "status": "failed",
            },
        ],
    }

    rendered = _run_link_check_detail_harness(
        {
            "pageTaskId": "alert-task",
            "windowTask": task,
        }
    )

    assert rendered["resultAlertCount"] == 3
    assert rendered["metaAlertCount"] >= 7
    assert rendered["binaryErrorAlertLabels"] == ["二值快检结果", "二值快检说明"]
    assert rendered["sameImageErrorAlertLabels"] == ["大模型同图判断"]
    assert "lc-result-card--alert" in rendered["resultsHtml"]
    assert "lc-meta-card--alert" in rendered["resultsHtml"]
    assert "review-source" in rendered["resultsHtml"]
    assert "no-text-source" in rendered["resultsHtml"]
    assert "failed-source" in rendered["resultsHtml"]


def test_link_check_detail_download_evidence_without_booleans_does_not_render_negative_conclusions():
    task = {
        "id": "download-evidence-missing",
        "status": "done",
        "target_language": "de",
        "target_language_name": "德语",
        "summary": {"overall_decision": "done"},
        "progress": {"total": 1},
        "items": [
            {
                "id": "site-1",
                "kind": "detail",
                "source_url": "https://img/site.jpg",
                "analysis": {"decision": "pass", "decision_source": "binary_quick_check"},
                "reference_match": {"status": "not_provided"},
                "binary_quick_check": {"status": "pass"},
                "same_image_llm": {"status": "skipped"},
                "download_evidence": {},
                "status": "done",
            }
        ],
    }

    rendered = _run_link_check_detail_harness(
        {
            "pageTaskId": "download-evidence-missing",
            "windowTask": task,
        }
    )

    assert _extract_result_meta_value(rendered["resultsHtml"], "是否保持同一资源") == "-"
    assert _extract_result_meta_value(rendered["resultsHtml"], "是否来自当前 Variant") == "-"


def test_link_check_detail_default_locale_evidence_does_not_render_unlocked_conclusion():
    task = {
        "id": "locale-evidence-defaulted",
        "status": "done",
        "target_language": "de",
        "target_language_name": "德语",
        "summary": {"overall_decision": "done"},
        "progress": {"total": 0},
        "locale_evidence": {
            "target_language": "de",
            "requested_url": "https://shop.example.com/de/products/demo?variant=123",
            "lock_source": "",
            "locked": False,
            "failure_reason": "",
            "attempts": [],
        },
        "items": [],
    }

    rendered = _run_link_check_detail_harness(
        {
            "pageTaskId": "locale-evidence-defaulted",
            "windowTask": task,
        }
    )

    assert _extract_summary_card_value(rendered["summaryHtml"], "锁定结果") == "-"
    assert "未锁定" not in rendered["summaryHtml"]
    assert "暂无证据" in rendered["summaryHtml"]


def test_link_check_detail_renders_locale_evidence_and_download_evidence_labels():
    task = {
        "id": "evidence-task",
        "status": "done",
        "target_language": "de",
        "target_language_name": "德语",
        "summary": {"overall_decision": "done"},
        "progress": {"total": 1, "analyzed": 1},
        "locale_evidence": {
            "target_language": "de",
            "requested_url": "https://shop.example.com/de/products/demo?variant=123",
            "lock_source": "warmup_attempt_2",
            "locked": True,
            "failure_reason": "",
            "attempts": [
                {
                    "phase": "initial",
                    "attempt_index": 1,
                    "wait_seconds_before_request": 0,
                    "requested_url": "https://shop.example.com/de/products/demo?variant=123",
                    "resolved_url": "https://shop.example.com/products/demo?variant=123",
                    "page_language": "en",
                    "locked": False,
                },
                {
                    "phase": "warmup",
                    "attempt_index": 2,
                    "wait_seconds_before_request": 2,
                    "requested_url": "https://shop.example.com/de/products/demo?variant=123",
                    "resolved_url": "https://shop.example.com/de/products/demo?variant=123",
                    "page_language": "de",
                    "locked": True,
                },
            ],
        },
        "items": [
            {
                "id": "site-1",
                "kind": "carousel",
                "source_url": "https://img/site.jpg",
                "analysis": {"decision": "pass", "decision_source": "binary_quick_check"},
                "reference_match": {"status": "not_provided"},
                "binary_quick_check": {"status": "pass"},
                "same_image_llm": {"status": "done", "answer": "是"},
                "download_evidence": {
                    "requested_source_url": "https://img/site.jpg",
                    "resolved_source_url": "https://cdn.example.com/site.jpg?width=1080",
                    "redirect_preserved_asset": True,
                    "variant_selected": True,
                    "evidence_status": "ok",
                    "evidence_reason": "",
                },
                "status": "done",
            }
        ],
    }

    rendered = _run_link_check_detail_harness(
        {
            "pageTaskId": "evidence-task",
            "windowTask": task,
        }
    )

    assert "warmup_attempt_2" in rendered["summaryHtml"]
    assert "requested_source_url" not in rendered["resultsHtml"]
    assert "最终下载 URL" in rendered["resultsHtml"]
    assert "是否保持同一资源" in rendered["resultsHtml"]
