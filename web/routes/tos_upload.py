from __future__ import annotations

import os
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appcore import tos_clients
from appcore.av_translate_inputs import build_default_av_translate_inputs
from appcore.db import execute as db_execute, query_one as db_query_one
from config import (
    OUTPUT_DIR,
    TOS_BUCKET,
    TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS,
    TOS_PUBLIC_ENDPOINT,
    TOS_REGION,
    TOS_SIGNED_URL_EXPIRES,
    UPLOAD_DIR,
)
from web import store

bp = Blueprint("tos_upload", __name__, url_prefix="/api/tos-upload")


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str, exclude_task_id: str | None = None) -> str:
    base = desired_name
    candidate = base
    n = 2
    while True:
        if exclude_task_id:
            row = db_query_one(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
                (user_id, candidate, exclude_task_id),
            )
        else:
            row = db_query_one(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
                (user_id, candidate),
            )
        if not row:
            return candidate
        candidate = f"{base} ({n})"
        n += 1


@bp.route("/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    return jsonify({"error": "新建翻译任务已切换为本地上传，禁止继续走通用 TOS 直传入口"}), 410

    if not tos_clients.is_tos_configured():
        return jsonify({"error": "TOS is not configured"}), 503

    body = request.get_json(silent=True) or {}
    original_filename = os.path.basename((body.get("original_filename") or body.get("filename") or "").strip())
    if not original_filename:
        return jsonify({"error": "original_filename required"}), 400

    task_id = str(uuid.uuid4())
    object_key = tos_clients.build_source_object_key(current_user.id, task_id, original_filename)
    return jsonify(
        {
            "task_id": task_id,
            "object_key": object_key,
            "upload_url": tos_clients.generate_signed_upload_url(object_key),
            "compat_only": True,
            "message": "该 TOS 直传入口仅保留给兼容流程使用，新建任务主链路已切回本地上传。",
            "bucket": TOS_BUCKET,
            "region": TOS_REGION,
            "endpoint": TOS_PUBLIC_ENDPOINT,
            "expires_in": TOS_SIGNED_URL_EXPIRES,
            "max_object_age_seconds": TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS,
        }
    )


@bp.route("/complete", methods=["POST"])
@login_required
def complete_upload():
    return jsonify({"error": "新建翻译任务已切换为本地上传，禁止继续通过 TOS complete 创建任务"}), 410

    body = request.get_json(silent=True) or {}
    task_id = (body.get("task_id") or "").strip()
    original_filename = os.path.basename((body.get("original_filename") or "").strip())
    object_key = (body.get("object_key") or "").strip()
    content_type = (body.get("content_type") or "").strip()
    file_size = int(body.get("file_size") or 0)

    if not task_id or not original_filename or not object_key:
        return jsonify({"error": "task_id, original_filename and object_key required"}), 400

    expected_key = tos_clients.build_source_object_key(current_user.id, task_id, original_filename)
    if object_key != expected_key:
        return jsonify({"error": "object_key mismatch"}), 400
    if not tos_clients.object_exists(object_key):
        return jsonify({"error": "Uploaded object not found"}), 400

    ext = os.path.splitext(original_filename)[1].lower()
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")

    store.create(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=current_user.id,
    )

    object_head = tos_clients.head_object(object_key)
    object_size = int(getattr(object_head, "content_length", 0) or file_size or 0)

    display_name = _resolve_name_conflict(current_user.id, _default_display_name(original_filename))
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
    store.update(
        task_id,
        display_name=display_name,
        source_language="zh",
        source_tos_key=object_key,
        pipeline_version="av",
        av_translate_inputs=build_default_av_translate_inputs(),
        source_object_info={
            "file_size": object_size,
            "content_type": content_type,
            "original_filename": original_filename,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        },
        delivery_mode="pure_tos",
    )
    return jsonify({"task_id": task_id}), 201
