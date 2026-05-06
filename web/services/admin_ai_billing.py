"""Service responses for AI usage billing routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from flask import jsonify


@dataclass(frozen=True)
class AdminAiBillingResponse:
    payload: dict[str, Any]
    status_code: int = 200


def admin_ai_billing_flask_response(result: AdminAiBillingResponse):
    return jsonify(result.payload), result.status_code


def build_ai_usage_payload_response(row: Mapping[str, Any] | None) -> AdminAiBillingResponse:
    if not row:
        return AdminAiBillingResponse({"request_data": None, "response_data": None}, 200)
    return AdminAiBillingResponse(
        {
            "request_data": row["request_data"],
            "response_data": row["response_data"],
        },
        200,
    )
