from datetime import date, datetime
from unittest.mock import patch

import pytest

from appcore import weekly_roas_report as wrr


@pytest.fixture
def sample_summary():
    return {
        "summary": {
            "order_count": 100,
            "line_count": 110,
            "units": 200,
            "order_revenue": 5000.0,
            "line_revenue": 5000.0,
            "shipping_revenue": 1000.0,
            "revenue_with_shipping": 6000.0,
            "ad_spend": 4000.0,
            "true_roas": 1.5,
            "meta_purchase_value": 5400.0,
            "meta_roas": 1.35,
            "meta_purchases": 95,
        },
        "rows": [
            {
                "meta_business_date": date(2026, 4, 27),
                "window_start_at": datetime(2026, 4, 27, 16, 0),
                "window_end_at": datetime(2026, 4, 28, 16, 0),
                "order_count": 50,
                "order_revenue": 2500.0,
                "shipping_revenue": 500.0,
                "revenue_with_shipping": 3000.0,
                "ad_spend": 2000.0,
                "true_roas": 1.5,
                "meta_purchase_value": 2700.0,
                "meta_roas": 1.35,
                "meta_purchases": 48,
                "line_revenue": 2500.0,
            },
            {
                "meta_business_date": date(2026, 4, 28),
                "window_start_at": datetime(2026, 4, 28, 16, 0),
                "window_end_at": datetime(2026, 4, 29, 16, 0),
                "order_count": 50,
                "order_revenue": 2500.0,
                "shipping_revenue": 500.0,
                "revenue_with_shipping": 3000.0,
                "ad_spend": 2000.0,
                "true_roas": 1.5,
                "meta_purchase_value": 2700.0,
                "meta_roas": 1.35,
                "meta_purchases": 47,
                "line_revenue": 2500.0,
            },
        ],
        "period": {"start": date(2026, 4, 27), "end": date(2026, 4, 28)},
    }


def test_previous_complete_week_basic():
    # 2026-05-05 是周二
    week_start, week_end = wrr.previous_complete_week(datetime(2026, 5, 5, 9, 0, 0))
    assert week_start == date(2026, 4, 27)
    assert week_end == date(2026, 5, 3)


def test_previous_complete_week_on_monday():
    # 周一 2026-05-04，应得到上一个完整周（4-27 ~ 5-03）
    week_start, week_end = wrr.previous_complete_week(datetime(2026, 5, 4, 0, 0, 1))
    assert week_start == date(2026, 4, 27)
    assert week_end == date(2026, 5, 3)


def test_previous_complete_week_on_sunday():
    # 周日 2026-05-03，本周一 = 2026-04-27，上一周一 = 2026-04-20
    week_start, week_end = wrr.previous_complete_week(datetime(2026, 5, 3, 23, 0, 0))
    assert week_start == date(2026, 4, 20)
    assert week_end == date(2026, 4, 26)


def test_compute_weekly_report_adds_sales_gap(sample_summary):
    with patch("appcore.weekly_roas_report.get_true_roas_summary", return_value=sample_summary):
        report = wrr.compute_weekly_report(date(2026, 4, 27), date(2026, 4, 28))
    assert report["summary"]["sales_gap"] == 600.0  # 6000 - 5400
    assert all(row["sales_gap"] == 300.0 for row in report["rows"])  # 3000 - 2700
    assert report["period"]["week_start"] == date(2026, 4, 27)
    assert report["period"]["week_end"] == date(2026, 4, 28)


def test_compute_weekly_report_handles_missing_meta(sample_summary):
    sample_summary["summary"]["meta_purchase_value"] = 0
    sample_summary["rows"][0]["meta_purchase_value"] = 0
    with patch("appcore.weekly_roas_report.get_true_roas_summary", return_value=sample_summary):
        report = wrr.compute_weekly_report(date(2026, 4, 27), date(2026, 4, 28))
    assert report["summary"]["sales_gap"] == 6000.0
    assert report["rows"][0]["sales_gap"] == 3000.0
