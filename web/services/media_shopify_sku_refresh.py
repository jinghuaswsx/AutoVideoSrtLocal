"""Service helpers for refreshing media product Shopify SKU mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RefreshShopifySkuResponse:
    payload: dict
    status_code: int


def build_refresh_product_shopify_sku_response(
    product_id: int,
    product: dict,
    *,
    fetch_shopify_and_dxm_fn: Callable[[], tuple[list[dict], dict]],
    build_pair_rows_fn: Callable[[list[dict], dict], dict],
    update_product_fn: Callable[..., None],
    replace_product_skus_fn: Callable[..., None],
    list_product_skus_fn: Callable[[int], list[dict]],
    list_xmyc_unit_prices_fn: Callable[[list[str]], dict],
    get_configured_rmb_per_usd_fn: Callable[[], float],
    serialize_product_skus_fn: Callable[..., list[dict]],
) -> RefreshShopifySkuResponse:
    shopify_id = (product.get("shopifyid") or "").strip()
    if not shopify_id:
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
        return RefreshShopifySkuResponse(
            {
                "error": "fetch_failed",
                "message": f"店小秘数据拉取失败：{exc}",
            },
            502,
        )

    pair_index = build_pair_rows_fn(shopify_products, dxm_index)
    title_map = {
        (item.get("shopify_product_id") or ""): (item.get("shopify_title") or "")
        for item in shopify_products
    }
    pairs = pair_index.get(shopify_id)
    if pairs is None:
        return RefreshShopifySkuResponse(
            {
                "error": "shopify_product_not_found",
                "message": f"店小秘 Shopify 商品库未找到 shopifyid={shopify_id}",
            },
            404,
        )

    new_title = (title_map.get(shopify_id) or "").strip() or None
    update_product_fn(product_id, shopify_title=new_title)
    replace_product_skus_fn(product_id, pairs, source="manual")

    fresh_skus = list_product_skus_fn(product_id)
    xmyc_index = list_xmyc_unit_prices_fn([
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
        xmyc_index=xmyc_index,
    )
    return RefreshShopifySkuResponse(
        {
            "ok": True,
            "shopify_title": new_title or "",
            "skus": serialized_skus,
            "summary": {
                "variant_pairs": len(pairs),
                "pairs_with_dxm": sum(1 for row in pairs if row.get("dianxiaomi_sku_code")),
            },
        },
        200,
    )
