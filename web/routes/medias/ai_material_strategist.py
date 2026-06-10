"""AI素材军师页面和 API。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md
"""
from __future__ import annotations

import re

from flask import abort, current_app, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import ai_material_strategist as service
from web.auth import admin_required, permission_required
from web.background import start_background_task

from . import bp

_SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,100}$")
_PUBLIC_LINK_KEYS = {
    "url",
    "task_url",
    "video_url",
    "product_url",
    "material_url",
    "detail_url",
    "payload",
    "method",
}


def _json(payload: dict, status: int = 200):
    return jsonify(payload), status


def _current_user_id() -> int | None:
    try:
        return int(current_user.id)
    except (TypeError, ValueError):
        return None


def _valid_share_token(share_token: str) -> bool:
    return bool(_SHARE_TOKEN_RE.fullmatch(str(share_token or "")))


def _strip_public_links(value):
    if isinstance(value, list):
        return [_strip_public_links(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_public_links(item)
            for key, item in value.items()
            if key not in _PUBLIC_LINK_KEYS
        }
    return value


def _public_project_payload(project: dict, share_token: str) -> dict:
    payload = _strip_public_links(project)
    payload.pop("ranking_prompt", None)
    payload.pop("data_snapshot", None)
    payload["public"] = True

    if "products" in payload:
        for p_idx, p in enumerate(payload["products"]):
            orig_p = project["products"][p_idx]
            if "mingkong_materials" in p and "mingkong_materials" in orig_p:
                for m_idx, m in enumerate(p["mingkong_materials"]):
                    orig_m = orig_p["mingkong_materials"][m_idx]
                    orig_video_url = orig_m.get("video_url")
                    if orig_video_url:
                        if "?" in orig_video_url:
                            m["video_url"] = f"{orig_video_url}&share_token={share_token}"
                        else:
                            m["video_url"] = f"{orig_video_url}?share_token={share_token}"
            if "local_materials" in p and "local_materials" in orig_p:
                for lm_idx, lm in enumerate(p["local_materials"]):
                    orig_lm = orig_p["local_materials"][lm_idx]
                    object_key = orig_lm.get("object_key")
                    if object_key:
                        lm["video_url"] = f"/medias/obj/{object_key}"
    return payload


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


@bp.route("/ai-material-strategist/share/<share_token>", methods=["GET"])
def ai_material_strategist_public_report(share_token: str):
    if not _valid_share_token(share_token):
        abort(404)
    return render_template(
        "medias_ai_material_strategist.html",
        initial_project_id=None,
        public_mode=True,
        share_token=share_token,
        aims_layout_template="medias_ai_material_strategist_public_base.html",
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

    try:
        start_background_task(
            service.run_project,
            project_id,
            user_id=_current_user_id(),
            run_ai=run_ai,
        )
    except Exception:
        current_app.logger.warning(
            "AI material strategist create scheduling failed; project marked interrupted: project_id=%s",
            project_id,
            exc_info=True,
        )
        interrupted = service.mark_project_interrupted(
            project_id,
            reason="create_schedule_failed",
            message="后台任务未能排队，已标记为中断；请点击步骤卡片「从此步继续」。",
        )
        return _json({
            "success": False,
            "message": "后台任务未能排队，已标记为中断；请点击步骤卡片「从此步继续」。",
            "project": interrupted or service.get_project(project_id) or project,
        }, 500)
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


@bp.route("/api/ai-material-strategist/projects/<int:project_id>/resume-from-step", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_resume_from_step(project_id: int):
    payload = request.get_json(silent=True) or {}
    step_key = str(payload.get("step_key") or "").strip()
    run_ai = payload.get("run_ai", True) is not False
    sync = bool(payload.get("sync"))
    try:
        project = service.resume_project_from_step(
            project_id,
            step_key,
            user_id=_current_user_id(),
        )
    except service.ProjectAlreadyRunningError as exc:
        running = exc.project or service.get_project(project_id) or {}
        return _json({
            "success": False,
            "message": "当前 AI素材军师执行器仍在运行，请等待当前步骤结束或服务重启恢复后再从指定步骤继续。",
            "running_project": running,
            "project": running,
        }, 409)
    except ValueError as exc:
        if "不存在" in str(exc):
            return _json({"success": False, "message": str(exc)}, 404)
        return _json({"success": False, "message": str(exc)}, 400)

    if sync:
        project = service.run_project(project_id, user_id=_current_user_id(), run_ai=run_ai)
        return _json({"success": True, "project": project})

    try:
        start_background_task(
            service.run_project,
            project_id,
            user_id=_current_user_id(),
            run_ai=run_ai,
        )
    except Exception:
        current_app.logger.warning(
            "AI material strategist manual resume scheduling failed; project marked interrupted: project_id=%s",
            project_id,
            exc_info=True,
        )
        interrupted = service.mark_project_interrupted(
            project_id,
            reason="manual_resume_schedule_failed",
            message="手动从步骤继续未能排队，已标记为中断；请稍后重试。",
        )
        return _json({
            "success": False,
            "message": "手动从步骤继续未能排队，已标记为中断；请稍后重试。",
            "project": interrupted or service.get_project(project_id) or project,
        }, 500)
    return _json({"success": True, "project": project}, 202)


@bp.route("/api/ai-material-strategist/projects/<int:project_id>/resume-checkpoint", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_resume_checkpoint(project_id: int):
    payload = request.get_json(silent=True) or {}
    run_ai = payload.get("run_ai", True) is not False
    sync = bool(payload.get("sync"))
    try:
        project = service.resume_project_checkpoint(
            project_id,
            user_id=_current_user_id(),
        )
    except service.ProjectAlreadyRunningError as exc:
        running = exc.project or service.get_project(project_id) or {}
        return _json({
            "success": False,
            "message": "当前 AI素材军师执行器仍在运行，请等待当前步骤结束后再继续未完成项目。",
            "running_project": running,
            "project": running,
        }, 409)
    except ValueError as exc:
        if "不存在" in str(exc):
            return _json({"success": False, "message": str(exc)}, 404)
        return _json({"success": False, "message": str(exc)}, 400)

    if sync:
        project = service.run_project(project_id, user_id=_current_user_id(), run_ai=run_ai)
        return _json({"success": True, "project": project})

    try:
        start_background_task(
            service.run_project,
            project_id,
            user_id=_current_user_id(),
            run_ai=run_ai,
        )
    except Exception:
        current_app.logger.warning(
            "AI material strategist checkpoint resume scheduling failed; project marked interrupted: project_id=%s",
            project_id,
            exc_info=True,
        )
        interrupted = service.mark_project_interrupted(
            project_id,
            reason="checkpoint_resume_schedule_failed",
            message="继续未完成项目未能排队，已标记为中断；请稍后重试。",
        )
        return _json({
            "success": False,
            "message": "继续未完成项目未能排队，已标记为中断；请稍后重试。",
            "project": interrupted or service.get_project(project_id) or project,
        }, 500)
    return _json({"success": True, "project": project}, 202)


@bp.route("/api/ai-material-strategist/projects/<int:project_id>", methods=["DELETE"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_delete_project(project_id: int):
    result = service.delete_project(project_id)
    if result.get("deleted"):
        return _json({"success": True, **result})
    if result.get("reason") == "running":
        return _json({
            "success": False,
            "message": "运行中的 AI素材军师项目不能删除，请等待完成或失败后再删除。",
            **result,
        }, 409)
    return _json({"success": False, "message": "AI素材军师项目不存在。"}, 404)


@bp.route("/api/ai-material-strategist/projects/<int:project_id>/share", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_share_project(project_id: int):
    share = service.ensure_project_share(project_id)
    if not share:
        abort(404)
    share["share_url"] = url_for(
        "medias.ai_material_strategist_public_report",
        share_token=share["share_token"],
        _external=True,
    )
    return _json({"success": True, "share": share})


@bp.route("/api/ai-material-strategist/share/<share_token>", methods=["GET"])
def api_ai_material_strategist_public_project(share_token: str):
    if not _valid_share_token(share_token):
        abort(404)
    project = service.get_project_by_share_token(share_token)
    if not project:
        abort(404)
    return _json({"success": True, "project": _public_project_payload(project, share_token)})


@bp.route("/api/ai-material-strategist/preview", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ai_material_strategist_preview():
    return _json({"success": True, "preview": service.build_preview()})
