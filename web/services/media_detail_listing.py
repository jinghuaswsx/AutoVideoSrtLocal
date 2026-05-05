"""Service helpers for media product detail image list responses."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DetailImagesListResponse:
    payload: dict
    status_code: int = 200


@dataclass(frozen=True)
class DetailImageProxyResponse:
    object_key: str | None = None
    not_found: bool = False


def build_detail_images_list_response(
    product_id: int,
    lang: str,
    *,
    is_valid_language_fn: Callable[[str], bool],
    list_detail_images_fn: Callable[[int, str], Sequence[Mapping[str, object]]],
    serialize_detail_image_fn: Callable[[Mapping[str, object]], dict],
) -> DetailImagesListResponse:
    normalized_lang = (lang or "en").strip().lower()
    if not is_valid_language_fn(normalized_lang):
        return DetailImagesListResponse(
            {"error": f"涓嶆敮鎸佺殑璇: {normalized_lang}"},
            400,
        )

    rows = list_detail_images_fn(product_id, normalized_lang)
    return DetailImagesListResponse({
        "items": [serialize_detail_image_fn(row) for row in rows],
    })


def build_detail_image_proxy_response(
    image_id: int,
    *,
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
    get_product_fn: Callable[[int], Mapping[str, object] | None],
    can_access_product_fn: Callable[[Mapping[str, object] | None], bool],
) -> DetailImageProxyResponse:
    row = get_detail_image_fn(int(image_id))
    if not row or row.get("deleted_at") is not None:
        return DetailImageProxyResponse(not_found=True)

    try:
        product_id = int(row["product_id"])
    except (KeyError, TypeError, ValueError):
        return DetailImageProxyResponse(not_found=True)

    product = get_product_fn(product_id)
    if not can_access_product_fn(product):
        return DetailImageProxyResponse(not_found=True)

    object_key = str(row.get("object_key") or "").strip()
    if not object_key:
        return DetailImageProxyResponse(not_found=True)
    return DetailImageProxyResponse(object_key=object_key)
