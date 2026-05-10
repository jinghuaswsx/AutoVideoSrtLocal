from __future__ import annotations

from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics.realtime import (
    _build_order_profit_status_label,
    _derive_order_profit_status,
    _derive_refund_status,
    _format_realtime_order_profit_rows,
    _get_realtime_order_profit_details,
    _is_refund_like_state,
    _resolve_refund_deduction,
)


def test_resolve_refund_deduction_uses_actual_partial_refund():
    assert _resolve_refund_deduction(
        total_revenue=100.0,
        refund_amount_usd=12.5,
        order_state="paid",
    ) == 12.5


def test_resolve_refund_deduction_caps_refund_to_total_revenue():
    assert _resolve_refund_deduction(
        total_revenue=100.0,
        refund_amount_usd=150.0,
        order_state="paid",
    ) == 100.0


def test_resolve_refund_deduction_uses_full_revenue_for_refund_state_without_amount():
    assert _resolve_refund_deduction(
        total_revenue=88.0,
        refund_amount_usd=0,
        order_state="refunded",
    ) == 88.0


def test_refund_state_detects_english_and_chinese_values():
    assert _is_refund_like_state("refund success") is True
    assert _is_refund_like_state("cancelled") is True
    assert _is_refund_like_state("已退款") is True
    assert _is_refund_like_state("已取消") is True
    assert _is_refund_like_state("paid") is False


def test_derive_refund_status():
    assert _derive_refund_status(total_revenue=100, refund_deduction=0) == "none"
    assert _derive_refund_status(total_revenue=100, refund_deduction=20) == "partial_refund"
    assert _derive_refund_status(total_revenue=100, refund_deduction=100) == "full_refund"


def test_derive_order_profit_status():
    assert _derive_order_profit_status(line_count=2, ok_count=2, incomplete_count=0) == "ok"
    assert _derive_order_profit_status(line_count=2, ok_count=1, incomplete_count=1) == "partially_complete"
    assert _derive_order_profit_status(line_count=2, ok_count=0, incomplete_count=2) == "incomplete"
    assert _derive_order_profit_status(line_count=0, ok_count=0, incomplete_count=0) == "not_computed"


def test_build_order_profit_status_label():
    assert _build_order_profit_status_label("ok", "none") == "完整"
    assert _build_order_profit_status_label("partially_complete", "none") == "部分完整"
    assert _build_order_profit_status_label("incomplete", "none") == "不完整"
    assert _build_order_profit_status_label("not_computed", "none") == "未核算"
    assert _build_order_profit_status_label("ok", "full_refund") == "完整 / 全额退款"
    assert _build_order_profit_status_label("ok", "partial_refund") == "完整 / 部分退款"


def test_get_realtime_order_profit_details_aggregates_costs_and_refunds(monkeypatch):
    target = date(2026, 5, 6)
    day_start = datetime(2026, 5, 5, 16, 0)
    data_until = datetime(2026, 5, 6, 12, 0)
    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "SELECT d.site_code" not in sql:
            return []
        assert "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql
        assert "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd" in sql
        assert args == (target, data_until)
        return [
            {
                "site_code": "newjoy",
                "dxm_package_id": "PKG-DE",
                "dxm_order_id": "DXM-DE",
                "package_number": "PN-DE",
                "order_state": "paid",
                "buyer_country": "DE",
                "buyer_country_name": "Germany",
                "order_time": datetime(2026, 5, 6, 10, 30),
                "line_count": 2,
                "profit_line_count": 1,
                "profit_ok_count": 1,
                "profit_incomplete_count": 0,
                "units": 3,
                "product_revenue": 100.0,
                "shipping_revenue": 10.0,
                "total_revenue": 110.0,
                "refund_amount_usd": 12.0,
                "return_reserve_usd": 1.1,
                "purchase_cost": 30.0,
                "purchase_estimate": 0.0,
                "logistics_cost": 8.0,
                "logistics_estimate": 0.0,
                "ad_cost": 11.0,
                "stored_shopify_fee_total": 5.75,
                "skus": "SKU-A / SKU-B",
                "product_names": "Product A / Product B",
            },
            {
                "site_code": "omurio",
                "dxm_package_id": "PKG-US",
                "dxm_order_id": "DXM-US",
                "package_number": "PN-US",
                "order_state": "refunded",
                "buyer_country": "US",
                "buyer_country_name": "United States",
                "order_time": datetime(2026, 5, 6, 9, 15),
                "line_count": 1,
                "profit_line_count": 1,
                "profit_ok_count": 1,
                "profit_incomplete_count": 0,
                "units": 1,
                "product_revenue": 50.0,
                "shipping_revenue": 5.0,
                "total_revenue": 55.0,
                "refund_amount_usd": 0.0,
                "return_reserve_usd": 0.55,
                "purchase_cost": 10.0,
                "purchase_estimate": 0.0,
                "logistics_cost": 5.0,
                "logistics_estimate": 0.0,
                "ad_cost": 2.0,
                "stored_shopify_fee_total": 1.68,
                "skus": "SKU-US",
                "product_names": "Product US",
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    details = _get_realtime_order_profit_details(target, day_start, data_until)

    assert any("SELECT d.site_code" in sql for sql, _args in calls)
    de_order = details[0]
    assert de_order["shopify_platform_fee_usd"] == 3.02
    assert de_order["international_card_fee_usd"] == 1.09
    assert de_order["currency_conversion_fee_usd"] == 1.64
    assert de_order["shopify_fee_total_usd"] == 5.75
    assert de_order["stored_shopify_fee_total_usd"] == 5.75
    assert de_order["refund_deduction_usd"] == 12.0
    assert de_order["return_reserve_usd"] == 1.1
    assert de_order["ad_cost_usd"] == 11.0
    assert de_order["order_profit_usd"] == 54.15
    assert de_order["profit_status"] == "ok"
    assert de_order["refund_status"] == "partial_refund"
    assert de_order["status_label"] == "完整 / 部分退款"
    assert de_order["shopify_tier"] == "D"
    assert de_order["presentment_currency"] == "EUR"

    us_order = details[1]
    assert us_order["refund_deduction_usd"] == 55.0
    assert us_order["refund_status"] == "full_refund"
    assert us_order["profit_status"] == "ok"
    assert us_order["order_profit_usd"] == 35.77


def test_format_order_profit_rows_uses_canonical_profit_fields():
    row = {
        "site_code": "newjoy",
        "dxm_package_id": "PKG-CANONICAL",
        "dxm_order_id": "DXM-CANONICAL",
        "package_number": "PN-CANONICAL",
        "order_state": "paid",
        "buyer_country": "DE",
        "buyer_country_name": "Germany",
        "order_time": datetime(2026, 4, 30, 10, 30),
        "line_count": 2,
        "profit_line_count": 2,
        "profit_ok_count": 1,
        "profit_incomplete_count": 1,
        "purchase_missing_count": 1,
        "logistics_missing_count": 1,
        "units": 2,
        "product_revenue": 236.17,
        "shipping_revenue": 72.38,
        "total_revenue": 308.55,
        "refund_amount_usd": 20.0,
        "return_reserve_usd": 3.09,
        "purchase_cost": 27.44,
        "purchase_estimate": 6.79,
        "logistics_cost": 53.78,
        "logistics_estimate": 3.64,
        "ad_cost": 190.04,
        "stored_shopify_fee_total": 15.89,
        "skus": "SKU-C",
        "product_names": "Canonical Product",
    }

    detail = _format_realtime_order_profit_rows([row], datetime(2026, 4, 29, 16, 0))[0]

    assert detail["total_revenue"] == 308.55
    assert detail["refund_deduction_usd"] == 20.0
    assert detail["return_reserve_usd"] == 3.09
    assert detail["purchase_cost_usd"] == 27.44
    assert detail["purchase_estimate_usd"] == 6.79
    assert detail["logistics_cost_usd"] == 53.78
    assert detail["logistics_estimate_usd"] == 3.64
    assert detail["shopify_fee_total_usd"] == 15.89
    assert detail["order_profit_with_estimate_usd"] == 7.88


def test_get_realtime_order_profit_details_marks_missing_profit_lines_incomplete(monkeypatch):
    target = date(2026, 5, 6)
    day_start = datetime(2026, 5, 5, 16, 0)
    data_until = datetime(2026, 5, 6, 12, 0)

    def fake_query(sql, args=()):
        assert "p.id IS NULL" in sql
        return [
            {
                "site_code": "newjoy",
                "dxm_package_id": "PKG-PARTIAL",
                "dxm_order_id": "DXM-PARTIAL",
                "package_number": "PN-PARTIAL",
                "order_state": "paid",
                "buyer_country": "US",
                "buyer_country_name": "United States",
                "order_time": datetime(2026, 5, 6, 10, 30),
                "line_count": 2,
                "profit_line_count": 1,
                "profit_ok_count": 1,
                "profit_incomplete_count": 1,
                "units": 2,
                "product_revenue": 100.0,
                "shipping_revenue": 0.0,
                "total_revenue": 100.0,
                "refund_amount_usd": 0.0,
                "purchase_cost": 30.0,
                "logistics_cost": 8.0,
                "ad_cost": 11.0,
                "stored_shopify_fee_total": 2.8,
                "skus": "SKU-A / SKU-B",
                "product_names": "Product A / Product B",
            }
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    detail = _get_realtime_order_profit_details(target, day_start, data_until)[0]

    assert detail["profit_status"] == "partially_complete"
    assert detail["status_label"] == "部分完整"


def test_get_realtime_order_profit_details_supports_product_filter_and_pagination(monkeypatch):
    target = date(2026, 5, 6)
    day_start = datetime(2026, 5, 5, 16, 0)
    data_until = datetime(2026, 5, 6, 12, 0)
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    rows = _get_realtime_order_profit_details(
        target,
        day_start,
        data_until,
        product_id=42,
        page=2,
        page_size=100,
    )

    assert rows == []
    assert "AND d.product_id = %s" in captured["sql"]
    assert "LIMIT %s OFFSET %s" in captured["sql"]
    assert captured["args"] == (target, data_until, 42, 100, 100)


def test_get_realtime_order_profit_details_applies_realtime_ad_cost_adjustments(monkeypatch):
    """日终广告表未到、order_profit_lines.ad_cost_usd=0 时，看板「订单盈亏明细」
    需要落到 meta_ad_realtime_daily_campaign_metrics 兜底分摊。"""
    target = date(2026, 5, 9)
    day_start = datetime(2026, 5, 8, 16, 0)
    data_until = datetime(2026, 5, 9, 12, 0)

    sql_calls: list[str] = []

    def fake_query(sql, args=()):
        sql_calls.append(sql)
        if "FROM dianxiaomi_order_lines d" in sql and "AND d.meta_business_date=%s" in sql:
            return [
                {
                    "site_code": "newjoy",
                    "dxm_package_id": "PKG-RT",
                    "dxm_order_id": "DXM-RT",
                    "package_number": "PN-RT",
                    "order_state": "paid",
                    "buyer_country": "US",
                    "buyer_country_name": "United States",
                    "order_time": datetime(2026, 5, 9, 11, 0),
                    "line_count": 1,
                    "profit_line_count": 1,
                    "profit_ok_count": 1,
                    "profit_incomplete_count": 0,
                    "units": 2,
                    "product_revenue": 100.0,
                    "shipping_revenue": 0.0,
                    "total_revenue": 100.0,
                    "refund_amount_usd": 0.0,
                    "return_reserve_usd": 1.0,
                    "purchase_cost": 20.0,
                    "purchase_estimate": 0.0,
                    "logistics_cost": 5.0,
                    "logistics_estimate": 0.0,
                    "ad_cost": 0.0,  # 日终未到，stored 仍是 0
                    "stored_shopify_fee_total": 5.6,
                    "skus": "SKU-RT",
                    "product_names": "Realtime Product",
                },
            ]
        # _load_realtime_ad_snapshot_fallback inside _load_realtime_ad_cost_adjustments
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY" in sql:
            return []
        if "MAX(snapshot_at)" in sql and "GROUP BY business_date, ad_account_id" in sql:
            return [{"business_date": target, "ad_account_id": "ACC", "snapshot_at": datetime(2026, 5, 9, 11, 0)}]
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql and "ad_account_id=%s" in sql:
            return [
                {
                    "business_date": target,
                    "campaign_name": "campaign-x",
                    "normalized_campaign_code": "campaign-x",
                    "spend_usd": 40.0,
                }
            ]
        if "FROM order_profit_lines p" in sql and "JOIN dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date, p.product_id" in sql:
            return [{"business_date": target, "product_id": 99, "units": 2}]
        if "FROM order_profit_lines p" in sql and "JOIN dianxiaomi_order_lines d" in sql and "WHERE d.meta_business_date BETWEEN" in sql:
            # 一个 line，quantity=2，stored ad_cost=0 → realtime_cost = 40 * 2/2 = 40 → delta=40
            return [
                {
                    "dxm_package_id": "PKG-RT",
                    "business_date": target,
                    "status": "ok",
                    "product_id": 99,
                    "quantity": 2,
                    "ad_cost_usd": 0.0,
                }
            ]
        return []

    from appcore.order_analytics import order_profit_aggregation as opa
    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(opa, "resolve_ad_product_match", lambda code: {"id": 99}, raising=False)

    details = _get_realtime_order_profit_details(target, day_start, data_until)

    assert len(details) == 1
    rt = details[0]
    # 兜底应用后：ad_cost_usd 从 0 抬到 40，order_profit 同步降 40
    assert rt["ad_cost_usd"] == 40.0
    # 订单利润 = 100 - 0(refund) - 20(purchase) - 5(logistics) - shopify_fee - 40(ad)
    # shopify_fee for US 100$ ≈ 5.6 USD by split_shopify_fee_for_order; 行内计算 → 利润 < 35
    assert rt["order_profit_usd"] < 35.0


def test_format_order_profit_rows_marks_missing_cost_estimates():
    row = {
        "site_code": "newjoy",
        "dxm_package_id": "PKG-MISSING",
        "dxm_order_id": "DXM-MISSING",
        "package_number": "PN-MISSING",
        "order_state": "paid",
        "buyer_country": "US",
        "buyer_country_name": "United States",
        "order_time": datetime(2026, 5, 6, 10, 30),
        "line_count": 1,
        "profit_line_count": 1,
        "profit_ok_count": 0,
        "profit_incomplete_count": 1,
        "purchase_missing_count": 1,
        "logistics_missing_count": 1,
        "units": 1,
        "product_revenue": 120.0,
        "shipping_revenue": 0.0,
        "total_revenue": 120.0,
        "refund_amount_usd": 0.0,
        "purchase_cost": 0.0,
        "logistics_cost": 0.0,
        "ad_cost": 6.0,
        "stored_shopify_fee_total": 3.3,
        "skus": "SKU-M",
        "product_names": "Missing Cost Product",
    }

    detail = _format_realtime_order_profit_rows([row], datetime(2026, 5, 5, 16, 0))[0]

    assert detail["purchase_cost_missing"] is True
    assert detail["purchase_estimate_usd"] == 12.0
    assert detail["logistics_cost_missing"] is True
    assert detail["logistics_estimate_usd"] == 24.0
    assert detail["order_profit_with_estimate_usd"] == 74.7


def test_build_order_profit_summary_uses_estimates_for_missing_costs():
    from appcore.order_analytics.realtime import _build_order_profit_summary

    rows = [
        {
            "total_revenue": 100.0,
            "refund_deduction_usd": 0.0,
            "purchase_cost_usd": 10.0,
            "purchase_estimate_usd": 0.0,
            "purchase_cost_missing": False,
            "logistics_cost_usd": 20.0,
            "logistics_estimate_usd": 0.0,
            "logistics_cost_missing": False,
            "shopify_fee_total_usd": 5.0,
            "ad_cost_usd": 7.0,
        },
        {
            "total_revenue": 200.0,
            "refund_deduction_usd": 10.0,
            "purchase_cost_usd": 0.0,
            "purchase_estimate_usd": 20.0,
            "purchase_cost_missing": True,
            "logistics_cost_usd": 0.0,
            "logistics_estimate_usd": 40.0,
            "logistics_cost_missing": True,
            "shopify_fee_total_usd": 8.0,
            "ad_cost_usd": 12.0,
        },
    ]

    summary = _build_order_profit_summary(rows)

    assert summary["order_count"] == 2
    assert summary["total_revenue_usd"] == 300.0
    assert summary["purchase_cost_usd"] == 10.0
    assert summary["purchase_estimate_usd"] == 20.0
    assert summary["purchase_cost_with_estimate_usd"] == 30.0
    assert summary["purchase_missing_order_count"] == 1
    assert summary["purchase_missing_order_ratio"] == 0.5
    assert summary["logistics_cost_usd"] == 20.0
    assert summary["logistics_estimate_usd"] == 40.0
    assert summary["logistics_cost_with_estimate_usd"] == 60.0
    assert summary["logistics_missing_order_count"] == 1
    assert summary["logistics_missing_order_ratio"] == 0.5
    assert summary["shopify_fee_total_usd"] == 13.0
    assert summary["ad_cost_usd"] == 19.0
    # 没传 total_ad_spend_usd 时，未分摊 = 0、利润退化为旧公式。
    assert summary["unallocated_ad_spend_usd"] == 0.0
    assert summary["total_ad_spend_usd"] == 19.0
    assert summary["profit_with_estimate_usd"] == 168.0


def test_build_order_profit_summary_subtracts_unallocated_ad_spend():
    from appcore.order_analytics.realtime import _build_order_profit_summary

    rows = [
        {
            "total_revenue": 100.0,
            "refund_deduction_usd": 0.0,
            "purchase_cost_usd": 10.0,
            "purchase_estimate_usd": 0.0,
            "purchase_cost_missing": False,
            "logistics_cost_usd": 20.0,
            "logistics_estimate_usd": 0.0,
            "logistics_cost_missing": False,
            "shopify_fee_total_usd": 5.0,
            "ad_cost_usd": 7.0,
        },
    ]

    # 已分摊 7，总 spend 30 → 未分摊 23，profit = 100 - 10 - 20 - 5 - 7 - 23 = 35
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=30.0)
    assert summary["ad_cost_usd"] == 7.0
    assert summary["unallocated_ad_spend_usd"] == 23.0
    assert summary["total_ad_spend_usd"] == 30.0
    assert summary["profit_with_estimate_usd"] == 35.0


def test_build_order_profit_summary_clamps_unallocated_when_total_below_allocated():
    from appcore.order_analytics.realtime import _build_order_profit_summary

    # 总 spend 比已分摊还小（例如实时快照落后于已写入利润行的日终重算）
    # → 未分摊不能为负，否则会重复计帐放大利润。
    rows = [
        {
            "total_revenue": 100.0,
            "refund_deduction_usd": 0.0,
            "purchase_cost_usd": 0.0,
            "purchase_estimate_usd": 0.0,
            "purchase_cost_missing": False,
            "logistics_cost_usd": 0.0,
            "logistics_estimate_usd": 0.0,
            "logistics_cost_missing": False,
            "shopify_fee_total_usd": 0.0,
            "ad_cost_usd": 50.0,
        },
    ]

    summary = _build_order_profit_summary(rows, total_ad_spend_usd=10.0)
    assert summary["ad_cost_usd"] == 50.0
    assert summary["unallocated_ad_spend_usd"] == 0.0
    assert summary["total_ad_spend_usd"] == 10.0
    # 利润仍按已分摊扣，等于 100 - 50 - 0 = 50（不会被未分摊负值"补回去"）
    assert summary["profit_with_estimate_usd"] == 50.0


def test_build_order_profit_summary_handles_empty_rows_with_total_ad_spend():
    from appcore.order_analytics.realtime import _build_order_profit_summary

    summary = _build_order_profit_summary([], total_ad_spend_usd=42.0)
    assert summary["order_count"] == 0
    assert summary["total_revenue_usd"] == 0.0
    assert summary["ad_cost_usd"] == 0.0
    assert summary["unallocated_ad_spend_usd"] == 42.0
    assert summary["total_ad_spend_usd"] == 42.0
    # 没有订单 → 收入 0、成本 0，利润 = -未分摊广告费
    assert summary["profit_with_estimate_usd"] == -42.0


def test_get_realtime_campaign_details_aggregates_per_account_latest_snapshot(monkeypatch):
    """spec 第 14 条同款规则：campaign 明细也要按 (business_date, ad_account_id) 各自
    取最新 snapshot 后合并。落后账户的 campaign 不能整账户被丢弃。"""
    from appcore.order_analytics.realtime import _get_realtime_campaign_details

    target = date(2026, 5, 8)
    tick_at = datetime(2026, 5, 8, 17, 0)

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id" in sql:
            assert "snapshot_at<=%s" in sql
            assert args == (target, tick_at)
            return [
                {"ad_account_id": "act_a", "latest_at": tick_at},
                {"ad_account_id": "act_b", "latest_at": datetime(2026, 5, 8, 16, 50)},
            ]
        if "ad_account_id=%s" in sql:
            ad_account_id = args[1]
            if ad_account_id == "act_a":
                return [
                    {
                        "ad_account_id": "act_a",
                        "ad_account_name": "Account A",
                        "campaign_id": "ca1",
                        "campaign_name": "Camp A1",
                        "normalized_campaign_code": "camp-a1",
                        "result_count": 3,
                        "spend_usd": 600.0,
                        "purchase_value_usd": 1500.0,
                        "impressions": 0,
                        "clicks": 0,
                    },
                ]
            if ad_account_id == "act_b":
                return [
                    {
                        "ad_account_id": "act_b",
                        "ad_account_name": "Account B (lag)",
                        "campaign_id": "cb1",
                        "campaign_name": "Camp B1",
                        "normalized_campaign_code": "camp-b1",
                        "result_count": 1,
                        "spend_usd": 850.0,
                        "purchase_value_usd": 0.0,
                        "impressions": 0,
                        "clicks": 0,
                    },
                ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    rows = _get_realtime_campaign_details(target, tick_at)

    assert len(rows) == 2
    # spend desc 排序后落后账户依然在列表里
    assert rows[0]["ad_account_id"] == "act_b"
    assert rows[1]["ad_account_id"] == "act_a"
    assert sum(float(r["spend_usd"]) for r in rows) == 1450.0


def test_get_realtime_campaign_details_returns_empty_when_no_snapshot(monkeypatch):
    """snapshot_at=None 时不查 DB，直接返回空。避免误打全表 scan。"""
    from appcore.order_analytics.realtime import _get_realtime_campaign_details

    def fake_query(*args, **kwargs):
        raise AssertionError("query should not be called when snapshot_at is None")

    monkeypatch.setattr(oa, "query", fake_query)
    assert _get_realtime_campaign_details(date(2026, 5, 8), None) == []


def test_annotate_campaign_allocation_marks_unmatched_product(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    monkeypatch.setattr(realtime_oa, "resolve_ad_product_match", lambda code: None)
    monkeypatch.setattr(oa, "query", lambda *a, **kw: [])

    campaigns = [{
        "campaign_name": "unknown-campaign",
        "normalized_campaign_code": "unknown-campaign",
        "spend_usd": 12.34,
    }]
    result = realtime_oa._annotate_campaign_allocation(
        campaigns,
        date(2026, 5, 9),
        date(2026, 5, 9),
    )

    row = result["campaigns"][0]
    assert row["allocation_status"] == "unallocated"
    assert row["allocation_reason"] == "unmatched_product"
    assert row["unallocated_spend_usd"] == 12.34
    assert result["unallocated_campaigns"] == [row]
    assert result["unallocated_campaign_summary"] == {"count": 1, "spend_usd": 12.34}


def test_annotate_campaign_allocation_marks_matched_product_without_units(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {
            "id": 427,
            "product_code": "fully-automatic-water-blaster-rjc",
            "name": "ARP9电动水枪",
        },
    )
    monkeypatch.setattr(oa, "query", lambda *a, **kw: [])

    campaigns = [{
        "campaign_name": "fully-automatic-water-blaster",
        "normalized_campaign_code": "fully-automatic-water-blaster",
        "spend_usd": 79.07,
    }]
    result = realtime_oa._annotate_campaign_allocation(
        campaigns,
        date(2026, 5, 9),
        date(2026, 5, 9),
    )

    row = result["campaigns"][0]
    assert row["allocation_status"] == "unallocated"
    assert row["allocation_reason"] == "matched_no_units"
    assert row["matched_product_id"] == 427
    assert row["matched_product_code"] == "fully-automatic-water-blaster-rjc"
    assert row["matched_product_name"] == "ARP9电动水枪"
    assert row["unallocated_spend_usd"] == 79.07
    assert result["unallocated_campaigns"] == [row]


def test_annotate_campaign_allocation_marks_allocated_when_units_exist(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {
            "id": 316,
            "product_code": "sonic-lens-refresher-rjc",
            "name": "隐形眼镜清洗器",
        },
    )

    def fake_query(sql, args=()):
        assert "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id" in sql
        return [{"business_date": date(2026, 5, 9), "product_id": 316, "units": 26}]

    monkeypatch.setattr(oa, "query", fake_query)

    campaigns = [{
        "campaign_name": "sonic-lens-refresher-rjc",
        "normalized_campaign_code": "sonic-lens-refresher-rjc",
        "spend_usd": 221.41,
    }]
    result = realtime_oa._annotate_campaign_allocation(
        campaigns,
        date(2026, 5, 9),
        date(2026, 5, 9),
    )

    row = result["campaigns"][0]
    assert row["allocation_status"] == "allocated"
    assert row["allocation_reason"] == "allocated"
    assert row["matched_product_id"] == 316
    assert row["unallocated_spend_usd"] == 0.0
    assert result["unallocated_campaigns"] == []
    assert result["unallocated_campaign_summary"] == {"count": 0, "spend_usd": 0.0}


def test_realtime_overview_exposes_unallocated_campaigns(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    target = date(2026, 5, 9)
    snapshot_at = datetime(2026, 5, 10, 2, 0)

    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [{
                "id": 879,
                "snapshot_at": snapshot_at,
                "source_run_id": 740,
                "order_count": 0,
                "line_count": 0,
                "units": 0,
                "order_revenue_usd": 0.0,
                "shipping_revenue_usd": 0.0,
                "ad_spend_usd": 12.34,
                "last_order_at": None,
                "order_data_status": "ok",
                "ad_data_status": "ok",
            }]
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql and "GROUP BY ad_account_id" in sql:
            return [{"ad_account_id": "act_a", "latest_at": snapshot_at}]
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql and "ad_account_id=%s" in sql:
            return [{
                "ad_account_id": "act_a",
                "ad_account_name": "Account A",
                "campaign_id": "camp_1",
                "campaign_name": "unknown-campaign",
                "normalized_campaign_code": "unknown-campaign",
                "result_count": 0,
                "spend_usd": 12.34,
                "purchase_value_usd": 0.0,
                "impressions": 0,
                "clicks": 0,
            }]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(realtime_oa, "resolve_ad_product_match", lambda code: None)
    monkeypatch.setattr(realtime_oa, "get_dianxiaomi_product_sales_stats", lambda *a, **kw: [])

    result = oa.get_realtime_roas_overview("2026-05-09", now=datetime(2026, 5, 10, 2, 5))

    assert result["unallocated_campaign_summary"] == {"count": 1, "spend_usd": 12.34}
    assert len(result["unallocated_campaigns"]) == 1
    assert result["unallocated_campaigns"][0]["allocation_reason"] == "unmatched_product"
    assert result["campaigns"][0]["allocation_status"] == "unallocated"
