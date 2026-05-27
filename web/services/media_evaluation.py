"""Service helpers for media AI evaluation responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from flask import jsonify

from appcore import material_evaluation


@dataclass(frozen=True)
class MediaEvaluationResponse:
    payload: dict
    status_code: int


def media_evaluation_flask_response(result: MediaEvaluationResponse):
    return jsonify(result.payload), result.status_code


def build_product_evaluation_response(
    product_id: int,
    *,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
    evaluate_product_fn: Callable[..., dict] = material_evaluation.evaluate_product_if_ready,
    material_evaluation_message_fn: Callable[[dict], str],
) -> MediaEvaluationResponse:
    kwargs = {"force": True, "manual": True}
    if media_item_id is not None:
        kwargs["media_item_id"] = int(media_item_id)
    if product_url_override:
        kwargs["product_url_override"] = str(product_url_override)
    result = evaluate_product_fn(product_id, **kwargs)
    message = material_evaluation_message_fn(result)
    ok = result.get("status") == "evaluated"
    payload = {"ok": ok, "message": message, "result": result}
    detail = result.get("ai_evaluation_detail") or result.get("detail")
    if ok and detail:
        payload["ai_evaluation_detail"] = detail
    if ok:
        return MediaEvaluationResponse(payload, 200)
    return MediaEvaluationResponse({**payload, "error": message}, 400)


def build_product_evaluation_preview_response(
    product_id: int,
    *,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
    build_request_debug_payload_fn: Callable[..., dict] = material_evaluation.build_request_debug_payload,
) -> MediaEvaluationResponse:
    try:
        kwargs = {"include_base64": False}
        if media_item_id is not None:
            kwargs["media_item_id"] = int(media_item_id)
        if product_url_override:
            kwargs["product_url_override"] = str(product_url_override)
        payload = build_request_debug_payload_fn(product_id, **kwargs)
    except ValueError as exc:
        return MediaEvaluationResponse({"ok": False, "error": str(exc)}, 400)
    query = {}
    if media_item_id is not None:
        query["media_item_id"] = int(media_item_id)
    if product_url_override:
        query["product_link"] = str(product_url_override)
    suffix = f"?{urlencode(query)}" if query else ""
    payload["full_payload_url"] = f"/medias/api/products/{product_id}/evaluate/request-payload{suffix}"
    return MediaEvaluationResponse({"ok": True, "payload": payload}, 200)


def build_product_evaluation_payload_response(
    product_id: int,
    *,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
    build_request_debug_payload_fn: Callable[..., dict] = material_evaluation.build_request_debug_payload,
) -> MediaEvaluationResponse:
    try:
        kwargs = {"include_base64": True}
        if media_item_id is not None:
            kwargs["media_item_id"] = int(media_item_id)
        if product_url_override:
            kwargs["product_url_override"] = str(product_url_override)
        payload = build_request_debug_payload_fn(product_id, **kwargs)
    except ValueError as exc:
        return MediaEvaluationResponse({"ok": False, "error": str(exc)}, 400)
    return MediaEvaluationResponse({"ok": True, "payload": payload}, 200)
