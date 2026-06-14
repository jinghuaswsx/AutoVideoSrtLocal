"""Dynamic Shopify fee integration tests for order profit backfill."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from tools import order_profit_backfill as backfill


def _line(**overrides):
    base = {
        "dxm_order_line_id": 1001,
        "product_id": 7,
        "quantity": 1,
        "line_amount": Decimal("40.00"),
        "order_amount": Decimal("100.00"),
        "ship_amount": Decimal("8.00"),
        "buyer_country": "DE",
        "order_paid_at": datetime(2026, 6, 13, 1, 0, 0),
        "paid_at": datetime(2026, 6, 13, 1, 0, 0),
        "attribution_time_at": None,
        "order_created_at": datetime(2026, 6, 12, 23, 0, 0),
        "meta_business_date": date(2026, 6, 13),
        "dxm_package_id": "PKG-1001",
        "dxm_order_id": "DXM-1001",
        "site_code": "newjoy",
        "extended_order_id": "#3001",
        "package_number": "3001",
        "logistic_fee": None,
        "purchase_price": Decimal("10.00"),
        "packet_cost_actual": Decimal("5.00"),
        "packet_cost_estimated": None,
    }
    base.update(overrides)
    return base


def test_should_skip_line_before_dynamic_effective_at(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    assert backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 6, 12, 23, 59, 59), "existing_profit_line_id": 9}
    )
    assert not backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 6, 12, 23, 59, 59)}
    )
    assert not backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 6, 13, 1, 0, 0), "existing_profit_line_id": 9}
    )
    assert backfill._should_skip_for_dynamic_fee_boundary(
        {
            "order_paid_at": None,
            "attribution_time_at": None,
            "order_created_at": None,
            "existing_profit_line_id": 9,
        }
    )
    assert not backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": None, "attribution_time_at": None, "order_created_at": None}
    )


def test_resolve_order_time_uses_documented_coalesce_without_paid_at():
    order_paid_at = datetime(2026, 6, 13, 10, 0, 0)
    attribution_time_at = datetime(2026, 6, 13, 9, 30, 0)
    order_created_at = datetime(2026, 6, 13, 8, 0, 0)
    paid_at = datetime(2026, 6, 14, 12, 0, 0)

    assert backfill._resolve_order_time(
        {
            "order_paid_at": order_paid_at,
            "paid_at": paid_at,
            "attribution_time_at": attribution_time_at,
            "order_created_at": order_created_at,
        }
    ) == order_paid_at
    assert backfill._resolve_order_time(
        {
            "order_paid_at": None,
            "paid_at": paid_at,
            "attribution_time_at": attribution_time_at,
            "order_created_at": order_created_at,
        }
    ) == attribution_time_at
    assert backfill._resolve_order_time(
        {
            "order_paid_at": None,
            "paid_at": paid_at,
            "attribution_time_at": None,
            "order_created_at": order_created_at,
        }
    ) == order_created_at
    assert backfill._resolve_order_time(
        {
            "order_paid_at": None,
            "paid_at": paid_at,
            "attribution_time_at": None,
            "order_created_at": None,
        }
    ) is None


def test_process_line_passes_resolved_fee_to_profit_calculation(monkeypatch):
    captured = {}

    def fake_calculate_line_profit(line_input, **kwargs):
        captured["line_input"] = line_input
        return {"status": "ok", "dxm_order_line_id": line_input["dxm_order_line_id"]}

    monkeypatch.setattr(backfill, "calculate_line_profit", fake_calculate_line_profit)
    monkeypatch.setattr(backfill, "get_sku_daily_units", lambda **kwargs: 1)
    monkeypatch.setattr(backfill, "get_sku_daily_ad_spend", lambda **kwargs: 0)

    fee_result = {
        "shopify_fee_usd": 6.72,
        "shopify_tier": "dynamic_region_rate",
        "presentment_currency": "EUR",
        "shopify_fee_source": "dynamic_region_rate",
        "shopify_fee_rate": 0.07542,
        "shopify_fee_rate_region": "europe",
        "shopify_fee_basis": {"snapshot_id": 9},
    }

    result, _business_date = backfill._process_line(
        _line(),
        order_total_amount=100.0,
        order_shipping=8.0,
        sku_units_cache={},
        sku_spend_cache={},
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
        exchange_rate_basis=None,
        shopify_fee_result=fee_result,
    )

    assert result["status"] == "ok"
    assert captured["line_input"]["site_code"] == "newjoy"
    assert captured["line_input"]["extended_order_id"] == "#3001"
    assert captured["line_input"]["package_number"] == "3001"
    assert captured["line_input"]["shopify_fee_result"] is fee_result
    assert captured["line_input"]["order_total_revenue_usd"] == 108.0


def test_backfill_skips_pre_effective_package_once_without_rewriting_history(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")
    lines = [
        _line(
            dxm_order_line_id=3001,
            dxm_package_id="PKG-LEGACY",
            line_amount=Decimal("40.00"),
            order_paid_at=datetime(2026, 6, 12, 23, 59, 59),
            existing_profit_line_id=901,
        ),
        _line(
            dxm_order_line_id=3002,
            dxm_package_id="PKG-LEGACY",
            line_amount=Decimal("60.00"),
            order_paid_at=datetime(2026, 6, 12, 23, 59, 59),
            existing_profit_line_id=902,
        ),
    ]
    upserts: list[dict] = []
    finish_calls: list[dict] = []

    monkeypatch.setattr(backfill, "query", lambda sql, params=(): lines)
    monkeypatch.setattr(backfill, "start_profit_run", lambda **kwargs: 123)
    monkeypatch.setattr(backfill, "finish_profit_run", lambda **kwargs: finish_calls.append(kwargs))
    monkeypatch.setattr(backfill, "get_unallocated_ad_spend", lambda **kwargs: 0)
    monkeypatch.setattr(backfill, "upsert_profit_line", lambda *args, **kwargs: upserts.append(kwargs))
    monkeypatch.setattr(
        backfill,
        "_process_line",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("_process_line called")),
    )

    result = backfill.backfill(
        date(2026, 6, 13),
        date(2026, 6, 13),
        dry_run=False,
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert upserts == []
    assert result["totals"]["lines_total"] == 0
    assert result["totals"]["legacy_fee_boundary_skipped"] == 1
    assert finish_calls[0]["summary"]["legacy_fee_boundary_skipped"] == 1


def test_backfill_skips_pre_effective_order_once_when_package_id_missing(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")
    lines = [
        _line(
            dxm_order_line_id=3101,
            dxm_package_id="",
            package_number="PN-LEGACY",
            extended_order_id="#LEGACY",
            line_amount=Decimal("40.00"),
            order_paid_at=datetime(2026, 6, 12, 23, 59, 59),
            existing_profit_line_id=911,
        ),
        _line(
            dxm_order_line_id=3102,
            dxm_package_id="",
            package_number="PN-LEGACY",
            extended_order_id="#LEGACY",
            line_amount=Decimal("60.00"),
            order_paid_at=datetime(2026, 6, 12, 23, 59, 59),
            existing_profit_line_id=912,
        ),
    ]
    upserts: list[dict] = []

    monkeypatch.setattr(backfill, "query", lambda sql, params=(): lines)
    monkeypatch.setattr(backfill, "start_profit_run", lambda **kwargs: 123)
    monkeypatch.setattr(backfill, "finish_profit_run", lambda **kwargs: None)
    monkeypatch.setattr(backfill, "get_unallocated_ad_spend", lambda **kwargs: 0)
    monkeypatch.setattr(backfill, "upsert_profit_line", lambda *args, **kwargs: upserts.append(kwargs))
    monkeypatch.setattr(
        backfill,
        "_process_line",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("_process_line called")),
    )

    result = backfill.backfill(
        date(2026, 6, 13),
        date(2026, 6, 13),
        dry_run=False,
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert upserts == []
    assert result["totals"]["lines_total"] == 0
    assert result["totals"]["legacy_fee_boundary_skipped"] == 1


def test_backfill_processes_missing_profit_lines_when_dynamic_effective_at_unconfigured(monkeypatch):
    monkeypatch.delenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", raising=False)
    monkeypatch.setattr("config.Config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "", raising=False)
    lines = [_line(order_paid_at=datetime(2026, 6, 13, 10, 0, 0))]
    upserts: list[dict] = []
    process_calls: list[int] = []

    monkeypatch.setattr(backfill, "query", lambda sql, params=(): lines)
    monkeypatch.setattr(backfill, "start_profit_run", lambda **kwargs: 123)
    monkeypatch.setattr(backfill, "finish_profit_run", lambda **kwargs: None)
    monkeypatch.setattr(backfill, "get_unallocated_ad_spend", lambda **kwargs: 0)
    monkeypatch.setattr(backfill, "upsert_profit_line", lambda result, **kwargs: upserts.append(result))

    def fake_process_line(line, **kwargs):
        process_calls.append(line["dxm_order_line_id"])
        return (
            {
                "status": "ok",
                "dxm_order_line_id": line["dxm_order_line_id"],
                "profit_usd": 1,
                "missing_fields": [],
            },
            line["meta_business_date"],
        )

    monkeypatch.setattr(backfill, "_process_line", fake_process_line)

    result = backfill.backfill(
        date(2026, 6, 13),
        date(2026, 6, 13),
        dry_run=False,
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert process_calls == [1001]
    assert len(upserts) == 1
    assert result["totals"]["lines_total"] == 1
    assert result["totals"]["lines_ok"] == 1
    assert result["totals"]["legacy_fee_boundary_skipped"] == 0


def test_backfill_resolves_fee_once_per_package_and_reuses_result(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")
    lines = [
        _line(dxm_order_line_id=2001, line_amount=Decimal("40.00")),
        _line(dxm_order_line_id=2002, line_amount=Decimal("60.00")),
    ]
    fee_result = {
        "shopify_fee_usd": 6.72,
        "shopify_fee_source": "dynamic_region_rate",
        "shopify_fee_basis": {"order_total_revenue_usd": 108.0},
    }
    resolver_calls: list[dict] = []
    process_fee_results: list[dict] = []

    monkeypatch.setattr(backfill, "query", lambda sql, params=(): lines)
    monkeypatch.setattr(backfill, "get_unallocated_ad_spend", lambda **kwargs: 0)

    def fake_resolver(**kwargs):
        resolver_calls.append(kwargs)
        return fee_result

    def fake_process_line(line, **kwargs):
        process_fee_results.append(kwargs["shopify_fee_result"])
        return (
            {
                "status": "ok",
                "dxm_order_line_id": line["dxm_order_line_id"],
                "profit_usd": 1,
                "missing_fields": [],
            },
            line["meta_business_date"],
        )

    monkeypatch.setattr(backfill, "resolve_shopify_fee_for_order", fake_resolver)
    monkeypatch.setattr(backfill, "_process_line", fake_process_line)

    result = backfill.backfill(
        date(2026, 6, 13),
        date(2026, 6, 13),
        dry_run=True,
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert len(resolver_calls) == 1
    assert resolver_calls[0]["amount"] == 108.0
    assert resolver_calls[0]["buyer_country"] == "DE"
    assert resolver_calls[0]["site_code"] == "newjoy"
    assert resolver_calls[0]["order_names"] == ["#3001", "3001"]
    assert resolver_calls[0]["order_time"] == datetime(2026, 6, 13, 1, 0, 0)
    assert process_fee_results == [fee_result, fee_result]
    assert result["totals"]["shopify_fee_source_counts"] == {"dynamic_region_rate": 1}


def test_backfill_does_not_merge_same_package_number_across_sites(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")
    lines = [
        _line(
            dxm_order_line_id=4001,
            dxm_package_id="",
            dxm_order_id="",
            site_code="newjoy",
            extended_order_id=None,
            package_number="PN-SAME",
            line_amount=Decimal("40.00"),
        ),
        _line(
            dxm_order_line_id=4002,
            dxm_package_id="",
            dxm_order_id="",
            site_code="omurio",
            extended_order_id=None,
            package_number="PN-SAME",
            line_amount=Decimal("60.00"),
        ),
    ]
    resolver_calls: list[dict] = []
    fee_result = {"shopify_fee_usd": 1.0, "shopify_fee_source": "dynamic_region_rate"}

    monkeypatch.setattr(backfill, "query", lambda sql, params=(): lines)
    monkeypatch.setattr(backfill, "get_unallocated_ad_spend", lambda **kwargs: 0)
    monkeypatch.setattr(
        backfill,
        "resolve_shopify_fee_for_order",
        lambda **kwargs: resolver_calls.append(kwargs) or fee_result,
    )

    def fake_process_line(line, **kwargs):
        return (
            {"status": "ok", "dxm_order_line_id": line["dxm_order_line_id"]},
            line["meta_business_date"],
        )

    monkeypatch.setattr(
        backfill,
        "_process_line",
        fake_process_line,
    )

    result = backfill.backfill(
        date(2026, 6, 13),
        date(2026, 6, 13),
        dry_run=True,
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert [call["site_code"] for call in resolver_calls] == ["newjoy", "omurio"]
    assert [call["amount"] for call in resolver_calls] == [48.0, 68.0]
    assert result["totals"]["shopify_fee_source_counts"] == {"dynamic_region_rate": 2}


def test_backfill_resolves_same_shopify_order_once_across_packages(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")
    lines = [
        _line(
            dxm_order_line_id=4101,
            dxm_package_id="PKG-A",
            package_number="PN-A",
            line_amount=Decimal("40.00"),
            ship_amount=Decimal("8.00"),
        ),
        _line(
            dxm_order_line_id=4102,
            dxm_package_id="PKG-B",
            package_number="PN-B",
            line_amount=Decimal("60.00"),
            ship_amount=Decimal("2.00"),
        ),
    ]
    fee_result = {
        "shopify_fee_usd": 6.72,
        "shopify_fee_source": "actual_payment",
        "shopify_fee_basis": {"order_total_revenue_usd": 110.0},
    }
    resolver_calls: list[dict] = []
    process_kwargs: list[dict] = []

    monkeypatch.setattr(backfill, "query", lambda sql, params=(): lines)
    monkeypatch.setattr(backfill, "get_unallocated_ad_spend", lambda **kwargs: 0)
    monkeypatch.setattr(
        backfill,
        "resolve_shopify_fee_for_order",
        lambda **kwargs: resolver_calls.append(kwargs) or fee_result,
    )

    def fake_process_line(line, **kwargs):
        process_kwargs.append(kwargs)
        return (
            {"status": "ok", "dxm_order_line_id": line["dxm_order_line_id"]},
            line["meta_business_date"],
        )

    monkeypatch.setattr(backfill, "_process_line", fake_process_line)

    result = backfill.backfill(
        date(2026, 6, 13),
        date(2026, 6, 13),
        dry_run=True,
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert len(resolver_calls) == 1
    assert resolver_calls[0]["amount"] == 110.0
    assert resolver_calls[0]["order_names"] == ["#3001", "PN-A"]
    assert [kwargs["order_total_amount"] for kwargs in process_kwargs] == [40.0, 60.0]
    assert [kwargs["order_shipping"] for kwargs in process_kwargs] == [8.0, 2.0]
    assert [kwargs["fee_total_revenue_usd"] for kwargs in process_kwargs] == [110.0, 110.0]
    assert result["totals"]["shopify_fee_source_counts"] == {"actual_payment": 1}


def test_history_lines_recomputed_when_effective_at_predates_all_orders(monkeypatch):
    # 开关设到 2026-01-01（早于最早订单 2/24）→ 所有历史订单 order_time >= 生效日
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-01-01T00:00:00+08:00")

    # 已落库历史行（existing_profit_line_id 有值）、订单时间 2/24 → 不应被跳过（会重算覆盖）
    assert not backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 2, 24, 10, 0, 0), "existing_profit_line_id": 5}
    )
    # 注：「真正早于生效日的订单仍跳过」的边界保护已由现成的
    # test_should_skip_line_before_dynamic_effective_at 覆盖，此处不重复
    # （避免 naive datetime 被 is_dynamic_fee_effective 当 UTC 比较的时区陷阱）。
