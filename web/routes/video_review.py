"""视频评分模块 Flask 蓝图：上传视频或从已有项目选择 → Gemini 评估 → 展示报告。"""
from __future__ import annotations

import json
import logging
import os
import uuid

import eventlet
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user

from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from config import UPLOAD_DIR, OUTPUT_DIR
from pipeline.video_review import get_review_prompts, save_review_prompts
from web.extensions import socketio

log = logging.getLogger(__name__)

bp = Blueprint("video_review", __name__)

# SocketIO 事件
EVT_VR_STEP = "vr_step_update"
EVT_VR_DONE = "vr_done"
EVT_VR_ERROR = "vr_error"


def _emit_to_task(task_id: str, event: str, payload: dict):
    socketio.emit(event, payload, room=task_id)


# ── 页面路由 ──

@bp.route("/video-review")
@login_required
def list_page():
    rows = db_query(
        "SELECT id, display_name, original_filename, thumbnail_path, status, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = 'video_review' AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (current_user.id,),
    )
    return render_template("video_review_list.html", projects=rows)


@bp.route("/video-review/<task_id>")
@login_required
def detail_page(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'video_review' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    return render_template("video_review_detail.html", project=row, state=state, task_id=task_id)


# ── API 路由 ──

@bp.route("/api/video-review/upload", methods=["POST"])
@login_required
def upload():
    """上传视频创建评估项目。"""
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="请上传视频"), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_filename = file.filename
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}_{video_filename}")
    file.save(video_path)

    display_name = os.path.splitext(video_filename)[0]

    state = {
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": video_filename,
        "display_name": display_name,
        "model": "google/gemini-2.5-flash",
        "custom_prompt": "",
        "steps": {"review": "pending"},
        "result": None,
    }

    db_execute(
        "INSERT INTO projects "
        "(id, user_id, type, original_filename, display_name, "
        "status, task_dir, state_json, created_at, expires_at) "
        "VALUES (%s, %s, 'video_review', %s, %s, 'uploaded', %s, %s, "
        "NOW(), DATE_ADD(NOW(), INTERVAL 48 HOUR))",
        (task_id, current_user.id, video_filename, display_name,
         task_dir, json.dumps(state, ensure_ascii=False)),
    )

    return jsonify({"id": task_id}), 201


@bp.route("/api/video-review/<task_id>/review", methods=["POST"])
@login_required
def start_review(task_id: str):
    """发起视频评估。"""
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'video_review' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404

    state = json.loads(row.get("state_json") or "{}")

    body = request.get_json(silent=True) or {}
    model = body.get("model") or state.get("model") or "google/gemini-2.5-flash"
    custom_prompt = (body.get("custom_prompt") or "").strip()
    prompt_lang = body.get("prompt_lang") or "en"

    video_path = state.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return jsonify(error="视频文件不存在"), 400

    # 检查文件大小（OpenRouter 有限制，一般 20MB 以内）
    file_size = os.path.getsize(video_path)
    if file_size > 20 * 1024 * 1024:
        return jsonify(error=f"视频文件过大（{file_size / 1024 / 1024:.1f}MB），请压缩到 20MB 以内"), 400

    _update_state(task_id, {
        "model": model,
        "custom_prompt": custom_prompt,
        "prompt_lang": prompt_lang,
        "steps.review": "running",
    })
    db_execute("UPDATE projects SET status = 'running' WHERE id = %s", (task_id,))

    eventlet.spawn(_do_review, task_id, video_path, model, custom_prompt, current_user.id, prompt_lang)

    return jsonify({"status": "started"})


def _do_review(task_id: str, video_path: str, model: str, custom_prompt: str,
               user_id: int, prompt_lang: str = "en"):
    """异步执行视频评估。"""
    import time as _time
    from pipeline.video_review import review_video

    try:
        _emit_to_task(task_id, EVT_VR_STEP, {"step": "review", "status": "running", "message": "正在分析视频..."})

        start_ts = _time.time()
        result = review_video(
            video_path,
            user_id=user_id,
            model=model,
            custom_prompt=custom_prompt or None,
            prompt_lang=prompt_lang,
        )
        elapsed = round(_time.time() - start_ts, 1)

        # 清除内部字段
        raw = result.pop("_raw", "")
        result.pop("_model", None)
        usage = result.pop("_usage", None)

        # 统计信息
        stats = {
            "completed_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": elapsed,
            "model": model,
        }
        if usage:
            stats["prompt_tokens"] = usage.get("prompt_tokens", 0)
            stats["completion_tokens"] = usage.get("completion_tokens", 0)
            stats["total_tokens"] = usage.get("total_tokens", 0)

        _update_state(task_id, {
            "result": result,
            "stats": stats,
            "raw_response": raw,
            "steps.review": "done",
        })
        db_execute("UPDATE projects SET status = 'done' WHERE id = %s", (task_id,))

        _emit_to_task(task_id, EVT_VR_DONE, {"result": result, "stats": stats})

    except Exception as e:
        log.exception("[VR] 视频评估失败: %s", task_id)
        _update_state(task_id, {"steps.review": "error"})
        db_execute("UPDATE projects SET status = 'error' WHERE id = %s", (task_id,))
        _emit_to_task(task_id, EVT_VR_ERROR, {"message": f"评估失败: {e}"})


@bp.route("/api/video-review/<task_id>/video")
@login_required
def get_video(task_id: str):
    """获取评估的视频文件。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_review'",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    path = state.get("video_path")
    if not path or not os.path.exists(path):
        return "Not Found", 404
    return send_file(path)


@bp.route("/api/video-review/prompts", methods=["GET"])
@login_required
def get_prompts():
    """获取全局评分提示词（中英）。"""
    prompts = get_review_prompts()
    return jsonify(prompts)


@bp.route("/api/video-review/prompts", methods=["PUT"])
@login_required
def update_prompts():
    """保存全局评分提示词（仅管理员）。"""
    if current_user.role != "admin":
        return jsonify(error="仅管理员可修改提示词"), 403
    body = request.get_json(silent=True) or {}
    en = (body.get("en") or "").strip()
    zh = (body.get("zh") or "").strip()
    if not en and not zh:
        return jsonify(error="提示词不能为空"), 400
    # 如果只传了一个，另一个保持原值
    current = get_review_prompts()
    en = en or current["en"]
    zh = zh or current["zh"]
    save_review_prompts(en, zh)
    return jsonify({"status": "ok", "en": en, "zh": zh})


@bp.route("/api/video-review/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s AND type = 'video_review'",
        (task_id, current_user.id),
    )
    return jsonify({"status": "ok"})


# ── 工具函数 ──

def _update_state(task_id: str, updates: dict):
    """更新 state_json 中的字段，支持点号路径。"""
    row = db_query_one("SELECT state_json FROM projects WHERE id = %s", (task_id,))
    if not row:
        return
    state = json.loads(row.get("state_json") or "{}")
    for key, val in updates.items():
        parts = key.split(".")
        target = state
        for p in parts[:-1]:
            target = target.setdefault(p, {})
        target[parts[-1]] = val
    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
