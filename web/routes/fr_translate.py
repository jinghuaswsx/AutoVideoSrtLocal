"""法语视频翻译蓝图：页面路由 + API。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, send_file, abort
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from pipeline.alignment import build_script_segments
from web import store
from web.services import fr_pipeline_runner

log = logging.getLogger(__name__)

bp = Blueprint("fr_translate", __name__)

from pipeline.ffutil import extract_thumbnail as _extract_thumbnail


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str) -> str:
    base = desired_name
    candidate = base
    n = 2
    while True:
        row = db_query_one(
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
            (user_id, candidate),
        )
        if not row:
            return candidate
        candidate = f"{base} ({n})"
        n += 1


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/fr-translate")
@login_required
def index():
    rows = db_query(
        """SELECT id, original_filename, display_name, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s AND type = 'fr_translate' AND deleted_at IS NULL
           ORDER BY created_at DESC""",
        (current_user.id,),
    )
    return render_template("fr_translate_list.html", projects=rows, now=datetime.now())


@bp.route("/fr-translate/<task_id>")
@login_required
def detail(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    from appcore.api_keys import get_key
    translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    return render_template(
        "fr_translate_detail.html",
        project=row,
        state=state,
        translate_pref=translate_pref,
    )


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/fr-translate/start", methods=["POST"])
@login_required
def upload_and_start():
    """上传视频，创建法语翻译任务。源语言将在 ASR 后自动检测。"""
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

    user_id = current_user.id
    store.create(task_id, video_path, task_dir,
                 original_filename=os.path.basename(file.filename),
                 user_id=user_id)

    db_execute("UPDATE projects SET type = 'fr_translate' WHERE id = %s", (task_id,))

    display_name = _resolve_name_conflict(user_id, _default_display_name(os.path.basename(file.filename)))
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
    store.update(task_id, display_name=display_name)

    thumb = _extract_thumbnail(video_path, task_dir)
    if thumb:
        db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))

    return jsonify({"task_id": task_id}), 201


@bp.route("/api/fr-translate/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@bp.route("/api/fr-translate/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    store.update(
        task_id,
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        interactive_review=body.get("interactive_review", "false") in ("true", True, "1"),
    )

    fr_pipeline_runner.start(task_id, user_id=current_user.id)
    updated_task = store.get(task_id) or task
    return jsonify({"status": "started", "task": updated_task})


@bp.route("/api/fr-translate/<task_id>/source-language", methods=["PUT"])
@login_required
def update_source_language(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    body = request.get_json(silent=True) or {}
    lang = body.get("source_language")
    if lang not in ("zh", "en"):
        return jsonify({"error": "source_language must be 'zh' or 'en'"}), 400
    store.update(task_id, source_language=lang)
    return jsonify({"status": "ok"})


@bp.route("/api/fr-translate/<task_id>/alignment", methods=["PUT"])
@login_required
def update_alignment(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    break_after = body.get("break_after")
    if not isinstance(break_after, list):
        return jsonify({"error": "break_after required"}), 400

    # Save source_language if provided (user may override auto-detection)
    source_language = body.get("source_language")
    if source_language in ("zh", "en"):
        store.update(task_id, source_language=source_language)

    from web.preview_artifacts import build_alignment_artifact
    script_segments = build_script_segments(task.get("utterances", []), break_after)
    store.confirm_alignment(task_id, break_after, script_segments)
    store.set_artifact(
        task_id, "alignment",
        build_alignment_artifact(task.get("scene_cuts", []), script_segments, break_after),
    )
    store.set_current_review_step(task_id, "")
    store.set_step(task_id, "alignment", "done")
    store.set_step_message(task_id, "alignment", "分段确认完成")

    if task.get("interactive_review"):
        store.set_current_review_step(task_id, "translate")
        store.set_step(task_id, "translate", "waiting")
        store.set_step_message(task_id, "translate", "请选择翻译模型和提示词")
        store.update(task_id, _translate_pre_select=True)
    else:
        fr_pipeline_runner.resume(task_id, "translate", user_id=current_user.id)
    return jsonify({"status": "ok", "script_segments": script_segments})


@bp.route("/api/fr-translate/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id):
    """用户确认/编辑法语翻译结果。"""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    segments = body.get("segments")
    if segments:
        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        localized_translation = dict(variant_state.get("localized_translation", {}))
        localized_translation["sentences"] = [
            {"index": seg.get("index", i), "text": seg.get("translated", ""),
             "source_segment_indices": seg.get("source_segment_indices", [i])}
            for i, seg in enumerate(segments)
        ]
        localized_translation["full_text"] = " ".join(
            s["text"] for s in localized_translation["sentences"]
        )
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        store.update(task_id, variants=variants, localized_translation=localized_translation, _segments_confirmed=True)

    store.set_current_review_step(task_id, "")
    fr_pipeline_runner.resume(task_id, "tts", user_id=current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/api/fr-translate/<task_id>/export", methods=["POST"])
@login_required
def export(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    fr_pipeline_runner.resume(task_id, "compose", user_id=current_user.id)
    return jsonify({"status": "started"})


RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]


@bp.route("/api/fr-translate/<task_id>/resume", methods=["POST"])
@login_required
def resume(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    body = request.get_json(silent=True) or {}
    start_step = body.get("start_step", "")
    if start_step not in RESUMABLE_STEPS:
        return jsonify({"error": f"start_step must be one of {RESUMABLE_STEPS}"}), 400

    started = False
    for s in RESUMABLE_STEPS:
        if s == start_step:
            started = True
        if started:
            store.set_step(task_id, s, "pending")
            store.set_step_message(task_id, s, "等待中...")

    store.update(task_id, status="running", current_review_step="")
    fr_pipeline_runner.resume(task_id, start_step, user_id=current_user.id)
    return jsonify({"status": "started", "start_step": start_step})


@bp.route("/api/fr-translate/<task_id>/download/<file_type>")
@login_required
def download(task_id, file_type):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    task_dir = task.get("task_dir") or os.path.join(OUTPUT_DIR, task_id)
    variant = request.args.get("variant", "normal")
    variant_state = (task.get("variants") or {}).get(variant, {})

    path_map = {
        "soft": variant_state.get("result", {}).get("soft_video"),
        "hard": variant_state.get("result", {}).get("hard_video"),
        "srt": variant_state.get("srt_path"),
        "capcut": variant_state.get("exports", {}).get("capcut_archive"),
    }
    path = path_map.get(file_type)
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(os.path.abspath(path), as_attachment=True)


@bp.route("/api/fr-translate/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id):
    """软删除法语翻译任务。"""
    row = db_query_one(
        "SELECT id, task_dir, state_json FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id) or {}
    from web.services import cleanup
    cleanup_payload = dict(task)
    cleanup_payload["task_dir"] = row.get("task_dir") or cleanup_payload.get("task_dir", "")
    cleanup_payload["state_json"] = row.get("state_json") or ""
    cleanup_payload["tos_keys"] = cleanup.collect_task_tos_keys(cleanup_payload)
    try:
        cleanup.delete_task_storage(cleanup_payload)
    except Exception:
        pass

    db_execute(
        "UPDATE projects SET deleted_at=NOW() WHERE id=%s",
        (task_id,),
    )
    store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})


@bp.route("/api/fr-translate/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id, name):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    preview_files = task.get("preview_files") or {}
    variant = request.args.get("variant")
    if variant:
        preview_files = (task.get("variants") or {}).get(variant, {}).get("preview_files", {})

    path = preview_files.get(name)
    if path and os.path.exists(path):
        return send_file(os.path.abspath(path))
    return jsonify({"error": "Artifact not found"}), 404
