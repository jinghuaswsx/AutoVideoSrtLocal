"""Payload builders for detail-image translation tasks."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

DETAIL_TRANSLATE_CONTEXT_ENTRY = "medias_edit_detail"
PRODUCT_NOT_LISTED_PAYLOAD = {
    "error": "product_not_listed",
    "message": "产品已下架，不能执行该操作",
}
DETAIL_TRANSLATE_TASKS_SQL = (
    "SELECT id, created_at, state_json "
    "FROM projects "
    "WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL "
    "ORDER BY created_at DESC LIMIT 50"
)


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


@dataclass(frozen=True)
class DetailTranslateFromEnOutcome:
    payload: dict | None = None
    error: str | None = None
    status_code: int = 200


@dataclass(frozen=True)
class DetailTranslateTasksOutcome:
    payload: dict | None = None
    error: str | None = None
    status_code: int = 200


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


def build_detail_translate_from_en_response(
    product_id: int,
    user_id: int,
    product: Mapping[str, object],
    body: Mapping[str, object] | None,
    *,
    is_product_listed_fn: Callable[[Mapping[str, object]], bool] | None = None,
    parse_lang_fn: Callable[..., tuple[str | None, str | None]],
    default_concurrency_mode: str,
    output_dir: str,
    list_detail_images_fn: Callable[[int, str], Sequence[Mapping[str, object]]],
    detail_images_is_gif_fn: Callable[[Mapping[str, object]], bool],
    get_prompts_for_lang_fn: Callable[[str], Mapping[str, object]],
    get_language_name_fn: Callable[[str], str],
    default_model_id_fn: Callable[[], str],
    compose_project_name_fn: Callable[[str, str, str], str],
    create_image_translate_fn: Callable[..., object],
    start_image_translate_runner_fn: Callable[[str, int], object],
) -> DetailTranslateFromEnOutcome:
    if is_product_listed_fn is not None and not is_product_listed_fn(product):
        return DetailTranslateFromEnOutcome(
            payload=dict(PRODUCT_NOT_LISTED_PAYLOAD),
            status_code=409,
        )

    body_dict = dict(body or {})
    lang, err = parse_lang_fn(body_dict, default="")
    if err:
        return DetailTranslateFromEnOutcome(error=err, status_code=400)
    target_lang = str(lang or "").strip().lower()
    if target_lang == "en":
        return DetailTranslateFromEnOutcome(
            error="english detail images do not need translate-from-en",
            status_code=400,
        )

    mode = str(body_dict.get("concurrency_mode") or default_concurrency_mode).strip().lower()
    if mode not in {"sequential", "parallel"}:
        return DetailTranslateFromEnOutcome(
            error="concurrency_mode must be sequential or parallel",
            status_code=400,
        )

    source_rows = list(list_detail_images_fn(product_id, "en"))
    if not source_rows:
        return DetailTranslateFromEnOutcome(
            error="english detail images are required first",
            status_code=409,
        )

    translatable_rows = [row for row in source_rows if not detail_images_is_gif_fn(row)]
    if not translatable_rows:
        return DetailTranslateFromEnOutcome(
            error="鑻辫鐗堣鎯呭浘鍏ㄩ儴涓?GIF 鍔ㄥ浘锛屾棤鍙炕璇戠殑闈欐€佸浘",
            status_code=409,
        )

    prompt_template = str((get_prompts_for_lang_fn(target_lang).get("detail") or "")).strip()
    if not prompt_template:
        return DetailTranslateFromEnOutcome(
            error="褰撳墠璇鏈厤缃鎯呭浘缈昏瘧 prompt",
            status_code=409,
        )

    language_name = get_language_name_fn(target_lang)
    task_payload = build_detail_translate_task_payload(
        output_dir=output_dir,
        user_id=user_id,
        product_id=product_id,
        product=product or {},
        target_lang=target_lang,
        target_language_name=language_name,
        prompt_template=prompt_template,
        source_rows=translatable_rows,
        model_id=str(body_dict.get("model_id") or "").strip() or default_model_id_fn(),
        concurrency_mode=mode,
        compose_project_name=compose_project_name_fn,
    )
    create_image_translate_fn(
        task_payload.task_id,
        task_payload.task_dir,
        **task_payload.create_kwargs,
    )
    start_image_translate_runner_fn(task_payload.task_id, user_id)
    return DetailTranslateFromEnOutcome(
        payload={
            "task_id": task_payload.task_id,
            "detail_url": f"/image-translate/{task_payload.task_id}",
        },
        status_code=201,
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


def build_detail_translate_tasks_response(
    product_id: int,
    user_id: int,
    target_lang: str,
    *,
    is_valid_language_fn: Callable[[str], bool],
    query_tasks_fn: Callable[[str, tuple[int]], Sequence[Mapping[str, object]]],
) -> DetailTranslateTasksOutcome:
    normalized_target_lang = (target_lang or "").strip().lower()
    if not is_valid_language_fn(normalized_target_lang):
        return DetailTranslateTasksOutcome(
            error=f"涓嶆敮鎸佺殑璇: {normalized_target_lang}",
            status_code=400,
        )

    rows = query_tasks_fn(DETAIL_TRANSLATE_TASKS_SQL, (int(user_id),))
    return DetailTranslateTasksOutcome(
        payload={
            "items": project_detail_translate_task_rows(
                rows,
                product_id=int(product_id),
                target_lang=normalized_target_lang,
            )
        }
    )


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


def build_detail_translate_apply_response(
    *,
    product_id: int,
    target_lang: str,
    task_id: str,
    user_id: int,
    is_valid_language_fn: Callable[[str], bool],
    get_task_fn: Callable[[str], Mapping[str, object] | None],
    is_running_fn: Callable[[str], bool],
    apply_translated_detail_images_fn: Callable[..., Mapping[str, object]],
) -> DetailTranslateApplyOutcome:
    normalized_target_lang = (target_lang or "").strip().lower()
    if not is_valid_language_fn(normalized_target_lang):
        return _apply_error(f"unsupported language: {normalized_target_lang}", 400)
    if normalized_target_lang == "en":
        return _apply_error("english detail images do not need manual apply", 400)

    return apply_detail_translate_task(
        get_task_fn(task_id),
        task_id=task_id,
        product_id=int(product_id),
        target_lang=normalized_target_lang,
        user_id=int(user_id),
        is_running=is_running_fn,
        apply_translated_detail_images=apply_translated_detail_images_fn,
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
