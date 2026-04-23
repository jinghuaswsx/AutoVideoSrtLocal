from __future__ import annotations

import json
import logging
from pathlib import Path

from appcore import medias
from appcore.bulk_translate_runtime import compute_progress, sync_task_with_children_once
from appcore.db import query


log = logging.getLogger(__name__)
_RETRYABLE_ITEM_STATUSES = {"failed", "error", "interrupted"}
_WAITING_ITEM_STATUSES = {"awaiting_voice"}
_PARENT_RESUMABLE_STATUSES = {"paused", "interrupted"}
_STUCK_PARENT_STATUSES = {"failed", "error", "interrupted", "paused", "waiting_manual"}
_DONE_PARENT_STATUSES = {"done", "cancelled"}
_ADMIN_GROUP_ORDER = {"stuck": 0, "running": 1, "done": 2}

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


def _list_candidate_rows(user_id: int, *, limit: int = 50) -> list[dict]:
    return query(
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


def list_product_task_ids(user_id: int, product_id: int, *, limit: int = 50) -> list[str]:
    task_ids: list[str] = []
    for row in _list_candidate_rows(user_id, limit=limit) or []:
        state = _parse_state(row.get("state_json"))
        if int(state.get("product_id") or 0) == int(product_id):
            task_ids.append(str(row["id"]))
    return task_ids


def list_product_tasks(user_id: int, product_id: int, *, limit: int = 50) -> list[dict]:
    rows = _list_candidate_rows(user_id, limit=limit)
    tasks: list[dict] = []
    for row in rows or []:
        state = _parse_state(row.get("state_json"))
        if int(state.get("product_id") or 0) != int(product_id):
            continue
        try:
            refreshed = sync_task_with_children_once(row["id"], user_id=user_id)
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


def list_admin_tasks(*, limit: int = 300) -> dict:
    """Return an admin-facing overview for all bulk translation parent tasks."""
    rows = query(
        """
        SELECT p.id, p.user_id, p.status, p.state_json, p.created_at,
               u.username AS username
        FROM projects p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.type = 'bulk_translate'
          AND p.deleted_at IS NULL
        ORDER BY p.created_at DESC
        LIMIT %s
        """,
        (max(1, min(int(limit or 300), 500)),),
    )

    tasks = [_serialize_admin_task(row, _parse_state(row.get("state_json"))) for row in rows or []]
    tasks.sort(key=lambda item: _ADMIN_GROUP_ORDER.get(item["group"], 9))
    stats = {
        "running": sum(1 for item in tasks if item["group"] == "running"),
        "stuck": sum(1 for item in tasks if item["group"] == "stuck"),
        "done": sum(1 for item in tasks if item["group"] == "done"),
        "total": len(tasks),
    }
    return {"stats": stats, "items": tasks}


def _serialize_admin_task(row: dict, state: dict) -> dict:
    detail_url = f"/tasks/{row['id']}?scope=admin"
    raw_plan = list(state.get("plan") or [])
    plan = [_serialize_item(item, parent_detail_url=detail_url) for item in raw_plan]
    progress = _progress_summary(dict(state.get("progress") or compute_progress(raw_plan)))
    waiting_voice_count = sum(1 for item in plan if item["status"] in _WAITING_ITEM_STATUSES)
    failed_count = sum(1 for item in plan if item["status"] in _RETRYABLE_ITEM_STATUSES)
    intervention_count = failed_count + waiting_voice_count
    status = (row.get("status") or "").strip()
    group = _admin_task_group(
        status,
        progress=progress,
        intervention_count=intervention_count,
    )
    product_id = int(state.get("product_id") or 0)
    product = _product_summary(product_id)
    cost_tracking = dict(state.get("cost_tracking") or {})
    cost_actual = dict(cost_tracking.get("actual") or {})
    cost_estimate = dict(cost_tracking.get("estimate") or {})
    initiator = dict(state.get("initiator") or {})
    creator = (row.get("username") or initiator.get("user_name") or "").strip()

    return {
        "id": row["id"],
        "status": status,
        "status_label": _status_label(status, waiting_voice_count=waiting_voice_count),
        "group": group,
        "group_label": _admin_group_label(group),
        "user_id": row.get("user_id"),
        "creator": {
            "id": row.get("user_id"),
            "name": creator or f"用户 {row.get('user_id') or '—'}",
        },
        "product_id": product_id,
        "product": product,
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
        "intervention_count": intervention_count,
        "cost_actual": _float_or_zero(cost_actual.get("actual_cost_cny")),
        "cost_estimate": _float_or_zero(cost_estimate.get("estimated_cost_cny")),
        "detail_url": detail_url,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _serialize_task(row: dict, state: dict) -> dict:
    detail_url = f"/tasks/{row['id']}"
    raw_source_ids = _collect_source_raw_ids(state)
    raw_source_name_map = _resolve_raw_source_name_map(raw_source_ids)
    plan = [
        _serialize_item(
            item,
            parent_detail_url=detail_url,
            raw_source_name_map=raw_source_name_map,
        )
        for item in (state.get("plan") or [])
    ]
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
        "raw_source_display_names": [
            raw_source_name_map.get(raw_source_id) or f"原始视频 #{raw_source_id}"
            for raw_source_id in raw_source_ids
        ],
        "video_params_snapshot": dict(state.get("video_params_snapshot") or {}),
        "can_resume": status in _PARENT_RESUMABLE_STATUSES,
        "can_retry_failed": failed_count > 0,
        "items": plan,
    }


def _progress_summary(progress: dict) -> dict:
    total = int(progress.get("total") or 0)
    done = int(progress.get("done") or 0)
    skipped = int(progress.get("skipped") or 0)
    failed = int(progress.get("failed") or 0)
    interrupted = int(progress.get("interrupted") or 0)
    awaiting_voice = int(progress.get("awaiting_voice") or 0)
    completed = done + skipped
    pct = min(100, round((completed / total) * 100)) if total else 0
    return {
        **progress,
        "total": total,
        "done": done,
        "skipped": skipped,
        "failed": failed,
        "interrupted": interrupted,
        "awaiting_voice": awaiting_voice,
        "completed": completed,
        "pct": pct,
    }


def _admin_task_group(status: str, *, progress: dict, intervention_count: int) -> str:
    if status in _STUCK_PARENT_STATUSES or intervention_count > 0:
        return "stuck"
    total = int(progress.get("total") or 0)
    completed = int(progress.get("completed") or 0)
    if status in _DONE_PARENT_STATUSES or (total > 0 and completed >= total):
        return "done"
    return "running"


def _admin_group_label(group: str) -> str:
    return {
        "stuck": "需要人工干预",
        "running": "正常进行中",
        "done": "已完成",
    }.get(group, group or "未知")


def _product_summary(product_id: int) -> dict:
    if not product_id:
        return {"id": 0, "name": "未关联商品", "product_code": ""}
    try:
        product = medias.get_product(product_id) or {}
    except Exception:
        log.warning("bulk_translate admin product lookup failed: %s", product_id, exc_info=True)
        product = {}
    return {
        "id": product_id,
        "name": (product.get("name") or f"商品 #{product_id}").strip(),
        "product_code": (product.get("product_code") or "").strip(),
    }


def _float_or_zero(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _serialize_item(
    raw_item: dict,
    *,
    parent_detail_url: str,
    raw_source_name_map: dict[int, str] | None = None,
) -> dict:
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
        "summary": _item_summary(kind, ref, raw_source_name_map=raw_source_name_map),
        "ref": ref,
    }


def _item_summary(kind: str, ref: dict, *, raw_source_name_map: dict[int, str] | None = None) -> str:
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
        display_name = (raw_source_name_map or {}).get(source_raw_id)
        if display_name:
            return f"原始视频 {display_name}"
        return f"原始视频 #{source_raw_id}"
    return "翻译子任务"


def _collect_source_raw_ids(state: dict) -> list[int]:
    raw_source_ids: list[int] = []
    for raw_source_id in state.get("raw_source_ids") or []:
        _append_unique_raw_source_id(raw_source_ids, raw_source_id)
    for item in state.get("plan") or []:
        ref = dict((item or {}).get("ref") or {})
        _append_unique_raw_source_id(raw_source_ids, ref.get("source_raw_id"))
        for raw_source_id in ref.get("source_raw_ids") or []:
            _append_unique_raw_source_id(raw_source_ids, raw_source_id)
        for raw_source_id in ref.get("source_cover_ids") or []:
            _append_unique_raw_source_id(raw_source_ids, raw_source_id)
    return raw_source_ids


def _append_unique_raw_source_id(raw_source_ids: list[int], raw_source_id) -> None:
    try:
        normalized = int(raw_source_id or 0)
    except (TypeError, ValueError):
        return
    if normalized <= 0 or normalized in raw_source_ids:
        return
    raw_source_ids.append(normalized)


def _resolve_raw_source_name_map(raw_source_ids: list[int]) -> dict[int, str]:
    return {
        raw_source_id: _resolve_raw_source_display_name(raw_source_id)
        for raw_source_id in raw_source_ids
    }


def _resolve_raw_source_display_name(raw_source_id: int) -> str:
    try:
        raw_source = medias.get_raw_source(raw_source_id) or {}
    except Exception:
        log.warning("bulk_translate raw source lookup failed: %s", raw_source_id, exc_info=True)
        raw_source = {}
    display_name = (raw_source.get("display_name") or "").strip()
    if display_name:
        return display_name
    video_object_key = (raw_source.get("video_object_key") or "").strip()
    if video_object_key:
        filename = Path(video_object_key).name.strip()
        if filename:
            return filename
    return ""


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
