from __future__ import annotations

from datetime import date, datetime
from appcore import ad_alerts


def _unwrap(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


import pytest


@pytest.fixture(autouse=True)
def mock_get_threshold(monkeypatch):
    monkeypatch.setattr(ad_alerts, "get_threshold", lambda: 1.5)


def test_get_aggregated_products(monkeypatch):
    # Mock get_alerts to return a predefined list of AlertItems
    item1 = ad_alerts.AlertItem(
        product_id=10,
        product_code="ABC123",
        product_name="Product 10",
        lang="de",
        store_codes=["DE01"],
        ad_spend_usd=100.0,
        purchase_value_usd=40.0,
        ad_roas=0.4,
        active_7d_ad_spend_usd=12.0,
        delivery_status="active",
        ad_roas_7d=0.5,
        computed_at="2026-06-11T08:00:00",
        severity=ad_alerts.Severity.SEVERE,
        trend=ad_alerts.TrendDirection.WORSENING,
        phase=ad_alerts.Phase.STABLE,
        conclusion="建议关停",
        reason="ROAS 低于 1.0",
        estimated_loss=-60.0,
        active_days=10,
    )
    item2 = ad_alerts.AlertItem(
        product_id=10,
        product_code="ABC123",
        product_name="Product 10",
        lang="fr",
        store_codes=["FR01"],
        ad_spend_usd=200.0,
        purchase_value_usd=160.0,
        ad_roas=0.8,
        active_7d_ad_spend_usd=20.0,
        delivery_status="active",
        ad_roas_7d=0.7,
        computed_at="2026-06-11T09:00:00",
        severity=ad_alerts.Severity.MODERATE,
        trend=ad_alerts.TrendDirection.STABLE,
        phase=ad_alerts.Phase.STABLE,
        conclusion="建议优化",
        reason="ROAS 低于 1.3",
        estimated_loss=-40.0,
        active_days=15,
    )

    monkeypatch.setattr(ad_alerts, "get_alerts", lambda **kwargs: [item1, item2])

    res = ad_alerts.get_aggregated_products(threshold=1.5)
    assert len(res) == 1
    p = res[0]
    assert p.product_id == 10
    assert p.product_code == "ABC123"
    assert p.ad_spend_usd == 300.0
    assert p.purchase_value_usd == 200.0
    assert p.ad_roas == 0.6667
    assert p.active_7d_ad_spend_usd == 32.0
    assert p.estimated_loss == -100.0
    assert p.max_severity == "severe"
    assert p.max_severity_label == "严重"
    assert p.active_days == 15
    assert len(p.alert_languages) == 2


def test_get_product_alert_details(monkeypatch):
    # Mock query_one for product lookup
    monkeypatch.setattr(
        ad_alerts,
        "query_one",
        lambda sql, params=None: {"id": 10, "product_code": "ABC123", "name": "Product 10"}
    )

    # Mock query to return summary cache row and ad metrics rows
    def fake_query(sql, params=None):
        if "media_product_lang_ad_summary_cache" in sql:
            return [
                {
                    "product_id": 10,
                    "lang": "de",
                    "ad_spend_usd": "100.00",
                    "purchase_value_usd": "40.00",
                    "ad_roas": "0.4000",
                    "active_7d_ad_spend_usd": "12.00",
                    "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                    "product_code": "ABC123",
                    "product_name": "Product 10",
                    "store_code": "DE01",
                }
            ]
        elif "meta_ad_realtime_daily_ad_metrics" in sql and "EXISTS" not in sql:
            return []  # today's realtime ads
        else:
            # daily ad metrics
            return [
                {
                    "ad_code": "ad_1",
                    "ad_name": "Ad One",
                    "ad_account_id": "acc_1",
                    "ad_account_name": "Account One",
                    "first_active_date": date(2026, 6, 1),
                    "last_active_date": date(2026, 6, 10),
                    "ad_spend_usd": 150.0,
                    "purchase_value_usd": 75.0,
                    "active_days": 10,
                }
            ]

    monkeypatch.setattr(ad_alerts, "query", fake_query)
    
    # Mock get_alert_detail to avoid full deep lookup
    monkeypatch.setattr(
        ad_alerts,
        "get_alert_detail",
        lambda product_id, lang, threshold=None: ad_alerts.AlertDetail(
            product_id=product_id,
            product_code="ABC123",
            product_name="Product 10",
            lang=lang,
            lang_label="德语",
            store_codes=["DE01"],
            ad_spend_usd=100.0,
            purchase_value_usd=40.0,
            ad_roas=0.4,
            active_7d_ad_spend_usd=12.0,
            estimated_loss=-60.0,
            delivery_start_time="2026-06-01",
            delivery_end_time="2026-06-10",
            active_days=10,
            computed_at="2026-06-11T08:00:00",
            judgment=ad_alerts.Judgment(ad_alerts.Severity.SEVERE, ad_alerts.TrendDirection.STABLE, ad_alerts.Phase.STABLE, "建议关停", "ROAS低于1.0"),
            trend=[]
        )
    )

    details = ad_alerts.get_product_alert_details(10)
    assert details["product_id"] == 10
    assert details["product_code"] == "ABC123"
    assert len(details["countries"]) == 1
    assert len(details["ads"]) == 1
    assert details["ads"][0]["ad_code"] == "ad_1"
    assert details["ads"][0]["ad_roas"] == 0.5


def test_api_products_route(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    # Mock get_aggregated_products
    item = ad_alerts.AggregatedProductAlert(
        product_id=10,
        product_code="ABC123",
        product_name="Product 10",
        store_codes=["DE01"],
        ad_spend_usd=100.0,
        purchase_value_usd=40.0,
        ad_roas=0.4,
        active_7d_ad_spend_usd=12.0,
        estimated_loss=-60.0,
        max_severity="severe",
        max_severity_label="严重",
        alert_languages=[],
        alert_count=1,
        active_days=10,
        computed_at="2026-06-11T08:00:00"
    )
    monkeypatch.setattr(route.ad_alerts, "get_aggregated_products", lambda **kwargs: [item])

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/products?threshold=1.5"):
        response = _unwrap(route.api_products)()

    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["product_id"] == 10
    assert payload["items"][0]["max_severity_label"] == "严重"


def test_ad_detail_page_route(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    # Mock get_ad_detail_and_trend
    monkeypatch.setattr(
        route.ad_alerts,
        "get_ad_detail_and_trend",
        lambda pid, code, acc_id: {
            "product_id": pid,
            "ad_code": code,
            "ad_name": "Test Ad",
            "ad_account_id": acc_id,
            "ad_account_name": "Test Acc",
            "first_active_date": "2026-06-01",
            "last_active_date": "2026-06-10",
            "metrics": {},
            "trend": []
        }
    )

    flask_app = Flask(__name__)
    # Setup dummy templates directory so render_template doesn't fail immediately in tests
    # Actually, we can mock render_template or create a test context
    monkeypatch.setattr(route, "render_template", lambda template_name, **context: template_name)

    with flask_app.test_request_context("/ad-alerts/product/10/ad/ad_1?ad_account_id=acc_1"):
        response = _unwrap(route.ad_detail_page)(10, "ad_1")

    assert response == "ad_alerts_ad_detail.html"
