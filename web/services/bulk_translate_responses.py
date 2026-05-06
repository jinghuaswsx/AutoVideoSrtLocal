"""Shared Flask JSON responses for bulk translation route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class BulkTranslateResponse:
    payload: Any
    status_code: int


def bulk_translate_flask_response(result: BulkTranslateResponse):
    return jsonify(result.payload), result.status_code


def build_bulk_translate_payload_response(
    payload: Any,
    status_code: int = 200,
) -> BulkTranslateResponse:
    return BulkTranslateResponse(payload, status_code)


def build_bulk_translate_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> BulkTranslateResponse:
    return BulkTranslateResponse({"error": error, **extra}, status_code)
