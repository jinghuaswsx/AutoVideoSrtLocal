"""
任务管理蓝图

负责视频上传、任务生命周期管理、翻译确认、文件下载。
不包含任何业务执行逻辑，执行逻辑在 services/pipeline_runner.py。
"""
import os
import subprocess
import uuid

from flask import Blueprint, request, jsonify, send_file, render_template, abort
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore.api_keys import resolve_jianying_project_root
from pipeline.alignment import build_script_segments
from pipeline.capcut import deploy_capcut_project, rewrite_capcut_project_paths
from web.preview_artifacts import (
    build_alignment_artifact,
    build_translate_artifact,
    build_variant_compare_artifact,
)
from web import store
from web.services import pipeline_runner
from appcore.db import query_one as db_query_one, execute as db_execute, query as db_query

bp = Blueprint("task", __name__, url_prefix="/api/tasks")


def _extract_thumbnail(video_path: str, task_dir: str) -> str | None:
    """Extract first frame as JPEG. Returns path or None on failure."""
    try:
        thumb_path = os.path.join(task_dir, "thumbnail.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-f", "image2", thumb_path],
            capture_output=True, timeout=15,
        )
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception:
        pass
    return None


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
    return render_template("index.html")


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
            "audio_extract": [f"{task_id}_audio.wav", f"{task_id}_audio.mp3"],
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


@bp.route("", methods=["POST"])
@login_required
def upload():
    """上传视频，创建任务，返回 task_id"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

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

    thumb = _extract_thumbnail(video_path, task_dir)
    if thumb and user_id is not None:
        db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))

    return jsonify({"task_id": task_id}), 201


@bp.route("/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
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

    return send_file(path, as_attachment=False)


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
        interactive_review=_parse_bool(body.get("interactive_review", False)),
    )
    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.start(task_id, user_id=user_id)
    return jsonify({"status": "started"})


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
    """下载成品文件，file_type: soft | hard | srt"""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant") or None
    variant_state = task.get("variants", {}).get(variant, {}) if variant else {}
    exports = variant_state.get("exports", {}) if variant else task.get("exports", {})
    result = variant_state.get("result", {}) if variant else task.get("result", {})
    path_map = {
        "soft": result.get("soft_video"),
        "hard": result.get("hard_video"),
        "srt": variant_state.get("srt_path") if variant else task.get("srt_path"),
        "capcut": exports.get("capcut_archive"),
    }
    path = path_map.get(file_type)
    if file_type == "capcut" and path:
        project_dir = exports.get("capcut_project")
        if project_dir and os.path.isdir(project_dir):
            manifest_path = exports.get("capcut_manifest")
            jianying_project_dir = rewrite_capcut_project_paths(
                project_dir=project_dir,
                manifest_path=manifest_path,
                archive_path=path,
                jianying_project_root=resolve_jianying_project_root(current_user.id),
            )
            updated_exports = dict(exports)
            updated_exports["jianying_project_dir"] = jianying_project_dir
            if variant:
                store.update_variant(task_id, variant, exports=updated_exports)
            else:
                store.update(task_id, exports=updated_exports)
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), as_attachment=True)


@bp.route("/<task_id>/deploy/capcut", methods=["POST"])
@login_required
def deploy_capcut(task_id):
    task = store.get(task_id)
    if not task:
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
    return jsonify({"status": "ok", "display_name": resolved})


@bp.route("/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    """软删除任务（设置 deleted_at）"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    from datetime import datetime
    db_execute(
        "UPDATE projects SET deleted_at=%s WHERE id=%s",
        (datetime.utcnow(), task_id),
    )
    store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})


RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]


@bp.route("/<task_id>/resume", methods=["POST"])
@login_required
def resume_from_step(task_id):
    """从指定步骤重新开始流水线，该步骤之前已完成的结果保留不动。"""
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
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

    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.resume(task_id, start_step, user_id=user_id)
    return jsonify({"status": "started", "start_step": start_step})
