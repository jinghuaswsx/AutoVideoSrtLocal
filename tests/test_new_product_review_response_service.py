from web.services.new_product_review import (
    build_new_product_review_admin_required_response,
    build_new_product_review_error_response,
    build_new_product_review_list_response,
    build_new_product_review_success_response,
)


def test_new_product_review_list_and_permission_response_shapes_are_stable():
    denied = build_new_product_review_admin_required_response()
    assert denied.payload == {"error": "仅管理员可访问"}
    assert denied.status_code == 403
    response = build_new_product_review_list_response(
        products=[{"id": 1}],
        languages=[{"code": "de"}],
        translators=[{"id": 10}],
    )
    assert response.payload == {
        "products": [{"id": 1}],
        "languages": [{"code": "de"}],
        "translators": [{"id": 10}],
    }


def test_new_product_review_result_response_shapes_are_stable():
    assert build_new_product_review_success_response({"status": "evaluated"}).payload == {
        "status": "evaluated"
    }
    not_found = build_new_product_review_error_response("product_not_found", "missing", 404)
    assert not_found.payload == {"error": "product_not_found", "detail": "missing"}
    assert not_found.status_code == 404
    failed = build_new_product_review_error_response("evaluation_failed", "timeout", 500)
    assert failed.payload == {"error": "evaluation_failed", "detail": "timeout"}
    assert failed.status_code == 500
