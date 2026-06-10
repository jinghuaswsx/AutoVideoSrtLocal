from decimal import Decimal

import pytest

from appcore.order_analytics.shopify_unsettled_payouts import parse_payout_file


pytestmark = pytest.mark.allow_shopify_browser_automation


SAMPLE_CSV = """Transaction Date,Type,Order,Card Brand,Card Source,Payout Status,Payout Date,Payout ID,Available On,Amount,Fee,Net,Currency
2026-06-06 04:15:48 -0700,charge,#25665,american_express,online,pending,2026/6/12,,2026/6/12,$21.83,$1.52,$20.31,USD
2026-06-06 04:14:56 -0700,charge,#25664,unknown,online,paid,2026/6/12,pout_1,2026/6/6,41.58,3.78,37.80,USD
2026-06-06 04:13:50 -0700,charge,#25663,master,online,scheduled,2026/6/12,pout_2,2026/6/12,36.76,2.13,34.63,USD
2026-06-06 04:12:01 -0700,refund,#25662,visa,online,refunded,2026/6/12,pout_3,2026/6/12,-8.00,0.00,-8.00,USD
"""


def test_parse_payout_file_summarizes_pending_paid_scheduled():
    parsed = parse_payout_file(SAMPLE_CSV.encode("utf-8"), "payments.csv")

    summary = parsed["summary"]
    buckets = summary["buckets"]

    assert summary["total_rows"] == 4
    assert summary["included_row_count"] == 3
    assert summary["ignored_row_count"] == 1
    assert summary["currency"] == "USD"

    assert buckets["pending"]["order_count"] == 1
    assert buckets["pending"]["amount_total"] == Decimal("21.83")
    assert buckets["pending"]["fee_total"] == Decimal("1.52")
    assert buckets["pending"]["net_total"] == Decimal("20.31")

    assert buckets["paid"]["order_count"] == 1
    assert buckets["paid"]["amount_total"] == Decimal("41.58")
    assert buckets["paid"]["fee_total"] == Decimal("3.78")
    assert buckets["paid"]["net_total"] == Decimal("37.80")

    assert buckets["scheduled"]["order_count"] == 1
    assert buckets["scheduled"]["amount_total"] == Decimal("36.76")
    assert buckets["scheduled"]["fee_total"] == Decimal("2.13")
    assert buckets["scheduled"]["net_total"] == Decimal("34.63")

    assert parsed["rows"][0]["row_number"] == 2
    assert parsed["rows"][0]["order_name"] == "#25665"


def test_parse_payout_file_requires_shopify_payment_columns():
    with pytest.raises(ValueError, match="缺少必需列"):
        parse_payout_file(b"Order,Amount,Net\n#1,10,9\n", "payments.csv")
