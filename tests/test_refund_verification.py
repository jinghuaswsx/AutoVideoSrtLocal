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
