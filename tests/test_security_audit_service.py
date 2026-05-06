from __future__ import annotations

from datetime import datetime

from web.services.security_audit import (
    build_security_audit_logs_response,
    build_security_audit_media_downloads_response,
)


def test_security_audit_logs_response_serializes_rows():
    result = build_security_audit_logs_response(
        rows=[
            {
                "id": 7,
                "created_at": datetime(2026, 5, 6, 14, 30, 5),
                "detail_json": '{"ip": "127.0.0.1"}',
            }
        ],
        total=12,
        page=2,
        page_size=50,
    )

    assert result.status_code == 200
    assert result.payload == {
        "items": [
            {
                "id": 7,
                "created_at": "2026-05-06 14:30:05",
                "detail_json": {"ip": "127.0.0.1"},
            }
        ],
        "total": 12,
        "page": 2,
        "page_size": 50,
    }


def test_security_audit_media_downloads_response_keeps_unparseable_detail_text():
    result = build_security_audit_media_downloads_response(
        rows=[{"id": 8, "detail_json": b"not-json"}],
        total=1,
        page=1,
        page_size=20,
    )

    assert result.status_code == 200
    assert result.payload["items"][0]["detail_json"] == "not-json"
