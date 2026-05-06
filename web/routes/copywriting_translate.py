"""copywriting_translate 子任务的 HTTP 入口。

父任务调度器(Phase 4)会直接在 Python 层调用 CopywritingTranslateRunner,
不走 HTTP。本路由主要给前端/测试工具手动触发单条翻译用,
以及后续"素材详情页 · 重新翻译此条"按钮。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 2.2 节
"""
from __future__ import annotations

import json
import uuid

from flask import Blueprint, request
from flask_login import current_user, login_required

from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
from appcore.db import execute as db_execute
from appcore.events import Event, EventBus, EVT_CT_PROGRESS
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
    db_execute(
        """
        INSERT INTO projects (id, user_id, type, status, state_json)
        VALUES (%s, %s, 'copywriting_translate', 'queued', %s)
        """,
        (task_id, current_user.id, json.dumps(state, ensure_ascii=False)),
    )

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
