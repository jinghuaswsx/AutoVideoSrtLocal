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

from flask import Blueprint, render_template, abort, request, jsonify, send_file
from flask_login import login_required, current_user

from web.auth import admin_required

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import task_state
from appcore.api_keys import resolve_key
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from appcore.settings import get_retention_hours
from pipeline.voice_library_sync import (
    embed_missing_voices,
    sync_all_shared_voices,
)
from web import store
from web.services import translate_lab_runner
from web.upload_util import validate_video_extension

log = logging.getLogger(__name__)

bp = Blueprint("translate_lab", __name__)

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
        row = db_query_one(
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s "
            "AND deleted_at IS NULL",
            (user_id, candidate),
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
    rows = db_query(
        """SELECT id, original_filename, display_name, thumbnail_path, status,
                  created_at, expires_at, deleted_at, state_json
           FROM projects
           WHERE user_id = %s AND type = 'translate_lab' AND deleted_at IS NULL
           ORDER BY created_at DESC""",
        (current_user.id,),
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
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
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
    """上传视频并创建 translate_lab 任务。

    表单字段：
    - ``video``（必填）视频文件。
    - ``source_language``（可选，默认 zh）:``zh|en``。
    - ``target_language``（可选，默认 en）:``en|de|fr|ja|es|pt|nl|sv|fi``。
    - ``voice_match_mode``（可选，默认 auto）:``auto|manual``。

    成功返回 ``{"task_id": "..."}``。
    """
    if "video" not in request.files:
        return jsonify({"error": "缺少视频文件"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "文件名为空"}), 400
    if not validate_video_extension(file.filename):
        return jsonify({"error": "不支持的视频格式"}), 400

    source_language = (request.form.get("source_language") or "zh").strip()
    target_language = (request.form.get("target_language") or "en").strip()
    voice_match_mode = (request.form.get("voice_match_mode") or "auto").strip()
    if source_language not in _ALLOWED_SOURCE_LANGUAGES:
        return jsonify({"error": "source_language 非法"}), 400
    if target_language not in _ALLOWED_TARGET_LANGUAGES:
        return jsonify({"error": "target_language 非法"}), 400
    if voice_match_mode not in _ALLOWED_VOICE_MODES:
        return jsonify({"error": "voice_match_mode 非法"}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower()
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    file.save(video_path)

    user_id = current_user.id
    original_filename = os.path.basename(file.filename)
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
    db_execute(
        "UPDATE projects SET display_name=%s WHERE id=%s",
        (display_name, task_id),
    )
    task_state.update(task_id, display_name=display_name)

    # 生成缩略图（失败不影响任务创建）
    try:
        from pipeline.ffutil import extract_thumbnail as _extract_thumbnail
        thumb = _extract_thumbnail(video_path, task_dir)
        if thumb:
            db_execute(
                "UPDATE projects SET thumbnail_path=%s WHERE id=%s",
                (thumb, task_id),
            )
    except Exception:
        log.warning("[translate_lab] 缩略图生成失败 task_id=%s",
                    task_id, exc_info=True)

    return jsonify({
        "task_id": task_id,
        "source_language": source_language,
        "target_language": target_language,
        "voice_match_mode": voice_match_mode,
    }), 201


@bp.route("/api/translate-lab/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: str):
    """软删除 translate_lab 任务。"""
    user_id = current_user.id
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s "
        "AND type='translate_lab' AND deleted_at IS NULL",
        (task_id, user_id),
    )
    if not row:
        return jsonify({"error": "任务不存在"}), 404
    db_execute(
        "UPDATE projects SET deleted_at=NOW() WHERE id=%s",
        (task_id,),
    )
    try:
        task_state.update(task_id, status="deleted")
    except Exception:
        pass
    return jsonify({"ok": True})


@bp.route("/api/translate-lab/<task_id>", methods=["GET"])
@login_required
def get_task(task_id: str):
    """读取 translate_lab 任务当前状态（用于详情页刷新兜底）。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    # 不把内部字段 _user_id 等暴露给前端
    payload = {k: v for k, v in task.items() if not k.startswith("_")}
    return jsonify(payload)


@bp.route("/api/translate-lab/<task_id>/start", methods=["POST"])
@login_required
def start_task(task_id: str):
    """写入用户选项并后台启动 PipelineRunnerV2。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "not found"}), 404
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
    return jsonify({"ok": True})


@bp.route("/api/translate-lab/<task_id>/resume", methods=["POST"])
@login_required
def resume_task(task_id: str):
    """从指定步骤恢复任务（前端传 ``start_step``）。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    start_step = payload.get("start_step", "extract")
    _VALID_STEPS = {"extract", "asr", "shot_decompose", "voice_match",
                    "translate", "tts", "subtitle", "compose", "export"}
    if start_step not in _VALID_STEPS:
        return jsonify({"error": f"start_step must be one of {sorted(_VALID_STEPS)}"}), 400
    task_state.update(task_id, status="running")
    translate_lab_runner.resume(
        task_id=task_id, start_step=start_step, user_id=user_id,
    )
    return jsonify({"ok": True, "start_step": start_step})


@bp.route("/api/translate-lab/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    """人工确认音色：写入 ``chosen_voice`` 让 runner 阻塞循环继续。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    voice_id = payload.get("voice_id")
    if not voice_id:
        return jsonify({"error": "voice_id required"}), 400
    pending = (task_state.get(task_id) or {}).get("pending_voice_choice") or []
    chosen = next(
        (v for v in pending if v.get("voice_id") == voice_id),
        None,
    )
    if chosen is None:
        chosen = {"voice_id": voice_id}
    task_state.update(task_id, chosen_voice=chosen, status="running")
    return jsonify({"ok": True, "chosen": chosen})


@bp.route("/api/translate-lab/<task_id>/subtitle", methods=["GET"])
@login_required
def download_subtitle(task_id: str):
    """下载最终生成的 SRT 字幕文件。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    srt_path = task.get("subtitle_path")
    if not srt_path or not os.path.isfile(srt_path):
        return jsonify({"error": "subtitle not ready"}), 404
    return send_file(
        srt_path,
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
        return jsonify({"error": "not found"}), 404
    tts_results = task.get("tts_results") or []
    target = next(
        (r for r in tts_results if r.get("shot_index") == shot_index),
        None,
    )
    if not target or not target.get("audio_path"):
        return jsonify({"error": "audio not ready"}), 404
    audio_path = target["audio_path"]
    if not os.path.isfile(audio_path):
        return jsonify({"error": "file missing"}), 404
    return send_file(audio_path, mimetype="audio/mpeg")


@bp.route("/api/translate-lab/<task_id>/final-video", methods=["GET"])
@login_required
def stream_final_video(task_id: str):
    """播放/下载合成完成的视频（优先硬字幕版）。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    compose_result = task.get("compose_result") or {}
    path = (
        compose_result.get("hard_video")
        or compose_result.get("soft_video")
        or task.get("final_video")
    )
    if not path or not os.path.isfile(path):
        return jsonify({"error": "video not ready"}), 404
    return send_file(path, mimetype="video/mp4")


@bp.route("/api/translate-lab/voice-library/sync", methods=["POST"])
@login_required
@admin_required
def sync_voice_library():
    """管理员触发：拉取 ElevenLabs 全量共享音色，upsert 本地库。"""
    user_id = current_user.id
    api_key = resolve_key(user_id, "elevenlabs", "ELEVENLABS_API_KEY")
    if not api_key:
        return jsonify({"error": "elevenlabs api key not configured"}), 400
    total = sync_all_shared_voices(api_key)
    return jsonify({"ok": True, "total": total})


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
    return jsonify({"ok": True, "count": count})
