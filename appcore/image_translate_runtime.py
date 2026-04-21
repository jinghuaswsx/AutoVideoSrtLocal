"""图片翻译后台 runtime：串行处理 items，自动重试 3 次，失败不中断。"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from collections import deque
from datetime import datetime

from appcore import gemini_image, medias, tos_clients
from appcore.events import Event, EventBus
from web import store

logger = logging.getLogger(__name__)


class _WatchdogTakeover(Exception):
    """slot 已被新 runner 抢占，旧 runtime 线程应退出（Task 2 完整实现）。"""


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
        try:
            self._finalize_auto_apply(task)
        except Exception as exc:
            ctx = dict(task.get("medias_context") or {})
            if ctx:
                ctx["apply_status"] = "apply_error"
                ctx["last_apply_error"] = str(exc)
                task["medias_context"] = ctx
        _update_progress(task)
        store.update(
            task_id,
            status=task["status"],
            steps=task["steps"],
            progress=task["progress"],
            items=task["items"],
            medias_context=task.get("medias_context") or {},
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
                self._download_source_image(task, item, src_path)
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
                    service="image_translate.generate",
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

    def _download_source_image(self, task: dict, item: dict, local_path: str) -> str:
        source_bucket = (
            item.get("source_bucket")
            or (task.get("medias_context") or {}).get("source_bucket")
            or "upload"
        ).strip().lower()
        if source_bucket == "media":
            return tos_clients.download_media_file(item["src_tos_key"], local_path)
        return tos_clients.download_file(item["src_tos_key"], local_path)

    def _finalize_auto_apply(self, task: dict) -> None:
        ctx = task.get("medias_context") or {}
        if not ctx.get("auto_apply_detail_images"):
            return
        apply_translated_detail_images_from_task(
            task, allow_partial=False, user_id=self.user_id,
        )

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


def apply_translated_detail_images_from_task(
    task: dict,
    *,
    allow_partial: bool,
    user_id: int | None = None,
) -> dict:
    """把一个 image_translate 任务的已成功项回填到 medias 详情图。

    - allow_partial=False：任一 item 非 done → apply_status='skipped_failed'，不回填
    - allow_partial=True：忽略 failed，只回填 done；若仍有 pending/running → 拒绝（RuntimeError）

    仅替换 origin_type='image_translate' 的旧条目，保留手动上传/链接下载的图。

    返回 {applied_ids, skipped_failed_indices, apply_status}
    """
    ctx = dict(task.get("medias_context") or {})
    if not ctx:
        raise ValueError("任务缺少 medias_context，无法回填")
    product_id = int(ctx.get("product_id") or 0)
    target_lang = (ctx.get("target_lang") or "").strip()
    if not product_id or not target_lang:
        raise ValueError("medias_context 缺少 product_id 或 target_lang")

    items = task.get("items") or []
    done_items: list[dict] = []
    failed_items: list[dict] = []
    pending_items: list[dict] = []
    for it in items:
        st = (it.get("status") or "").strip()
        if st == "done":
            done_items.append(it)
        elif st == "failed":
            failed_items.append(it)
        else:
            pending_items.append(it)

    if pending_items:
        raise RuntimeError(
            f"任务还有 {len(pending_items)} 项未完成（pending/running），请先让任务跑完"
        )

    if not allow_partial and failed_items:
        ctx["apply_status"] = "skipped_failed"
        task["medias_context"] = ctx
        store.update(task["id"], medias_context=ctx)
        return {
            "applied_ids": [],
            "skipped_failed_indices": [it.get("idx") for it in failed_items],
            "apply_status": "skipped_failed",
        }

    if not done_items:
        raise RuntimeError("没有成功的翻译结果可回填")

    created_images: list[dict] = []
    resolved_uid = int(task.get("_user_id") or user_id or 0)
    for item in done_items:
        dst_key = (item.get("dst_tos_key") or "").strip()
        if not dst_key:
            raise ValueError(f"任务项 {item.get('idx')} 缺少输出文件")
        ext = ImageTranslateRuntime._ext_from_key(dst_key) or ".png"
        download_fd, download_path = tempfile.mkstemp(suffix=ext, prefix="it_apply_")
        os.close(download_fd)
        try:
            tos_clients.download_file(dst_key, download_path)
            with open(download_path, "rb") as f:
                data = f.read()
        finally:
            if os.path.exists(download_path):
                try:
                    os.unlink(download_path)
                except OSError:
                    pass

        base_name = os.path.splitext(
            os.path.basename(item.get("filename") or f"detail_{item.get('idx') or 0}")
        )[0]
        filename = f"{base_name or 'detail'}{ext}"
        object_key = tos_clients.build_media_object_key(resolved_uid, product_id, filename)
        content_type = ImageTranslateRuntime._guess_mime(dst_key)
        tos_clients.upload_media_object(object_key, data, content_type=content_type)
        created_images.append({
            "object_key": object_key,
            "content_type": content_type,
            "file_size": len(data),
            "origin_type": "image_translate",
            "source_detail_image_id": item.get("source_detail_image_id"),
            "image_translate_task_id": task["id"],
        })

    created_ids = medias.replace_translated_detail_images_for_lang(
        product_id, target_lang, created_images,
    )

    failed_indices = [it.get("idx") for it in failed_items]
    apply_status = "applied_partial" if failed_indices else "applied"

    ctx["apply_status"] = apply_status
    ctx["applied_at"] = datetime.now().isoformat()
    ctx["applied_detail_image_ids"] = created_ids
    ctx["skipped_failed_indices"] = failed_indices
    ctx["last_apply_error"] = ""
    task["medias_context"] = ctx
    store.update(task["id"], medias_context=ctx)

    return {
        "applied_ids": created_ids,
        "skipped_failed_indices": failed_indices,
        "apply_status": apply_status,
    }
