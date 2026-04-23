"""日语视频翻译蓝图：独立页面 + 日语专用 API。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from appcore import task_state
from appcore.db import execute as db_execute, query as db_query, query_one as db_query_one
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from appcore.task_recovery import recover_all_interrupted_tasks, recover_project_if_needed, recover_task_if_needed
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.alignment import build_script_segments
from web import store
from web.services import ja_pipeline_runner
from web.services.artifact_download import serve_artifact_download
from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    lookup_default_voice_row,
    normalize_confirm_voice_payload,
    resolve_round_file_entry,
)

log = logging.getLogger(__name__)

bp = Blueprint("ja_translate", __name__)

_ALLOWED_ROUND_KINDS = {
    "localized_translation": ("localized_translation.round_{r}.json", "application/json"),
    "localized_rewrite_messages": ("ja_localized_rewrite_messages.round_{r}.json", "application/json"),
    "initial_translate_messages": ("ja_localized_translate_messages.json", "application/json"),
    "tts_script": ("tts_script.round_{r}.json", "application/json"),
    "tts_full_audio": ("tts_full.ja_round_{r}.mp3", "audio/mpeg"),
}


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


def _ensure_uploaded_video_thumbnail(task_id: str, video_path: str, task_dir: str) -> str:
    if not video_path or not os.path.exists(video_path):
        return ""
    try:
        from pipeline.ffutil import extract_thumbnail

        if task_dir:
            os.makedirs(task_dir, exist_ok=True)
        thumb_path = os.path.join(task_dir, "thumbnail.jpg")
        thumb = thumb_path if os.path.exists(thumb_path) else extract_thumbnail(video_path, task_dir)
    except Exception:
        log.warning("[ja_translate] thumbnail generation failed for task %s", task_id, exc_info=True)
        return ""
    if not thumb or not os.path.exists(thumb):
        return ""
    db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))
    task = store.get(task_id)
    if task is not None:
        task["thumbnail_path"] = thumb
    return thumb


def _is_admin_user() -> bool:
    return getattr(current_user, "role", "") == "admin"


def _task_belongs_to_current_user(task: dict) -> bool:
    return str(task.get("_user_id")) == str(getattr(current_user, "id", ""))


def _can_view_task(task: dict) -> bool:
    return _task_belongs_to_current_user(task) or _is_admin_user()


def _get_viewable_task(task_id: str) -> dict | None:
    task = store.get(task_id)
    if not task or not _can_view_task(task):
        return None
    return task


def _query_viewable_project(
    task_id: str,
    columns: str = "*",
    *,
    include_deleted: bool = True,
) -> dict | None:
    deleted_sql = "" if include_deleted else " AND deleted_at IS NULL"
    if _is_admin_user():
        return db_query_one(
            f"SELECT {columns} FROM projects WHERE id = %s AND type = 'ja_translate'{deleted_sql}",
            (task_id,),
        )
    return db_query_one(
        f"SELECT {columns} FROM projects WHERE id = %s AND user_id = %s AND type = 'ja_translate'{deleted_sql}",
        (task_id, current_user.id),
    )


def _project_row_from_task(task: dict) -> dict:
    return {
        "id": task.get("id", ""),
        "user_id": task.get("_user_id"),
        "type": task.get("type", ""),
        "original_filename": task.get("original_filename", ""),
        "display_name": task.get("display_name", ""),
        "thumbnail_path": task.get("thumbnail_path", ""),
        "status": task.get("status", ""),
        "state_json": json.dumps(task, ensure_ascii=False, default=str),
        "created_at": task.get("created_at"),
        "expires_at": task.get("expires_at"),
        "deleted_at": task.get("deleted_at"),
    }


def _list_scope() -> tuple[str, tuple]:
    if _is_admin_user():
        return "type = 'ja_translate' AND deleted_at IS NULL", ()
    return "user_id = %s AND type = 'ja_translate' AND deleted_at IS NULL", (current_user.id,)


def create_ja_translate_task_from_upload(file, *, user_id: int | None = None, auto_start: bool = True) -> dict:
    """Create a Japanese translation task from an uploaded video file."""
    if file is None:
        raise ValueError("No video file")
    if not file.filename:
        raise ValueError("Empty filename")

    from web.upload_util import build_source_object_info, save_uploaded_video, validate_video_extension

    original_filename = os.path.basename(file.filename)
    if not validate_video_extension(original_filename):
        raise ValueError("不支持的视频格式")

    actual_user_id = user_id if user_id is not None else current_user.id
    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_path, file_size, content_type = save_uploaded_video(file, UPLOAD_DIR, task_id, original_filename)
    store.create(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=actual_user_id,
    )

    display_name = _resolve_name_conflict(actual_user_id, _default_display_name(original_filename))
    store.update(
        task_id,
        display_name=display_name,
        type="ja_translate",
        target_lang="ja",
        source_language="en",
        source_tos_key="",
        source_object_info=build_source_object_info(
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
            storage_backend="local",
            uploaded_at=datetime.now().isoformat(timespec="seconds"),
        ),
        delivery_mode="local_primary",
        pipeline_version="ja",
    )

    store.set_preview_file(task_id, "source_video", video_path)
    _ensure_uploaded_video_thumbnail(task_id, video_path, task_dir)

    if auto_start:
        ja_pipeline_runner.start(task_id, user_id=actual_user_id)

    return {
        "task_id": task_id,
        "redirect_url": f"/ja-translate/{task_id}",
        "task": store.get(task_id),
    }


@bp.route("/ja-translate")
@login_required
def index():
    recover_all_interrupted_tasks()
    scope_sql, scope_args = _list_scope()
    rows = db_query(
        "SELECT id, original_filename, display_name, thumbnail_path, status, "
        "       state_json, created_at, expires_at, deleted_at "
        "FROM projects "
        f"WHERE {scope_sql} "
        "ORDER BY created_at DESC",
        scope_args,
    )
    from appcore.settings import get_retention_hours

    return render_template(
        "ja_translate_list.html",
        projects=rows,
        now=datetime.now(),
        retention_hours=get_retention_hours("ja_translate"),
    )


@bp.route("/ja-translate/<task_id>")
@login_required
def detail(task_id: str):
    recover_project_if_needed(task_id, "ja_translate")
    row = _query_viewable_project(task_id)
    state = {}
    if row and row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    if not row:
        task = _get_viewable_task(task_id)
        if task and task.get("type") == "ja_translate":
            row = _project_row_from_task(task)
            state = dict(task)
    if not row:
        abort(404)
    return render_template(
        "ja_translate_detail.html",
        project=row,
        state=state,
        target_lang="ja",
        translate_pref="ja_translate.localize",
    )


@bp.route("/api/ja-translate/start", methods=["POST"])
@login_required
def upload_and_start():
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    try:
        result = create_ja_translate_task_from_upload(request.files["video"], user_id=current_user.id, auto_start=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"task_id": result["task_id"], "redirect_url": result["redirect_url"]}), 201


@bp.route("/api/ja-translate/<task_id>/subtitle-preview", methods=["GET"])
@login_required
def subtitle_preview(task_id: str):
    row = _query_viewable_project(task_id, "id, user_id", include_deleted=False)
    if not row:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(build_multi_translate_preview_payload(task_id, row.get("user_id") or current_user.id))


@bp.route("/api/ja-translate/<task_id>/voice-library", methods=["GET"])
@login_required
def voice_library_for_task(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")

    from appcore.voice_library_browse import list_voices

    try:
        data = list_voices(
            language="ja",
            gender=request.args.get("gender") or None,
            q=request.args.get("q") or None,
            page=1,
            page_size=500,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    owner_user_id = row.get("user_id") or current_user.id
    default_voice = lookup_default_voice_row("ja", owner_user_id)
    payload = build_voice_library_payload(
        state=state,
        owner_user_id=owner_user_id,
        items=data.get("items", []),
        total=data.get("total", 0),
        default_voice=default_voice,
    )
    return jsonify(payload)


@bp.route("/api/ja-translate/<task_id>", methods=["GET"])
@login_required
def get_task(task_id: str):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@bp.route("/api/ja-translate/<task_id>/rematch", methods=["POST"])
@login_required
def rematch_voice(task_id: str):
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)

    state = json.loads(row["state_json"] or "{}")
    body = request.get_json(silent=True) or {}
    gender = (body.get("gender") or "").strip().lower() or None
    if gender and gender not in {"male", "female"}:
        return jsonify({"error": "gender must be male|female|null"}), 400

    embedding_b64 = state.get("voice_match_query_embedding")
    if not embedding_b64:
        return jsonify({"error": "voice_match 尚未完成，暂时不能重算候选音色"}), 409

    import base64
    from appcore.video_translate_defaults import resolve_default_voice
    from pipeline.voice_embedding import deserialize_embedding
    from pipeline.voice_match import match_candidates

    try:
        vec = deserialize_embedding(base64.b64decode(embedding_b64))
    except Exception:
        return jsonify({"error": "query embedding 解码失败"}), 500

    default_voice_id = resolve_default_voice("ja", user_id=current_user.id)
    candidates = match_candidates(
        vec,
        language="ja",
        gender=gender,
        top_k=10,
        exclude_voice_ids={default_voice_id} if default_voice_id else None,
    ) or []
    for candidate in candidates:
        candidate["similarity"] = float(candidate.get("similarity", 0.0))

    state["voice_match_candidates"] = candidates
    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
    task_state.update(task_id, voice_match_candidates=candidates)
    return jsonify({"ok": True, "gender": gender, "candidates": candidates})


@bp.route("/api/ja-translate/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)

    state = json.loads(row["state_json"] or "{}")

    from appcore.video_translate_defaults import resolve_default_voice

    try:
        normalized = normalize_confirm_voice_payload(
            body=request.get_json() or {},
            lang="ja",
            default_voice_id=resolve_default_voice("ja", user_id=current_user.id),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    state["selected_voice_id"] = normalized["voice_id"]
    if normalized["voice_name"]:
        state["selected_voice_name"] = normalized["voice_name"]
    state["subtitle_font"] = normalized["subtitle_font"]
    state["subtitle_size"] = normalized["subtitle_size"]
    state["subtitle_position_y"] = normalized["subtitle_position_y"]
    state["subtitle_position"] = normalized["subtitle_position"]
    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )

    task_state.update(
        task_id,
        selected_voice_id=normalized["voice_id"],
        selected_voice_name=normalized["voice_name"],
        voice_id=normalized["voice_id"],
        subtitle_font=normalized["subtitle_font"],
        subtitle_size=normalized["subtitle_size"],
        subtitle_position_y=normalized["subtitle_position_y"],
        subtitle_position=normalized["subtitle_position"],
    )
    task_state.set_step(task_id, "voice_match", "done")
    task_state.set_current_review_step(task_id, "")
    ja_pipeline_runner.resume(task_id, "alignment", user_id=current_user.id)

    medias_context = state.get("medias_context") or {}
    parent_task_id = (medias_context.get("parent_task_id") or "").strip()
    if parent_task_id:
        try:
            from web.background import start_background_task
            from web.routes.bulk_translate import _spawn_scheduler

            start_background_task(_spawn_scheduler, parent_task_id)
        except Exception:
            log.exception("failed to resume parent bulk_translate task after voice confirm")

    return jsonify({"ok": True, "voice_id": normalized["voice_id"], "voice_name": normalized["voice_name"]})


@bp.route("/api/ja-translate/<task_id>/start", methods=["POST"])
@login_required
def start(task_id: str):
    recover_task_if_needed(task_id)
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
        subtitle_size=body.get("subtitle_size", 14),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        interactive_review=body.get("interactive_review", "false") in ("true", True, "1"),
        target_lang="ja",
        source_language=body.get("source_language") if body.get("source_language") in ("zh", "en") else task.get("source_language", "en"),
    )
    ja_pipeline_runner.start(task_id, user_id=current_user.id)
    return jsonify({"status": "started", "task": store.get(task_id) or task})


@bp.route("/api/ja-translate/<task_id>/restart", methods=["POST"])
@login_required
def restart(task_id: str):
    recover_task_if_needed(task_id)
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    from web.services.task_restart import restart_task

    updated = restart_task(
        task_id,
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        voice_gender=body.get("voice_gender", "male"),
        subtitle_font=body.get("subtitle_font", "Impact"),
        subtitle_size=body.get("subtitle_size", 14),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        subtitle_position=body.get("subtitle_position", "bottom"),
        interactive_review=body.get("interactive_review", "false") in ("true", True, "1"),
        user_id=current_user.id,
        runner=ja_pipeline_runner,
    )
    store.update(task_id, target_lang="ja", source_language=task.get("source_language", "en"))
    return jsonify({"status": "restarted", "task": updated})


@bp.route("/api/ja-translate/<task_id>/source-language", methods=["PUT"])
@login_required
def update_source_language(task_id: str):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    body = request.get_json(silent=True) or {}
    lang = body.get("source_language")
    if lang not in ("zh", "en"):
        return jsonify({"error": "source_language must be 'zh' or 'en'"}), 400
    store.update(task_id, source_language=lang)
    return jsonify({"status": "ok"})


@bp.route("/api/ja-translate/<task_id>/alignment", methods=["PUT"])
@login_required
def update_alignment(task_id: str):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    break_after = body.get("break_after")
    if not isinstance(break_after, list):
        return jsonify({"error": "break_after required"}), 400

    source_language = body.get("source_language")
    if source_language in ("zh", "en"):
        store.update(task_id, source_language=source_language)

    from web.preview_artifacts import build_alignment_artifact

    script_segments = build_script_segments(task.get("utterances", []), break_after)
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
        store.set_current_review_step(task_id, "translate")
        store.set_step(task_id, "translate", "waiting")
        store.set_step_message(task_id, "translate", "请选择日语翻译设置")
        store.update(task_id, _translate_pre_select=True)
    else:
        ja_pipeline_runner.resume(task_id, "translate", user_id=current_user.id)
    return jsonify({"status": "ok", "script_segments": script_segments})


@bp.route("/api/ja-translate/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id: str):
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
        sentences = []
        for i, seg in enumerate(segments):
            sentences.append(
                {
                    "index": seg.get("index", i),
                    "asr_index": (seg.get("source_segment_indices") or [i])[0],
                    "text": seg.get("translated", ""),
                    "source_segment_indices": seg.get("source_segment_indices", [i]),
                }
            )
        localized_translation["sentences"] = sentences
        localized_translation["full_text"] = "".join(s["text"] for s in sentences)
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        store.update(task_id, variants=variants, localized_translation=localized_translation, _segments_confirmed=True)

    store.set_current_review_step(task_id, "")
    ja_pipeline_runner.resume(task_id, "tts", user_id=current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/api/ja-translate/<task_id>/export", methods=["POST"])
@login_required
def export(task_id: str):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    ja_pipeline_runner.resume(task_id, "compose", user_id=current_user.id)
    return jsonify({"status": "started"})


RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]


@bp.route("/api/ja-translate/<task_id>/resume", methods=["POST"])
@login_required
def resume(task_id: str):
    recover_task_if_needed(task_id)
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    body = request.get_json(silent=True) or {}
    start_step = body.get("start_step", "")
    if start_step not in RESUMABLE_STEPS:
        return jsonify({"error": f"start_step must be one of {RESUMABLE_STEPS}"}), 400

    started = False
    for step in RESUMABLE_STEPS:
        if step == start_step:
            started = True
        if started:
            store.set_step(task_id, step, "pending")
            store.set_step_message(task_id, step, "等待中...")

    store.update(task_id, status="running", current_review_step="")
    ja_pipeline_runner.resume(task_id, start_step, user_id=current_user.id)
    return jsonify({"status": "started", "start_step": start_step})


@bp.route("/api/ja-translate/<task_id>/download/<file_type>")
@login_required
def download(task_id: str, file_type: str):
    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    variant = request.args.get("variant", "normal")
    return serve_artifact_download(task, task_id, file_type, variant=variant)


@bp.route("/api/ja-translate/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
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

    db_execute("UPDATE projects SET deleted_at=NOW() WHERE id=%s", (task_id,))
    store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})


@bp.route("/api/ja-translate/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id: str, name: str):
    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant") or None
    from web.services.artifact_download import preview_artifact_tos_redirect

    tos_resp = preview_artifact_tos_redirect(task, name, variant=variant)
    if tos_resp is not None:
        return tos_resp

    preview_files = task.get("preview_files") or {}
    if variant:
        preview_files = (task.get("variants") or {}).get(variant, {}).get("preview_files", {})
    path = preview_files.get(name)
    if path and os.path.exists(path):
        return send_file(os.path.abspath(path))
    return jsonify({"error": "Artifact not found"}), 404


@bp.route("/api/ja-translate/<task_id>/round-file/<int:round_index>/<kind>")
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    try:
        filename, mime = resolve_round_file_entry(_ALLOWED_ROUND_KINDS, round_index, kind)
    except KeyError:
        abort(404)

    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    path = os.path.join(task.get("task_dir", ""), filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(
        os.path.abspath(path),
        mimetype=mime,
        as_attachment=False,
        download_name=filename,
        conditional=False,
    )


@bp.route("/api/ja-translate/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id: str):
    if not _query_viewable_project(task_id, "id", include_deleted=False):
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"error": "analysis not supported for ja_translate"}), 501
