"""Service helpers for media product detail image list responses."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DetailImagesListResponse:
    payload: dict
    status_code: int = 200


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
