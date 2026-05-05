"""Response builders for media-product push routes."""

from __future__ import annotations

from dataclasses import dataclass

from appcore import pushes


@dataclass(frozen=True)
class MediaPushErrorResponse:
    payload: dict
    status_code: int


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
