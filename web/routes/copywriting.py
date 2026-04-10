"""web/routes/copywriting.py
文案创作模块 Flask 蓝图：页面路由 + API。
"""

from __future__ import annotations

import json
import os
import uuid

import eventlet
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user

from appcore import task_state
from appcore.settings import get_retention_hours
from appcore.copywriting_runtime import CopywritingRunner
from appcore.events import EventBus
from appcore.db import get_conn as get_connection
from config import UPLOAD_DIR, OUTPUT_DIR

bp = Blueprint("copywriting", __name__)


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/copywriting")
@login_required
def list_page():
    """文案项目列表页。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, display_name, original_filename, thumbnail_path, "
                "status, created_at, expires_at "
                "FROM projects "
                "WHERE user_id = %s AND type = 'copywriting' AND deleted_at IS NULL "
                "ORDER BY created_at DESC",
                (current_user.id,),
            )
            projects = cur.fetchall()
    finally:
        conn.close()
    from datetime import datetime
    return render_template("copywriting_list.html", projects=projects, now=datetime.now(),
                           retention_hours=get_retention_hours("copywriting"))


@bp.route("/copywriting/<task_id>")
@login_required
def detail_page(task_id: str):
    """文案创作工作页。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return "Not found", 404

    # 加载商品信息
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            inputs = cur.fetchone() or {}
    finally:
        conn.close()

    from pipeline.copywriting import DEFAULT_SYSTEM_PROMPT_EN, DEFAULT_SYSTEM_PROMPT_ZH
    from pipeline.translate import resolve_provider_config

    # 构建模型列表: (value, label, provider, model_id)
    # value 格式: provider:model_id (provider 和 model 用冒号分隔)
    models = []
    try:
        _, or_model = resolve_provider_config("openrouter", user_id=current_user.id)
        models.append((f"openrouter:{or_model}", f"OpenRouter ({or_model})"))
    except Exception:
        models.append(("openrouter:", "OpenRouter (Claude)"))
    # Gemini 通过 OpenRouter
    models.append(("openrouter:google/gemini-3-flash-preview", "Gemini 3 Flash Preview (支持视频)"))
    models.append(("openrouter:google/gemini-2.5-flash", "Gemini 2.5 Flash (支持视频)"))
    try:
        _, db_model = resolve_provider_config("doubao", user_id=current_user.id)
        models.append((f"doubao:{db_model}", f"豆包 ({db_model})"))
    except Exception:
        models.append(("doubao:", "豆包 (Doubao)"))

    # 当前默认选中（第一个选项）
    current_provider = models[0][0] if models else "openrouter:"

    # Jinja2 中 task.copy 会调用 dict.copy() 方法而不是访问 key "copy"，
    # 所以需要把关键数据单独传给模板避免冲突
    copy_data = task.get("copy") or {}
    copy_segments = copy_data.get("segments") or []
    copy_debug = copy_data.get("_debug")

    return render_template("copywriting_detail.html",
                           task=task, inputs=inputs, task_id=task_id,
                           copy_data=copy_data, copy_segments=copy_segments,
                           copy_debug=copy_debug,
                           default_prompt_en=DEFAULT_SYSTEM_PROMPT_EN,
                           default_prompt_zh=DEFAULT_SYSTEM_PROMPT_ZH,
                           models=models, current_provider=current_provider)


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/copywriting/upload", methods=["POST"])
@login_required
def upload():
    """上传视频 + 商品信息，创建文案项目并启动抽帧。"""
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="请上传视频文件"), 400

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

    # 生成缩略图
    thumbnail_path = _extract_thumbnail(video_path, task_dir)

    # 创建任务状态
    task = task_state.create_copywriting(
        task_id=task_id,
        video_path=video_path,
        task_dir=task_dir,
        original_filename=video_filename,
        user_id=current_user.id,
    )

    # 解析显示名
    display_name = os.path.splitext(video_filename)[0]
    task_state.update(task_id, display_name=display_name)

    # 写入数据库
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO projects "
                "(id, user_id, type, original_filename, display_name, "
                "thumbnail_path, status, task_dir, state_json, "
                "created_at, expires_at) "
                "VALUES (%s, %s, 'copywriting', %s, %s, %s, 'uploaded', %s, %s, "
                "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
                (task_id, current_user.id, video_filename, display_name,
                 thumbnail_path, task_dir, json.dumps(task, ensure_ascii=False),
                 get_retention_hours("copywriting")),
            )

            # 保存商品信息
            selling_points = request.form.get("selling_points", "")
            cur.execute(
                "INSERT INTO copywriting_inputs "
                "(project_id, product_title, price, selling_points, "
                "target_audience, extra_info, language) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (task_id,
                 request.form.get("product_title", ""),
                 request.form.get("price", ""),
                 selling_points,
                 request.form.get("target_audience", ""),
                 request.form.get("extra_info", ""),
                 request.form.get("language", "en")),
            )
        conn.commit()
    finally:
        conn.close()

    # 处理商品主图上传
    product_image = request.files.get("product_image")
    if product_image and product_image.filename:
        from web.upload_util import validate_image_extension
        if not validate_image_extension(product_image.filename):
            return jsonify(error="不支持的图片格式"), 400
        img_path = os.path.join(task_dir, "product_image.jpg")
        product_image.save(img_path)
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE copywriting_inputs SET product_image_url = %s "
                    "WHERE project_id = %s",
                    (img_path, task_id),
                )
            conn.commit()
        finally:
            conn.close()

    # 后台启动管线（keyframe → copywrite）
    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    runner = CopywritingRunner(bus, user_id=current_user.id)
    eventlet.spawn(runner.start, task_id)

    return jsonify(task_id=task_id), 201


@bp.route("/api/copywriting/<task_id>/inputs", methods=["PUT"])
@login_required
def update_inputs(task_id: str):
    """更新商品信息。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    data = request.get_json()
    if not data:
        return jsonify(error="缺少数据"), 400

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            fields = []
            values = []
            for key in ("product_title", "price", "selling_points",
                        "target_audience", "extra_info", "language"):
                if key in data:
                    fields.append(f"{key} = %s")
                    values.append(data[key])
            if fields:
                values.append(task_id)
                cur.execute(
                    f"UPDATE copywriting_inputs SET {', '.join(fields)} "
                    "WHERE project_id = %s",
                    values,
                )
            conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/preview", methods=["POST"])
@login_required
def preview(task_id: str):
    """预览将要提交给 LLM 的完整请求结构。"""
    from pipeline.copywriting import preview_request

    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    data = request.get_json(silent=True) or {}

    # 加载商品信息
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT product_title, product_image_url, price, "
                "selling_points, target_audience, extra_info, language "
                "FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            product_inputs = dict(row) if row else {"language": "en"}
    finally:
        conn.close()

    language = product_inputs.get("language", "en")

    # provider：优先用前端传入的
    provider = data.get("provider") or "openrouter"

    # 解析自定义提示词
    custom_prompt = None
    prompt_id = data.get("prompt_id")
    if prompt_id:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt_text, prompt_text_zh FROM user_prompts "
                    "WHERE id = %s AND user_id = %s AND type = 'copywriting'",
                    (prompt_id, current_user.id),
                )
                row = cur.fetchone()
                if row:
                    if language == "zh" and row.get("prompt_text_zh"):
                        custom_prompt = row["prompt_text_zh"]
                    else:
                        custom_prompt = row.get("prompt_text")
        finally:
            conn.close()

    model_override = data.get("model")

    result = preview_request(
        keyframe_paths=task.get("keyframes", []),
        product_inputs=product_inputs,
        provider=provider,
        user_id=current_user.id,
        custom_system_prompt=custom_prompt,
        language=language,
        video_path=task.get("video_path"),
        model_override=model_override,
    )
    return jsonify(result)


@bp.route("/api/copywriting/<task_id>/generate", methods=["POST"])
@login_required
def generate(task_id: str):
    """触发文案生成（首次或重新生成）。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    # 可选：前端传入 prompt_id 和 provider
    data = request.get_json(silent=True) or {}
    if data.get("prompt_id"):
        task_state.update(task_id, prompt_id=data["prompt_id"])
    if data.get("provider"):
        task_state.update(task_id, cw_provider=data["provider"])
    if data.get("model"):
        task_state.update(task_id, cw_model=data["model"])

    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    runner = CopywritingRunner(bus, user_id=current_user.id)
    eventlet.spawn(runner.generate_copy, task_id)

    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/rewrite-segment", methods=["POST"])
@login_required
def rewrite_segment(task_id: str):
    """单段重写。"""
    data = request.get_json()
    if not data or "index" not in data:
        return jsonify(error="缺少 index"), 400

    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404
    if not task.get("copy"):
        return jsonify(error="文案未生成"), 400

    segments = task["copy"].get("segments", [])
    idx = data["index"]
    if idx < 0 or idx >= len(segments):
        return jsonify(error="index 超出范围"), 400

    from pipeline.copywriting import rewrite_segment as _rewrite

    # 解析 provider
    provider = "openrouter"
    try:
        from appcore.api_keys import resolve_extra
        extra = resolve_extra(current_user.id, "translate_preference")
        if extra and extra.get("provider"):
            provider = extra["provider"]
    except Exception:
        pass

    language = "en"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT language FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            if row:
                language = row["language"]
    finally:
        conn.close()

    new_seg = _rewrite(
        full_text=task["copy"].get("full_text", ""),
        segment=segments[idx],
        user_instruction=data.get("instruction", ""),
        provider=provider,
        user_id=current_user.id,
        language=language,
    )

    # 记录 rewrite 的 token 用量
    _rw_usage = new_seg.pop("_usage", None) or {}
    from appcore.usage_log import record as _log_usage
    _log_usage(current_user.id, task_id, f"copywriting_rewrite:{provider}",
               model_name=provider, success=True,
               input_tokens=_rw_usage.get("input_tokens"),
               output_tokens=_rw_usage.get("output_tokens"))

    new_seg["index"] = idx
    task_state.update_copy_segment(task_id, idx, new_seg)

    return jsonify(segment=new_seg)


@bp.route("/api/copywriting/<task_id>/segments", methods=["PUT"])
@login_required
def save_segments(task_id: str):
    """保存用户编辑后的文案。"""
    data = request.get_json()
    if not data or "segments" not in data:
        return jsonify(error="缺少 segments"), 400

    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    copy_data = task.get("copy", {})
    copy_data["segments"] = data["segments"]
    copy_data["full_text"] = " ".join(s["text"] for s in data["segments"])
    task_state.set_copy(task_id, copy_data)

    return jsonify(ok=True)


ALLOWED_FIX_STATUSES = {"pending"}


@bp.route("/api/copywriting/<task_id>/fix-step", methods=["POST"])
@login_required
def fix_step(task_id: str):
    """修正卡住的步骤状态（前端刷新时检测到不一致）。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404
    data = request.get_json(silent=True) or {}
    step = data.get("step")
    status = data.get("status")
    if not step or not status or step not in task.get("steps", {}):
        return jsonify(error="无效的步骤参数"), 400
    if status not in ALLOWED_FIX_STATUSES:
        return jsonify(error="不允许设置该状态"), 400
    task_state.set_step(task_id, step, status)
    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/tts", methods=["POST"])
@login_required
def start_tts(task_id: str):
    """触发 TTS + 合成。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    # 可选：前端传入 voice_id
    data = request.get_json(silent=True) or {}
    if data.get("voice_id"):
        task_state.update(task_id, voice_id=data["voice_id"])

    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    runner = CopywritingRunner(bus, user_id=current_user.id)
    eventlet.spawn(runner.start_tts_compose, task_id)

    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/download/<file_type>")
@login_required
def download(task_id: str, file_type: str):
    """下载产物。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    if file_type == "copy":
        # 导出纯文案文本
        copy_data = task.get("copy", {})
        text = copy_data.get("full_text", "")
        segments_text = "\n\n".join(
            f"[{s.get('label', '')}]\n{s['text']}"
            for s in copy_data.get("segments", [])
        )
        content = segments_text or text
        return content, 200, {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": f"attachment; filename={task_id}_copy.txt",
        }

    result = task.get("result", {})
    path = result.get(file_type)  # "soft_video", "hard_video", "srt"
    if not path or not os.path.isfile(path):
        return jsonify(error="文件不存在"), 404
    return send_file(path, as_attachment=True)


@bp.route("/api/copywriting/<task_id>/keyframe/<int:index>")
@login_required
def get_keyframe(task_id: str, index: int):
    """获取关键帧图片。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    keyframes = task.get("keyframes", [])
    if index < 0 or index >= len(keyframes):
        return jsonify(error="帧不存在"), 404

    return send_file(keyframes[index])


@bp.route("/api/copywriting/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id: str, name: str):
    """获取中间产物（音频预览等）。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404

    if name == "video_source":
        video_path = task.get("video_path")
        if video_path and os.path.isfile(video_path):
            return send_file(video_path)
    elif name == "product_image":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT product_image_url FROM copywriting_inputs WHERE project_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row.get("product_image_url") and os.path.isfile(row["product_image_url"]):
                    return send_file(row["product_image_url"])
        finally:
            conn.close()
    elif name == "thumbnail":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thumbnail_path FROM projects WHERE id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row.get("thumbnail_path") and os.path.isfile(row["thumbnail_path"]):
                    return send_file(row["thumbnail_path"])
        finally:
            conn.close()
    elif name == "tts_audio":
        artifacts = task.get("artifacts", {})
        tts = artifacts.get("tts", {})
        audio_path = tts.get("audio_path")
        if audio_path and os.path.isfile(audio_path):
            return send_file(audio_path)
    elif name == "video":
        result = task.get("result", {})
        video_path = result.get("soft_video")
        if video_path and os.path.isfile(video_path):
            return send_file(video_path)

    return jsonify(error="产物不存在"), 404


# ── 辅助函数 ──────────────────────────────────────────

from pipeline.ffutil import extract_thumbnail as _extract_thumbnail


def _subscribe_socketio(bus: EventBus, socketio):
    """将 EventBus 事件转发到 SocketIO。"""
    def handler(event):
        socketio.emit(event.type, {
            "task_id": event.task_id,
            **event.payload,
        }, room=event.task_id)
    bus.subscribe(handler)
