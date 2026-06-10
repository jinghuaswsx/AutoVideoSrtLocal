from unittest.mock import patch
import pytest

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    from web.app import create_app
    app = create_app()
    return app.test_client()

def test_order_trend_routes_unauthenticated_302(client):
    """Verify that unauthenticated users are redirected (302) to the login page."""
    routes = [
        "/order-analytics/dxm-orders-view/order-trend",
        "/order-analytics/dxm-orders-view/order-trend/test-product-code"
    ]
    for route in routes:
        resp = client.get(route)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

def test_order_trend_routes_authenticated_200(authed_client_no_db):
    """Verify that authenticated users can access the trend sub-tab page."""
    with patch("appcore.meta_ad_accounts.get_all_accounts", return_value=[]), \
         patch("appcore.meta_ad_accounts.AVAILABLE_STORE_CODES", {"newjoy", "omurio"}):
        resp = authed_client_no_db.get("/order-analytics/dxm-orders-view/order-trend")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'var initialTab = "dxmOrders";' in html
        assert 'id="dxmProductSearchInput"' in html

def test_order_trend_detail_route_authenticated_200(authed_client_no_db):
    """Verify that authenticated users can load the product detail trend page with mock data."""
    mock_trend_data = {
        "product_code": "test-product-code",
        "product_name": "Mock Test Product Name",
        "product_id": 123,
        "daily": [
            {
                "date": "2026-06-01",
                "units": 10,
                "orders": 8,
                "sales": 200.0,
                "spend": 50.0,
                "purchase_value": 100.0,
                "meta_roas": 2.0,
                "real_roas": 4.0
            }
        ],
        "weekly": [
            {
                "label": "W22 (06-01 ~ 06-07)",
                "units": 10,
                "orders": 8,
                "sales": 200.0,
                "start_date": "2026-06-01",
                "end_date": "2026-06-07"
            }
        ],
        "monthly": [
            {
                "label": "2026-06",
                "units": 10,
                "orders": 8,
                "sales": 200.0
            }
        ]
    }
    with patch("appcore.order_analytics.get_product_order_trend_data", return_value=mock_trend_data):
        resp = authed_client_no_db.get("/order-analytics/dxm-orders-view/order-trend/test-product-code")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Mock Test Product Name" in html
        assert "test-product-code" in html
        assert "2026-06-01" in html
        assert "W22" in html
