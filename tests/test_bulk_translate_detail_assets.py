from pathlib import Path


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
    assert "function renderTaskSection" in script
    assert "当前正在处理" in script
    assert "需要处理" in script
    assert "待执行" in script
    assert "已完成" in script
    assert ">整个任务重新启动</button>" in script
    assert ">重跑失败项</button>" in script
    assert ">单个重新启动</button>" in script
    assert "中断项不会在服务启动时自动重跑" in script


def test_bulk_translate_detail_css_adds_roomy_status_layout():
    css = (ROOT / "web" / "static" / "bulk_translate_ui.css").read_text(
        encoding="utf-8"
    )

    assert ".bt-status-hero" in css
    assert ".bt-stat-card" in css
    assert ".bt-plan-section" in css
    assert ".bt-plan-item__body" in css
    assert "line-height: 1.55" in css
