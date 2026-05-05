"""OpenAPI Shopify image localizer bootstrap response assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from appcore import medias
from web.services.openapi_materials_serializers import media_download_url


IsValidLanguageFn = Callable[[str], bool]
GetProductByCodeFn = Callable[[str], dict | None]
ResolveShopifyProductIdFn = Callable[[int], str | None]
ListReferenceImagesFn = Callable[[int, str], list[dict]]
GetLanguageNameFn = Callable[[str], str]
MediaDownloadUrlFn = Callable[[str | None], str | None]


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
