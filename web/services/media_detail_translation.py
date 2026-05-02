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


@dataclass(frozen=True)
class DetailTranslateApplyOutcome:
    payload: dict | None = None
    error: str | None = None
    status_code: int = 200
    not_found: bool = False


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


def apply_detail_translate_task(
    task: Mapping[str, object] | None,
    *,
    task_id: str,
    product_id: int,
    target_lang: str,
    user_id: int,
    is_running: Callable[[str], bool],
    apply_translated_detail_images: Callable[..., Mapping[str, object]],
) -> DetailTranslateApplyOutcome:
    if not isinstance(task, MappingABC) or task.get("type") != "image_translate":
        return DetailTranslateApplyOutcome(not_found=True, status_code=404)

    task_user_id = task.get("_user_id")
    if task_user_id is not None and _optional_int(task_user_id) != int(user_id):
        return DetailTranslateApplyOutcome(not_found=True, status_code=404)

    ctx = task.get("medias_context") or {}
    if not isinstance(ctx, MappingABC):
        ctx = {}
    if _optional_int(ctx.get("product_id")) != product_id:
        return _apply_error("task does not belong to this product", 400)
    normalized_target_lang = (target_lang or "").strip().lower()
    if str(ctx.get("target_lang") or "").strip().lower() != normalized_target_lang:
        return _apply_error("task target language does not match current language", 400)

    if is_running(task_id):
        return _apply_error("task is still running", 409)
    if (task.get("status") or "") not in {"done", "error"}:
        return _apply_error("task has not finished yet", 409)

    try:
        result = apply_translated_detail_images(task, allow_partial=True, user_id=int(user_id))
    except (ValueError, RuntimeError) as exc:
        return _apply_error(str(exc), 409)

    applied_ids = list(result.get("applied_ids") or [])
    skipped_failed_indices = list(result.get("skipped_failed_indices") or [])
    return DetailTranslateApplyOutcome(
        payload={
            "ok": True,
            "applied": len(applied_ids),
            "skipped_failed": len(skipped_failed_indices),
            "apply_status": result.get("apply_status"),
            "applied_detail_image_ids": applied_ids,
        }
    )


def _apply_error(message: str, status_code: int) -> DetailTranslateApplyOutcome:
    return DetailTranslateApplyOutcome(error=message, status_code=status_code)


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
