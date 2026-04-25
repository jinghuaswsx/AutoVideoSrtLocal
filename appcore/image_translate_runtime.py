"""图片翻译后台 runtime：串行处理 items，自动重试 3 次，失败不中断。"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from appcore import gemini_image, llm_client, local_media_storage, medias, object_keys
from appcore.events import Event, EventBus
from web import store

logger = logging.getLogger(__name__)


_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0  # 秒
_BATCH_SIZE = 10  # 并行模式单批最大并发数
_TEXT_DETECT_SCHEMA = {
    "type": "object",
    "properties": {
        "has_text": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["has_text"],
}
_TEXT_DETECT_PROMPT = (
    "判断这张图片里是否存在任何可读文字，包括商品包装、标签、标题、"
    "水印、数字、字母或短语。只要有可读文字就返回 has_text=true；"
    "如果没有可读文字，或只有无法辨认的纹理/装饰，就返回 has_text=false。"
    "reason 用一句简短中文说明判断依据。"
)

# 任务级熔断：当上游持续返回 429/5xx 时，避免把所有 items 都跑完 3 次重试
# 形成 retry 风暴，进而触发宿主机 watchdog 强制重启 VM（2026-04-17 事故）
_RATE_LIMIT_THRESHOLD = 5
_RATE_LIMIT_WINDOW_SEC = 60.0

# APIMART 提交即可能计费：有已保存 task_id 时永远先检查上游结果；
# 只有检查后仍无结果且提交已满 5 分钟，才允许重新提交。
_APIMART_MIN_REGENERATE_AGE_SEC = 300  # 5 minutes


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


def _delete_artifact_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        local_media_storage.delete(key)
    except Exception:
        pass


def _reset_item_processing_state(item: dict) -> None:
    item["status"] = "pending"
    item["attempts"] = 0
    item["error"] = ""
    item["dst_tos_key"] = ""
    item["text_detect_status"] = "pending"
    item["text_detect_has_text"] = None
    item["text_detect_reason"] = ""
    item["text_detect_error"] = ""
    item["result_source"] = ""
    # Keep async provider snapshots across retries. The next run checks the
    # saved upstream task before submitting another paid generation.


def _item_source_is_gif(item: dict) -> bool:
    for value in (
        item.get("src_tos_key"),
        item.get("filename"),
        item.get("dst_tos_key"),
    ):
        if str(value or "").strip().lower().endswith(".gif"):
            return True
    return False


def reset_failed_items_for_retry(task_id: str, user_id: int | None = None) -> int:
    """Reset only failed image items on an existing image_translate task."""
    task = store.get(task_id)
    if not task or task.get("type") != "image_translate":
        raise ValueError(f"image_translate task not found: {task_id}")
    if user_id is not None and str(task.get("_user_id")) != str(user_id):
        raise PermissionError("image_translate task belongs to another user")

    reset_count = 0
    for item in task.get("items") or []:
        if item.get("status") not in {"failed", "error", "interrupted"}:
            continue
        _delete_artifact_object(item.get("dst_tos_key"))
        _reset_item_processing_state(item)
        reset_count += 1

    if reset_count:
        task["status"] = "queued"
        task.setdefault("steps", {})["process"] = "pending"
        task["error"] = ""
        _update_progress(task)
        store.update(
            task_id,
            status=task["status"],
            steps=task.get("steps", {}),
            progress=task["progress"],
            items=task.get("items") or [],
            error=task.get("error") or "",
        )
    return reset_count


class ImageTranslateRuntime:
    def __init__(self, *, bus: EventBus, user_id: int | None = None) -> None:
        self.bus = bus
        self.user_id = user_id
        self._rate_limit_hits: deque[float] = deque()
        self._state_lock = threading.Lock()

    def _record_rate_limit_hit(self) -> bool:
        """记一次可重试错误（429/5xx），返回 True 表示应熔断整任务。"""
        now = time.monotonic()
        cutoff = now - _RATE_LIMIT_WINDOW_SEC
        with self._state_lock:
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

        circuit_msg = ""
        try:
            mode = (task.get("concurrency_mode") or "sequential").strip().lower()
            if mode == "parallel":
                self._run_parallel(task, task_id)
            else:
                self._run_sequential(task, task_id)
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

    def _run_sequential(self, task: dict, task_id: str) -> None:
        items = task.get("items") or []
        for idx in range(len(items)):
            if items[idx]["status"] in {"done", "failed"}:
                continue
            self._process_one(task, task_id, idx)

    def _run_parallel(self, task: dict, task_id: str) -> None:
        items = task.get("items") or []
        pending_idxs = [
            i for i, it in enumerate(items)
            if it["status"] not in {"done", "failed"}
        ]
        for batch_start in range(0, len(pending_idxs), _BATCH_SIZE):
            batch = pending_idxs[batch_start: batch_start + _BATCH_SIZE]
            with ThreadPoolExecutor(max_workers=_BATCH_SIZE) as pool:
                futures = [
                    pool.submit(self._process_one, task, task_id, idx)
                    for idx in batch
                ]
                for fut in as_completed(futures):
                    fut.result()

    def _abort_remaining_items(self, task: dict, task_id: str, reason: str) -> None:
        for it in task["items"]:
            if it["status"] in {"done", "failed"}:
                continue
            it["status"] = "failed"
            it["error"] = f"已熔断（上游持续限流）：{reason}"
            self._emit_item(task_id, it)
        _update_progress(task)
        self._emit_progress(task_id, task["progress"])

    def _detect_source_text(self, task: dict, task_id: str, item: dict, src_path: str) -> bool:
        cached = item.get("text_detect_has_text")
        if isinstance(cached, bool) and item.get("text_detect_status") in {"done", "error"}:
            return cached

        with self._state_lock:
            item["text_detect_status"] = "running"
            item["text_detect_has_text"] = None
            item["text_detect_reason"] = ""
            item["text_detect_error"] = ""
            store.update(task_id, items=task["items"], progress=task["progress"])
        self._emit_item(task_id, item)

        try:
            result = llm_client.invoke_generate(
                "image_translate.detect",
                prompt=_TEXT_DETECT_PROMPT,
                media=[src_path],
                user_id=task.get("_user_id"),
                project_id=task_id,
                response_schema=_TEXT_DETECT_SCHEMA,
                temperature=0,
                max_output_tokens=128,
                billing_extra={
                    "operation": "image_text_detect",
                    "item_idx": item.get("idx"),
                    "filename": item.get("filename") or "",
                    "source_key": item.get("src_tos_key") or "",
                    "source_bucket": item.get("source_bucket") or "",
                },
            )
            has_text, reason = _parse_text_detection_result(result)
            status = "done"
            error = ""
        except Exception as exc:
            # 检测失败时保守按“有文字”处理，避免误跳过需要翻译的图片。
            has_text = True
            reason = "文字检测失败，已按有文字处理"
            status = "error"
            error = str(exc)

        with self._state_lock:
            item["text_detect_status"] = status
            item["text_detect_has_text"] = has_text
            item["text_detect_reason"] = reason
            item["text_detect_error"] = error
            store.update(task_id, items=task["items"], progress=task["progress"])
        self._emit_item(task_id, item)
        return has_text

    def _mark_gif_passthrough(self, task: dict, task_id: str, item: dict) -> None:
        with self._state_lock:
            item["text_detect_status"] = "done"
            item["text_detect_has_text"] = False
            item["text_detect_reason"] = "GIF 动图跳过文字检测与图片翻译"
            item["text_detect_error"] = ""
            store.update(task_id, items=task["items"], progress=task["progress"])
        self._emit_item(task_id, item)

    def _process_one(self, task: dict, task_id: str, idx: int) -> None:
        item = task["items"][idx]
        with self._state_lock:
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
                mime = self._guess_mime(
                    item["src_tos_key"],
                    filename=item.get("filename") or "",
                    data=src_bytes,
                )

                # 2. 先判断图片是否有文字；无文字时直接复制源图，保持一图一结果。
                if _item_source_is_gif(item) or mime == "image/gif":
                    self._mark_gif_passthrough(task, task_id, item)
                    has_text = False
                else:
                    has_text = self._detect_source_text(task, task_id, item, src_path)
                if has_text:
                    out_bytes, out_mime = self._generate_with_apimart_recovery(
                        task, task_id, item, idx, src_bytes, mime,
                    )
                    dst_ext = self._ext_from_mime(out_mime) or ".png"
                    dst_key = self._build_dst_key(task, idx, dst_ext)
                    local_media_storage.write_bytes(dst_key, out_bytes)
                    result_source = "image_translate"
                else:
                    dst_ext = self._ext_from_key(item["src_tos_key"]) or self._ext_from_mime(mime) or ".jpg"
                    dst_key = self._build_dst_key(task, idx, dst_ext)
                    local_media_storage.write_bytes(dst_key, src_bytes)
                    result_source = "copied_source"

                with self._state_lock:
                    item["status"] = "done"
                    item["dst_tos_key"] = dst_key
                    item["result_source"] = result_source
                    item["error"] = ""
                    _update_progress(task)
                    store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            except gemini_image.GeminiImageError as e:
                with self._state_lock:
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
                    with self._state_lock:
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
                with self._state_lock:
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
                with self._state_lock:
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

    def _generate_with_apimart_recovery(
        self,
        task: dict,
        task_id: str,
        item: dict,
        idx: int,
        src_bytes: bytes,
        mime: str,
    ) -> tuple[bytes, str]:
        """调用 gemini_image.generate_image；对异步 provider（目前只有 APIMART）先尝试
        复用 item 上已落盘的 provider_task_id。

        字段约定：
        - provider_task_id / provider_task_submitted_at：通用字段，新代码统一写入。
        - apimart_task_id / apimart_submitted_at：旧字段，仅用于读路径兼容在途任务，
          新写入统一走 provider_* 命名。

        逻辑：
        - If the item has a provider_task_id, poll that upstream task first.
        - GeminiImageError means the upstream task failed: clear the snapshot,
          then submit a new generation.
        - GeminiImageRetryable means the upstream task may still finish: only
          submit a new generation after the saved task is at least 5 minutes old.
        - If there is no task snapshot, call generate_image and persist the new
          provider_task_id via on_apimart_submitted.
        """
        channel = (task.get("channel") or "").strip()

        if channel == "apimart":
            existing_task_id = (
                (item.get("provider_task_id") or "").strip()
                or (item.get("apimart_task_id") or "").strip()
            )
            submitted_at = float(
                item.get("provider_task_submitted_at")
                or item.get("apimart_submitted_at")
                or 0.0
            )
            age = time.time() - submitted_at if submitted_at else float("inf")
            if existing_task_id:
                logger.info(
                    "[image_translate] resuming APIMART task %s for %s item %d (age=%.0fs)",
                    existing_task_id, task_id, idx, age,
                )
                try:
                    out_bytes, out_mime, _ = gemini_image.poll_apimart_task(
                        existing_task_id,
                        api_key=gemini_image._resolve_apimart_api_key(),
                        initial_wait=False,
                    )
                    return out_bytes, out_mime
                except gemini_image.GeminiImageError as e:
                    logger.info(
                        "[image_translate] APIMART task %s failed upstream, re-generating: %s",
                        existing_task_id, e,
                    )
                    with self._state_lock:
                        item["provider_task_id"] = ""
                        item["provider_task_submitted_at"] = 0.0
                        item["apimart_task_id"] = ""
                        item["apimart_submitted_at"] = 0.0
                        store.update(task_id, items=task["items"])
                except gemini_image.GeminiImageRetryable as e:
                    if age < _APIMART_MIN_REGENERATE_AGE_SEC:
                        raise
                    logger.info(
                        "[image_translate] APIMART task %s still unavailable after %.0fs; re-generating: %s",
                        existing_task_id, age, e,
                    )
                    with self._state_lock:
                        item["provider_task_id"] = ""
                        item["provider_task_submitted_at"] = 0.0
                        item["apimart_task_id"] = ""
                        item["apimart_submitted_at"] = 0.0
                        store.update(task_id, items=task["items"])

        on_submitted = None
        if channel == "apimart":
            def on_submitted(submitted_task_id: str, _item=item, _task=task, _task_id=task_id) -> None:
                with self._state_lock:
                    _item["provider_task_id"] = submitted_task_id
                    _item["provider_task_submitted_at"] = time.time()
                    # 旧字段清空，避免脏数据让 recovery 误判
                    _item["apimart_task_id"] = ""
                    _item["apimart_submitted_at"] = 0.0
                    store.update(_task_id, items=_task["items"])

        apimart_size, apimart_resolution = gemini_image._resolve_apimart_output_params(src_bytes)
        return gemini_image.generate_image(
            prompt=task["prompt"],
            source_image=src_bytes,
            source_mime=mime,
            model=task["model_id"],
            user_id=task.get("_user_id"),
            project_id=task_id,
            service="image_translate.generate",
            apimart_size=apimart_size,
            apimart_resolution=apimart_resolution,
            on_apimart_submitted=on_submitted,
        )

    def _download_source_image(self, task: dict, item: dict, local_path: str) -> str:
        object_key = item["src_tos_key"]
        if local_media_storage.exists(object_key):
            return local_media_storage.download_to(object_key, local_path)
        source_bucket = (
            item.get("source_bucket")
            or (task.get("medias_context") or {}).get("source_bucket")
            or "upload"
        ).strip().lower()
        raise FileNotFoundError(
            f"local source image not found: {object_key} (source_bucket={source_bucket})"
        )

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
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
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
        if mime == "image/gif":
            return ".gif"
        return ""

    @staticmethod
    def _sniff_mime_bytes(data: bytes | None) -> str:
        raw = data or b""
        if raw.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return "image/webp"
        if raw[:6] in {b"GIF87a", b"GIF89a"}:
            return "image/gif"
        return ""

    @classmethod
    def _guess_mime(cls, key: str, *, filename: str = "", data: bytes | None = None) -> str:
        sniffed = cls._sniff_mime_bytes(data)
        if sniffed:
            return sniffed
        for candidate in (key, filename):
            lower = candidate.lower()
            if lower.endswith(".jpg") or lower.endswith(".jpeg"):
                return "image/jpeg"
            if lower.endswith(".png"):
                return "image/png"
            if lower.endswith(".webp"):
                return "image/webp"
            if lower.endswith(".gif"):
                return "image/gif"
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
                "text_detect_status": item.get("text_detect_status") or "pending",
                "text_detect_has_text": item.get("text_detect_has_text"),
                "text_detect_reason": item.get("text_detect_reason") or "",
                "text_detect_error": item.get("text_detect_error") or "",
                "result_source": item.get("result_source") or "",
            },
        ))

    def _emit_progress(self, task_id: str, progress: dict) -> None:
        self.bus.publish(Event(
            type="image_translate:progress",
            task_id=task_id,
            payload={"task_id": task_id, **progress},
        ))


def _parse_text_detection_result(result: dict) -> tuple[bool, str]:
    data = result.get("json") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return True, "文字检测未返回结构化结果，已按有文字处理"

    raw_has_text = data.get("has_text")
    if isinstance(raw_has_text, bool):
        has_text = raw_has_text
    elif isinstance(raw_has_text, str):
        has_text = raw_has_text.strip().lower() in {"true", "yes", "1", "有", "有文字"}
    else:
        return True, "文字检测结果缺少 has_text，已按有文字处理"

    reason = str(data.get("reason") or "").strip()
    return has_text, reason[:200]


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
    skipped_source_gif_items: list[dict] = []
    for it in items:
        st = (it.get("status") or "").strip()
        if st == "done":
            if _item_source_is_gif(it):
                skipped_source_gif_items.append(it)
            else:
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
        ctx["skipped_source_gif_indices"] = [it.get("idx") for it in skipped_source_gif_items]
        task["medias_context"] = ctx
        store.update(task["id"], medias_context=ctx)
        return {
            "applied_ids": [],
            "skipped_failed_indices": [it.get("idx") for it in failed_items],
            "skipped_source_gif_indices": [it.get("idx") for it in skipped_source_gif_items],
            "apply_status": "skipped_failed",
        }

    if not done_items:
        if skipped_source_gif_items and not failed_items:
            skipped_gif_indices = [it.get("idx") for it in skipped_source_gif_items]
            ctx["apply_status"] = "skipped_source_gif_only"
            ctx["applied_at"] = datetime.now().isoformat()
            ctx["applied_detail_image_ids"] = []
            ctx["skipped_failed_indices"] = []
            ctx["skipped_source_gif_indices"] = skipped_gif_indices
            ctx["last_apply_error"] = ""
            task["medias_context"] = ctx
            store.update(task["id"], medias_context=ctx)
            return {
                "applied_ids": [],
                "skipped_failed_indices": [],
                "skipped_source_gif_indices": skipped_gif_indices,
                "apply_status": "skipped_source_gif_only",
            }
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
            if not local_media_storage.exists(dst_key):
                raise FileNotFoundError(f"local translated image not found: {dst_key}")
            local_media_storage.download_to(dst_key, download_path)
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
        object_key = object_keys.build_media_object_key(resolved_uid, product_id, filename)
        content_type = ImageTranslateRuntime._guess_mime(dst_key)
        local_media_storage.write_bytes(object_key, data)
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
    skipped_gif_indices = [it.get("idx") for it in skipped_source_gif_items]
    apply_status = "applied_partial" if failed_indices else "applied"

    ctx["apply_status"] = apply_status
    ctx["applied_at"] = datetime.now().isoformat()
    ctx["applied_detail_image_ids"] = created_ids
    ctx["skipped_failed_indices"] = failed_indices
    ctx["skipped_source_gif_indices"] = skipped_gif_indices
    ctx["last_apply_error"] = ""
    task["medias_context"] = ctx
    store.update(task["id"], medias_context=ctx)

    return {
        "applied_ids": created_ids,
        "skipped_failed_indices": failed_indices,
        "skipped_source_gif_indices": skipped_gif_indices,
        "apply_status": apply_status,
    }
