"""多语种视频翻译蓝图：页面路由 + API。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, send_file, abort
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import task_state
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from appcore.task_recovery import recover_all_interrupted_tasks, recover_project_if_needed, recover_task_if_needed
from pipeline.alignment import build_script_segments
from web import store
from web.services import multi_pipeline_runner
from web.services.artifact_download import serve_artifact_download

log = logging.getLogger(__name__)

bp = Blueprint("multi_translate", __name__)

SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi")


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
            f"SELECT {columns} FROM projects WHERE id = %s AND type = 'multi_translate'{deleted_sql}",
            (task_id,),
        )
    return db_query_one(
        f"SELECT {columns} FROM projects WHERE id = %s AND user_id = %s AND type = 'multi_translate'{deleted_sql}",
        (task_id, current_user.id),
    )


def _multi_translate_list_scope() -> tuple[str, tuple]:
    if _is_admin_user():
        return "type = 'multi_translate' AND deleted_at IS NULL", ()
    return "user_id = %s AND type = 'multi_translate' AND deleted_at IS NULL", (current_user.id,)


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/multi-translate")
@login_required
def index():
    recover_all_interrupted_tasks()

    lang = request.args.get("lang", "").strip()
    if lang and lang not in SUPPORTED_LANGS:
        lang = ""

    if lang:
        scope_sql, scope_args = _multi_translate_list_scope()
        rows = db_query(
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "       state_json, created_at, expires_at, deleted_at "
            "FROM projects "
            f"WHERE {scope_sql} "
            "  AND JSON_EXTRACT(state_json, '$.target_lang') = %s "
            "ORDER BY created_at DESC",
            (*scope_args, lang),
        )
    else:
        scope_sql, scope_args = _multi_translate_list_scope()
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
        "multi_translate_list.html",
        projects=rows, now=datetime.now(),
        current_lang=lang,
        supported_langs=SUPPORTED_LANGS,
        retention_hours=get_retention_hours("multi_translate"),
    )


@bp.route("/multi-translate/<task_id>")
@login_required
def detail(task_id: str):
    recover_project_if_needed(task_id, "multi_translate")
    row = _query_viewable_project(task_id)
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    target_lang = state.get("target_lang", "")
    from appcore.api_keys import get_key
    translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    return render_template(
        "multi_translate_detail.html",
        project=row,
        state=state,
        target_lang=target_lang,
        translate_pref=translate_pref,
    )


@bp.route("/api/multi-translate/<task_id>/subtitle-preview", methods=["GET"])
@login_required
def subtitle_preview(task_id: str):
    row = _query_viewable_project(task_id, "id, user_id", include_deleted=False)
    if not row:
        return jsonify({"error": "Task not found"}), 404
    payload = build_multi_translate_preview_payload(task_id, row.get("user_id") or current_user.id)
    return jsonify(payload)


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/multi-translate/start", methods=["POST"])
@login_required
def upload_and_start():
    """上传视频，创建多语种翻译任务。源语言将在 ASR 后自动检测。"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    from web.upload_util import build_source_object_info, save_uploaded_video, validate_video_extension

    original_filename = os.path.basename(file.filename)
    if not validate_video_extension(original_filename):
        return jsonify({"error": "涓嶆敮鎸佺殑瑙嗛鏍煎紡"}), 400

    target_lang = (request.form.get("target_lang") or "").strip()
    if target_lang not in SUPPORTED_LANGS:
        return jsonify({"error": f"target_lang must be one of {list(SUPPORTED_LANGS)}"}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_path, file_size, content_type = save_uploaded_video(file, UPLOAD_DIR, task_id, original_filename)
    user_id = current_user.id

    store.create(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=user_id,
    )

    display_name = _resolve_name_conflict(user_id, _default_display_name(original_filename))
    store.update(
        task_id,
        display_name=display_name,
        type="multi_translate",
        target_lang=target_lang,
        source_tos_key="",
        source_object_info=build_source_object_info(
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
            storage_backend="local",
            uploaded_at=datetime.now().isoformat(timespec="seconds"),
        ),
        delivery_mode="local_primary",
    )

    # 注册源视频到 preview_files，让 artifact 端点能直接 serve 给前端预览
    store.set_preview_file(task_id, "source_video", video_path)

    multi_pipeline_runner.start(task_id, user_id=user_id)
    return jsonify({"task_id": task_id}), 201


@bp.route("/api/multi-translate/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    return jsonify({"error": "新建多语种翻译任务已切换为本地上传，请改用 multipart /api/multi-translate/start"}), 410

@bp.route("/api/multi-translate/complete", methods=["POST"])
@login_required
def complete_upload():
    return jsonify({"error": "新建多语种翻译任务已切换为本地上传，TOS complete 创建任务入口已停用"}), 410

@bp.route("/api/multi-translate/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@bp.route("/api/multi-translate/<task_id>/restart", methods=["POST"])
@login_required
def restart(task_id):
    """清上一轮产物，用新参数重跑多语种翻译流水线。"""
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
        runner=multi_pipeline_runner,
    )
    return jsonify({"status": "restarted", "task": updated})


@bp.route("/api/multi-translate/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
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
    )

    multi_pipeline_runner.start(task_id, user_id=current_user.id)
    updated_task = store.get(task_id) or task
    return jsonify({"status": "started", "task": updated_task})


@bp.route("/api/multi-translate/<task_id>/source-language", methods=["PUT"])
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


@bp.route("/api/multi-translate/<task_id>/alignment", methods=["PUT"])
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
        multi_pipeline_runner.resume(task_id, "translate", user_id=current_user.id)
    return jsonify({"status": "ok", "script_segments": script_segments})


@bp.route("/api/multi-translate/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id):
    """用户确认/编辑多语种翻译结果。"""
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
    multi_pipeline_runner.resume(task_id, "tts", user_id=current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/api/multi-translate/<task_id>/export", methods=["POST"])
@login_required
def export(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    multi_pipeline_runner.resume(task_id, "compose", user_id=current_user.id)
    return jsonify({"status": "started"})


RESUMABLE_STEPS = ["extract", "asr", "voice_match", "alignment", "translate", "tts", "subtitle", "compose", "export"]


@bp.route("/api/multi-translate/<task_id>/resume", methods=["POST"])
@login_required
def resume(task_id):
    recover_task_if_needed(task_id)
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
    multi_pipeline_runner.resume(task_id, start_step, user_id=current_user.id)
    return jsonify({"status": "started", "start_step": start_step})


@bp.route("/api/multi-translate/<task_id>/download/<file_type>")
@login_required
def download(task_id, file_type):
    """下载多语种任务产物，TOS 优先 / 本地兜底。"""
    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant", "normal")
    return serve_artifact_download(task, task_id, file_type, variant=variant)


@bp.route("/api/multi-translate/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id):
    """软删除多语种翻译任务。"""
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


@bp.route("/api/multi-translate/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id, name):
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


_ALLOWED_ROUND_KINDS = {
    "localized_translation":        ("localized_translation.round_{r}.json",       "application/json"),
    "localized_rewrite_messages":   ("localized_rewrite_messages.round_{r}.json",  "application/json"),
    "initial_translate_messages":   ("localized_translate_messages.json",          "application/json"),
    "tts_script":                   ("tts_script.round_{r}.json",                  "application/json"),
    "tts_full_audio":               ("tts_full.round_{r}.mp3",                     "audio/mpeg"),
}


@bp.route("/api/multi-translate/<task_id>/round-file/<int:round_index>/attempt/<int:attempt>")
@login_required
def get_round_attempt_file(task_id: str, round_index: int, attempt: int):
    """Serve per-rewrite-attempt intermediate translation JSON."""
    if round_index not in (1, 2, 3, 4, 5):
        abort(404)
    if attempt not in (1, 2, 3, 4, 5):
        abort(404)

    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    filename = f"localized_translation.round_{round_index}.attempt_{attempt}.json"
    path = os.path.join(task.get("task_dir", ""), filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), mimetype="application/json",
                     as_attachment=False, download_name=filename,
                     conditional=False)


@bp.route("/api/multi-translate/<task_id>/round-file/<int:round_index>/<kind>")
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    """Serve per-round intermediate artifacts."""
    if round_index not in (1, 2, 3, 4, 5):
        abort(404)
    if kind not in _ALLOWED_ROUND_KINDS:
        abort(404)

    task = _get_viewable_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    filename_pattern, mime = _ALLOWED_ROUND_KINDS[kind]
    filename = filename_pattern.format(r=round_index)
    path = os.path.join(task.get("task_dir", ""), filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), mimetype=mime,
                     as_attachment=False, download_name=filename,
                     conditional=False)


@bp.route("/api/multi-translate/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发多语种项目 AI 视频分析，不影响任务整体 status。"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    if (task.get("steps") or {}).get("analysis") == "running":
        return jsonify({"error": "AI 分析正在运行中"}), 409

    # multi_pipeline_runner does not expose run_analysis yet; placeholder
    return jsonify({"error": "analysis not supported for multi_translate"}), 501


@bp.route("/api/multi-translate/user-default-voice", methods=["PUT"])
@login_required
def set_user_default_voice_route():
    """把某条音色设为该用户 × 该语种的默认。下次新建同语种任务会置顶。"""
    body = request.get_json() or {}
    lang = (body.get("lang") or "").strip()
    voice_id = (body.get("voice_id") or "").strip()
    voice_name = (body.get("voice_name") or "").strip() or None
    if lang not in SUPPORTED_LANGS:
        return jsonify({"error": f"lang must be one of {list(SUPPORTED_LANGS)}"}), 400
    if not voice_id:
        return jsonify({"error": "voice_id required"}), 400

    from appcore.video_translate_defaults import set_user_default_voice
    set_user_default_voice(current_user.id, lang, voice_id, voice_name)
    return jsonify({"ok": True, "lang": lang, "voice_id": voice_id, "voice_name": voice_name})


@bp.route("/api/multi-translate/<task_id>/voice", methods=["PUT"])
@login_required
def update_voice(task_id: str):
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    body = request.get_json() or {}
    voice_id = body.get("voice_id")
    if not voice_id:
        return jsonify({"error": "voice_id is required"}), 400
    state["selected_voice_id"] = voice_id
    if body.get("voice_name"):
        state["selected_voice_name"] = body["voice_name"]
    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
    return jsonify({"ok": True, "voice_id": voice_id})


@bp.route("/api/multi-translate/<task_id>/voice-library", methods=["GET"])
@login_required
def voice_library_for_task(task_id: str):
    """返回任务目标语言下的所有音色（给前端滚动列表铺全库用）。"""
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    lang = state.get("target_lang")
    if not lang:
        return jsonify({"error": "task has no target_lang"}), 400

    from appcore.voice_library_browse import list_voices
    gender = request.args.get("gender") or None
    q = request.args.get("q") or None
    try:
        data = list_voices(language=lang, gender=gender, q=q,
                             page=1, page_size=500)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # 管道状态 —— 前端据此决定是否显示"等 ASR 中"
    steps = state.get("steps", {}) or {}
    pipeline = {
        "extract": steps.get("extract", "pending"),
        "asr": steps.get("asr", "pending"),
        "voice_match": steps.get("voice_match", "pending"),
    }
    # voice_match 处于 waiting 或 done 才表示候选已就绪
    vm_status = pipeline["voice_match"]
    voice_match_ready = vm_status in ("waiting", "done")

    # 默认音色：让前端把它置顶可预览 + 可选中
    # 优先用 user_voice_defaults 里用户自己设的，没有就走 resolve_default_voice 兜底
    from appcore.video_translate_defaults import resolve_default_voice
    default_voice = None
    owner_user_id = row.get("user_id") or current_user.id
    default_voice_id = resolve_default_voice(lang, user_id=owner_user_id) if lang else None
    if default_voice_id:
        row2 = db_query_one(
            "SELECT voice_id, name, gender, accent, age, descriptive, preview_url "
            "FROM elevenlabs_voices WHERE voice_id = %s LIMIT 1",
            (default_voice_id,),
        )
        if row2:
            default_voice = dict(row2)
            default_voice["description"] = row2.get("descriptive") or ""

    return jsonify({
        "items": data.get("items", []),
        "total": data.get("total", 0),
        "candidates": state.get("voice_match_candidates", []),
        "fallback_voice_id": state.get("voice_match_fallback_voice_id"),
        "selected_voice_id": state.get("selected_voice_id"),
        "pipeline": pipeline,
        "voice_match_ready": voice_match_ready,
        "default_voice": default_voice,
    })


@bp.route("/api/multi-translate/<task_id>/rematch", methods=["POST"])
@login_required
def rematch_voice(task_id: str):
    """基于前端当前筛选条件（目前：gender）重新对该子集做向量匹配，返回新 top-10。

    完全不重新抽样/embed——复用 voice_match 步骤里保存到 state 的 query embedding。
    写回 state.voice_match_candidates 让刷新页面也能看到同样结果。
    """
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    lang = state.get("target_lang")
    if not lang:
        return jsonify({"error": "task has no target_lang"}), 400

    body = request.get_json(silent=True) or {}
    gender = (body.get("gender") or "").strip().lower() or None
    if gender and gender not in {"male", "female"}:
        return jsonify({"error": "gender must be male|female|null"}), 400

    embedding_b64 = state.get("voice_match_query_embedding")
    if not embedding_b64:
        return jsonify({
            "error": "voice_match 尚未完成，无法重算；请等待向量匹配就绪"
        }), 409

    import base64
    from appcore.video_translate_defaults import resolve_default_voice
    from pipeline.voice_embedding import deserialize_embedding
    from pipeline.voice_match import match_candidates

    try:
        vec = deserialize_embedding(base64.b64decode(embedding_b64))
    except Exception:
        return jsonify({"error": "query embedding 解码失败"}), 500

    default_voice_id = resolve_default_voice(lang, user_id=current_user.id)
    candidates = match_candidates(
        vec,
        language=lang,
        gender=gender,
        top_k=10,
        exclude_voice_ids={default_voice_id} if default_voice_id else None,
    ) or []
    for c in candidates:
        c["similarity"] = float(c.get("similarity", 0.0))

    state["voice_match_candidates"] = candidates
    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
    # 同步内存态，避免其他路径读到旧值
    try:
        from appcore import task_state as _ts
        _ts.update(task_id, voice_match_candidates=candidates)
    except Exception:
        pass

    return jsonify({"ok": True, "gender": gender, "candidates": candidates})


@bp.route("/api/multi-translate/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    """用户在 UI 上选定 TTS 音色 → 写入 state + 内存 → 恢复 pipeline 跑 alignment。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)

    body = request.get_json() or {}
    voice_id = (body.get("voice_id") or "").strip()
    voice_name = (body.get("voice_name") or "").strip() or None

    state = json.loads(row["state_json"] or "{}")
    lang = state.get("target_lang")

    # voice_id 为 "default" / 空 时：走 resolve_default_voice（含用户自定义默认）
    if not voice_id or voice_id == "default":
        from appcore.video_translate_defaults import resolve_default_voice
        voice_id = resolve_default_voice(lang, user_id=current_user.id) if lang else None
        if not voice_id:
            return jsonify({"error": "no default voice available for this language"}), 400
        voice_name = voice_name or "默认音色"

    # 收可选字幕参数（都有默认值）
    subtitle_font = (body.get("subtitle_font") or "Impact").strip()
    try:
        subtitle_size = int(body.get("subtitle_size") or 14)
    except (TypeError, ValueError):
        subtitle_size = 14
    try:
        subtitle_position_y = float(body.get("subtitle_position_y") or 0.68)
    except (TypeError, ValueError):
        subtitle_position_y = 0.68
    subtitle_position = (body.get("subtitle_position") or "bottom").strip()

    # 写 DB state_json
    state["selected_voice_id"] = voice_id
    if voice_name:
        state["selected_voice_name"] = voice_name
    state["subtitle_font"] = subtitle_font
    state["subtitle_size"] = subtitle_size
    state["subtitle_position_y"] = subtitle_position_y
    state["subtitle_position"] = subtitle_position
    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )

    # 写内存 task_state
    task_state.update(
        task_id,
        selected_voice_id=voice_id,
        selected_voice_name=voice_name,
        voice_id=voice_id,  # 基类 _resolve_voice 也能兜底用
        subtitle_font=subtitle_font,
        subtitle_size=subtitle_size,
        subtitle_position_y=subtitle_position_y,
        subtitle_position=subtitle_position,
    )
    task_state.set_step(task_id, "voice_match", "done")
    task_state.set_current_review_step(task_id, "")

    # 从 alignment 恢复 pipeline
    multi_pipeline_runner.resume(task_id, "alignment", user_id=current_user.id)

    medias_context = state.get("medias_context") or {}
    parent_task_id = (medias_context.get("parent_task_id") or "").strip()
    if parent_task_id:
        try:
            from web.background import start_background_task
            from web.routes.bulk_translate import _spawn_scheduler

            start_background_task(_spawn_scheduler, parent_task_id)
        except Exception:
            log.exception("failed to resume parent bulk_translate task after voice confirm")

    return jsonify({"ok": True, "voice_id": voice_id, "voice_name": voice_name})
