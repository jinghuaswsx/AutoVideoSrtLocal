"""投放素材AI分析 — 独立蓝图。

路由前缀：/video-analyse-ai
从 medias 蓝图独立出来，避免与素材管理混在一起。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-10-功能拆分纠偏
"""
from __future__ import annotations

import re

from flask import Blueprint, abort, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import ad_material_ai_analysis as service
from web.auth import admin_required
from web.background import start_background_task

bp = Blueprint("video_analyse_ai", __name__, url_prefix="/video-analyse-ai")

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


# ── 页面路由 ──

@bp.route("/", methods=["GET"])
@login_required
@admin_required
def index_page():
    return render_template(
        "video_analyse_ai.html",
        initial_project_id=None,
    )


@bp.route("/projects/<int:project_id>", methods=["GET"])
@login_required
@admin_required
def project_page(project_id: int):
    return render_template(
        "video_analyse_ai.html",
        initial_project_id=project_id,
    )


@bp.route("/share/<share_token>", methods=["GET"])
def public_report(share_token: str):
    if not _valid_share_token(share_token):
        abort(404)
    return render_template(
        "video_analyse_ai.html",
        initial_project_id=None,
        public_mode=True,
        share_token=share_token,
        aims_layout_template="medias_ad_material_ai_analysis_public_base.html",
    )


# ── API ──

@bp.route("/api/projects", methods=["GET"])
@login_required
@admin_required
def api_projects():
    limit = request.args.get("limit", "30")
    try:
        limit_value = int(limit)
    except ValueError:
        limit_value = 30
    return _json({"success": True, "projects": service.list_projects(limit_value)})


@bp.route("/api/projects", methods=["POST"])
@login_required
@admin_required
def api_create_project():
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


@bp.route("/api/projects/<int:project_id>", methods=["GET"])
@login_required
@admin_required
def api_project(project_id: int):
    project = service.get_project(project_id)
    if not project:
        abort(404)
    return _json({"success": True, "project": project})


@bp.route("/api/projects/<int:project_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_project(project_id: int):
    result = service.delete_project(project_id)
    if result.get("deleted"):
        return _json({"success": True, "deleted": True, "project_id": project_id})
    reason = result.get("reason", "failed")
    if reason == "not_found":
        abort(404)
    if reason == "running":
        return _json({
            "success": False,
            "message": "运行中的项目不能删除",
            "reason": "running"
        }, 409)
    return _json({"success": False, "message": "删除失败", "reason": reason}, 500)


@bp.route("/api/projects/<int:project_id>/share", methods=["POST"])
@login_required
@admin_required
def api_share_project(project_id: int):
    share = service.ensure_project_share(project_id)
    if not share:
        abort(404)
    share["share_url"] = url_for(
        "video_analyse_ai.public_report",
        share_token=share["share_token"],
        _external=True,
    )
    return _json({"success": True, "share": share})


@bp.route("/api/share/<share_token>", methods=["GET"])
def api_public_project(share_token: str):
    if not _valid_share_token(share_token):
        abort(404)
    project = service.get_project_by_share_token(share_token)
    if not project:
        abort(404)
    return _json({"success": True, "project": _public_project_payload(project, share_token)})


@bp.route("/api/preview", methods=["GET"])
@login_required
@admin_required
def api_preview():
    return _json({"success": True, "preview": service.build_preview()})


@bp.route("/api/llm-payload/<int:log_id>", methods=["GET"])
@login_required
@admin_required
def api_llm_payload(log_id: int):
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
