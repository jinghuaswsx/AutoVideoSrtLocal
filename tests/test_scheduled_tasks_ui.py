from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_scheduled_tasks_route_requires_admin_single_user():
    source = _read("web/routes/scheduled_tasks.py")

    assert "def _is_admin_single_user" in source
    assert 'getattr(current_user, "role", None) == "admin"' in source
    assert 'getattr(current_user, "username", None) == "admin"' in source
    assert "abort(403)" in source


def test_layout_only_shows_scheduled_tasks_to_admin_single_user():
    source = _read("web/templates/layout.html")

    assert "url_for('scheduled_tasks.page')" in source
    assert "current_user.username == 'admin'" in source
    assert "scheduled-failure-alert" in source
    assert "data-scheduled-alert-close" in source
    assert "scheduled-task-alert-dismissed:" in source


def test_scheduled_tasks_page_has_shopifyid_tab_and_daily_result_table():
    source = _read("web/templates/scheduled_tasks.html")

    assert "定时任务" in source
    assert "最近状态" in source
    assert "在线总数" in source
    assert "新回填" in source
    assert "日志" in source
