from pathlib import Path


def test_link_check_js_locks_submit_until_first_progress_render():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "setSubmitting(" in script
    assert "submitButton.disabled = isSubmitting" in script
    assert "正在创建任务" in script
    assert "正在获取首批进度" in script
