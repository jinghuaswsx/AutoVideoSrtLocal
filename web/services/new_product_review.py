"""Service responses for new product review routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class NewProductReviewResponse:
    payload: dict[str, Any]
    status_code: int


def new_product_review_flask_response(result: NewProductReviewResponse):
    return jsonify(result.payload), result.status_code


def build_new_product_review_admin_required_response() -> NewProductReviewResponse:
    return NewProductReviewResponse({"error": "仅管理员可访问"}, 403)


def build_new_product_review_list_response(
    *,
    products: list[dict[str, Any]],
    languages: list[dict[str, Any]],
    translators: list[dict[str, Any]],
) -> NewProductReviewResponse:
    return NewProductReviewResponse(
        {
            "products": products,
            "languages": languages,
            "translators": translators,
        },
        200,
    )


def build_new_product_review_success_response(result: dict[str, Any]) -> NewProductReviewResponse:
    return NewProductReviewResponse(result, 200)


def build_new_product_review_error_response(
    error: str,
    detail: str,
    status_code: int,
) -> NewProductReviewResponse:
    return NewProductReviewResponse({"error": error, "detail": detail}, status_code)
