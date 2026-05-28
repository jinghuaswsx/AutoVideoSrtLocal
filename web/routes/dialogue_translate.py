"""Dialogue video translation routes."""
from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import medias, task_state, translation_route_store
from appcore.audio_loudness import validate_loudness_profile
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from appcore.omni_v2_config import current_fixed_plugin_config
from appcore.project_state import save_project_state, update_project_state
from appcore.runtime_dialogue import DialogueTranslateRunner
from appcore.task_recovery import recover_project_if_needed, recover_task_if_needed
from pipeline.alignment import build_script_segments
from pipeline.languages.registry import (
    SOURCE_LANGS as ALLOWED_SOURCE_LANGUAGES,
    SUPPORTED_LANGS,
    normalize_enabled_target_langs,
)
from web import store
from web.auth import admin_required, permission_required
from web.services import dialogue_pipeline_runner
from web.services.artifact_download import serve_artifact_download
from web.services.llm_debug import build_llm_debug_payload
from web.services.task_alignment import confirm_task_alignment
from web.services.task_retranslate import retranslate_task
from web.services.task_translate import start_task_translate
from web.services.task_translation_selection import select_task_translation
from web.services.translate_detail_protocol import resolve_round_file_entry
from web.services.translate_route_responses import (
    build_translate_route_payload_response,
    translate_route_flask_response,
)
from web.services.translate_step_reset import (
    build_step_resume_reset_updates,
    reset_step_names,
)

log = logging.getLogger(__name__)

bp = Blueprint("dialogue_translate", __name__)

db_query = translation_route_store.query
db_query_one = translation_route_store.query_one
db_execute = translation_route_store.execute

_OPTIONAL_PROGRESS_STEPS = {"av_sync_audit"}
_PROJECT_STATE_COLUMNS = (
    "id, user_id, original_filename, display_name, task_dir, state_json, "
    "status, thumbnail_path, created_at, expires_at, deleted_at"
)


def _json_response(payload: dict, status_code: int = 200):
    return translate_route_flask_response(
        build_translate_route_payload_response(payload, status_code)
    )


def _is_admin_user() -> bool:
    return getattr(current_user, "is_admin", False)


def _task_belongs_to_current_user(task: dict) -> bool:
    return str(task.get("_user_id")) == str(getattr(current_user, "id", ""))


def _can_view_task(task: dict) -> bool:
    return _is_admin_user() or _task_belongs_to_current_user(task)


def _task_from_project_row(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        task = json.loads(row.get("state_json") or "{}")
    except Exception:
        task = {}
    if row.get("user_id") is not None:
        task["_user_id"] = row.get("user_id")
    for key in (
        "id",
        "status",
        "original_filename",
        "display_name",
        "task_dir",
        "thumbnail_path",
        "created_at",
        "expires_at",
        "deleted_at",
    ):
        if row.get(key) is not None and not task.get(key):
            task[key] = row[key]
    return task


def _is_deleted_task(task: dict) -> bool:
    return bool(task.get("deleted_at")) or str(task.get("status") or "").lower() in {
        "deleted",
        "expired",
    }


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
        "task_dir": task.get("task_dir", ""),
    }


def _query_viewable_project(
    task_id: str,
    columns: str = "*",
    *,
    include_deleted: bool = False,
) -> dict | None:
    return translation_route_store.get_viewable_project(
        task_id,
        "dialogue_translate",
        user_id=current_user.id,
        is_admin=_is_admin_user(),
        columns=columns,
        include_deleted=include_deleted,
        include_visible_to_all=True,
        query_one_func=db_query_one,
    )


def _fresh_viewable_project_task(
    task_id: str,
    *,
    include_deleted: bool = False,
) -> dict | None:
    row = _query_viewable_project(
        task_id,
        _PROJECT_STATE_COLUMNS,
        include_deleted=include_deleted,
    )
    task = _task_from_project_row(row)
    if not task or not _can_view_task(task):
        return None
    return task


def _hydrate_task_state_cache(task_id: str, task: dict) -> None:
    if store is not task_state:
        return
    with task_state._lock:
        task_state._tasks[task_id] = copy.deepcopy(task)


def _get_viewable_task(
    task_id: str,
    *,
    include_deleted: bool = False,
) -> dict | None:
    fresh_task = _fresh_viewable_project_task(
        task_id,
        include_deleted=include_deleted,
    )
    if fresh_task:
        _hydrate_task_state_cache(task_id, fresh_task)
        return fresh_task
    if not include_deleted:
        deleted_or_expired_task = _fresh_viewable_project_task(
            task_id,
            include_deleted=True,
        )
        if deleted_or_expired_task and _is_deleted_task(deleted_or_expired_task):
            return None
    task = store.get(task_id)
    if not task or task.get("type") != "dialogue_translate" or not _can_view_task(task):
        return None
    if not include_deleted and _is_deleted_task(task):
        return None
    return task


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str) -> str:
    base = desired_name
    candidate = base
    suffix = 2
    while True:
        row = translation_route_store.find_project_by_display_name(
            user_id,
            candidate,
            query_one_func=db_query_one,
        )
        if not row:
            return candidate
        candidate = f"{base} ({suffix})"
        suffix += 1


def _ensure_uploaded_video_thumbnail(task_id: str, video_path: str, task_dir: str) -> str:
    if not video_path or not os.path.exists(video_path):
        return ""
    try:
        from pipeline.ffutil import extract_thumbnail

        if task_dir:
            os.makedirs(task_dir, exist_ok=True)
        thumb_path = os.path.join(task_dir, "thumbnail.jpg")
        thumb = thumb_path if os.path.exists(thumb_path) else extract_thumbnail(
            video_path,
            task_dir,
        )
    except Exception:
        log.warning(
            "[dialogue_translate] thumbnail generation failed for task %s",
            task_id,
            exc_info=True,
        )
        return ""
    if not thumb or not os.path.exists(thumb):
        return ""
    translation_route_store.set_project_thumbnail_path(
        task_id,
        "dialogue_translate",
        thumb,
        execute_func=db_execute,
    )
    task = store.get(task_id)
    if task is not None:
        task["thumbnail_path"] = thumb
    return thumb


def _list_enabled_target_langs() -> tuple[str, ...]:
    try:
        enabled = medias.list_enabled_language_codes()
    except Exception:
        log.warning(
            "[dialogue_translate] failed to load enabled languages, falling back",
            exc_info=True,
        )
        return SUPPORTED_LANGS
    return normalize_enabled_target_langs(enabled)


def _step_maps(step_names: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    return (
        {step: "pending" for step in step_names},
        {step: "" for step in step_names},
    )


def _dialogue_pipeline_step_names(
    task: dict | None,
    *,
    include_analysis: bool = False,
) -> list[str]:
    plugin_config = dict((task or {}).get("plugin_config") or current_fixed_plugin_config())
    return DialogueTranslateRunner.pipeline_step_names_for_config(
        plugin_config,
        include_analysis=include_analysis,
    )


def _post_asr_step(step_names: list[str]) -> str:
    for step in step_names:
        if step in {"asr_clean", "asr_normalize"}:
            return step
    raise ValueError("dialogue pipeline missing post-ASR step")


def _reset_steps_from(task_id: str, step_names: list[str], start_step: str) -> None:
    started = False
    for step in step_names:
        if step == start_step:
            started = True
        if started:
            store.set_step(task_id, step, "pending")
            store.set_step_message(task_id, step, "waiting...")


def _dialogue_resume_cleanup_updates(
    task: dict,
    step_names: list[str],
    start_step: str,
) -> dict:
    updates = build_step_resume_reset_updates(task, step_names, start_step)
    reset_set = set(reset_step_names(step_names, start_step))
    if "speaker_detect" in reset_set:
        updates.update(
            dialogue_segments=[],
            speaker_summary={},
            speaker_sample_specs=[],
            speaker_profiles={},
            selected_voice_by_speaker={},
        )
    elif "voice_match_ab" in reset_set:
        updates.update(
            speaker_sample_specs=[],
            speaker_profiles={},
            selected_voice_by_speaker={},
        )
    if start_step in {"asr_clean", "asr_normalize"}:
        source_language = str(task.get("source_language") or "en").strip()
        if source_language not in ALLOWED_SOURCE_LANGUAGES:
            raise ValueError(
                f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"
            )
        updates.update(source_language=source_language, user_specified_source_language=True)
    return updates


def _applied_loudness_profile(task: dict) -> tuple[str | None, int | None]:
    tts_loudness = ((task.get("separation") or {}).get("tts_loudness") or {})
    return tts_loudness.get("profile"), tts_loudness.get("manual_boost_pct")


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


def _resolve_translate_pref(state: dict) -> str:
    from appcore.api_keys import get_key
    from appcore.runtime import _VALID_TRANSLATE_PREFS

    for value in (
        state.get("custom_translate_provider"),
        state.get("translate_pref"),
        get_key(current_user.id, "translate_pref"),
        "openrouter",
    ):
        candidate = str(value or "").strip()
        if candidate in _VALID_TRANSLATE_PREFS:
            return candidate
    return "openrouter"


def _voice_id_from(value: object) -> str:
    if isinstance(value, dict):
        for key in ("voice_id", "elevenlabs_voice_id", "id"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        return ""
    return str(value or "").strip()


def _voice_name_from(value: object, voice_id: str) -> str:
    if isinstance(value, dict):
        for key in ("name", "voice_name", "label"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
    return voice_id


def _normalize_voice_selection_payload(body: dict) -> dict[str, str]:
    raw = body.get("selected_voice_by_speaker") or {}
    if not isinstance(raw, dict):
        raise ValueError("selected_voice_by_speaker must be an object")
    normalized: dict[str, str] = {}
    missing: list[str] = []
    for speaker in ("A", "B"):
        voice_id = _voice_id_from(raw.get(speaker))
        if not voice_id:
            missing.append(speaker)
            continue
        normalized[speaker] = voice_id
    if missing:
        raise ValueError("selected_voice_by_speaker must include A and B voice_id")
    return normalized


def _selected_voice_payload(
    profile: dict,
    requested_voice_id: str,
    *,
    speaker: str,
) -> dict:
    requested_voice_id = str(requested_voice_id or "").strip()
    candidates = profile.get("candidates") or []
    if not candidates:
        raise ValueError(f"Speaker {speaker} has no voice candidates")
    for candidate in candidates:
        if _voice_id_from(candidate) == requested_voice_id:
            payload = {
                "voice_id": requested_voice_id,
                "name": _voice_name_from(candidate, requested_voice_id),
            }
            if isinstance(candidate, dict) and candidate.get("voice_name"):
                payload["voice_name"] = candidate["voice_name"]
            return payload
    raise ValueError(f"voice_id is not a candidate for Speaker {speaker}")


@bp.before_request
def _require_dialogue_translate_permission():
    if not current_user.is_authenticated:
        return None
    if not current_user.has_permission("dialogue_translate"):
        if request.path.startswith("/api/dialogue-translate"):
            return _json_response({"error": "Forbidden"}, 403)
        abort(403)
    return None


@bp.route("/dialogue-translate")
@login_required
@admin_required
@permission_required("dialogue_translate")
def index():
    rows = translation_route_store.list_projects_with_state(
        user_id=current_user.id,
        project_type="dialogue_translate",
        is_admin=_is_admin_user(),
        query_func=db_query,
    )
    for row in rows:
        try:
            state = json.loads(row.get("state_json") or "{}")
        except Exception:
            state = {}
        row["source_lang"] = state.get("source_language") or "en"
        row["target_lang"] = state.get("target_lang") or ""
        row["current_review_step"] = state.get("current_review_step") or ""

    from appcore.settings import get_retention_hours

    return render_template(
        "dialogue_translate.html",
        projects=rows,
        now=datetime.now(),
        allowed_source_languages=ALLOWED_SOURCE_LANGUAGES,
        supported_langs=_list_enabled_target_langs(),
        retention_hours=get_retention_hours("dialogue_translate"),
    )


@bp.route("/dialogue-translate/<task_id>")
@login_required
@admin_required
@permission_required("dialogue_translate")
def detail(task_id: str):
    recover_project_if_needed(task_id, "dialogue_translate")
    row = _query_viewable_project(task_id, include_deleted=True)
    state = _task_from_project_row(row)
    if not row:
        task = _get_viewable_task(task_id, include_deleted=True)
        if task and task.get("type") == "dialogue_translate":
            row = _project_row_from_task(task)
            state = dict(task)
    if not row:
        abort(404)

    pipeline_main_steps = _dialogue_pipeline_step_names(
        state,
        include_analysis=False,
    )
    pipeline_progress_steps = [
        step for step in pipeline_main_steps if step not in _OPTIONAL_PROGRESS_STEPS
    ]
    pipeline_step_order = _dialogue_pipeline_step_names(
        state,
        include_analysis=True,
    )
    return render_template(
        "dialogue_translate_detail.html",
        project=row,
        state=state,
        target_lang=state.get("target_lang") or "",
        translate_pref=_resolve_translate_pref(state),
        pipeline_main_steps=pipeline_main_steps,
        pipeline_progress_steps=pipeline_progress_steps,
        pipeline_step_order=pipeline_step_order,
    )


@bp.route("/api/dialogue-translate/start", methods=["POST"])
@login_required
@admin_required
def upload_and_start():
    if "video" not in request.files:
        return _json_response({"error": "No video file"}, 400)
    file = request.files["video"]
    if not file.filename:
        return _json_response({"error": "Empty filename"}, 400)

    from web.upload_util import (
        build_source_object_info,
        client_filename_basename,
        save_uploaded_video,
        validate_video_extension,
    )

    original_filename = client_filename_basename(file.filename)
    if not validate_video_extension(original_filename):
        return _json_response({"error": "不支持的视频格式"}, 400)

    source_language = (request.form.get("source_language") or "").strip()
    if source_language not in ALLOWED_SOURCE_LANGUAGES:
        return _json_response(
            {"error": f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"},
            400,
        )

    target_lang = (request.form.get("target_lang") or "").strip()
    enabled_target_langs = _list_enabled_target_langs()
    if target_lang not in enabled_target_langs:
        return _json_response(
            {"error": f"target_lang must be one of {list(enabled_target_langs)}"},
            400,
        )

    plugin_config = current_fixed_plugin_config()
    step_names = _dialogue_pipeline_step_names(
        {"plugin_config": plugin_config},
        include_analysis=False,
    )
    steps, step_messages = _step_maps(step_names)

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_path, file_size, content_type = save_uploaded_video(
        file,
        UPLOAD_DIR,
        task_id,
        original_filename,
    )
    user_id = current_user.id
    store.create(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=user_id,
    )

    desired_name = (request.form.get("display_name") or "").strip()[:200]
    display_name = _resolve_name_conflict(
        user_id,
        desired_name or _default_display_name(original_filename),
    )
    store.update(
        task_id,
        display_name=display_name,
        type="dialogue_translate",
        status="running",
        source_language=source_language,
        user_specified_source_language=True,
        target_lang=target_lang,
        plugin_config=plugin_config,
        pipeline_version="dialogue",
        source_tos_key="",
        source_object_info=build_source_object_info(
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
            storage_backend="local",
            uploaded_at=datetime.now().isoformat(timespec="seconds"),
        ),
        delivery_mode="local_primary",
        steps=steps,
        step_messages=step_messages,
        dialogue_segments=[],
        speaker_profiles={},
        selected_voice_by_speaker={},
    )
    store.set_preview_file(task_id, "source_video", video_path)
    _ensure_uploaded_video_thumbnail(task_id, video_path, task_dir)

    dialogue_pipeline_runner.start(task_id, user_id=user_id)
    return _json_response(
        {
            "task_id": task_id,
            "redirect_url": f"/dialogue-translate/{task_id}",
        },
        201,
    )


@bp.route("/api/dialogue-translate/<task_id>", methods=["GET"])
@login_required
@admin_required
def get_task(task_id: str):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    return _json_response(task)


@bp.route("/api/dialogue-translate/<task_id>/subtitle-preview", methods=["GET"])
@login_required
@admin_required
def subtitle_preview(task_id: str):
    row = _query_viewable_project(task_id, "id, user_id", include_deleted=False)
    if not row:
        return _json_response({"error": "Task not found"}, 404)
    payload = build_multi_translate_preview_payload(
        task_id,
        row.get("user_id") or current_user.id,
        api_base="/api/dialogue-translate",
    )
    return _json_response(payload)


@bp.route("/api/dialogue-translate/<task_id>/llm-debug/<step>", methods=["GET"])
@login_required
@admin_required
def get_llm_debug(task_id: str, step: str):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    payload = build_llm_debug_payload(task, step)
    if not payload:
        return _json_response({"error": "LLM debug data not found"}, 404)
    return _json_response(payload)


@bp.route("/api/dialogue-translate/<task_id>/restart", methods=["POST"])
@login_required
@admin_required
def restart(task_id: str):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id

    body = request.get_json(silent=True) or {}
    raw_source_language = body.get("source_language", None)
    if raw_source_language is not None:
        raw_source_language = str(raw_source_language).strip()
        if raw_source_language not in ALLOWED_SOURCE_LANGUAGES:
            return _json_response(
                {"error": f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"},
                400,
            )
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
        source_language=raw_source_language,
        user_id=owner_id,
        runner=dialogue_pipeline_runner,
        step_order=tuple(_dialogue_pipeline_step_names(task)),
        extra_reset_fields={
            "dialogue_segments": [],
            "speaker_summary": {},
            "speaker_sample_specs": [],
            "speaker_profiles": {},
            "selected_voice_by_speaker": {},
        },
    )
    return _json_response({"status": "restarted", "task": updated})


@bp.route("/api/dialogue-translate/<task_id>/source-language", methods=["PUT"])
@login_required
@admin_required
def update_source_language(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id
    body = request.get_json(silent=True) or {}
    raw_lang = str(body.get("source_language") or "").strip()
    if raw_lang not in ALLOWED_SOURCE_LANGUAGES:
        return _json_response(
            {"error": f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"},
            400,
        )

    step_names = _dialogue_pipeline_step_names(task)
    try:
        start_step = _post_asr_step(step_names)
        reset_task = dict(task)
        reset_task["source_language"] = raw_lang
        updates = _dialogue_resume_cleanup_updates(reset_task, step_names, start_step)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)
    updates.update(source_language=raw_lang, user_specified_source_language=True)
    store.update(task_id, **updates)
    _reset_steps_from(task_id, step_names, start_step)

    dialogue_pipeline_runner.resume(task_id, start_step, user_id=owner_id)
    return _json_response(
        {
            "status": "started",
            "source_language": raw_lang,
            "user_specified_source_language": True,
        }
    )


@bp.route("/api/dialogue-translate/<task_id>/alignment", methods=["PUT"])
@login_required
@admin_required
def update_alignment(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    source_language = (request.get_json(silent=True) or {}).get("source_language")
    if source_language in ALLOWED_SOURCE_LANGUAGES:
        store.update(task_id, source_language=source_language, user_specified_source_language=True)
    outcome = confirm_task_alignment(
        task_id,
        task,
        request.get_json(silent=True) or {},
        user_id=task.get("_user_id") or current_user.id,
        build_segments=build_script_segments,
        runner=dialogue_pipeline_runner,
    )
    return _json_response(outcome.payload, outcome.status_code)


@bp.route("/api/dialogue-translate/<task_id>/segments", methods=["PUT"])
@login_required
@admin_required
def update_segments(task_id: str):
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
            {
                "index": segment.get("index", index),
                "text": segment.get("translated", ""),
                "source_segment_indices": segment.get("source_segment_indices", [index]),
            }
            for index, segment in enumerate(segments)
        ]
        localized_translation["full_text"] = " ".join(
            sentence["text"] for sentence in localized_translation["sentences"]
        )
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        store.update(
            task_id,
            variants=variants,
            localized_translation=localized_translation,
            _segments_confirmed=True,
            evals_invalidated_at=datetime.now(timezone.utc).isoformat(),
        )

    store.set_current_review_step(task_id, "")
    dialogue_pipeline_runner.resume(task_id, "tts", user_id=owner_id)
    return _json_response({"status": "ok"})


@bp.route("/api/dialogue-translate/<task_id>/loudness-profile", methods=["POST"])
@login_required
@admin_required
def set_loudness_profile(task_id: str):
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
    return _json_response(
        {
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
        }
    )


@bp.route("/api/dialogue-translate/<task_id>/resume", methods=["POST"])
@login_required
@admin_required
def resume(task_id: str):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    owner_id = task.get("_user_id") or current_user.id
    body = request.get_json(silent=True) or {}
    start_step = str(body.get("start_step") or "").strip()
    step_names = _dialogue_pipeline_step_names(task)
    if start_step not in step_names:
        return _json_response(
            {
                "error": f"start_step {start_step!r} must be one of {step_names}",
                "steps": step_names,
            },
            400,
        )
    try:
        updates = _dialogue_resume_cleanup_updates(task, step_names, start_step)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    store.update(task_id, **updates)
    _reset_steps_from(task_id, step_names, start_step)
    dialogue_pipeline_runner.resume(task_id, start_step, user_id=owner_id)
    return _json_response({"status": "started", "start_step": start_step})


@bp.route("/api/dialogue-translate/<task_id>/download/<file_type>")
@login_required
@admin_required
def download(task_id: str, file_type: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    variant = request.args.get("variant", "normal")
    return serve_artifact_download(task, task_id, file_type, variant=variant)


@bp.route("/api/dialogue-translate/<task_id>/visible-to-all", methods=["PUT"])
@login_required
@admin_required
def toggle_visible_to_all(task_id: str):
    if not getattr(current_user, "is_superadmin", False):
        return _json_response({"error": "Only superadmin can change visibility"}, 403)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    body = request.get_json(silent=True) or {}
    value = bool(body.get("visible_to_all", False))
    update_project_state(
        task_id,
        {"visible_to_all": value},
        query_one_func=db_query_one,
        execute_func=db_execute,
    )
    store.update(task_id, visible_to_all=value)
    return _json_response({"visible_to_all": value})


@bp.route("/api/dialogue-translate/<task_id>/artifact/<name>")
@login_required
@admin_required
def get_artifact(task_id: str, name: str):
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
        separation = task.get("separation") or {}
        path = (
            separation.get("vocals_path")
            if name == "separation_vocals"
            else separation.get("accompaniment_path")
        )
    if path:
        return safe_task_file_response(task, path)
    return _json_response({"error": "Artifact not found"}, 404)


@bp.route("/api/dialogue-translate/<task_id>/artifact-path")
@login_required
@admin_required
def get_artifact_path(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    from web.services.artifact_download import safe_task_relative_file_response

    return safe_task_relative_file_response(task, request.args.get("path"))


_ALLOWED_ROUND_KINDS = {
    "localized_translation": ("localized_translation.round_{r}.json", "application/json"),
    "localized_rewrite_messages": (
        "localized_rewrite_messages.round_{r}.json",
        "application/json",
    ),
    "initial_translate_messages": ("localized_translate_messages.json", "application/json"),
    "tts_script": ("tts_script.round_{r}.json", "application/json"),
    "tts_full_audio": ("tts_full.round_{r}.mp3", "audio/mpeg"),
}


@bp.route("/api/dialogue-translate/<task_id>/round-file/<int:round_index>/attempt/<int:attempt>")
@login_required
@admin_required
def get_round_attempt_file(task_id: str, round_index: int, attempt: int):
    if round_index not in (1, 2, 3, 4, 5) or attempt not in (1, 2, 3, 4, 5):
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


@bp.route("/api/dialogue-translate/<task_id>/round-file/<int:round_index>/<kind>")
@login_required
@admin_required
def get_round_file(task_id: str, round_index: int, kind: str):
    try:
        filename, mime = resolve_round_file_entry(
            _ALLOWED_ROUND_KINDS,
            round_index,
            kind,
        )
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


@bp.route("/api/dialogue-translate/<task_id>/analysis/run", methods=["POST"])
@login_required
@admin_required
def run_ai_analysis(task_id: str):
    if not _get_viewable_task(task_id):
        return _json_response({"error": "Task not found"}, 404)
    return _json_response({"error": "analysis not supported for dialogue_translate"}, 501)


@bp.route("/api/dialogue-translate/<task_id>/start-translate", methods=["POST"])
@login_required
@admin_required
def start_translate(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    body = request.get_json(silent=True) or {}
    outcome = start_task_translate(
        task_id,
        task,
        body,
        user_id=task.get("_user_id") or current_user.id,
        runner=dialogue_pipeline_runner,
    )
    return _json_response(outcome.payload, outcome.status_code)


@bp.route("/api/dialogue-translate/<task_id>/retranslate", methods=["POST"])
@login_required
@admin_required
def retranslate(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    body = request.get_json(silent=True) or {}
    outcome = retranslate_task(
        task_id,
        task,
        body,
        user_id=task.get("_user_id") or current_user.id,
    )
    return _json_response(outcome.payload, outcome.status_code)


@bp.route("/api/dialogue-translate/<task_id>/select-translation", methods=["PUT"])
@login_required
@admin_required
def select_translation(task_id: str):
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)
    body = request.get_json(silent=True) or {}
    outcome = select_task_translation(task_id, task, body)
    return _json_response(outcome.payload, outcome.status_code)


@bp.route("/api/dialogue-translate/<task_id>/confirm-voices", methods=["POST"])
@login_required
@admin_required
def confirm_voices(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    owner_user_id = row.get("user_id") or current_user.id
    try:
        state = json.loads(row.get("state_json") or "{}")
    except Exception:
        state = {}
    if (state.get("steps") or {}).get("voice_match_ab") != "waiting":
        return _json_response({"error": "voice_match_ab is not waiting"}, 409)

    try:
        selected_voice_ids = _normalize_voice_selection_payload(
            request.get_json(silent=True) or {}
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    speaker_profiles = {
        speaker: dict(profile) if isinstance(profile, dict) else {}
        for speaker, profile in (state.get("speaker_profiles") or {}).items()
    }
    selected_voice_by_speaker: dict[str, dict] = {}
    for speaker in ("A", "B"):
        profile = speaker_profiles.get(speaker) or {}
        try:
            selected_voice = _selected_voice_payload(
                profile,
                selected_voice_ids[speaker],
                speaker=speaker,
            )
        except ValueError as exc:
            return _json_response({"error": str(exc)}, 400)
        profile["selected_voice"] = selected_voice
        speaker_profiles[speaker] = profile
        selected_voice_by_speaker[speaker] = selected_voice

    steps = dict(state.get("steps") or {})
    steps["voice_match_ab"] = "done"
    state["speaker_profiles"] = speaker_profiles
    state["selected_voice_by_speaker"] = selected_voice_by_speaker
    state["steps"] = steps
    state["status"] = "running"
    state["error"] = ""
    state["current_review_step"] = ""
    save_project_state(task_id, state, execute_func=db_execute)

    task_state.update(
        task_id,
        speaker_profiles=speaker_profiles,
        selected_voice_by_speaker=selected_voice_by_speaker,
        status="running",
        error="",
    )
    task_state.set_step(task_id, "voice_match_ab", "done")
    task_state.set_current_review_step(task_id, "")

    dialogue_pipeline_runner.resume(
        task_id,
        "alignment",
        user_id=owner_user_id,
    )
    return _json_response(
        {
            "ok": True,
            "selected_voice_by_speaker": selected_voice_ids,
        }
    )
