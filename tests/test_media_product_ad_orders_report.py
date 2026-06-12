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
            return {
                "id": 10, "product_code": "PROD10", "name": "Test Product",
                "purchase_price": 10.0, "packet_cost_estimated": 5.0,
                "packet_cost_actual": 4.5, "standalone_price": 29.9,
                "standalone_shipping_fee": 0.0
            }
        if "TABLES" in sql or "meta_ad_realtime_daily_ad_metrics" in str(params):
            return {"ok": 1}
        return None

    def fake_query(sql, params):
        queries.append((sql, params))
        if "order_profit_lines" in sql:
            return [
                # today orders (DE)
                {"product_id": 10, "buyer_country": "DE", "business_date": date(2026, 6, 5), "order_count": 2,
                 "revenue_usd": 100.0, "shopify_fee_usd": 3.0, "purchase_usd": 20.0, "shipping_cost_usd": 15.0, "return_reserve_usd": 2.0},
                # yesterday orders (DE)
                {"product_id": 10, "buyer_country": "DE", "business_date": date(2026, 6, 4), "order_count": 1,
                 "revenue_usd": 50.0, "shopify_fee_usd": 1.5, "purchase_usd": 10.0, "shipping_cost_usd": 7.5, "return_reserve_usd": 1.0},
                # 7d orders (FR)
                {"product_id": 10, "buyer_country": "FR", "business_date": date(2026, 6, 1), "order_count": 3,
                 "revenue_usd": 150.0, "shopify_fee_usd": 4.5, "purchase_usd": 30.0, "shipping_cost_usd": 22.5, "return_reserve_usd": 3.0},
                # 30d orders (US)
                {"product_id": 10, "buyer_country": "US", "business_date": date(2026, 5, 15), "order_count": 5,
                 "revenue_usd": 250.0, "shopify_fee_usd": 7.5, "purchase_usd": 50.0, "shipping_cost_usd": 37.5, "return_reserve_usd": 5.0},
                # total orders (unknown country)
                {"product_id": 10, "buyer_country": "XX", "business_date": date(2026, 5, 1), "order_count": 1,
                 "revenue_usd": 50.0, "shopify_fee_usd": 1.5, "purchase_usd": 10.0, "shipping_cost_usd": 7.5, "return_reserve_usd": 1.0},
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

    assert "breakeven_roas" in res
    assert res["breakeven_roas"] is not None
    assert round(res["breakeven_roas"], 2) == 1.16  # standalone 29.9 / (29.9 * 0.93 - 10/6.83 - 4.5/6.83) = 29.9 / (27.807 - 1.464 - 0.659) = 1.16

    total = res["total"]
    assert total["today_spend"] == 5.0
    assert total["today_orders"] == 2
    assert total["today_roas"] == 3.0
    # Today order profit: 100 - 3 - 20 - 15 - 2 = 60. Spend: 5. Net profit: 55
    assert total["today_profit"] == 55.0

    assert total["yesterday_spend"] == 10.0
    assert total["yesterday_orders"] == 1
    assert total["yesterday_roas"] == 2.5
    # Yesterday order profit: 50 - 1.5 - 10 - 7.5 - 1 = 30. Spend: 10. Net profit: 20
    assert total["yesterday_profit"] == 20.0

    assert total["last_7d_spend"] == 35.0
    assert total["last_7d_orders"] == 6
    assert total["last_7d_roas"] == 2.29
    # 7d order profit: today DE (60) + yesterday DE (30) + 7d FR (150-4.5-30-22.5-3 = 90) = 180. Spend: 35. Net profit: 145
    assert total["last_7d_profit"] == 145.0

    assert total["last_30d_spend"] == 85.0
    assert total["last_30d_orders"] == 11
    assert total["last_30d_roas"] == 2.12
    # 30d order profit: 180 + 30d US (250-7.5-50-37.5-5 = 150) = 330. Spend: 85. Net profit: 245
    assert total["last_30d_profit"] == 245.0
    assert total["last_30d_order_roas"] == 6.47

    assert total["total_spend"] == 85.0
    assert total["total_orders"] == 12
    assert total["total_roas"] == 2.12
    # Total order profit: 330 + total XX (50-1.5-10-7.5-1 = 30) = 360. Spend: 85. Net profit: 275
    assert total["total_profit"] == 275.0
    assert total["total_order_roas"] == 7.06

    de = res["by_lang"]["de"]
    assert de["today_spend"] == 5.0
    assert de["today_orders"] == 2
    assert de["today_roas"] == 3.0
    assert de["today_profit"] == 55.0
    assert de["today_order_roas"] == 20.0
    assert de["yesterday_spend"] == 10.0
    assert de["yesterday_orders"] == 1
    assert de["yesterday_roas"] == 2.5
    assert de["yesterday_profit"] == 20.0
    assert de["yesterday_order_roas"] == 5.0
