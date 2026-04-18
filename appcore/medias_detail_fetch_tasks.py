"""商品详情图「从 URL 一键下载」的后台任务管理器（进程内存态，TTL 30 min）。

不依赖 Celery 或 DB，前端轮询 /status 拿进度即可。失败 / 完成后状态保留 30 分钟供 UI 读取。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

log = logging.getLogger(__name__)

TTL_SECONDS = 30 * 60
_CLEANUP_INTERVAL = 60
_MAX_WORKERS = 2

_TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS)


def create(*, user_id: int, product_id: int, url: str, lang: str,
           worker) -> str:
    """启动一个抓取任务。`worker` 是一个回调 (task_id, update_fn) → None，
    负责实际 fetch + 下载 + 写 DB。progress 由 worker 通过 update_fn 推进。
    """
    task_id = "mdf_" + uuid.uuid4().hex
    with _LOCK:
        _TASKS[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "product_id": product_id,
            "url": url,
            "lang": lang,
            "status": "pending",   # pending → fetching → downloading → done / failed
            "progress": 0,          # 已处理的图片数
            "total": 0,             # 检测到的总数（fetch 后才知道）
            "current_url": "",
            "inserted": [],
            "errors": [],
            "message": "等待启动",
            "error": None,
            "_expires_at": time.time() + TTL_SECONDS,
        }

    def _update(**patch):
        with _LOCK:
            t = _TASKS.get(task_id)
            if t:
                t.update(patch)
                t["_expires_at"] = time.time() + TTL_SECONDS

    def _run():
        try:
            worker(task_id, _update)
        except Exception as exc:
            log.exception("medias_detail_fetch worker failed: %s", exc)
            _update(status="failed", error=str(exc), message=f"任务异常：{exc}")

    _EXECUTOR.submit(_run)
    return task_id


def get(task_id: str, *, user_id: int) -> Optional[dict]:
    with _LOCK:
        t = _TASKS.get(task_id)
        if t and t.get("user_id") == user_id:
            # 返回时去掉下划线开头的内部字段
            return {k: v for k, v in t.items() if not k.startswith("_")}
    return None


def _cleanup_expired() -> None:
    now = time.time()
    purge: list[str] = []
    with _LOCK:
        for tid, t in _TASKS.items():
            if t.get("_expires_at", 0) <= now:
                purge.append(tid)
        for tid in purge:
            _TASKS.pop(tid, None)


def _cleanup_loop() -> None:
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        try:
            _cleanup_expired()
        except Exception:
            log.warning("medias_detail_fetch TTL cleanup failed", exc_info=True)


_cleanup_thread = threading.Thread(
    target=_cleanup_loop, daemon=True, name="mdf-cleanup",
)
_cleanup_thread.start()
