from __future__ import annotations

from flask import Blueprint, abort, render_template, request
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
    active_tab = (request.args.get("tab") or "shopifyid").strip() or "shopifyid"
    task = scheduled_tasks.get_task_definition(active_tab)
    active_tab = task["code"]
    return render_template(
        "scheduled_tasks.html",
        tasks=scheduled_tasks.task_definitions(),
        active_tab=active_tab,
        task=task,
        latest_run=scheduled_tasks.latest_run(active_tab),
        runs=scheduled_tasks.list_runs(active_tab),
    )
