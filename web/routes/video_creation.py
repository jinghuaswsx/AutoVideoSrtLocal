"""视频创作模块 Flask 蓝图：Seedance 2.0 — 文案+视频+图片+音频 → 生成新视频。"""
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
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from config import UPLOAD_DIR, OUTPUT_DIR
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


# Seedance 2.0 单图像素上限 36_000_000（3840×9375 左右）
_SEEDANCE_IMG_MAX_PIXELS = 36_000_000


def _shrink_image_if_oversize(path: str) -> str:
    """图像超过 Seedance 允许的最大像素数时，就地缩到上限内；返回最终路径。"""
    try:
        from PIL import Image
    except ImportError:
        return path
    try:
        with Image.open(path) as im:
            w, h = im.size
            total = w * h
            if total <= _SEEDANCE_IMG_MAX_PIXELS:
                return path
            ratio = (_SEEDANCE_IMG_MAX_PIXELS / total) ** 0.5
            new_w = max(1, int(w * ratio))
            new_h = max(1, int(h * ratio))
            im2 = im.convert("RGB") if im.mode in ("RGBA", "P", "LA") else im
            im2 = im2.resize((new_w, new_h), Image.LANCZOS)
            ext = os.path.splitext(path)[1].lower()
            save_kwargs = {"quality": 92} if ext in (".jpg", ".jpeg") else {}
            im2.save(path, **save_kwargs)
            log.info("[VC] 图像缩放: %s %dx%d -> %dx%d", path, w, h, new_w, new_h)
    except Exception as e:
        log.warning("[VC] 图像缩放失败，保持原图: %s (%s)", path, e)
    return path


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
    """接收 prompt + 可选视频/图片/音频，创建 Seedance 2.0 项目。"""
    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return jsonify(error="请输入文案"), 400
    if len(prompt) > 2000:
        return jsonify(error="文案不能超过 2000 字"), 400

    # 图片（0-9）
    images = request.files.getlist("images")
    images = [f for f in images if f and f.filename]
    if len(images) > 9:
        return jsonify(error="图片最多 9 张"), 400

    # 视频（0-1）
    video_file = request.files.get("video")
    if video_file and not video_file.filename:
        video_file = None

    # 音频（0-1）
    audio_file = request.files.get("audio")
    if audio_file and not audio_file.filename:
        audio_file = None

    # 配置
    ratio = request.form.get("ratio", "9:16")
    duration = int(request.form.get("duration", 5))
    generate_audio = request.form.get("generate_audio", "true").lower() not in ("false", "0", "off")
    watermark = request.form.get("watermark", "false").lower() in ("true", "1", "on")

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    from web.upload_util import secure_filename_component, validate_video_extension, validate_image_extension

    # 保存视频
    video_path = None
    video_filename = None
    if video_file:
        if not validate_video_extension(video_file.filename):
            return jsonify(error="不支持的视频格式"), 400
        video_filename = video_file.filename
        video_path = os.path.join(UPLOAD_DIR, f"{task_id}_video_{secure_filename_component(video_filename)}")
        video_file.save(video_path)

    # 保存图片
    image_paths = []
    for idx, img in enumerate(images):
        if not validate_image_extension(img.filename):
            return jsonify(error=f"图片 {img.filename} 格式不支持"), 400
        safe_name = secure_filename_component(img.filename)
        img_path = os.path.join(UPLOAD_DIR, f"{task_id}_img{idx}_{safe_name}")
        img.save(img_path)
        image_paths.append(img_path)

    # 保存音频
    audio_path = None
    if audio_file:
        safe_name = secure_filename_component(audio_file.filename)
        audio_path = os.path.join(UPLOAD_DIR, f"{task_id}_audio_{safe_name}")
        audio_file.save(audio_path)

    # 缩略图：优先视频首帧，其次第一张图片
    thumbnail_path = None
    if video_path and os.path.exists(video_path):
        thumbnail_path = _extract_thumbnail(video_path, task_dir)
    elif image_paths:
        thumbnail_path = image_paths[0]

    # display_name
    if video_filename:
        display_name = os.path.splitext(video_filename)[0]
    else:
        display_name = prompt[:30].replace("\n", " ").replace("\r", "")

    original_filename = video_filename or (images[0].filename if images else "")

    state = {
        "task_dir": task_dir,
        "display_name": display_name,
        "prompt": prompt,
        "video_path": video_path,
        "image_paths": image_paths,
        "audio_path": audio_path,
        "ratio": ratio,
        "duration": duration,
        "generate_audio": generate_audio,
        "watermark": watermark,
        "steps": {
            "upload": "done",
            "generate": "pending",
        },
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
        (task_id, current_user.id, original_filename, display_name,
         thumbnail_path, task_dir, json.dumps(state, ensure_ascii=False),
         get_retention_hours("video_creation")),
    )

    # 异步生成
    api_key = resolve_key(current_user.id, "seedance", "SEEDANCE_API_KEY")
    if not api_key:
        return jsonify(error="请先在 API 配置中设置 Seedance API Key"), 400

    eventlet.spawn(_do_generate_v2, task_id, api_key, state)

    return jsonify({"id": task_id}), 201


def _do_generate_v2(task_id: str, api_key: str, state: dict):
    """异步执行 Seedance 2.0 视频生成。"""
    from pipeline.seedance import generate_video_v2

    task_dir = state.get("task_dir", "")

    try:
        _update_state(task_id, {"steps.generate": "running"})
        db_execute("UPDATE projects SET status = 'running' WHERE id = %s", (task_id,))
        _emit_to_task(task_id, EVT_VC_STEP, {"step": "generate", "status": "running", "message": "上传素材到云存储..."})

        # 上传本地文件到 TOS 获取公网 URL
        video_url = None
        if state.get("video_path") and os.path.exists(state["video_path"]):
            video_url = tos_upload(state["video_path"], expires=86400)

        image_urls = []
        for img_path in (state.get("image_paths") or []):
            if os.path.exists(img_path):
                _shrink_image_if_oversize(img_path)
                image_urls.append(tos_upload(img_path, expires=86400))

        audio_url = None
        if state.get("audio_path") and os.path.exists(state["audio_path"]):
            audio_url = tos_upload(state["audio_path"], expires=86400)

        _emit_to_task(task_id, EVT_VC_STEP, {"step": "generate", "status": "running", "message": "已提交生成任务，等待结果..."})

        def on_progress(status, message):
            _emit_to_task(task_id, EVT_VC_STEP, {"step": "generate", "status": "running", "message": message})

        result = generate_video_v2(
            api_key=api_key,
            prompt=state["prompt"],
            video_url=video_url,
            image_urls=image_urls or None,
            audio_url=audio_url,
            ratio=state.get("ratio", "9:16"),
            duration=state.get("duration", 5),
            generate_audio=state.get("generate_audio", True),
            watermark=state.get("watermark", False),
            on_progress=on_progress,
        )

        video_result_url = result.get("video_url", "")
        seedance_task_id = result.get("task_id", "")

        # 下载生成的视频到本地
        local_video_path = None
        if video_result_url:
            import requests as req
            local_video_path = os.path.join(task_dir, "generated_video.mp4")
            resp = req.get(video_result_url, timeout=120)
            resp.raise_for_status()
            with open(local_video_path, "wb") as f:
                f.write(resp.content)

        _update_state(task_id, {
            "seedance_task_id": seedance_task_id,
            "result_video_url": video_result_url,
            "result_video_path": local_video_path,
            "steps.generate": "done",
        })
        db_execute("UPDATE projects SET status = 'done' WHERE id = %s", (task_id,))

        _emit_to_task(task_id, EVT_VC_DONE, {
            "video_url": video_result_url,
            "local_path": os.path.basename(local_video_path) if local_video_path else None,
        })

    except Exception as e:
        log.exception("[VC] 视频生成失败: %s", task_id)
        _update_state(task_id, {"steps.generate": "error"})
        db_execute("UPDATE projects SET status = 'error' WHERE id = %s", (task_id,))
        _emit_to_task(task_id, EVT_VC_ERROR, {"message": f"视频生成失败: {e}"})


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


@bp.route("/api/video-creation/<task_id>/asset/<kind>/<int:idx>")
@login_required
def get_asset(task_id: str, kind: str, idx: int):
    """获取任务素材文件（kind = video / image / audio，idx 对 image 为 0-8，其余为 0）。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation'",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")

    if kind == "video":
        if idx != 0:
            return "Not Found", 404
        path = state.get("video_path")
    elif kind == "image":
        paths = state.get("image_paths", [])
        if idx >= len(paths):
            return "Not Found", 404
        path = paths[idx]
    elif kind == "audio":
        if idx != 0:
            return "Not Found", 404
        path = state.get("audio_path")
    else:
        return "Not Found", 404

    if not path or not os.path.exists(path):
        return "Not Found", 404
    return send_file(path)


@bp.route("/api/video-creation/<task_id>/asset/<kind>/<int:idx>", methods=["DELETE"])
@login_required
def delete_asset(task_id: str, kind: str, idx: int):
    """删除某项素材。kind = video / image / audio。仅当 steps.generate != running 时允许。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404
    state = json.loads(row.get("state_json") or "{}")
    if state.get("steps", {}).get("generate") == "running":
        return jsonify(error="生成进行中，无法删除素材"), 400

    if kind == "video":
        if idx != 0:
            return jsonify(error="not found"), 404
        path = state.get("video_path")
        if not path:
            return jsonify(error="not found"), 404
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        state["video_path"] = None
    elif kind == "image":
        paths = state.get("image_paths", [])
        if idx >= len(paths):
            return jsonify(error="not found"), 404
        path = paths[idx]
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        paths.pop(idx)
        state["image_paths"] = paths
    elif kind == "audio":
        if idx != 0:
            return jsonify(error="not found"), 404
        path = state.get("audio_path")
        if not path:
            return jsonify(error="not found"), 404
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        state["audio_path"] = None
    else:
        return jsonify(error="unknown kind"), 400

    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
    return jsonify({"status": "ok"})


@bp.route("/api/video-creation/<task_id>/asset/<kind>", methods=["POST"])
@login_required
def add_asset(task_id: str, kind: str):
    """追加素材。kind = video / image / audio。multipart: files['file']。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404
    state = json.loads(row.get("state_json") or "{}")
    if state.get("steps", {}).get("generate") == "running":
        return jsonify(error="生成进行中，无法添加素材"), 400

    upload_file = request.files.get("file")
    if not upload_file or not upload_file.filename:
        return jsonify(error="请上传文件"), 400

    from web.upload_util import secure_filename_component, validate_video_extension, validate_image_extension

    if kind == "video":
        if state.get("video_path"):
            return jsonify(error="已存在视频，请先删除"), 400
        if not validate_video_extension(upload_file.filename):
            return jsonify(error="不支持的视频格式"), 400
        safe_name = secure_filename_component(upload_file.filename)
        path = os.path.join(UPLOAD_DIR, f"{task_id}_video_{safe_name}")
        upload_file.save(path)
        state["video_path"] = path
    elif kind == "image":
        image_paths = state.get("image_paths", [])
        if len(image_paths) >= 9:
            return jsonify(error="图片最多 9 张"), 400
        if not validate_image_extension(upload_file.filename):
            return jsonify(error="不支持的图片格式"), 400
        safe_name = secure_filename_component(upload_file.filename)
        idx = len(image_paths)
        path = os.path.join(UPLOAD_DIR, f"{task_id}_img{idx}_{safe_name}")
        upload_file.save(path)
        _shrink_image_if_oversize(path)
        image_paths.append(path)
        state["image_paths"] = image_paths
    elif kind == "audio":
        if state.get("audio_path"):
            return jsonify(error="已存在音频，请先删除"), 400
        safe_name = secure_filename_component(upload_file.filename)
        path = os.path.join(UPLOAD_DIR, f"{task_id}_audio_{safe_name}")
        upload_file.save(path)
        state["audio_path"] = path
    else:
        return jsonify(error="unknown kind"), 400

    db_execute(
        "UPDATE projects SET state_json = %s WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )
    return jsonify({"status": "ok"})


@bp.route("/api/video-creation/<task_id>/regenerate", methods=["POST"])
@login_required
def regenerate(task_id: str):
    """重新触发 Seedance 生成。仅当状态不是 running 时允许。"""
    row = db_query_one(
        "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404
    state = json.loads(row.get("state_json") or "{}")
    if state.get("steps", {}).get("generate") == "running":
        return jsonify(error="生成进行中"), 400

    api_key = resolve_key(current_user.id, "seedance", "SEEDANCE_API_KEY")
    if not api_key:
        return jsonify(error="请先在 API 配置中设置 Seedance API Key"), 400

    # 重置状态
    state.setdefault("steps", {})["generate"] = "pending"
    state["result_video_url"] = None
    state["result_video_path"] = None
    state["seedance_task_id"] = None
    db_execute(
        "UPDATE projects SET state_json = %s, status = 'uploaded' WHERE id = %s",
        (json.dumps(state, ensure_ascii=False), task_id),
    )

    eventlet.spawn(_do_generate_v2, task_id, api_key, state)
    return jsonify({"status": "ok"})


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
    """更新 state_json 中的字段，支持点号路径（如 steps.generate）。"""
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
