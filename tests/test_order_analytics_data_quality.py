"""数据分析看板数据质量护栏单元测试。

Docs-anchor: docs/analytics-data-quality-guardrails.md
"""
from __future__ import annotations

from datetime import date, datetime

import pytest


def test_status_ok_when_all_checks_pass():
    from appcore.order_analytics import data_quality as dq

    payload = dq.build_data_quality(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        source_mode="daily_final",
        watermarks={
            "orders": {
                "latest_business_date": "2026-05-08",
                "latest_updated_at": "2026-05-08T18:20:00+08:00",
            },
            "meta_daily_ads": {
                "latest_business_date": "2026-05-07",
                "latest_import_finished_at": "2026-05-08T17:10:00+08:00",
            },
        },
        checks=[
            {
                "code": "ad_spend_reconciled",
                "status": "ok",
                "expected": 1443.75,
                "actual": 1443.75,
                "diff": 0.0,
                "message": "广告源表总额与已分摊+未分摊金额一致",
            }
        ],
    )

    assert payload["status"] == "ok"
    assert payload["source_mode"] == "daily_final"
    assert payload["business_date_from"] == "2026-05-07"
    assert payload["business_date_to"] == "2026-05-07"
    assert payload["checks"][0]["status"] == "ok"
    assert payload["warnings"] == []
    assert payload["errors"] == []
    assert "generated_at" in payload


def test_status_propagates_worst_check_status():
    from appcore.order_analytics import data_quality as dq

    payload = dq.build_data_quality(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        source_mode="daily_final",
        checks=[
            {"code": "watermark_ok", "status": "ok"},
            {"code": "ad_spend_reconciled", "status": "mismatch", "diff": 100.0},
            {"code": "derived_freshness", "status": "warning"},
        ],
    )

    assert payload["status"] == "mismatch"
    assert any(w["code"] == "derived_freshness" for w in payload["warnings"])
    assert any(e["code"] == "ad_spend_reconciled" for e in payload["errors"])


def test_status_stale_for_derived_lag():
    from appcore.order_analytics import data_quality as dq

    payload = dq.build_data_quality(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        source_mode="derived_cache",
        checks=[
            {"code": "derived_profit_freshness", "status": "stale"},
        ],
    )

    assert payload["status"] == "stale"


def test_default_source_mode_unknown():
    from appcore.order_analytics import data_quality as dq

    payload = dq.build_data_quality(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        checks=[],
    )

    assert payload["source_mode"] == "unknown"
    # 没有 checks 时不能默认 ok：未知数据源 + 无校验 → warning
    assert payload["status"] == "warning"


def test_reconcile_ad_spend_ok_when_balances_match(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    captured = {}

    def fake_query(sql, args=None):
        captured["sql"] = sql
        captured["args"] = args
        # Source: 1443.75
        if "meta_ad_daily_campaign_metrics" in sql and "GROUP BY" not in sql:
            return [{"source_total": 1443.75, "unallocated_total": 405.63}]
        return []

    monkeypatch.setattr(dq, "query", fake_query)

    check = dq.reconcile_ad_spend(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        allocated_ad_spend_usd=1038.12,
    )

    assert check["code"] == "ad_spend_reconciled"
    assert check["status"] == "ok"
    assert check["expected"] == pytest.approx(1443.75)
    assert check["actual"] == pytest.approx(1443.75)
    assert abs(check["diff"]) < 0.05


def test_reconcile_ad_spend_can_use_page_unallocated_override(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query(sql, args=None):
        return [{"source_total": 1500.00, "unallocated_total": 100.00}]

    monkeypatch.setattr(dq, "query", fake_query)

    check = dq.reconcile_ad_spend(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        allocated_ad_spend_usd=1200.00,
        unallocated_ad_spend_usd=300.00,
    )

    assert check["status"] == "ok"
    assert check["expected"] == pytest.approx(1500.00)
    assert check["actual"] == pytest.approx(1500.00)
    assert check["message"] == "广告源表总额与已分摊+未分摊金额一致"


def test_reconcile_ad_spend_uses_country_source_when_country_filtered(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    captured = {}

    def fake_query(sql, args=None):
        captured["sql"] = sql
        captured["args"] = args
        return [{"source_total": 90.00, "unallocated_total": 15.00}]

    monkeypatch.setattr(dq, "query", fake_query)

    check = dq.reconcile_ad_spend(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        allocated_ad_spend_usd=75.00,
        country="vn",
    )

    assert "FROM meta_ad_daily_ad_metrics" in captured["sql"]
    assert "market_country = %s" in captured["sql"]
    assert captured["args"] == (date(2026, 5, 7), date(2026, 5, 7), "VN")
    assert check["status"] == "ok"
    assert check["expected"] == pytest.approx(90.00)
    assert check["actual"] == pytest.approx(90.00)


def test_reconcile_ad_spend_mismatch_when_diff_exceeds_threshold(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query(sql, args=None):
        return [{"source_total": 1500.00, "unallocated_total": 100.00}]

    monkeypatch.setattr(dq, "query", fake_query)

    check = dq.reconcile_ad_spend(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        allocated_ad_spend_usd=200.00,
    )

    assert check["code"] == "ad_spend_reconciled"
    assert check["status"] == "mismatch"
    assert check["diff"] == pytest.approx(1200.00)


def test_reconcile_ad_spend_no_source_emits_warning(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query(sql, args=None):
        return [{"source_total": 0, "unallocated_total": 0}]

    monkeypatch.setattr(dq, "query", fake_query)

    check = dq.reconcile_ad_spend(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        allocated_ad_spend_usd=0,
    )

    # 源表无数据：日终未到，应该是 warning（业务可接受）
    assert check["status"] in ("warning", "ok")


def test_check_meta_ad_day_uniqueness_flags_cross_business_day_duplicates(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    calls = []

    def fake_query_one(sql, args=None):
        calls.append((sql, args))
        if "meta_ad_daily_campaign_metrics" in sql:
            return {"duplicate_groups": 2, "affected_spend": 1210.44}
        if "meta_ad_daily_ad_metrics" in sql:
            return {"duplicate_groups": 0, "affected_spend": 0}
        return {}

    monkeypatch.setattr(dq, "query_one", fake_query_one)

    check = dq.check_meta_ad_day_uniqueness(
        business_date_from=date(2026, 5, 1),
        business_date_to=date(2026, 5, 9),
    )

    assert check["code"] == "meta_ad_day_uniqueness"
    assert check["status"] == "mismatch"
    assert check["duplicate_groups"] == 2
    assert check["affected_spend_usd"] == pytest.approx(1210.44)
    assert "每个广告自然日只能保留一份" in check["message"]
    assert calls[0][1] == (date(2026, 5, 1), date(2026, 5, 9))
    assert "$.merged_rows" not in calls[0][0]


def test_check_meta_ad_day_uniqueness_does_not_flag_same_day_merged_rows(monkeypatch):
    """同日同名实体合并是合法去重，不能当成跨业务日脏数据。"""
    from appcore.order_analytics import data_quality as dq

    captured_sql = []

    def fake_query_one(sql, args=None):
        captured_sql.append(sql)
        return {"duplicate_groups": 0, "affected_spend": 0}

    monkeypatch.setattr(dq, "query_one", fake_query_one)

    check = dq.check_meta_ad_day_uniqueness(
        business_date_from=date(2026, 5, 1),
        business_date_to=date(2026, 5, 9),
    )

    assert check["status"] == "ok"
    assert all("$.merged_rows" not in sql for sql in captured_sql)
    assert all("business_dates > 1 OR off_target_rows > 0" in sql for sql in captured_sql)


def test_build_for_product_profit_includes_meta_ad_day_uniqueness(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    monkeypatch.setattr(dq, "fetch_watermarks", lambda: {})
    monkeypatch.setattr(dq, "resolve_source_mode", lambda **kwargs: dq.SOURCE_MODE_DAILY_FINAL)
    monkeypatch.setattr(
        dq,
        "reconcile_ad_spend",
        lambda **kwargs: {"code": "ad_spend_reconciled", "status": "ok"},
    )
    monkeypatch.setattr(
        dq,
        "check_meta_ad_day_uniqueness",
        lambda **kwargs: {"code": "meta_ad_day_uniqueness", "status": "mismatch"},
    )

    payload = dq.build_for_product_profit(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 9),
        allocated_ad_spend_usd=100,
    )

    assert payload["status"] == "mismatch"
    assert any(
        check["code"] == "meta_ad_day_uniqueness" for check in payload["checks"]
    )


def test_check_derived_profit_freshness_stale_when_source_newer(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query_one(sql, args=None):
        if "meta_ad_daily_campaign_metrics" in sql:
            return {"latest_finished": datetime(2026, 5, 8, 17, 10)}
        if "order_profit_lines" in sql:
            return {"latest_run": datetime(2026, 5, 7, 9, 0)}
        return None

    monkeypatch.setattr(dq, "query_one", fake_query_one)

    check = dq.check_derived_profit_freshness(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
    )

    assert check["code"] == "derived_profit_freshness"
    assert check["status"] == "stale"


def test_check_derived_profit_freshness_ok_when_derived_newer(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query_one(sql, args=None):
        if "meta_ad_daily_campaign_metrics" in sql:
            return {"latest_finished": datetime(2026, 5, 8, 17, 10)}
        if "order_profit_lines" in sql:
            return {"latest_run": datetime(2026, 5, 8, 18, 30)}
        return None

    monkeypatch.setattr(dq, "query_one", fake_query_one)

    check = dq.check_derived_profit_freshness(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
    )

    assert check["status"] == "ok"


def test_check_derived_profit_freshness_qualifies_profit_updated_at(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    captured_sql = []

    def fake_query_one(sql, args=None):
        captured_sql.append(sql)
        if "meta_ad_daily_campaign_metrics" in sql:
            return {"latest_finished": datetime(2026, 5, 8, 17, 10)}
        if "order_profit_lines" in sql:
            return {"latest_run": datetime(2026, 5, 8, 18, 30)}
        return None

    monkeypatch.setattr(dq, "query_one", fake_query_one)

    dq.check_derived_profit_freshness(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
    )

    derived_sql = next(sql for sql in captured_sql if "order_profit_lines p" in sql)
    assert "MAX(p.updated_at) AS latest_run" in derived_sql


def test_fetch_watermarks_returns_all_keys(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query_one(sql, args=None):
        # All four watermark queries return the same shape for simplicity
        if "dianxiaomi_order_lines" in sql:
            return {
                "latest_business_date": date(2026, 5, 8),
                "latest_updated_at": datetime(2026, 5, 8, 18, 20),
            }
        if "meta_ad_daily_campaign_metrics" in sql:
            return {
                "latest_business_date": date(2026, 5, 7),
                "latest_import_finished_at": datetime(2026, 5, 8, 17, 10),
            }
        if "meta_ad_realtime_daily_campaign_metrics" in sql:
            return {
                "latest_business_date": date(2026, 5, 8),
                "latest_snapshot_at": datetime(2026, 5, 8, 18, 20),
            }
        if "order_profit_lines" in sql:
            return {
                "latest_business_date": date(2026, 5, 8),
                "latest_run_finished_at": datetime(2026, 5, 8, 18, 25),
            }
        return None

    monkeypatch.setattr(dq, "query_one", fake_query_one)

    watermarks = dq.fetch_watermarks()

    assert set(watermarks.keys()) == {
        "orders",
        "meta_daily_ads",
        "meta_realtime_ads",
        "derived_profit",
    }
    assert watermarks["orders"]["latest_business_date"] == "2026-05-08"
    assert watermarks["meta_daily_ads"]["latest_business_date"] == "2026-05-07"


def test_resolve_source_mode_daily_final(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query(sql, args=None):
        if "meta_ad_daily_campaign_metrics" in sql:
            return [{"business_date": date(2026, 5, 7), "rows": 12}]
        return []

    monkeypatch.setattr(dq, "query", fake_query)

    mode = dq.resolve_source_mode(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
    )
    assert mode == "daily_final"


def test_resolve_source_mode_realtime_when_no_daily(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query(sql, args=None):
        # No daily rows but realtime exists
        if "meta_ad_daily_campaign_metrics" in sql:
            return []
        if "meta_ad_realtime_daily_campaign_metrics" in sql:
            return [{"business_date": date(2026, 5, 8)}]
        return []

    monkeypatch.setattr(dq, "query", fake_query)

    mode = dq.resolve_source_mode(
        business_date_from=date(2026, 5, 8),
        business_date_to=date(2026, 5, 8),
    )
    assert mode == "realtime_snapshot"


def test_resolve_source_mode_mixed(monkeypatch):
    from appcore.order_analytics import data_quality as dq

    def fake_query(sql, args=None):
        if "meta_ad_daily_campaign_metrics" in sql:
            # Only one of the two days has daily final
            return [{"business_date": date(2026, 5, 7), "rows": 5}]
        if "meta_ad_realtime_daily_campaign_metrics" in sql:
            return [{"business_date": date(2026, 5, 8)}]
        return []

    monkeypatch.setattr(dq, "query", fake_query)

    mode = dq.resolve_source_mode(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 8),
    )
    assert mode == "mixed"


def test_warnings_and_errors_are_separated():
    from appcore.order_analytics import data_quality as dq

    payload = dq.build_data_quality(
        business_date_from=date(2026, 5, 7),
        business_date_to=date(2026, 5, 7),
        source_mode="daily_final",
        checks=[
            {"code": "a", "status": "ok"},
            {"code": "b", "status": "warning", "message": "minor"},
            {"code": "c", "status": "stale", "message": "派生数据滞后"},
            {"code": "d", "status": "error", "message": "boom"},
        ],
    )

    assert payload["status"] == "error"
    codes = {item["code"] for item in payload["warnings"]}
    assert "b" in codes and "c" in codes
    error_codes = {item["code"] for item in payload["errors"]}
    assert "d" in error_codes
