from pathlib import Path
import json
import os
import subprocess
import tempfile
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_bulk_translate_detail_template_provides_clarity_regions():
    template = (ROOT / "web" / "templates" / "bulk_translate_detail.html").read_text(
        encoding="utf-8"
    )

    assert 'data-bt-status-panel' in template
    assert 'data-bt-progress-label' in template
    assert "任务总览" in template
    assert "子任务进度" in template
    assert "操作记录" in template


def test_bulk_translate_detail_script_renders_status_and_task_sections():
    script = (ROOT / "web" / "static" / "bulk_translate_detail.js").read_text(
        encoding="utf-8"
    )

    assert "function renderStatusPanel" in script
    assert "function buildStatusInsight" in script
    assert "function buildProgressEstimate" in script
    assert "function renderTaskCard" in script
    assert "function splitTaskCards" in script
    assert "需要人工干预" in script
    assert "正常运行 / 等待执行 / 已完成" in script
    assert "bt-task-card" in script


def test_bulk_translate_detail_css_adds_roomy_status_layout():
    css = (ROOT / "web" / "static" / "bulk_translate_ui.css").read_text(
        encoding="utf-8"
    )

    assert ".bt-status-hero" in css
    assert ".bt-status-meter-bar" in css
    assert ".bt-stat-card" in css
    assert ".bt-intervention-zone" in css
    assert ".bt-task-card" in css
    assert ".bt-task-card__body" in css
    assert "line-height: 1.55" in css


def _run_detail_harness(task_payload: dict) -> dict:
    script_path = ROOT / "web" / "static" / "bulk_translate_detail.js"
    harness = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");

        const task = JSON.parse(process.env.BT_DETAIL_TASK);
        const script = fs.readFileSync(process.env.BT_DETAIL_SCRIPT, "utf8");

        function createElement(name) {
          return {
            name,
            innerHTML: "",
            textContent: "",
            dataset: {},
            style: {},
            listeners: {},
            querySelectorAll(selector) {
              if (selector === "[data-retry-idx]" || selector === "[data-act]") {
                return [];
              }
              return [];
            },
            addEventListener(type, handler) {
              this.listeners[type] = handler;
            },
          };
        }

        const nodes = {
          "[data-bt-status-panel]": createElement("status"),
          "[data-bt-meta]": createElement("meta"),
          ".bt-detail__progress-fill": createElement("progressFill"),
          "[data-bt-progress-label]": createElement("progressLabel"),
          "[data-bt-stats]": createElement("stats"),
          "[data-bt-actions]": createElement("actions"),
          "[data-bt-plan]": createElement("plan"),
          "[data-bt-audit]": createElement("audit"),
        };

        const root = {
          dataset: { taskId: task.id || "task-under-test" },
          querySelector(selector) {
            return nodes[selector] || createElement(selector);
          },
        };

        global.document = {
          querySelector(selector) {
            return selector === ".bt-detail" ? root : null;
          },
        };
        global.window = { addEventListener() {} };
        global.confirm = () => true;
        global.alert = (message) => { throw new Error(message); };
        global.fetch = async () => ({
          ok: true,
          json: async () => task,
        });

        vm.runInThisContext(script, { filename: process.env.BT_DETAIL_SCRIPT });

        setImmediate(() => {
          process.stdout.write(JSON.stringify({
            statusHtml: nodes["[data-bt-status-panel]"].innerHTML,
            statsHtml: nodes["[data-bt-stats]"].innerHTML,
            planHtml: nodes["[data-bt-plan]"].innerHTML,
          }));
        });
        """
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as handle:
        handle.write(harness)
        harness_path = Path(handle.name)

    try:
        completed = subprocess.run(
            ["node", str(harness_path)],
            cwd=ROOT,
            env={
                **os.environ,
                "BT_DETAIL_SCRIPT": str(script_path),
                "BT_DETAIL_TASK": json.dumps(task_payload, ensure_ascii=False),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    finally:
        harness_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise AssertionError(
            f"Node harness failed with code {completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def test_bulk_translate_detail_renders_intervention_cards_before_normal_cards():
    rendered = _run_detail_harness(
        {
            "id": "task-card-demo",
            "status": "running",
            "created_at": "2026-04-23T10:00:00+00:00",
            "updated_at": "2026-04-23T10:10:00+00:00",
            "state": {
                "initiator": {"user_name": "admin", "ip": "127.0.0.1"},
                "progress": {"total": 4, "done": 2, "running": 1, "failed": 1, "pending": 0},
                "cost_tracking": {"estimate": {"estimated_cost_cny": 0}, "actual": {"actual_cost_cny": 1.2}},
                "plan": [
                    {
                        "idx": 0,
                        "lang": "it",
                        "kind": "copywriting",
                        "status": "done",
                        "ref": {"source_copy_id": 475},
                        "child_task_id": "sub-done-1",
                    },
                    {
                        "idx": 1,
                        "lang": "it",
                        "kind": "detail_images",
                        "status": "failed",
                        "error": "图片翻译失败",
                        "ref": {"source_detail_ids": [1, 2, 3]},
                        "child_task_id": "sub-fail-1",
                    },
                    {
                        "idx": 2,
                        "lang": "de",
                        "kind": "videos",
                        "status": "awaiting_voice",
                        "ref": {"source_raw_id": 9},
                        "child_task_id": "sub-voice-1",
                        "child_task_type": "multi_translate",
                    },
                    {
                        "idx": 3,
                        "lang": "fr",
                        "kind": "video_covers",
                        "status": "running",
                        "ref": {"source_raw_ids": [7]},
                        "child_task_id": "sub-run-1",
                    },
                ],
                "audit_events": [],
            },
        }
    )

    plan_html = rendered["planHtml"]
    assert "需要人工干预" in plan_html
    assert "正常运行 / 等待执行 / 已完成" in plan_html
    assert plan_html.index("需要人工干预") < plan_html.index("正常运行 / 等待执行 / 已完成")
    assert plan_html.count("bt-task-card") >= 4
    assert "图片翻译失败" in plan_html
    assert "等待人工选声音" in plan_html
    assert "重跑" in plan_html
    assert "2/4" in rendered["statsHtml"]
    assert "50%" in rendered["statusHtml"]
    assert "剩余 2 个" in rendered["statusHtml"]
