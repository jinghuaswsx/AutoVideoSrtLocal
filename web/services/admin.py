"""Service responses for admin routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class AdminResponse:
    payload: dict[str, Any]
    status_code: int


def admin_flask_response(result: AdminResponse):
    return jsonify(result.payload), result.status_code


def build_admin_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> AdminResponse:
    return AdminResponse(payload, status_code)


def build_admin_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> AdminResponse:
    return AdminResponse({"error": error, **extra}, status_code)


def build_admin_ok_response(
    status_code: int = 200,
    **extra: Any,
) -> AdminResponse:
    return AdminResponse({"ok": True, **extra}, status_code)
