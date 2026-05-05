"""Service helpers for media XMYC SKU responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from appcore import product_roas, sku_aggregates, xmyc_storage


@dataclass(frozen=True)
class XmycSkuResponse:
    payload: dict
    status_code: int
    not_found: bool = False


def build_xmyc_skus_list_response(
    args: Mapping[str, str],
    *,
    list_skus_fn: Callable[..., list[dict]] = xmyc_storage.list_skus,
    get_configured_rmb_per_usd_fn: Callable[[], float] = product_roas.get_configured_rmb_per_usd,
    enrich_skus_with_roas_fn: Callable[[list[dict], float], list[dict]] = sku_aggregates.enrich_skus_with_roas,
) -> XmycSkuResponse:
    keyword = (args.get("keyword") or "").strip() or None
    matched_filter = (args.get("matched") or "all").strip().lower()
    if matched_filter not in ("all", "matched", "unmatched"):
        matched_filter = "all"
    try:
        limit = max(1, min(500, int(args.get("limit") or 200)))
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        return XmycSkuResponse({"error": "invalid_pagination"}, 400)

    rows = list_skus_fn(
        keyword=keyword,
        matched_filter=matched_filter,
        limit=limit,
        offset=offset,
    )
    rate = get_configured_rmb_per_usd_fn()
    rows = enrich_skus_with_roas_fn(rows, rate)
    return XmycSkuResponse({"ok": True, "items": rows, "limit": limit, "offset": offset}, 200)


def build_product_xmyc_skus_response(
    product_id: int,
    *,
    get_skus_for_product_fn: Callable[[int], list[dict]] = xmyc_storage.get_skus_for_product,
    get_configured_rmb_per_usd_fn: Callable[[], float] = product_roas.get_configured_rmb_per_usd,
    enrich_skus_with_roas_fn: Callable[[list[dict], float], list[dict]] = sku_aggregates.enrich_skus_with_roas,
) -> XmycSkuResponse:
    rows = get_skus_for_product_fn(product_id)
    rate = get_configured_rmb_per_usd_fn()
    rows = enrich_skus_with_roas_fn(rows, rate)
    return XmycSkuResponse({"ok": True, "items": rows}, 200)


def build_product_xmyc_skus_set_response(
    product_id: int,
    body: dict | None,
    *,
    matched_by: int | None,
    set_product_skus_fn: Callable[..., dict] = xmyc_storage.set_product_skus,
) -> XmycSkuResponse:
    body = body or {}
    raw_skus = body.get("skus") or []
    if not isinstance(raw_skus, list):
        return XmycSkuResponse({"error": "skus_must_be_list"}, 400)
    skus = [str(sku).strip() for sku in raw_skus if str(sku).strip()]
    result = set_product_skus_fn(product_id, skus, matched_by=matched_by)
    return XmycSkuResponse({"ok": True, **result}, 200)


def build_xmyc_sku_update_response(
    sku_id: int,
    body: dict | None,
    *,
    update_sku_fn: Callable[[int, dict], dict] = xmyc_storage.update_sku,
    get_configured_rmb_per_usd_fn: Callable[[], float] = product_roas.get_configured_rmb_per_usd,
    enrich_skus_with_roas_fn: Callable[[list[dict], float], list[dict]] = sku_aggregates.enrich_skus_with_roas,
) -> XmycSkuResponse:
    body = body or {}
    try:
        row = update_sku_fn(sku_id, body)
    except ValueError as exc:
        return XmycSkuResponse({"error": "invalid_fields", "message": str(exc)}, 400)
    except LookupError:
        return XmycSkuResponse({}, 404, not_found=True)

    rate = get_configured_rmb_per_usd_fn()
    enriched = enrich_skus_with_roas_fn([row], rate)
    return XmycSkuResponse({"ok": True, "item": enriched[0]}, 200)
