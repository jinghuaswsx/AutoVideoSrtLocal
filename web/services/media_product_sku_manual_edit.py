"""Service helpers for manually editing media product SKU rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import jsonify

from appcore import medias, product_roas


@dataclass(frozen=True)
class ProductSkuUpdateResponse:
    payload: dict
    status_code: int
    not_found: bool = False


def build_product_sku_update_response(
    product_id: int,
    sku_id: int,
    product: dict,
    body: dict | None,
    *,
    edited_by: int | None,
    update_product_sku_fn: Callable[..., dict] = medias.update_product_sku_manual,
    normalize_fields_fn: Callable[[dict], dict] = medias.normalize_product_sku_manual_update,
    list_xmyc_unit_prices_fn: Callable[[list[str]], dict] = medias.list_xmyc_unit_prices,
    get_configured_rmb_per_usd_fn: Callable[[], float] = product_roas.get_configured_rmb_per_usd,
    serialize_product_skus_fn: Callable[..., list[dict]] | None = None,
) -> ProductSkuUpdateResponse:
    body = body or {}
    try:
        fields = normalize_fields_fn(body)
    except ValueError as exc:
        return ProductSkuUpdateResponse(
            {"error": "invalid_fields", "message": str(exc)},
            400,
        )

    try:
        row = update_product_sku_fn(
            product_id,
            sku_id,
            fields,
            edited_by=edited_by,
        )
    except ValueError as exc:
        return ProductSkuUpdateResponse(
            {"error": "invalid_fields", "message": str(exc)},
            400,
        )
    except LookupError:
        return ProductSkuUpdateResponse({}, 404, not_found=True)

    if serialize_product_skus_fn is None:
        from web.routes.medias._serializers import _serialize_product_skus

        serialize_product_skus_fn = _serialize_product_skus

    dxm_sku = (row.get("dianxiaomi_sku") or "").strip()
    xmyc_index = list_xmyc_unit_prices_fn([dxm_sku] if dxm_sku else [])
    cost_inputs = {
        "purchase_price": product.get("purchase_price"),
        "packet_cost_estimated": product.get("packet_cost_estimated"),
        "packet_cost_actual": product.get("packet_cost_actual"),
        "standalone_shipping_fee": product.get("standalone_shipping_fee"),
    }
    serialized = serialize_product_skus_fn(
        [row],
        cost_inputs=cost_inputs,
        rmb_per_usd=get_configured_rmb_per_usd_fn(),
        xmyc_index=xmyc_index,
    )
    return ProductSkuUpdateResponse({"ok": True, "item": serialized[0]}, 200)


def product_sku_update_flask_response(result: ProductSkuUpdateResponse):
    return jsonify(result.payload), result.status_code
