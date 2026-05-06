from web.services.order_profit import (
    build_order_profit_error_response,
    build_order_profit_ok_response,
    build_order_profit_payload_response,
)


def test_order_profit_payload_response_shape_is_stable():
    response = build_order_profit_payload_response({"orders": [], "total": 0})
    assert response.payload == {"orders": [], "total": 0}
    assert response.status_code == 200


def test_order_profit_error_response_shape_is_stable():
    response = build_order_profit_error_response(
        "order_not_found",
        404,
        dxm_package_id="pkg-1",
    )
    assert response.payload == {
        "error": "order_not_found",
        "dxm_package_id": "pkg-1",
    }
    assert response.status_code == 404


def test_order_profit_ok_response_shape_is_stable():
    response = build_order_profit_ok_response(stats={"inserted": 2})
    assert response.payload == {"ok": True, "stats": {"inserted": 2}}
    assert response.status_code == 200
