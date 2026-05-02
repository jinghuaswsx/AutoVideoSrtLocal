"""bulk_translate 父任务 + 视频翻译参数配置的 HTTP API 套件。

本期已实现的端点(由 Phase 3/5 逐步扩充):
  Phase 3:
    POST /api/bulk-translate/estimate     — 费用预估
    GET  /api/video-translate-profile     — 读取合并后的参数
    PUT  /api/video-translate-profile     — 保存参数(三种 scope)

Phase 5 会追加:
    POST /api/bulk-translate/create / start / pause / resume / cancel
    POST /api/bulk-translate/<id>/retry-item / retry-failed
    GET  /api/bulk-translate/<id> / list / audit

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 6 章
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore.bulk_translate_estimator import estimate as do_estimate
from appcore.bulk_translate_runtime import (
    cancel_task,
    create_bulk_translate_task,
    force_backfill_item,
    get_task,
    pause_task,
    resume_task,
    retry_failed_items,
    retry_item,
    run_scheduler,
    start_task,
)
from appcore.bulk_translate_projection import list_admin_tasks
from appcore.db import query
from appcore.events import EVT_BT_DONE, EVT_BT_PROGRESS, Event, EventBus
from appcore.task_recovery import try_register_active_task, unregister_active_task
from appcore.video_translate_defaults import (
    SYSTEM_DEFAULTS,
    load_effective_params,
    save_profile,
)
from web.auth import admin_required
from web.background import start_background_task

log = logging.getLogger(__name__)

bp = Blueprint("bulk_translate", __name__, url_prefix="/api/bulk-translate")
profile_bp = Blueprint("video_translate_profile", __name__,
                        url_prefix="/api/video-translate-profile")
# 页面路由(非 API),没有 /api 前缀
pages_bp = Blueprint("bulk_translate_pages", __name__)


@pages_bp.get("/tasks")
@login_required
def tasks_list_page():
    return render_template("bulk_translate_list.html")


@pages_bp.get("/tasks/<task_id>")
@login_required
def tasks_detail_page(task_id):
    admin_scope = getattr(current_user, "is_admin", False) and request.args.get("scope") == "admin"
    return render_template("bulk_translate_detail.html", task_id=task_id, admin_scope=admin_scope)


@pages_bp.get("/admin/bulk-translate/tasks")
@login_required
@admin_required
def admin_tasks_page():
    return render_template("admin_bulk_translate_tasks.html")


# ============================================================
# POST /api/bulk-translate/estimate
# ============================================================
@bp.post("/estimate")
@login_required
def estimate_endpoint():
    """兼容旧入口：不再执行预估，统一改为完成后计算实际价格。

    Body:
      {
        "product_id": int,              # 必填
        "target_langs": ["de", "fr"],   # 必填,非空
        "content_types": ["copy", ...], # 必填
        "force_retranslate": bool       # 默认 false
      }
    """
    payload = request.get_json(force=True, silent=True) or {}
    product_id = payload.get("product_id")
    target_langs = payload.get("target_langs") or []
    content_types = payload.get("content_types") or []
    force = bool(payload.get("force_retranslate", False))

    if not isinstance(product_id, int):
        return jsonify({"error": "product_id 必填且为 int"}), 400
    if not target_langs or not isinstance(target_langs, list):
        return jsonify({"error": "target_langs 必填且为非空数组"}), 400
    if not content_types or not isinstance(content_types, list):
        return jsonify({"error": "content_types 必填且为非空数组"}), 400

    return jsonify({
        "estimate_enabled": False,
        "message": "estimate disabled; actual cost is calculated after successful completion",
    }), 200


# ============================================================
# GET / PUT /api/video-translate-profile
# ============================================================
@profile_bp.get("")
@profile_bp.get("/")
@login_required
def get_profile():
    """读取合并后的 12 项参数值。三层回填逻辑内置。

    Query args:
      product_id: int|""  — 空字符串视为 None(用户级查询)
      lang:       str|""  — 空字符串视为 None
    """
    product_id_raw = request.args.get("product_id")
    lang_raw = request.args.get("lang")

    product_id = int(product_id_raw) if product_id_raw else None
    lang = lang_raw if lang_raw else None

    params = load_effective_params(current_user.id, product_id, lang)
    return jsonify(params), 200


@profile_bp.put("")
@profile_bp.put("/")
@login_required
def put_profile():
    """保存一条 profile。

    Body:
      {
        "product_id": int|null,  # null 表示用户级
        "lang": str|null,        # null 表示产品级(对所有语言生效)
        "params": { ... }        # 至少一个字段
      }

    scope 对应按钮:
      - 保存配置:            product_id=X, lang=Y
      - 保存为该产品默认:    product_id=X, lang=null
      - 保存为我的默认:      product_id=null, lang=null
    """
    payload = request.get_json(force=True, silent=True) or {}
    product_id = payload.get("product_id")
    lang = payload.get("lang")
    params = payload.get("params")

    if not isinstance(params, dict) or not params:
        return jsonify({"error": "params 必填且为非空 dict"}), 400

    # 白名单校验:只接受 SYSTEM_DEFAULTS 里的 key
    unknown = set(params.keys()) - set(SYSTEM_DEFAULTS.keys())
    if unknown:
        return jsonify({"error": f"未知参数: {sorted(unknown)}"}), 400

    if product_id is not None and not isinstance(product_id, int):
        return jsonify({"error": "product_id 必须是 int 或 null"}), 400

    save_profile(current_user.id, product_id, lang, params)
    return jsonify({"ok": True}), 200


# ============================================================
# Phase 5:父任务生命周期 API
# ============================================================

def _subscribe_socketio(bus: EventBus, socketio) -> None:
    """把父任务 bus 事件桥到 socketio.emit,按 task_id 分房间。"""
    def handler(event: Event) -> None:
        if event.type not in (EVT_BT_PROGRESS, EVT_BT_DONE):
            return
        try:
            socketio.emit(
                event.type,
                {"task_id": event.task_id, **event.payload},
                room=event.task_id,
            )
        except Exception:
            pass
    bus.subscribe(handler)


def _spawn_scheduler(task_id: str) -> None:
    """在 eventlet 绿色线程跑父任务调度循环。铁律:所有调度必须经过这里,
    绝不在进程启动或定时器里触发。"""
    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    try:
        run_scheduler(task_id, bus=bus)
    except Exception:
        # 调度器 greenthread 死掉时绝大多数 pending 项会永远 stuck，必须留下
        # traceback 才能事后定位。原先 silent pass 让 2026-04-27 的事故彻底
        # 无日志：scheduler 在某次循环里抛异常 → pass → pending 永远不派发。
        log.exception("bulk_translate scheduler crashed task_id=%s", task_id)


def start_bulk_scheduler_background(
    task_id: str,
    *,
    user_id: int | None,
    entrypoint: str,
    action: str,
    details: dict | None = None,
) -> bool:
    active_details = {"action": action, **(details or {})}
    if not try_register_active_task(
        "bulk_translate",
        task_id,
        user_id=user_id,
        runner="web.routes.bulk_translate._run_scheduler_with_tracking",
        entrypoint=entrypoint,
        stage="queued_scheduler",
        details=active_details,
    ):
        return False
    try:
        start_background_task(_run_scheduler_with_tracking, task_id)
    except BaseException:
        unregister_active_task("bulk_translate", task_id)
        raise
    return True


def _run_scheduler_with_tracking(task_id: str) -> None:
    try:
        _spawn_scheduler(task_id)
    finally:
        unregister_active_task("bulk_translate", task_id)


def _scheduler_already_running_response():
    return jsonify({"ok": True, "status": "already_running"}), 202


def _load_and_check_ownership(task_id: str):
    """加载父任务并做 owner 校验。返回 task dict 或 Flask Response。"""
    task = get_task(task_id)
    if not task:
        return None, (jsonify({"error": "Task not found"}), 404)
    admin_scope = getattr(current_user, "is_admin", False) and request.args.get("scope") == "admin"
    if task["user_id"] != current_user.id and not admin_scope:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return task, None


# ------------------------------------------------------------
# POST /api/bulk-translate/create  — planning 态,尚未启动
# ------------------------------------------------------------
@bp.post("/create")
@login_required
def create_endpoint():
    payload = request.get_json(force=True, silent=True) or {}
    product_id = payload.get("product_id")
    target_langs = payload.get("target_langs") or []
    content_types = payload.get("content_types") or []
    force = bool(payload.get("force_retranslate", False))
    video_params = payload.get("video_params") or {}

    if not isinstance(product_id, int):
        return jsonify({"error": "product_id 必填且为 int"}), 400
    if not target_langs or not isinstance(target_langs, list):
        return jsonify({"error": "target_langs 必填且为非空数组"}), 400
    if not content_types or not isinstance(content_types, list):
        return jsonify({"error": "content_types 必填且为非空数组"}), 400

    initiator = {
        "user_id": current_user.id,
        "user_name": getattr(current_user, "username", "") or "",
        "ip": request.remote_addr or "",
        "user_agent": request.headers.get("User-Agent", "") or "",
    }
    task_id = create_bulk_translate_task(
        user_id=current_user.id, product_id=product_id,
        target_langs=target_langs, content_types=content_types,
        force_retranslate=force, video_params=video_params,
        initiator=initiator,
    )
    return jsonify({"task_id": task_id, "status": "planning"}), 201


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/start  — planning → running + spawn 调度器
# ------------------------------------------------------------
@bp.post("/<task_id>/start")
@login_required
def start_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    try:
        start_task(task_id, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not start_bulk_scheduler_background(
        task_id,
        user_id=current_user.id,
        entrypoint="bulk_translate.start",
        action="start",
    ):
        return _scheduler_already_running_response()
    return jsonify({"ok": True}), 202


# ------------------------------------------------------------
# GET /api/bulk-translate/<id>  — 详情
# ------------------------------------------------------------
@bp.get("/<task_id>")
@login_required
def get_endpoint(task_id):
    task, err = _load_and_check_ownership(task_id)
    if err:
        return err
    return jsonify({
        "id": task["id"],
        "status": task["status"],
        "user_id": task["user_id"],
        "state": task["state"],
        "created_at": task["created_at"].isoformat() if task["created_at"] else None,
        "updated_at": task["updated_at"].isoformat() if task["updated_at"] else None,  # updated_at 可能为 None
    }), 200


# ------------------------------------------------------------
# GET /api/bulk-translate/list  — 当前用户的任务列表(支持 status 筛选)
# ------------------------------------------------------------
@bp.get("/list")
@login_required
def list_endpoint():
    status = request.args.get("status")
    where = "user_id = %s AND type = 'bulk_translate'"
    args: list = [current_user.id]
    if status:
        where += " AND status = %s"
        args.append(status)

    rows = query(
        f"SELECT id, status, state_json, created_at "
        f"FROM projects WHERE {where} ORDER BY created_at DESC LIMIT 200",
        tuple(args),
    )

    result = []
    for r in rows:
        import json as _j
        raw = r["state_json"]
        state = raw if isinstance(raw, dict) else _j.loads(raw or "{}")
        result.append({
            "id": r["id"],
            "status": r["status"],
            "product_id": state.get("product_id"),
            "target_langs": state.get("target_langs"),
            "content_types": state.get("content_types"),
            "progress": state.get("progress"),
            "cost_estimate": None,
            "cost_actual": state.get("cost_tracking", {}).get("actual", {}).get("actual_cost_cny"),
            "initiator": state.get("initiator"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return jsonify(result), 200


@bp.get("/admin/list")
@login_required
@admin_required
def admin_list_endpoint():
    limit = request.args.get("limit", type=int) or 300
    return jsonify(list_admin_tasks(limit=limit)), 200


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/pause
# ------------------------------------------------------------
@bp.post("/<task_id>/pause")
@login_required
def pause_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    try:
        pause_task(task_id, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/resume  — 对账 + 继续调度
# ------------------------------------------------------------
@bp.post("/<task_id>/resume")
@login_required
def resume_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    try:
        resume_task(task_id, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not start_bulk_scheduler_background(
        task_id,
        user_id=current_user.id,
        entrypoint="bulk_translate.resume",
        action="resume",
    ):
        return _scheduler_already_running_response()
    return jsonify({"ok": True}), 202


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/cancel
# ------------------------------------------------------------
@bp.post("/<task_id>/cancel")
@login_required
def cancel_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    try:
        cancel_task(task_id, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/retry-item
# ------------------------------------------------------------
@bp.post("/<task_id>/retry-item")
@login_required
def retry_item_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    payload = request.get_json(force=True, silent=True) or {}
    idx = payload.get("idx")
    if not isinstance(idx, int):
        return jsonify({"error": "idx 必填且为 int"}), 400
    try:
        retry_item(task_id, idx=idx, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not start_bulk_scheduler_background(
        task_id,
        user_id=current_user.id,
        entrypoint="bulk_translate.retry_item",
        action="retry_item",
        details={"idx": idx},
    ):
        return _scheduler_already_running_response()
    return jsonify({"ok": True}), 202


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/retry-failed
# ------------------------------------------------------------
@bp.post("/<task_id>/retry-failed")
@login_required
def retry_failed_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    try:
        retry_failed_items(task_id, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not start_bulk_scheduler_background(
        task_id,
        user_id=current_user.id,
        entrypoint="bulk_translate.retry_failed",
        action="retry_failed",
    ):
        return _scheduler_already_running_response()
    return jsonify({"ok": True}), 202


# ------------------------------------------------------------
# POST /api/bulk-translate/<id>/force-backfill-item
# ------------------------------------------------------------
@bp.post("/<task_id>/force-backfill-item")
@login_required
def force_backfill_item_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    payload = request.get_json(force=True, silent=True) or {}
    idx = payload.get("idx")
    if not isinstance(idx, int):
        return jsonify({"error": "idx 必填且为 int"}), 400
    try:
        force_backfill_item(task_id, idx=idx, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True}), 202


# ------------------------------------------------------------
# GET /api/bulk-translate/<id>/audit  — audit_events 时间线
# ------------------------------------------------------------
@bp.get("/<task_id>/audit")
@login_required
def audit_endpoint(task_id):
    task, err = _load_and_check_ownership(task_id)
    if err:
        return err
    return jsonify(task["state"].get("audit_events", [])), 200
