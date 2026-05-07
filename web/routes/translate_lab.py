"""视频翻译（测试）模块蓝图。

包含：
- 列表页与详情页（Task 2 已实现）
- 新建任务上传 API（Task 14）
- 启动 / 恢复 API、音色确认 API（Task 13）
- 管理员触发共享音色库全量同步、embedding 回填 API（Task 13）

模块内部字段与流水线均遵循 ``appcore.task_state.create_translate_lab``
的 7 步骨架。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, render_template, abort, request
from flask_login import login_required, current_user

from web.auth import admin_required

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import task_state, translate_lab_store
from appcore.api_keys import resolve_key
from appcore.settings import get_retention_hours
from pipeline.voice_library_sync import (
    embed_missing_voices,
    sync_all_shared_voices,
)
from web import store
from web.services.artifact_download import safe_task_file_response
from web.services import translate_lab_runner
from web.services.translate_lab import (
    build_translate_lab_created_response,
    build_translate_lab_embed_response,
    build_translate_lab_error_response,
    build_translate_lab_ok_response,
    build_translate_lab_payload_response,
    build_translate_lab_sync_response,
    build_translate_lab_voice_confirmed_response,
    translate_lab_flask_response,
)
from web.upload_util import client_filename_basename, save_uploaded_file_to_path, validate_video_extension

log = logging.getLogger(__name__)

bp = Blueprint("translate_lab", __name__)

db_query = translate_lab_store.query
db_query_one = translate_lab_store.query_one
db_execute = translate_lab_store.execute

# 允许的目标语言 / 源语言 / 音色匹配模式
_ALLOWED_SOURCE_LANGUAGES = {"zh", "en"}
_ALLOWED_TARGET_LANGUAGES = {"en", "de", "fr", "ja", "es", "pt", "nl", "sv", "fi"}
_ALLOWED_VOICE_MODES = {"auto", "manual"}


def _default_display_name(original_filename: str) -> str:
    """截取文件名前 10 字作为默认显示名。"""
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str) -> str:
    """若 display_name 已被同一用户占用，则追加 (2)/(3)/... 递增。"""
    base = desired_name
    candidate = base
    n = 2
    while True:
        row = translate_lab_store.find_project_by_display_name(
            user_id,
            candidate,
            query_one_func=db_query_one,
        )
        if not row:
            return candidate
        candidate = f"{base} ({n})"
        n += 1


def _get_lab_task(task_id: str, user_id: int) -> dict | None:
    """从 task_state 或 DB 中取任务，仅限本用户的 translate_lab 任务。"""
    task = task_state.get(task_id)
    if not task:
        return None
    # ``task_state`` 中 user id 字段为 ``_user_id``（下划线前缀）
    owner = task.get("_user_id") or task.get("user_id")
    if owner is not None and int(owner) != int(user_id):
        return None
    task_type = task.get("type") or task.get("project_type")
    if task_type != "translate_lab":
        return None
    return task


@bp.route("/translate-lab")
@login_required
def index():
    rows = translate_lab_store.list_user_projects(
        current_user.id,
        query_func=db_query,
    )
    # source_language / target_language live inside state_json, not as DB
    # columns — parse them out so templates can display actual values instead
    # of defaulting to zh/en for every project.
    for row in (rows or []):
        raw = row.pop("state_json", None)
        if raw:
            try:
                state = json.loads(raw)
                row["source_language"] = state.get("source_language") or "zh"
                row["target_language"] = state.get("target_language") or "en"
            except Exception:
                row["source_language"] = "zh"
                row["target_language"] = "en"
        else:
            row["source_language"] = "zh"
            row["target_language"] = "en"
    try:
        retention_hours = get_retention_hours("translate_lab")
    except Exception:
        log.warning("get_retention_hours failed for translate_lab", exc_info=True)
        retention_hours = 168
    return render_template(
        "translate_lab_list.html",
        projects=rows or [],
        now=datetime.now(),
        retention_hours=retention_hours,
    )


@bp.route("/translate-lab/<task_id>")
@login_required
def detail(task_id: str):
    row = translate_lab_store.get_user_project(
        task_id,
        current_user.id,
        query_one_func=db_query_one,
    )
    if not row or row.get("type") != "translate_lab":
        abort(404)

    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            state = {}

    return render_template(
        "translate_lab_detail.html",
        project=row,
        state=state,
    )


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/translate-lab", methods=["POST"])
@login_required
def upload_and_create():
    """已 deprecated（Phase 6, 2026-05-07）。

    本入口于 omni 合并任务里被替换：新建任务请到 ``/omni-translate/``
    选 ``lab-current`` preset（或自定义勾选「镜头分镜」+「按镜头字符上限」）。

    返回 410 Gone。详情页 / 老任务读取仍可访问，runtime 代码 / DB 表
    全部保留作为防御。
    """
    return translate_lab_flask_response(
        build_translate_lab_error_response(
            "本模块已 deprecated。请到 /omni-translate/ 用 lab-current preset 创建任务。",
            410,
        )
    )

    # ── 以下为旧创建逻辑，保留作为参考；410 已 short-circuit 返回 ──
    if "video" not in request.files:
        return translate_lab_flask_response(
            build_translate_lab_error_response("缺少视频文件", 400)
        )
    file = request.files["video"]
    if not file.filename:
        return translate_lab_flask_response(
            build_translate_lab_error_response("文件名为空", 400)
        )
    original_filename = client_filename_basename(file.filename)
    if not validate_video_extension(original_filename):
        return translate_lab_flask_response(
            build_translate_lab_error_response("不支持的视频格式", 400)
        )

    source_language = (request.form.get("source_language") or "zh").strip()
    target_language = (request.form.get("target_language") or "en").strip()
    voice_match_mode = (request.form.get("voice_match_mode") or "auto").strip()
    if source_language not in _ALLOWED_SOURCE_LANGUAGES:
        return translate_lab_flask_response(
            build_translate_lab_error_response("source_language 非法", 400)
        )
    if target_language not in _ALLOWED_TARGET_LANGUAGES:
        return translate_lab_flask_response(
            build_translate_lab_error_response("target_language 非法", 400)
        )
    if voice_match_mode not in _ALLOWED_VOICE_MODES:
        return translate_lab_flask_response(
            build_translate_lab_error_response("voice_match_mode 非法", 400)
        )

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    ext = os.path.splitext(original_filename)[1].lower()
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    save_uploaded_file_to_path(file, video_path)

    user_id = current_user.id
    store.create_translate_lab(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=user_id,
        source_language=source_language,
        target_language=target_language,
        voice_match_mode=voice_match_mode,
    )

    display_name = _resolve_name_conflict(
        user_id, _default_display_name(original_filename),
    )
    translate_lab_store.set_project_display_name(
        task_id,
        display_name,
        execute_func=db_execute,
    )
    task_state.update(task_id, display_name=display_name)

    # 生成缩略图（失败不影响任务创建）
    try:
        from pipeline.ffutil import extract_thumbnail as _extract_thumbnail
        thumb = _extract_thumbnail(video_path, task_dir)
        if thumb:
            translate_lab_store.set_project_thumbnail_path(
                task_id,
                thumb,
                execute_func=db_execute,
            )
    except Exception:
        log.warning("[translate_lab] 缩略图生成失败 task_id=%s",
                    task_id, exc_info=True)

    return translate_lab_flask_response(
        build_translate_lab_created_response(
            task_id=task_id,
            source_language=source_language,
            target_language=target_language,
            voice_match_mode=voice_match_mode,
        )
    )


@bp.route("/api/translate-lab/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: str):
    """软删除 translate_lab 任务。"""
    user_id = current_user.id
    row = translate_lab_store.get_active_user_project_id(
        task_id,
        user_id,
        query_one_func=db_query_one,
    )
    if not row:
        return translate_lab_flask_response(
            build_translate_lab_error_response("任务不存在", 404)
        )
    translate_lab_store.soft_delete_project(
        task_id,
        user_id,
        execute_func=db_execute,
    )
    try:
        task_state.update(task_id, status="deleted")
    except Exception:
        pass
    return translate_lab_flask_response(build_translate_lab_ok_response())


@bp.route("/api/translate-lab/<task_id>", methods=["GET"])
@login_required
def get_task(task_id: str):
    """读取 translate_lab 任务当前状态（用于详情页刷新兜底）。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("任务不存在", 404)
        )
    # 不把内部字段 _user_id 等暴露给前端
    payload = {k: v for k, v in task.items() if not k.startswith("_")}
    return translate_lab_flask_response(build_translate_lab_payload_response(payload))


@bp.route("/api/translate-lab/<task_id>/start", methods=["POST"])
@login_required
def start_task(task_id: str):
    """写入用户选项并后台启动 PipelineRunnerV2。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("not found", 404)
        )
    options = request.get_json(silent=True) or {}
    # Whitelist user-controllable fields — never let clients overwrite internal
    # task_state keys (ownership, paths, status, etc.) through a start call.
    _ALLOWED_START_FIELDS = {
        "source_language", "target_language",
        "voice_match_mode", "voice_gender",
        "interactive_review",
        "subtitle_position", "subtitle_font", "subtitle_size", "subtitle_position_y",
    }
    update_fields = {k: v for k, v in options.items() if k in _ALLOWED_START_FIELDS}
    update_fields["status"] = "running"
    task_state.update(task_id, **update_fields)
    translate_lab_runner.start(task_id=task_id, user_id=user_id)
    return translate_lab_flask_response(build_translate_lab_ok_response())


@bp.route("/api/translate-lab/<task_id>/resume", methods=["POST"])
@login_required
def resume_task(task_id: str):
    """从指定步骤恢复任务（前端传 ``start_step``）。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("not found", 404)
        )
    payload = request.get_json(silent=True) or {}
    start_step = payload.get("start_step", "extract")
    _VALID_STEPS = {"extract", "asr", "shot_decompose", "voice_match",
                    "translate", "tts", "subtitle", "compose", "export"}
    if start_step not in _VALID_STEPS:
        return translate_lab_flask_response(
            build_translate_lab_error_response(
                f"start_step must be one of {sorted(_VALID_STEPS)}",
                400,
            )
        )
    task_state.update(task_id, status="running")
    translate_lab_runner.resume(
        task_id=task_id, start_step=start_step, user_id=user_id,
    )
    return translate_lab_flask_response(
        build_translate_lab_ok_response(start_step=start_step)
    )


@bp.route("/api/translate-lab/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    """人工确认音色：写入 ``chosen_voice`` 让 runner 阻塞循环继续。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("not found", 404)
        )
    payload = request.get_json(silent=True) or {}
    voice_id = payload.get("voice_id")
    if not voice_id:
        return translate_lab_flask_response(
            build_translate_lab_error_response("voice_id required", 400)
        )
    pending = (task_state.get(task_id) or {}).get("pending_voice_choice") or []
    chosen = next(
        (v for v in pending if v.get("voice_id") == voice_id),
        None,
    )
    if chosen is None:
        chosen = {"voice_id": voice_id}
    task_state.update(task_id, chosen_voice=chosen, status="running")
    return translate_lab_flask_response(build_translate_lab_voice_confirmed_response(chosen))


@bp.route("/api/translate-lab/<task_id>/subtitle", methods=["GET"])
@login_required
def download_subtitle(task_id: str):
    """下载最终生成的 SRT 字幕文件。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("not found", 404)
        )
    srt_path = task.get("subtitle_path")
    if not srt_path or not os.path.isfile(srt_path):
        return translate_lab_flask_response(
            build_translate_lab_error_response("subtitle not ready", 404)
        )
    return safe_task_file_response(
        task,
        srt_path,
        not_found_message="subtitle not ready",
        mimetype="application/x-subrip",
        as_attachment=True,
        download_name=f"{task_id}.srt",
    )


@bp.route("/api/translate-lab/<task_id>/audio/<int:shot_index>",
          methods=["GET"])
@login_required
def stream_shot_audio(task_id: str, shot_index: int):
    """按分镜索引流式返回对应 TTS 音频。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("not found", 404)
        )
    tts_results = task.get("tts_results") or []
    target = next(
        (r for r in tts_results if r.get("shot_index") == shot_index),
        None,
    )
    if not target or not target.get("audio_path"):
        return translate_lab_flask_response(
            build_translate_lab_error_response("audio not ready", 404)
        )
    audio_path = target["audio_path"]
    if not os.path.isfile(audio_path):
        return translate_lab_flask_response(
            build_translate_lab_error_response("file missing", 404)
        )
    return safe_task_file_response(
        task,
        audio_path,
        not_found_message="file missing",
        mimetype="audio/mpeg",
    )


@bp.route("/api/translate-lab/<task_id>/final-video", methods=["GET"])
@login_required
def stream_final_video(task_id: str):
    """播放/下载合成完成的视频（优先硬字幕版）。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return translate_lab_flask_response(
            build_translate_lab_error_response("not found", 404)
        )
    compose_result = task.get("compose_result") or {}
    path = (
        compose_result.get("hard_video")
        or compose_result.get("soft_video")
        or task.get("final_video")
    )
    if not path or not os.path.isfile(path):
        return translate_lab_flask_response(
            build_translate_lab_error_response("video not ready", 404)
        )
    return safe_task_file_response(
        task,
        path,
        not_found_message="video not ready",
        mimetype="video/mp4",
    )


@bp.route("/api/translate-lab/voice-library/sync", methods=["POST"])
@login_required
@admin_required
def sync_voice_library():
    """管理员触发：拉取 ElevenLabs 全量共享音色，upsert 本地库。"""
    user_id = current_user.id
    api_key = resolve_key(user_id, "elevenlabs", "ELEVENLABS_API_KEY")
    if not api_key:
        return translate_lab_flask_response(
            build_translate_lab_error_response("elevenlabs api key not configured", 400)
        )
    total = sync_all_shared_voices(api_key)
    return translate_lab_flask_response(build_translate_lab_sync_response(total))


@bp.route("/api/translate-lab/voice-library/embed", methods=["POST"])
@login_required
@admin_required
def embed_voice_library():
    """管理员触发：为 preview_url 已有但 embedding 缺失的音色补算。"""
    payload = request.get_json(silent=True) or {}
    try:
        from config import OUTPUT_DIR as _OUTPUT_DIR
    except Exception:
        _OUTPUT_DIR = os.path.join(os.getcwd(), "output")
    cache_dir = payload.get("cache_dir") or os.path.join(
        _OUTPUT_DIR, "voice_embed_cache",
    )
    limit = payload.get("limit")
    count = embed_missing_voices(cache_dir=cache_dir, limit=limit)
    return translate_lab_flask_response(build_translate_lab_embed_response(count))
