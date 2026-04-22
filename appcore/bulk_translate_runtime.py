"""bulk_translate 父任务状态机 + 调度器 + 人工恢复。

核心铁律(开发全程必守):
  1. 绝不自动恢复任何 bulk_translate 任务 — 进程启动不扫描、不对账、不触发执行
  2. 子任务失败 → 父任务立即停(error),绝不跳过继续跑
  3. 所有恢复/重跑必须由用户按按钮触发(resume / retry_failed / retry_item)

模块划分:
  Task 16 (本文件): create / get / start 状态转换
  Task 17: run_scheduler 主循环 + 失败即停
  Task 18: 子任务派发器 _dispatch_sub_task + _translation_exists_for_item
  Task 19: SocketIO 事件推送(EVT_BT_PROGRESS / EVT_BT_DONE)
  Task 20: 人工恢复三路径 resume_task / retry_failed_items / retry_item

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 4 章
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from appcore import local_media_storage, medias
from appcore.bulk_translate_estimator import (
    COST_PER_1K_TOKENS_CNY,
    COST_PER_IMAGE_CNY,
    COST_PER_VIDEO_MINUTE_CNY,
    estimate as do_estimate,
)
from appcore.bulk_translate_plan import generate_plan
from appcore.db import execute, query, query_one
from appcore.events import EVT_BT_DONE, EVT_BT_PROGRESS, Event, EventBus
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS
from config import OUTPUT_DIR

log = logging.getLogger(__name__)


# ============================================================
# 创建父任务(planning 状态)
# ============================================================
def create_bulk_translate_task(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
    video_params: dict,
    initiator: dict,
    raw_source_ids: list[int] | None = None,
) -> str:
    """创建父任务,生成 plan + 费用预估 + 审计初始记录。

    初始 status='planning',尚未启动调度器——等用户二次确认后调 start_task。

    返回 task_id(UUID)。
    """
    plan = generate_plan(
        user_id,
        product_id,
        target_langs,
        content_types,
        force_retranslate,
        raw_source_ids=raw_source_ids,
    )
    cost = do_estimate(user_id, product_id, target_langs,
                         content_types, force_retranslate)

    state = {
        "product_id": product_id,
        "source_lang": "en",
        "target_langs": target_langs,
        "content_types": content_types,
        "force_retranslate": force_retranslate,
        "raw_source_ids": raw_source_ids or [],
        "video_params_snapshot": video_params or {},
        "initiator": initiator,
        "plan": plan,
        "progress": compute_progress(plan),
        "current_idx": 0,
        "cancel_requested": False,
        "audit_events": [_audit(user_id, "create", {
            "target_langs": target_langs,
            "content_types": content_types,
            "force": force_retranslate,
            "estimated_cost_cny": cost["estimated_cost_cny"],
        })],
        "cost_tracking": {
            "estimate": {
                "copy_tokens": cost["copy_tokens"],
                "image_count": cost["image_count"],
                "video_minutes": cost["video_minutes"],
                "estimated_cost_cny": cost["estimated_cost_cny"],
            },
            "actual": {
                "copy_tokens_used": 0,
                "image_processed": 0,
                "video_minutes_processed": 0,
                "actual_cost_cny": 0.0,
            },
        },
    }

    task_id = str(uuid.uuid4())
    execute(
        """
        INSERT INTO projects (id, user_id, type, status, state_json)
        VALUES (%s, %s, 'bulk_translate', 'planning', %s)
        """,
        (task_id, user_id,
         json.dumps(state, ensure_ascii=False, default=str)),
    )
    log.info(
        "bulk_translate task created task_id=%s product=%s langs=%s",
        task_id, product_id, target_langs,
    )
    return task_id


# ============================================================
# 读取父任务(全部状态一次性读出)
# ============================================================
def get_task(task_id: str) -> dict | None:
    """返回 { id, user_id, status, state, created_at, updated_at } 或 None。

    调度器/API 层都用这个统一入口。
    """
    row = query_one(
        "SELECT id, user_id, status, state_json, created_at "
        "FROM projects WHERE id = %s AND type = 'bulk_translate'",
        (task_id,),
    )
    if not row:
        return None
    raw = row["state_json"]
    state = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "state": state,
        "created_at": row["created_at"],
        "updated_at": None,
    }


# ============================================================
# 启动父任务(planning → running)
# ============================================================
def start_task(task_id: str, user_id: int) -> None:
    """把 planning 状态改为 running,追加 audit_events。

    注意: 本函数只做状态转换,不启动调度器。
    调度器由路由层在请求返回后 eventlet.spawn(run_scheduler, task_id)。
    """
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    if task["status"] != "planning":
        raise ValueError(
            f"Cannot start task in status={task['status']}, must be 'planning'"
        )
    state = task["state"]
    _append_audit(state, user_id, "start")
    _save_state(task_id, state, status="running")


# ============================================================
# 内部工具
# ============================================================
def compute_progress(plan: list[dict]) -> dict:
    """基于 plan 项 status 聚合进度。注意状态值到统计 key 的映射:
    pending / running / done / skipped / failed(plan 项的 'error' 在进度里算 'failed')。
    """
    progress = {"total": len(plan), "done": 0, "running": 0,
                "failed": 0, "skipped": 0, "pending": 0}
    for item in plan:
        st = item["status"]
        if st == "pending":
            progress["pending"] += 1
        elif st == "running":
            progress["running"] += 1
        elif st == "done":
            progress["done"] += 1
        elif st == "skipped":
            progress["skipped"] += 1
        elif st == "error":
            progress["failed"] += 1
    return progress


def _save_state(task_id: str, state: dict, status: str | None = None) -> None:
    """把 state 回写 projects.state_json,可选同时改 status。"""
    state["progress"] = compute_progress(state["plan"])
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if status is not None:
        execute(
            "UPDATE projects SET state_json=%s, status=%s WHERE id=%s",
            (payload, status, task_id),
        )
    else:
        execute(
            "UPDATE projects SET state_json=%s WHERE id=%s",
            (payload, task_id),
        )


def _audit(user_id: int, action: str, detail: dict | None = None) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "action": action,
        "detail": detail or {},
    }


def _append_audit(state: dict, user_id: int, action: str,
                   detail: dict | None = None) -> None:
    state.setdefault("audit_events", []).append(_audit(user_id, action, detail))


# ============================================================
# Task 17-18-19:调度器 + 派发器 + SocketIO 事件推送
# ============================================================

class SubTaskResult:
    """子任务执行结果的统一封装。派发器里各 kind 的函数返回此类实例。"""
    __slots__ = ("sub_task_id", "status", "error",
                 "tokens_used", "image_count", "video_minutes")

    def __init__(self, sub_task_id=None, status="error", error=None,
                 tokens_used=0, image_count=0, video_minutes=0.0):
        self.sub_task_id = sub_task_id
        self.status = status
        self.error = error
        self.tokens_used = tokens_used
        self.image_count = image_count
        self.video_minutes = video_minutes


def run_scheduler(task_id: str, bus: EventBus | None = None) -> None:
    """父任务主调度循环。串行派发子任务,失败立即停。

    关键铁律:
      - 子任务 error → 父任务 status=error,调度循环立即退出
      - 视频 plan 项 × 非 de/fr 目标 → 静默 skipped(不是 error)
      - 已存在译本且未强制重翻 → 静默 skipped

    bus: 可选。若提供,会在状态变化时 publish EVT_BT_PROGRESS/EVT_BT_DONE。
    """
    while True:
        task = get_task(task_id)
        if not task:
            log.warning("run_scheduler: task %s not found, exiting", task_id)
            return
        state = task["state"]
        status = task["status"]

        # 用户点取消 → 本轮退出
        if state.get("cancel_requested"):
            _save_state(task_id, state, status="cancelled")
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "cancelled")
            return

        # status 已不在 running → 退出(被 pause 了,或外部改过)
        if status != "running":
            return

        # 找下一个可执行项
        next_item = _find_next_pending(state["plan"])
        if next_item is None:
            _save_state(task_id, state, status="done")
            _emit(bus, EVT_BT_DONE, task_id, state, "done")
            return

        # 视频 × 非 de/fr → 跳过
        if (next_item["kind"] == "video"
                and next_item["lang"] not in VIDEO_SUPPORTED_LANGS):
            _mark_item_skipped(next_item, "video_lang_not_supported")
            _save_state(task_id, state)
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "running")
            continue

        # 已有译本且未 force → 跳过
        if (not state.get("force_retranslate")
                and _translation_exists_for_item(next_item)):
            _mark_item_skipped(next_item, "already_exists")
            _save_state(task_id, state)
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "running")
            continue

        # 派发并同步等待
        _mark_item_running(next_item)
        state["current_idx"] = next_item["idx"]
        _save_state(task_id, state)
        _emit(bus, EVT_BT_PROGRESS, task_id, state, "running")

        try:
            result = _dispatch_sub_task(task_id, next_item, state, bus=bus)
        except Exception as e:
            log.exception("dispatch failed task=%s idx=%d",
                           task_id, next_item["idx"])
            result = SubTaskResult(sub_task_id=None, status="error",
                                    error=str(e))

        if result.status == "done":
            _mark_item_done(next_item, result)
            _roll_up_cost(state, result)
            _save_state(task_id, state)
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "running")
            continue

        # --- 铁律 2:失败即停,绝不跳过继续跑 ---
        _mark_item_error(next_item, result)
        _save_state(task_id, state, status="error")
        _emit(bus, EVT_BT_PROGRESS, task_id, state, "error")
        return


# ------------------------------------------------------------
# plan 项状态流转
# ------------------------------------------------------------
def _find_next_pending(plan: list[dict]) -> dict | None:
    for item in plan:
        if item["status"] == "pending":
            return item
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_item_running(item: dict) -> None:
    item["status"] = "running"
    item["started_at"] = _now_iso()


def _mark_item_done(item: dict, result: SubTaskResult) -> None:
    item["status"] = "done"
    item["sub_task_id"] = result.sub_task_id
    item["finished_at"] = _now_iso()


def _mark_item_error(item: dict, result: SubTaskResult) -> None:
    item["status"] = "error"
    item["error"] = result.error
    item["sub_task_id"] = result.sub_task_id
    item["finished_at"] = _now_iso()


def _mark_item_skipped(item: dict, reason: str) -> None:
    item["status"] = "skipped"
    item["error"] = reason
    item["finished_at"] = _now_iso()


def _roll_up_cost(state: dict, result: SubTaskResult) -> None:
    """把子任务消耗累积到父任务 cost_tracking.actual。"""
    actual = state["cost_tracking"]["actual"]
    actual["copy_tokens_used"] += result.tokens_used
    actual["image_processed"] += result.image_count
    actual["video_minutes_processed"] += result.video_minutes
    total = (
        (actual["copy_tokens_used"] / 1000.0) * COST_PER_1K_TOKENS_CNY
        + actual["image_processed"] * COST_PER_IMAGE_CNY
        + actual["video_minutes_processed"] * COST_PER_VIDEO_MINUTE_CNY
    )
    actual["actual_cost_cny"] = round(total, 2)


# ============================================================
# Task 18:子任务派发(3 种 kind)
# ============================================================
def _dispatch_sub_task(parent_id: str, item: dict, parent_state: dict,
                        bus: EventBus | None = None) -> SubTaskResult:
    """根据 item.kind 创建并同步执行对应的真实子任务。"""
    kind = item["kind"]
    lang = item["lang"]
    product_id = parent_state["product_id"]
    user_id = parent_state["initiator"]["user_id"]

    if kind == "copy":
        return _dispatch_copy(parent_id, user_id, product_id, lang, item, bus)
    if kind == "detail":
        return _dispatch_image_batch(parent_id, user_id, product_id, lang,
                                       item, preset="detail")
    if kind == "cover":
        return _dispatch_image_batch(parent_id, user_id, product_id, lang,
                                       item, preset="cover")
    if kind == "video":
        return _dispatch_video(parent_id, user_id, product_id, lang,
                                 item, parent_state)
    raise ValueError(f"Unknown plan kind: {kind}")


def _dispatch_copy(parent_id, user_id, product_id, lang, item, bus):
    """派发 copywriting_translate 子任务,同步阻塞到完成。"""
    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner

    sub_id = str(uuid.uuid4())
    state = {
        "product_id": product_id,
        "source_lang": "en",
        "target_lang": lang,
        "source_copy_id": item["ref"]["source_copy_id"],
        "parent_task_id": parent_id,
    }
    execute(
        """
        INSERT INTO projects (id, user_id, type, status, state_json)
        VALUES (%s, %s, 'copywriting_translate', 'queued', %s)
        """,
        (sub_id, user_id, json.dumps(state, ensure_ascii=False)),
    )

    try:
        CopywritingTranslateRunner(sub_id, bus=bus).start()
    except Exception as e:
        return SubTaskResult(sub_id, status="error", error=str(e))

    sub = query_one(
        "SELECT status, state_json FROM projects WHERE id=%s",
        (sub_id,),
    )
    sub_state = (sub["state_json"] if isinstance(sub["state_json"], dict)
                 else json.loads(sub["state_json"] or "{}"))
    return SubTaskResult(
        sub_id,
        status=sub["status"],
        error=sub_state.get("last_error"),
        tokens_used=int(sub_state.get("tokens_used") or 0),
    )


def _dispatch_image_batch(parent_id, user_id, product_id, lang, item, preset):
    """派发 image_translate 批量子任务。复用现有 image_translate type。

    注意: image_translate 的实际 runner 对输入格式有自己约定。
    本期我们保持简化——创建子任务行(queued)并让后续部署阶段对接
    image_translate 现有 runner。若现有 runner 接口不兼容本 plan 的
    source_ids 形式,下一轮迭代再适配。
    """
    from appcore.image_translate_runtime import ImageTranslateRuntime

    source_ids = (item["ref"].get("source_detail_ids")
                  or item["ref"].get("source_cover_ids") or [])
    sub_id = str(uuid.uuid4())
    state = {
        "product_id": product_id,
        "target_language": lang,
        "source_ids": source_ids,
        "preset": preset,
        "parent_task_id": parent_id,
    }
    execute(
        """
        INSERT INTO projects (id, user_id, type, status, state_json)
        VALUES (%s, %s, 'image_translate', 'queued', %s)
        """,
        (sub_id, user_id, json.dumps(state, ensure_ascii=False)),
    )

    try:
        ImageTranslateRuntime(sub_id).start()
    except Exception as e:
        return SubTaskResult(sub_id, status="error", error=str(e))

    sub = query_one(
        "SELECT status, state_json FROM projects WHERE id=%s",
        (sub_id,),
    )
    sub_state = (sub["state_json"] if isinstance(sub["state_json"], dict)
                 else json.loads(sub["state_json"] or "{}"))
    return SubTaskResult(
        sub_id,
        status=sub["status"],
        error=sub_state.get("last_error"),
        image_count=len(source_ids),
    )


def _dispatch_video(parent_id, user_id, product_id, lang, item, parent_state):
    """派发 translate_lab 视频子任务。仅 de/fr(调度层已保证)。"""
    if lang not in VIDEO_SUPPORTED_LANGS:
        return SubTaskResult(
            status="error",
            error=f"unsupported video target lang: {lang}",
        )
    raw_id = int(item["ref"]["source_raw_id"])
    row = medias.get_raw_source(raw_id)
    if not row:
        return SubTaskResult(
            status="error",
            error=f"raw source {raw_id} missing",
        )

    local_video = ""
    sub_id = None
    try:
        local_video = _download_media_to_tmp(
            row["video_object_key"],
            suffix=_suffix_from_key(row["video_object_key"], default=".mp4"),
        )
        video_result = _translate_video_to_media_key(
            local_video,
            target_lang=lang,
            product_id=row["product_id"],
            user_id=row["user_id"],
            parent_state=parent_state,
        )
        if isinstance(video_result, tuple):
            sub_id, video_out_key = video_result
        else:
            video_out_key = video_result

        cover_out_key = _translate_cover_to_media_key(
            source_cover_key=row["cover_object_key"],
            target_lang=lang,
            product_id=row["product_id"],
            user_id=row["user_id"],
        )
        new_item_id = medias.create_item(
            product_id=row["product_id"],
            user_id=row["user_id"],
            filename=Path(video_out_key).name,
            object_key=video_out_key,
            cover_object_key=cover_out_key,
            duration_seconds=row.get("duration_seconds"),
            file_size=None,
            lang=lang,
        )
        execute(
            "UPDATE media_items SET source_raw_id=%s WHERE id=%s",
            (raw_id, new_item_id),
        )
        dur_sec = float(row.get("duration_seconds") or 0.0)
        return SubTaskResult(
            sub_id,
            status="done",
            video_minutes=dur_sec / 60.0,
        )
    except Exception as e:
        return SubTaskResult(sub_id, status="error", error=str(e))
    finally:
        if local_video and os.path.exists(local_video):
            try:
                os.unlink(local_video)
            except OSError:
                pass


def _download_media_to_tmp(object_key: str, suffix: str = ".bin") -> str:
    from appcore import tos_clients

    source_name = Path(object_key or "").name
    prefix = f"bt_{Path(source_name).stem[:32] or 'raw'}_"
    fd, local_path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(fd)
    if local_media_storage.exists(object_key):
        return local_media_storage.download_to(object_key, local_path)
    return tos_clients.download_media_file(object_key, local_path)


def _translate_video_to_media_key(local_video, target_lang, product_id, user_id, parent_state):
    from appcore import task_state, tos_clients
    from appcore.runtime_v2 import PipelineRunnerV2

    sub_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, sub_id)
    os.makedirs(task_dir, exist_ok=True)
    video_params = dict(parent_state.get("video_params_snapshot") or {})
    task_state.create_translate_lab(
        sub_id,
        local_video,
        task_dir,
        original_filename=Path(local_video).name,
        user_id=user_id,
        source_language="en",
        target_language=target_lang,
        **video_params,
    )
    PipelineRunnerV2(bus=EventBus(), user_id=user_id).start(sub_id)

    task = task_state.get(sub_id) or {}
    result_path = _resolve_translated_video_path(task)
    if not result_path:
        message = task.get("error") or "translate_lab output missing"
        raise RuntimeError(message)

    with open(result_path, "rb") as fh:
        payload = fh.read()
    output_name = f"{target_lang}_{Path(local_video).stem}{Path(result_path).suffix or '.mp4'}"
    object_key = tos_clients.build_media_object_key(user_id, product_id, output_name)
    local_media_storage.write_bytes(object_key, payload)
    return sub_id, object_key


def _translate_cover_to_media_key(source_cover_key, target_lang, product_id, user_id):
    from appcore import image_translate_settings as its
    from appcore import task_state, tos_clients
    from appcore.image_translate_runtime import ImageTranslateRuntime

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    lang_name = medias.get_language_name(target_lang)
    prompt = its.get_prompt("cover", target_lang).replace(
        "{target_language_name}",
        lang_name,
    )
    task_state.create_image_translate(
        task_id,
        task_dir,
        user_id=user_id,
        preset="cover",
        target_language=target_lang,
        target_language_name=lang_name,
        model_id=_default_image_translate_model_id(user_id),
        prompt=prompt,
        items=[{
            "idx": 0,
            "filename": Path(source_cover_key).name or "cover.jpg",
            "src_tos_key": source_cover_key,
            "source_bucket": "media",
        }],
        medias_context={"source_bucket": "media"},
    )
    ImageTranslateRuntime(bus=EventBus(), user_id=user_id).start(task_id)

    task = task_state.get(task_id) or {}
    items = task.get("items") or []
    first = items[0] if items else {}
    if (first.get("status") or "") != "done":
        raise RuntimeError(first.get("error") or task.get("error") or "cover translate failed")
    dst_key = (first.get("dst_tos_key") or "").strip()
    if not dst_key:
        raise RuntimeError("cover translate output missing")

    ext = _suffix_from_key(dst_key, default=_suffix_from_key(source_cover_key, default=".png"))
    fd, local_path = tempfile.mkstemp(suffix=ext, prefix="bt_cover_")
    os.close(fd)
    try:
        if local_media_storage.exists(dst_key):
            local_media_storage.download_to(dst_key, local_path)
        else:
            tos_clients.download_file(dst_key, local_path)
        with open(local_path, "rb") as fh:
            payload = fh.read()
    finally:
        if os.path.exists(local_path):
            try:
                os.unlink(local_path)
            except OSError:
                pass

    filename = f"cover_{target_lang}_{Path(source_cover_key).name or f'{target_lang}{ext}'}"
    object_key = tos_clients.build_media_object_key(user_id, product_id, filename)
    local_media_storage.write_bytes(object_key, payload)
    return object_key


def _default_image_translate_model_id(user_id: int | None) -> str:
    from appcore.api_keys import resolve_extra
    from appcore.gemini_image import IMAGE_MODELS

    try:
        extra = resolve_extra(user_id, "image_translate") or {}
        preferred = (extra.get("default_model_id") or "").strip()
        if preferred:
            return preferred
    except Exception:
        pass
    if IMAGE_MODELS:
        return IMAGE_MODELS[0][0]
    return "gemini-3.1-flash-image-preview"


def _resolve_translated_video_path(task: dict) -> str:
    candidates = [
        ((task.get("compose_result") or {}).get("hard_video") or ""),
        ((task.get("compose_result") or {}).get("soft_video") or ""),
        ((task.get("result") or {}).get("hard_video") or ""),
        ((task.get("result") or {}).get("soft_video") or ""),
        ((task.get("preview_files") or {}).get("hard_video") or ""),
        ((task.get("preview_files") or {}).get("soft_video") or ""),
        (task.get("final_video") or ""),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _suffix_from_key(key: str, default: str = "") -> str:
    suffix = Path(key or "").suffix
    return suffix or default


def _image_content_type_from_key(key: str) -> str:
    lower = (key or "").lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"


# ============================================================
# Task 18:已存在译本查询
# ============================================================
def _translation_exists_for_item(item: dict) -> bool:
    """判断该 plan 项的目标素材是否已存在译本。"""
    kind = item["kind"]
    lang = item["lang"]
    ref = item["ref"]

    if kind == "copy":
        return _exists_one(
            "media_copywritings",
            source_ref_id=ref["source_copy_id"], lang=lang,
        )
    if kind == "video":
        return _exists_one(
            "media_items",
            source_raw_id=ref["source_raw_id"], lang=lang,
        )
    if kind == "detail":
        for src_id in ref.get("source_detail_ids") or []:
            if _exists_one(
                "media_product_detail_images",
                source_ref_id=src_id, lang=lang,
            ):
                return True
        return False
    if kind == "cover":
        for src_id in ref.get("source_cover_ids") or []:
            if _exists_one(
                "media_product_covers",
                source_ref_id=src_id, lang=lang,
            ):
                return True
        return False
    return False


_EXISTS_ALLOWED = {
    "media_copywritings",
    "media_items",
    "media_product_detail_images",
    "media_product_covers",
}

_SOFT_DELETE_TABLES = {"media_items", "media_product_detail_images"}


def _exists_one(
    table: str,
    *,
    source_ref_id: int | None = None,
    source_raw_id: int | None = None,
    lang: str,
) -> bool:
    if table not in _EXISTS_ALLOWED:
        raise ValueError(f"Unsupported table: {table}")
    if (source_ref_id is None) == (source_raw_id is None):
        raise ValueError("exactly one of source_ref_id/source_raw_id is required")
    column = "source_ref_id" if source_ref_id is not None else "source_raw_id"
    source_id = source_ref_id if source_ref_id is not None else source_raw_id
    where_del = " AND deleted_at IS NULL" if table in _SOFT_DELETE_TABLES else ""
    row = query_one(
        f"SELECT 1 AS x FROM {table} "
        f"WHERE {column} = %s AND lang = %s{where_del} LIMIT 1",
        (source_id, lang),
    )
    return row is not None


# ============================================================
# Task 19:SocketIO 事件推送
# ============================================================
def _emit(bus: EventBus | None, event_type: str, task_id: str,
           state: dict, status: str) -> None:
    """给父任务 bus 发一条 progress/done 事件(bus=None 时静默)。"""
    if bus is None:
        return
    try:
        payload = {
            "status": status,
            "progress": state.get("progress"),
            "current_idx": state.get("current_idx"),
            "cost_actual": state.get("cost_tracking", {}).get("actual"),
        }
        bus.publish(Event(type=event_type, task_id=task_id, payload=payload))
    except Exception:
        log.exception("EventBus publish failed task_id=%s", task_id)


# ============================================================
# Task 20:人工恢复三路径 — 绝不自动触发
# ============================================================
def _reconcile_running_items(state: dict) -> None:
    """把所有 running 项标 error(进程可能已丢失)。

    仅在用户按按钮时调用。永远不在进程启动/定时任务里调。
    """
    for item in state["plan"]:
        if item["status"] == "running":
            item["status"] = "error"
            item["error"] = item.get("error") or "Reconciled: process lost"


def pause_task(task_id: str, user_id: int) -> None:
    """用户点"⏸ 暂停"。当前 running 项跑完不再取下一个。
    调度器主循环下一轮见 status != running 就退出。
    """
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    _append_audit(state, user_id, "pause")
    _save_state(task_id, state, status="paused")


def cancel_task(task_id: str, user_id: int) -> None:
    """用户点"取消"。置 cancel_requested=True,调度器下一轮见状态转 cancelled。"""
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    state["cancel_requested"] = True
    _append_audit(state, user_id, "cancel")
    _save_state(task_id, state)


def resume_task(task_id: str, user_id: int) -> None:
    """用户点"▶ 继续执行"。

    流程:
      1. 对账:把 running 项标 error(进程可能已丢失)
      2. 仅恢复 pending 项为可执行(error 项保持 error,不自动重置)
      3. status → running

    调用方负责在本函数返回后 spawn 调度器。
    """
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    _reconcile_running_items(state)
    state["cancel_requested"] = False
    _append_audit(state, user_id, "resume")
    _save_state(task_id, state, status="running")


def retry_failed_items(task_id: str, user_id: int) -> None:
    """用户点"🔁 重跑所有失败项"。

    把所有 status=error 的 plan 项重置为 pending,然后 status=running。
    """
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    _reconcile_running_items(state)
    reset_count = 0
    for item in state["plan"]:
        if item["status"] == "error":
            item["status"] = "pending"
            item["error"] = None
            item["sub_task_id"] = None
            item["started_at"] = None
            item["finished_at"] = None
            reset_count += 1
    state["cancel_requested"] = False
    _append_audit(state, user_id, "retry_failed", {"reset_count": reset_count})
    _save_state(task_id, state, status="running")


def retry_item(task_id: str, idx: int, user_id: int) -> None:
    """单项重跑。支持把 done/error 项重置为 pending。

    父任务若之前是 done,重跑后回到 running 状态。
    """
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    if idx < 0 or idx >= len(state["plan"]):
        raise ValueError(f"Invalid idx={idx}, plan has {len(state['plan'])} items")

    item = state["plan"][idx]
    item["status"] = "pending"
    item["error"] = None
    item["sub_task_id"] = None
    item["started_at"] = None
    item["finished_at"] = None
    state["cancel_requested"] = False
    _append_audit(state, user_id, "retry_item", {"idx": idx})
    _save_state(task_id, state, status="running")
