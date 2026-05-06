"""Service helpers for media product list responses."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from flask import jsonify

from appcore import medias, product_roas


DEFAULT_PAGE_SIZE = 20


SerializeProductFn = Callable[..., dict]


def products_list_flask_response(payload: dict):
    return jsonify(payload)


def _request_arg(args: Mapping[str, Any], name: str, default: str = "") -> Any:
    getter = getattr(args, "get", None)
    if callable(getter):
        return getter(name, default)
    return args[name] if name in args else default


def _default_serialize_product(*args, **kwargs) -> dict:
    from web.routes.medias._serializers import _serialize_product

    return _serialize_product(*args, **kwargs)


def build_products_list_response(
    args: Mapping[str, Any],
    *,
    list_products_fn=None,
    count_items_by_product_fn=None,
    count_raw_sources_by_product_fn=None,
    first_thumb_item_by_product_fn=None,
    list_item_filenames_by_product_fn=None,
    lang_coverage_by_product_fn=None,
    get_product_covers_batch_fn=None,
    list_product_skus_batch_fn=None,
    list_xmyc_unit_prices_fn=None,
    get_configured_rmb_per_usd_fn=None,
    serialize_product_fn: SerializeProductFn | None = None,
) -> dict:
    list_products_fn = list_products_fn or medias.list_products
    count_items_by_product_fn = count_items_by_product_fn or medias.count_items_by_product
    count_raw_sources_by_product_fn = (
        count_raw_sources_by_product_fn or medias.count_raw_sources_by_product
    )
    first_thumb_item_by_product_fn = (
        first_thumb_item_by_product_fn or medias.first_thumb_item_by_product
    )
    list_item_filenames_by_product_fn = (
        list_item_filenames_by_product_fn or medias.list_item_filenames_by_product
    )
    lang_coverage_by_product_fn = lang_coverage_by_product_fn or medias.lang_coverage_by_product
    get_product_covers_batch_fn = (
        get_product_covers_batch_fn or medias.get_product_covers_batch
    )
    list_product_skus_batch_fn = list_product_skus_batch_fn or medias.list_product_skus_batch
    list_xmyc_unit_prices_fn = list_xmyc_unit_prices_fn or medias.list_xmyc_unit_prices
    get_configured_rmb_per_usd_fn = (
        get_configured_rmb_per_usd_fn or product_roas.get_configured_rmb_per_usd
    )

    keyword = str(_request_arg(args, "keyword", "") or "").strip()
    archived = _request_arg(args, "archived", "") in ("1", "true", "yes")
    page = max(1, int(_request_arg(args, "page", 1) or 1))
    limit = DEFAULT_PAGE_SIZE
    offset = (page - 1) * limit

    xmyc_match = str(_request_arg(args, "xmyc_match", "all") or "all").strip().lower()
    if xmyc_match not in medias.XMYC_MATCH_FILTERS:
        xmyc_match = "all"
    roas_status = str(_request_arg(args, "roas_status", "all") or "all").strip().lower()
    if roas_status not in medias.ROAS_STATUS_FILTERS:
        roas_status = "all"

    rows, total = list_products_fn(
        None,
        keyword=keyword,
        archived=archived,
        offset=offset,
        limit=limit,
        xmyc_match=xmyc_match,
        roas_status=roas_status,
    )
    pids = [row["id"] for row in rows]
    counts = count_items_by_product_fn(pids)
    raw_counts = count_raw_sources_by_product_fn(pids)
    thumb_covers = first_thumb_item_by_product_fn(pids)
    filenames = list_item_filenames_by_product_fn(pids, limit_per=5)
    coverage = lang_coverage_by_product_fn(pids)
    covers_map = get_product_covers_batch_fn(pids)
    skus_map = list_product_skus_batch_fn(pids)
    all_dxm_skus = sorted({
        (sku.get("dianxiaomi_sku") or "").strip()
        for sku_rows in skus_map.values()
        for sku in sku_rows
        if (sku.get("dianxiaomi_sku") or "").strip()
    })
    xmyc_index = list_xmyc_unit_prices_fn(all_dxm_skus)
    roas_rmb_per_usd = get_configured_rmb_per_usd_fn()
    serialize_product_fn = serialize_product_fn or _default_serialize_product

    data = [
        serialize_product_fn(
            row,
            counts.get(row["id"], 0),
            thumb_covers.get(row["id"]),
            items_filenames=filenames.get(row["id"], []),
            lang_coverage=coverage.get(row["id"], {}),
            covers=covers_map.get(row["id"], {}),
            raw_sources_count=raw_counts.get(row["id"], 0),
            roas_rmb_per_usd=roas_rmb_per_usd,
            skus=skus_map.get(row["id"], []),
            xmyc_index=xmyc_index,
        )
        for row in rows
    ]
    return {"items": data, "total": total, "page": page, "page_size": limit}
