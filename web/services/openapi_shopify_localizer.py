"""OpenAPI Shopify image localizer bootstrap response assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from appcore import medias, shopify_image_tasks
from web.services.openapi_materials_serializers import media_download_url, serialize_shopify_image_task


IsValidLanguageFn = Callable[[str], bool]
GetProductByCodeFn = Callable[[str], dict | None]
ResolveShopifyProductIdFn = Callable[[int], str | None]
ListReferenceImagesFn = Callable[[int, str], list[dict]]
GetLanguageNameFn = Callable[[str], str]
MediaDownloadUrlFn = Callable[[str | None], str | None]
ClaimNextTaskFn = Callable[..., dict | None]
HeartbeatTaskFn = Callable[[int, str, int], int]
CompleteTaskFn = Callable[[int, dict], dict]
FailTaskFn = Callable[[int, str, str, dict], dict]
SerializeShopifyImageTaskFn = Callable[[dict | None], dict | None]


@dataclass(frozen=True)
class ShopifyLocalizerBootstrapError(Exception):
    error: str
    status_code: int
    message: str | None = None


def _serialize_detail_images(
    rows: list[dict],
    *,
    media_download_url_fn: MediaDownloadUrlFn,
) -> list[dict]:
    images = []
    for item in rows or []:
        object_key = (item.get("object_key") or "").strip()
        if item.get("kind") != "detail" or not object_key:
            continue
        images.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "filename": item.get("filename"),
            "url": media_download_url_fn(object_key),
        })
    return images


def _parse_lock_seconds(value, *, default: int = 900) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def build_shopify_localizer_bootstrap_response(
    body: dict | None,
    *,
    is_valid_language_fn: IsValidLanguageFn | None = None,
    get_product_by_code_fn: GetProductByCodeFn | None = None,
    resolve_shopify_product_id_fn: ResolveShopifyProductIdFn | None = None,
    list_reference_images_for_lang_fn: ListReferenceImagesFn | None = None,
    get_language_name_fn: GetLanguageNameFn | None = None,
    media_download_url_fn: MediaDownloadUrlFn = media_download_url,
) -> dict:
    body = body or {}
    product_code = str(body.get("product_code") or "").strip().lower()
    lang = str(body.get("lang") or "").strip().lower()
    if not product_code or not lang:
        raise ShopifyLocalizerBootstrapError("missing product_code or lang", 400)

    is_valid_language_fn = is_valid_language_fn or medias.is_valid_language
    get_product_by_code_fn = get_product_by_code_fn or medias.get_product_by_code
    resolve_shopify_product_id_fn = resolve_shopify_product_id_fn or medias.resolve_shopify_product_id
    list_reference_images_for_lang_fn = list_reference_images_for_lang_fn or medias.list_reference_images_for_lang
    get_language_name_fn = get_language_name_fn or medias.get_language_name

    if not is_valid_language_fn(lang):
        raise ShopifyLocalizerBootstrapError("invalid lang", 400)
    if lang == "en":
        raise ShopifyLocalizerBootstrapError(
            "invalid_target_lang",
            400,
            "英文为源语言，不能作为图片本地化目标语言。",
        )

    product = get_product_by_code_fn(product_code)
    if not product:
        raise ShopifyLocalizerBootstrapError("product not found", 404)

    shopify_product_id_override = str(body.get("shopify_product_id") or "").strip()
    shopify_product_id = shopify_product_id_override or resolve_shopify_product_id_fn(int(product["id"]))
    if not shopify_product_id:
        raise ShopifyLocalizerBootstrapError(
            "shopify_product_id_missing",
            409,
            "未找到 Shopify ID。请先到产品编辑页最底部填写 Shopify ID 后，再执行图片本地化工具。",
        )

    reference_images = _serialize_detail_images(
        list_reference_images_for_lang_fn(int(product["id"]), "en"),
        media_download_url_fn=media_download_url_fn,
    )
    localized_images = _serialize_detail_images(
        list_reference_images_for_lang_fn(int(product["id"]), lang),
        media_download_url_fn=media_download_url_fn,
    )
    if not reference_images:
        raise ShopifyLocalizerBootstrapError("english references not ready", 409)
    if not localized_images:
        raise ShopifyLocalizerBootstrapError("localized images not ready", 409)

    return {
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code"),
            "shopify_product_id": shopify_product_id,
            "name": product.get("name"),
        },
        "language": {
            "code": lang,
            "name_zh": get_language_name_fn(lang),
            "shop_locale": lang,
            "folder_code": lang,
        },
        "reference_images": reference_images,
        "localized_images": localized_images,
    }


def build_shopify_localizer_task_claim_response(
    body: dict | None,
    *,
    claim_next_task_fn: ClaimNextTaskFn | None = None,
    serialize_shopify_image_task_fn: SerializeShopifyImageTaskFn = serialize_shopify_image_task,
) -> dict:
    body = body or {}
    claim_next_task_fn = claim_next_task_fn or shopify_image_tasks.claim_next_task
    worker_id = str(body.get("worker_id") or "").strip() or "unknown-worker"
    lock_seconds = _parse_lock_seconds(body.get("lock_seconds"))
    task = claim_next_task_fn(worker_id, lock_seconds=lock_seconds)
    return {"task": serialize_shopify_image_task_fn(task)}


def build_shopify_localizer_task_heartbeat_response(
    task_id: int,
    body: dict | None,
    *,
    heartbeat_task_fn: HeartbeatTaskFn | None = None,
) -> dict:
    body = body or {}
    heartbeat_task_fn = heartbeat_task_fn or shopify_image_tasks.heartbeat_task
    worker_id = str(body.get("worker_id") or "").strip()
    lock_seconds = _parse_lock_seconds(body.get("lock_seconds"))
    updated = heartbeat_task_fn(task_id, worker_id, lock_seconds)
    return {"ok": bool(updated)}


def build_shopify_localizer_task_complete_response(
    task_id: int,
    body: dict | None,
    *,
    complete_task_fn: CompleteTaskFn | None = None,
) -> dict:
    body = body or {}
    complete_task_fn = complete_task_fn or shopify_image_tasks.complete_task
    status = complete_task_fn(task_id, body.get("result") or {})
    return {"ok": True, "status": status}


def build_shopify_localizer_task_fail_response(
    task_id: int,
    body: dict | None,
    *,
    fail_task_fn: FailTaskFn | None = None,
) -> dict:
    body = body or {}
    fail_task_fn = fail_task_fn or shopify_image_tasks.fail_task
    status = fail_task_fn(
        task_id,
        str(body.get("error_code") or "worker_failed"),
        str(body.get("error_message") or ""),
        body.get("result") or {},
    )
    return {"ok": True, "status": status}
