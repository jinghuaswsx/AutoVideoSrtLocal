"""Service helpers for refreshing media product Shopify SKU mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import jsonify


@dataclass(frozen=True)
class RefreshShopifySkuResponse:
    payload: dict
    status_code: int


def refresh_shopify_sku_flask_response(result: RefreshShopifySkuResponse):
    return jsonify(result.payload), result.status_code


def _candidate_shopify_ids(
    product_id: int,
    product: dict,
    list_shopify_product_ids_fn: Callable[[int], list[dict]] | None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(value) -> None:
        shopify_id = str(value or "").strip()
        if shopify_id and shopify_id not in seen:
            seen.add(shopify_id)
            out.append(shopify_id)

    add(product.get("shopifyid"))
    rows = None
    if list_shopify_product_ids_fn is not None:
        rows = list_shopify_product_ids_fn(product_id)
    elif isinstance(product.get("shopify_ids"), list):
        rows = product.get("shopify_ids")
    for row in rows or []:
        if isinstance(row, dict):
            add(row.get("shopify_product_id"))
    return out


def build_refresh_product_shopify_sku_response(
    product_id: int,
    product: dict,
    *,
    fetch_shopify_and_dxm_fn: Callable[[], tuple[list[dict], dict]],
    build_pair_rows_fn: Callable[[list[dict], dict], dict],
    update_product_fn: Callable[..., None],
    replace_product_skus_fn: Callable[..., None],
    list_product_skus_fn: Callable[[int], list[dict]],
    list_yuncang_unit_prices_fn: Callable[[list[str]], dict],
    get_configured_rmb_per_usd_fn: Callable[[], float],
    serialize_product_skus_fn: Callable[..., list[dict]],
    record_fetch_failure_fn: Callable[..., int] | None = None,
    list_shopify_product_ids_fn: Callable[[int], list[dict]] | None = None,
) -> RefreshShopifySkuResponse:
    shopify_ids = _candidate_shopify_ids(product_id, product, list_shopify_product_ids_fn)
    if not shopify_ids:
        return RefreshShopifySkuResponse(
            {
                "error": "missing_shopifyid",
                "message": "该产品尚未关联 Shopify ID，无法刷新 SKU/英文名",
            },
            400,
        )

    try:
        shopify_products, dxm_index = fetch_shopify_and_dxm_fn()
    except Exception as exc:
        message = f"店小秘数据拉取失败：{exc}"
        if record_fetch_failure_fn is not None:
            try:
                record_fetch_failure_fn(
                    task_code="dianxiaomi_sku",
                    error_message=message,
                    summary={
                        "stage": "manual_refresh_fetch",
                        "product_id": int(product_id),
                        "shopifyid": shopify_ids[0] if shopify_ids else "",
                        "shopifyids": shopify_ids,
                    },
                )
            except Exception:
                pass
        return RefreshShopifySkuResponse(
            {
                "error": "fetch_failed",
                "message": message,
            },
            502,
        )

    pair_index = build_pair_rows_fn(shopify_products, dxm_index)
    title_map = {
        (item.get("shopify_product_id") or ""): (item.get("shopify_title") or "")
        for item in shopify_products
    }
    pairs: list[dict] = []
    matched_shopify_ids: list[str] = []
    missing_shopify_ids: list[str] = []
    new_title: str | None = None
    for shopify_id in shopify_ids:
        matched_pairs = pair_index.get(shopify_id)
        if matched_pairs is None:
            missing_shopify_ids.append(shopify_id)
            continue
        matched_shopify_ids.append(shopify_id)
        pairs.extend(matched_pairs)
        if new_title is None:
            new_title = (title_map.get(shopify_id) or "").strip() or None

    if not matched_shopify_ids:
        return RefreshShopifySkuResponse(
            {
                "error": "shopify_product_not_found",
                "message": f"店小秘 Shopify 商品库未找到 shopifyid={','.join(shopify_ids)}",
            },
            404,
        )

    update_product_fn(product_id, shopify_title=new_title)
    replace_product_skus_fn(product_id, pairs, source="manual")

    fresh_skus = list_product_skus_fn(product_id)
    yuncang_index = list_yuncang_unit_prices_fn([
        sku.get("dianxiaomi_sku") or "" for sku in fresh_skus
    ])
    cost_inputs = {
        "purchase_price": product.get("purchase_price"),
        "packet_cost_estimated": product.get("packet_cost_estimated"),
        "packet_cost_actual": product.get("packet_cost_actual"),
        "standalone_shipping_fee": product.get("standalone_shipping_fee"),
    }
    serialized_skus = serialize_product_skus_fn(
        fresh_skus,
        cost_inputs=cost_inputs,
        rmb_per_usd=get_configured_rmb_per_usd_fn(),
        yuncang_index=yuncang_index,
    )
    return RefreshShopifySkuResponse(
        {
            "ok": True,
            "shopify_title": new_title or "",
            "skus": serialized_skus,
            "summary": {
                "variant_pairs": len(pairs),
                "pairs_with_dxm": sum(1 for row in pairs if row.get("dianxiaomi_sku_code")),
                "shopifyids": matched_shopify_ids,
                "missing_shopifyids": missing_shopify_ids,
            },
        },
        200,
    )
