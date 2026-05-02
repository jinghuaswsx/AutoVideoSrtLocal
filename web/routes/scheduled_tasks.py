from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import scheduled_tasks

bp = Blueprint("scheduled_tasks", __name__, url_prefix="/scheduled-tasks")


def _is_admin_single_user() -> bool:
    return (
        current_user.is_authenticated
        and getattr(current_user, "is_superadmin", False)
    )


@bp.route("")
@login_required
def page():
    if not _is_admin_single_user():
        abort(403)
    active_view = (request.args.get("view") or "logs").strip() or "logs"
    if active_view not in {"logs", "management"}:
        active_view = "logs"

    active_task = (request.args.get("task") or request.args.get("tab") or "all").strip() or "all"
    if active_task != "all" and not scheduled_tasks.is_known_task(active_task):
        active_task = "all"
    task = (
        {
            "code": "all",
            "name": "全部日志",
            "description": "汇总所有已接入运行表的定时任务日志。",
            "schedule": "全部",
        }
        if active_task == "all"
        else scheduled_tasks.get_task_definition(active_task)
    )
    latest_run = scheduled_tasks.latest_run(active_task) if active_view == "logs" else None
    runs = scheduled_tasks.list_runs(active_task) if active_view == "logs" else []
    return render_template(
        "scheduled_tasks.html",
        tasks=scheduled_tasks.task_definitions(),
        log_filters=scheduled_tasks.log_filter_definitions(),
        management_tasks=scheduled_tasks.management_tasks(),
        active_view=active_view,
        active_task=active_task,
        task=task,
        latest_run=latest_run,
        runs=runs,
    )


@bp.post("/<task_code>/control")
@login_required
def control(task_code: str):
    if not _is_admin_single_user():
        abort(403)
    action = (request.form.get("action") or "").strip().lower()
    if action not in {"enable", "disable"}:
        abort(400)
    enabled = action == "enable"
    try:
        task = scheduled_tasks.set_task_enabled(
            task_code,
            enabled,
            actor=getattr(current_user, "username", "") or None,
            confirmation=request.form.get("confirm_task_code"),
        )
        flash(f"{task['name']} 已{'启用' if enabled else '停用'}")
    except Exception as exc:
        flash(f"定时任务控制失败：{exc}")
    return redirect(url_for("scheduled_tasks.page", view="management"))
