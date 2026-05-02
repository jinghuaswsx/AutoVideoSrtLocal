"""Payload builders for detail-image translation tasks."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

DETAIL_TRANSLATE_CONTEXT_ENTRY = "medias_edit_detail"


@dataclass(frozen=True)
class DetailTranslateTaskPayload:
    task_id: str
    task_dir: str
    create_kwargs: dict


def build_detail_translate_task_payload(
    *,
    output_dir: str,
    user_id: int,
    product_id: int,
    product: Mapping[str, object],
    target_lang: str,
    target_language_name: str,
    prompt_template: str,
    source_rows: Sequence[Mapping[str, object]],
    model_id: str,
    concurrency_mode: str,
    compose_project_name: Callable[[str, str, str], str],
) -> DetailTranslateTaskPayload:
    task_id = uuid.uuid4().hex
    task_dir = os.path.join(output_dir, task_id)
    product_name = str(product.get("name") or "").strip()

    items = [
        {
            "idx": idx,
            "filename": os.path.basename(row.get("object_key") or "") or f"detail_{idx}.png",
            "src_tos_key": row["object_key"],
            "source_bucket": "media",
            "source_detail_image_id": row["id"],
        }
        for idx, row in enumerate(source_rows)
    ]
    medias_context = {
        "entry": DETAIL_TRANSLATE_CONTEXT_ENTRY,
        "product_id": product_id,
        "source_lang": "en",
        "target_lang": target_lang,
        "source_bucket": "media",
        "source_detail_image_ids": [row["id"] for row in source_rows],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
        "applied_at": "",
        "applied_detail_image_ids": [],
        "last_apply_error": "",
    }
    return DetailTranslateTaskPayload(
        task_id=task_id,
        task_dir=task_dir,
        create_kwargs={
            "user_id": user_id,
            "preset": "detail",
            "target_language": target_lang,
            "target_language_name": target_language_name,
            "model_id": model_id,
            "prompt": prompt_template.replace("{target_language_name}", target_language_name),
            "items": items,
            "product_name": product_name,
            "project_name": compose_project_name(product_name, "detail", target_language_name),
            "medias_context": medias_context,
            "concurrency_mode": concurrency_mode,
        },
    )


def project_detail_translate_task_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    product_id: int,
    target_lang: str,
) -> list[dict]:
    items = []
    normalized_target_lang = (target_lang or "").strip().lower()
    for row in rows:
        state = _load_state(row.get("state_json"))
        if not isinstance(state, MappingABC):
            continue
        ctx = state.get("medias_context") or {}
        if not isinstance(ctx, MappingABC):
            continue
        if state.get("preset") != "detail":
            continue
        if ctx.get("entry") != DETAIL_TRANSLATE_CONTEXT_ENTRY:
            continue
        if _optional_int(ctx.get("product_id")) != product_id:
            continue
        if str(ctx.get("target_lang") or "").strip().lower() != normalized_target_lang:
            continue

        progress = state.get("progress") or {}
        if not isinstance(progress, MappingABC):
            progress = {}
        task_id = str(row["id"])
        items.append({
            "task_id": task_id,
            "status": state.get("status") or "queued",
            "apply_status": ctx.get("apply_status") or "",
            "applied_detail_image_ids": list(ctx.get("applied_detail_image_ids") or []),
            "last_apply_error": ctx.get("last_apply_error") or "",
            "progress": dict(progress),
            "detail_url": f"/image-translate/{task_id}",
            "created_at": _isoformat_or_none(row.get("created_at")),
        })
    return items


def _load_state(value: object) -> object:
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _optional_int(value: object) -> int | None:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return None


def _isoformat_or_none(value: object) -> str | None:
    if not value:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)
