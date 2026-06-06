from __future__ import annotations

from datetime import date
import appcore.media_product_ad_orders_report as report

def test_get_product_ad_orders_report_none_product(monkeypatch):
    monkeypatch.setattr(report, "query_one", lambda sql, params: None)
    res = report.get_product_ad_orders_report(999)
    assert res == {"total": {}, "by_lang": {}}

def test_get_product_ad_orders_report_aggregation(monkeypatch):
    queries = []
    
    def fake_query_one(sql, params=()):
        if "media_products" in sql:
            return {"id": 10, "product_code": "PROD10", "name": "Test Product"}
        if "TABLES" in sql or "meta_ad_realtime_daily_ad_metrics" in str(params):
            return {"ok": 1}
        return None

    def fake_query(sql, params):
        queries.append((sql, params))
        if "order_profit_lines" in sql:
            return [
                # today orders (DE)
                {"product_id": 10, "buyer_country": "DE", "business_date": date(2026, 6, 5), "order_count": 2},
                # yesterday orders (DE)
                {"product_id": 10, "buyer_country": "DE", "business_date": date(2026, 6, 4), "order_count": 1},
                # 7d orders (FR)
                {"product_id": 10, "buyer_country": "FR", "business_date": date(2026, 6, 1), "order_count": 3},
                # 30d orders (US)
                {"product_id": 10, "buyer_country": "US", "business_date": date(2026, 5, 15), "order_count": 5},
                # total orders (unknown country)
                {"product_id": 10, "buyer_country": "XX", "business_date": date(2026, 5, 1), "order_count": 1},
            ]
        elif "meta_ad_daily_ad_metrics" in sql:
            return [
                # yesterday spend (DE)
                {"product_id": 10, "ad_account_id": "123", "activity_date": date(2026, 6, 4), "spend_usd": 10.0, "purchase_value_usd": 25.0, "market_country": "DE", "id": 1},
                # 7d spend (FR)
                {"product_id": 10, "ad_account_id": "123", "activity_date": date(2026, 6, 1), "spend_usd": 20.0, "purchase_value_usd": 40.0, "market_country": "FR", "id": 2},
                # 30d spend (US)
                {"product_id": 10, "ad_account_id": "123", "activity_date": date(2026, 5, 15), "spend_usd": 50.0, "purchase_value_usd": 100.0, "market_country": "US", "id": 3},
            ]
        elif "meta_ad_realtime_daily_ad_metrics" in sql:
            return [
                # today spend (DE)
                {"product_id": 10, "ad_account_id": "123", "activity_date": date(2026, 6, 5), "spend_usd": 5.0, "purchase_value_usd": 15.0, "market_country": "DE", "id": 100},
            ]
        return []

    monkeypatch.setattr(report, "query_one", fake_query_one)
    monkeypatch.setattr(report, "query", fake_query)

    res = report.get_product_ad_orders_report(10, today=date(2026, 6, 5))

    total = res["total"]
    assert total["today_spend"] == 5.0
    assert total["today_orders"] == 2
    assert total["today_roas"] == 3.0

    assert total["yesterday_spend"] == 10.0
    assert total["yesterday_orders"] == 1
    assert total["yesterday_roas"] == 2.5

    assert total["last_7d_spend"] == 35.0
    assert total["last_7d_orders"] == 6
    assert total["last_7d_roas"] == 2.29

    assert total["last_30d_spend"] == 85.0
    assert total["last_30d_orders"] == 11
    assert total["last_30d_roas"] == 2.12

    assert total["total_spend"] == 85.0
    assert total["total_orders"] == 12
    assert total["total_roas"] == 2.12

    de = res["by_lang"]["de"]
    assert de["today_spend"] == 5.0
    assert de["today_orders"] == 2
    assert de["today_roas"] == 3.0
    assert de["yesterday_spend"] == 10.0
    assert de["yesterday_orders"] == 1
    assert de["yesterday_roas"] == 2.5
