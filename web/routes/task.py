"""
任务管理蓝图

负责视频上传、任务生命周期管理、翻译确认、文件下载。
不包含任何业务执行逻辑，执行逻辑在 services/pipeline_runner.py。
"""
import os
import uuid
from datetime import datetime, timezone

import mimetypes

from flask import Blueprint, request, jsonify, send_file, render_template, abort, redirect, Response, make_response
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import cleanup, tos_clients
from appcore.task_recovery import recover_task_if_needed
from pipeline.alignment import build_script_segments
from pipeline.capcut import deploy_capcut_project
from web.preview_artifacts import (
    build_alignment_artifact,
    build_translate_artifact,
    build_variant_compare_artifact,
)
from web import store
from web.services import pipeline_runner
from web.services.artifact_download import serve_artifact_download
from appcore.db import query_one as db_query_one, execute as db_execute, query as db_query

bp = Blueprint("task", __name__, url_prefix="/api/tasks")


from pipeline.ffutil import extract_thumbnail as _extract_thumbnail


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "manual"}
    return bool(value)


def _default_display_name(original_filename: str) -> str:
    """取文件名（去扩展名）前10个字符作为默认展示名。"""
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str, exclude_task_id: str | None = None) -> str:
    """
    检查 desired_name 是否已被同用户其他项目占用。
    若冲突则在末尾追加 (2)、(3)… 直到不冲突。
    exclude_task_id: 重命名时排除自身。
    """
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


def _build_translate_compare_artifact(task: dict) -> dict:
    variants = dict(task.get("variants", {}))
    compare_variants = {}
    source_full_text_zh = task.get("source_full_text_zh", "")

    for variant, variant_state in variants.items():
        localized_translation = variant_state.get("localized_translation", {})
        payload = build_translate_artifact(source_full_text_zh, localized_translation)
        store.set_variant_artifact(task["id"], variant, "translate", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }

    return build_variant_compare_artifact("翻译本土化", compare_variants)


@bp.route("/upload-page", endpoint="upload_page")
@login_required
def upload_page():
    from appcore.api_keys import get_key
    translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    return render_template("index.html", translate_pref=translate_pref)


def _artifact_candidates(task_id: str, name: str, task: dict | None = None, variant: str | None = None) -> list[str]:
    task_dir = (task or {}).get("task_dir") or os.path.join(OUTPUT_DIR, task_id)
    candidates: list[str] = []

    preview_files = (
        ((task or {}).get("variants", {}).get(variant, {}).get("preview_files", {}))
        if variant
        else (task or {}).get("preview_files", {})
    )
    preview_path = preview_files.get(name)
    if preview_path:
        candidates.append(preview_path)

    if variant:
        filename_map = {
            "tts_full_audio": [f"tts_full.{variant}.mp3", f"tts_full.{variant}.wav"],
            "soft_video": [f"{task_id}_soft.{variant}.mp4"],
            "hard_video": [f"{task_id}_hard.{variant}.mp4"],
        }
    else:
        filename_map = {
            "audio_extract": [f"{task_id}_audio.mp3", f"{task_id}_audio.wav"],
            "tts_full_audio": ["tts_full.mp3", "tts_full.wav"],
            "soft_video": [f"{task_id}_soft.mp4", "soft.mp4"],
            "hard_video": [f"{task_id}_hard.mp4", "hard.mp4"],
        }

    for filename in filename_map.get(name, []):
        candidates.append(os.path.join(task_dir, filename))

    return candidates


def _resolve_artifact_path(task_id: str, name: str, task: dict | None = None, variant: str | None = None) -> str | None:
    for path in _artifact_candidates(task_id, name, task, variant=variant):
        if path and os.path.exists(path):
            return os.path.abspath(path)
    return None


def _ensure_local_source_video(task_id: str, task: dict) -> None:
    source_tos_key = (task.get("source_tos_key") or "").strip()
    video_path = (task.get("video_path") or "").strip()
    if not source_tos_key or not video_path or os.path.exists(video_path):
        return

    tos_clients.download_file(source_tos_key, video_path)
    thumb = _extract_thumbnail(video_path, task.get("task_dir") or os.path.dirname(video_path))
    if thumb:
        db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))


def _task_requires_source_sync(task: dict) -> bool:
    source_tos_key = (task.get("source_tos_key") or "").strip()
    video_path = (task.get("video_path") or "").strip()
    return bool(source_tos_key and video_path and not os.path.exists(video_path))


@bp.route("", methods=["POST"])
@login_required
def upload():
    """上传视频，创建任务，返回 task_id"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    from web.upload_util import validate_video_extension
    if not validate_video_extension(file.filename):
        return jsonify({"error": "不支持的视频格式"}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower()
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    file.save(video_path)

    user_id = current_user.id if current_user.is_authenticated else None
    store.create(task_id, video_path, task_dir,
                 original_filename=os.path.basename(file.filename),
                 user_id=user_id)

    if user_id is not None:
        default_name = _default_display_name(os.path.basename(file.filename))
        display_name = _resolve_name_conflict(user_id, default_name)
        db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
        store.update(task_id, display_name=display_name)

    thumb = _extract_thumbnail(video_path, task_dir)
    if thumb and user_id is not None:
        db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))

    return jsonify({"task_id": task_id}), 201


@bp.route("/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    recover_task_if_needed(task_id)
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@bp.route("/<task_id>/thumbnail")
@login_required
def thumbnail(task_id: str):
    row = db_query_one(
        "SELECT thumbnail_path FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row or not row.get("thumbnail_path") or not os.path.exists(row["thumbnail_path"]):
        abort(404)
    return send_file(row["thumbnail_path"], mimetype="image/jpeg")


@bp.route("/<task_id>/artifact/<name>", methods=["GET"])
@login_required
def get_artifact(task_id, name):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    variant = request.args.get("variant") or None
    path = _resolve_artifact_path(task_id, name, task, variant=variant)
    if not path:
        return jsonify({"error": "Artifact not found"}), 404

    return _send_with_range(path)


def _send_with_range(path: str):
    """Serve a file with HTTP Range support for audio/video streaming."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")

    if not range_header:
        start, end = 0, file_size - 1
        status = 200
    else:
        try:
            ranges = range_header.replace("bytes=", "").split("-")
            start = int(ranges[0]) if ranges[0] else 0
            end = int(ranges[1]) if ranges[1] else file_size - 1
        except (ValueError, IndexError):
            start, end = 0, file_size - 1
        start = max(0, start)
        end = min(end, file_size - 1)
        if start > end:
            start, end = 0, file_size - 1
            status = 200
        else:
            status = 206

    length = end - start + 1

    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(length)

    resp = Response(data, status=status, mimetype=mime, direct_passthrough=True)
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = length
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
    """配置并启动流水线"""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    store.update(
        task_id,
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        subtitle_font=body.get("subtitle_font", "Impact"),
        subtitle_size=body.get("subtitle_size", "medium"),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        interactive_review=_parse_bool(body.get("interactive_review", False)),
    )
    task = store.get(task_id) or task

    if _task_requires_source_sync(task):
        _ensure_local_source_video(task_id, task)

    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.start(task_id, user_id=user_id)
    updated_task = store.get(task_id) or task
    return jsonify({"status": "started", "task": updated_task})


@bp.route("/<task_id>/start-translate", methods=["POST"])
@login_required
def start_translate(task_id):
    """User picks model + prompt, then starts the translate step."""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    if not task.get("_translate_pre_select"):
        return jsonify({"error": "翻译步骤不在预选状态"}), 400

    body = request.get_json(silent=True) or {}
    model_provider = body.get("model_provider", "").strip()
    prompt_id = body.get("prompt_id")
    prompt_text = (body.get("prompt_text") or "").strip()

    # Resolve prompt
    if not prompt_text and prompt_id:
        row = db_query_one(
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            (prompt_id, current_user.id),
        )
        if row:
            prompt_text = row["prompt_text"]

    # Save choices to task state so runtime can read them
    updates = {"_translate_pre_select": False}
    if model_provider in ("openrouter", "doubao"):
        from appcore.api_keys import set_key
        set_key(current_user.id, "translate_pref", model_provider)
    if prompt_text:
        updates["custom_translate_prompt"] = prompt_text

    store.update(task_id, **updates)
    store.set_current_review_step(task_id, "")

    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.resume(task_id, "translate", user_id=user_id)
    return jsonify({"status": "started"})


@bp.route("/<task_id>/retranslate", methods=["POST"])
@login_required
def retranslate(task_id):
    """Re-run translation with a different prompt. Stores result alongside existing translations."""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    step_status = (task.get("steps") or {}).get("translate")
    if step_status not in ("done", "error"):
        return jsonify({"error": "翻译步骤尚未完成，无法重新翻译"}), 400

    body = request.get_json(silent=True) or {}
    prompt_text = (body.get("prompt_text") or "").strip()
    prompt_id = body.get("prompt_id")
    model_provider = body.get("model_provider", "").strip()

    if not prompt_text and prompt_id:
        row = db_query_one(
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            (prompt_id, current_user.id),
        )
        if row:
            prompt_text = row["prompt_text"]

    if not prompt_text:
        return jsonify({"error": "需要提供 prompt_text 或有效的 prompt_id"}), 400

    # Resolve provider: explicit param > user pref > default
    if model_provider not in ("openrouter", "doubao"):
        from appcore.api_keys import get_key
        model_provider = get_key(current_user.id, "translate_pref") or "openrouter"

    from pipeline.translate import generate_localized_translation
    from pipeline.localization import build_source_full_text_zh

    script_segments = task.get("script_segments") or []
    source_full_text_zh = build_source_full_text_zh(script_segments)

    try:
        result = generate_localized_translation(
            source_full_text_zh, script_segments, variant="normal",
            custom_system_prompt=prompt_text,
            provider=model_provider, user_id=current_user.id,
        )
    except Exception as exc:
        return jsonify({"error": f"翻译失败: {exc}"}), 500

    # Store as additional translation attempt
    translation_history = task.get("translation_history") or []
    translation_history.append({
        "prompt_text": prompt_text,
        "prompt_id": prompt_id,
        "model_provider": model_provider,
        "result": result,
    })
    if len(translation_history) > 3:
        translation_history = translation_history[-3:]

    store.update(task_id, translation_history=translation_history)

    return jsonify({
        "translation": result,
        "history_index": len(translation_history) - 1,
        "translation_history": translation_history,
    })


@bp.route("/<task_id>/select-translation", methods=["PUT"])
@login_required
def select_translation(task_id):
    """Select one of the translation attempts as the active translation."""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    index = body.get("index")
    if index is None:
        return jsonify({"error": "index is required"}), 400

    translation_history = task.get("translation_history") or []
    if not (0 <= index < len(translation_history)):
        return jsonify({"error": "无效的翻译索引"}), 400

    selected = translation_history[index]["result"]
    store.update_variant(task_id, "normal", localized_translation=selected)
    store.update(task_id, selected_translation_index=index)

    return jsonify({"status": "ok", "selected_index": index})


@bp.route("/<task_id>/alignment", methods=["PUT"])
@login_required
def update_alignment(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    break_after = body.get("break_after")
    if not isinstance(break_after, list):
        return jsonify({"error": "break_after required"}), 400

    try:
        script_segments = build_script_segments(task.get("utterances", []), break_after)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    store.confirm_alignment(task_id, break_after, script_segments)
    store.set_artifact(
        task_id,
        "alignment",
        build_alignment_artifact(task.get("scene_cuts", []), script_segments, break_after),
    )
    store.set_current_review_step(task_id, "")
    store.set_step(task_id, "alignment", "done")
    store.set_step_message(task_id, "alignment", "分段确认完成")

    if task.get("interactive_review"):
        # 手动确认模式：暂停让用户先选模型和提示词
        store.set_current_review_step(task_id, "translate")
        store.set_step(task_id, "translate", "waiting")
        store.set_step_message(task_id, "translate", "请选择翻译模型和提示词")
        store.update(task_id, _translate_pre_select=True)
    else:
        pipeline_runner.resume(task_id, "translate", user_id=current_user.id if current_user.is_authenticated else None)
    return jsonify({"status": "ok", "script_segments": script_segments})


@bp.route("/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id):
    """用户确认/编辑翻译结果"""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json()
    if not body or "segments" not in body:
        return jsonify({"error": "segments required"}), 400

    store.confirm_segments(task_id, body["segments"])
    updated_task = store.get(task_id) or task
    store.set_artifact(task_id, "translate", _build_translate_compare_artifact(updated_task))
    store.set_current_review_step(task_id, "")
    store.set_step(task_id, "translate", "done")
    store.set_step_message(task_id, "translate", "翻译确认完成")
    pipeline_runner.resume(task_id, "tts", user_id=current_user.id if current_user.is_authenticated else None)
    return jsonify({"status": "ok"})


@bp.route("/<task_id>/download/<file_type>", methods=["GET"])
@login_required
def download(task_id, file_type):
    """下载成品文件，file_type: soft | hard | srt | capcut。

    实际下载逻辑见 web.services.artifact_download.serve_artifact_download，
    三个翻译模块共用同一套 TOS-优先 / 本地-兜底 策略。
    """
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant") or None
    return serve_artifact_download(task, task_id, file_type, variant=variant)


@bp.route("/<task_id>/deploy/capcut", methods=["POST"])
@login_required
def deploy_capcut(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant") or None
    variant_state = task.get("variants", {}).get(variant, {}) if variant else {}
    exports = variant_state.get("exports", {}) if variant else task.get("exports", {})
    project_dir = exports.get("capcut_project")
    if not project_dir or not os.path.isdir(project_dir):
        return jsonify({"error": "CapCut project not ready"}), 404

    deployed_project_dir = deploy_capcut_project(project_dir)
    exports = dict(exports)
    exports["jianying_project_dir"] = deployed_project_dir

    if variant:
        store.update_variant(task_id, variant, exports=exports)
    else:
        store.update(task_id, exports=exports)

    return jsonify({"status": "ok", "deployed_project_dir": deployed_project_dir})


@bp.route("/<task_id>", methods=["PATCH"])
@login_required
def rename_task(task_id):
    """重命名任务展示名称"""
    row = db_query_one(
        "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    new_name = (body.get("display_name") or "").strip()
    if not new_name:
        return jsonify({"error": "display_name required"}), 400
    if len(new_name) > 50:
        return jsonify({"error": "名称不超过50个字符"}), 400

    resolved = _resolve_name_conflict(current_user.id, new_name, exclude_task_id=task_id)
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (resolved, task_id))
    store.get(task_id)
    store.update(task_id, display_name=resolved)
    return jsonify({"status": "ok", "display_name": resolved})


@bp.route("/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    """软删除任务（设置 deleted_at）"""
    row = db_query_one(
        "SELECT id, task_dir, state_json FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id) or {}
    cleanup_payload = dict(task)
    cleanup_payload["task_dir"] = row.get("task_dir") or cleanup_payload.get("task_dir", "")
    cleanup_payload["state_json"] = row.get("state_json") or ""
    cleanup_payload["tos_keys"] = cleanup.collect_task_tos_keys(cleanup_payload)
    try:
        cleanup.delete_task_storage(cleanup_payload)
    except Exception:
        pass

    db_execute(
        "UPDATE projects SET deleted_at=%s WHERE id=%s",
        (datetime.now(timezone.utc), task_id),
    )
    store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})


RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "analysis", "export"]


@bp.route("/<task_id>/resume", methods=["POST"])
@login_required
def resume_from_step(task_id):
    recover_task_if_needed(task_id)
    """从指定步骤重新开始流水线，该步骤之前已完成的结果保留不动。"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    start_step = body.get("start_step", "")
    if start_step not in RESUMABLE_STEPS:
        return jsonify({"error": f"start_step must be one of {RESUMABLE_STEPS}"}), 400

    # 把 start_step 及之后的步骤状态重置为 pending
    started = False
    for s in RESUMABLE_STEPS:
        if s == start_step:
            started = True
        if started:
            store.set_step(task_id, s, "pending")
            store.set_step_message(task_id, s, "等待中...")

    store.update(task_id, status="running", current_review_step="")
    task = store.get(task_id) or task
    _ensure_local_source_video(task_id, task)

    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.resume(task_id, start_step, user_id=user_id)
    return jsonify({"status": "started", "start_step": start_step})
