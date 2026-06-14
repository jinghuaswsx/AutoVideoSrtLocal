import io
from appcore import order_analytics as oa
from appcore.order_analytics import refund_verification as rv


def test_aggregate_payments_refunds_sums_abs_by_order():
    payments = [
        {"type": "refund", "order_name": "#23863", "amount_usd": -56.89},
        {"type": "refund", "order_name": "#23863", "amount_usd": -10.0},
        {"type": "chargeback", "order_name": "#100", "amount_usd": -20.0},
        {"type": "charge", "order_name": "#200", "amount_usd": 30.0},
    ]
    out = rv.aggregate_payment_refunds(payments)
    assert out["23863"] == 66.89
    assert out["100"] == 20.0
    assert "200" not in out


def test_extract_order_refund_statuses():
    orders = [
        {"order_name": "#300", "financial_status": "refunded"},
        {"order_name": "#301", "financial_status": "partially_refunded"},
        {"order_name": "#302", "financial_status": "paid"},
    ]
    out = rv.extract_order_refund_statuses(orders)
    assert out == {"300": "refunded", "301": "partially_refunded"}


def test_aggregate_refunds_from_db(monkeypatch):
    def fake_query(sql, args=()):
        if "shopify_payments_transactions" in sql:
            return [
                {"order_name": "#23863", "total_refund": 66.89},
                {"order_name": "#100",   "total_refund": 20.0},
            ]
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    out = rv.aggregate_refunds_from_db(site_code="newjoy")
    assert out["23863"] == 66.89
    assert out["100"] == 20.0


def test_build_verification_rows_classifies_with_site(monkeypatch):
    def fake_query(sql, args=()):
        return [
            {"extended_order_id": "23863", "dxm_package_id": "PKG-A",
             "site_code": "newjoy", "revenue": 50.0},
            {"extended_order_id": "301", "dxm_package_id": "PKG-B",
             "site_code": "newjoy", "revenue": 40.0},
        ]
    monkeypatch.setattr(oa, "query", fake_query)
    refunds = {"23863": 66.89, "999": 12.0}
    statuses = {"301": "refunded"}
    rows = rv.build_verification_rows(refunds, statuses, site_code="newjoy")
    by_order = {r["extended_order_id"]: r for r in rows}
    assert by_order["23863"]["match_status"] == "anomaly"
    assert by_order["301"]["match_status"] == "anomaly"
    assert by_order["999"]["match_status"] == "unmatched"


def test_create_batch(monkeypatch):
    executed = []
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append(sql))
    def fake_query(sql, args=()):
        if "dianxiaomi_order_lines" in sql:
            return [{"extended_order_id": "23863", "dxm_package_id": "P1", "site_code": "newjoy", "revenue": 100.0}]
        if "order_profit_lines" in sql:
            return [{"extended_order_id": "23863", "reserve": 1.0}]
        if "LAST_INSERT_ID" in sql:
            return [{"id": 7}]
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    summary = rv.create_batch(
        refunds={"23863": 56.89}, statuses={},
        source_files={"payments_csv": "p.csv"}, created_by="admin", site_code="newjoy",
    )
    assert summary["batch_id"] == 7
    assert summary["matched_count"] == 1


def test_apply_discard_revert(monkeypatch):
    calls = []
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): calls.append(sql))
    rv.apply_batch(7)
    rv.discard_batch(8)
    rv.revert_batch(9)
    assert any("applied" in s and "batches" in s for s in calls)
    assert any("discarded" in s for s in calls)
