"""Quality-assessment API: list + manual rerun.

Mounted twice under different URL prefixes (omni / multi) so each project type's
detail page hits its own URL family.
"""
from __future__ import annotations

import logging

from flask import Blueprint, request
from flask_login import current_user, login_required

from appcore import task_state
from appcore.db import query as db_query, query_one as db_query_one
from web.services import quality_assessment as svc
from web.services.translation_quality import (
    build_translation_quality_admin_only_response,
    build_translation_quality_assessment_in_progress_response,
    build_translation_quality_list_response,
    build_translation_quality_not_found_response,
    build_translation_quality_started_response,
    translation_quality_flask_response,
)

log = logging.getLogger(__name__)

bp = Blueprint("translation_quality", __name__)


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


def _can_view(task_row: dict) -> bool:
    if not task_row:
        return False
    if _is_admin():
        return True
    return int(task_row.get("user_id") or 0) == int(getattr(current_user, "id", 0))


def _load_task(task_id: str, project_type: str) -> dict | None:
    return db_query_one(
        "SELECT id, user_id, type FROM projects WHERE id=%s AND type=%s AND deleted_at IS NULL",
        (task_id, project_type),
    )


def _list_route(project_type: str):
    def view(task_id):
        task_row = _load_task(task_id, project_type)
        if not _can_view(task_row):
            return translation_quality_flask_response(
                build_translation_quality_not_found_response()
            )
        rows = db_query(
            "SELECT * FROM translation_quality_assessments "
            "WHERE task_id=%s ORDER BY run_id DESC",
            (task_id,),
        )
        # 把 task_state.evals_invalidated_at 一并返回——前端用它把比这早的
        # assessment 视为 stale（评估的是上一轮译文，不该当本轮结果展示）。
        ts_state = task_state.get(task_id) or {}
        return translation_quality_flask_response(
            build_translation_quality_list_response(
                rows=rows,
                task_evals_invalidated_at=ts_state.get("evals_invalidated_at"),
            )
        )
    view.__name__ = f"list_assessments_{project_type}"
    return view


def _run_route(project_type: str):
    def view(task_id):
        if not _is_admin():
            return translation_quality_flask_response(
                build_translation_quality_admin_only_response()
            )
        task_row = _load_task(task_id, project_type)
        if not task_row:
            return translation_quality_flask_response(
                build_translation_quality_not_found_response()
            )
        try:
            run_id = svc.trigger_assessment(
                task_id=task_id, project_type=project_type,
                triggered_by="manual", user_id=current_user.id,
                run_in_thread=True,
            )
        except svc.AssessmentInProgressError as exc:
            return translation_quality_flask_response(
                build_translation_quality_assessment_in_progress_response(
                    run_id=exc.run_id
                )
            )
        return translation_quality_flask_response(
            build_translation_quality_started_response(run_id=run_id)
        )
    view.__name__ = f"run_assessment_{project_type}"
    return view


for project_type in ("omni_translate", "multi_translate"):
    url_prefix = "/api/omni-translate" if project_type == "omni_translate" else "/api/multi-translate"
    bp.add_url_rule(
        f"{url_prefix}/<task_id>/quality-assessments",
        view_func=login_required(_list_route(project_type)),
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{url_prefix}/<task_id>/quality-assessments/run",
        view_func=login_required(_run_route(project_type)),
        methods=["POST"],
    )
