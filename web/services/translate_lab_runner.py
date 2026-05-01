"""视频翻译（测试）Socket.IO 适配 + 后台运行。

桥接 appcore.runtime_v2.PipelineRunnerV2 事件总线到 Flask-SocketIO，
事件以 task_id 作为 room 广播。为保持与其他 runner（de/fr）行为一致：
每个 task 启动时新建一个 EventBus，订阅一个 per-task handler，线程跑 V2
runner 的 start / resume。
"""
from __future__ import annotations

from appcore.events import EventBus
from appcore.runner_lifecycle import start_tracked_thread
from appcore.runtime_v2 import PipelineRunnerV2
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    """返回一个将 bus 事件按 room=task_id 广播到 Socket.IO 的 handler。"""

    def handler(event):
        try:
            socketio.emit(event.type, event.payload, room=task_id)
        except Exception:
            # 防止 handler 异常破坏事件循环
            pass

    return handler


def start(task_id: str, user_id: int | None = None) -> bool:
    """在后台线程启动 PipelineRunnerV2.start(task_id)。"""
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunnerV2(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=runner.start,
        args=(task_id,),
        daemon=True,
    )


def resume(
    task_id: str, start_step: str, user_id: int | None = None,
) -> bool:
    """在后台线程调用 PipelineRunnerV2.resume(task_id, start_step)。"""
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunnerV2(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=runner.resume,
        args=(task_id, start_step),
        daemon=True,
    )
