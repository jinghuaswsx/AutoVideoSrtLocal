from __future__ import annotations

from flask import Flask


def test_order_analytics_flask_response_returns_payload_and_status():
    from web.services.order_analytics_responses import (
        OrderAnalyticsRouteResponse,
        order_analytics_flask_response,
    )

    app = Flask(__name__)
    result = OrderAnalyticsRouteResponse({"ok": True}, 202)

    with app.app_context():
        response, status_code = order_analytics_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"ok": True}


def test_order_analytics_response_builders_preserve_payloads_and_error_extras():
    from web.services.order_analytics_responses import (
        build_order_analytics_error_response,
        build_order_analytics_payload_response,
    )

    payload = build_order_analytics_payload_response({"rows": []})
    error = build_order_analytics_error_response("invalid_date", 400, detail="bad")

    assert payload.status_code == 200
    assert payload.payload == {"rows": []}
    assert error.status_code == 400
    assert error.payload == {"error": "invalid_date", "detail": "bad"}
