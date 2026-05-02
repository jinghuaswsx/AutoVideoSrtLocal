"""
任务管理蓝图

负责视频上传、任务生命周期管理、翻译确认、文件下载。
不包含任何业务执行逻辑，执行逻辑在 services/pipeline_runner.py。
"""
import os
import subprocess
import uuid
from datetime import datetime, timezone

import mimetypes

from flask import Blueprint, request, jsonify, send_file, render_template, abort, redirect, Response, make_response
from flask_login import login_required, current_user

from appcore import ai_billing
from appcore.runtime import (
    _VALID_TRANSLATE_PREFS,
    _build_av_localized_translation,
    _build_av_tts_segments,
    _llm_request_payload,
    _llm_response_payload,
)
from appcore.av_translate_inputs import (
    AV_TARGET_LANGUAGE_CODES,
    AV_TARGET_MARKET_CODES,
    AV_TARGET_MARKET_OPTIONS,
    available_av_target_language_codes,
    build_available_av_translate_inputs,
    list_available_av_target_language_options,
    normalize_av_translate_inputs,
)
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import cleanup, tos_backup_storage
from appcore.task_recovery import recover_task_if_needed
from pipeline.alignment import build_script_segments
from pipeline.capcut import deploy_capcut_project
from pipeline import tts
from pipeline.av_subtitle_units import build_subtitle_units_from_sentences
from pipeline.duration_reconcile import classify_overshoot, compute_speed_for_target, duration_ratio
from pipeline.subtitle import build_srt_from_chunks, save_srt
from web.preview_artifacts import (
    build_alignment_artifact,
    build_subtitle_artifact,
    build_tts_artifact,
    build_translate_artifact,
    build_variant_compare_artifact,
)
from web import store
from web.services import pipeline_runner
from web.services.artifact_download import safe_task_dir_path, serve_artifact_download
from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    lookup_default_voice_row,
    normalize_confirm_voice_payload,
)
from appcore.db import query_one as db_query_one, execute as db_execute, query as db_query

bp = Blueprint("task", __name__, url_prefix="/api/tasks")

AV_SYNC_STEPS = (
    "extract",
    "asr",
    "asr_normalize",
    "voice_match",
    "alignment",
    "translate",
    "tts",
    "subtitle",
    "compose",
    "export",
)


ALLOWED_SOURCE_LANGUAGES = (
    "zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi",
)


def _collect_av_source_language(payload: dict | None, current_task: dict | None = None) -> tuple[dict, str | None]:
    data = payload or {}
    if "source_language" not in data and current_task:
        source_language = str(current_task.get("source_language") or "").strip().lower()
        if source_language not in ALLOWED_SOURCE_LANGUAGES:
            return {}, f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"
        return {
            "source_language": source_language,
            "user_specified_source_language": True,
        }, None

    raw_source_language = str(data.get("source_language") or "").strip().lower()
    if raw_source_language not in ALLOWED_SOURCE_LANGUAGES:
        return {}, f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"
    return {
        "source_language": raw_source_language,
        "user_specified_source_language": True,
    }, None


def _av_step_maps(status: str = "pending") -> tuple[dict, dict]:
    return {step: status for step in AV_SYNC_STEPS}, {step: "" for step in AV_SYNC_STEPS}


from pipeline.ffutil import extract_thumbnail as _extract_thumbnail


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "manual"}
    return bool(value)


def _is_admin_user() -> bool:
    return getattr(current_user, "is_admin", False)


def _request_payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict(flat=True)


def _collect_av_translate_inputs(payload: dict | None, current_task: dict | None = None) -> dict:
    current_inputs = (current_task or {}).get("av_translate_inputs") or {}
    data = payload or {}
    nested = data.get("av_translate_inputs") or {}
    nested_inputs = nested if isinstance(nested, dict) else {}
    override_inputs = dict(nested_inputs.get("product_overrides") or {})

    flat_map = {
        "product_name": data.get("override_product_name"),
        "brand": data.get("override_brand"),
        "selling_points": data.get("override_selling_points"),
        "price": data.get("override_price"),
        "target_audience": data.get("override_target_audience"),
        "extra_info": data.get("override_extra_info"),
    }
    for key, value in flat_map.items():
        if value is not None:
            override_inputs[key] = value

    raw_inputs = {
        "target_language": data.get("target_language") or data.get("target_lang") or nested_inputs.get("target_language"),
        "target_language_name": data.get("target_language_name", nested_inputs.get("target_language_name")),
        "target_market": data.get("target_market", nested_inputs.get("target_market")),
        "sync_granularity": data.get("sync_granularity", nested_inputs.get("sync_granularity")),
        "product_overrides": override_inputs,
    }
    return normalize_av_translate_inputs(raw_inputs, base=current_inputs)


def _validate_av_translate_inputs(av_inputs: dict) -> str | None:
    target_language = str(av_inputs.get("target_language") or "").strip().lower()
    if target_language not in available_av_target_language_codes():
        return "target_language 非法"
    target_market = str(av_inputs.get("target_market") or "").strip().upper()
    if target_market not in AV_TARGET_MARKET_CODES:
        return "target_market 非法"
    return None


def _get_current_user_task(task_id: str) -> dict | None:
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return None
    return task


def _av_task_target_lang(task: dict) -> str:
    av_inputs = task.get("av_translate_inputs") if isinstance(task.get("av_translate_inputs"), dict) else {}
    return str(task.get("target_lang") or av_inputs.get("target_language") or "").strip().lower()


def _rebuild_tts_full_audio(task_dir: str, segments: list[dict], variant: str = "av") -> str:
    seg_dir = os.path.join(task_dir, "tts_segments", variant) if variant else os.path.join(task_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)
    concat_list_path = os.path.join(seg_dir, "concat.rewrite.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_file:
        for segment in segments:
            segment_path = os.path.abspath(str(segment.get("tts_path") or ""))
            if not segment_path or not os.path.exists(segment_path):
                raise FileNotFoundError(f"找不到配音片段: {segment_path}")
            escaped_segment_path = segment_path.replace("'", "'\\''")
            concat_file.write(f"file '{escaped_segment_path}'\n")

    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(task_dir, full_audio_name)
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")
    return full_audio_path


def _resolve_av_voice_ids(task: dict, variant_state: dict) -> tuple[str | None, str | None]:
    stored_voice_id = variant_state.get("voice_id") or task.get("voice_id") or task.get("recommended_voice_id")
    voice = None
    if stored_voice_id:
        try:
            voice = tts.get_voice_by_id(stored_voice_id, current_user.id)
        except Exception:
            voice = None
    if not isinstance(voice, dict):
        elevenlabs_voice_id = stored_voice_id if isinstance(stored_voice_id, str) else None
        return stored_voice_id, elevenlabs_voice_id
    resolved_voice_id = voice.get("id") or stored_voice_id
    elevenlabs_voice_id = voice.get("elevenlabs_voice_id") or voice.get("voice_id") or voice.get("id")
    return resolved_voice_id, elevenlabs_voice_id


def _clear_av_compose_outputs(
    task: dict,
    variant_state: dict,
    variant: str = "av",
) -> tuple[dict, dict, dict, dict, dict, dict, dict, dict, dict]:
    result = dict(task.get("result") or {})
    exports = dict(task.get("exports") or {})
    artifacts = dict(task.get("artifacts") or {})
    preview_files = dict(task.get("preview_files") or {})
    tos_uploads = dict(task.get("tos_uploads") or {})

    result.pop("hard_video", None)
    exports.pop("capcut_archive", None)
    exports.pop("capcut_project", None)
    exports.pop("jianying_project_dir", None)
    artifacts.pop("compose", None)
    artifacts.pop("export", None)
    preview_files.pop("hard_video", None)

    for key, payload in list(tos_uploads.items()):
        payload_variant = payload.get("variant") if isinstance(payload, dict) else None
        if key.startswith(f"{variant}:") or payload_variant == variant:
            tos_uploads.pop(key, None)

    variant_result = dict(variant_state.get("result") or {})
    variant_exports = dict(variant_state.get("exports") or {})
    variant_artifacts = dict(variant_state.get("artifacts") or {})
    variant_preview_files = dict(variant_state.get("preview_files") or {})

    variant_result.clear()
    variant_exports.clear()
    variant_artifacts.pop("compose", None)
    variant_artifacts.pop("export", None)
    variant_preview_files.pop("hard_video", None)

    return (
        result,
        exports,
        artifacts,
        preview_files,
        tos_uploads,
        variant_result,
        variant_exports,
        variant_artifacts,
        variant_preview_files,
    )


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
    try:
        translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    except Exception:
        translate_pref = "openrouter"
    return render_template(
        "index.html",
        translate_pref=translate_pref,
        av_target_languages=list_available_av_target_language_options(),
        av_target_markets=AV_TARGET_MARKET_OPTIONS,
        av_translate_defaults=build_available_av_translate_inputs(),
    )


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
    if not task:
        return None
    from appcore.safe_paths import PathSafetyError, resolve_under_allowed_roots
    from web.services.artifact_download import artifact_allowed_roots

    for path in _artifact_candidates(task_id, name, task, variant=variant):
        if not path:
            continue
        try:
            safe_path = resolve_under_allowed_roots(path, artifact_allowed_roots(task))
        except PathSafetyError:
            continue
        if safe_path.is_file():
            return str(safe_path)
    return None


def _ensure_local_source_video(task_id: str, task: dict) -> None:
    video_path = (task.get("video_path") or "").strip()
    if not video_path or os.path.exists(video_path):
        return
    tos_backup_storage.ensure_local_copy_for_local_path(video_path)
    if os.path.exists(video_path):
        return
    raise FileNotFoundError(
        f"本地源视频缺失: {video_path}。请先运行本地存储迁移回填，或重新上传源视频。"
    )


def _task_requires_source_sync(task: dict) -> bool:
    video_path = (task.get("video_path") or "").strip()
    return bool(video_path and not os.path.exists(video_path))


@bp.route("", methods=["POST"])
@login_required
def upload():
    """上传视频，创建任务，返回 task_id"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    from web.upload_util import build_source_object_info, save_uploaded_video, validate_video_extension

    original_filename = os.path.basename(file.filename)
    if not validate_video_extension(original_filename):
        return jsonify({"error": "涓嶆敮鎸佺殑瑙嗛鏍煎紡"}), 400
    form_payload = request.form.to_dict(flat=True)
    av_inputs = _collect_av_translate_inputs(form_payload)
    av_error = _validate_av_translate_inputs(av_inputs)
    if av_error:
        return jsonify({"error": av_error}), 400
    source_updates, source_error = _collect_av_source_language(form_payload)
    if source_error:
        return jsonify({"error": source_error}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_path, file_size, content_type = save_uploaded_video(file, UPLOAD_DIR, task_id, original_filename)
    user_id = current_user.id if current_user.is_authenticated else None

    store.create(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=user_id,
    )

    desired_name = (form_payload.get("display_name") or "").strip()[:200]
    display_name = desired_name or _default_display_name(original_filename)
    if user_id is not None:
        display_name = _resolve_name_conflict(user_id, display_name)
        db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))

    steps, step_messages = _av_step_maps()
    store.update(
        task_id,
        display_name=display_name,
        type="translation",
        source_language=source_updates["source_language"],
        user_specified_source_language=source_updates["user_specified_source_language"],
        pipeline_version="av",
        target_lang=av_inputs["target_language"],
        av_translate_inputs=av_inputs,
        steps=steps,
        step_messages=step_messages,
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
    return jsonify({"task_id": task_id, "redirect_url": f"/sentence_translate/{task_id}"}), 201


@bp.route("/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    recover_task_if_needed(task_id)
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@bp.route("/<task_id>/subtitle-preview", methods=["GET"])
@login_required
def subtitle_preview(task_id: str):
    task = _get_current_user_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(build_multi_translate_preview_payload(task_id, current_user.id, api_base="/api/tasks"))


@bp.route("/user-default-voice", methods=["PUT"])
@login_required
def set_user_default_voice_route():
    body = request.get_json(silent=True) or {}
    lang = (body.get("lang") or "").strip().lower()
    voice_id = (body.get("voice_id") or "").strip()
    voice_name = (body.get("voice_name") or "").strip() or None
    if lang not in AV_TARGET_LANGUAGE_CODES:
        return jsonify({"error": f"lang must be one of {sorted(AV_TARGET_LANGUAGE_CODES)}"}), 400
    if not voice_id:
        return jsonify({"error": "voice_id required"}), 400

    from appcore.video_translate_defaults import set_user_default_voice

    set_user_default_voice(current_user.id, lang, voice_id, voice_name)
    return jsonify({"ok": True, "lang": lang, "voice_id": voice_id, "voice_name": voice_name})


@bp.route("/<task_id>/voice-library", methods=["GET"])
@login_required
def voice_library_for_task(task_id: str):
    task = _get_current_user_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    lang = _av_task_target_lang(task)
    if not lang:
        return jsonify({"error": "task has no target_lang"}), 400

    from appcore.voice_library_browse import list_voices

    gender = request.args.get("gender") or None
    q = request.args.get("q") or None
    try:
        data = list_voices(language=lang, gender=gender, q=q, page=1, page_size=500)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    state = dict(task)
    state["target_lang"] = lang
    default_voice = lookup_default_voice_row(lang, current_user.id)
    payload = build_voice_library_payload(
        state=state,
        owner_user_id=current_user.id,
        items=data.get("items", []),
        total=data.get("total", 0),
        default_voice=default_voice,
    )
    return jsonify(payload)


@bp.route("/<task_id>/rematch", methods=["POST"])
@login_required
def rematch_voice(task_id: str):
    task = _get_current_user_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    lang = _av_task_target_lang(task)
    if not lang:
        return jsonify({"error": "task has no target_lang"}), 400

    body = request.get_json(silent=True) or {}
    gender = (body.get("gender") or "").strip().lower() or None
    if gender and gender not in {"male", "female"}:
        return jsonify({"error": "gender must be male|female|null"}), 400

    embedding_b64 = task.get("voice_match_query_embedding")
    if not embedding_b64:
        return jsonify({"error": "voice_match 尚未完成，无法重新匹配"}), 409

    import base64
    from appcore.video_translate_defaults import resolve_default_voice
    from appcore.voice_library_browse import fetch_voices_by_ids
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
    for candidate in candidates:
        candidate["similarity"] = float(candidate.get("similarity", 0.0))

    candidate_ids = [candidate["voice_id"] for candidate in candidates if candidate.get("voice_id")]
    extra_items = fetch_voices_by_ids(language=lang, voice_ids=candidate_ids) if candidate_ids else []
    store.update(task_id, voice_match_candidates=candidates)
    return jsonify({"ok": True, "gender": gender, "candidates": candidates, "extra_items": extra_items})


@bp.route("/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    task = _get_current_user_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    lang = _av_task_target_lang(task)
    body = request.get_json(silent=True) or {}

    from appcore.video_translate_defaults import resolve_default_voice

    try:
        normalized = normalize_confirm_voice_payload(
            body=body,
            lang=lang or "",
            default_voice_id=resolve_default_voice(lang, user_id=current_user.id) if lang else None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    store.update(
        task_id,
        type="translation",
        selected_voice_id=normalized["voice_id"],
        selected_voice_name=normalized["voice_name"],
        voice_id=normalized["voice_id"],
        subtitle_font=normalized["subtitle_font"],
        subtitle_size=normalized["subtitle_size"],
        subtitle_position_y=normalized["subtitle_position_y"],
        subtitle_position=normalized["subtitle_position"],
        pipeline_version="av",
        target_lang=lang or task.get("target_lang"),
    )
    store.set_step(task_id, "voice_match", "done")
    store.set_current_review_step(task_id, "")

    updated_task = store.get(task_id) or task
    try:
        _ensure_local_source_video(task_id, updated_task)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 409

    pipeline_runner.resume(
        task_id,
        "alignment",
        user_id=current_user.id if current_user.is_authenticated else None,
    )
    return jsonify({"ok": True, "voice_id": normalized["voice_id"], "voice_name": normalized["voice_name"]})


@bp.route("/<task_id>/thumbnail")
@login_required
def thumbnail(task_id: str):
    if _is_admin_user():
        row = db_query_one(
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND deleted_at IS NULL",
            (task_id,),
        )
    else:
        row = db_query_one(
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND user_id = %s",
            (task_id, current_user.id),
        )
    if not row or not row.get("thumbnail_path") or not os.path.exists(row["thumbnail_path"]):
        abort(404)
    from web.services.artifact_download import safe_task_file_response
    return safe_task_file_response(
        row,
        row["thumbnail_path"],
        not_found_message="thumbnail not found",
        mimetype="image/jpeg",
    )


@bp.route("/<task_id>/artifact/<name>", methods=["GET"])
@login_required
def get_artifact(task_id, name):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    variant = request.args.get("variant") or None

    from web.services.artifact_download import preview_artifact_tos_redirect
    tos_resp = preview_artifact_tos_redirect(task, name, variant=variant)
    if tos_resp is not None:
        return tos_resp

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


_ALLOWED_ROUND_KINDS = {
    "localized_translation":        ("localized_translation.round_{r}.json",       "application/json"),
    "localized_rewrite_messages":   ("localized_rewrite_messages.round_{r}.json",  "application/json"),
    "initial_translate_messages":   ("localized_translate_messages.json",          "application/json"),
    "tts_script":                   ("tts_script.round_{r}.json",                  "application/json"),
    "tts_full_audio":               ("tts_full.round_{r}.mp3",                     "audio/mpeg"),
}


@bp.route("/<task_id>/round-file/<int:round_index>/<kind>", methods=["GET"])
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    """Serve per-round intermediate artifacts for the default (English) translation pipeline."""
    if round_index not in (1, 2, 3, 4, 5):
        abort(404)
    if kind not in _ALLOWED_ROUND_KINDS:
        abort(404)

    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    filename_pattern, mime = _ALLOWED_ROUND_KINDS[kind]
    filename = filename_pattern.format(r=round_index)
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

    # conditional=False 禁用 304，避免浏览器对 round 文件命中 If-None-Match 后
    # 返回空 body，前端 res.json() 爆 "Unexpected end of JSON input"。


@bp.route("/<task_id>/restart", methods=["POST"])
@login_required
def restart(task_id):
    """清掉上一轮的中间/结果/TOS 产物，按新参数从头跑一遍。"""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = _request_payload()
    av_inputs = _collect_av_translate_inputs(body, current_task=task)
    av_error = _validate_av_translate_inputs(av_inputs)
    if av_error:
        return jsonify({"error": av_error}), 400
    source_updates, source_error = _collect_av_source_language(body, current_task=task)
    if source_error:
        return jsonify({"error": source_error}), 400
    store.update(
        task_id,
        type="translation",
        pipeline_version="av",
        target_lang=av_inputs["target_language"],
        av_translate_inputs=av_inputs,
        **source_updates,
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
        interactive_review=_parse_bool(body.get("interactive_review", False)),
        user_id=current_user.id if current_user.is_authenticated else None,
        runner=pipeline_runner,
        step_order=AV_SYNC_STEPS,
    )
    updated = store.get(task_id) or updated
    return jsonify({"status": "restarted", "task": updated})


@bp.route("/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
    """配置并启动流水线"""
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = _request_payload()
    av_inputs = _collect_av_translate_inputs(body, current_task=task)
    av_error = _validate_av_translate_inputs(av_inputs)
    if av_error:
        return jsonify({"error": av_error}), 400
    source_updates, source_error = _collect_av_source_language(body, current_task=task)
    if source_error:
        return jsonify({"error": source_error}), 400
    current_steps = task.get("steps") or {}
    current_messages = task.get("step_messages") or {}
    av_steps = {step: current_steps.get(step, "pending") for step in AV_SYNC_STEPS}
    av_step_messages = {step: current_messages.get(step, "") for step in AV_SYNC_STEPS}
    store.update(
        task_id,
        type="translation",
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        subtitle_font=body.get("subtitle_font", "Impact"),
        subtitle_size=body.get("subtitle_size", 14),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        interactive_review=_parse_bool(body.get("interactive_review", False)),
        pipeline_version="av",
        av_translate_inputs=av_inputs,
        target_lang=av_inputs["target_language"],
        **source_updates,
        steps=av_steps,
        step_messages=av_step_messages,
    )
    task = store.get(task_id) or task

    if _task_requires_source_sync(task):
        try:
            _ensure_local_source_video(task_id, task)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 409
        updated_task = store.get(task_id) or task
        return jsonify({"status": "source_ready", "task": updated_task})

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
    if model_provider in _VALID_TRANSLATE_PREFS:
        updates["custom_translate_provider"] = model_provider
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
    if model_provider not in _VALID_TRANSLATE_PREFS:
        from appcore.api_keys import get_key
        model_provider = get_key(current_user.id, "translate_pref") or "openrouter"

    from pipeline.translate import generate_localized_translation, get_model_display_name
    from pipeline.localization import build_source_full_text_zh

    script_segments = task.get("script_segments") or []
    source_full_text_zh = build_source_full_text_zh(script_segments)
    if model_provider == "doubao":
        billing_provider = "doubao"
    elif model_provider.startswith("vertex_adc_"):
        billing_provider = "gemini_vertex_adc"
    elif model_provider.startswith("vertex_"):
        billing_provider = "gemini_vertex"
    else:
        billing_provider = "openrouter"
    resolved_model = get_model_display_name(model_provider, current_user.id)

    try:
        result = generate_localized_translation(
            source_full_text_zh, script_segments, variant="normal",
            custom_system_prompt=prompt_text,
            provider=model_provider, user_id=current_user.id,
        )
        usage = result.get("_usage") or {}
        ai_billing.log_request(
            use_case_code="video_translate.localize",
            user_id=current_user.id,
            project_id=task_id,
            provider=billing_provider,
            model=resolved_model,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            units_type="tokens",
            response_cost_cny=usage.get("cost_cny"),
            success=True,
            extra={"source": "task.retranslate"},
            request_payload=_llm_request_payload(
                result, model_provider, "video_translate.localize"
            ),
            response_payload=_llm_response_payload(result),
        )
    except Exception as exc:
        ai_billing.log_request(
            use_case_code="video_translate.localize",
            user_id=current_user.id,
            project_id=task_id,
            provider=billing_provider,
            model=resolved_model,
            units_type="tokens",
            success=False,
            extra={"source": "task.retranslate", "error": str(exc)[:500]},
            request_payload={
                "type": "chat",
                "use_case_code": "video_translate.localize",
                "provider": model_provider,
                "source_full_text": source_full_text_zh,
                "script_segments": script_segments,
                "custom_system_prompt": prompt_text,
            },
            response_payload={"error": str(exc)[:500]},
        )
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
    if str(updated_task.get("pipeline_version") or "").strip() == "av":
        variant_state = dict((updated_task.get("variants") or {}).get("av") or {})
        existing_sentences = [
            dict(item)
            for item in (variant_state.get("sentences") or [])
            if isinstance(item, dict)
        ]
        existing_by_asr = {
            int(sentence.get("asr_index", sentence.get("index", idx))): sentence
            for idx, sentence in enumerate(existing_sentences)
        }
        av_sentences = []
        for fallback_index, segment in enumerate(body["segments"]):
            if not isinstance(segment, dict):
                continue
            asr_index = int(segment.get("asr_index", segment.get("index", fallback_index)))
            base = dict(existing_by_asr.get(asr_index, {}))
            translated = str(segment.get("translated") or segment.get("target_text") or segment.get("text") or "")
            base.update(
                {
                    "asr_index": asr_index,
                    "text": translated,
                    "est_chars": len(translated),
                    "start_time": float(segment.get("start_time", base.get("start_time", 0.0)) or 0.0),
                    "end_time": float(segment.get("end_time", base.get("end_time", 0.0)) or 0.0),
                    "source_text": str(segment.get("text") or base.get("source_text") or ""),
                }
            )
            if "target_duration" not in base:
                base["target_duration"] = max(0.0, base["end_time"] - base["start_time"])
            av_sentences.append(base)
        localized_translation = _build_av_localized_translation(av_sentences)
        store.update_variant(
            task_id,
            "av",
            sentences=av_sentences,
            localized_translation=localized_translation,
        )
        store.update(task_id, localized_translation=localized_translation, segments=av_sentences)
        updated_task = store.get(task_id) or updated_task
    store.set_artifact(task_id, "translate", _build_translate_compare_artifact(updated_task))
    store.set_current_review_step(task_id, "")
    store.set_step(task_id, "translate", "done")
    store.set_step_message(task_id, "translate", "翻译确认完成")
    pipeline_runner.resume(task_id, "tts", user_id=current_user.id if current_user.is_authenticated else None)
    return jsonify({"status": "ok"})


@bp.route("/<task_id>/av/rewrite_sentence", methods=["POST"])
@login_required
def av_rewrite_sentence(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    variant = "av"
    variant_state = dict((task.get("variants") or {}).get(variant) or {})
    sentences = [dict(item) for item in (variant_state.get("sentences") or []) if isinstance(item, dict)]
    if not sentences:
        return jsonify({"error": "当前任务没有可重写的音画同步句子"}), 400

    body = request.get_json(silent=True) or {}
    try:
        asr_index = int(body.get("asr_index"))
    except (TypeError, ValueError):
        return jsonify({"error": "asr_index 非法"}), 400
    new_text = str(body.get("text") or "").strip()
    if not new_text:
        return jsonify({"error": "text 不能为空"}), 400

    sentence_index = None
    for idx, sentence in enumerate(sentences):
        current_asr_index = int(sentence.get("asr_index", sentence.get("index", idx)))
        if current_asr_index == asr_index:
            sentence_index = idx
            break
    if sentence_index is None:
        return jsonify({"error": "未找到对应句子"}), 404

    task_dir = str(task.get("task_dir") or "").strip()
    if not task_dir:
        return jsonify({"error": "任务目录缺失，无法重写"}), 400

    resolved_voice_id, elevenlabs_voice_id = _resolve_av_voice_ids(task, variant_state)
    if not elevenlabs_voice_id:
        return jsonify({"error": "未找到可用音色，无法重写配音"}), 400

    av_inputs = task.get("av_translate_inputs") or {}
    target_language = str(av_inputs.get("target_language") or "en").strip().lower() or "en"

    updated_sentence = dict(sentences[sentence_index])
    attempts = updated_sentence.get("attempts")
    updated_sentence["attempts"] = attempts if isinstance(attempts, list) else []
    segment_path = updated_sentence.get("tts_path") or os.path.join(
        task_dir,
        "tts_segments",
        variant,
        f"seg_{sentence_index:04d}.mp3",
    )
    updated_sentence["text"] = new_text
    updated_sentence["est_chars"] = len(new_text)
    updated_sentence["tts_path"] = segment_path

    tts.generate_segment_audio(
        text=new_text,
        voice_id=elevenlabs_voice_id,
        output_path=segment_path,
        language_code=target_language,
    )
    tts_duration = float(tts.get_audio_duration(segment_path) or 0.0)
    target_duration = float(updated_sentence.get("target_duration", 0.0) or 0.0)
    status, _speed = classify_overshoot(target_duration, tts_duration)
    updated_sentence["tts_duration"] = tts_duration
    updated_sentence["status"] = status
    updated_sentence["speed"] = 1.0
    updated_sentence["duration_ratio"] = duration_ratio(target_duration, tts_duration)

    if status == "ok":
        speed = compute_speed_for_target(target_duration, tts_duration)
        if speed is not None and speed != 1.0:
            tts.generate_segment_audio(
                text=new_text,
                voice_id=elevenlabs_voice_id,
                output_path=segment_path,
                language_code=target_language,
                speed=speed,
            )
            updated_sentence["tts_duration"] = float(tts.get_audio_duration(segment_path) or 0.0)
            updated_sentence["duration_ratio"] = duration_ratio(target_duration, updated_sentence["tts_duration"])
            updated_sentence["status"] = "speed_adjusted"
            updated_sentence["speed"] = speed
    elif status == "needs_rewrite":
        updated_sentence["status"] = "warning_long"
        updated_sentence["speed"] = 1.0
    elif status == "needs_expand":
        updated_sentence["status"] = "warning_short"
        updated_sentence["speed"] = 1.0

    sentences[sentence_index] = updated_sentence
    localized_translation = _build_av_localized_translation(sentences)
    tts_segments = _build_av_tts_segments(sentences)
    full_audio_path = _rebuild_tts_full_audio(task_dir, tts_segments, variant)

    sync_granularity = str((av_inputs or {}).get("sync_granularity") or "hybrid")
    subtitle_units = build_subtitle_units_from_sentences(sentences, mode=sync_granularity)
    srt_content = build_srt_from_chunks(subtitle_units)
    srt_path = save_srt(srt_content, os.path.join(task_dir, f"subtitle.{variant}.srt"))

    (
        result,
        exports,
        artifacts,
        preview_files,
        tos_uploads,
        variant_result,
        variant_exports,
        variant_artifacts,
        variant_preview_files,
    ) = _clear_av_compose_outputs(task, variant_state, variant=variant)

    artifacts["tts"] = build_tts_artifact(tts_segments)
    artifacts["subtitle"] = build_subtitle_artifact(srt_content, target_language=target_language)
    preview_files["tts_full_audio"] = full_audio_path
    preview_files["srt"] = srt_path

    variant_artifacts["tts"] = build_tts_artifact(tts_segments)
    variant_artifacts["subtitle"] = build_subtitle_artifact(srt_content, target_language=target_language)
    variant_preview_files["tts_full_audio"] = full_audio_path
    variant_preview_files["srt"] = srt_path

    steps = dict(task.get("steps") or {})
    steps["tts"] = "done"
    steps["subtitle"] = "done"
    steps["compose"] = "done"
    steps["export"] = "done"

    step_messages = dict(task.get("step_messages") or {})
    step_messages["tts"] = f"句子 #{asr_index} 配音已更新"
    step_messages["subtitle"] = "字幕已基于最新配音重新生成"
    step_messages["compose"] = "配音或字幕已更新，请从此步继续重新合成"
    step_messages["export"] = "配音或字幕已更新，请从此步继续重新导出"

    variant_state.update(
        {
            "voice_id": resolved_voice_id or variant_state.get("voice_id"),
            "sentences": sentences,
            "localized_translation": localized_translation,
            "tts_result": {"full_audio_path": full_audio_path, "segments": tts_segments},
            "tts_audio_path": full_audio_path,
            "subtitle_units": subtitle_units,
            "srt_path": srt_path,
            "corrected_subtitle": {"chunks": subtitle_units, "srt_content": srt_content},
            "result": variant_result,
            "exports": variant_exports,
            "artifacts": variant_artifacts,
            "preview_files": variant_preview_files,
        }
    )
    variants = dict(task.get("variants") or {})
    variants[variant] = variant_state

    store.update(
        task_id,
        status="done",
        variants=variants,
        steps=steps,
        step_messages=step_messages,
        segments=tts_segments,
        localized_translation=localized_translation,
        tts_audio_path=full_audio_path,
        srt_path=srt_path,
        corrected_subtitle={"chunks": subtitle_units, "srt_content": srt_content},
        result=result,
        exports=exports,
        artifacts=artifacts,
        preview_files=preview_files,
        tos_uploads=tos_uploads,
        voice_id=resolved_voice_id or task.get("voice_id"),
    )
    updated_task = store.get(task_id) or task
    return jsonify(
        {
            "ok": True,
            "status": updated_sentence["status"],
            "tts_duration": updated_sentence["tts_duration"],
            "compose_stale": True,
            "task": updated_task,
        }
    )


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
    safe_project_dir = safe_task_dir_path(task, project_dir)
    if not safe_project_dir:
        return jsonify({"error": "CapCut project not ready"}), 404

    deployed_project_dir = deploy_capcut_project(safe_project_dir)
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


RESUMABLE_STEPS = list(AV_SYNC_STEPS)


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

    resume_payload = {"status": "running", "error": "", "current_review_step": ""}
    if (task.get("pipeline_version") or "") == "av":
        resume_payload["type"] = "translation"
    store.update(task_id, **resume_payload)
    task = store.get(task_id) or task
    try:
        _ensure_local_source_video(task_id, task)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 409

    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.resume(task_id, start_step, user_id=user_id)
    return jsonify({"status": "started", "start_step": start_step})


@bp.route("/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发 AI 视频分析（评分 + CSK），不影响任务整体 status。"""
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

    user_id = current_user.id if current_user.is_authenticated else None
    if not pipeline_runner.run_analysis(task_id, user_id=user_id):
        return jsonify({"error": "AI 分析正在运行中"}), 409
    return jsonify({"status": "started"})
