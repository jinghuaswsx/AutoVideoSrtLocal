from __future__ import annotations

from datetime import date

from appcore.order_analytics.shopify_fee_dynamic import (
    SAMPLE_STATUS_INSUFFICIENT,
    SAMPLE_STATUS_OK_30D,
    SAMPLE_STATUS_OK_7D,
    build_snapshot_row,
    infer_store_code_from_source_csv,
    load_best_fee_rate_snapshot,
    region_for_presentment_currency,
    save_fee_rate_snapshots,
    select_snapshot_window,
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
