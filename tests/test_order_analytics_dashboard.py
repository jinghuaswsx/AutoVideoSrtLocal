from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from appcore import order_analytics as oa


def test_compute_pct_change_normal():
    assert oa._compute_pct_change(120, 100) == 20.0
    assert oa._compute_pct_change(80, 100) == -20.0


def test_compute_pct_change_both_zero():
    assert oa._compute_pct_change(0, 0) == 0.0


def test_compute_pct_change_prev_zero_now_positive():
    # 无法计算百分比时返回 None（前端显示 "新增" 或 "-"）
    assert oa._compute_pct_change(50, 0) is None


def test_compute_pct_change_now_zero_prev_positive():
    assert oa._compute_pct_change(0, 100) == -100.0


def test_compute_pct_change_handles_none_inputs():
    assert oa._compute_pct_change(None, 100) == -100.0
    assert oa._compute_pct_change(100, None) is None
    assert oa._compute_pct_change(None, None) == 0.0


def test_resolve_period_range_full_past_month():
    start, end = oa._resolve_period_range("month", year=2026, month=3, today=date(2026, 4, 26))
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 31)


def test_resolve_period_range_current_month_truncates_to_yesterday():
    start, end = oa._resolve_period_range("month", year=2026, month=4, today=date(2026, 4, 26))
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 25)  # 昨日


def test_resolve_period_range_iso_week():
    # 2026 ISO week 17 = 2026-04-20 (Mon) ~ 2026-04-26 (Sun)
    start, end = oa._resolve_period_range("week", year=2026, week=17, today=date(2026, 5, 1))
    assert start == date(2026, 4, 20)
    assert end == date(2026, 4, 26)


def test_resolve_period_range_current_week_truncates_to_yesterday():
    start, end = oa._resolve_period_range("week", year=2026, week=17, today=date(2026, 4, 23))
    assert start == date(2026, 4, 20)
    assert end == date(2026, 4, 22)


def test_resolve_period_range_day():
    start, end = oa._resolve_period_range("day", date_str="2026-04-25", today=date(2026, 4, 26))
    assert start == date(2026, 4, 25)
    assert end == date(2026, 4, 25)


def test_resolve_period_range_invalid_period_raises():
    with pytest.raises(ValueError, match="invalid period"):
        oa._resolve_period_range("year", year=2026, today=date(2026, 4, 26))


def test_resolve_compare_range_full_month_to_prev_full_month():
    start, end = oa._resolve_compare_range(
        date(2026, 3, 1), date(2026, 3, 31), "month"
    )
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_resolve_compare_range_partial_month_to_prev_same_day():
    # 当月 4-1 ~ 4-25（截至昨日）→ 上月 3-1 ~ 3-25
    start, end = oa._resolve_compare_range(
        date(2026, 4, 1), date(2026, 4, 25), "month"
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 25)


def test_resolve_compare_range_week_to_prev_week():
    start, end = oa._resolve_compare_range(
        date(2026, 4, 20), date(2026, 4, 26), "week"
    )
    assert start == date(2026, 4, 13)
    assert end == date(2026, 4, 19)


def test_resolve_compare_range_partial_week_to_same_length_prev_week():
    # 当周 4-20 ~ 4-22（截至周三）→ 上周 4-13 ~ 4-15
    start, end = oa._resolve_compare_range(
        date(2026, 4, 20), date(2026, 4, 22), "week"
    )
    assert start == date(2026, 4, 13)
    assert end == date(2026, 4, 15)


def test_resolve_compare_range_day_to_prev_day():
    start, end = oa._resolve_compare_range(date(2026, 4, 25), date(2026, 4, 25), "day")
    assert start == date(2026, 4, 24)
    assert end == date(2026, 4, 24)


def test_resolve_compare_range_month_clamps_start_day():
    """决策回归：start.day=31 → 上月若只有 28/30 天必须 clamp，不能 ValueError。"""
    # 3-31 → 2 月只有 28 天 → start clamp 到 2-28
    start, end = oa._resolve_compare_range(date(2026, 3, 31), date(2026, 3, 31), "month")
    assert start == date(2026, 2, 28)
    assert end == date(2026, 2, 28)
    # 5-31 → 4 月只有 30 天 → start clamp 到 4-30
    start, end = oa._resolve_compare_range(date(2026, 5, 31), date(2026, 5, 31), "month")
    assert start == date(2026, 4, 30)
    assert end == date(2026, 4, 30)


def test_aggregate_orders_by_product_returns_dict_keyed_by_product_id(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"product_id": 42, "orders": 10, "units": 12, "revenue": 240.5},
            {"product_id": 99, "orders": 3, "units": 3, "revenue": 60.0},
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country=None)

    assert 42 in result and 99 in result
    assert result[42]["orders"] == 10
    assert result[42]["units"] == 12
    assert result[42]["revenue"] == 240.5
    assert "FROM dianxiaomi_order_lines" in captured["sql"]
    assert "meta_business_date >= %s" in captured["sql"]
    assert "meta_business_date <= %s" in captured["sql"]
    assert "COALESCE(line_amount" in captured["sql"]
    assert "buyer_country" not in captured["sql"]  # 无国家筛选时不带
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 25))


def test_aggregate_orders_by_product_with_country_filter(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country="DE")

    assert "buyer_country" in captured["sql"]
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 25), "DE")


def test_aggregate_orders_by_product_skips_null_product_id(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": None, "orders": 5, "units": 5, "revenue": 100.0},
        {"product_id": 42, "orders": 2, "units": 2, "revenue": 40.0},
    ])
    result = oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country=None)
    assert list(result.keys()) == [42]


def test_aggregate_ads_by_product_uses_daily_meta_metrics(monkeypatch):
    """决策 #7：只纳入完全被 [start, end] 覆盖的广告报表。"""
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"product_id": 42, "spend": 1200.5, "purchases": 130, "purchase_value": 4500.0},
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))

    # SQL 必须用 'report_start_date >= start AND report_end_date <= end'（完全覆盖语义）
    assert "FROM meta_ad_daily_campaign_metrics" in captured["sql"]
    assert "meta_business_date >= %s" in captured["sql"]
    assert "meta_business_date <= %s" in captured["sql"]
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 30))
    assert result[42]["spend"] == 1200.5
    assert result[42]["purchases"] == 130
    assert result[42]["purchase_value"] == 4500.0


def test_aggregate_ads_by_product_skips_null_product_id(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": None, "spend": 100.0, "purchases": 5, "purchase_value": 0.0},
        {"product_id": 42, "spend": 200.0, "purchases": 10, "purchase_value": 600.0},
    ])
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))
    assert list(result.keys()) == [42]


def test_aggregate_ads_by_product_decimals_to_floats(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": 42, "spend": Decimal("1200.50"), "purchases": Decimal("130"),
         "purchase_value": Decimal("4500.00")},
    ])
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))
    assert result[42]["spend"] == 1200.5
    assert isinstance(result[42]["spend"], float)
    assert result[42]["purchases"] == 130


def test_count_media_items_by_product_groups_by_lang(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": 42, "lang": "en", "n": 1},
        {"product_id": 42, "lang": "de", "n": 2},
        {"product_id": 99, "lang": "en", "n": 1},
    ])
    result = oa._count_media_items_by_product()
    assert result[42] == {"en": 1, "de": 2}
    assert result[99] == {"en": 1}


def test_count_media_items_by_product_filters_deleted(monkeypatch):
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    oa._count_media_items_by_product()
    assert "deleted_at IS NULL" in captured["sql"]


def test_join_and_compute_filters_zero_zero_products():
    """决策 #12: orders=0 + spend=0 的产品被剔除。"""
    products = {
        42: {"id": 42, "name": "Glow", "product_code": "glow-rjc"},
        99: {"id": 99, "name": "Other", "product_code": "other-rjc"},
        7:  {"id": 7,  "name": "Zero", "product_code": "zero"},
    }
    orders_now  = {42: {"orders": 10, "units": 12, "revenue": 200.0}}
    orders_prev = {42: {"orders": 8,  "units": 10, "revenue": 150.0}}
    ads_now     = {99: {"spend": 100.0, "purchases": 5, "purchase_value": 250.0}}
    ads_prev    = {99: {"spend": 80.0,  "purchases": 4, "purchase_value": 200.0}}
    items       = {42: {"en": 1}, 99: {"en": 1}}

    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now=orders_now, orders_prev=orders_prev,
        ads_now=ads_now, ads_prev=ads_prev,
        items=items,
        ad_data_available=True,
    )

    pids = {r["product_id"] for r in rows}
    assert pids == {42, 99}  # 7 被剔除


def test_join_and_compute_roas_uses_shopify_revenue_over_spend():
    """决策 #13: ROAS = Shopify 收入 / Meta 花费。"""
    products = {42: {"id": 42, "name": "X", "product_code": "x"}}
    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now={42: {"orders": 5, "units": 5, "revenue": 500.0}},
        orders_prev={42: {"orders": 3, "units": 3, "revenue": 300.0}},
        ads_now={42: {"spend": 100.0, "purchases": 10, "purchase_value": 999.0}},
        ads_prev={42: {"spend": 80.0,  "purchases": 8,  "purchase_value": 800.0}},
        items={42: {"en": 1}},
        ad_data_available=True,
    )
    assert rows[0]["roas"] == 5.0          # 500 / 100，不是 999 / 100
    assert rows[0]["roas_prev"] == 3.75    # 300 / 80


def test_join_and_compute_ad_unavailable_drops_ad_columns():
    products = {42: {"id": 42, "name": "X", "product_code": "x"}}
    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now={42: {"orders": 5, "units": 5, "revenue": 500.0}},
        orders_prev={},
        ads_now={}, ads_prev={},
        items={42: {"en": 1}},
        ad_data_available=False,
    )
    assert rows[0]["ad_data_available"] is False
    assert rows[0]["spend"] is None
    assert rows[0]["roas"] is None


def test_get_dashboard_month_view_happy_path(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 12, "revenue": 500.0}
    } if s == date(2026, 4, 1) else {
        42: {"orders": 8, "units": 10, "revenue": 400.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 100.0, "purchases": 10, "purchase_value": 500.0}
    } if s == date(2026, 4, 1) else {
        42: {"spend": 80.0, "purchases": 8, "purchase_value": 400.0}
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1, "de": 2}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow Set", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="month", year=2026, month=4,
        today=date(2026, 4, 26), compare=True,
    )

    assert result["period"]["start"] == "2026-04-01"
    assert result["period"]["end"] == "2026-04-25"
    assert result["compare_period"]["start"] == "2026-03-01"
    assert result["compare_period"]["end"] == "2026-03-25"
    assert len(result["products"]) == 1
    assert result["products"][0]["product_id"] == 42
    assert result["products"][0]["roas"] == 5.0
    assert result["country"] is None
    assert result["summary"]["total_orders"] == 10
    assert result["summary"]["total_revenue"] == 500.0
    assert result["summary"]["total_spend"] == 100.0


def test_get_dashboard_accepts_explicit_date_range(monkeypatch):
    captured = {}

    def fake_orders(start, end, *, country=None):
        captured.setdefault("orders_ranges", []).append((start, end))
        return {42: {"orders": 10, "units": 12, "revenue": 500.0}}

    def fake_ads(start, end):
        captured.setdefault("ads_ranges", []).append((start, end))
        return {42: {"spend": 100.0, "purchases": 10, "purchase_value": 500.0}}

    monkeypatch.setattr(oa, "_aggregate_orders_by_product", fake_orders)
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", fake_ads)
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow Set", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="range",
        start_date="2026-04-01",
        end_date="2026-04-18",
        compare=True,
        today=date(2026, 4, 26),
    )

    assert captured["orders_ranges"][0] == (date(2026, 4, 1), date(2026, 4, 18))
    assert captured["orders_ranges"][1] == (date(2026, 3, 14), date(2026, 3, 31))
    assert captured["ads_ranges"][0] == (date(2026, 4, 1), date(2026, 4, 18))
    assert captured["ads_ranges"][1] == (date(2026, 3, 14), date(2026, 3, 31))
    assert result["period"] == {
        "start": "2026-04-01",
        "end": "2026-04-18",
        "label": "2026-04-01 ~ 2026-04-18",
    }
    assert result["compare_period"]["start"] == "2026-03-14"
    assert result["compare_period"]["end"] == "2026-03-31"
    assert result["summary"]["total_spend"] == 100.0


def test_get_dashboard_day_view_no_ads_data(monkeypatch):
    """决策 #3: 日视图不显示广告。"""
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 3, "units": 3, "revenue": 90.0}
    })
    # _aggregate_ads_by_product 不该被调用
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: pytest.fail("should not be called"))
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(period="day", date_str="2026-04-25", today=date(2026, 4, 26))
    assert result["products"][0]["ad_data_available"] is False
    assert result["products"][0]["spend"] is None


def test_get_dashboard_country_filter_drops_ads(monkeypatch):
    """决策 #8 + meta_ad 表无 country 字段：国家筛选启用时广告整列降级。"""
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    ad_called = []
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: ad_called.append(1) or {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="month", year=2026, month=4, country="DE",
        today=date(2026, 4, 26),
    )
    assert ad_called == []
    assert result["products"][0]["ad_data_available"] is False
    assert result["country"] == "DE"


def test_get_dashboard_search_filter(monkeypatch):
    """搜索按 product_code / name 过滤，仅传给 SQL，service 端不再做 in-memory filter。"""
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 42, "name": "Glow", "product_code": "glow-rjc"}]

    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", fake_query)

    oa.get_dashboard(period="month", year=2026, month=4, search="glow", today=date(2026, 4, 26))
    assert "name LIKE" in captured["sql"] or "product_code LIKE" in captured["sql"]
    assert "%glow%" in captured["args"]


def test_get_dashboard_default_sort_orders_desc_for_month(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 10, "revenue": 200.0},
        99: {"orders": 5, "units": 5, "revenue": 100.0},
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 50.0, "purchases": 5, "purchase_value": 100.0},
        99: {"spend": 200.0, "purchases": 10, "purchase_value": 500.0},
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en":1}, 99: {"en":1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "A", "product_code": "a"},
        {"id": 99, "name": "B", "product_code": "b"},
    ])

    result = oa.get_dashboard(period="month", year=2026, month=4, today=date(2026, 4, 26))
    # 默认按订单量降序，广告花费不影响默认排序。
    assert [p["product_id"] for p in result["products"]] == [42, 99]


def test_get_dashboard_explicit_roas_desc_keeps_none_last(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 10, "revenue": 500.0},
        99: {"orders": 1, "units": 1, "revenue": 100.0},
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 0.0, "purchases": 0, "purchase_value": 0.0},
        99: {"spend": 100.0, "purchases": 1, "purchase_value": 100.0},
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}, 99: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "No Roas", "product_code": "no-roas"},
        {"id": 99, "name": "Has Roas", "product_code": "has-roas"},
    ])

    result = oa.get_dashboard(
        period="month",
        year=2026,
        month=4,
        sort_by="roas",
        sort_dir="desc",
        compare=False,
        today=date(2026, 4, 26),
    )

    assert [p["product_id"] for p in result["products"]] == [99, 42]
    assert result["products"][0]["roas"] == 1.0
    assert result["products"][1]["roas"] is None


def test_get_dashboard_load_products_filters_archived_and_deleted(monkeypatch):
    """回归：_load_products 始终过滤 archived/deleted_at。"""
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        return [{"id": 42, "name": "Glow", "product_code": "glow-rjc"}]

    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", fake_query)

    oa.get_dashboard(period="month", year=2026, month=4, today=date(2026, 4, 26))
    assert "archived = 0 OR archived IS NULL" in captured["sql"]
    assert "deleted_at IS NULL" in captured["sql"]


# ── 国家映射 / 启用国家推导 ────────────────────────────────


def test_country_to_lang_canonical_codes_present():
    assert oa.COUNTRY_TO_LANG["US"] == "en"
    assert oa.COUNTRY_TO_LANG["GB"] == "en"
    assert oa.COUNTRY_TO_LANG["UK"] == "en"
    assert oa.COUNTRY_TO_LANG["DE"] == "de"
    assert oa.COUNTRY_TO_LANG["AT"] == "de"
    assert oa.COUNTRY_TO_LANG["BR"] == "pt-BR"
    assert oa.COUNTRY_TO_LANG["PT"] == "pt"


def test_lang_to_countries_uses_priority_order(monkeypatch):
    """同一语种多个国家时，按 LANG_PRIORITY_COUNTRIES 给的固定优先序输出。"""
    enabled = ["en", "de"]
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: enabled)
    cols = oa.get_enabled_country_columns()
    countries = [c["country"] for c in cols]
    # en → US, GB, AU, CA, IE, NZ；de → DE, AT
    assert countries == ["US", "GB", "AU", "CA", "IE", "NZ", "DE", "AT"]
    # 每列都带 lang 字段
    assert cols[0] == {"country": "US", "lang": "en"}
    assert cols[6] == {"country": "DE", "lang": "de"}


def test_get_enabled_country_columns_skips_unmapped_lang(monkeypatch):
    """启用了 COUNTRY_TO_LANG 没覆盖的语种时，跳过该语种但不报错。"""
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en", "xx-unknown"])
    cols = oa.get_enabled_country_columns()
    assert all(c["lang"] != "xx-unknown" for c in cols)
    assert {c["country"] for c in cols} == {"US", "GB", "AU", "CA", "IE", "NZ"}


def test_get_enabled_country_columns_full_set(monkeypatch):
    monkeypatch.setattr(
        oa,
        "_load_enabled_lang_codes",
        lambda: ["en", "de", "fr", "es", "it", "ja", "nl", "sv", "fi", "pt-BR"],
    )
    cols = oa.get_enabled_country_columns()
    countries = [c["country"] for c in cols]
    assert countries == [
        "US", "GB", "AU", "CA", "IE", "NZ",
        "DE", "AT",
        "FR",
        "ES",
        "IT",
        "JP",
        "NL",
        "SE",
        "FI",
        "BR",
    ]


def test_get_monthly_summary_returns_country_columns_and_media_counts(monkeypatch):
    """get_monthly_summary 应返回固定 country_columns 与 media_counts 两个新字段。"""
    # 模拟启用 en + de
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en", "de"])
    # 桩掉 DB 调用
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=None: _stub_monthly_query(sql),
    )

    result = oa.get_monthly_summary(2026, 4)

    assert "country_columns" in result
    assert result["country_columns"][0] == {"country": "US", "lang": "en"}
    assert "media_counts" in result
    # product_id=10 在 fixture 里有 en×3 + de×1
    assert result["media_counts"][10] == {"en": 3, "de": 1}


def _stub_monthly_query(sql: str):
    """根据 SQL 关键字返回不同 fixture，模拟 4 类查询。"""
    s = sql.lower()
    if "from media_items" in s:
        return [
            {"product_id": 10, "lang": "en", "n": 3},
            {"product_id": 10, "lang": "de", "n": 1},
        ]
    if "group by billing_country" in s:
        return [{"billing_country": "US", "total_qty": 5, "order_count": 4}]
    if "group by so.product_id, display_name, so.billing_country" in s:
        return [
            {"product_id": 10, "display_name": "P10", "billing_country": "US", "total_qty": 5},
        ]
    # products 汇总
    return [
        {
            "product_id": 10,
            "display_name": "P10",
            "product_code": "PCK10",
            "total_qty": 5,
            "order_count": 4,
            "total_revenue": Decimal("99.00"),
        }
    ]


def test_get_product_country_detail_covers_all_enabled_countries(monkeypatch):
    """每个启用国家都要出现在返回里，即使该国 0 单 0 素材。"""
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en", "de"])

    def stub_query(sql, args=None):
        s = sql.lower()
        if "from media_items" in s:
            # product_id=42 → en×2，de 没素材
            return [{"product_id": 42, "lang": "en", "n": 2}]
        if "group by so.billing_country" in s:
            # 只有 US 有订单
            return [{
                "billing_country": "US",
                "qty": 23,
                "orders": 18,
                "revenue": Decimal("489.50"),
            }]
        return []

    monkeypatch.setattr(oa, "query", stub_query)

    rows = oa.get_product_country_detail(42, 2026, 4)

    # 8 个 en 国家 (US/GB/AU/CA/IE/NZ) + 2 个 de 国家 (DE/AT) = 8 行
    assert len(rows) == 8
    countries = [r["country"] for r in rows]
    assert countries == ["US", "GB", "AU", "CA", "IE", "NZ", "DE", "AT"]

    us = next(r for r in rows if r["country"] == "US")
    assert us == {
        "country": "US",
        "lang": "en",
        "qty": 23,
        "orders": 18,
        "revenue": 489.50,
        "media_count": 2,
    }

    # GB 启用了但当月 0 单；en 素材数 = 2
    gb = next(r for r in rows if r["country"] == "GB")
    assert gb == {
        "country": "GB", "lang": "en",
        "qty": 0, "orders": 0, "revenue": 0.0,
        "media_count": 2,
    }

    # DE 也启用了但当月 0 单；de 素材数 = 0
    de = next(r for r in rows if r["country"] == "DE")
    assert de == {
        "country": "DE", "lang": "de",
        "qty": 0, "orders": 0, "revenue": 0.0,
        "media_count": 0,
    }


def test_get_product_country_detail_no_orders_no_media(monkeypatch):
    """完全没数据的产品，每个启用国家也应返回一行全 0。"""
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en"])
    monkeypatch.setattr(oa, "query", lambda sql, args=None: [])

    rows = oa.get_product_country_detail(99, 2026, 4)
    assert len(rows) == 6  # US, GB, AU, CA, IE, NZ
    for r in rows:
        assert r["qty"] == 0
        assert r["orders"] == 0
        assert r["revenue"] == 0.0
        assert r["media_count"] == 0
        assert r["lang"] == "en"


def test_get_monthly_summary_uses_media_product_chinese_name(monkeypatch):
    """display_name 应优先取 media_products.name (中文)，再到 page_title / lineitem_name。"""
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en"])

    captured_sqls = []

    def stub_query(sql, args=None):
        captured_sqls.append(sql)
        s = sql.lower()
        if "from media_items" in s:
            return []
        if "group by billing_country" in s:
            return []
        if "group by so.product_id, display_name, so.billing_country" in s:
            return []
        # products aggregate — make sure SQL contains mp.name in COALESCE
        return [{
            "product_id": 7,
            "display_name": "中文产品名",  # 假装 SQL 已经返回中文
            "product_code": "PCK7",
            "total_qty": 1,
            "order_count": 1,
            "total_revenue": Decimal("10.00"),
        }]

    monkeypatch.setattr(oa, "query", stub_query)

    result = oa.get_monthly_summary(2026, 4)

    # 验证: products 汇总 / matrix_rows / countries / media_items 都跑过
    assert any("coalesce(mp.name" in s.lower() for s in captured_sqls), \
        "至少一个 SQL 里要把 mp.name 放在 COALESCE 第一位（products + matrix_rows）"
    # 至少有 2 个查询用了 mp.name 优先（products 汇总 + matrix_rows）
    mp_name_count = sum(1 for s in captured_sqls if "coalesce(mp.name" in s.lower())
    assert mp_name_count >= 2, f"期望至少 2 个 SQL 用 mp.name 在 COALESCE 首位，实际 {mp_name_count}"


def test_get_daily_detail_uses_media_product_chinese_name(monkeypatch):
    captured_sqls = []
    monkeypatch.setattr(oa, "query", lambda sql, args=None: (captured_sqls.append(sql), [])[1])
    oa.get_daily_detail(2026, 4)
    assert any("coalesce(mp.name" in s.lower() for s in captured_sqls)


def test_search_products_uses_media_product_chinese_name(monkeypatch):
    captured = []
    monkeypatch.setattr(oa, "query", lambda sql, args=None: (captured.append(sql), [])[1])
    oa.search_products("test")
    assert any("coalesce(mp.name" in s.lower() for s in captured)
