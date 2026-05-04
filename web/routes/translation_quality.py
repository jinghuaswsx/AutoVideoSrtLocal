"""Quality-assessment API: list + manual rerun.

Mounted twice under different URL prefixes (omni / multi) so each project type's
detail page hits its own URL family.
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appcore import task_state
from appcore.db import query as db_query, query_one as db_query_one
from web.services import quality_assessment as svc

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


def _row_to_dict(row: dict) -> dict:
    out = dict(row)
    for col in ("translation_dimensions", "tts_dimensions",
                "translation_issues", "translation_highlights",
                "tts_issues", "tts_highlights",
                "prompt_input", "raw_response"):
        v = out.get(col)
        if isinstance(v, str) and v:
            try:
                out[col] = json.loads(v)
            except Exception:
                pass
    for col in ("created_at", "completed_at"):
        if out.get(col):
            out[col] = out[col].isoformat() if hasattr(out[col], "isoformat") else str(out[col])
    return out


def _list_route(project_type: str):
    def view(task_id):
        task_row = _load_task(task_id, project_type)
        if not _can_view(task_row):
            return jsonify({"error": "Task not found"}), 404
        rows = db_query(
            "SELECT * FROM translation_quality_assessments "
            "WHERE task_id=%s ORDER BY run_id DESC",
            (task_id,),
        )
        # 把 task_state.evals_invalidated_at 一并返回——前端用它把比这早的
        # assessment 视为 stale（评估的是上一轮译文，不该当本轮结果展示）。
        ts_state = task_state.get(task_id) or {}
        return jsonify({
            "assessments": [_row_to_dict(r) for r in rows],
            "task_evals_invalidated_at": ts_state.get("evals_invalidated_at"),
        })
    view.__name__ = f"list_assessments_{project_type}"
    return view


def _run_route(project_type: str):
    def view(task_id):
        if not _is_admin():
            return jsonify({"error": "admin only"}), 403
        task_row = _load_task(task_id, project_type)
        if not task_row:
            return jsonify({"error": "Task not found"}), 404
        try:
            run_id = svc.trigger_assessment(
                task_id=task_id, project_type=project_type,
                triggered_by="manual", user_id=current_user.id,
                run_in_thread=True,
            )
        except svc.AssessmentInProgressError as exc:
            return jsonify({"error": "assessment_in_progress", "run_id": exc.run_id}), 409
        return jsonify({"ok": True, "run_id": run_id})
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
