from __future__ import annotations

import json
import os
import uuid

import config
from flask import Blueprint, render_template, abort, jsonify, request, send_file, url_for
from flask_login import login_required, current_user

from appcore import tos_clients
from appcore.db import execute as db_execute, query_one as db_query_one
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.ffutil import extract_thumbnail, probe_media_info
from web import store
from web.services import subtitle_removal_runner
from web.upload_util import validate_video_extension

bp = Blueprint("subtitle_removal", __name__)


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if (
        not task
        or task.get("_user_id") != current_user.id
        or task.get("type") != "subtitle_removal"
    ):
        abort(404)
    return task


def _media_info_is_ready(media_info: dict | None) -> bool:
    info = media_info or {}
    return bool(
        int(info.get("width") or 0) > 0
        and int(info.get("height") or 0) > 0
        and float(info.get("duration") or 0.0) > 0
        and (info.get("resolution") or "").strip()
    )


def _normalize_selection_box(mode: str, selection_box: dict | None, media_info: dict) -> dict:
    try:
        width = int(media_info.get("width") or 0)
        height = int(media_info.get("height") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid media dimensions") from exc
    if width <= 0 or height <= 0:
        raise ValueError("media dimensions are required")
    if mode == "full":
        return {"x1": 0, "y1": 0, "x2": width, "y2": height}
    if not isinstance(selection_box, dict):
        raise ValueError("selection_box required for box mode")
    try:
        x1 = selection_box.get("x1")
        y1 = selection_box.get("y1")
        x2 = selection_box.get("x2")
        y2 = selection_box.get("y2")
        if x1 is None and "l" in selection_box:
            x1 = selection_box.get("l")
        if y1 is None and "t" in selection_box:
            y1 = selection_box.get("t")
        if x2 is None and "w" in selection_box and x1 is not None:
            x2 = int(x1) + int(selection_box.get("w") or 0)
        if y2 is None and "h" in selection_box and y1 is not None:
            y2 = int(y1) + int(selection_box.get("h") or 0)
        x1 = int(x1 or 0)
        y1 = int(y1 or 0)
        x2 = int(x2 or 0)
        y2 = int(y2 or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("selection_box must contain integer coordinates") from exc
    x1 = max(0, min(width, x1))
    y1 = max(0, min(height, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("selection_box must have positive width and height")
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _to_position_payload(selection_box: dict) -> dict:
    return {
        "l": selection_box["x1"],
        "t": selection_box["y1"],
        "w": selection_box["x2"] - selection_box["x1"],
        "h": selection_box["y2"] - selection_box["y1"],
    }


def _subtitle_removal_state_payload(task: dict, task_id: str | None = None) -> dict:
    task_id = task_id or task.get("id") or ""
    thumbnail_path = (task.get("thumbnail_path") or "").strip()
    payload = {
        "id": task_id,
        "type": task.get("type") or "subtitle_removal",
        "status": task.get("status") or "uploaded",
        "original_filename": task.get("original_filename") or "",
        "display_name": task.get("display_name") or "",
        "remove_mode": task.get("remove_mode") or "",
        "selection_box": task.get("selection_box"),
        "position_payload": task.get("position_payload"),
        "media_info": dict(task.get("media_info") or {}),
        "steps": dict(task.get("steps") or {}),
        "step_messages": dict(task.get("step_messages") or {}),
        "error": task.get("error") or "",
        "provider_task_id": task.get("provider_task_id") or "",
        "provider_status": task.get("provider_status") or "",
        "provider_emsg": task.get("provider_emsg") or "",
        "provider_result_url": task.get("provider_result_url") or "",
        "result_tos_key": task.get("result_tos_key") or "",
        "result_video_path": task.get("result_video_path") or "",
        "source_tos_key": task.get("source_tos_key") or "",
        "source_object_info": dict(task.get("source_object_info") or {}),
        "thumbnail_url": url_for("subtitle_removal.get_source_artifact", task_id=task_id) if thumbnail_path else "",
        "detail_url": url_for("subtitle_removal.detail_page", task_id=task_id),
        "state_api_url": url_for("subtitle_removal.get_state", task_id=task_id),
    }
    return payload


@bp.route("/subtitle-removal")
@login_required
def upload_page():
    return render_template("subtitle_removal_upload.html")


@bp.route("/subtitle-removal/<task_id>")
@login_required
def detail_page(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'subtitle_removal' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            state = {}
    return render_template(
        "subtitle_removal_detail.html",
        project=row,
        state=_subtitle_removal_state_payload(state, task_id),
        task_id=task_id,
    )


@bp.route("/api/subtitle-removal/<task_id>", methods=["GET"])
@login_required
def get_state(task_id: str):
    task = _get_owned_task(task_id)
    return jsonify(_subtitle_removal_state_payload(task, task_id))


@bp.route("/api/subtitle-removal/upload/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    if not tos_clients.is_tos_configured():
        return jsonify({"error": "TOS is not configured"}), 503

    body = request.get_json(silent=True) or {}
    original_filename = os.path.basename((body.get("original_filename") or "").strip())
    if not original_filename:
        return jsonify({"error": "original_filename required"}), 400
    if not validate_video_extension(original_filename):
        return jsonify({"error": "invalid video file type"}), 400

    task_id = str(uuid.uuid4())
    object_key = tos_clients.build_source_object_key(current_user.id, task_id, original_filename)
    return jsonify(
        {
            "task_id": task_id,
            "object_key": object_key,
            "upload_url": tos_clients.generate_signed_upload_url(object_key),
        }
    )


@bp.route("/api/subtitle-removal/upload/complete", methods=["POST"])
@login_required
def complete_upload():
    body = request.get_json(silent=True) or {}
    task_id = (body.get("task_id") or "").strip()
    original_filename = os.path.basename((body.get("original_filename") or "").strip())
    object_key = (body.get("object_key") or "").strip()
    content_type = (body.get("content_type") or "").strip()
    try:
        file_size = int(body.get("file_size") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "file_size must be an integer"}), 400

    if not task_id or not original_filename or not object_key:
        return jsonify({"error": "task_id, original_filename and object_key required"}), 400
    if not validate_video_extension(original_filename):
        return jsonify({"error": "invalid video file type"}), 400

    expected_key = tos_clients.build_source_object_key(current_user.id, task_id, original_filename)
    if object_key != expected_key:
        return jsonify({"error": "object_key mismatch"}), 400
    if not tos_clients.object_exists(object_key):
        return jsonify({"error": "Uploaded object not found"}), 400

    ext = os.path.splitext(original_filename)[1].lower()
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    source_object_info = {
        "file_size": file_size,
        "content_type": content_type,
        "original_filename": original_filename,
    }

    store.create_subtitle_removal(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=current_user.id,
    )
    store.update(
        task_id,
        source_tos_key=object_key,
        source_object_info=source_object_info,
    )

    try:
        object_head = tos_clients.head_object(object_key)
    except Exception:
        store.update(task_id, error="head object failed")
        return jsonify({"error": "Unable to inspect uploaded source object"}), 502

    object_size = int(getattr(object_head, "content_length", 0) or file_size or 0)
    source_object_info["file_size"] = object_size
    store.update(task_id, source_object_info=source_object_info)

    try:
        tos_clients.download_file(object_key, video_path)
    except Exception:
        store.update(task_id, error="download source failed")
        return jsonify({"error": "Unable to download uploaded source object"}), 502

    media_info = dict(probe_media_info(video_path) or {})
    media_info["file_size_mb"] = round(object_size / (1024 * 1024), 2) if object_size else 0.0
    thumbnail_path = extract_thumbnail(video_path, task_dir) or ""

    if not _media_info_is_ready(media_info):
        store.update(task_id, error="media probe failed", media_info=media_info)
        return jsonify({"error": "Unable to read uploaded media info"}), 422
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        store.update(task_id, error="thumbnail extraction failed", media_info=media_info)
        return jsonify({"error": "Unable to extract first frame thumbnail"}), 422

    display_name = _default_display_name(original_filename)

    store.update(
        task_id,
        status="ready",
        display_name=display_name,
        thumbnail_path=thumbnail_path,
        media_info=media_info,
    )
    store.set_step(task_id, "prepare", "done")
    store.set_step_message(task_id, "prepare", "首帧提取和媒体信息解析已完成")
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
    return jsonify({"task_id": task_id}), 201


@bp.route("/api/subtitle-removal/<task_id>/submit", methods=["POST"])
@login_required
def submit(task_id: str):
    task = _get_owned_task(task_id)
    if (task.get("status") or "").strip() != "ready":
        return jsonify({"error": "task is not ready for submit"}), 409
    body = request.get_json(silent=True) or {}
    mode = (body.get("remove_mode") or "").strip().lower()
    selection_box = body.get("selection_box")
    media_info = task.get("media_info") or {}

    if mode not in {"full", "box"}:
        return jsonify({"error": "remove_mode must be full or box"}), 400

    try:
        duration = float(media_info.get("duration") or 0.0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid media duration"}), 400
    if duration > config.SUBTITLE_REMOVAL_MAX_DURATION_SECONDS:
        return jsonify({"error": "video duration exceeds provider limit"}), 400

    try:
        normalized = _normalize_selection_box(mode, selection_box, media_info)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    store.update(
        task_id,
        status="queued",
        remove_mode=mode,
        selection_box=normalized,
        position_payload=_to_position_payload(normalized),
        provider_task_id="",
        provider_status="queued",
        provider_emsg="",
        provider_result_url="",
        result_video_path="",
        result_tos_key="",
        result_object_info={},
        error="",
    )
    store.set_step(task_id, "submit", "queued")
    store.set_step_message(task_id, "submit", "等待后台提交去字幕任务")
    subtitle_removal_runner.start(task_id, user_id=current_user.id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@bp.route("/api/subtitle-removal/<task_id>/artifact/source", methods=["GET"])
@login_required
def get_source_artifact(task_id: str):
    task = _get_owned_task(task_id)
    thumbnail_path = (task.get("thumbnail_path") or "").strip()
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        abort(404)
    return send_file(thumbnail_path, mimetype="image/jpeg")
