"""Service helpers for media product detail responses."""

from __future__ import annotations

from collections.abc import Callable

from flask import jsonify

from appcore import medias, product_roas


SerializeProductFn = Callable[..., dict]
SerializeItemFn = Callable[[dict, dict[int, dict]], dict]


def product_detail_flask_response(payload: dict):
    return jsonify(payload)


def _default_serialize_product(*args, **kwargs) -> dict:
    from web.routes.medias._serializers import _serialize_product

    return _serialize_product(*args, **kwargs)


def _default_serialize_item(*args, **kwargs) -> dict:
    from web.routes.medias._serializers import _serialize_item

    return _serialize_item(*args, **kwargs)


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _items_need_raw_sources(items: list[dict]) -> bool:
    return any(
        _int_or_none(item.get("source_raw_id"))
        or (item.get("auto_translated") and _int_or_none(item.get("source_ref_id")))
        for item in items
    )


def _source_item_name(item: dict | None) -> str:
    if not item:
        return ""
    return str(item.get("display_name") or item.get("filename") or "").strip()


def _source_item_key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _source_raw_id_for_item(item: dict) -> int | None:
    source_raw_id = _int_or_none(item.get("source_raw_id"))
    if source_raw_id is None and item.get("auto_translated"):
        source_raw_id = _int_or_none(item.get("source_ref_id"))
    return source_raw_id


def _source_english_item_payload(item: dict | None) -> dict | None:
    if not item:
        return None
    item_id = _int_or_none(item.get("id"))
    if item_id is None:
        return None
    filename = str(item.get("filename") or "").strip()
    display_name = _source_item_name(item) or filename
    return {
        "id": item_id,
        "filename": filename,
        "display_name": display_name,
        "lang": str(item.get("lang") or "en").strip().lower() or "en",
    }


def _annotate_source_english_items(
    items: list[dict],
    raw_sources_by_id: dict[int, dict],
) -> list[dict]:
    english_items_by_name: dict[str, dict] = {}
    for item in items:
        if str(item.get("lang") or "en").strip().lower() != "en":
            continue
        for name in {item.get("display_name"), item.get("filename")}:
            key = _source_item_key(name)
            if key and key not in english_items_by_name:
                english_items_by_name[key] = item

    annotated: list[dict] = []
    for item in items:
        source_english_item = None
        if str(item.get("lang") or "").strip().lower() != "en":
            source_raw_id = _source_raw_id_for_item(item)
            source_raw = raw_sources_by_id.get(source_raw_id or 0)
            source_name = _source_item_name(source_raw)
            source_english_item = _source_english_item_payload(
                english_items_by_name.get(_source_item_key(source_name))
            )
        annotated.append({**item, "source_english_item": source_english_item})
    return annotated


def build_product_detail_response(
    product_id: int,
    *,
    product: dict,
    get_product_covers_fn=None,
    list_items_fn=None,
    list_raw_sources_fn=None,
    list_product_skus_fn=None,
    list_xmyc_unit_prices_fn=None,
    list_copywritings_fn=None,
    get_configured_rmb_per_usd_fn=None,
    count_item_versions_fn=None,
    serialize_product_fn: SerializeProductFn | None = None,
    serialize_item_fn: SerializeItemFn | None = None,
) -> dict:
    get_product_covers_fn = get_product_covers_fn or medias.get_product_covers
    list_items_fn = list_items_fn or medias.list_items
    list_raw_sources_fn = list_raw_sources_fn or medias.list_raw_sources
    list_product_skus_fn = list_product_skus_fn or medias.list_product_skus
    list_xmyc_unit_prices_fn = list_xmyc_unit_prices_fn or medias.list_xmyc_unit_prices
    list_copywritings_fn = list_copywritings_fn or medias.list_copywritings
    count_item_versions_fn = count_item_versions_fn or medias.count_item_versions
    get_configured_rmb_per_usd_fn = (
        get_configured_rmb_per_usd_fn or product_roas.get_configured_rmb_per_usd
    )
    serialize_product_fn = serialize_product_fn or _default_serialize_product
    serialize_item_fn = serialize_item_fn or _default_serialize_item

    covers = get_product_covers_fn(product_id)
    items = list_items_fn(product_id)
    raw_sources_by_id: dict[int, dict] = {}
    if _items_need_raw_sources(items):
        raw_sources_by_id = {
            int(row["id"]): row
            for row in list_raw_sources_fn(product_id)
            if row.get("id") is not None
        }
    item_ids = [int(item["id"]) for item in items if item.get("id") is not None]
    version_counts = count_item_versions_fn(item_ids)
    items = [
        {
            **item,
            "versions_count": int(version_counts.get(int(item["id"]), 0))
            if item.get("id") is not None else 0,
        }
        for item in items
    ]
    items = _annotate_source_english_items(items, raw_sources_by_id)
    skus = list_product_skus_fn(product_id)
    xmyc_index = list_xmyc_unit_prices_fn(
        [sku.get("dianxiaomi_sku") or "" for sku in skus]
    )

    return {
        "product": serialize_product_fn(
            product,
            None,
            None,
            covers=covers,
            roas_rmb_per_usd=get_configured_rmb_per_usd_fn(),
            skus=skus,
            xmyc_index=xmyc_index,
        ),
        "covers": covers,
        "copywritings": list_copywritings_fn(product_id),
        "items": [serialize_item_fn(item, raw_sources_by_id) for item in items],
    }
