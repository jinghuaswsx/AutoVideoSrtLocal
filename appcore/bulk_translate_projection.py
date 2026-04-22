from __future__ import annotations

import json
from datetime import datetime

from appcore import medias
from appcore.db import query

GROUP_ORDER = ("copywriting", "detail_images", "video_covers", "videos")


def build_product_task_payload(user_id: int, product_id: int) -> dict:
    product = medias.get_product(product_id)
    if not product:
        raise ValueError(f"product {product_id} not found")

    rows = query(
        "SELECT id, status, state_json, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = 'bulk_translate' AND deleted_at IS NULL "
        "  AND JSON_UNQUOTE(JSON_EXTRACT(state_json, '$.product_id')) = %s "
        "ORDER BY created_at DESC "
        "LIMIT 50",
        (user_id, str(product_id)),
    )

    return {
        "product": _serialize_product(product),
        "batches": [_serialize_batch(row) for row in rows],
    }


def build_task_action(item: dict) -> dict:
    status = str(item.get("status") or "")
    task_id = str(item.get("task_id") or "")
    idx = int(item.get("idx") or 0)
    child_task_id = str(item.get("child_task_id") or "").strip()

    if status == "failed" and task_id:
        return {
            "label": "重新启动",
            "method": "POST",
            "endpoint": f"/api/bulk-translate/{task_id}/retry-item",
            "payload": {"idx": idx},
        }
    if status == "interrupted" and task_id:
        return {
            "label": "从中断点继续",
            "method": "POST",
            "endpoint": f"/api/bulk-translate/{task_id}/resume",
            "payload": {},
        }
    if status == "awaiting_voice" and child_task_id:
        return {
            "label": "去选声音",
            "href": f"/multi-translate/{child_task_id}",
        }
    return {}


def _serialize_product(product: dict) -> dict:
    return {
        "id": product["id"],
        "name": product.get("name") or "",
        "product_code": product.get("product_code") or "",
    }


def _serialize_batch(row: dict) -> dict:
    state = _coerce_state(row.get("state_json"))
    plan = list(state.get("plan") or [])
    grouped = {kind: [] for kind in GROUP_ORDER}
    for item in plan:
        kind = str(item.get("kind") or "")
        if kind not in grouped:
            continue
        normalized = {
            "task_id": row["id"],
            "idx": int(item.get("idx") or 0),
            "kind": kind,
            "lang": str(item.get("lang") or ""),
            "label": _build_label(item),
            "status": str(item.get("status") or ""),
            "child_task_id": item.get("child_task_id"),
            "ref": item.get("ref") or {},
        }
        normalized["action"] = build_task_action(normalized)
        grouped[kind].append(normalized)

    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        created_at_value = created_at.isoformat()
    else:
        created_at_value = str(created_at) if created_at is not None else None

    return {
        "task_id": row["id"],
        "status": row.get("status") or "",
        "created_at": created_at_value,
        "groups": grouped,
    }


def _build_label(item: dict) -> str:
    kind = str(item.get("kind") or "")
    lang = str(item.get("lang") or "").upper()
    ref = item.get("ref") or {}

    if kind == "copywriting":
        source_id = ref.get("source_copy_id")
        return f"{lang} 文案 #{source_id}" if source_id else f"{lang} 文案"
    if kind == "detail_images":
        count = len(ref.get("source_detail_ids") or [])
        return f"{lang} 详情图 x{count}" if count else f"{lang} 详情图"
    if kind == "video_covers":
        count = len(ref.get("source_raw_ids") or [])
        return f"{lang} 视频封面 x{count}" if count else f"{lang} 视频封面"
    if kind == "videos":
        raw_id = ref.get("source_raw_id")
        return f"{lang} 视频 #{raw_id}" if raw_id else f"{lang} 视频"
    return f"{lang} 任务"


def _coerce_state(raw_state: object) -> dict:
    if isinstance(raw_state, dict):
        return raw_state
    if not raw_state:
        return {}
    if isinstance(raw_state, bytes):
        raw_state = raw_state.decode("utf-8")
    if isinstance(raw_state, str):
        try:
            parsed = json.loads(raw_state)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
