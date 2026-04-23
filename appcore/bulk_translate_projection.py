from __future__ import annotations

import json
import logging

from appcore import medias
from appcore.bulk_translate_runtime import compute_progress, refresh_task_from_children
from appcore.db import query


log = logging.getLogger(__name__)
_RETRYABLE_ITEM_STATUSES = {"failed", "error", "interrupted"}
_WAITING_ITEM_STATUSES = {"awaiting_voice"}
_PARENT_RESUMABLE_STATUSES = {"failed", "error", "paused", "interrupted"}

_KIND_LABELS = {
    "copy": "文案翻译",
    "copywriting": "文案翻译",
    "detail": "商品详情图翻译",
    "detail_images": "商品详情图翻译",
    "cover": "视频封面翻译",
    "video_covers": "视频封面翻译",
    "video": "视频翻译",
    "videos": "视频翻译",
}

_CONTENT_TYPE_LABELS = {
    "copywriting": "文案翻译",
    "detail_images": "商品详情图翻译",
    "video_covers": "视频封面翻译",
    "videos": "视频翻译",
    "copy": "文案翻译",
    "detail": "商品详情图翻译",
    "cover": "视频封面翻译",
    "video": "视频翻译",
}


def list_product_tasks(user_id: int, product_id: int, *, limit: int = 50) -> list[dict]:
    rows = query(
        """
        SELECT id, status, state_json, created_at
        FROM projects
        WHERE user_id = %s
          AND type = 'bulk_translate'
          AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (user_id, limit),
    )
    tasks: list[dict] = []
    for row in rows or []:
        state = _parse_state(row.get("state_json"))
        if int(state.get("product_id") or 0) != int(product_id):
            continue
        try:
            refreshed = refresh_task_from_children(row["id"], user_id=user_id)
        except Exception:
            log.warning("bulk_translate projection refresh failed: %s", row.get("id"), exc_info=True)
            refreshed = None
        if refreshed:
            state = dict(refreshed.get("state") or state)
            row = {
                **row,
                "status": refreshed.get("status") or row.get("status"),
                "created_at": refreshed.get("created_at") or row.get("created_at"),
            }
        tasks.append(_serialize_task(row, state))
    return tasks


def _serialize_task(row: dict, state: dict) -> dict:
    detail_url = f"/tasks/{row['id']}"
    plan = [_serialize_item(item, parent_detail_url=detail_url) for item in (state.get("plan") or [])]
    progress = dict(state.get("progress") or compute_progress(plan))
    waiting_voice_count = sum(1 for item in plan if item["status"] in _WAITING_ITEM_STATUSES)
    failed_count = sum(1 for item in plan if item["status"] in _RETRYABLE_ITEM_STATUSES)
    status = (row.get("status") or "").strip()
    return {
        "id": row["id"],
        "status": status,
        "status_label": _status_label(status, waiting_voice_count=waiting_voice_count),
        "product_id": int(state.get("product_id") or 0),
        "target_langs": list(state.get("target_langs") or []),
        "target_lang_labels": [medias.get_language_name(lang) for lang in (state.get("target_langs") or [])],
        "content_types": list(state.get("content_types") or []),
        "content_type_labels": [
            _CONTENT_TYPE_LABELS.get(content_type, content_type)
            for content_type in (state.get("content_types") or [])
        ],
        "progress": progress,
        "waiting_voice_count": waiting_voice_count,
        "failed_count": failed_count,
        "detail_url": detail_url,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "video_params_snapshot": dict(state.get("video_params_snapshot") or {}),
        "can_resume": status in _PARENT_RESUMABLE_STATUSES,
        "can_retry_failed": failed_count > 0,
        "items": plan,
    }


def _serialize_item(raw_item: dict, *, parent_detail_url: str) -> dict:
    item = dict(raw_item or {})
    kind = (item.get("kind") or "").strip()
    lang = (item.get("lang") or "").strip().lower()
    status = (item.get("status") or "pending").strip()
    child_task_id = item.get("child_task_id") or item.get("sub_task_id")
    child_task_type = item.get("child_task_type") or ""
    ref = dict(item.get("ref") or {})
    manual_step = "voice_selection" if status == "awaiting_voice" and child_task_type == "multi_translate" else None
    return {
        "idx": int(item.get("idx") or 0),
        "kind": kind,
        "kind_label": _KIND_LABELS.get(kind, kind or "翻译任务"),
        "lang": lang,
        "lang_label": medias.get_language_name(lang) if lang else "",
        "status": status,
        "status_label": _status_label(status, waiting_voice_count=1 if manual_step else 0),
        "error": (item.get("error") or "").strip(),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "dispatch_after_seconds": int(item.get("dispatch_after_seconds") or 0),
        "result_synced": bool(item.get("result_synced")),
        "child_task_id": child_task_id,
        "child_task_type": child_task_type or None,
        "detail_url": _child_detail_url(child_task_type, child_task_id),
        "parent_detail_url": parent_detail_url,
        "retryable": status in _RETRYABLE_ITEM_STATUSES,
        "manual_step": manual_step,
        "summary": _item_summary(kind, ref),
        "ref": ref,
    }


def _item_summary(kind: str, ref: dict) -> str:
    if kind in {"copy", "copywriting"}:
        return f"英文文案 #{int(ref.get('source_copy_id') or 0)}"
    if kind in {"detail", "detail_images"}:
        count = len(ref.get("source_detail_ids") or [])
        return f"{count} 张英文详情图"
    if kind in {"cover", "video_covers"}:
        count = len(ref.get("source_raw_ids") or ref.get("source_cover_ids") or [])
        return f"{count} 条视频封面"
    if kind in {"video", "videos"}:
        source_raw_id = int(ref.get("source_raw_id") or 0)
        return f"原始视频 #{source_raw_id}"
    return "翻译子任务"


def _child_detail_url(task_type: str | None, child_task_id: str | None) -> str | None:
    if not task_type or not child_task_id:
        return None
    if task_type == "multi_translate":
        return f"/multi-translate/{child_task_id}"
    if task_type == "image_translate":
        return f"/image-translate/{child_task_id}"
    if task_type == "copywriting_translate":
        return None
    return None


def _parse_state(raw_state: dict | str | None) -> dict:
    if isinstance(raw_state, dict):
        return dict(raw_state)
    if isinstance(raw_state, str) and raw_state.strip():
        try:
            parsed = json.loads(raw_state)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return {}


def _status_label(status: str, *, waiting_voice_count: int = 0) -> str:
    if status == "waiting_manual" and waiting_voice_count:
        return "等待选声音"
    return {
        "planning": "待启动",
        "pending": "待执行",
        "dispatching": "排队创建中",
        "running": "执行中",
        "syncing_result": "同步结果中",
        "awaiting_voice": "等待选声音",
        "waiting_manual": "等待人工处理",
        "failed": "失败",
        "error": "失败",
        "interrupted": "已中断",
        "paused": "已暂停",
        "done": "已完成",
        "skipped": "已跳过",
        "cancelled": "已取消",
    }.get(status, status or "未知状态")
