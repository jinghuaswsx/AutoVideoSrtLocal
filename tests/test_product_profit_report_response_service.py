from web.services.product_profit_report import (
    build_product_profit_report_error_response,
    build_product_profit_report_payload_response,
    product_profit_report_flask_response,
)


def test_product_profit_report_flask_response_returns_payload_and_status(authed_client_no_db):
    result = build_product_profit_report_payload_response({"products": []})

    with authed_client_no_db.application.app_context():
        response, status_code = product_profit_report_flask_response(result)

    assert status_code == 200
    assert response.get_json() == {"products": []}


def test_product_profit_report_error_response_supports_status_and_extra():
    result = build_product_profit_report_error_response(
        "invalid store_code",
        400,
        hint="use letters",
    )

    assert result.payload == {"error": "invalid store_code", "hint": "use letters"}
    assert result.status_code == 400
