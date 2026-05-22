"""Task creator (non-mk) — 从 Shopify 链接到任务中心的完整项目流水线路由."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from flask import Blueprint, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import (
    task_creator,
    task_creator_project_store,
)
from appcore.active_tasks import try_register, unregister as unregister_active
from appcore.project_state import save_project_state, update_project_state
from appcore.settings import get_retention_hours
from appcore.task_recovery import (
    RECOVERY_ERROR_MESSAGE,
    try_register_active_task,
    unregister_active_task,
)
from appcore.video_cover_generation import VideoCoverGenerationError
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.ffutil import extract_thumbnail
from web.auth import admin_required
from web.background import start_background_task
from web.upload_util import (
    client_filename_basename,
    save_uploaded_file_to_path,
    secure_filename_component,
    validate_video_extension,
)

bp = Blueprint("task_creator", __name__)

STEP_ORDER = task_creator.STEP_ORDER
STEP_LABELS = task_creator.STEP_LABELS
AUTO_CHAIN_LAST = task_creator.AUTO_CHAIN_LAST


def _json_response(data: dict, status: int = 200):
    from flask import jsonify
    return jsonify(data), status


def _time_time() -> float:
    return time.time()


def _load_project(task_id: str) -> tuple[dict | None, dict]:
    row = task_creator_project_store.get_project(
        task_id, user_id=0, is_admin=True
    )
    if not row:
        return None, {}
    try:
        state = json.loads(row.get("state_json") or "{}")
    except Exception:
        state = {}
    return row, state


def _load_user_project(task_id: str) -> tuple[dict | None, dict]:
    row = task_creator_project_store.get_project(
        task_id,
        user_id=int(current_user.id),
        is_admin=current_user.is_admin,
    )
    if not row:
        return None, {}
    try:
        state = json.loads(row.get("state_json") or "{}")
    except Exception:
        state = {}
    return row, state


def _save_state(task_id: str, state: dict, *, status: str | None = None) -> None:
    save_project_state(task_id, state, status=status)


def _state_with_urls(task_id: str, state: dict) -> dict:
    """Attach media URLs to step outputs for frontend display."""
    view = dict(state)
    # Attach cover URLs
    cover_result = view.get("cover_result") or {}
    covers = []
    for c in (cover_result.get("covers") or []):
        row = dict(c)
        if row.get("object_key") and not row.get("url"):
            row["url"] = url_for("medias.media_object_proxy", object_key=row["object_key"])
        covers.append(row)
    if covers:
        view.setdefault("cover_result", {})["covers"] = covers

    # Attach shopify product image URL
    sp = view.get("shopify_product") or {}
    if sp.get("main_image_url") and not sp.get("main_image_proxy_url"):
        try:
            obj_key = sp.get("main_image_object_key") or ""
            if obj_key:
                sp["main_image_proxy_url"] = url_for("medias.media_object_proxy", object_key=obj_key)
        except Exception:
            pass
        view["shopify_product"] = sp

    return view


def _norm_steps(state: dict) -> dict:
    """Normalize step timing for display."""
    steps = state.get("steps") or {}
    messages = state.get("step_messages") or {}
    timing = {}
    now = _time_time()
    for step, t in (state.get("step_timing") or {}).items():
        row = dict(t or {})
        if steps.get(step) == "running" and row.get("started_at"):
            row["running_seconds"] = max(0, int(round(now - float(row["started_at"]))))
        timing[step] = row
    return {
        "steps": steps,
        "step_messages": messages,
        "step_timing": timing,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@bp.route("/task-creator/", methods=["GET"])
@login_required
@admin_required
def list_page():
    projects = task_creator_project_store.list_projects(
        user_id=int(current_user.id),
        is_admin=current_user.is_admin,
    )
    return render_template(
        "task_creator_list.html",
        projects=projects,
        step_order=STEP_ORDER,
        step_labels=STEP_LABELS,
    )


@bp.route("/task-creator/<task_id>", methods=["GET"])
@login_required
@admin_required
def detail_page(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return "Not Found", 404
    view_state = _state_with_urls(task_id, state)
    step_info = _norm_steps(state)
    return render_template(
        "task_creator_detail.html",
        project=row,
        state=view_state,
        task_id=task_id,
        step_order=STEP_ORDER,
        step_labels=STEP_LABELS,
        auto_chain_last=AUTO_CHAIN_LAST,
        step_info=step_info,
    )


# ---------------------------------------------------------------------------
# API: Create project
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/projects", methods=["POST"])
@login_required
@admin_required
def api_create_project():
    try:
        shopify_url = str(request.form.get("shopify_url") or "").strip()
        if not shopify_url:
            raise VideoCoverGenerationError("请输入 Shopify 商品链接")
        product_name_cn = str(request.form.get("product_name_cn") or "").strip()
        if not product_name_cn:
            raise VideoCoverGenerationError("请输入产品中文名称")

        upload = request.files.get("video_file")
        if not upload or not (upload.filename or "").strip():
            raise VideoCoverGenerationError("请上传视频文件")
        original_filename = client_filename_basename(upload.filename)
        if not validate_video_extension(original_filename):
            raise VideoCoverGenerationError("不支持的视频格式")

        task_id = uuid.uuid4().hex
        task_dir = os.path.join(OUTPUT_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)

        safe_name = secure_filename_component(original_filename)
        video_path = os.path.join(UPLOAD_DIR, f"{task_id}_video_{safe_name}")
        save_uploaded_file_to_path(upload, video_path)

        thumbnail_path = None
        try:
            thumbnail_path = extract_thumbnail(video_path, os.path.join(task_dir, "card_thumb.jpg"))
        except Exception:
            pass

        display_name = Path(original_filename).stem or "task-creator"
        state = task_creator._initial_state(
            task_id=task_id,
            user_id=int(current_user.id),
            product_name_cn=product_name_cn,
            shopify_url=shopify_url,
            video_path=video_path,
            video_filename=original_filename,
            task_dir=task_dir,
            display_name=display_name,
            thumbnail_path=thumbnail_path,
        )
        task_creator_project_store.insert_project(
            task_id=task_id,
            user_id=int(current_user.id),
            original_filename=original_filename,
            display_name=display_name,
            thumbnail_path=thumbnail_path,
            task_dir=task_dir,
            state=state,
            retention_hours=get_retention_hours(task_creator_project_store.TASK_CREATOR_TYPE),
        )
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    return _json_response({"ok": True, "id": task_id}, 201)


# ---------------------------------------------------------------------------
# API: Delete project
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/<task_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_project(task_id: str):
    row, _state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)

    from appcore import cleanup
    try:
        cleanup.delete_task_storage({
            "task_dir": row.get("task_dir") or "",
            "state_json": row.get("state_json") or "",
        })
    except Exception:
        pass

    task_creator_project_store.soft_delete_project(
        task_id,
        user_id=int(current_user.id),
        is_admin=current_user.is_admin,
    )
    return _json_response({"ok": True})


# ---------------------------------------------------------------------------
# API: Get project state
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/<task_id>/state", methods=["GET"])
@login_required
@admin_required
def api_project_state(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    step_info = _norm_steps(state)
    return _json_response({
        "ok": True,
        "status": state.get("status"),
        **step_info,
        "config": state.get("config") or {},
        "shopify_product": state.get("shopify_product"),
        "material_result": state.get("material_result"),
        "task_result": state.get("task_result"),
        "display_name": state.get("display_name"),
        "product_name_cn": state.get("product_name_cn"),
        "shopify_url": state.get("shopify_url"),
    })


# ---------------------------------------------------------------------------
# API: Run step
# ---------------------------------------------------------------------------

def _run_task_creator_chain(task_id: str, start_step: str) -> None:
    row, state = _load_project(task_id)
    if not row:
        return
    state.setdefault("id", task_id)
    state.setdefault("steps", {step: "pending" for step in STEP_ORDER})
    state.setdefault("step_messages", {step: "" for step in STEP_ORDER})
    user_id = int(row.get("user_id") or state.get("user_id") or 0)

    # Clear from start_step onward
    task_creator._clear_step_outputs(state, start_step)

    # Determine chain end: auto chain stops before material_ingest
    start_idx = task_creator._step_index(start_step)
    auto_chain_idx = task_creator._step_index(AUTO_CHAIN_LAST)
    if start_idx <= auto_chain_idx:
        chain_end = AUTO_CHAIN_LAST
    else:
        chain_end = start_step

    for step in STEP_ORDER[start_idx : task_creator._step_index(chain_end) + 1]:
        try:
            task_creator._ensure_previous_steps_done(state, step)
            _mark_step_running(state, step)
            _save_state(task_id, state, status="running")

            task_creator.run_step(state, step, user_id=user_id)

            _mark_step_done(state, step)
            next_status = "done" if step == STEP_ORDER[-1] else "running"
            _save_state(task_id, state, status=next_status)
        except VideoCoverGenerationError as exc:
            _mark_step_error(state, step, str(exc))
            _save_state(task_id, state, status="error")
            unregister_active_task("task_creator", task_id)
            return
        except Exception as exc:
            _mark_step_error(state, step, f"{STEP_LABELS[step]}失败：{exc}")
            _save_state(task_id, state, status="error")
            unregister_active_task("task_creator", task_id)
            return

    unregister_active_task("task_creator", task_id)


def _mark_step_running(state: dict, step: str) -> None:
    now = _time_time()
    state.setdefault("steps", {})[step] = "running"
    state.setdefault("step_messages", {})[step] = "运行中..."
    state.setdefault("step_timing", {}).setdefault(step, {})["started_at"] = now


def _mark_step_done(state: dict, step: str) -> None:
    now = _time_time()
    timing = state.setdefault("step_timing", {}).setdefault(step, {})
    started = float(timing.get("started_at") or now)
    timing["finished_at"] = now
    timing["elapsed_seconds"] = max(0, int(round(now - started)))
    state.setdefault("steps", {})[step] = "done"
    state.setdefault("step_messages", {})[step] = f"已完成，耗时 {timing['elapsed_seconds']} 秒"


def _mark_step_error(state: dict, step: str, message: str) -> None:
    now = _time_time()
    timing = state.setdefault("step_timing", {}).setdefault(step, {})
    started = float(timing.get("started_at") or now)
    timing["finished_at"] = now
    timing["elapsed_seconds"] = max(0, int(round(now - started)))
    state.setdefault("steps", {})[step] = "error"
    state.setdefault("step_messages", {})[step] = message
    state["error"] = message


@bp.route("/task-creator/api/<task_id>/run/<step>", methods=["POST"])
@login_required
@admin_required
def api_run_step(task_id: str, step: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)

    if step not in STEP_ORDER:
        return _json_response({"ok": False, "error": f"未知步骤：{step}"}, 400)

    # Steps 6 and 7 run only themselves (not chain)
    if step in ("material_ingest", "task_creation"):
        if not try_register_active_task("task_creator", task_id):
            return _json_response({"ok": False, "error": "任务正在执行中，请稍后再试"}, 409)
        start_background_task(
            _run_single_step,
            task_id,
            step,
            user_id=int(current_user.id),
        )
        return _json_response({"ok": True, "step": step})

    # Steps 1-5 chain
    try:
        task_creator._ensure_previous_steps_done(state, step)
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)

    if not try_register_active_task("task_creator", task_id):
        return _json_response({"ok": False, "error": "任务正在执行中，请稍后再试"}, 409)

    start_background_task(_run_task_creator_chain, task_id, step)
    return _json_response({"ok": True, "step": step})


def _run_single_step(task_id: str, step: str, *, user_id: int = 0) -> None:
    row, state = _load_project(task_id)
    if not row:
        unregister_active_task("task_creator", task_id)
        return
    state.setdefault("steps", {step: "pending" for step in STEP_ORDER})
    state.setdefault("step_messages", {step: "" for step in STEP_ORDER})

    task_creator._clear_step_outputs(state, step)

    try:
        task_creator._ensure_previous_steps_done(state, step)
        _mark_step_running(state, step)
        _save_state(task_id, state, status="running")

        task_creator.run_step(state, step, user_id=user_id)

        _mark_step_done(state, step)
        next_status = "done" if step == STEP_ORDER[-1] else "running"
        _save_state(task_id, state, status=next_status)
    except VideoCoverGenerationError as exc:
        _mark_step_error(state, step, str(exc))
        _save_state(task_id, state, status="error")
    except Exception as exc:
        _mark_step_error(state, step, f"{STEP_LABELS[step]}失败：{exc}")
        _save_state(task_id, state, status="error")
    finally:
        unregister_active_task("task_creator", task_id)


# ---------------------------------------------------------------------------
# API: Restart
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/<task_id>/restart", methods=["POST"])
@login_required
@admin_required
def api_restart_project(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)

    if not try_register_active_task("task_creator", task_id):
        return _json_response({"ok": False, "error": "任务正在执行中，请稍后再试"}, 409)

    task_creator._clear_all_outputs(state)
    _save_state(task_id, state, status="running")
    start_background_task(_run_task_creator_chain, task_id, "shopify_extract")
    return _json_response({"ok": True})


# ---------------------------------------------------------------------------
# API: Update config
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/<task_id>/config", methods=["PATCH"])
@login_required
@admin_required
def api_update_config(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)

    body = request.get_json(silent=True) or {}
    config = state.setdefault("config", {})

    for key in ("domain", "filename", "countries", "language_assignments",
                 "raw_processor_id", "translator_id"):
        if key in body:
            config[key] = body[key]

    _save_state(task_id, state)
    return _json_response({"ok": True, "config": config})


# ---------------------------------------------------------------------------
# API: Shopify preview
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/shopify-preview", methods=["POST"])
@login_required
@admin_required
def api_shopify_preview():
    shopify_url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not shopify_url:
        return _json_response({"ok": False, "error": "请输入 Shopify 商品链接"}, 400)

    from appcore.meta_hot_posts.product_analysis import fetch_product_analysis
    try:
        product = fetch_product_analysis(shopify_url)
    except Exception as exc:
        return _json_response({"ok": False, "error": f"获取商品信息失败：{exc}"}, 502)

    if not product or not getattr(product, "title", None):
        return _json_response({"ok": False, "error": "无法从链接提取商品信息"}, 400)

    from urllib.parse import urlparse
    parsed = urlparse(shopify_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    product_code = ""
    if "products" in path_parts:
        idx = path_parts.index("products")
        if idx + 1 < len(path_parts):
            product_code = path_parts[idx + 1]

    return _json_response({
        "ok": True,
        "title": str(getattr(product, "title", "") or ""),
        "main_image_url": str(getattr(product, "main_image_url", "") or ""),
        "product_code": product_code,
        "price_min": getattr(product, "price_min", None),
        "price_max": getattr(product, "price_max", None),
        "currency": getattr(product, "currency", "USD"),
    })


# ---------------------------------------------------------------------------
# API: Domains / Users / Languages
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/domains", methods=["GET"])
@login_required
@admin_required
def api_domains():
    from appcore.product_link_domains import list_domains
    try:
        domains = list_domains(include_disabled=False)
    except Exception:
        domains = []
    return _json_response({"ok": True, "domains": domains})


@bp.route("/task-creator/api/users/translators", methods=["GET"])
@login_required
@admin_required
def api_translators():
    """Reuse the existing tasks endpoint for translator list."""
    from web.routes.tasks import api_translators as tasks_api_translators
    return tasks_api_translators()


@bp.route("/task-creator/api/users/raw-processors", methods=["GET"])
@login_required
@admin_required
def api_raw_processors():
    from web.routes.tasks import api_raw_processors as tasks_api_raw_processors
    return tasks_api_raw_processors()


@bp.route("/task-creator/api/languages", methods=["GET"])
@login_required
@admin_required
def api_languages():
    from web.routes.tasks import api_languages as tasks_api_languages
    return tasks_api_languages()


# ---------------------------------------------------------------------------
# API: Source video
# ---------------------------------------------------------------------------

@bp.route("/task-creator/api/<task_id>/source-video", methods=["GET"])
@login_required
@admin_required
def api_source_video(task_id: str):
    from flask import send_file
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    video_path = state.get("video_path") or ""
    if not video_path or not Path(video_path).is_file():
        return _json_response({"ok": False, "error": "video not found"}, 404)
    return send_file(video_path, mimetype="video/mp4")


@bp.route("/task-creator/api/<task_id>/product-image", methods=["GET"])
@login_required
@admin_required
def api_product_image(task_id: str):
    from flask import send_file
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    sp = state.get("shopify_product") or {}
    img_path = sp.get("main_image_path") or ""
    if not img_path or not Path(img_path).is_file():
        return _json_response({"ok": False, "error": "image not found"}, 404)
    return send_file(img_path, mimetype="image/jpeg")
