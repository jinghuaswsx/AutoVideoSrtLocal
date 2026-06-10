"""投放素材AI分析页面和 API。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-10-功能拆分纠偏
"""
from __future__ import annotations

import re

from flask import abort, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import ad_material_ai_analysis as service
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


@bp.route("/ad-material-ai-analysis", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def ad_material_ai_analysis_page():
    return render_template(
        "medias_ad_material_ai_analysis.html",
        initial_project_id=None,
    )


@bp.route("/ad-material-ai-analysis/projects/<int:project_id>", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def ad_material_ai_analysis_project_page(project_id: int):
    return render_template(
        "medias_ad_material_ai_analysis.html",
        initial_project_id=project_id,
    )


@bp.route("/ad-material-ai-analysis/share/<share_token>", methods=["GET"])
def ad_material_ai_analysis_public_report(share_token: str):
    if not _valid_share_token(share_token):
        abort(404)
    return render_template(
        "medias_ad_material_ai_analysis.html",
        initial_project_id=None,
        public_mode=True,
        share_token=share_token,
        aims_layout_template="medias_ad_material_ai_analysis_public_base.html",
    )


@bp.route("/api/ad-material-ai-analysis/projects", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ad_material_ai_analysis_projects():
    limit = request.args.get("limit", "30")
    try:
        limit_value = int(limit)
    except ValueError:
        limit_value = 30
    return _json({"success": True, "projects": service.list_projects(limit_value)})


@bp.route("/api/ad-material-ai-analysis/projects", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_ad_material_ai_analysis_create_project():
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
            "message": "已有投放素材AI分析项目正在运行，同一时间只能运行一个项目。",
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


@bp.route("/api/ad-material-ai-analysis/projects/<int:project_id>", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ad_material_ai_analysis_project(project_id: int):
    project = service.get_project(project_id)
    if not project:
        abort(404)
    return _json({"success": True, "project": project})


@bp.route("/api/ad-material-ai-analysis/projects/<int:project_id>/share", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_ad_material_ai_analysis_share_project(project_id: int):
    share = service.ensure_project_share(project_id)
    if not share:
        abort(404)
    share["share_url"] = url_for(
        "medias.ad_material_ai_analysis_public_report",
        share_token=share["share_token"],
        _external=True,
    )
    return _json({"success": True, "share": share})


@bp.route("/api/ad-material-ai-analysis/share/<share_token>", methods=["GET"])
def api_ad_material_ai_analysis_public_project(share_token: str):
    if not _valid_share_token(share_token):
        abort(404)
    project = service.get_project_by_share_token(share_token)
    if not project:
        abort(404)
    return _json({"success": True, "project": _public_project_payload(project, share_token)})


@bp.route("/api/ad-material-ai-analysis/preview", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ad_material_ai_analysis_preview():
    return _json({"success": True, "preview": service.build_preview()})


@bp.route("/api/ad-material-ai-analysis/llm-payload/<int:log_id>", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_ad_material_ai_analysis_llm_payload(log_id: int):
    import json
    from appcore import usage_log
    payload = usage_log.get_user_usage_payload(log_id, user_id=_current_user_id())
    if not payload:
        return _json({"success": False, "message": "未找到大模型调用报文记录"}, 404)

    req_data = None
    if payload.get("request_data"):
        try:
            req_data = json.loads(payload["request_data"])
        except Exception:
            req_data = payload["request_data"]

    resp_data = None
    if payload.get("response_data"):
        try:
            resp_data = json.loads(payload["response_data"])
        except Exception:
            resp_data = payload["response_data"]

    return _json({
        "success": True,
        "payload": {
            "request_data": req_data,
            "response_data": resp_data
        }
    })
