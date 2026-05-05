"""OpenAPI link-check bootstrap response assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from appcore import medias
from appcore.link_check_locale import detect_target_language_from_url
from web.services.openapi_materials_serializers import media_download_url, normalize_target_url


ListLanguagesFn = Callable[[], list[dict]]
DetectTargetLanguageFn = Callable[[str, set[str]], str | None]
FindProductFn = Callable[[str, str], dict | None]
ListReferenceImagesFn = Callable[[int, str], list[dict]]
GetLanguageNameFn = Callable[[str], str]
MediaDownloadUrlFn = Callable[[str | None], str | None]


@dataclass(frozen=True)
class LinkCheckBootstrapError(Exception):
    error: str
    status_code: int


def _enabled_language_codes(rows: list[dict]) -> set[str]:
    return {
        (row.get("code") or "").strip().lower()
        for row in rows or []
        if row and row.get("enabled", 1)
    }


def _serialize_reference_images(
    rows: list[dict],
    *,
    media_download_url_fn: MediaDownloadUrlFn,
) -> list[dict]:
    reference_images = []
    for item in rows or []:
        object_key = (item.get("object_key") or "").strip()
        if not object_key:
            continue
        reference_images.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "filename": item.get("filename"),
            "download_url": media_download_url_fn(object_key),
            "storage_backend": "local",
        })
    return reference_images


def build_link_check_bootstrap_response(
    target_url: str | None,
    *,
    list_languages_fn: ListLanguagesFn | None = None,
    detect_target_language_fn: DetectTargetLanguageFn | None = None,
    find_product_fn: FindProductFn | None = None,
    list_reference_images_fn: ListReferenceImagesFn | None = None,
    get_language_name_fn: GetLanguageNameFn | None = None,
    media_download_url_fn: MediaDownloadUrlFn = media_download_url,
) -> dict:
    target_url = (target_url or "").strip()
    if not target_url or not target_url.lower().startswith(("http://", "https://")):
        raise LinkCheckBootstrapError("invalid target_url", 400)

    list_languages_fn = list_languages_fn or medias.list_languages
    detect_target_language_fn = detect_target_language_fn or detect_target_language_from_url
    find_product_fn = find_product_fn or medias.find_product_for_link_check_url
    list_reference_images_fn = list_reference_images_fn or medias.list_reference_images_for_lang
    get_language_name_fn = get_language_name_fn or medias.get_language_name

    enabled_languages = _enabled_language_codes(list_languages_fn())
    target_language = detect_target_language_fn(target_url, enabled_languages)
    if not target_language:
        raise LinkCheckBootstrapError("language not detected", 409)

    product = find_product_fn(target_url, target_language)
    if not product:
        raise LinkCheckBootstrapError("product not found", 404)

    raw_reference_images = list_reference_images_fn(int(product["id"]), target_language)
    if not raw_reference_images:
        raise LinkCheckBootstrapError("references not ready", 409)

    reference_images = _serialize_reference_images(
        raw_reference_images,
        media_download_url_fn=media_download_url_fn,
    )
    if not reference_images:
        raise LinkCheckBootstrapError("references not ready", 409)

    return {
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code"),
            "name": product.get("name"),
        },
        "target_language": target_language,
        "target_language_name": get_language_name_fn(target_language),
        "matched_by": product.get("_matched_by"),
        "normalized_url": normalize_target_url(target_url),
        "reference_images": reference_images,
    }
