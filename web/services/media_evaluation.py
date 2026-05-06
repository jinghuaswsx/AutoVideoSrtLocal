"""Service helpers for media AI evaluation responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
    evaluate_product_fn: Callable[..., dict] = material_evaluation.evaluate_product_if_ready,
    material_evaluation_message_fn: Callable[[dict], str],
) -> MediaEvaluationResponse:
    result = evaluate_product_fn(product_id, force=True, manual=True)
    message = material_evaluation_message_fn(result)
    ok = result.get("status") == "evaluated"
    payload = {"ok": ok, "message": message, "result": result}
    if ok:
        return MediaEvaluationResponse(payload, 200)
    return MediaEvaluationResponse({**payload, "error": message}, 400)


def build_product_evaluation_preview_response(
    product_id: int,
    *,
    build_request_debug_payload_fn: Callable[..., dict] = material_evaluation.build_request_debug_payload,
) -> MediaEvaluationResponse:
    try:
        payload = build_request_debug_payload_fn(product_id, include_base64=False)
    except ValueError as exc:
        return MediaEvaluationResponse({"ok": False, "error": str(exc)}, 400)
    payload["full_payload_url"] = f"/medias/api/products/{product_id}/evaluate/request-payload"
    return MediaEvaluationResponse({"ok": True, "payload": payload}, 200)


def build_product_evaluation_payload_response(
    product_id: int,
    *,
    build_request_debug_payload_fn: Callable[..., dict] = material_evaluation.build_request_debug_payload,
) -> MediaEvaluationResponse:
    try:
        payload = build_request_debug_payload_fn(product_id, include_base64=True)
    except ValueError as exc:
        return MediaEvaluationResponse({"ok": False, "error": str(exc)}, 400)
    return MediaEvaluationResponse({"ok": True, "payload": payload}, 200)
