from __future__ import annotations

from web.services.admin_ai_billing import build_ai_usage_payload_response


def test_ai_usage_payload_response_returns_empty_payload_when_row_missing():
    result = build_ai_usage_payload_response(None)

    assert result.status_code == 200
    assert result.payload == {"request_data": None, "response_data": None}


def test_ai_usage_payload_response_returns_payload_columns():
    result = build_ai_usage_payload_response(
        {"request_data": {"prompt": "hello"}, "response_data": {"text": "world"}}
    )

    assert result.status_code == 200
    assert result.payload == {
        "request_data": {"prompt": "hello"},
        "response_data": {"text": "world"},
    }
