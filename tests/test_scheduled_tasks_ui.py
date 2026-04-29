from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_scheduled_tasks_route_requires_admin_single_user():
    source = _read("web/routes/scheduled_tasks.py")

    assert "def _is_admin_single_user" in source
    assert 'getattr(current_user, "is_superadmin", False)' in source
    assert "abort(403)" in source


def test_layout_only_shows_scheduled_tasks_to_admin_single_user():
    source = _read("web/templates/layout.html")

    assert "url_for('scheduled_tasks.page')" in source
    assert "has_permission('scheduled_tasks')" in source
    assert "scheduled-failure-alert" in source
    assert "data-scheduled-alert-close" in source
    assert "scheduled-task-alert-dismissed:" in source


def test_scheduled_tasks_page_has_log_and_management_capsules():
    source = _read("web/templates/scheduled_tasks.html")

    assert "定时任务" in source
    assert "定时任务的运行日志" in source
    assert "定时任务管理" in source
    assert "log_filters" in source
    assert "最近状态" in source
    assert "触发来源" in source
    assert "登记状态" in source
    assert "日志" in source
    assert "item.source_label" in source
    assert "item.name" in source
    assert "scheduled-control-pill" in source
    assert "scheduled-action-pill" in source
    assert "control_supported" in source
    assert "csrf_token()" in source
    assert "max-width:none" in source


def test_scheduled_tasks_route_uses_view_and_task_filters():
    source = _read("web/routes/scheduled_tasks.py")

    assert 'request.args.get("view")' in source
    assert 'request.args.get("task")' in source
    assert "log_filter_definitions" in source
    assert "management_tasks" in source
    assert '@bp.post("/<task_code>/control")' in source
    assert "set_task_enabled" in source
