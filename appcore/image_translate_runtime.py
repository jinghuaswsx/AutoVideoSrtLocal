"""图片翻译后台 runtime：串行处理 items，自动重试 3 次，失败不中断。"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from collections import deque

from appcore import gemini_image, tos_clients
from appcore.events import Event, EventBus
from web import store

logger = logging.getLogger(__name__)


_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0  # 秒

# 任务级熔断：当上游持续返回 429/5xx 时，避免把所有 items 都跑完 3 次重试
# 形成 retry 风暴，进而触发宿主机 watchdog 强制重启 VM（2026-04-17 事故）
_RATE_LIMIT_THRESHOLD = 5
_RATE_LIMIT_WINDOW_SEC = 60.0


class _CircuitOpen(Exception):
    """上游持续限流，任务级熔断信号；由 start() 在外层捕获。"""


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _update_progress(task: dict) -> None:
    items = task.get("items") or []
    total = len(items)
    done = sum(1 for it in items if it["status"] == "done")
    failed = sum(1 for it in items if it["status"] == "failed")
    running = sum(1 for it in items if it["status"] == "running")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": running}


class ImageTranslateRuntime:
    def __init__(self, *, bus: EventBus, user_id: int | None = None) -> None:
        self.bus = bus
        self.user_id = user_id
        self._rate_limit_hits: deque[float] = deque()

    def _record_rate_limit_hit(self) -> bool:
        """记一次可重试错误（429/5xx），返回 True 表示应熔断整任务。"""
        now = time.monotonic()
        cutoff = now - _RATE_LIMIT_WINDOW_SEC
        while self._rate_limit_hits and self._rate_limit_hits[0] < cutoff:
            self._rate_limit_hits.popleft()
        self._rate_limit_hits.append(now)
        return len(self._rate_limit_hits) >= _RATE_LIMIT_THRESHOLD

    def start(self, task_id: str) -> None:
        task = store.get(task_id)
        if not task or task.get("type") != "image_translate":
            logger.warning("image_translate runtime: task not found: %s", task_id)
            return

        task["status"] = "running"
        task["steps"]["process"] = "running"
        # 记录图片翻译使用的模型
        _it_model = task.get("model_id") or "gemini-2.5-flash"
        task.setdefault("step_model_tags", {})["process"] = f"gemini · {_it_model}"
        store.update(task_id, status="running", steps=task["steps"],
                     step_model_tags=task.get("step_model_tags", {}))

        items = task.get("items") or []
        circuit_msg = ""
        try:
            for idx in range(len(items)):
                if items[idx]["status"] in {"done", "failed"}:
                    continue
                self._process_one(task, task_id, idx)
        except _CircuitOpen as exc:
            circuit_msg = str(exc) or "上游持续限流，已熔断"
            logger.warning(
                "[image_translate] circuit breaker opened for task %s: %s",
                task_id, circuit_msg,
            )
            self._abort_remaining_items(task, task_id, circuit_msg)

        if circuit_msg:
            task["status"] = "error"
            task["steps"]["process"] = "error"
            task["error"] = circuit_msg
        else:
            task["status"] = "done"
            task["steps"]["process"] = "done"
        _update_progress(task)
        store.update(
            task_id,
            status=task["status"],
            steps=task["steps"],
            progress=task["progress"],
            items=task["items"],
        )
        self.bus.publish(Event(
            type="image_translate:task_done",
            task_id=task_id,
            payload={"task_id": task_id, "status": task["status"]},
        ))

    def _abort_remaining_items(self, task: dict, task_id: str, reason: str) -> None:
        for it in task["items"]:
            if it["status"] in {"done", "failed"}:
                continue
            it["status"] = "failed"
            it["error"] = f"已熔断（上游持续限流）：{reason}"
            self._emit_item(task_id, it)
        _update_progress(task)
        self._emit_progress(task_id, task["progress"])

    def _process_one(self, task: dict, task_id: str, idx: int) -> None:
        item = task["items"][idx]
        item["status"] = "running"
        _update_progress(task)
        store.update(task_id, items=task["items"], progress=task["progress"])
        self._emit_item(task_id, item)

        attempts = 0
        while attempts < _MAX_ATTEMPTS:
            attempts += 1
            item["attempts"] = attempts
            src_path = ""
            dst_path = ""
            try:
                # 1. 下载原图到临时文件
                src_suffix = self._ext_from_key(item["src_tos_key"]) or ".jpg"
                src_fd, src_path = tempfile.mkstemp(suffix=src_suffix, prefix="it_src_")
                os.close(src_fd)
                tos_clients.download_file(item["src_tos_key"], src_path)
                with open(src_path, "rb") as f:
                    src_bytes = f.read()
                mime = self._guess_mime(item["src_tos_key"])

                # 2. 调 gemini_image
                out_bytes, out_mime = gemini_image.generate_image(
                    prompt=task["prompt"],
                    source_image=src_bytes,
                    source_mime=mime,
                    model=task["model_id"],
                    user_id=task.get("_user_id"),
                    project_id=task_id,
                    service="image_translate",
                )

                # 3. 写临时文件 + 上传 TOS
                dst_ext = self._ext_from_mime(out_mime) or ".png"
                dst_key = self._build_dst_key(task, idx, dst_ext)
                dst_fd, dst_path = tempfile.mkstemp(suffix=dst_ext, prefix="it_dst_")
                os.close(dst_fd)
                with open(dst_path, "wb") as f:
                    f.write(out_bytes)
                tos_clients.upload_file(dst_path, dst_key)

                item["status"] = "done"
                item["dst_tos_key"] = dst_key
                item["error"] = ""
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            except gemini_image.GeminiImageError as e:
                item["status"] = "failed"
                item["error"] = str(e)
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            except gemini_image.GeminiImageRetryable as e:
                # 任务级熔断：先记一次速率事件，超阈值立刻终止整任务
                if self._record_rate_limit_hit():
                    item["status"] = "failed"
                    item["error"] = f"已熔断（上游持续限流）：{e}"
                    _update_progress(task)
                    store.update(task_id, items=task["items"], progress=task["progress"])
                    self._emit_item(task_id, item)
                    self._emit_progress(task_id, task["progress"])
                    raise _CircuitOpen(str(e)) from e
                if attempts < _MAX_ATTEMPTS:
                    _sleep(_BACKOFF_BASE * (2 ** (attempts - 1)))
                    continue
                item["status"] = "failed"
                item["error"] = f"重试 {attempts} 次仍失败：{e}"
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            except Exception as e:
                if attempts < _MAX_ATTEMPTS:
                    _sleep(_BACKOFF_BASE * (2 ** (attempts - 1)))
                    continue
                item["status"] = "failed"
                item["error"] = f"未知错误：{e}"
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            finally:
                for p in (src_path, dst_path):
                    if p and os.path.exists(p):
                        try:
                            os.unlink(p)
                        except OSError:
                            pass

    @staticmethod
    def _ext_from_key(key: str) -> str:
        lower = key.lower()
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            if lower.endswith(ext):
                return ext
        return ""

    @staticmethod
    def _ext_from_mime(mime: str) -> str:
        mime = (mime or "").lower()
        if mime == "image/jpeg":
            return ".jpg"
        if mime == "image/png":
            return ".png"
        if mime == "image/webp":
            return ".webp"
        return ""

    @staticmethod
    def _guess_mime(key: str) -> str:
        lower = key.lower()
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            return "image/jpeg"
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith(".webp"):
            return "image/webp"
        return "application/octet-stream"

    @staticmethod
    def _build_dst_key(task: dict, idx: int, ext: str) -> str:
        uid = task.get("_user_id") or 0
        return f"artifacts/image_translate/{uid}/{task['id']}/out_{idx}{ext}"

    def _emit_item(self, task_id: str, item: dict) -> None:
        self.bus.publish(Event(
            type="image_translate:item_updated",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "idx": item["idx"],
                "status": item["status"],
                "attempts": item["attempts"],
                "error": item["error"],
                "dst_tos_key": item.get("dst_tos_key") or "",
            },
        ))

    def _emit_progress(self, task_id: str, progress: dict) -> None:
        self.bus.publish(Event(
            type="image_translate:progress",
            task_id=task_id,
            payload={"task_id": task_id, **progress},
        ))
