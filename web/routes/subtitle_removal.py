from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime

import config
from flask import Blueprint, render_template, abort, jsonify, request, redirect, send_file, url_for
from flask_login import login_required, current_user

from appcore import task_state
from appcore import tos_clients
from appcore.db import execute as db_execute, query as db_query, query_one as db_query_one
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.ffutil import extract_thumbnail, probe_media_info
from web import store
from web.services import subtitle_removal_runner
from web.upload_util import validate_video_extension

bp = Blueprint("subtitle_removal", __name__)
_submit_locks: dict[str, threading.Lock] = {}
_submit_locks_guard = threading.Lock()
_upload_bootstrap_reservations: dict[str, dict] = {}
_upload_bootstrap_guard = threading.Lock()
_INFLIGHT_STEP_STATUSES = {
    "submit": {"queued", "running"},
    "poll": {"queued", "running"},
    "download_result": {"running"},
    "upload_result": {"running"},
}


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if (
        not task
        or task.get("_user_id") != current_user.id
        or task.get("type") != "subtitle_removal"
        or (task.get("status") or "").strip() == "deleted"
        or task.get("deleted_at")
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


def _get_submit_lock(task_id: str) -> threading.Lock:
    with _submit_locks_guard:
        lock = _submit_locks.get(task_id)
        if lock is None:
            lock = threading.Lock()
            _submit_locks[task_id] = lock
        return lock


def _reserve_upload_bootstrap(task_id: str, user_id: int, original_filename: str, object_key: str) -> None:
    with _upload_bootstrap_guard:
        _upload_bootstrap_reservations[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "original_filename": original_filename,
            "object_key": object_key,
        }


def _consume_upload_bootstrap(task_id: str) -> dict | None:
    with _upload_bootstrap_guard:
        return _upload_bootstrap_reservations.get(task_id)


def _release_upload_bootstrap(task_id: str) -> None:
    with _upload_bootstrap_guard:
        _upload_bootstrap_reservations.pop(task_id, None)


def _cleanup_result_artifacts(task: dict) -> None:
    result_video_path = (task.get("result_video_path") or "").strip()
    if result_video_path and os.path.isfile(result_video_path):
        try:
            os.remove(result_video_path)
        except Exception:
            pass

    result_tos_key = (task.get("result_tos_key") or "").strip()
    if result_tos_key:
        try:
            tos_clients.delete_object(result_tos_key)
        except Exception:
            pass


def _task_needs_resume(task: dict) -> bool:
    steps = task.get("steps") or {}
    submit_status = (steps.get("submit") or "").strip().lower()
    poll_status = (steps.get("poll") or "").strip().lower()
    download_status = (steps.get("download_result") or "").strip().lower()
    upload_status = (steps.get("upload_result") or "").strip().lower()
    provider_task_id = (task.get("provider_task_id") or "").strip()
    result_video_path = (task.get("result_video_path") or "").strip()

    if submit_status in _INFLIGHT_STEP_STATUSES["submit"]:
        return True
    if poll_status in _INFLIGHT_STEP_STATUSES["poll"]:
        return True
    if submit_status == "done" and poll_status == "pending" and provider_task_id:
        return True
    if download_status in _INFLIGHT_STEP_STATUSES["download_result"]:
        return True
    if download_status == "done" and upload_status == "pending" and result_video_path:
        return True
    if upload_status in _INFLIGHT_STEP_STATUSES["upload_result"]:
        return True
    return False


def resume_inflight_tasks() -> list[str]:
    restored: list[str] = []
    try:
        rows = db_query(
            """
            SELECT id, user_id, status, state_json
            FROM projects
            WHERE type = 'subtitle_removal'
              AND deleted_at IS NULL
              AND status IN ('queued', 'running', 'submitted')
            ORDER BY created_at ASC
            """,
            (),
        )
    except Exception:
        return restored

    for row in rows:
        task_id = (row.get("id") or "").strip()
        if not task_id:
            continue
        if subtitle_removal_runner.is_running(task_id):
            continue

        row_status = (row.get("status") or "").strip().lower()
        task = None
        state_json = row.get("state_json") or ""
        if state_json:
            try:
                task = json.loads(state_json)
            except Exception:
                task = None
        if not task:
            try:
                task = store.get(task_id)
            except Exception:
                task = None
        if not task or task.get("type") != "subtitle_removal":
            continue
        task_status = (task.get("status") or row_status).strip().lower()
        if task_status in {"deleted", "done", "error"} or task.get("deleted_at"):
            continue
        if not _task_needs_resume(task):
            continue
        try:
            task.setdefault("_user_id", row.get("user_id"))
            with task_state._lock:
                task_state._tasks[task_id] = task
            if subtitle_removal_runner.start(task_id, user_id=row.get("user_id")):
                restored.append(task_id)
        except Exception:
            continue

    return restored


def _submit_locked(task_id: str, task: dict, body: dict):
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

    next_steps = dict(task.get("steps") or {})
    next_steps.update(
        {
            "prepare": "done",
            "submit": "queued",
            "poll": "pending",
            "download_result": "pending",
            "upload_result": "pending",
        }
    )
    next_messages = dict(task.get("step_messages") or {})
    next_messages.setdefault("prepare", "首帧提取和媒体信息解析已完成")
    next_messages["submit"] = "等待后台提交去字幕任务"
    next_messages["poll"] = ""
    next_messages["download_result"] = ""
    next_messages["upload_result"] = ""

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
        provider_raw={},
        poll_attempts=0,
        last_polled_at=None,
        result_video_path="",
        result_tos_key="",
        result_object_info={},
        error="",
        steps=next_steps,
        step_messages=next_messages,
    )
    store.set_step(task_id, "submit", "queued")
    store.set_step_message(task_id, "submit", "等待后台提交去字幕任务")
    subtitle_removal_runner.start(task_id, user_id=current_user.id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


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
        "result_artifact_url": url_for("subtitle_removal.get_result_artifact", task_id=task_id),
        "result_download_url": url_for("subtitle_removal.download_result", task_id=task_id),
        "resume_poll_url": url_for("subtitle_removal.resume_poll", task_id=task_id),
        "resubmit_url": url_for("subtitle_removal.resubmit", task_id=task_id),
        "delete_url": url_for("subtitle_removal.delete_task", task_id=task_id),
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
    _reserve_upload_bootstrap(task_id, current_user.id, original_filename, object_key)
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
    reservation = _consume_upload_bootstrap(task_id)
    if not reservation:
        return jsonify({"error": "bootstrap reservation required"}), 403
    if reservation.get("user_id") != current_user.id:
        return jsonify({"error": "bootstrap reservation owned by another user"}), 403
    if reservation.get("original_filename") != original_filename or reservation.get("object_key") != object_key:
        return jsonify({"error": "bootstrap reservation mismatch"}), 403

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
    _release_upload_bootstrap(task_id)
    return jsonify({"task_id": task_id}), 201


@bp.route("/api/subtitle-removal/<task_id>/submit", methods=["POST"])
@login_required
def submit(task_id: str):
    lock = _get_submit_lock(task_id)
    if not lock.acquire(blocking=False):
        return jsonify({"error": "submit already in progress"}), 409

    try:
        task = _get_owned_task(task_id)
        if (task.get("status") or "").strip() != "ready":
            return jsonify({"error": "task is not ready for submit"}), 409

        body = request.get_json(silent=True) or {}
        return _submit_locked(task_id, task, body)
    finally:
        lock.release()


@bp.route("/api/subtitle-removal/<task_id>/artifact/source", methods=["GET"])
@login_required
def get_source_artifact(task_id: str):
    task = _get_owned_task(task_id)
    thumbnail_path = (task.get("thumbnail_path") or "").strip()
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        abort(404)
    return send_file(thumbnail_path, mimetype="image/jpeg")


def _result_response(task: dict, *, as_attachment: bool = False):
    result_video_path = (task.get("result_video_path") or "").strip()
    if result_video_path and os.path.exists(result_video_path):
        if as_attachment:
            download_name = f"{(task.get('display_name') or task.get('original_filename') or task.get('id') or 'subtitle-removal').strip()}.cleaned.mp4"
            return send_file(result_video_path, as_attachment=True, download_name=download_name)
        return send_file(result_video_path, mimetype="video/mp4")

    result_tos_key = (task.get("result_tos_key") or "").strip()
    if result_tos_key:
        return redirect(tos_clients.generate_signed_download_url(result_tos_key))

    # VOD provider: 产物托管在 VOD，provider_result_url 已是完整可播放 URL
    provider_result_url = (task.get("provider_result_url") or "").strip()
    if provider_result_url.startswith("http://") or provider_result_url.startswith("https://"):
        return redirect(provider_result_url)

    abort(404)


@bp.route("/api/subtitle-removal/<task_id>/artifact/result", methods=["GET"])
@login_required
def get_result_artifact(task_id: str):
    task = _get_owned_task(task_id)
    return _result_response(task, as_attachment=False)


@bp.route("/api/subtitle-removal/<task_id>/download/result", methods=["GET"])
@login_required
def download_result(task_id: str):
    task = _get_owned_task(task_id)
    return _result_response(task, as_attachment=True)


@bp.route("/api/subtitle-removal/<task_id>/resume-poll", methods=["POST"])
@login_required
def resume_poll(task_id: str):
    task = _get_owned_task(task_id)
    if (task.get("status") or "").strip() == "done":
        return jsonify({"error": "task is already finished"}), 409
    if not (task.get("provider_task_id") or "").strip():
        return jsonify({"error": "provider_task_id required"}), 400
    if subtitle_removal_runner.is_running(task_id):
        return jsonify({"error": "task is already running"}), 409
    subtitle_removal_runner.start(task_id, user_id=current_user.id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@bp.route("/api/subtitle-removal/<task_id>/resubmit", methods=["POST"])
@login_required
def resubmit(task_id: str):
    lock = _get_submit_lock(task_id)
    if not lock.acquire(blocking=False):
        return jsonify({"error": "submit already in progress"}), 409

    try:
        task = _get_owned_task(task_id)
        if (task.get("status") or "").strip() in {"queued", "running"}:
            return jsonify({"error": "task is already running"}), 409
        _cleanup_result_artifacts(task)
        body = request.get_json(silent=True) or {}
        return _submit_locked(task_id, task, body)
    finally:
        lock.release()


@bp.route("/api/subtitle-removal/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: str):
    task = _get_owned_task(task_id)
    _cleanup_result_artifacts(task)
    db_execute("UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    store.update(task_id, status="deleted", deleted_at=datetime.now().isoformat(timespec="seconds"))
    return ("", 204)
