"""Response builders for media-product push routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import jsonify

from appcore import pushes


@dataclass(frozen=True)
class MediaPushErrorResponse:
    payload: dict
    status_code: int


def media_push_flask_response(result: MediaPushErrorResponse):
    return jsonify(result.payload), result.status_code


def _status_for_push_result(result: dict) -> int:
    return 200 if result.get("ok") else 502


def build_product_push_admin_required_response() -> MediaPushErrorResponse:
    return MediaPushErrorResponse({"error": "\u4ec5\u7ba1\u7406\u5458\u53ef\u64cd\u4f5c"}, 403)


def build_product_links_push_error_response(exc: Exception) -> MediaPushErrorResponse:
    message = str(exc)
    if isinstance(exc, pushes.ProductNotListedError):
        return MediaPushErrorResponse(
            {"error": "product_not_listed", "message": "产品已下架，不能推送投放链接"},
            409,
        )
    if isinstance(exc, pushes.ProductLinksPushConfigError):
        return MediaPushErrorResponse({"error": message or "push_product_links_config_missing"}, 500)
    if isinstance(exc, pushes.ProductLinksPayloadError):
        return MediaPushErrorResponse({"error": message or "product_links_payload_invalid"}, 400)
    return MediaPushErrorResponse({"error": "product_links_push_failed", "message": message}, 500)


def build_product_links_push_preview_response(
    product: dict,
    *,
    build_preview_fn: Callable[[dict], dict] = pushes.build_product_links_push_preview,
) -> MediaPushErrorResponse:
    try:
        return MediaPushErrorResponse(build_preview_fn(product), 200)
    except Exception as exc:
        return build_product_links_push_error_response(exc)


def build_product_links_push_response(
    product: dict,
    *,
    push_product_links_fn: Callable[[dict], dict] = pushes.push_product_links,
) -> MediaPushErrorResponse:
    try:
        result = push_product_links_fn(product)
    except Exception as exc:
        return build_product_links_push_error_response(exc)
    return MediaPushErrorResponse(result, _status_for_push_result(result))


def build_product_localized_texts_push_error_response(exc: Exception) -> MediaPushErrorResponse:
    message = str(exc)
    if isinstance(exc, pushes.ProductNotListedError):
        return MediaPushErrorResponse(
            {"error": "product_not_listed", "message": "产品已下架，不能推送小语种文案"},
            409,
        )
    if isinstance(exc, pushes.ProductLocalizedTextsPushConfigError):
        return MediaPushErrorResponse({"error": message or "push_localized_texts_config_missing"}, 500)
    if isinstance(exc, pushes.ProductLocalizedTextsPayloadError):
        return MediaPushErrorResponse({"error": message or "localized_texts_payload_invalid"}, 400)
    return MediaPushErrorResponse(
        {"error": "product_localized_texts_push_failed", "message": message},
        500,
    )


def build_product_localized_texts_push_preview_response(
    product: dict,
    *,
    build_preview_fn: Callable[[dict], dict] = pushes.build_product_localized_texts_push_preview,
) -> MediaPushErrorResponse:
    try:
        return MediaPushErrorResponse(build_preview_fn(product), 200)
    except Exception as exc:
        return build_product_localized_texts_push_error_response(exc)


def build_product_localized_texts_push_response(
    product: dict,
    *,
    push_localized_texts_fn: Callable[[dict], dict] = pushes.push_product_localized_texts,
) -> MediaPushErrorResponse:
    try:
        result = push_localized_texts_fn(product)
    except Exception as exc:
        return build_product_localized_texts_push_error_response(exc)
    return MediaPushErrorResponse(result, _status_for_push_result(result))


def build_product_unsuitable_push_error_response(exc: Exception) -> MediaPushErrorResponse:
    message = str(exc)
    if isinstance(exc, pushes.ProductNotListedError):
        return MediaPushErrorResponse(
            {"error": "product_not_listed", "message": "产品已下架，不能推送不合适标注"},
            409,
        )
    if isinstance(exc, pushes.ProductLocalizedTextsPushConfigError):
        return MediaPushErrorResponse({"error": message or "push_localized_texts_config_missing"}, 500)
    if isinstance(exc, pushes.ProductLinksPushConfigError):
        return MediaPushErrorResponse({"error": message or "push_product_links_config_missing"}, 500)
    if isinstance(exc, pushes.ProductLocalizedTextsPayloadError):
        return MediaPushErrorResponse({"error": message or "localized_texts_payload_invalid"}, 400)
    if isinstance(exc, pushes.ProductLinksPayloadError):
        return MediaPushErrorResponse({"error": message or "product_links_payload_invalid"}, 400)
    return MediaPushErrorResponse({"error": "product_unsuitable_push_failed", "message": message}, 500)


def build_product_unsuitable_push_preview_response(
    product: dict,
    *,
    build_preview_fn: Callable[[dict], dict] = pushes.build_unsuitable_product_push_preview,
) -> MediaPushErrorResponse:
    try:
        return MediaPushErrorResponse(build_preview_fn(product), 200)
    except Exception as exc:
        return build_product_unsuitable_push_error_response(exc)


def build_product_unsuitable_push_response(
    product: dict,
    body: dict | None,
    *,
    push_unsuitable_product_fn: Callable[..., dict] = pushes.push_unsuitable_product,
) -> MediaPushErrorResponse:
    body = body if isinstance(body, dict) else {}
    raw_type = (body.get("type") or "").strip().lower()
    kwargs: dict[str, Any] = {}
    if raw_type in {"copy", "links"}:
        kwargs["only_type"] = raw_type

    try:
        result = push_unsuitable_product_fn(product, **kwargs)
    except Exception as exc:
        return build_product_unsuitable_push_error_response(exc)
    return MediaPushErrorResponse(result, _status_for_push_result(result))
