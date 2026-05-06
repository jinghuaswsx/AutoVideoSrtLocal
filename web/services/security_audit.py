"""Service responses for superadmin security audit APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class SecurityAuditResponse:
    payload: dict[str, Any]
    status_code: int = 200


def security_audit_flask_response(result: SecurityAuditResponse):
    return jsonify(result.payload), result.status_code


def build_security_audit_logs_response(
    *,
    rows,
    total: int,
    page: int,
    page_size: int,
) -> SecurityAuditResponse:
    return _build_paginated_response(rows=rows, total=total, page=page, page_size=page_size)


def build_security_audit_media_downloads_response(
    *,
    rows,
    total: int,
    page: int,
    page_size: int,
) -> SecurityAuditResponse:
    return _build_paginated_response(rows=rows, total=total, page=page, page_size=page_size)


def _build_paginated_response(
    *,
    rows,
    total: int,
    page: int,
    page_size: int,
) -> SecurityAuditResponse:
    return SecurityAuditResponse(
        {
            "items": [_serialize_row(dict(row)) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        200,
    )


def _json_detail(raw: Any) -> Any:
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def _serialize_row(row: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        elif key == "detail_json":
            out[key] = _json_detail(value)
        else:
            out[key] = value
    return out
