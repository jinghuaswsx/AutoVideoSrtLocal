"""Shared Flask JSON responses for OpenAPI routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class OpenAPIResponse:
    payload: dict[str, Any]
    status_code: int


def openapi_flask_response(result: OpenAPIResponse):
    return jsonify(result.payload), result.status_code


def build_openapi_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> OpenAPIResponse:
    return OpenAPIResponse(payload, status_code)


def build_openapi_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> OpenAPIResponse:
    return OpenAPIResponse({"error": error, **extra}, status_code)
