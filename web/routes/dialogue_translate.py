"""Dialogue video translation routes."""
from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import medias, task_state, translation_route_store
from appcore.omni_v2_config import current_fixed_plugin_config
from appcore.project_state import save_project_state
from appcore.runtime_dialogue import DialogueTranslateRunner
from appcore.task_recovery import recover_project_if_needed, recover_task_if_needed
from pipeline.languages.registry import (
    SOURCE_LANGS as ALLOWED_SOURCE_LANGUAGES,
    SUPPORTED_LANGS,
    normalize_enabled_target_langs,
)
from web import store
from web.auth import admin_required, permission_required
from web.services import dialogue_pipeline_runner
from web.services.translate_route_responses import (
    build_translate_route_payload_response,
    translate_route_flask_response,
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
    include_deleted: bool = True,
) -> dict | None:
    return translation_route_store.get_viewable_project(
        task_id,
        "dialogue_translate",
        user_id=current_user.id,
        is_admin=_is_admin_user(),
        columns=columns,
        include_deleted=include_deleted,
        query_one_func=db_query_one,
    )


def _fresh_viewable_project_task(task_id: str) -> dict | None:
    row = _query_viewable_project(task_id, _PROJECT_STATE_COLUMNS)
    task = _task_from_project_row(row)
    if not task or not _can_view_task(task):
        return None
    return task


def _hydrate_task_state_cache(task_id: str, task: dict) -> None:
    if store is not task_state:
        return
    with task_state._lock:
        task_state._tasks[task_id] = copy.deepcopy(task)


def _get_viewable_task(task_id: str) -> dict | None:
    fresh_task = _fresh_viewable_project_task(task_id)
    if fresh_task:
        _hydrate_task_state_cache(task_id, fresh_task)
        return fresh_task
    task = store.get(task_id)
    if not task or task.get("type") != "dialogue_translate" or not _can_view_task(task):
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


def _selected_voice_payload(profile: dict, requested_voice_id: str) -> dict:
    requested_voice_id = str(requested_voice_id or "").strip()
    selected_voice = profile.get("selected_voice")
    if _voice_id_from(selected_voice) == requested_voice_id:
        return {
            "voice_id": requested_voice_id,
            "name": _voice_name_from(selected_voice, requested_voice_id),
        }
    for candidate in profile.get("candidates") or []:
        if _voice_id_from(candidate) == requested_voice_id:
            payload = {
                "voice_id": requested_voice_id,
                "name": _voice_name_from(candidate, requested_voice_id),
            }
            if isinstance(candidate, dict) and candidate.get("voice_name"):
                payload["voice_name"] = candidate["voice_name"]
            return payload
    return {
        "voice_id": requested_voice_id,
        "name": requested_voice_id,
    }


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
    row = _query_viewable_project(task_id)
    state = _task_from_project_row(row)
    if not row:
        task = _get_viewable_task(task_id)
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
        translate_pref="dialogue_translate.localize",
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
        selected_voice = _selected_voice_payload(
            profile,
            selected_voice_ids[speaker],
        )
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
