"""D 子系统：原始素材任务库 Blueprint。"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from appcore import raw_video_pool as rvp_svc

bp = Blueprint("raw_video_pool", __name__, url_prefix="/raw-video-pool")

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXT = (".mp4", ".mov", ".webm", ".mkv")


def _viewer_role() -> str:
    return getattr(current_user, "role", "user")


def _is_admin() -> bool:
    return _viewer_role() in ("admin", "superadmin")


@bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template(
        "raw_video_pool_list.html",
        is_admin=_is_admin(),
    )


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    result = rvp_svc.list_visible_tasks(
        viewer_user_id=int(current_user.id),
        viewer_role=_viewer_role(),
    )
    return jsonify(result)


@bp.route("/api/task/<int:tid>/download", methods=["GET"])
@login_required
def api_download(tid: int):
    try:
        path, fname = rvp_svc.stream_original_video(tid, int(current_user.id))
    except rvp_svc.PermissionDenied as e:
        return jsonify({"error": "forbidden", "detail": str(e)}), 403
    except rvp_svc.StateError as e:
        return jsonify({"error": "state_error", "detail": str(e)}), 422
    if not os.path.exists(path):
        return jsonify({"error": "file_not_found", "detail": path}), 404
    return send_file(path, as_attachment=True, download_name=fname,
                     mimetype="video/mp4")


@bp.route("/api/task/<int:tid>/upload", methods=["POST"])
@login_required
def api_upload(tid: int):
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no_file"}), 400
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file_too_large", "max_mb": 500}), 413
    if not (f.filename or "").lower().endswith(ALLOWED_EXT):
        return jsonify({"error": "unsupported_type"}), 415
    try:
        new_size = rvp_svc.replace_processed_video(
            task_id=tid,
            actor_user_id=int(current_user.id),
            uploaded_file=f,
        )
    except rvp_svc.PermissionDenied as e:
        return jsonify({"error": "forbidden", "detail": str(e)}), 403
    except rvp_svc.StateError as e:
        return jsonify({"error": "state_error", "detail": str(e)}), 422
    except Exception as e:
        return jsonify({"error": "internal", "detail": str(e)}), 500
    return jsonify({"ok": True, "new_size": new_size})
