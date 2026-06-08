from __future__ import annotations

from datetime import date, timedelta


def _orders(today: date, days: int, count: int) -> dict[date, int]:
    start = today - timedelta(days=days - 1)
    return {start + timedelta(days=offset): count for offset in range(days)}


def test_classify_product_marks_7d_stable_by_daily_floor_and_total():
    from appcore import media_product_stability as stability

    today = date(2026, 6, 7)
    row = stability.classify_product(
        product_id=1,
        product_code="P1",
        daily_orders=_orders(today, 7, 20),
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 10,
            "delivery_start_time": "2026-06-01T14:00:00",
        },
        today=today,
    )

    assert row["status"] == "stable"
    assert row["stable_7d"] is True
    assert row["last_7d_orders"] == 140
    assert row["stable_marks"] == ["7天稳定"]


def test_classify_product_marks_7d_stable_by_weekly_total():
    from appcore import media_product_stability as stability

    today = date(2026, 6, 7)
    daily = _orders(today, 7, 0)
    daily[today] = 210

    row = stability.classify_product(
        product_id=1,
        daily_orders=daily,
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 10,
            "delivery_start_time": "2026-06-01",
        },
        today=today,
    )

    assert row["status"] == "stable"
    assert row["stable_7d"] is True
    assert row["min_daily_orders_7d"] == 0


def test_classify_product_marks_30d_stable():
    from appcore import media_product_stability as stability

    today = date(2026, 6, 7)
    row = stability.classify_product(
        product_id=1,
        daily_orders=_orders(today, 30, 20),
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 10,
            "delivery_start_time": "2026-05-01",
        },
        today=today,
    )

    assert row["status"] == "stable"
    assert row["stable_30d"] is True
    assert "30天稳定" in row["stable_marks"]


def test_classify_product_splits_secondary_test_stopped_and_never():
    from appcore import media_product_stability as stability

    today = date(2026, 6, 7)
    secondary = stability.classify_product(
        product_id=1,
        daily_orders=_orders(today, 7, 11),
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 5,
            "delivery_start_time": "2026-06-01",
        },
        today=today,
    )
    test = stability.classify_product(
        product_id=2,
        daily_orders=_orders(today, 7, 4),
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 5,
            "delivery_start_time": "2026-06-01",
        },
        today=today,
    )
    stopped = stability.classify_product(
        product_id=3,
        daily_orders=_orders(today, 7, 20),
        ad_summary={"delivery_status": "stopped", "ad_spend_usd": 100},
        today=today,
    )
    never = stability.classify_product(
        product_id=4,
        daily_orders=_orders(today, 7, 20),
        ad_summary={},
        today=today,
    )

    assert secondary["status"] == "secondary_stable"
    assert secondary["stable_marks"] == ["二级稳定"]
    assert test["status"] == "test"
    assert stopped["status"] == "stopped"
    assert stopped["stable_7d"] is False
    assert never["status"] == "never"


def test_classify_product_requires_full_7_delivery_days():
    from appcore import media_product_stability as stability

    today = date(2026, 6, 7)
    row = stability.classify_product(
        product_id=1,
        daily_orders=_orders(today, 7, 0),
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 10,
            "delivery_start_time": "2026-06-02T14:30:00",
        },
        today=today,
    )

    assert row["status"] == "insufficient_history"
    assert row["stable_7d"] is False
    assert row["delivery_age_days"] == 6
    assert row["eligible_for_weekly_analysis"] is False


def test_classify_product_potential_new():
    from appcore import media_product_stability as stability

    today = date(2026, 6, 7)
    row = stability.classify_product(
        product_id=1,
        daily_orders=_orders(today, 7, 2),  # 7d orders = 14 >= 5
        ad_summary={
            "delivery_status": "active",
            "active_7d_ad_spend_usd": 10,
            "delivery_start_time": "2026-06-02T14:30:00",
        },
        today=today,
    )

    assert row["status"] == "potential_new"
    assert row["stable_marks"] == ["潜力新品"]
    assert "达到潜力新品判定标准" in row["details"]["reasons"]


def test_stability_summary_counts_and_limits_rows():
    from appcore import media_product_stability as stability

    rows = [
        {
            "product_id": 1,
            "product_code": "S7",
            "status": "stable",
            "stable_7d": True,
            "stable_30d": False,
            "last_7d_orders": 140,
        },
        {
            "product_id": 2,
            "product_code": "S30",
            "status": "stable",
            "stable_7d": False,
            "stable_30d": True,
            "last_7d_orders": 90,
        },
        {"product_id": 3, "product_code": "P", "status": "secondary_stable", "last_7d_orders": 77},
        {"product_id": 6, "product_code": "LEGACY", "status": "potential", "last_7d_orders": 50},
        {"product_id": 8, "product_code": "NEWBIE", "status": "potential_new", "last_7d_orders": 10},
        {"product_id": 4, "product_code": "T", "status": "test", "last_7d_orders": 8},
        {"product_id": 5, "product_code": "X", "status": "stopped", "last_7d_orders": 0},
        {"product_id": 7, "product_code": "N", "status": "insufficient_history", "last_7d_orders": 80},
    ]

    summary = stability.stability_summary_from_rows(rows, limit=1)

    assert summary["counts"]["stable_total"] == 2
    assert summary["counts"]["stable_7d"] == 1
    assert summary["counts"]["stable_30d"] == 1
    assert summary["counts"]["secondary_stable"] == 1
    assert summary["counts"]["potential"] == 1
    assert summary["counts"]["potential_new"] == 1
    assert summary["counts"]["test"] == 1
    assert summary["counts"]["stopped"] == 1
    assert summary["counts"]["insufficient_history"] == 1
    assert len(summary["buckets"]["stable"]) == 1
    assert summary["buckets"]["stable"][0]["product_code"] == "S7"
