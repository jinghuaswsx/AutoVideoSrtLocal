"""Service helpers for media product detail responses."""

from __future__ import annotations

from collections.abc import Callable

from appcore import medias, product_roas


SerializeProductFn = Callable[..., dict]
SerializeItemFn = Callable[[dict, dict[int, dict]], dict]


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
    serialize_product_fn: SerializeProductFn | None = None,
    serialize_item_fn: SerializeItemFn | None = None,
) -> dict:
    get_product_covers_fn = get_product_covers_fn or medias.get_product_covers
    list_items_fn = list_items_fn or medias.list_items
    list_raw_sources_fn = list_raw_sources_fn or medias.list_raw_sources
    list_product_skus_fn = list_product_skus_fn or medias.list_product_skus
    list_xmyc_unit_prices_fn = list_xmyc_unit_prices_fn or medias.list_xmyc_unit_prices
    list_copywritings_fn = list_copywritings_fn or medias.list_copywritings
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
