"""视频评分模块 Flask 蓝图：上传视频或从已有项目选择 → Gemini 评估 → 展示报告。"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from appcore.project_state import update_project_state
from appcore.task_recovery import (
    recover_all_interrupted_tasks,
    recover_project_if_needed,
    try_register_active_task,
    unregister_active_task,
)
from appcore.settings import get_retention_hours
from config import UPLOAD_DIR, OUTPUT_DIR
from pipeline.video_review import get_review_prompts, save_review_prompts
from web.background import start_background_task
from web.extensions import socketio
from web.services.artifact_download import safe_task_file_response
from web.services.video_review import (
    build_video_review_already_running_response,
    build_video_review_delete_success_response,
    build_video_review_empty_prompts_response,
    build_video_review_file_missing_response,
    build_video_review_file_too_large_response,
    build_video_review_forbidden_prompts_response,
    build_video_review_missing_upload_response,
    build_video_review_not_found_response,
    build_video_review_prompts_response,
    build_video_review_prompts_saved_response,
    build_video_review_started_response,
    build_video_review_unsupported_upload_response,
    build_video_review_upload_success_response,
    video_review_flask_response,
)
from web.upload_util import save_uploaded_file_to_path

log = logging.getLogger(__name__)

bp = Blueprint("video_review", __name__)

# SocketIO 事件
EVT_VR_STEP = "vr_step_update"
EVT_VR_DONE = "vr_done"
EVT_VR_ERROR = "vr_error"

# 超过多少秒仍停留在 running 视为僵尸任务（服务重启 / worker 崩溃等）。
# 正常 Gemini Pro 视频评估 < 3 分钟，给 5 分钟冗余。
VR_STALE_SECONDS = 300


def _reset_if_stale(task_id: str, state: dict) -> dict:
    """若 review 已 running 超过 VR_STALE_SECONDS 仍无结果，自动置为 error。"""
    if state.get("steps", {}).get("review") != "running":
        return state
    started_at = state.get("review_started_at") or 0
    if started_at and (time.time() - started_at) < VR_STALE_SECONDS:
        return state
    state.setdefault("steps", {})["review"] = "error"
    state["error"] = "评估已超过 5 分钟未完成，可能因服务重启/Gemini 超时中断，请重试"
    state.pop("review_started_at", None)
    _update_state(task_id, {
        "steps.review": "error",
        "error": state["error"],
        "review_started_at": None,
    })
    db_execute("UPDATE projects SET status = 'error' WHERE id = %s", (task_id,))
    log.warning("[VR] 任务 %s 停留 running 过久，已自动标记为 error", task_id)
    return state


def _emit_to_task(task_id: str, event: str, payload: dict):
    socketio.emit(event, payload, room=task_id)


# ── 页面路由 ──

@bp.route("/video-review")
@login_required
def list_page():
    recover_all_interrupted_tasks()
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
    recover_project_if_needed(task_id, "video_review")
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'video_review' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    state = _reset_if_stale(task_id, state)
    return render_template("video_review_detail.html", project=row, state=state, task_id=task_id)


# ── API 路由 ──

@bp.route("/api/video-review/upload", methods=["POST"])
@login_required
def upload():
    """上传视频创建评估项目。"""
    file = request.files.get("video")
    if not file or not file.filename:
        return video_review_flask_response(build_video_review_missing_upload_response())

    from web.upload_util import validate_video_extension
    if not validate_video_extension(file.filename):
        return video_review_flask_response(build_video_review_unsupported_upload_response())

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    video_filename = file.filename
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}_{video_filename}")
    save_uploaded_file_to_path(file, video_path)

    display_name = os.path.splitext(video_filename)[0]

    state = {
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": video_filename,
        "display_name": display_name,
        "model": "gemini-3.1-pro-preview",
        "custom_prompt": "",
        "steps": {"review": "pending"},
        "result": None,
    }

    db_execute(
        "INSERT INTO projects "
        "(id, user_id, type, original_filename, display_name, "
        "status, task_dir, state_json, created_at, expires_at) "
        "VALUES (%s, %s, 'video_review', %s, %s, 'uploaded', %s, %s, "
        "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
        (task_id, current_user.id, video_filename, display_name,
         task_dir, json.dumps(state, ensure_ascii=False),
         get_retention_hours("video_review")),
    )

    return video_review_flask_response(build_video_review_upload_success_response(task_id))


@bp.route("/api/video-review/<task_id>/review", methods=["POST"])
@login_required
def start_review(task_id: str):
    """发起视频评估。"""
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'video_review' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return video_review_flask_response(build_video_review_not_found_response())

    state = json.loads(row.get("state_json") or "{}")

    body = request.get_json(silent=True) or {}
    model = body.get("model") or state.get("model") or "gemini-3.1-pro-preview"
    custom_prompt = (body.get("custom_prompt") or "").strip()
    prompt_lang = body.get("prompt_lang") or "en"

    video_path = state.get("video_path", "")
    if not video_path or not os.path.exists(video_path):
        return video_review_flask_response(build_video_review_file_missing_response())

    # 检查文件大小（OpenRouter 有限制，一般 20MB 以内）
    file_size = os.path.getsize(video_path)
    if file_size > 100 * 1024 * 1024:
        return video_review_flask_response(
            build_video_review_file_too_large_response(file_size / 1024 / 1024)
        )

    active_metadata = {
        "user_id": current_user.id,
        "runner": "web.routes.video_review._run_review_with_tracking",
        "entrypoint": "video_review.review",
        "stage": "queued_review",
        "details": {
            "model": model,
            "prompt_lang": prompt_lang,
        },
    }
    if not try_register_active_task("video_review", task_id, **active_metadata):
        return video_review_flask_response(build_video_review_already_running_response())

    _update_state(task_id, {
        "model": model,
        "custom_prompt": custom_prompt,
        "prompt_lang": prompt_lang,
        "steps.review": "running",
        "review_started_at": int(time.time()),
        "error": None,
    })
    db_execute("UPDATE projects SET status = 'running' WHERE id = %s", (task_id,))

    try:
        start_background_task(
            _run_review_with_tracking,
            task_id,
            video_path,
            model,
            custom_prompt,
            current_user.id,
            prompt_lang,
        )
    except BaseException:
        unregister_active_task("video_review", task_id)
        raise

    return video_review_flask_response(build_video_review_started_response())


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
            "review_started_at": None,
            "error": None,
        })
        db_execute("UPDATE projects SET status = 'done' WHERE id = %s", (task_id,))

        _emit_to_task(task_id, EVT_VR_DONE, {"result": result, "stats": stats})

    except Exception as e:
        log.exception("[VR] 视频评估失败: %s", task_id)
        _update_state(task_id, {
            "steps.review": "error",
            "review_started_at": None,
            "error": str(e),
        })
        db_execute("UPDATE projects SET status = 'error' WHERE id = %s", (task_id,))
        _emit_to_task(task_id, EVT_VR_ERROR, {"message": f"评估失败: {e}"})


def _run_review_with_tracking(task_id: str, video_path: str, model: str, custom_prompt: str,
                              user_id: int, prompt_lang: str = "en"):
    try:
        return _do_review(task_id, video_path, model, custom_prompt, user_id, prompt_lang)
    finally:
        unregister_active_task("video_review", task_id)


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
    return safe_task_file_response(state, path, not_found_message="Not Found")


@bp.route("/api/video-review/prompts", methods=["GET"])
@login_required
def get_prompts():
    """获取全局评分提示词（中英）。"""
    prompts = get_review_prompts()
    return video_review_flask_response(build_video_review_prompts_response(prompts))


@bp.route("/api/video-review/prompts", methods=["PUT"])
@login_required
def update_prompts():
    """保存全局评分提示词（仅管理员）。"""
    if not current_user.is_admin:
        return video_review_flask_response(build_video_review_forbidden_prompts_response())
    body = request.get_json(silent=True) or {}
    en = (body.get("en") or "").strip()
    zh = (body.get("zh") or "").strip()
    if not en and not zh:
        return video_review_flask_response(build_video_review_empty_prompts_response())
    # 如果只传了一个，另一个保持原值
    current = get_review_prompts()
    en = en or current["en"]
    zh = zh or current["zh"]
    save_review_prompts(en, zh)
    return video_review_flask_response(build_video_review_prompts_saved_response(en, zh))


@bp.route("/api/video-review/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s AND type = 'video_review'",
        (task_id, current_user.id),
    )
    return video_review_flask_response(build_video_review_delete_success_response())


# ── 工具函数 ──

def _update_state(task_id: str, updates: dict):
    """更新 state_json 中的字段，支持点号路径。"""
    update_project_state(
        task_id,
        updates,
        query_one_func=db_query_one,
        execute_func=db_execute,
    )
