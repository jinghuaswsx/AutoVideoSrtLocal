"""copywriting_translate 子任务的 HTTP 入口。

父任务调度器(Phase 4)会直接在 Python 层调用 CopywritingTranslateRunner,
不走 HTTP。本路由主要给前端/测试工具手动触发单条翻译用,
以及后续"素材详情页 · 重新翻译此条"按钮。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 2.2 节
"""
from __future__ import annotations

import json
import uuid

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
from appcore.db import query_one
from appcore.events import Event, EventBus, EVT_CT_PROGRESS
from appcore import project_state as project_store
from appcore.task_recovery import try_register_active_task, unregister_active_task
from web.background import start_background_task
from web.services.copywriting_translate import (
    build_copywriting_translate_already_running_response,
    build_copywriting_translate_missing_source_copy_response,
    build_copywriting_translate_missing_target_lang_response,
    build_copywriting_translate_started_response,
    copywriting_translate_flask_response,
)

bp = Blueprint("copywriting_translate", __name__,
                url_prefix="/api/copywriting-translate")
pages_bp = Blueprint("copywriting_translate_pages", __name__)


def _is_admin_user() -> bool:
    return (
        bool(getattr(current_user, "is_superadmin", False))
        or bool(getattr(current_user, "is_admin", False))
        or getattr(current_user, "role", "") == "admin"
    )


def _parse_state_json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _stringify_time(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ")
    return str(value)


def _load_copy_row(copy_id) -> dict | None:
    normalized = _positive_int(copy_id)
    if not normalized:
        return None
    row = query_one(
        "SELECT id, product_id, lang, idx, title, body, description, "
        "ad_carrier, ad_copy, ad_keywords, created_at, updated_at "
        "FROM media_copywritings WHERE id=%s",
        (normalized,),
    )
    return dict(row) if row else None


def _load_copywriting_translate_detail(task_id: str) -> dict:
    row = query_one(
        "SELECT id, user_id, status, state_json, created_at "
        "FROM projects "
        "WHERE id=%s AND type='copywriting_translate' AND deleted_at IS NULL",
        (task_id,),
    )
    if not row:
        abort(404)
    if str(row.get("user_id")) != str(getattr(current_user, "id", "")) and not _is_admin_user():
        abort(404)
    state = _parse_state_json(row.get("state_json"))
    source_copy_id = _positive_int(state.get("source_copy_id"))
    target_copy_id = _positive_int(state.get("target_copy_id"))
    parent_task_id = str(state.get("parent_task_id") or "").strip()
    return {
        "task": {
            "id": str(row.get("id") or task_id),
            "status": row.get("status") or "",
            "source_lang": state.get("source_lang") or "",
            "target_lang": state.get("target_lang") or "",
            "parent_task_id": parent_task_id,
            "parent_task_url": f"/tasks/{parent_task_id}" if parent_task_id else "",
            "source_copy_id": source_copy_id,
            "target_copy_id": target_copy_id,
            "tokens_used": int(state.get("tokens_used") or 0),
            "last_error": state.get("last_error") or "",
            "created_at": _stringify_time(row.get("created_at")),
            "updated_at": _stringify_time(row.get("created_at")),
        },
        "source_copy": _load_copy_row(source_copy_id),
        "target_copy": _load_copy_row(target_copy_id),
    }


def _subscribe_socketio(bus: EventBus, socketio) -> None:
    """把 bus 上的 CT 事件桥接到 SocketIO。事件按 task_id 分房间推送。"""
    def handler(event: Event) -> None:
        if event.type != EVT_CT_PROGRESS:
            return
        try:
            socketio.emit(
                EVT_CT_PROGRESS,
                {"task_id": event.task_id, **event.payload},
                room=event.task_id,
            )
        except Exception:
            pass
    bus.subscribe(handler)


def _spawn_runner(task_id: str) -> None:
    """在 eventlet 绿色线程里跑子任务,失败时状态已在 Runner 内标记。"""
    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    try:
        CopywritingTranslateRunner(task_id, bus=bus).start()
    except Exception:
        # 错误状态已写入 projects 行,异常吞掉防 greenthread 崩溃日志刷屏。
        pass


def _start_runner_background(
    task_id: str,
    *,
    user_id: int,
    details: dict,
) -> bool:
    if not try_register_active_task(
        "copywriting_translate",
        task_id,
        user_id=user_id,
        runner="web.routes.copywriting_translate._run_runner_with_tracking",
        entrypoint="copywriting_translate.start",
        stage="queued_translate",
        details=details,
    ):
        return False
    try:
        start_background_task(_run_runner_with_tracking, task_id)
    except BaseException:
        unregister_active_task("copywriting_translate", task_id)
        raise
    return True


def _run_runner_with_tracking(task_id: str) -> None:
    try:
        _spawn_runner(task_id)
    finally:
        unregister_active_task("copywriting_translate", task_id)


@bp.post("/start")
@login_required
def start():
    """
    POST /api/copywriting-translate/start

    Body:
      {
        "source_copy_id": 123,      # 必填
        "target_lang": "de",        # 必填
        "source_lang": "en",        # 可选,默认 "en"
        "parent_task_id": "uuid|null"  # 可选,父 bulk_translate 任务 id
      }

    返回: 202 + { "task_id": "<new-project-uuid>" }
    """
    payload = request.get_json(force=True, silent=True) or {}
    source_copy_id = payload.get("source_copy_id")
    target_lang = (payload.get("target_lang") or "").strip()
    source_lang = (payload.get("source_lang") or "en").strip()
    parent_task_id = payload.get("parent_task_id")

    if not source_copy_id or not isinstance(source_copy_id, int):
        return copywriting_translate_flask_response(
            build_copywriting_translate_missing_source_copy_response()
        )
    if not target_lang:
        return copywriting_translate_flask_response(
            build_copywriting_translate_missing_target_lang_response()
        )

    task_id = str(uuid.uuid4())
    state = {
        "source_copy_id": source_copy_id,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "parent_task_id": parent_task_id,
    }
    project_store.create_copywriting_translate_project(task_id, current_user.id, state)

    if not _start_runner_background(
        task_id,
        user_id=current_user.id,
        details={
            "source_copy_id": source_copy_id,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "parent_task_id": parent_task_id,
        },
    ):
        return copywriting_translate_flask_response(
            build_copywriting_translate_already_running_response()
        )
    return copywriting_translate_flask_response(
        build_copywriting_translate_started_response(task_id=task_id)
    )


@bp.get("/<task_id>")
@login_required
def api_detail(task_id: str):
    return jsonify(_load_copywriting_translate_detail(task_id))


@pages_bp.get("/copywriting-translate/<task_id>")
@login_required
def detail_page(task_id: str):
    return render_template(
        "copywriting_translate_detail.html",
        detail=_load_copywriting_translate_detail(task_id),
    )
