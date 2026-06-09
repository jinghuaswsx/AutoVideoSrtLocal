"""AI素材军师页面和 API。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md
"""
from __future__ import annotations

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import ai_material_strategist as service
from web.auth import admin_required, permission_required
from web.background import start_background_task

from . import bp


def _json(payload: dict, status: int = 200):
    return jsonify(payload), status


def _current_user_id() -> int | None:
    try:
        return int(current_user.id)
    except (TypeError, ValueError):
        return None


@bp.route("/ai-material-strategist", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def ai_material_strategist_page():
    return render_template(
        "medias_ai_material_strategist.html",
        initial_project_id=None,
    )


@bp.route("/ai-material-strategist/projects/<int:project_id>", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def ai_material_strategist_project_page(project_id: int):
    return render_template(
        "medias_ai_material_strategist.html",
        initial_project_id=project_id,
    )


@bp.route("/api/ai-material-strategist/projects", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_projects():
    limit = request.args.get("limit", "30")
    try:
        limit_value = int(limit)
    except ValueError:
        limit_value = 30
    return _json({"success": True, "projects": service.list_projects(limit_value)})


@bp.route("/api/ai-material-strategist/projects", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_create_project():
    payload = request.get_json(silent=True) or {}
    run_ai = payload.get("run_ai", True) is not False
    sync = bool(payload.get("sync"))
    try:
        project = service.create_project_record(
            _current_user_id(),
            project_name=payload.get("project_name"),
        )
    except service.ProjectAlreadyRunningError as exc:
        running = exc.project or service.get_running_project() or {}
        return _json({
            "success": False,
            "message": "已有 AI素材军师项目正在运行，同一时间只能运行一个项目。",
            "running_project": running,
            "project": running,
        }, 409)
    project_id = int(project["id"])
    if sync:
        project = service.run_project(project_id, user_id=_current_user_id(), run_ai=run_ai)
        return _json({"success": True, "project": project})

    start_background_task(
        service.run_project,
        project_id,
        user_id=_current_user_id(),
        run_ai=run_ai,
    )
    return _json({"success": True, "project": project}, 202)


@bp.route("/api/ai-material-strategist/projects/<int:project_id>", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_project(project_id: int):
    project = service.get_project(project_id)
    if not project:
        abort(404)
    return _json({"success": True, "project": project})


@bp.route("/api/ai-material-strategist/preview", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_preview():
    return _json({"success": True, "preview": service.build_preview()})
