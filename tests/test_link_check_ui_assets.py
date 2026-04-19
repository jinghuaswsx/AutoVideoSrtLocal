import json
import os
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


def test_link_check_projects_css_focuses_on_create_and_list_page():
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert ".lc-project-list" in style
    assert ".lc-project-card" in style
    assert ".lc-form-grid" in style
    assert ".lc-panel-tip" in style

    assert ".lc-result-card--alert" in style
    assert ".lc-meta-card--alert" in style
    assert ".lc-issue-summary" in style


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
    assert "lc-result-card--alert" in rendered["resultsHtml"]
    assert "lc-meta-card--alert" in rendered["resultsHtml"]
    assert "review-source" in rendered["resultsHtml"]
    assert "no-text-source" in rendered["resultsHtml"]
    assert "failed-source" in rendered["resultsHtml"]
