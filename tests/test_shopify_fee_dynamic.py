from __future__ import annotations

from datetime import date, datetime

import appcore.order_analytics.shopify_fee_dynamic as fee_dynamic
from appcore.order_analytics.shopify_fee_dynamic import (
    SAMPLE_STATUS_INSUFFICIENT,
    SAMPLE_STATUS_OK_30D,
    SAMPLE_STATUS_OK_7D,
    build_snapshot_row,
    infer_store_code_from_source_csv,
    load_best_fee_rate_snapshot,
    region_for_presentment_currency,
    refresh_fee_rate_snapshots,
    save_fee_rate_snapshots,
    select_snapshot_window,
)
from appcore.order_analytics.shopify_fee_resolver import (
    FEE_SOURCE_ACTUAL_PAYMENT,
    FEE_SOURCE_DYNAMIC_REGION_RATE,
    FEE_SOURCE_LEGACY_STRATEGY_C,
    FEE_SOURCE_STRATEGY_C_FALLBACK,
    is_dynamic_fee_effective,
    resolve_shopify_fee_for_order,
)


def test_region_for_presentment_currency():
    assert region_for_presentment_currency("USD") == "us"
    assert region_for_presentment_currency("eur") == "europe"
    assert region_for_presentment_currency("GBP") == "europe"
    assert region_for_presentment_currency("JPY") == "other"
    assert region_for_presentment_currency(None) == "other"


def test_infer_store_code_from_source_csv():
    assert infer_store_code_from_source_csv("newjoyloo__newjoyloo0606.csv") == "newjoy"
    assert infer_store_code_from_source_csv("Omurio__omurio0606.csv") == "omurio"
    assert infer_store_code_from_source_csv("") == "all"


def test_build_snapshot_row_keeps_fixed_fee_separate():
    row = build_snapshot_row(
        store_code="newjoy",
        region="europe",
        window_start_date=date(2026, 5, 30),
        window_end_date=date(2026, 6, 5),
        window_days=7,
        orders_count=3290,
        amount_usd=88165.45,
        fee_usd=6649.36,
        source_csvs=["newjoyloo__newjoyloo0606.csv"],
        sample_status=SAMPLE_STATUS_OK_7D,
    )

    assert row["store_code"] == "newjoy"
    assert row["region"] == "europe"
    assert row["effective_rate"] == round(6649.36 / 88165.45, 8)
    expected_variable_rate = (6649.36 - 3290 * 0.30) / 88165.45
    assert row["variable_rate"] == round(expected_variable_rate, 8)
    assert row["fixed_fee_per_order"] == 0.30
    assert row["source_csvs_json"] == ["newjoyloo__newjoyloo0606.csv"]


def test_select_snapshot_window_prefers_sufficient_7d():
    selected = select_snapshot_window(
        seven_day={"orders_count": 100, "amount_usd": 1000.0, "fee_usd": 70.0},
        thirty_day={"orders_count": 500, "amount_usd": 5000.0, "fee_usd": 350.0},
    )
    assert selected["sample_status"] == SAMPLE_STATUS_OK_7D
    assert selected["window_days"] == 7


def test_select_snapshot_window_uses_30d_when_7d_is_small():
    selected = select_snapshot_window(
        seven_day={"orders_count": 99, "amount_usd": 990.0, "fee_usd": 70.0},
        thirty_day={"orders_count": 300, "amount_usd": 3000.0, "fee_usd": 210.0},
    )
    assert selected["sample_status"] == SAMPLE_STATUS_OK_30D
    assert selected["window_days"] == 30


def test_select_snapshot_window_marks_insufficient():
    selected = select_snapshot_window(
        seven_day={"orders_count": 40, "amount_usd": 400.0, "fee_usd": 28.0},
        thirty_day={"orders_count": 299, "amount_usd": 2990.0, "fee_usd": 209.3},
    )
    assert selected["sample_status"] == SAMPLE_STATUS_INSUFFICIENT
    assert selected["window_days"] == 30


def test_save_fee_rate_snapshots_inserts_rows_in_one_transaction(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.executemany_calls = []

        def executemany(self, sql, params_list):
            self.executemany_calls.append((sql, params_list))

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.autocommit_values = []
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def autocommit(self, value):
            self.autocommit_values.append(value)

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    conn = FakeConnection()

    row = build_snapshot_row(
        store_code="newjoy",
        region="us",
        window_start_date=date(2026, 5, 30),
        window_end_date=date(2026, 6, 5),
        window_days=7,
        orders_count=389,
        amount_usd=12258.58,
        fee_usd=473.62,
        source_csvs=["newjoyloo__newjoyloo0606.csv"],
        sample_status=SAMPLE_STATUS_OK_7D,
    )

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.get_conn", lambda: conn)

    saved = save_fee_rate_snapshots([row])

    assert saved == 1
    assert conn.autocommit_values == [False, True]
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert conn.closed is True
    assert len(conn.cursor_obj.executemany_calls) == 1
    sql, params_list = conn.cursor_obj.executemany_calls[0]
    assert "insert into shopify_fee_rate_snapshots" in sql.lower()
    assert len(params_list) == 1
    assert params_list[0][0] == "newjoy"
    assert params_list[0][1] == "us"


def test_save_fee_rate_snapshots_returns_zero_without_connection(monkeypatch):
    def fail_get_conn():
        raise AssertionError("get_conn should not be called for empty rows")

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.get_conn", fail_get_conn)

    assert save_fee_rate_snapshots([]) == 0


def test_refresh_fee_rate_snapshots_uses_max_transaction_date_and_7d_window(monkeypatch):
    saved_rows = []
    query_calls = []

    def fake_query(sql, params=None):
        query_calls.append((sql, params))
        normalized_sql = sql.lower()
        if "max(date(transaction_date))" in normalized_sql:
            return [{"max_date": "2026-06-06"}]
        if "date_sub" in normalized_sql and params[1] == 6:
            return [
                {
                    "store_code": "newjoy",
                    "region": "europe",
                    "orders_count": 3290,
                    "amount_usd": 88165.45,
                    "fee_usd": 6649.36,
                }
            ]
        if "date_sub" in normalized_sql and params[1] == 29:
            return [
                {
                    "store_code": "newjoy",
                    "region": "europe",
                    "orders_count": 9000,
                    "amount_usd": 240000.0,
                    "fee_usd": 18000.0,
                }
            ]
        raise AssertionError(f"unexpected query: {sql}")

    def fake_save(rows):
        saved_rows.extend(rows)
        return len(rows)

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_dynamic.save_fee_rate_snapshots",
        fake_save,
    )

    result = refresh_fee_rate_snapshots(source_csvs=["newjoyloo__newjoyloo0606.csv"])

    assert result == {
        "saved": 2,
        "window_end_date": date(2026, 6, 6),
        "source_csvs": ["newjoyloo__newjoyloo0606.csv"],
    }
    assert len(saved_rows) == 2
    rows_by_store = {row["store_code"]: row for row in saved_rows}
    assert set(rows_by_store) == {"newjoy", "all"}
    assert rows_by_store["newjoy"]["region"] == "europe"
    assert rows_by_store["newjoy"]["window_start_date"] == date(2026, 5, 31)
    assert rows_by_store["newjoy"]["window_end_date"] == date(2026, 6, 6)
    assert rows_by_store["newjoy"]["window_days"] == 7
    assert rows_by_store["newjoy"]["orders_count"] == 3290
    assert rows_by_store["newjoy"]["sample_status"] == SAMPLE_STATUS_OK_7D
    assert rows_by_store["newjoy"]["source_csvs_json"] == ["newjoyloo__newjoyloo0606.csv"]
    assert rows_by_store["all"]["region"] == "europe"
    assert rows_by_store["all"]["orders_count"] == 3290
    assert rows_by_store["all"]["amount_usd"] == 88165.45
    assert rows_by_store["all"]["fee_usd"] == 6649.36
    assert rows_by_store["all"]["sample_status"] == SAMPLE_STATUS_OK_7D
    assert "source_csv in (%s)" in query_calls[0][0].lower()
    assert query_calls[0][1] == ("newjoyloo__newjoyloo0606.csv",)
    assert query_calls[1][1] == (
        date(2026, 6, 6),
        6,
        date(2026, 6, 6),
        "newjoyloo__newjoyloo0606.csv",
    )


def test_refresh_fee_rate_snapshots_uses_30d_when_7d_is_insufficient(monkeypatch):
    saved_rows = []

    def fake_query(sql, params=None):
        normalized_sql = sql.lower()
        if "max(date(transaction_date))" in normalized_sql:
            return [{"max_date": date(2026, 6, 6)}]
        if "date_sub" in normalized_sql and params[1] == 6:
            return [
                {
                    "store_code": "omurio",
                    "region": "us",
                    "orders_count": 99,
                    "amount_usd": 990.0,
                    "fee_usd": 39.0,
                }
            ]
        if "date_sub" in normalized_sql and params[1] == 29:
            return [
                {
                    "store_code": "omurio",
                    "region": "us",
                    "orders_count": 300,
                    "amount_usd": 3000.0,
                    "fee_usd": 120.0,
                }
            ]
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_dynamic.save_fee_rate_snapshots",
        lambda rows: saved_rows.extend(rows) or len(rows),
    )

    result = refresh_fee_rate_snapshots(source_csvs=["omurio__payments.csv"])

    assert result["saved"] == 2
    rows_by_store = {row["store_code"]: row for row in saved_rows}
    assert set(rows_by_store) == {"omurio", "all"}
    assert rows_by_store["omurio"]["region"] == "us"
    assert rows_by_store["omurio"]["window_start_date"] == date(2026, 5, 8)
    assert rows_by_store["omurio"]["window_days"] == 30
    assert rows_by_store["omurio"]["orders_count"] == 300
    assert rows_by_store["omurio"]["sample_status"] == SAMPLE_STATUS_OK_30D
    assert rows_by_store["all"]["orders_count"] == 300
    assert rows_by_store["all"]["sample_status"] == SAMPLE_STATUS_OK_30D


def test_refresh_fee_rate_snapshots_saves_all_store_region_aggregate(monkeypatch):
    saved_rows = []

    def fake_query(sql, params=None):
        normalized_sql = sql.lower()
        if "max(date(transaction_date))" in normalized_sql:
            return [{"max_date": date(2026, 6, 6)}]
        if "date_sub" in normalized_sql and params[1] == 6:
            return [
                {
                    "store_code": "newjoy",
                    "region": "europe",
                    "orders_count": 70,
                    "amount_usd": 700.0,
                    "fee_usd": 35.0,
                },
                {
                    "store_code": "omurio",
                    "region": "europe",
                    "orders_count": 50,
                    "amount_usd": 500.0,
                    "fee_usd": 25.0,
                },
            ]
        if "date_sub" in normalized_sql and params[1] == 29:
            return [
                {
                    "store_code": "newjoy",
                    "region": "europe",
                    "orders_count": 70,
                    "amount_usd": 700.0,
                    "fee_usd": 35.0,
                },
                {
                    "store_code": "omurio",
                    "region": "europe",
                    "orders_count": 50,
                    "amount_usd": 500.0,
                    "fee_usd": 25.0,
                },
            ]
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_dynamic.save_fee_rate_snapshots",
        lambda rows: saved_rows.extend(rows) or len(rows),
    )

    result = refresh_fee_rate_snapshots(
        source_csvs=["newjoyloo__payments.csv", "omurio__payments.csv"]
    )

    assert result["saved"] == 3
    rows_by_store = {row["store_code"]: row for row in saved_rows}
    assert set(rows_by_store) == {"newjoy", "omurio", "all"}
    assert rows_by_store["newjoy"]["orders_count"] == 70
    assert rows_by_store["omurio"]["orders_count"] == 50
    all_row = rows_by_store["all"]
    assert all_row["region"] == "europe"
    assert all_row["orders_count"] == 120
    assert all_row["amount_usd"] == 1200.0
    assert all_row["fee_usd"] == 60.0
    assert all_row["window_days"] == 7
    assert all_row["sample_status"] == SAMPLE_STATUS_OK_7D


def test_refresh_fee_rate_snapshots_saves_insufficient_sample(monkeypatch):
    saved_rows = []

    def fake_query(sql, params=None):
        normalized_sql = sql.lower()
        if "max(date(transaction_date))" in normalized_sql:
            return [{"max_date": date(2026, 6, 6)}]
        if "date_sub" in normalized_sql and params[1] == 6:
            return [
                {
                    "store_code": "all",
                    "region": "other",
                    "orders_count": 40,
                    "amount_usd": 400.0,
                    "fee_usd": 28.0,
                }
            ]
        if "date_sub" in normalized_sql and params[1] == 29:
            return [
                {
                    "store_code": "all",
                    "region": "other",
                    "orders_count": 299,
                    "amount_usd": 2990.0,
                    "fee_usd": 209.3,
                }
            ]
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_dynamic.save_fee_rate_snapshots",
        lambda rows: saved_rows.extend(rows) or len(rows),
    )

    result = refresh_fee_rate_snapshots(source_csvs=None)

    assert result["saved"] == 1
    assert saved_rows[0]["window_days"] == 30
    assert saved_rows[0]["orders_count"] == 299
    assert saved_rows[0]["sample_status"] == SAMPLE_STATUS_INSUFFICIENT
    assert saved_rows[0]["source_csvs_json"] == []


def test_load_window_aggregates_normalizes_order_name_for_order_count(monkeypatch):
    captured = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)

    fee_dynamic._load_window_aggregates(
        window_end_date=date(2026, 6, 6),
        window_days=7,
        source_csvs=["newjoyloo__payments.csv"],
    )

    normalized_sql = " ".join(captured["sql"].lower().split())
    assert (
        "count(distinct coalesce( nullif( case when left(trim(order_name), 1) = '#' "
        "then substring(trim(order_name), 2) else trim(order_name) end, '' ), "
        "transaction_id )) as orders_count"
    ) in normalized_sql


def test_save_fee_rate_snapshots_rolls_back_and_closes_on_insert_error(monkeypatch):
    class FakeCursor:
        def executemany(self, sql, params_list):
            raise RuntimeError("insert failed")

    class FakeConnection:
        def __init__(self):
            self.autocommit_values = []
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def autocommit(self, value):
            self.autocommit_values.append(value)

        def cursor(self):
            return FakeCursor()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    conn = FakeConnection()
    row = build_snapshot_row(
        store_code="newjoy",
        region="us",
        window_start_date=date(2026, 5, 30),
        window_end_date=date(2026, 6, 5),
        window_days=7,
        orders_count=389,
        amount_usd=12258.58,
        fee_usd=473.62,
        source_csvs=["newjoyloo__newjoyloo0606.csv"],
        sample_status=SAMPLE_STATUS_OK_7D,
    )

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.get_conn", lambda: conn)

    try:
        save_fee_rate_snapshots([row])
    except RuntimeError as exc:
        assert str(exc) == "insert failed"
    else:
        raise AssertionError("expected save_fee_rate_snapshots to re-raise insert error")

    assert conn.autocommit_values == [False, True]
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert conn.closed is True


def test_load_best_fee_rate_snapshot_prefers_store_region(monkeypatch):
    queries = []

    def fake_query(sql, params=None):
        queries.append((sql, params))
        return [
            {
                "id": 9,
                "store_code": "newjoy",
                "region": "europe",
                "window_start_date": date(2026, 5, 30),
                "window_end_date": date(2026, 6, 5),
                "orders_count": 3290,
                "effective_rate": 0.07542,
                "variable_rate": 0.06422,
                "fixed_fee_per_order": 0.30,
                "sample_status": SAMPLE_STATUS_OK_7D,
            }
        ]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)

    snapshot = load_best_fee_rate_snapshot("newjoy", "europe")

    assert snapshot["id"] == 9
    assert snapshot["store_code"] == "newjoy"
    sql = queries[0][0].lower()
    assert "sample_status in ('ok_7d', 'ok_30d')" in sql
    assert "order by window_end_date desc, computed_at desc, id desc" in sql
    assert "limit 1" in sql
    assert queries[0][1] == ("newjoy", "europe")


def test_load_best_fee_rate_snapshot_falls_back_to_all_store_scope(monkeypatch):
    calls = []

    def fake_query(sql, params=None):
        calls.append((sql, params))
        if params[0] == "newjoy":
            return []
        return [
            {
                "id": 22,
                "store_code": "all",
                "region": "other",
                "window_start_date": date(2026, 5, 30),
                "window_end_date": date(2026, 6, 5),
                "orders_count": 400,
                "effective_rate": 0.064,
                "variable_rate": 0.052,
                "fixed_fee_per_order": 0.30,
                "sample_status": SAMPLE_STATUS_OK_7D,
            }
        ]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)

    snapshot = load_best_fee_rate_snapshot("newjoy", "other")

    assert snapshot["id"] == 22
    for sql, _params in calls:
        normalized_sql = sql.lower()
        assert "sample_status in ('ok_7d', 'ok_30d')" in normalized_sql
        assert "order by window_end_date desc, computed_at desc, id desc" in normalized_sql
        assert "limit 1" in normalized_sql
    assert [params for _sql, params in calls] == [("newjoy", "other"), ("all", "other")]


def test_is_dynamic_fee_effective_uses_configured_boundary(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    assert not is_dynamic_fee_effective(datetime(2026, 6, 13, 0, 59, 59))
    assert is_dynamic_fee_effective(datetime(2026, 6, 13, 1, 0, 0))


def test_is_dynamic_fee_effective_reads_current_env_before_config(monkeypatch):
    monkeypatch.setattr(
        "config.Config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT",
        "2026-06-14T00:00:00+00:00",
        raising=False,
    )
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    assert is_dynamic_fee_effective(datetime(2026, 6, 13, 1, 0, 0))


def test_is_dynamic_fee_effective_disables_invalid_boundary(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "not-a-date")

    assert not is_dynamic_fee_effective(datetime(2026, 6, 12, 0, 0, 0))


def test_is_dynamic_fee_effective_disables_empty_boundary(monkeypatch):
    monkeypatch.delenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", raising=False)
    monkeypatch.setattr("config.Config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "", raising=False)

    assert not is_dynamic_fee_effective(datetime(2026, 6, 13, 10, 0, 0))


def test_is_dynamic_fee_effective_disables_missing_order_time(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    assert not is_dynamic_fee_effective(None)


def test_resolver_returns_legacy_strategy_for_pre_effective_order(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="US",
        site_code="newjoy",
        order_names=["#1001"],
        order_time=datetime(2026, 6, 12, 23, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_LEGACY_STRATEGY_C
    assert result["shopify_fee_usd"] > 0
    assert result["shopify_fee_basis"]["fallback_reason"] == "dynamic_fee_not_effective"


def test_resolver_returns_legacy_strategy_when_effective_at_unconfigured(monkeypatch):
    monkeypatch.delenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", raising=False)
    monkeypatch.setattr("config.Config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "", raising=False)

    def fail_query(*args, **kwargs):
        raise AssertionError("actual payment query should not run before dynamic fee is effective")

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fail_query)

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="US",
        site_code="newjoy",
        order_names=["#1001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_LEGACY_STRATEGY_C


def test_resolver_prefers_actual_payment(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T00:00:00+00:00")
    captured = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"order_name": "#2001", "fee_usd": 1.19, "transaction_ids": "11,12"}]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fake_query)

    result = resolve_shopify_fee_for_order(
        amount=22.13,
        buyer_country="DE",
        site_code="newjoy",
        order_names=["#2001", "2001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_ACTUAL_PAYMENT
    assert result["shopify_fee_usd"] == 1.19
    assert result["shopify_fee_basis"]["matched_payment_transaction_ids"] == ["11", "12"]
    assert "lower(source_csv) like %s" in captured["sql"].lower()
    assert captured["params"] == ("#2001", "2001", "newjoyloo__%")


def test_resolver_filters_actual_payment_by_site_code(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T00:00:00+00:00")
    captured = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"order_name": "#2001", "fee_usd": 1.08, "transaction_ids": "omurio-11"}]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fake_query)

    result = resolve_shopify_fee_for_order(
        amount=22.13,
        buyer_country="US",
        site_code="omurio",
        order_names=["#2001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_ACTUAL_PAYMENT
    assert result["shopify_fee_basis"]["matched_payment_transaction_ids"] == ["omurio-11"]
    assert "lower(source_csv) like %s" in captured["sql"].lower()
    assert captured["params"] == ("#2001", "2001", "omurio__%")


def test_resolver_actual_payment_does_not_sum_hash_and_plain_variants(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T00:00:00+00:00")

    captured = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {"order_name": "#2001", "fee_usd": 1.19, "transaction_ids": "11"},
            {"order_name": "2001", "fee_usd": 1.19, "transaction_ids": "12"},
        ]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fake_query)

    result = resolve_shopify_fee_for_order(
        amount=22.13,
        buyer_country="DE",
        site_code="newjoy",
        order_names=["#2001", "2001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_ACTUAL_PAYMENT
    assert result["shopify_fee_usd"] == 1.19
    assert result["shopify_fee_basis"]["matched_payment_transaction_ids"] == ["11"]
    assert "group by order_name" in captured["sql"].lower()


def test_resolver_uses_dynamic_region_rate_when_no_actual_payment(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T00:00:00+00:00")

    def fake_query(sql, params=None):
        return []

    def fake_snapshot(store_code, region):
        assert store_code == "newjoy"
        assert region == "europe"
        return {
            "id": 9,
            "store_code": "newjoy",
            "region": "europe",
            "window_start_date": date(2026, 5, 30),
            "window_end_date": date(2026, 6, 5),
            "effective_rate": 0.07542,
            "variable_rate": 0.06422,
            "fixed_fee_per_order": 0.30,
            "sample_status": SAMPLE_STATUS_OK_7D,
        }

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fake_query)
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_resolver.load_best_fee_rate_snapshot",
        fake_snapshot,
    )

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="DE",
        site_code="newjoy",
        order_names=["#3001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_DYNAMIC_REGION_RATE
    assert result["shopify_fee_usd"] == 6.72
    assert result["shopify_fee_rate"] == 0.07542
    assert result["shopify_fee_rate_region"] == "europe"
    assert result["shopify_fee_basis"]["snapshot_id"] == 9


def test_resolver_falls_back_to_strategy_c(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T00:00:00+00:00")
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_resolver.query",
        lambda sql, params=None: [],
    )
    monkeypatch.setattr(
        "appcore.order_analytics.shopify_fee_resolver.load_best_fee_rate_snapshot",
        lambda store_code, region: None,
    )

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="US",
        site_code="newjoy",
        order_names=["#4001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_STRATEGY_C_FALLBACK
    assert result["shopify_fee_usd"] > 0
    assert result["shopify_fee_basis"]["fallback_reason"] == "no_actual_payment_or_dynamic_snapshot"
