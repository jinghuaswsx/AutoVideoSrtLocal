"""产品盈亏列表（全产品聚合）测试。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from appcore.order_analytics import product_profit_list as ppl


def test_generate_list_empty_returns_empty_rows():
    """无订单数据 → rows=[]，summary 全 0。"""
    with patch.object(ppl, "query") as q, patch.object(ppl, "query_one") as q1:
        q.return_value = []
        q1.return_value = None
        result = ppl.generate_list(
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
            country=None,
        )
    assert result["rows"] == []
    assert result["summary"]["product_count"] == 0
    assert result["summary"]["total_revenue_usd"] == 0
    assert result["summary"]["total_profit_usd"] == 0
    assert result["summary"]["overall_roas"] is None


def test_generate_list_single_product_aggregates_columns():
    """单产品 + 2 笔订单 → 9 列字段全部正确（订单数 / 收入 / 各项费用 / 占比 / ROAS / 利润 / 完备）。"""
    fake_lines = [
        {
            "product_id": 100, "product_code": "ABC", "name": "Test Product",
            "business_date": date(2026, 5, 5),
            "buyer_country": "VN", "site_code": "newjoy",
            "revenue_usd": Decimal("50.00"), "shopify_fee_usd": Decimal("2.00"),
            "purchase_usd": Decimal("10.00"), "shipping_cost_usd": Decimal("3.00"),
            "return_reserve_usd": Decimal("0.50"),
            "quantity": 1,
        },
        {
            "product_id": 100, "product_code": "ABC", "name": "Test Product",
            "business_date": date(2026, 5, 6),
            "buyer_country": "VN", "site_code": "newjoy",
            "revenue_usd": Decimal("50.00"), "shopify_fee_usd": Decimal("2.00"),
            "purchase_usd": Decimal("10.00"), "shipping_cost_usd": Decimal("3.00"),
            "return_reserve_usd": Decimal("0.50"),
            "quantity": 1,
        },
    ]
    fake_ads = {
        # (date, ad_account_id) → spend_usd
        (date(2026, 5, 5), "2110407576446225"): Decimal("8.00"),
        (date(2026, 5, 6), "2110407576446225"): Decimal("8.00"),
    }
    fake_site_units = {
        (date(2026, 5, 5), "newjoy"): 1,
        (date(2026, 5, 6), "newjoy"): 1,
    }
    fake_product_costs = {
        100: {"purchase_price": Decimal("3.00"), "packet_cost_actual": Decimal("1.50")},
    }
    with patch.object(ppl, "_load_lines", return_value=fake_lines), \
         patch.object(ppl, "_load_ad_spend", return_value=fake_ads), \
         patch.object(ppl, "_load_site_units", return_value=fake_site_units), \
         patch.object(ppl, "_load_product_costs", return_value=fake_product_costs):
        result = ppl.generate_list(
            date_from=date(2026, 5, 1), date_to=date(2026, 5, 7), country=None,
        )
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["product_id"] == 100
    assert row["product_code"] == "ABC"
    assert row["order_count"] == 2
    assert row["revenue_usd"] == 100.0
    assert row["ad_cost_usd"] == 16.0      # 全产品占用全站广告
    assert row["roas"] == 100.0 / 16.0
    # profit = 100 - 4 - 16 - 20 - 6 - 1 = 53
    assert row["profit_usd"] == 53.0
    assert row["cost_completeness"] == "ok"
    assert result["summary"]["product_count"] == 1
    assert result["summary"]["total_revenue_usd"] == 100.0
    assert result["summary"]["overall_roas"] == 100.0 / 16.0


def test_generate_list_country_filter_passes_country_to_loader():
    """country='vn' 时 _load_lines 会收到 country 参数（值原样透传，归一化在 loader 内）。"""
    fake_lines = [
        {"product_id": 100, "product_code": "A", "name": "A", "business_date": date(2026, 5, 5),
         "buyer_country": "VN", "site_code": "newjoy", "revenue_usd": Decimal("50"),
         "shopify_fee_usd": Decimal("2"), "purchase_usd": Decimal("10"),
         "shipping_cost_usd": Decimal("3"), "return_reserve_usd": Decimal("0.5"), "quantity": 1},
    ]
    with patch.object(ppl, "_load_lines") as load:
        load.return_value = fake_lines
        with patch.object(ppl, "_load_ad_spend", return_value={}), \
             patch.object(ppl, "_load_site_units", return_value={}), \
             patch.object(ppl, "_load_product_costs", return_value={}):
            ppl.generate_list(date_from=date(2026, 5, 1), date_to=date(2026, 5, 7), country="vn")
    args, kwargs = load.call_args
    flat = list(args) + list(kwargs.values())
    assert "vn" in flat, f"_load_lines 未收到 country 参数：{flat}"


def test_generate_list_load_lines_appends_country_predicate():
    """_load_lines 内部 SQL：country 非空时带上 buyer_country = %s 谓词，参数为大写。"""
    captured: dict[str, Any] = {}

    def _fake_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch.object(ppl, "query", side_effect=_fake_query):
        ppl._load_lines(date(2026, 5, 1), date(2026, 5, 7), "vn")

    assert "opl.buyer_country = %s" in captured["sql"]
    assert "VN" in captured["params"]


def test_generate_list_load_lines_skips_country_predicate_when_blank():
    """country 为 None / "" / "all" 时 SQL 不带 buyer_country 过滤谓词。"""
    for c in (None, "", "all", "ALL"):
        captured: dict[str, Any] = {}

        def _fake_query(sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        with patch.object(ppl, "query", side_effect=_fake_query):
            ppl._load_lines(date(2026, 5, 1), date(2026, 5, 7), c)
        assert "opl.buyer_country = %s" not in captured["sql"], f"country={c!r}"
