"""Service helpers for starting media-product bulk translation."""

from __future__ import annotations

from dataclasses import dataclass

from appcore import bulk_translate_runtime, medias
from web.routes.bulk_translate import start_bulk_scheduler_background


DEFAULT_CONTENT_TYPES = ["copywriting", "detail_images", "video_covers", "videos"]
ALLOWED_CONTENT_TYPES = {"copywriting", "detail_images", "video_covers", "videos"}
PRODUCT_NOT_LISTED_PAYLOAD = {
    "error": "product_not_listed",
    "message": "产品已下架，不能执行该操作",
}


@dataclass(frozen=True)
class ProductTranslateResult:
    ok: bool
    status_code: int
    task_id: str | None = None
    error: str | None = None
    payload: dict | None = None


def _validation_error(message: str) -> ProductTranslateResult:
    return ProductTranslateResult(ok=False, status_code=400, error=message)


def _coerce_raw_ids(raw_ids) -> tuple[list[int], ProductTranslateResult | None]:
    try:
        return [int(x) for x in raw_ids], None
    except (TypeError, ValueError):
        return [], _validation_error("raw_ids must be integers")


def start_product_translation(
    *,
    user_id: int,
    user_name: str,
    product_id: int,
    product: dict | None = None,
    body: dict,
    ip: str,
    user_agent: str,
) -> ProductTranslateResult:
    if product is not None and not medias.is_product_listed(product):
        return ProductTranslateResult(
            ok=False,
            status_code=409,
            error="product_not_listed",
            payload=dict(PRODUCT_NOT_LISTED_PAYLOAD),
        )

    raw_ids = body.get("raw_ids") or []
    target_langs = body.get("target_langs") or []
    content_types = body.get("content_types") or list(DEFAULT_CONTENT_TYPES)

    if ("videos" in content_types or "video_covers" in content_types) and not raw_ids:
        return _validation_error("raw_ids 不能为空")
    if not target_langs:
        return _validation_error("target_langs 不能为空")

    if not isinstance(content_types, list) or not content_types:
        return _validation_error("content_types 不能为空")

    raw_ids_int, error = _coerce_raw_ids(raw_ids)
    if error:
        return error

    rows = medias.list_raw_sources(product_id)
    valid_ids = {int(r["id"]) for r in rows}
    bad = [rid for rid in raw_ids_int if rid not in valid_ids]
    if bad:
        return _validation_error(f"raw_ids 不属于该产品或已删除: {bad}")

    for lang in target_langs:
        if lang == "en" or not medias.is_valid_language(lang):
            return _validation_error(f"target_langs 不支持: {lang}")

    for content_type in content_types:
        if content_type not in ALLOWED_CONTENT_TYPES:
            return _validation_error(f"content_types 不支持: {content_type}")

    initiator = {
        "user_id": user_id,
        "user_name": user_name or "",
        "ip": ip or "",
        "user_agent": user_agent or "",
        "source": "medias_raw_translate",
    }
    task_id = bulk_translate_runtime.create_bulk_translate_task(
        user_id=user_id,
        product_id=product_id,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=bool(body.get("force_retranslate")),
        video_params=body.get("video_params") or {},
        initiator=initiator,
        raw_source_ids=raw_ids_int,
    )
    bulk_translate_runtime.start_task(task_id, user_id)
    start_bulk_scheduler_background(
        task_id,
        user_id=user_id,
        entrypoint="medias.raw_translate",
        action="start",
        details={"source": "medias_raw_translate"},
    )
    return ProductTranslateResult(ok=True, status_code=202, task_id=task_id)
