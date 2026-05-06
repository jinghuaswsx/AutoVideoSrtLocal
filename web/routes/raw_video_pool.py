"""D 子系统：原始素材任务库 Blueprint。"""
from __future__ import annotations

import os

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from appcore import raw_video_pool as rvp_svc
from web.services.artifact_download import safe_task_file_response
from web.services.raw_video_pool import (
    build_raw_video_pool_file_not_found_response,
    build_raw_video_pool_file_too_large_response,
    build_raw_video_pool_internal_error_response,
    build_raw_video_pool_list_response,
    build_raw_video_pool_no_file_response,
    build_raw_video_pool_permission_denied_response,
    build_raw_video_pool_state_error_response,
    build_raw_video_pool_unsupported_type_response,
    build_raw_video_pool_upload_success_response,
    raw_video_pool_flask_response,
)

bp = Blueprint("raw_video_pool", __name__, url_prefix="/raw-video-pool")

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXT = (".mp4", ".mov", ".webm", ".mkv")


def _viewer_role() -> str:
    return getattr(current_user, "role", "user")


def _is_admin() -> bool:
    return _viewer_role() in ("admin", "superadmin")


def _can_process_raw_video() -> bool:
    if _is_admin():
        return True
    has_permission = getattr(current_user, "has_permission", None)
    if callable(has_permission):
        return bool(has_permission("can_process_raw_video"))
    perms = getattr(current_user, "permissions", None) or {}
    if isinstance(perms, dict):
        return bool(perms.get("can_process_raw_video"))
    return False


@bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template(
        "raw_video_pool_list.html",
        is_admin=_is_admin(),
        can_process_raw_video=_can_process_raw_video(),
    )


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    result = rvp_svc.list_visible_tasks(
        viewer_user_id=int(current_user.id),
        viewer_role=_viewer_role(),
    )
    return raw_video_pool_flask_response(build_raw_video_pool_list_response(result))


@bp.route("/api/task/<int:tid>/download", methods=["GET"])
@login_required
def api_download(tid: int):
    try:
        path, fname = rvp_svc.stream_original_video(tid, int(current_user.id))
    except rvp_svc.PermissionDenied as e:
        return raw_video_pool_flask_response(build_raw_video_pool_permission_denied_response(e))
    except rvp_svc.StateError as e:
        return raw_video_pool_flask_response(build_raw_video_pool_state_error_response(e))
    if not os.path.exists(path):
        return raw_video_pool_flask_response(build_raw_video_pool_file_not_found_response(path))
    return safe_task_file_response(
        {},
        path,
        not_found_message="file_not_found",
        as_attachment=True,
        download_name=fname,
        mimetype="video/mp4",
    )


@bp.route("/api/task/<int:tid>/upload", methods=["POST"])
@login_required
def api_upload(tid: int):
    f = request.files.get("file")
    if not f:
        return raw_video_pool_flask_response(build_raw_video_pool_no_file_response())
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return raw_video_pool_flask_response(
            build_raw_video_pool_file_too_large_response(max_mb=500)
        )
    if not (f.filename or "").lower().endswith(ALLOWED_EXT):
        return raw_video_pool_flask_response(build_raw_video_pool_unsupported_type_response())
    try:
        new_size = rvp_svc.replace_processed_video(
            task_id=tid,
            actor_user_id=int(current_user.id),
            uploaded_file=f,
        )
    except rvp_svc.PermissionDenied as e:
        return raw_video_pool_flask_response(build_raw_video_pool_permission_denied_response(e))
    except rvp_svc.StateError as e:
        return raw_video_pool_flask_response(build_raw_video_pool_state_error_response(e))
    except Exception as e:
        return raw_video_pool_flask_response(build_raw_video_pool_internal_error_response(e))
    return raw_video_pool_flask_response(build_raw_video_pool_upload_success_response(new_size))
