"""Service responses for copywriting routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class CopywritingResponse:
    payload: dict[str, Any]
    status_code: int


def copywriting_flask_response(result: CopywritingResponse):
    return jsonify(result.payload), result.status_code


def build_copywriting_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> CopywritingResponse:
    return CopywritingResponse(payload, status_code)


def build_copywriting_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> CopywritingResponse:
    return CopywritingResponse({"error": error, **extra}, status_code)


def build_copywriting_ok_response(
    status_code: int = 200,
    **extra: Any,
) -> CopywritingResponse:
    return CopywritingResponse({"ok": True, **extra}, status_code)
