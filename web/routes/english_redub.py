"""英语视频重新配音蓝图：页面路由 + API。"""
from __future__ import annotations

import copy
import json
import logging
import mimetypes
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, render_template, request, send_file, abort
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import task_state, medias, translation_route_store
from appcore.audio_loudness import validate_loudness_profile
from appcore.runtime_english_redub import (
    ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG,
    SCRIPT_MODE_ORIGINAL,
    VALID_SCRIPT_MODES,
    normalize_script_mode,
)
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from appcore.project_state import save_project_state, update_project_state
from appcore.task_recovery import recover_all_interrupted_tasks, recover_project_if_needed, recover_task_if_needed
from appcore.voice_ai_rank_cache import (
    VOICE_AI_RANK_NOT_RUN_STATUS,
    apply_cached_rank_result,
    cache_rank_result,
    derive_rank_result_from_all_cache,
    ensure_current_rank_cached,
    force_speed_fallback_rank_state,
    get_cached_rank_result,
    normalize_rank_condition,
    set_active_unranked_candidates,
)
from appcore.voice_ai_selection_settings import is_voice_ai_auto_select_enabled
from pipeline.alignment import build_script_segments
from pipeline.languages.registry import (
    SOURCE_LANGS as ALLOWED_SOURCE_LANGUAGES,
    SUPPORTED_LANGS,
    normalize_enabled_target_langs,
)
from web import store
from web.services import english_redub_pipeline_runner
from web.services.artifact_download import serve_artifact_download
from web.services.llm_debug import build_llm_debug_payload
from web.services.omni_preset_annotation import build_plugin_config_annotation
from web.services.translate_step_reset import build_step_resume_reset_updates
from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    normalize_confirm_voice_payload,
    resolve_round_file_entry,
)
from web.services.translate_route_responses import (
    build_translate_route_payload_response,
    translate_route_flask_response,
)
from web.upload_util import (
    build_source_object_info,
    client_filename_basename,
    save_uploaded_video,
    validate_video_extension,
)
from web.auth import admin_required, permission_required

log = logging.getLogger(__name__)

bp = Blueprint("english_redub", __name__)

db_query = translation_route_store.query
db_query_one = translation_route_store.query_one
db_execute = translation_route_store.execute

_PROJECT_STATE_COLUMNS = (
    "id, user_id, original_filename, display_name, task_dir, state_json"
)


def _json_response(payload: dict, status_code: int = 200):
    return translate_route_flask_response(
        build_translate_route_payload_response(payload, status_code)
    )


def _list_enabled_target_langs() -> tuple[str, ...]:
    """English redub is fixed English-in / English-out."""
    return ("en",)


def _list_filter_langs() -> tuple[str, ...]:
    """English redub projects only have English as source and target."""
    return ("en",)


def _omni_pipeline_steps_for_task(
    task_id: str,
    task: dict | None,
    *,
    include_analysis: bool = False,
) -> list[str]:
    from appcore.runtime_english_redub import EnglishRedubRunner

    cfg = (task or {}).get("plugin_config") or ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG
    try:
        return EnglishRedubRunner.pipeline_step_names_for_config(
            cfg,
            include_analysis=include_analysis,
        )
    except Exception:
        log.warning("[english_redub] invalid task plugin_config, using default steps task=%s", task_id, exc_info=True)
        return EnglishRedubRunner.pipeline_step_names_for_config(
            ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG,
            include_analysis=include_analysis,
        )


def _post_asr_step(step_names: list[str]) -> str:
    for step in step_names:
        if step in {"asr_clean", "asr_normalize"}:
            return step
    raise ValueError("omni pipeline missing post-ASR step")


def _reset_steps_from(task_id: str, step_names: list[str], start_step: str) -> None:
    started = False
    for step in step_names:
        if step == start_step:
            started = True
        if started:
            store.set_step(task_id, step, "pending")
            store.set_step_message(task_id, step, "等待中...")


def _resume_cleanup_updates(task: dict, step_names: list[str], start_step: str) -> dict:
    updates = build_step_resume_reset_updates(task, step_names, start_step)
    if start_step in {"asr_clean", "asr_normalize"}:
        source_language = (task.get("source_language") or "en").strip()
        if source_language not in ALLOWED_SOURCE_LANGUAGES:
            raise ValueError(
                f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"
            )
        updates.update(source_language=source_language, user_specified_source_language=True)
    return updates


def _applied_loudness_profile(task: dict) -> tuple[str | None, int | None]:
    tl = ((task.get("separation") or {}).get("tts_loudness") or {})
    applied_profile = tl.get("profile")
    applied_manual_pct = tl.get("manual_boost_pct")
    return applied_profile, applied_manual_pct


def _loudness_profile_needs_resume(
    *,
    selected_profile: str,
    selected_manual_pct: int | None,
    applied_profile: str | None,
    applied_manual_pct: int | None,
) -> bool:
    if applied_profile != selected_profile:
        return True
    if selected_profile == "manual_boost":
        return applied_manual_pct != selected_manual_pct
    return False


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str) -> str:
    base = desired_name
    candidate = base
    n = 2
    while True:
        row = translation_route_store.find_project_by_display_name(
            user_id,
            candidate,
            query_one_func=db_query_one,
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
        log.warning("[english_redub] thumbnail generation failed for task %s", task_id, exc_info=True)
        return ""

    if not thumb or not os.path.exists(thumb):
        return ""

    translation_route_store.set_project_thumbnail_path(
        task_id,
        "english_redub",
        thumb,
        execute_func=db_execute,
    )
    task = store.get(task_id)
    if task is not None:
        task["thumbnail_path"] = thumb
    return thumb


def _task_from_project_row(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        task = json.loads(row.get("state_json") or "{}")
    except Exception:
        task = {}
    if row.get("user_id") is not None:
        task["_user_id"] = row.get("user_id")
    for key in ("id", "original_filename", "display_name", "task_dir"):
        if row.get(key) and not task.get(key):
            task[key] = row[key]
    return task


def _fresh_viewable_project_task(task_id: str) -> dict | None:
    try:
        row = _query_viewable_project(task_id, _PROJECT_STATE_COLUMNS)
    except Exception:
        log.warning("[english_redub] fresh project state lookup failed task=%s", task_id, exc_info=True)
        return None
    task = _task_from_project_row(row)
    if not task or not _can_view_task(task):
        return None
    return task


def _hydrate_task_state_cache(task_id: str, task: dict) -> None:
    if store is not task_state:
        return
    with task_state._lock:
        task_state._tasks[task_id] = copy.deepcopy(task)


def _copy_source_video_for_duplicate(
    *,
    source_video_path: str,
    task_id: str,
    original_filename: str,
) -> tuple[str, int, str]:
    ext = os.path.splitext(original_filename or source_video_path)[1].lower()
    if not ext:
        ext = os.path.splitext(source_video_path)[1].lower() or ".mp4"

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    destination = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    shutil.copy2(source_video_path, destination)

    try:
        from appcore import tos_backup_storage

        tos_backup_storage.ensure_remote_copy_for_local_path(destination)
    except Exception:
        log.warning(
            "[english_redub] TOS backup sync failed after duplicate source copy: %s",
            destination,
            exc_info=True,
        )

    content_type = mimetypes.guess_type(original_filename or destination)[0] or "application/octet-stream"
    return destination, os.path.getsize(destination), content_type


def _duplicate_display_name(task: dict, row: dict) -> str:
    original_filename = task.get("original_filename") or row.get("original_filename") or ""
    base = task.get("display_name") or row.get("display_name") or _default_display_name(original_filename)
    return f"{base} 副本"


def _is_superadmin_user() -> bool:
    return getattr(current_user, "is_superadmin", False)


def _is_admin_user() -> bool:
    return getattr(current_user, "is_admin", False)


def _task_belongs_to_current_user(task: dict) -> bool:
    return str(task.get("_user_id")) == str(getattr(current_user, "id", ""))


def _can_view_task(task: dict) -> bool:
    if _task_belongs_to_current_user(task) or _is_admin_user():
        return True
    return bool(task.get("visible_to_all"))


def _get_viewable_task(task_id: str) -> dict | None:
    fresh_task = _fresh_viewable_project_task(task_id)
    if fresh_task:
        _hydrate_task_state_cache(task_id, fresh_task)
        return fresh_task

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
    return translation_route_store.get_viewable_project(
        task_id,
        "english_redub",
        user_id=current_user.id,
        is_admin=_is_admin_user(),
        columns=columns,
        include_deleted=include_deleted,
        query_one_func=db_query_one,
    )


def _multi_translate_creator_name_expr() -> str:
    try:
        return medias._media_product_owner_name_expr()
    except Exception:
        log.warning("[english_redub] resolve creator name expr failed; fallback to username", exc_info=True)
        return "u.username"


@bp.before_request
def _require_english_redub_permission():
    if not current_user.is_authenticated:
        return None
    if not current_user.has_permission("english_redub"):
        if request.path.startswith("/api/english-redub"):
            return _json_response({"error": "Forbidden"}, 403)
        abort(403)
    return None


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/english-redub")
@login_required
@permission_required("english_redub")
def index():
    recover_all_interrupted_tasks()

    filter_langs = _list_filter_langs()
    lang = request.args.get("lang", "").strip()
    if lang and lang not in filter_langs:
        lang = ""

    owner_name_expr = _multi_translate_creator_name_expr()

    rows = translation_route_store.list_projects_with_creator(
        user_id=current_user.id,
        project_type="english_redub",
        is_admin=_is_superadmin_user(),
        owner_name_expr=owner_name_expr,
        target_lang=lang,
        query_func=db_query,
    )
    for row in rows:
        try:
            state = json.loads(row.get("state_json") or "{}")
        except Exception:
            state = {}
        row["source_lang"] = state.get("source_language") or "en"
        row["target_lang"] = state.get("target_lang") or "en"
        row["video_duration"] = state.get("video_duration")

    from appcore.settings import get_retention_hours
    return render_template(
        "english_redub_list.html",
        projects=rows, now=datetime.now(),
        current_lang=lang,
        filter_langs=filter_langs,
        supported_langs=_list_enabled_target_langs(),
        retention_hours=get_retention_hours("english_redub"),
    )


@bp.route("/english-redub/<task_id>")
@login_required
@permission_required("english_redub")
def detail(task_id: str):
    recover_project_if_needed(task_id, "english_redub")
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
    pipeline_main_steps = _omni_pipeline_steps_for_task(
        task_id,
        state,
        include_analysis=False,
    )
    pipeline_step_order = _omni_pipeline_steps_for_task(
        task_id,
        state,
        include_analysis=True,
    )
    return render_template(
        "english_redub_detail.html",
        project=row,
        state=state,
        target_lang=target_lang,
        translate_pref=translate_pref,
        pipeline_main_steps=pipeline_main_steps,
        pipeline_step_order=pipeline_step_order,
        plugin_config_annotation=build_plugin_config_annotation(task_id, state),
    )


@bp.route("/api/english-redub/<task_id>/subtitle-preview", methods=["GET"])
@login_required
def subtitle_preview(task_id: str):
    row = _query_viewable_project(task_id, "id, user_id", include_deleted=False)
    if not row:
        return _json_response({"error": "Task not found"}, 404)
    payload = build_multi_translate_preview_payload(
        task_id, row.get("user_id") or current_user.id,
        api_base="/api/english-redub",
    )
    return _json_response(payload)


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/english-redub/start", methods=["POST"])
@login_required
def upload_and_start():
    """上传英文视频，创建 English redub 任务。输入和输出语言固定为英语。"""
    if "video" not in request.files:
        return _json_response({"error": "No video file"}, 400)
    file = request.files["video"]
    if not file.filename:
        return _json_response({"error": "Empty filename"}, 400)

    original_filename = client_filename_basename(file.filename)
    if not validate_video_extension(original_filename):
        return _json_response({"error": "涓嶆敮鎸佺殑瑙嗛鏍煎紡"}, 400)

    raw_script_mode = (request.form.get("script_mode") or "").strip().lower()
    if raw_script_mode and raw_script_mode not in VALID_SCRIPT_MODES:
        return _json_response({"error": "script_mode must be original|rewrite"}, 400)
    script_mode = normalize_script_mode(raw_script_mode or SCRIPT_MODE_ORIGINAL)

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

    desired_name = (request.form.get("display_name") or "").strip()[:200]
    base_name = desired_name or _default_display_name(original_filename)
    display_name = _resolve_name_conflict(user_id, base_name)
    update_kwargs = dict(
        display_name=display_name,
        type="english_redub",
        target_lang="en",
        source_language="en",
        user_specified_source_language=True,
        script_mode=script_mode,
        plugin_config=dict(ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG),
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
    store.update(task_id, **update_kwargs)

    # 注册源视频到 preview_files，让 artifact 端点能直接 serve 给前端预览
    store.set_preview_file(task_id, "source_video", video_path)
    _ensure_uploaded_video_thumbnail(task_id, video_path, task_dir)

    english_redub_pipeline_runner.start(task_id, user_id=user_id)
    return _json_response({"task_id": task_id}, 201)


@bp.route("/api/english-redub/<task_id>/duplicate", methods=["POST"])
@login_required
def duplicate(task_id: str):
    """复制一个全能视频翻译项目，使用独立源视频文件重新跑。"""
    row = _query_viewable_project(
        task_id,
        "id, user_id, original_filename, display_name, task_dir, state_json",
        include_deleted=False,
    )
    if not row:
        return _json_response({"error": "Task not found"}, 404)

    row_task = _task_from_project_row(row)
    source_task = copy.deepcopy(store.get(task_id) or {})
    if source_task:
        for key, value in row_task.items():
            source_task.setdefault(key, value)
    else:
        source_task = row_task

    source_video_path = (source_task.get("video_path") or "").strip()
    if not source_video_path:
        return _json_response({"error": "源视频缺失，无法复制项目。"}, 409)

    if not os.path.exists(source_video_path):
        try:
            from web.services.task_source_video import ensure_local_source_video

            ensure_local_source_video(task_id, source_task)
        except FileNotFoundError as exc:
            return _json_response({"error": str(exc)}, 409)

    if not os.path.exists(source_video_path):
        return _json_response({"error": f"源视频缺失: {source_video_path}"}, 409)

    original_filename = (
        source_task.get("original_filename")
        or row.get("original_filename")
        or os.path.basename(source_video_path)
    )
    new_task_id = str(uuid.uuid4())
    new_task_dir = os.path.join(OUTPUT_DIR, new_task_id)
    os.makedirs(new_task_dir, exist_ok=True)

    try:
        new_video_path, file_size, content_type = _copy_source_video_for_duplicate(
            source_video_path=source_video_path,
            task_id=new_task_id,
            original_filename=original_filename,
        )
    except OSError as exc:
        log.exception("[english_redub] duplicate source copy failed task=%s", task_id)
        return _json_response({"error": f"复制源视频失败: {exc}"}, 500)

    user_id = current_user.id
    store.create(
        new_task_id,
        new_video_path,
        new_task_dir,
        original_filename=original_filename,
        user_id=user_id,
    )

    from web.upload_util import build_source_object_info

    display_name = _resolve_name_conflict(
        user_id,
        _duplicate_display_name(source_task, row),
    )
    update_kwargs = dict(
        display_name=display_name,
        type="english_redub",
        target_lang="en",
        source_language="en",
        user_specified_source_language=True,
        script_mode=normalize_script_mode(source_task.get("script_mode")),
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
    for key in (
        "plugin_config",
        "voice_gender",
        "voice_id",
        "subtitle_position",
        "subtitle_font",
        "subtitle_size",
        "subtitle_position_y",
        "interactive_review",
        "loudness_profile",
        "loudness_manual_boost_pct",
    ):
        if key in source_task:
            update_kwargs[key] = copy.deepcopy(source_task[key])

    store.update(new_task_id, **update_kwargs)
    store.set_preview_file(new_task_id, "source_video", new_video_path)
    _ensure_uploaded_video_thumbnail(new_task_id, new_video_path, new_task_dir)

    english_redub_pipeline_runner.start(new_task_id, user_id=user_id)
    return _json_response({
        "status": "started",
        "task_id": new_task_id,
        "redirect_url": f"/english-redub/{new_task_id}",
    }, 201)


@bp.route("/api/english-redub/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    return _json_response({"error": "新建多语种翻译任务已切换为本地上传，请改用 multipart /api/english-redub/start"}, 410)

@bp.route("/api/english-redub/complete", methods=["POST"])
@login_required
def complete_upload():
    return _json_response({"error": "新建多语种翻译任务已切换为本地上传，TOS complete 创建任务入口已停用"}, 410)

@bp.route("/api/english-redub/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    return _json_response(task)


@bp.route("/api/english-redub/<task_id>/llm-debug/<step>", methods=["GET"])
@login_required
def get_llm_debug(task_id: str, step: str):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    payload = build_llm_debug_payload(task, step)
    if not payload:
        return _json_response({"error": "LLM debug data not found"}, 404)
    return _json_response(payload)


@bp.route("/api/english-redub/<task_id>/restart", methods=["POST"])
@login_required
def restart(task_id):
    """清上一轮产物，用新参数重跑多语种翻译流水线。"""
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id

    body = request.get_json(silent=True) or {}
    raw_source_language = body.get("source_language", None)
    if raw_source_language is not None:
        raw_source_language = str(raw_source_language).strip()
        if raw_source_language != "en":
            return _json_response({"error": "source_language must be en for English redub"}, 400)
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
        source_language="en",
        user_id=owner_id,
        runner=english_redub_pipeline_runner,
    )
    return _json_response({"status": "restarted", "task": updated})


@bp.route("/api/english-redub/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id

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

    english_redub_pipeline_runner.start(task_id, user_id=owner_id)
    updated_task = store.get(task_id) or task
    return _json_response({"status": "started", "task": updated_task})


@bp.route("/api/english-redub/<task_id>/source-language", methods=["PUT"])
@login_required
def update_source_language(task_id):
    """改写源语言并从当前 Omni 配置的 ASR 后处理步骤重跑。"""
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id
    body = request.get_json(silent=True) or {}
    raw_lang = (body.get("source_language") or "").strip()
    if raw_lang != "en":
        return _json_response({"error": "source_language must be en for English redub"}, 400)
    new_lang = "en"

    step_names = _omni_pipeline_steps_for_task(task_id, task)
    start_step = _post_asr_step(step_names)
    reset_task = dict(task)
    reset_task["source_language"] = new_lang
    try:
        updates = _resume_cleanup_updates(reset_task, step_names, start_step)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)
    updates.update(source_language=new_lang, user_specified_source_language=True)
    store.update(task_id, **updates)

    _reset_steps_from(task_id, step_names, start_step)

    english_redub_pipeline_runner.resume(task_id, start_step, user_id=owner_id)
    return _json_response({
        "status": "started",
        "source_language": new_lang,
        "user_specified_source_language": True,
    })


@bp.route("/api/english-redub/<task_id>/alignment", methods=["PUT"])
@login_required
def update_alignment(task_id):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id

    body = request.get_json(silent=True) or {}
    break_after = body.get("break_after")
    if not isinstance(break_after, list):
        return _json_response({"error": "break_after required"}, 400)

    source_language = body.get("source_language")
    if source_language and source_language != "en":
        return _json_response({"error": "source_language must be en for English redub"}, 400)
    store.update(task_id, source_language="en", user_specified_source_language=True)

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
        english_redub_pipeline_runner.resume(task_id, "translate", user_id=owner_id)
    return _json_response({"status": "ok", "script_segments": script_segments})


@bp.route("/api/english-redub/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id):
    """用户确认/编辑多语种翻译结果。"""
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id

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
        # 用户改了译文，已有的 QA / AI Review 评估都对应旧译文，标记为过期。
        from datetime import datetime, timezone
        store.update(task_id, variants=variants, localized_translation=localized_translation,
                     _segments_confirmed=True,
                     evals_invalidated_at=datetime.now(timezone.utc).isoformat())

    store.set_current_review_step(task_id, "")
    english_redub_pipeline_runner.resume(task_id, "tts", user_id=owner_id)
    return _json_response({"status": "ok"})


@bp.route("/api/english-redub/<task_id>/export", methods=["POST"])
@login_required
def export(task_id):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id
    english_redub_pipeline_runner.resume(task_id, "compose", user_id=owner_id)
    return _json_response({"status": "started"})


RESUMABLE_STEPS = [
    "extract",
    "asr",
    "separate",
    "asr_clean",
    "asr_normalize",
    "voice_match",
    "alignment",
    "shot_decompose",
    "translate",
    "tts",
    "av_sync_audit",
    "loudness_match",
    "subtitle",
    "compose",
    "export",
]


@bp.route("/api/english-redub/<task_id>/loudness-profile", methods=["POST"])
@login_required
def set_loudness_profile(task_id):
    if not _is_admin_user():
        return _json_response({"error": "仅管理员可操作"}, 403)
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    body = request.get_json(silent=True)
    if request.is_json and body is None:
        return _json_response({"error": "invalid JSON body"}, 400)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return _json_response({"error": "JSON body must be an object"}, 400)

    try:
        profile, manual_pct = validate_loudness_profile(
            body.get("profile"),
            body.get("manual_boost_pct"),
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    applied_profile, applied_manual_pct = _applied_loudness_profile(task)
    store.update(
        task_id,
        loudness_profile=profile,
        loudness_manual_boost_pct=manual_pct,
    )
    return _json_response({
        "status": "ok",
        "profile": profile,
        "manual_boost_pct": manual_pct,
        "applied_profile": applied_profile,
        "applied_manual_boost_pct": applied_manual_pct,
        "needs_resume": _loudness_profile_needs_resume(
            selected_profile=profile,
            selected_manual_pct=manual_pct,
            applied_profile=applied_profile,
            applied_manual_pct=applied_manual_pct,
        ),
    })


@bp.route("/api/english-redub/<task_id>/resume", methods=["POST"])
@login_required
def resume(task_id):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id
    body = request.get_json(silent=True) or {}
    start_step = body.get("start_step", "")
    step_names = _omni_pipeline_steps_for_task(task_id, task)
    if start_step not in step_names:
        return _json_response({
            "error": f"start_step {start_step!r} must be one of {step_names}",
            "steps": step_names,
        }, 400)

    try:
        updates = _resume_cleanup_updates(task, step_names, start_step)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    store.update(task_id, **updates)
    _reset_steps_from(task_id, step_names, start_step)
    english_redub_pipeline_runner.resume(task_id, start_step, user_id=owner_id)
    return _json_response({"status": "started", "start_step": start_step})


@bp.route("/api/english-redub/<task_id>/download/<file_type>")
@login_required
def download(task_id, file_type):
    """下载多语种任务产物，TOS 优先 / 本地兜底。"""
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    variant = request.args.get("variant", "normal")
    return serve_artifact_download(task, task_id, file_type, variant=variant)


# AI 视频分析（手动触发，多模态 ADC 通道）—— 与 multi_translate 共用同一个
# service 与 DB 表，只是 source_type='english_redub_task' 单独归类。
@bp.route("/api/english-redub/<task_id>/video-ai-review/run", methods=["POST"])
@login_required
def run_video_ai_review(task_id):
    if not _get_viewable_task(task_id):
        return _json_response({"error": "Task not found"}, 404)
    from appcore import video_ai_review
    try:
        run_id = video_ai_review.trigger_review(
            source_type="english_redub_task",
            source_id=task_id,
            user_id=current_user.id,
            triggered_by="manual",
        )
    except video_ai_review.ReviewInProgressError as exc:
        return _json_response({
            "error": "AI 视频分析正在运行中",
            "in_flight_run_id": exc.run_id,
        }, 409)
    except Exception as exc:
        log.exception("[video-ai-review] omni trigger failed task=%s", task_id)
        return _json_response({"error": str(exc)}, 500)
    return _json_response({
        "status": "started", "run_id": run_id,
        "channel": video_ai_review.CHANNEL,
        "model": video_ai_review.MODEL,
    })


@bp.route("/api/english-redub/<task_id>/video-ai-review", methods=["GET"])
@login_required
def get_video_ai_review(task_id):
    if not _get_viewable_task(task_id):
        return _json_response({"error": "Task not found"}, 404)
    from appcore import video_ai_review, task_state
    payload = video_ai_review.latest_review("english_redub_task", task_id)
    ts_state = task_state.get(task_id) or {}
    return _json_response({
        "review": payload,
        "task_evals_invalidated_at": ts_state.get("evals_invalidated_at"),
    })


@bp.route("/api/english-redub/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id):
    """软删除多语种翻译任务。"""
    row = translation_route_store.get_active_project_storage(
        task_id,
        current_user.id,
        "english_redub",
        query_one_func=db_query_one,
    )
    if not row:
        return _json_response({"error": "Task not found"}, 404)

    task = store.get(task_id) or {}
    from appcore import cleanup
    cleanup_payload = dict(task)
    cleanup_payload["task_dir"] = row.get("task_dir") or cleanup_payload.get("task_dir", "")
    cleanup_payload["state_json"] = row.get("state_json") or ""
    cleanup_payload["tos_keys"] = cleanup.collect_task_tos_keys(cleanup_payload)
    try:
        cleanup.delete_task_storage(cleanup_payload)
    except Exception:
        pass

    translation_route_store.soft_delete_project(
        task_id,
        current_user.id,
        "english_redub",
        execute_func=db_execute,
    )
    store.update(task_id, status="deleted")
    return _json_response({"status": "ok"})


@bp.route("/api/english-redub/<task_id>/visible-to-all", methods=["PUT"])
@login_required
def toggle_visible_to_all(task_id: str):
    if not getattr(current_user, "is_superadmin", False):
        return _json_response({"error": "仅超级管理员可操作"}, 403)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    body = request.get_json(silent=True) or {}
    value = bool(body.get("visible_to_all", False))
    update_project_state(task_id, {"visible_to_all": value}, query_one_func=db_query_one)
    store.update(task_id, visible_to_all=value)
    return _json_response({"visible_to_all": value})


@bp.route("/api/english-redub/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id, name):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    variant = request.args.get("variant") or None

    from web.services.artifact_download import (
        preview_artifact_tos_redirect,
        safe_task_file_response,
    )
    tos_resp = preview_artifact_tos_redirect(task, name, variant=variant)
    if tos_resp is not None:
        return tos_resp

    preview_files = task.get("preview_files") or {}
    if variant:
        preview_files = (task.get("variants") or {}).get(variant, {}).get("preview_files", {})

    path = preview_files.get(name)
    if not path and name in {"separation_vocals", "separation_accompaniment"}:
        # 兼容上线前跑过的老任务：从 task["separation"] 直接读分离结果路径
        sep = task.get("separation") or {}
        path = sep.get("vocals_path") if name == "separation_vocals" else sep.get("accompaniment_path")
    if path:
        return safe_task_file_response(task, path)
    return _json_response({"error": "Artifact not found"}, 404)


@bp.route("/api/english-redub/<task_id>/artifact-path")
@login_required
def get_artifact_path(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    from web.services.artifact_download import safe_task_relative_file_response

    return safe_task_relative_file_response(task, request.args.get("path"))


_ALLOWED_ROUND_KINDS = {
    "localized_translation":        ("localized_translation.round_{r}.json",       "application/json"),
    "localized_rewrite_messages":   ("localized_rewrite_messages.round_{r}.json",  "application/json"),
    "initial_translate_messages":   ("localized_translate_messages.json",          "application/json"),
    "tts_script":                   ("tts_script.round_{r}.json",                  "application/json"),
    "tts_full_audio":               ("tts_full.round_{r}.mp3",                     "audio/mpeg"),
}


@bp.route("/api/english-redub/<task_id>/round-file/<int:round_index>/attempt/<int:attempt>")
@login_required
def get_round_attempt_file(task_id: str, round_index: int, attempt: int):
    """Serve per-rewrite-attempt intermediate translation JSON."""
    if round_index not in (1, 2, 3, 4, 5):
        abort(404)
    if attempt not in (1, 2, 3, 4, 5):
        abort(404)

    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    filename = f"localized_translation.round_{round_index}.attempt_{attempt}.json"
    path = os.path.join(task.get("task_dir", ""), filename)
    from web.services.artifact_download import safe_task_file_response
    return safe_task_file_response(
        task,
        path,
        not_found_message="File not ready",
        mimetype="application/json",
        as_attachment=False,
        download_name=filename,
        conditional=False,
    )


@bp.route("/api/english-redub/<task_id>/round-file/<int:round_index>/<kind>")
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    """Serve per-round intermediate artifacts."""
    try:
        filename, mime = resolve_round_file_entry(_ALLOWED_ROUND_KINDS, round_index, kind)
    except KeyError:
        abort(404)

    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    path = os.path.join(task.get("task_dir", ""), filename)
    from web.services.artifact_download import safe_task_file_response
    return safe_task_file_response(
        task,
        path,
        not_found_message="File not ready",
        mimetype=mime,
        as_attachment=False,
        download_name=filename,
        conditional=False,
    )


@bp.route("/api/english-redub/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发英语重新配音项目 AI 视频分析，不影响任务整体 status。"""
    row = translation_route_store.get_active_project_id(
        task_id,
        current_user.id,
        "english_redub",
        query_one_func=db_query_one,
    )
    if not row:
        return _json_response({"error": "Task not found"}, 404)

    task = store.get(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    if (task.get("steps") or {}).get("analysis") == "running":
        return _json_response({"error": "AI 分析正在运行中"}, 409)

    owner_id = task.get("_user_id") or current_user.id
    if not english_redub_pipeline_runner.run_analysis(task_id, user_id=owner_id):
        return _json_response({"error": "AI 分析正在运行中"}, 409)

    return _json_response({"status": "started"})


@bp.route("/api/english-redub/user-default-voice", methods=["PUT"])
@login_required
def set_user_default_voice_route():
    """把某条音色设为该用户 × 该语种的默认。下次新建同语种任务会置顶。"""
    body = request.get_json() or {}
    lang = (body.get("lang") or "").strip()
    voice_id = (body.get("voice_id") or "").strip()
    voice_name = (body.get("voice_name") or "").strip() or None
    if lang not in SUPPORTED_LANGS:
        return _json_response({"error": f"lang must be one of {list(SUPPORTED_LANGS)}"}, 400)
    if not voice_id:
        return _json_response({"error": "voice_id required"}, 400)

    from appcore.video_translate_defaults import set_user_default_voice
    set_user_default_voice(current_user.id, lang, voice_id, voice_name)
    return _json_response({"ok": True, "lang": lang, "voice_id": voice_id, "voice_name": voice_name})


@bp.route("/api/english-redub/<task_id>/voice", methods=["PUT"])
@login_required
def update_voice(task_id: str):
    row = _query_viewable_project(task_id, "state_json")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    body = request.get_json() or {}
    voice_id = body.get("voice_id")
    if not voice_id:
        return _json_response({"error": "voice_id is required"}, 400)
    state["selected_voice_id"] = voice_id
    if body.get("voice_name"):
        state["selected_voice_name"] = body["voice_name"]
    save_project_state(task_id, state, execute_func=db_execute)
    return _json_response({"ok": True, "voice_id": voice_id})


def _candidate_audio_path(raw_path: object, task_dir: Path) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    candidates = [path]
    if not path.is_absolute() and str(task_dir):
        candidates.append(task_dir / path)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _resolve_voice_ai_source_audio_path(state: dict) -> Path | None:
    task_dir = Path(str(state.get("task_dir") or ""))
    debug = state.get("voice_ai_rank_debug") or {}
    request_debug = debug.get("request") if isinstance(debug, dict) else {}
    visual = (request_debug or {}).get("visual") if isinstance(request_debug, dict) else {}
    media = visual.get("media") if isinstance(visual, dict) else []
    for item in media if isinstance(media, list) else []:
        if not isinstance(item, dict) or item.get("role") != "source_sample":
            continue
        for key in ("path", "relative_path"):
            path = _candidate_audio_path(item.get(key), task_dir)
            if path:
                return path

    for raw_path in (
        task_dir / "voice_ai_ranking" / "source_sample.mp3",
        (state.get("separation") or {}).get("vocals_path")
        if isinstance(state.get("separation"), dict) else None,
        state.get("voice_match_sample_audio_path"),
    ):
        path = _candidate_audio_path(raw_path, task_dir)
        if path:
            return path
    return None


def _voice_ai_rank_state_updates(state: dict) -> dict:
    return {
        "voice_match_candidates": state.get("voice_match_candidates") or [],
        "voice_ai_rankings": state.get("voice_ai_rankings") or [],
        "voice_ai_rank_status": state.get("voice_ai_rank_status") or "",
        "voice_ai_rank_model": state.get("voice_ai_rank_model") or "",
        "voice_ai_rank_provider": state.get("voice_ai_rank_provider") or "",
        "voice_ai_rank_candidate_limit": state.get("voice_ai_rank_candidate_limit"),
        "voice_ai_rank_debug": state.get("voice_ai_rank_debug"),
        "voice_ai_rank_usage_log_id": state.get("voice_ai_rank_usage_log_id"),
        "voice_ai_rank_candidate_signature": state.get("voice_ai_rank_candidate_signature"),
        "voice_ai_rank_active_key": state.get("voice_ai_rank_active_key") or "all",
        "voice_ai_rank_cache": state.get("voice_ai_rank_cache") or {},
    }


def _voice_ai_rank_response_fields(state: dict, *, cached: bool) -> dict:
    return {
        "voice_ai_rankings": state.get("voice_ai_rankings") or [],
        "voice_ai_rank_status": state.get("voice_ai_rank_status") or "",
        "voice_ai_rank_model": state.get("voice_ai_rank_model") or "",
        "voice_ai_rank_provider": state.get("voice_ai_rank_provider") or "",
        "voice_ai_rank_debug": state.get("voice_ai_rank_debug"),
        "voice_ai_rank_usage_log_id": state.get("voice_ai_rank_usage_log_id"),
        "voice_ai_auto_select_enabled": is_voice_ai_auto_select_enabled(),
        "voice_ai_rank_cache_key": state.get("voice_ai_rank_active_key") or "all",
        "voice_ai_rank_cached": cached,
        "candidate_limit": state.get("voice_ai_rank_candidate_limit"),
        "candidates": state.get("voice_match_candidates") or [],
    }


@bp.route("/api/english-redub/<task_id>/voice-ai-ranking", methods=["POST"])
@login_required
@admin_required
def rerun_voice_ai_ranking(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    body = request.get_json(silent=True) or {}
    rank_key = normalize_rank_condition(
        body.get("gender") if "gender" in body else state.get("voice_ai_rank_active_key")
    )
    ensure_current_rank_cached(state, "all")
    candidates = state.get("voice_match_candidates") or []
    if not candidates:
        return _json_response({"error": "voice_match_candidates is empty"}, 400)

    cached_entry = get_cached_rank_result(state, rank_key, candidates)
    if cached_entry:
        apply_cached_rank_result(state, rank_key, cached_entry)
        save_project_state(task_id, state, execute_func=db_execute)
        task_state.update(task_id, **_voice_ai_rank_state_updates(state))
        return _json_response({
            "ok": True,
            **_voice_ai_rank_response_fields(state, cached=True),
        })

    source_audio_path = _resolve_voice_ai_source_audio_path(state)
    if not source_audio_path:
        return _json_response({"error": "voice_ai_source_audio_not_found"}, 400)

    from appcore.voice_ai_ranking import rank_voice_candidates

    ai_result = rank_voice_candidates(
        task_id=task_id,
        task=state,
        candidates=candidates,
        source_audio_path=source_audio_path,
        task_dir=state.get("task_dir") or "",
        user_id=row.get("user_id") or current_user.id,
        candidate_limit=body.get("candidate_limit"),
    )
    updated_candidates = ai_result.get("candidates") or candidates
    rankings = ai_result.get("rankings") or []
    status = ai_result.get("status") or "done"
    model = ai_result.get("model")
    provider = ai_result.get("provider")
    candidate_limit = ai_result.get("candidate_limit")
    debug = ai_result.get("debug")
    usage_log_id = ai_result.get("usage_log_id")

    cache_rank_result(
        state,
        key=rank_key,
        candidates=updated_candidates,
        rankings=rankings,
        status=status,
        model=model,
        provider=provider,
        debug=debug,
        candidate_limit=candidate_limit,
        usage_log_id=usage_log_id,
    )
    save_project_state(task_id, state, execute_func=db_execute)
    task_state.update(task_id, **_voice_ai_rank_state_updates(state))

    return _json_response({
        "ok": True,
        **_voice_ai_rank_response_fields(state, cached=False),
    })


@bp.route("/api/english-redub/<task_id>/voice-ai-ranking/force-speed-fallback", methods=["POST"])
@login_required
@admin_required
def force_voice_speed_fallback(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    body = request.get_json(silent=True) or {}
    rank_key = normalize_rank_condition(
        body.get("gender") if "gender" in body else state.get("voice_ai_rank_active_key")
    )
    force_speed_fallback_rank_state(state, key=rank_key)

    save_project_state(task_id, state, execute_func=db_execute)
    task_state.update(task_id, **_voice_ai_rank_state_updates(state))

    return _json_response({
        "ok": True,
        **_voice_ai_rank_response_fields(state, cached=False),
    })


@bp.route("/api/english-redub/<task_id>/voice-library", methods=["GET"])
@login_required
def voice_library_for_task(task_id: str):
    """Return the full voice-library payload for the shared detail shell."""
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    lang = state.get("target_lang")
    if not lang:
        return _json_response({"error": "task has no target_lang"}, 400)

    from appcore.voice_library_browse import list_voices

    gender = request.args.get("gender") or None
    q = request.args.get("q") or None
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(200, int(request.args.get("page_size") or 30)))
    except (TypeError, ValueError):
        page_size = 30
    try:
        data = list_voices(
            language=lang,
            gender=gender,
            q=q,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    owner_user_id = row.get("user_id") or current_user.id
    voice_ai_rank_status_before = state.get("voice_ai_rank_status")
    payload = build_voice_library_payload(
        state=state,
        owner_user_id=owner_user_id,
        items=data.get("items", []),
        total=data.get("total", 0),
        page=data.get("page", page),
        page_size=data.get("page_size", page_size),
    )
    if state.get("voice_ai_rank_status") != voice_ai_rank_status_before:
        save_project_state(task_id, state, execute_func=db_execute)
    return _json_response(payload)


@bp.route("/api/english-redub/<task_id>/rematch", methods=["POST"])

@login_required
def rematch_voice(task_id: str):
    """基于前端当前筛选条件（目前：gender）重新对该子集做向量匹配，返回新 top-10。

    完全不重新抽样/embed——复用 voice_match 步骤里保存到 state 的 query embedding。
    写回 state.voice_match_candidates 让刷新页面也能看到同样结果。
    """
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    owner_user_id = row.get("user_id") or current_user.id
    is_owner = str(owner_user_id) == str(current_user.id)
    lang = state.get("target_lang")
    if not lang:
        return _json_response({"error": "task has no target_lang"}, 400)

    body = request.get_json(silent=True) or {}
    gender = (body.get("gender") or "").strip().lower() or None
    if gender and gender not in {"male", "female"}:
        return _json_response({"error": "gender must be male|female|null"}, 400)
    rank_key = normalize_rank_condition(gender)
    ensure_current_rank_cached(state, "all")

    embedding_b64 = state.get("voice_match_query_embedding")
    if not embedding_b64:
        return _json_response({
            "error": "voice_match 尚未完成，无法重算；请等待向量匹配就绪"
        }, 409)

    import base64
    from appcore.video_translate_defaults import resolve_default_voice
    from appcore.voice_library_browse import fetch_voices_by_ids
    from pipeline.voice_embedding import deserialize_embedding
    from appcore import english_redub_settings

    try:
        vec = deserialize_embedding(base64.b64decode(embedding_b64))
    except Exception:
        return _json_response({"error": "query embedding 解码失败"}, 500)

    # 用 owner 的默认音色排除规则，保证 admin 浏览时算出的候选与 owner 看到的一致
    default_voice_id = resolve_default_voice(lang, user_id=owner_user_id)
    if english_redub_settings.get_voice_match_strategy() == english_redub_settings.STRATEGY_TIMBRE_SPEED:
        from pipeline.voice_match_speed import match_candidates_speed_aware

        candidates = match_candidates_speed_aware(
            vec,
            language=lang,
            source_utterances=state.get("utterances") or [],
            gender=gender,
            candidate_pool_size=20,
            top_k=20,
            exclude_voice_ids={default_voice_id} if default_voice_id else None,
        ) or []
    else:
        from pipeline.voice_match import match_candidates

        candidates = match_candidates(
            vec,
            language=lang,
            gender=gender,
            top_k=10,
            exclude_voice_ids={default_voice_id} if default_voice_id else None,
        ) or []
    for c in candidates:
        c["similarity"] = float(c.get("similarity", 0.0))

    # 拉这些候选音色的完整行返回给前端，让它合并进 allItems。
    # 否则筛性别后的新候选可能不在前端 list_voices 拿到的前 200 里，
    # join 失败 → 用户看到 0 个推荐。
    candidate_ids = [c["voice_id"] for c in candidates if c.get("voice_id")]
    extra_items = (
        fetch_voices_by_ids(language=lang, voice_ids=candidate_ids)
        if candidate_ids else []
    )
    cached_entry = get_cached_rank_result(state, rank_key, candidates)
    cached = cached_entry is not None
    if cached_entry:
        apply_cached_rank_result(state, rank_key, cached_entry)
        candidates = state.get("voice_match_candidates") or candidates
    else:
        derived_entry = derive_rank_result_from_all_cache(state, key=rank_key, candidates=candidates)
        if derived_entry:
            apply_cached_rank_result(state, rank_key, derived_entry)
            candidates = state.get("voice_match_candidates") or candidates
        else:
            set_active_unranked_candidates(
                state,
                key=rank_key,
                candidates=candidates,
                status=VOICE_AI_RANK_NOT_RUN_STATUS,
            )

    if is_owner:
        save_project_state(task_id, state, execute_func=db_execute)
        # 同步内存态，避免其他路径读到旧值
        try:
            from appcore import task_state as _ts
            _ts.update(task_id, **_voice_ai_rank_state_updates(state))
        except Exception:
            pass

    return _json_response({
        "ok": True, "gender": gender,
        "candidates": candidates, "extra_items": extra_items,
        **_voice_ai_rank_response_fields(state, cached=cached),
    })


@bp.route("/api/english-redub/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    """Persist the shared-shell voice selection and resume from alignment."""
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    owner_id = row.get("user_id") or current_user.id

    body = request.get_json() or {}
    state = json.loads(row["state_json"] or "{}")
    lang = state.get("target_lang")

    try:
        normalized = normalize_confirm_voice_payload(
            body=body,
            lang=lang or "",
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    state["selected_voice_id"] = normalized["voice_id"]
    if normalized["voice_name"]:
        state["selected_voice_name"] = normalized["voice_name"]
    state["subtitle_font"] = normalized["subtitle_font"]
    state["subtitle_size"] = normalized["subtitle_size"]
    state["subtitle_position_y"] = normalized["subtitle_position_y"]
    state["subtitle_position"] = normalized["subtitle_position"]
    save_project_state(task_id, state, execute_func=db_execute)

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

    # Phase 3 bug fix (2026-05-07): av_sentence cfg 跳过 alignment，
    # 硬编码 resume("alignment") 在 av-sync-current preset 下找不到 step
    # 名导致 _run 整个循环跳过、task 卡 limbo。改成从当前 plugin_config
    # 解析出来的 step 列表里找 voice_match 的下一步。
    next_step = "alignment"  # 兜底（multi-like / omni-current / lab-current 都对）
    try:
        from appcore.events import EventBus
        from appcore.runtime_english_redub import EnglishRedubRunner

        _runner = EnglishRedubRunner(bus=EventBus(), user_id=owner_id)
        _steps = _runner._get_pipeline_steps(
            task_id, state.get("video_path", ""), state.get("task_dir", ""),
        )
        _names = [n for n, _ in _steps]
        idx = _names.index("voice_match")
        if idx + 1 < len(_names):
            next_step = _names[idx + 1]
    except Exception:
        log.warning("[omni] confirm-voice: next_step 解析失败，回退 alignment", exc_info=True)
    english_redub_pipeline_runner.resume(task_id, next_step, user_id=owner_id)

    medias_context = state.get("medias_context") or {}
    parent_task_id = (medias_context.get("parent_task_id") or "").strip()
    if parent_task_id:
        try:
            from web.routes.bulk_translate import start_bulk_scheduler_background

            start_bulk_scheduler_background(
                parent_task_id,
                user_id=owner_id,
                entrypoint="english_redub.voice_confirm",
                action="resume_after_voice_confirm",
                details={"child_task_id": task_id},
            )
        except Exception:
            log.exception("failed to resume parent bulk_translate task after voice confirm")

    return _json_response({"ok": True, "voice_id": normalized["voice_id"], "voice_name": normalized["voice_name"]})
