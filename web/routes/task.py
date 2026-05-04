"""
任务管理蓝图

负责视频上传、任务生命周期管理、翻译确认、文件下载。
不包含任何业务执行逻辑，执行逻辑在 services/pipeline_runner.py。
"""
import os
import uuid

from flask import Blueprint, request, jsonify, render_template, abort, redirect, make_response
from flask_login import login_required, current_user

from appcore.av_translate_inputs import (
    AV_TARGET_LANGUAGE_CODES,
    AV_TARGET_MARKET_OPTIONS,
    build_available_av_translate_inputs,
    list_available_av_target_language_options,
)
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload
from config import OUTPUT_DIR, UPLOAD_DIR
from appcore.task_recovery import recover_task_if_needed
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
from web.services.task_av_rewrite import rewrite_task_av_sentence
from web.services.task_access import get_user_task, is_admin_user, load_task, optional_user_id
from web.services.task_alignment import confirm_task_alignment
from web.services.task_analysis import start_task_analysis
from web.services.task_capcut import deploy_task_capcut_project
from web.services.task_deletion import delete_task_workflow
from web.services.task_rename import rename_task_display_name
from web.services.task_responses import task_not_found_response
from web.services.task_resume import resume_task_from_step
from web.services.task_retranslate import retranslate_task
from web.services.task_restart import restart_task_workflow
from web.services.task_segments import confirm_task_segments
from web.services.task_start import start_task_pipeline
from web.services.task_start_inputs import json_payload_from, parse_bool, request_payload_from
from web.services.task_thumbnail import resolve_task_thumbnail_row
from web.services.task_translate import start_task_translate
from web.services.task_translation_selection import select_task_translation
from web.services.task_upload import initialize_uploaded_av_task
from web.services.task_voice import confirm_task_voice
from web.services.task_voice_rematch import rematch_task_voice
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
    outcome = rematch_task_voice(
        task_id,
        task,
        json_payload_from(request),
        user_id=optional_user_id(current_user),
    )
    return jsonify(outcome.payload), outcome.status_code


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
    outcome = restart_task_workflow(
        task_id,
        body,
        av_inputs=av_inputs,
        source_updates=source_updates,
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
        step_order=AV_SYNC_STEPS,
    )
    return jsonify(outcome.payload), outcome.status_code


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

    body = json_payload_from(request)
    outcome = start_task_translate(
        task_id,
        task,
        body,
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
    )
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/retranslate", methods=["POST"])
@login_required
def retranslate(task_id):
    """Re-run translation with a different prompt. Stores result alongside existing translations."""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = json_payload_from(request)
    outcome = retranslate_task(
        task_id,
        task,
        body,
        user_id=optional_user_id(current_user),
    )
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/select-translation", methods=["PUT"])
@login_required
def select_translation(task_id):
    """Select one of the translation attempts as the active translation."""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = json_payload_from(request)
    outcome = select_task_translation(task_id, task, body)
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/alignment", methods=["PUT"])
@login_required
def update_alignment(task_id):
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = json_payload_from(request)
    outcome = confirm_task_alignment(
        task_id,
        task,
        body,
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
    )
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/segments", methods=["PUT"])
@login_required
def update_segments(task_id):
    """用户确认/编辑翻译结果"""
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    body = json_payload_from(request)
    outcome = confirm_task_segments(
        task_id,
        task,
        body,
        user_id=optional_user_id(current_user),
        runner=pipeline_runner,
    )
    return jsonify(outcome.payload), outcome.status_code


@bp.route("/<task_id>/av/rewrite_sentence", methods=["POST"])
@login_required
def av_rewrite_sentence(task_id):
    task = get_user_task(task_id, user_id=current_user.id)
    if not task:
        return task_not_found_response()

    outcome = rewrite_task_av_sentence(
        task_id,
        task,
        json_payload_from(request),
        user_id=current_user.id,
    )
    return jsonify(outcome.payload), outcome.status_code


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
    outcome = delete_task_workflow(
        task_id,
        user_id=current_user.id,
        query_one=db_query_one,
        execute=db_execute,
    )
    if outcome.not_found:
        return task_not_found_response()
    return jsonify(outcome.payload), outcome.status_code


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


# AI 视频分析（多模态，ADC 通道）—— 与 multi/omni 共用 service + DB 表，
# source_type='av_sync_task' 单独归类。视频翻译音画通话详情页的按钮调这里。
def _can_view_av_task(task_id: str) -> bool:
    task = load_task(task_id)
    if not task:
        return False
    if is_admin_user(current_user):
        return True
    return task.get("_user_id") == current_user.id


@bp.route("/<task_id>/video-ai-review/run", methods=["POST"])
@login_required
def run_video_ai_review(task_id):
    if not _can_view_av_task(task_id):
        return task_not_found_response()
    from appcore import video_ai_review
    try:
        run_id = video_ai_review.trigger_review(
            source_type="av_sync_task",
            source_id=task_id,
            user_id=current_user.id,
            triggered_by="manual",
        )
    except video_ai_review.ReviewInProgressError as exc:
        return jsonify({
            "error": "AI 视频分析正在运行中",
            "in_flight_run_id": exc.run_id,
        }), 409
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("[video-ai-review] av_sync trigger failed task=%s", task_id)
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "status": "started", "run_id": run_id,
        "channel": video_ai_review.CHANNEL,
        "model": video_ai_review.MODEL,
    })


@bp.route("/<task_id>/video-ai-review", methods=["GET"])
@login_required
def get_video_ai_review(task_id):
    if not _can_view_av_task(task_id):
        return task_not_found_response()
    from appcore import video_ai_review
    payload = video_ai_review.latest_review("av_sync_task", task_id)
    return jsonify({"review": payload})
