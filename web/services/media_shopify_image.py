"""Service response builders for product Shopify image status routes."""

from __future__ import annotations

from dataclasses import dataclass

from appcore import medias, shopify_image_tasks


@dataclass(frozen=True)
class MediaShopifyImageResponse:
    payload: dict
    status_code: int = 200


def normalize_shopify_image_lang(lang: str | None) -> str | None:
    normalized = (lang or "").strip().lower()
    if not normalized or normalized == "en" or not medias.is_valid_language(normalized):
        return None
    return normalized


def build_shopify_image_confirm_response(
    *,
    product_id: int,
    lang: str,
    user_id: int | None,
) -> MediaShopifyImageResponse:
    status = shopify_image_tasks.confirm_lang(product_id, lang, user_id)
    return MediaShopifyImageResponse({"ok": True, "status": status})


def build_shopify_image_unavailable_response(
    *,
    product_id: int,
    lang: str,
    body: dict | None,
) -> MediaShopifyImageResponse:
    body = body if isinstance(body, dict) else {}
    status = shopify_image_tasks.mark_link_unavailable(
        product_id,
        lang,
        (body.get("reason") or "").strip(),
    )
    return MediaShopifyImageResponse({"ok": True, "status": status})


def build_shopify_image_clear_response(
    *,
    product_id: int,
    lang: str,
) -> MediaShopifyImageResponse:
    status = shopify_image_tasks.reset_lang(product_id, lang)
    return MediaShopifyImageResponse({"ok": True, "status": status})


def build_shopify_image_requeue_response(
    *,
    product_id: int,
    lang: str,
) -> MediaShopifyImageResponse:
    shopify_image_tasks.reset_lang(product_id, lang)
    task = shopify_image_tasks.create_or_reuse_task(product_id, lang)
    status_code = 202 if task.get("status") != shopify_image_tasks.TASK_BLOCKED else 409
    return MediaShopifyImageResponse({"ok": status_code == 202, "task": task}, status_code)
