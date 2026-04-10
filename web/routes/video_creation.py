"""视频创作模块 Flask 蓝图：上传参考视频 → Seedance 生成新视频。"""
from __future__ import annotations

import json
import logging
import os
import uuid

import eventlet
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user

from appcore.api_keys import resolve_key
from appcore.settings import get_retention_hours
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute, get_conn
from appcore.events import EventBus, Event
from config import UPLOAD_DIR, OUTPUT_DIR
from pipeline.keyframe import extract_keyframes
from pipeline.storage import upload_file as tos_upload
from web.extensions import socketio

log = logging.getLogger(__name__)

bp = Blueprint("video_creation", __name__)

# ── SocketIO 事件 ──
EVT_VC_STEP = "vc_step_update"
EVT_VC_DONE = "vc_done"
EVT_VC_ERROR = "vc_error"


def _emit_to_task(task_id: str, event: str, payload: dict):
    socketio.emit(event, payload, room=task_id)


from pipeline.ffutil import extract_thumbnail


def _extract_thumbnail(video_path: str, output_dir: str) -> str | None:
    return extract_thumbnail(video_path, output_dir, scale="360:-2")


# ── 页面路由 ──

@bp.route("/video-creation")
@login_required
def list_page():
    rows = db_query(
        "SELECT id, display_name, original_filename, thumbnail_path, status, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = 'video_creation' AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (current_user.id,),
    )
    return render_template("video_creation_list.html", projects=rows)


@bp.route("/video-creation/<task_id>")
@login_required
def detail_page(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    return render_template("video_creation_detail.html", project=row, state=state, task_id=task_id)


# ── API 路由 ──

@bp.route("/api/video-creation/upload", methods=["POST"])
@login_required
def upload():
    """上传参考视频（必填）+ 可选参考图片/产品信息，创建项目。"""
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="请上传参考视频"), 400

    from web.upload_util import validate_video_extension
    if not validate_video_extension(file.filename):
        return jsonify(error="不支持的视频格式"), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    # 保存视频
    video_filename = file.filename
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}_{video_filename}")
    file.save(video_path)

    # 缩略图
    thumbnail_path = _extract_thumbnail(video_path, task_dir)

    # 可选：参考图片
    ref_image_path = None
    ref_image = request.files.get("ref_image")
    if ref_image and ref_image.filename:
        from web.upload_util import secure_filename_component
        safe_name = secure_filename_component(ref_image.filename)
        ref_image_path = os.path.join(task_dir, f"ref_{safe_name}")
        ref_image.save(ref_image_path)

    display_name = os.path.splitext(video_filename)[0]

    # 构建初始状态
    state = {
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": video_filename,
        "display_name": display_name,
        "ref_image_path": ref_image_path,
        "product_info": request.form.get("product_info", ""),
        "keyframes": [],
        "steps": {
            "keyframe": "pending",
            "generate": "pending",
        },
        "prompt": "",
        "duration": 5,
        "seedance_task_id": None,
        "result_video_url": None,
        "result_video_path": None,
    }

    db_execute(
        "INSERT INTO projects "
        "(id, user_id, type, original_filename, display_name, thumbnail_path, "
        "status, task_dir, state_json, created_at, expires_at) "
        "VALUES (%s, %s, 'video_creation', %s, %s, %s, 'uploaded', %s, %s, "
        "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
        (task_id, current_user.id, video_filename, display_name,
         thumbnail_path, task_dir, json.dumps(state, ensure_ascii=False),
         get_retention_hours("video_creation")),
    )

    # 异步抽帧
    eventlet.spawn(_do_keyframe, task_id, video_path, task_dir)

    return jsonify({"id": task_id}), 201


def _do_keyframe(task_id: str, video_path: str, task_dir: str):
    """异步抽取关键帧。"""
    try:
        _update_state(task_id, {"steps.keyframe": "running"})
        _emit_to_task(task_id, EVT_VC_STEP, {"step": "keyframe", "status": "running"})

        frames_dir = os.path.join(task_dir, "keyframes")
        keyframes = extract_keyframes(video_path, frames_dir, max_frames=6)

        _update_state(task_id, {"keyframes": keyframes, "steps.keyframe": "done"})
        _emit_to_task(task_id, EVT_VC_STEP, {
            "step": "keyframe", "status": "done",
            "keyframes": [os.path.basename(f) for f in keyframes],
        })
    except Exception as e:
        log.exception("[VC] 抽帧失败: %s", task_id)
        _update_state(task_id, {"steps.keyframe": "error"})
        _emit_to_task(task_id, EVT_VC_ERROR, {"message": f"抽帧失败: {e}"})


@bp.route("/api/video-creation/<task_id>/generate", methods=["POST"])
@login_required
def generate(task_id: str):
    """发起 Seedance 视频生成。"""
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404

    state = json.loads(row.get("state_json") or "{}")

    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    duration = int(body.get("duration", 5))
    keyframe_index = body.get("keyframe_index", 0)  # 用哪一帧作为参考图

    if not prompt:
        return jsonify(error="请输入提示词"), 400

    # 获取 API Key
    api_key = resolve_key(current_user.id, "seedance", "SEEDANCE_API_KEY")
    if not api_key:
        return jsonify(error="请先在 API 配置中设置 Seedance API Key"), 400

    # 准备参考图片 URL
    image_url = None
    # 优先用用户上传的参考图
    if state.get("ref_image_path") and os.path.exists(state["ref_image_path"]):
        image_url = tos_upload(state["ref_image_path"], expires=3600)
    # 否则用关键帧
    elif state.get("keyframes") and keyframe_index < len(state["keyframes"]):
        kf_path = state["keyframes"][keyframe_index]
        if os.path.exists(kf_path):
            image_url = tos_upload(kf_path, expires=3600)

    # 更新状态
    _update_state(task_id, {
        "prompt": prompt,
        "duration": duration,
        "steps.generate": "running",
    })
    db_execute("UPDATE projects SET status = 'running' WHERE id = %s", (task_id,))

    # 异步生成
    eventlet.spawn(_do_generate, task_id, api_key, prompt, image_url, duration, state.get("task_dir", ""))

    return jsonify({"status": "started"})


def _do_generate(task_id: str, api_key: str, prompt: str, image_url: str | None,
                 duration: int, task_dir: str):
    """异步执行 Seedance 视频生成。"""
    from pipeline.seedance import generate_video

    try:
        _emit_to_task(task_id, EVT_VC_STEP, {"step": "generate", "status": "running", "message": "已提交生成任务..."})

        def on_progress(status, message):
            _emit_to_task(task_id, EVT_VC_STEP, {"step": "generate", "status": "running", "message": message})

        result = generate_video(
            api_key=api_key,
            prompt=prompt,
            image_url=image_url,
            duration=duration,
            on_progress=on_progress,
        )

        video_url = result.get("video_url", "")
        seedance_task_id = result.get("task_id", "")

        # 下载视频到本地
        local_video_path = None
        if video_url:
            import requests as req
            local_video_path = os.path.join(task_dir, "generated_video.mp4")
            resp = req.get(video_url, timeout=120)
            resp.raise_for_status()
            with open(local_video_path, "wb") as f:
                f.write(resp.content)

        _update_state(task_id, {
            "seedance_task_id": seedance_task_id,
            "result_video_url": video_url,
            "result_video_path": local_video_path,
            "steps.generate": "done",
        })
        db_execute("UPDATE projects SET status = 'done' WHERE id = %s", (task_id,))

        _emit_to_task(task_id, EVT_VC_DONE, {
            "video_url": video_url,
            "local_path": os.path.basename(local_video_path) if local_video_path else None,
        })

    except Exception as e:
        log.exception("[VC] 视频生成失败: %s", task_id)
        _update_state(task_id, {"steps.generate": "error"})
        db_execute("UPDATE projects SET status = 'error' WHERE id = %s", (task_id,))
        _emit_to_task(task_id, EVT_VC_ERROR, {"message": f"视频生成失败: {e}"})


@bp.route("/api/video-creation/<task_id>/keyframe/<int:index>")
@login_required
def get_keyframe(task_id: str, index: int):
    """获取关键帧图片。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation'",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    keyframes = state.get("keyframes", [])
    if index >= len(keyframes):
        return "Not Found", 404
    return send_file(keyframes[index])


@bp.route("/api/video-creation/<task_id>/result-video")
@login_required
def get_result_video(task_id: str):
    """下载生成的视频。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation'",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    path = state.get("result_video_path")
    if not path or not os.path.exists(path):
        return "Not Found", 404
    return send_file(path, as_attachment=True, download_name="generated_video.mp4")


@bp.route("/api/video-creation/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
    row = db_query_one(
        "SELECT task_dir, state_json FROM projects "
        "WHERE id = %s AND user_id = %s AND type = 'video_creation' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404
    from appcore import cleanup
    cleanup.delete_task_storage(row)
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    return jsonify({"status": "ok"})


# ── 工具函数 ──

def _update_state(task_id: str, updates: dict):
    """更新 state_json 中的字段，支持点号路径（如 steps.keyframe）。"""
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
