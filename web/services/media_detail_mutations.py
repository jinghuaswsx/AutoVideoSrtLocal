"""Mutation helpers for product detail images."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DetailImageMutationOutcome:
    payload: dict | None = None
    error: str | None = None
    status_code: int = 200
    not_found: bool = False


def delete_detail_image(
    image_id: int,
    *,
    product_id: int,
    get_detail_image: Callable[[int], Mapping[str, object] | None],
    soft_delete_detail_image: Callable[[int], object],
    delete_media_object: Callable[[str], object],
) -> DetailImageMutationOutcome:
    row = get_detail_image(image_id)
    if (
        not isinstance(row, Mapping)
        or _optional_int(row.get("product_id")) != int(product_id)
        or row.get("deleted_at") is not None
    ):
        return DetailImageMutationOutcome(not_found=True, status_code=404)

    soft_delete_detail_image(image_id)
    _best_effort_delete(row.get("object_key"), delete_media_object)
    return DetailImageMutationOutcome(payload={"ok": True})


def clear_detail_images(
    product_id: int,
    lang: str,
    *,
    list_detail_images: Callable[[int, str], Sequence[Mapping[str, object]]],
    soft_delete_detail_images_by_lang: Callable[[int, str], int],
    delete_media_object: Callable[[str], object],
) -> DetailImageMutationOutcome:
    if (lang or "").strip().lower() == "en":
        return DetailImageMutationOutcome(
            error="english detail images cannot be cleared via this endpoint",
            status_code=400,
        )

    normalized_lang = (lang or "").strip().lower()
    rows = list_detail_images(product_id, normalized_lang)
    cleared = soft_delete_detail_images_by_lang(product_id, normalized_lang)
    for row in rows:
        _best_effort_delete(row.get("object_key"), delete_media_object)
    return DetailImageMutationOutcome(payload={"ok": True, "cleared": cleared})


def build_clear_detail_images_response(
    product_id: int,
    body: Mapping[str, object] | None,
    *,
    parse_lang_fn: Callable[..., tuple[str | None, str | None]],
    list_detail_images_fn: Callable[[int, str], Sequence[Mapping[str, object]]],
    soft_delete_detail_images_by_lang_fn: Callable[[int, str], int],
    delete_media_object_fn: Callable[[str], object],
) -> DetailImageMutationOutcome:
    lang, err = parse_lang_fn(dict(body or {}), default="")
    if err:
        return DetailImageMutationOutcome(error=err, status_code=400)
    return clear_detail_images(
        product_id,
        lang or "",
        list_detail_images=list_detail_images_fn,
        soft_delete_detail_images_by_lang=soft_delete_detail_images_by_lang_fn,
        delete_media_object=delete_media_object_fn,
    )


def reorder_detail_images(
    product_id: int,
    lang: str,
    ids: object,
    *,
    reorder_detail_images: Callable[[int, str, list[int]], int],
) -> DetailImageMutationOutcome:
    if not isinstance(ids, list):
        return DetailImageMutationOutcome(error="ids must be list", status_code=400)
    try:
        ids_int = [int(value) for value in ids]
    except (TypeError, ValueError):
        return DetailImageMutationOutcome(error="ids must be integers", status_code=400)

    updated = reorder_detail_images(product_id, lang, ids_int)
    return DetailImageMutationOutcome(payload={"ok": True, "updated": updated})


def build_reorder_detail_images_response(
    product_id: int,
    body: Mapping[str, object] | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str | None, str | None]],
    reorder_detail_images_fn: Callable[[int, str, list[int]], int],
) -> DetailImageMutationOutcome:
    body_dict = dict(body or {})
    lang, err = parse_lang_fn(body_dict)
    if err:
        return DetailImageMutationOutcome(error=err, status_code=400)
    return reorder_detail_images(
        product_id,
        lang or "",
        body_dict.get("ids") or [],
        reorder_detail_images=reorder_detail_images_fn,
    )


def _best_effort_delete(object_key: object, delete_media_object: Callable[[str], object]) -> None:
    key = str(object_key or "").strip()
    if not key:
        return
    try:
        delete_media_object(key)
    except Exception:
        pass


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
