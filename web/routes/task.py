"""
任务管理蓝图

负责视频上传、任务生命周期管理、翻译确认、文件下载。
不包含任何业务执行逻辑，执行逻辑在 services/pipeline_runner.py。
"""
import os
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, render_template, abort, redirect, make_response
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
    AV_TARGET_MARKET_OPTIONS,
    build_available_av_translate_inputs,
    list_available_av_target_language_options,
)
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import cleanup
from appcore.task_recovery import recover_task_if_needed
from pipeline.alignment import build_script_segments
from pipeline import tts
from pipeline.av_subtitle_units import build_subtitle_units_from_sentences
from pipeline.duration_reconcile import classify_overshoot, compute_speed_for_target, duration_ratio
from pipeline.subtitle import build_srt_from_chunks, save_srt
from web.preview_artifacts import (
    build_alignment_artifact,
    build_subtitle_artifact,
    build_tts_artifact,
)
from web import store
from web.services import pipeline_runner
from web.services.artifact_download import (
    resolve_preview_artifact_path,
    send_file_with_range,
    serve_artifact_download,
)
from web.services.task_av_inputs import (
    AV_SYNC_STEPS,
    av_task_target_lang,
    collect_av_source_language,
    collect_av_translate_inputs,
    validate_av_translate_inputs,
)
from web.services.task_av_rewrite import (
    build_translate_compare_artifact,
    clear_av_compose_outputs,
    rebuild_tts_full_audio,
    resolve_av_voice_ids,
)
from web.services.task_access import get_user_task, is_admin_user, load_task, optional_user_id, refresh_task
from web.services.task_analysis import start_task_analysis
from web.services.task_capcut import deploy_task_capcut_project
from web.services.task_deletion import cleanup_deleted_task_storage
from web.services.task_llm import resolve_translate_billing_provider
from web.services.task_prompts import resolve_task_prompt_text
from web.services.task_rename import rename_task_display_name
from web.services.task_responses import task_not_found_response
from web.services.task_resume import resume_task_from_step
from web.services.task_start import start_task_pipeline
from web.services.task_start_inputs import json_payload_from, parse_bool, request_payload_from
from web.services.task_thumbnail import resolve_task_thumbnail_row
from web.services.task_upload import initialize_uploaded_av_task
from web.services.task_voice import confirm_task_voice
from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    lookup_default_voice_row,
)
from appcore.db import query_one as db_query_one, execute as db_execute, query as db_query

bp = Blueprint("task", __name__, url_prefix="/api/tasks")


from pipeline.ffutil import extract_thumbnail as _extract_thumbnail


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


@bp.route("", methods=["POST"])
@login_required
def upload():
    """上传视频，创建任务，返回 task_id"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    from web.upload_util import save_uploaded_video, validate_video_extension

    original_filename = os.path.basename(file.filename)
    if not validate_video_extension(original_filename):
        return jsonify({"error": "涓嶆敮鎸佺殑瑙嗛鏍煎紡"}), 400
    form_payload = request.form.to_dict(flat=True)
    av_inputs = collect_av_translate_inputs(form_payload)
    av_error = validate_av_translate_inputs(av_inputs)
    if av_error:
        return jsonify({"error": av_error}), 400
    source_updates, source_error = collect_av_source_language(form_payload)
    if source_error:
        return jsonify({"error": source_error}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_path, file_size, content_type = save_uploaded_video(file, UPLOAD_DIR, task_id, original_filename)
    user_id = optional_user_id(current_user)

    result = initialize_uploaded_av_task(
        task_id,
        video_path=video_path,
        task_dir=task_dir,
        original_filename=original_filename,
        form_payload=form_payload,
        av_inputs=av_inputs,
        source_updates=source_updates,
        file_size=file_size,
        content_type=content_type,
        user_id=user_id,
        query_one=db_query_one,
        execute=db_execute,
    )
    return jsonify(result.payload), 201


@bp.route("/<task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    recover_task_if_needed(task_id)
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()
    return jsonify(task)


@bp.route("/<task_id>/subtitle-preview", methods=["GET"])
@login_required
def subtitle_preview(task_id: str):
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()
    return jsonify(build_multi_translate_preview_payload(task_id, current_user.id, api_base="/api/tasks"))


@bp.route("/user-default-voice", methods=["PUT"])
@login_required
def set_user_default_voice_route():
    body = json_payload_from(request)
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()
    lang = av_task_target_lang(task)
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()
    lang = av_task_target_lang(task)
    if not lang:
        return jsonify({"error": "task has no target_lang"}), 400

    body = json_payload_from(request)
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()
    outcome = confirm_task_voice(
        task_id,
        task,
        json_payload_from(request),
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
    )
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/thumbnail")
@login_required
def thumbnail(task_id: str):
    row = resolve_task_thumbnail_row(
        task_id,
        user_id=current_user.id,
        is_admin=is_admin_user(current_user),
        query_one=db_query_one,
    )
    if not row:
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()
    variant = request.args.get("variant") or None

    from web.services.artifact_download import preview_artifact_tos_redirect
    tos_resp = preview_artifact_tos_redirect(task, name, variant=variant)
    if tos_resp is not None:
        return tos_resp

    path = resolve_preview_artifact_path(task_id, name, task, variant=variant)
    if not path:
        return jsonify({"error": "Artifact not found"}), 404

    return send_file_with_range(path)


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

    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = request_payload_from(request)
    av_inputs = collect_av_translate_inputs(body, current_task=task)
    av_error = validate_av_translate_inputs(av_inputs)
    if av_error:
        return jsonify({"error": av_error}), 400
    source_updates, source_error = collect_av_source_language(body, current_task=task)
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
        interactive_review=parse_bool(body.get("interactive_review", False)),
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
        step_order=AV_SYNC_STEPS,
    )
    updated = refresh_task(task_id, updated)
    return jsonify({"status": "restarted", "task": updated})


@bp.route("/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
    """配置并启动流水线"""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = request_payload_from(request)
    av_inputs = collect_av_translate_inputs(body, current_task=task)
    av_error = validate_av_translate_inputs(av_inputs)
    if av_error:
        return jsonify({"error": av_error}), 400
    source_updates, source_error = collect_av_source_language(body, current_task=task)
    if source_error:
        return jsonify({"error": source_error}), 400
    outcome = start_task_pipeline(
        task_id,
        task,
        body,
        av_inputs=av_inputs,
        source_updates=source_updates,
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
    )
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/start-translate", methods=["POST"])
@login_required
def start_translate(task_id):
    """User picks model + prompt, then starts the translate step."""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    if not task.get("_translate_pre_select"):
        return jsonify({"error": "翻译步骤不在预选状态"}), 400

    body = json_payload_from(request)
    model_provider = body.get("model_provider", "").strip()
    prompt_id = body.get("prompt_id")
    prompt_text = resolve_task_prompt_text(
        (body.get("prompt_text") or "").strip(),
        prompt_id,
        user_id=current_user.id,
    )

    # Save choices to task state so runtime can read them
    updates = {"_translate_pre_select": False}
    if model_provider in _VALID_TRANSLATE_PREFS:
        updates["custom_translate_provider"] = model_provider
    if prompt_text:
        updates["custom_translate_prompt"] = prompt_text

    store.update(task_id, **updates)
    store.set_current_review_step(task_id, "")

    user_id = optional_user_id(current_user)
    pipeline_runner.resume(task_id, "translate", user_id=user_id)
    return jsonify({"status": "started"})


@bp.route("/<task_id>/retranslate", methods=["POST"])
@login_required
def retranslate(task_id):
    """Re-run translation with a different prompt. Stores result alongside existing translations."""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    step_status = (task.get("steps") or {}).get("translate")
    if step_status not in ("done", "error"):
        return jsonify({"error": "翻译步骤尚未完成，无法重新翻译"}), 400

    body = json_payload_from(request)
    prompt_id = body.get("prompt_id")
    prompt_text = resolve_task_prompt_text(
        (body.get("prompt_text") or "").strip(),
        prompt_id,
        user_id=current_user.id,
    )
    model_provider = body.get("model_provider", "").strip()

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
    billing_provider = resolve_translate_billing_provider(model_provider)
    resolved_model = get_model_display_name(model_provider, current_user.id)

    try:
        result = generate_localized_translation(
            source_full_text_zh, script_segments, variant="normal",
            custom_system_prompt=prompt_text,
            provider=model_provider, user_id=current_user.id,
            use_case="video_translate.localize",
            project_id=task_id,
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = json_payload_from(request)
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = json_payload_from(request)
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
        pipeline_runner.resume(task_id, "translate", user_id=optional_user_id(current_user))
    return jsonify({"status": "ok", "script_segments": script_segments})


@bp.route("/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id):
    """用户确认/编辑翻译结果"""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = request.get_json()
    if not body or "segments" not in body:
        return jsonify({"error": "segments required"}), 400

    store.confirm_segments(task_id, body["segments"])
    updated_task = refresh_task(task_id, task)
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
        updated_task = refresh_task(task_id, updated_task)
    store.set_artifact(task_id, "translate", build_translate_compare_artifact(updated_task))
    store.set_current_review_step(task_id, "")
    store.set_step(task_id, "translate", "done")
    store.set_step_message(task_id, "translate", "翻译确认完成")
    pipeline_runner.resume(task_id, "tts", user_id=optional_user_id(current_user))
    return jsonify({"status": "ok"})


@bp.route("/<task_id>/av/rewrite_sentence", methods=["POST"])
@login_required
def av_rewrite_sentence(task_id):
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    variant = "av"
    variant_state = dict((task.get("variants") or {}).get(variant) or {})
    sentences = [dict(item) for item in (variant_state.get("sentences") or []) if isinstance(item, dict)]
    if not sentences:
        return jsonify({"error": "当前任务没有可重写的音画同步句子"}), 400

    body = json_payload_from(request)
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

    resolved_voice_id, elevenlabs_voice_id = resolve_av_voice_ids(task, variant_state, user_id=current_user.id)
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
    full_audio_path = rebuild_tts_full_audio(task_dir, tts_segments, variant)

    sync_granularity = str((av_inputs or {}).get("sync_granularity") or "hybrid")
    subtitle_units = build_subtitle_units_from_sentences(sentences, mode=sync_granularity)
    srt_content = build_srt_from_chunks(subtitle_units)
    srt_path = save_srt(srt_content, os.path.join(task_dir, f"subtitle.{variant}.srt"))

    cleared_outputs = clear_av_compose_outputs(task, variant_state, variant=variant)
    result = cleared_outputs.result
    exports = cleared_outputs.exports
    artifacts = cleared_outputs.artifacts
    preview_files = cleared_outputs.preview_files
    tos_uploads = cleared_outputs.tos_uploads
    variant_result = cleared_outputs.variant_result
    variant_exports = cleared_outputs.variant_exports
    variant_artifacts = cleared_outputs.variant_artifacts
    variant_preview_files = cleared_outputs.variant_preview_files

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
    updated_task = refresh_task(task_id, task)
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
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    variant = request.args.get("variant") or None
    return serve_artifact_download(task, task_id, file_type, variant=variant)


@bp.route("/<task_id>/deploy/capcut", methods=["POST"])
@login_required
def deploy_capcut(task_id):
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    variant = request.args.get("variant") or None
    result = deploy_task_capcut_project(task_id, task, variant=variant)
    if result is None:
        return jsonify({"error": "CapCut project not ready"}), 404
    return jsonify({"status": "ok", **result})


@bp.route("/<task_id>", methods=["PATCH"])
@login_required
def rename_task(task_id):
    """重命名任务展示名称"""
    outcome = rename_task_display_name(
        task_id,
        json_payload_from(request),
        user_id=current_user.id,
        query_one=db_query_one,
        execute=db_execute,
    )
    if outcome.not_found:
        return task_not_found_response()
    if outcome.error:
        return jsonify({"error": outcome.error}), outcome.status_code
    return jsonify(outcome.payload)


@bp.route("/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    """软删除任务（设置 deleted_at）"""
    row = db_query_one(
        "SELECT id, task_dir, state_json FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return task_not_found_response()

    cleanup_deleted_task_storage(
        refresh_task(task_id, {}),
        row,
        collect_task_tos_keys=cleanup.collect_task_tos_keys,
        delete_task_storage=cleanup.delete_task_storage,
    )

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
    """从指定步骤重新开始流水线，该步骤之前已完成的结果保留不动。"""
    body = json_payload_from(request)
    outcome = resume_task_from_step(
        task_id,
        user_id=current_user.id,
        start_step=body.get("start_step", ""),
        resumable_steps=RESUMABLE_STEPS,
        query_one=db_query_one,
    )
    if outcome.not_found:
        return task_not_found_response()
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/analysis/run", methods=["POST"])
@login_required
def run_ai_analysis(task_id):
    """手动触发 AI 视频分析（评分 + CSK），不影响任务整体 status。"""
    outcome = start_task_analysis(
        task_id,
        user_id=current_user.id,
        query_one=db_query_one,
        load_task=load_task,
    )
    if outcome.not_found:
        return task_not_found_response()
    return jsonify(outcome.payload), outcome.status_code
