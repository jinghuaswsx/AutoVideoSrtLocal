"""Service helpers for media product owner changes."""

from __future__ import annotations

from dataclasses import dataclass

from flask import jsonify

from appcore import medias


@dataclass(frozen=True)
class ProductOwnerUpdateResponse:
    payload: dict
    status_code: int
    not_found: bool = False


def build_product_owner_update_response(
    product_id: int,
    body: dict | None,
    *,
    is_admin: bool,
    get_product_fn=None,
    update_product_owner_fn=None,
    get_user_display_name_fn=None,
) -> ProductOwnerUpdateResponse:
    if not is_admin:
        return ProductOwnerUpdateResponse({"error": "仅管理员可操作"}, 403)

    body = body or {}
    raw_uid = body.get("user_id")
    try:
        new_uid = int(raw_uid)
    except (TypeError, ValueError):
        return ProductOwnerUpdateResponse({"error": "user_id required"}, 400)

    get_product_fn = get_product_fn or medias.get_product
    update_product_owner_fn = update_product_owner_fn or medias.update_product_owner
    get_user_display_name_fn = get_user_display_name_fn or medias.get_user_display_name

    product = get_product_fn(product_id)
    if not product or product.get("deleted_at") is not None:
        return ProductOwnerUpdateResponse({}, 404, not_found=True)

    try:
        update_product_owner_fn(product_id, new_uid)
    except ValueError as exc:
        msg = str(exc)
        if msg == "product not found":
            return ProductOwnerUpdateResponse({}, 404, not_found=True)
        return ProductOwnerUpdateResponse({"error": msg}, 400)

    owner_name = get_user_display_name_fn(new_uid)
    return ProductOwnerUpdateResponse({"user_id": new_uid, "owner_name": owner_name}, 200)


def product_owner_update_flask_response(result: ProductOwnerUpdateResponse):
    return jsonify(result.payload), result.status_code
