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


def test_order_analytics_sub_routes_unauthenticated_302(client):
    """验证未登录用户访问各子路由都会被 302 重定向到登录页。"""
    sub_routes = [
        "/order-analytics",
        "/order-analytics/realtime",
        "/order-analytics/new-product-launch",
        "/order-analytics/dxm-orders-view",
        "/order-analytics/ads-view",
        "/order-analytics/ad-accounts-view",
        "/order-analytics/product-dashboard-view",
        "/order-analytics/country-dashboard-view",
        "/order-analytics/true-roas-view",
        "/order-analytics/weekly-roas-view",
        "/order-analytics/import-view",
        "/order-analytics/shopify-analytics-view",
    ]
    for route in sub_routes:
        resp = client.get(route)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def test_order_analytics_sub_routes_authenticated_200(authed_client_no_db):
    """验证已登录用户（且拥有数据分析权限）访问各子路由都返回 200，并正确激活 tab。"""
    sub_routes = {
        "/order-analytics": "realtime",
        "/order-analytics/realtime": "realtime",
        "/order-analytics/new-product-launch": "newProductLaunch",
        "/order-analytics/dxm-orders-view": "dxmOrders",
        "/order-analytics/ads-view": "ads",
        "/order-analytics/ad-accounts-view": "adAccounts",
        "/order-analytics/product-dashboard-view": "dashboard",
        "/order-analytics/country-dashboard-view": "countryDashboard",
        "/order-analytics/true-roas-view": "trueRoas",
        "/order-analytics/weekly-roas-view": "weeklyRoas",
        "/order-analytics/import-view": "import",
        "/order-analytics/shopify-analytics-view": "analytics",
    }
    
    # 模拟以防外部依赖报错
    with patch("appcore.meta_ad_accounts.get_all_accounts", return_value=[]), \
         patch("appcore.meta_ad_accounts.AVAILABLE_STORE_CODES", {"newjoy", "omurio"}):
        for route, expected_tab in sub_routes.items():
            resp = authed_client_no_db.get(route)
            assert resp.status_code == 200
            html = resp.data.decode("utf-8")
            # 验证 HTML 中注入的 active_tab 值是正确的
            assert f'var initialTab = "{expected_tab}";' in html


def test_ads_view_initializes_after_ads_state_and_subtab_hook(authed_client_no_db):
    """直接打开广告分析路由时，不能在广告状态变量初始化前调用 initAds。"""
    with patch("appcore.meta_ad_accounts.get_all_accounts", return_value=[]), \
         patch("appcore.meta_ad_accounts.AVAILABLE_STORE_CODES", {"newjoy", "omurio"}):
        resp = authed_client_no_db.get("/order-analytics/ads-view")

    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    init_call = html.index("initActiveOrderAnalyticsTab();")
    assert html.index('var initialTab = "ads";') < init_call
    assert html.index("var metaAdAccountsState =") < init_call
    assert html.index("var __origInitAds =") < init_call
