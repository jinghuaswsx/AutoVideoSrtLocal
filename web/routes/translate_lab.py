"""视频翻译（测试）模块蓝图。

包含：
- 列表页与详情页（Task 2 已实现）
- 启动 / 恢复 API、音色确认 API（Task 13）
- 管理员触发共享音色库全量同步、embedding 回填 API（Task 13）

模块内部字段与流水线均遵循 ``appcore.task_state.create_translate_lab``
的 7 步骨架。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from flask import Blueprint, render_template, abort, request, jsonify
from flask_login import login_required, current_user

from appcore import task_state
from appcore.api_keys import resolve_key
from appcore.db import query as db_query, query_one as db_query_one
from appcore.settings import get_retention_hours
from pipeline.voice_library_sync import (
    embed_missing_voices,
    sync_all_shared_voices,
)
from web.services import translate_lab_runner

log = logging.getLogger(__name__)

bp = Blueprint("translate_lab", __name__)


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
                  created_at, expires_at, deleted_at
           FROM projects
           WHERE user_id = %s AND type = 'translate_lab' AND deleted_at IS NULL
           ORDER BY created_at DESC""",
        (current_user.id,),
    )
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

@bp.route("/api/translate-lab/<task_id>/start", methods=["POST"])
@login_required
def start_task(task_id: str):
    """写入用户选项并后台启动 PipelineRunnerV2。"""
    user_id = current_user.id
    task = _get_lab_task(task_id, user_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    options = request.get_json(silent=True) or {}
    update_fields = dict(options)
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


@bp.route("/api/translate-lab/voice-library/sync", methods=["POST"])
@login_required
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
