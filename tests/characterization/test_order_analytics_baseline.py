"""Baseline characterization tests for ``appcore.order_analytics``.

锁定 ``order_analytics`` 公开函数的"形状"（顶层 keys、返回类型、参数兼容），
作为后续 package 拆分（PR 1.1+）的回归防护网。

每个 sub-module 拆分前，本文件须保持绿；拆分过程中 import 路径与函数签名
不变，本文件不应被修改。

不连接数据库 / 网络：所有 ``query``/``query_one``/``execute``/``get_conn``/
``requests`` 都通过 ``monkeypatch`` 替换。
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import pytest


# ─────────────────────────────────────────────────────────────────────
# 1. Import 连通性：所有公开符号 + 子类引用的私有符号都可 import
# ─────────────────────────────────────────────────────────────────────


def test_public_functions_importable():
    from appcore import order_analytics as oa

    public = [
        "compute_meta_business_window_bj", "compute_order_meta_attribution",
        "extract_dianxiaomi_shopify_product_id", "extract_dianxiaomi_product_handle",
        "build_dianxiaomi_product_scope", "normalize_dianxiaomi_order",
        "start_dianxiaomi_order_import_batch", "finish_dianxiaomi_order_import_batch",
        "upsert_dianxiaomi_order_lines", "get_dianxiaomi_order_import_batches",
        "get_dianxiaomi_order_analysis",
        "parse_shopify_file", "import_orders", "get_import_stats",
        "fetch_product_page_title", "refresh_product_titles",
        "match_orders_to_products",
        "parse_meta_ad_file", "product_code_candidates_for_ad_campaign",
        "resolve_ad_product_match", "import_meta_ad_rows",
        "match_meta_ads_to_products",
        "get_meta_ad_stats", "get_meta_ad_periods", "get_meta_ad_summary",
        "get_realtime_roas_overview", "get_true_roas_summary",
        "get_country_dashboard",
        "get_monthly_summary", "get_product_country_detail", "get_daily_detail",
        "get_weekly_summary", "search_products", "get_available_months",
        "get_dashboard", "get_enabled_country_columns",
    ]
    missing = [name for name in public if not hasattr(oa, name)]
    assert not missing, f"missing public symbols: {missing}"
    not_callable = [name for name in public if not callable(getattr(oa, name))]
    assert not not_callable, f"public symbols not callable: {not_callable}"


def test_dataclass_and_constants_importable():
    from appcore import order_analytics as oa

    assert hasattr(oa, "DianxiaomiProductScope")
    for name in (
        "META_ATTRIBUTION_CUTOVER_HOUR_BJ",
        "META_ATTRIBUTION_TIMEZONE",
        "COUNTRY_TO_LANG",
        "LANG_PRIORITY_COUNTRIES",
    ):
        assert hasattr(oa, name), f"missing constant: {name}"


def test_private_helpers_importable():
    """子类 / 调用方引用的 ``_*`` helpers 必须保持可 import。

    拆 package 后 façade re-export 必须把这些都覆盖到，否则会破坏：
    - 测试文件 (``test_order_analytics_*.py``) 直接用 ``oa._compute_pct_change``
      等私有 helper
    - ``web/routes/order_analytics.py`` 等调用方
    """
    from appcore import order_analytics as oa

    private = [
        # 通用 helpers
        "_money", "_roas", "_revenue_with_shipping", "_beijing_now",
        "_business_hour", "_safe_decimal_float", "_safe_int", "_safe_float",
        "_safe_float_default", "_parse_meta_date", "_parse_iso_date_param",
        "_combined_link_text", "_canonical_product_handle",
        "_compute_pct_change", "_dianxiaomi_order_time_expr",
        "_parse_dianxiaomi_ts", "_json_dumps_for_db",
        # period helpers
        "_resolve_period_range", "_resolve_compare_range",
        "_month_range", "_format_period_label",
        # dashboard helpers
        "_aggregate_orders_by_product", "_aggregate_ads_by_product",
        "_count_media_items_by_product", "_join_and_compute_dashboard_rows",
        "_load_products", "_summarize_dashboard",
        # country dashboard helpers
        "_sort_order_dashboard_rows", "_coerce_country_dashboard_date",
        "_coerce_ad_frequency",
        # realtime helpers
        "_get_realtime_order_details", "_get_realtime_campaign_details",
        "_get_daily_campaigns", "_get_today_realtime_meta_totals",
        "_get_realtime_ad_updated_at",
        # meta ads helpers
        "_resolve_meta_ad_period", "_coerce_meta_product_id",
        "_aggregate_meta_ad_summary_rows", "_normalize_meta_ad_row",
        # shopify helpers
        "_parse_excel", "_parse_shopify_ts",
        # dianxiaomi helpers
        "_dianxiaomi_order_lines", "_resolve_dianxiaomi_line_product",
        "_infer_dianxiaomi_site_code_from_text",
        "_dianxiaomi_order_line_values",
        # enabled-lang loader
        "_load_enabled_lang_codes",
    ]
    missing = [name for name in private if not hasattr(oa, name)]
    assert not missing, f"missing private helpers: {missing}"


# ─────────────────────────────────────────────────────────────────────
# 2. 通用 helpers 形状测试
# ─────────────────────────────────────────────────────────────────────


def test_money_shape():
    from appcore import order_analytics as oa

    assert oa._money(None) == 0.0
    assert isinstance(oa._money("1.5"), float)
    assert oa._money(1.236) == 1.24


def test_roas_shape():
    from appcore import order_analytics as oa

    assert oa._roas(0.0, 0.0) is None
    assert oa._roas(100.0, 0.0) is None
    assert oa._roas(100.0, 50.0) == 2.0


def test_revenue_with_shipping_shape():
    from appcore import order_analytics as oa

    assert oa._revenue_with_shipping(10.0, 5.0) == 15.0
    assert oa._revenue_with_shipping(None, None) == 0.0


def test_business_hour_shape():
    from appcore import order_analytics as oa

    day_start = datetime(2026, 5, 1, 16, 0)
    assert oa._business_hour(None, day_start) is None
    h = oa._business_hour(datetime(2026, 5, 1, 18, 30), day_start)
    assert isinstance(h, int)
    assert 0 <= h <= 23


def test_safe_int_shape():
    from appcore import order_analytics as oa

    assert oa._safe_int("5") == 5
    assert oa._safe_int("not-int", default=99) == 99


def test_safe_float_shape():
    from appcore import order_analytics as oa

    assert oa._safe_float("1.5") == 1.5
    assert oa._safe_float("not-float") is None


def test_safe_float_default_shape():
    from appcore import order_analytics as oa

    assert oa._safe_float_default("1.5") == 1.5
    assert oa._safe_float_default("nope", default=2.0) == 2.0


def test_safe_decimal_float_shape():
    from appcore import order_analytics as oa

    assert oa._safe_decimal_float(None) is None
    assert oa._safe_decimal_float("1,234.56") == 1234.56


def test_parse_meta_date_shape():
    from appcore import order_analytics as oa

    d = oa._parse_meta_date("2026-04-01")
    assert isinstance(d, date)
    assert d == date(2026, 4, 1)


def test_parse_iso_date_param_shape():
    from appcore import order_analytics as oa

    assert oa._parse_iso_date_param("2026-04-01", "test_field") == date(2026, 4, 1)
    with pytest.raises(ValueError):
        oa._parse_iso_date_param("nope", "test_field")


def test_combined_link_text_shape():
    from appcore import order_analytics as oa

    out = oa._combined_link_text("A", None, "b")
    assert isinstance(out, str)
    assert out == "a  b"


def test_canonical_product_handle_shape():
    from appcore import order_analytics as oa

    assert oa._canonical_product_handle(None) is None
    assert oa._canonical_product_handle("https://x.com/products/abc-rjc") == "abc"


def test_dianxiaomi_order_time_expr_shape():
    from appcore import order_analytics as oa

    expr = oa._dianxiaomi_order_time_expr()
    assert isinstance(expr, str)
    assert "COALESCE" in expr


def test_parse_dianxiaomi_ts_shape():
    from appcore import order_analytics as oa

    assert oa._parse_dianxiaomi_ts(None) is None
    assert oa._parse_dianxiaomi_ts("") is None
    ts = oa._parse_dianxiaomi_ts("2026-04-27 10:03:00")
    assert isinstance(ts, datetime)


def test_compute_meta_business_window_bj_shape():
    from appcore import order_analytics as oa

    start, end = oa.compute_meta_business_window_bj(date(2026, 5, 1))
    assert start == datetime(2026, 5, 1, 16, 0, 0)
    assert end == start + timedelta(days=1)


def test_compute_order_meta_attribution_all_none_shape():
    from appcore import order_analytics as oa

    result = oa.compute_order_meta_attribution(None, None, None)
    expected_keys = {
        "attribution_time_at", "attribution_source", "attribution_timezone",
        "meta_business_date", "meta_window_start_at", "meta_window_end_at",
    }
    assert set(result) == expected_keys
    assert result["attribution_time_at"] is None
    assert result["meta_business_date"] is None
    assert result["attribution_timezone"] == oa.META_ATTRIBUTION_TIMEZONE


def test_compute_order_meta_attribution_with_value_shape():
    from appcore import order_analytics as oa

    ts = datetime(2026, 5, 1, 12, 30)
    result = oa.compute_order_meta_attribution(None, ts, None)
    assert result["attribution_time_at"] == ts
    assert isinstance(result["meta_business_date"], date)
    assert isinstance(result["meta_window_start_at"], datetime)
    assert isinstance(result["meta_window_end_at"], datetime)


# ─────────────────────────────────────────────────────────────────────
# 3. dianxiaomi 子模块
# ─────────────────────────────────────────────────────────────────────


def test_dianxiaomi_product_scope_dataclass_shape():
    from appcore import order_analytics as oa

    scope = oa.DianxiaomiProductScope(
        by_shopify_id={},
        by_handle={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy"},
    )
    assert scope.by_shopify_id == {}
    assert scope.requested_site_codes == {"newjoy"}


def test_extract_dianxiaomi_product_handle_shape():
    from appcore import order_analytics as oa

    assert oa.extract_dianxiaomi_product_handle(
        {"productUrl": "https://x.com/products/foo"}
    ) == "foo"
    assert oa.extract_dianxiaomi_product_handle({}) is None


def test_get_dianxiaomi_order_import_batches_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.get_dianxiaomi_order_import_batches() == []


def test_get_dianxiaomi_order_analysis_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): {})

    result = oa.get_dianxiaomi_order_analysis("2026-04-01", "2026-04-30")
    assert isinstance(result, dict)
    assert {"period", "filters", "summary", "pagination", "rows"}.issubset(result.keys())
    assert {"start_date", "end_date", "date_field", "timezone"}.issubset(result["period"].keys())
    assert {"order_count", "units", "shipping", "product_net_sales", "total_sales"}.issubset(result["summary"].keys())
    assert {"page", "page_size", "total", "total_pages"}.issubset(result["pagination"].keys())


# ─────────────────────────────────────────────────────────────────────
# 4. shopify_orders 子模块
# ─────────────────────────────────────────────────────────────────────


def test_parse_shopify_file_csv_returns_list():
    from appcore import order_analytics as oa

    csv_text = (
        "Id,Name,Created at,Lineitem name,Lineitem sku,Lineitem quantity,"
        "Lineitem price,Billing Country,Total,Subtotal,Shipping,Currency,"
        "Financial Status,Fulfillment Status,Vendor\n"
        "1234,#1001,2026-04-01 10:00:00 +0000,Product Foo,SKU-A,2,9.99,US,"
        "19.98,19.98,0,USD,paid,fulfilled,Vendor\n"
    )
    rows = oa.parse_shopify_file(io.BytesIO(csv_text.encode("utf-8")), "shopify.csv")
    assert isinstance(rows, list)
    assert len(rows) == 1


def test_get_import_stats_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): {"total_rows": 0})
    result = oa.get_import_stats()
    assert isinstance(result, dict)


def test_import_orders_empty_shape():
    from appcore import order_analytics as oa

    assert oa.import_orders([]) == {"imported": 0, "skipped": 0}


def test_match_orders_to_products_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "execute", lambda sql, args=(): 0)
    assert isinstance(oa.match_orders_to_products(), int)


def test_fetch_product_page_title_handles_failure(monkeypatch):
    from appcore import order_analytics as oa

    class FakeResp:
        status_code = 500
        text = ""

    monkeypatch.setattr(oa.requests, "get", lambda url, **kw: FakeResp())
    assert oa.fetch_product_page_title("foo") is None


def test_refresh_product_titles_empty_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    monkeypatch.setattr(oa.time, "sleep", lambda *args, **kw: None)

    result = oa.refresh_product_titles()
    assert isinstance(result, dict)
    assert {"fetched", "skipped", "errors"}.issubset(result.keys())


# ─────────────────────────────────────────────────────────────────────
# 5. meta_ads 子模块
# ─────────────────────────────────────────────────────────────────────


def test_get_meta_ad_stats_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): {"total_rows": 0})
    result = oa.get_meta_ad_stats()
    assert isinstance(result, dict)


def test_get_meta_ad_periods_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.get_meta_ad_periods() == []


def test_get_meta_ad_summary_no_period_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)

    result = oa.get_meta_ad_summary()
    assert isinstance(result, dict)
    assert {"period", "rows", "unmatched"}.issubset(result.keys())
    assert result["rows"] == []
    assert result["unmatched"] == []


# ─────────────────────────────────────────────────────────────────────
# 6. realtime 子模块
# ─────────────────────────────────────────────────────────────────────


def test_get_realtime_roas_overview_no_data_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_realtime_roas_overview(
        date_text="2026-04-01", now=datetime(2026, 5, 1, 12, 0)
    )
    assert isinstance(result, dict)
    assert {
        "period", "scope", "freshness", "summary",
        "hourly", "roas_points", "order_details", "campaigns",
    }.issubset(result.keys())
    assert isinstance(result["hourly"], list)
    assert len(result["roas_points"]) == 24


def test_get_true_roas_summary_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_true_roas_summary("2026-04-01", "2026-04-03")
    assert isinstance(result, dict)
    assert {"period", "summary", "rows"}.issubset(result.keys())
    assert isinstance(result["rows"], list)


# ─────────────────────────────────────────────────────────────────────
# 7. country_dashboard 子模块
# ─────────────────────────────────────────────────────────────────────


def test_get_country_dashboard_explicit_range_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_country_dashboard(
        period="day", start_date="2026-04-01", end_date="2026-04-01"
    )
    assert isinstance(result, dict)
    assert {"period", "summary", "countries"}.issubset(result.keys())
    assert {"type", "start", "end", "label", "date_field", "timezone"}.issubset(result["period"].keys())
    assert {
        "country_count", "total_orders", "total_units",
        "total_sales", "shipping", "product_net_sales",
    }.issubset(result["summary"].keys())


# ─────────────────────────────────────────────────────────────────────
# 8. periodic 子模块
# ─────────────────────────────────────────────────────────────────────


def test_get_monthly_summary_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_monthly_summary(2026, 4)
    assert isinstance(result, dict)
    assert {
        "products", "countries", "country_list", "matrix",
        "product_order", "country_columns", "media_counts",
    }.issubset(result.keys())


def test_get_product_country_detail_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.get_product_country_detail(1, 2026, 4) == []


def test_get_daily_detail_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.get_daily_detail(2026, 4) == []


def test_get_weekly_summary_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_weekly_summary(2026, 17)
    assert isinstance(result, dict)
    assert {"products", "countries"}.issubset(result.keys())


def test_search_products_text_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.search_products("foo") == []


def test_search_products_numeric_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.search_products("123") == []


def test_get_available_months_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert oa.get_available_months() == []


def test_get_enabled_country_columns_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en", "de"])
    result = oa.get_enabled_country_columns()
    assert isinstance(result, list)
    assert all({"country", "lang"}.issubset(item.keys()) for item in result)


# ─────────────────────────────────────────────────────────────────────
# 9. dashboard 子模块
# ─────────────────────────────────────────────────────────────────────


def test_get_dashboard_with_compare_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_dashboard(
        period="month", year=2026, month=3,
        today=date(2026, 4, 26),
    )
    assert isinstance(result, dict)
    assert {"period", "compare_period", "country", "products", "summary"}.issubset(result.keys())
    assert {"start", "end", "label"}.issubset(result["period"].keys())
    assert isinstance(result["products"], list)
    assert isinstance(result["compare_period"], dict)


def test_get_dashboard_no_compare_shape(monkeypatch):
    from appcore import order_analytics as oa

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    result = oa.get_dashboard(
        period="month", year=2026, month=3, compare=False,
        today=date(2026, 4, 26),
    )
    assert result["compare_period"] is None
