from __future__ import annotations

from datetime import date


def test_build_listing_payload_uses_rolling_7_day_window_and_sales_sort():
    from tools import dianxiaomi_listing_ranking_sync as sync

    payload = sync.build_listing_payload(
        date(2026, 5, 12),
        page_no=2,
        page_size=100,
        window_days=7,
    )

    assert payload["beginDate"] == "2026-05-06"
    assert payload["endDate"] == "2026-05-12"
    assert payload["pageNo"] == 2
    assert payload["pageSize"] == 100
    assert payload["sortType"] == "paidProductCount"
    assert payload["isDesc"] == "1"
    assert payload["searchType"] == "productId"
    assert payload["searchValue"] == ""
    assert payload["searchCondition"] == "2"
    assert sync.LISTING_API_URL.endswith("/api/stat/product/statSalesPageListNew.json")


def test_select_missing_dates_uncapped_archive_requires_only_existing_rows():
    from tools import dianxiaomi_listing_ranking_sync as sync

    missing = sync.select_missing_dates(
        start_date=date(2026, 4, 23),
        end_date=date(2026, 4, 26),
        existing_counts={
            date(2026, 4, 23): 300,
            date(2026, 4, 24): 0,
            "2026-04-25": 2,
        },
        target_rows=0,
    )

    assert [item.isoformat() for item in missing] == [
        "2026-04-24",
        "2026-04-26",
    ]


def test_normalize_listing_row_maps_paid_sales_fields():
    from tools import dianxiaomi_listing_ranking_sync as sync

    normalized = sync.normalize_listing_row(
        {
            "productId": 123456,
            "productName": "Portable Tooth Cleaner",
            "sourceUrl": "https://newjoyloo.com/products/tooth-cleaner",
            "shopName": "newjoyloo",
            "platform": "shopify",
            "parentSku": "TOOTH-01",
            "paidOrderCount": "12",
            "paidProductCount": "34",
            "paidAmountCny": 123.4,
            "averagePaidAmountCny": "3.63",
            "refundOrderCount": 1,
            "refundProductCount": "2",
            "refundAmountCny": "4.5",
            "refundRate": 5,
        },
        snapshot_date=date(2026, 4, 23),
        rank_position=7,
    )

    assert normalized == {
        "product_id": "123456",
        "product_name": "Portable Tooth Cleaner",
        "product_url": "https://newjoyloo.com/products/tooth-cleaner",
        "store": "newjoyloo",
        "platform": "shopify",
        "parent_sku": "TOOTH-01",
        "order_count": 12,
        "sales_count": 34,
        "revenue_main": "CNY 123.40",
        "revenue_split": "CNY 3.63",
        "refund_orders": 1,
        "refund_qty": 2,
        "refund_amt": "CNY 4.50",
        "refund_rate": "5%",
        "media_product_id": None,
        "snapshot_date": date(2026, 4, 23),
        "rank_position": 7,
    }


def test_collect_top_rankings_fetches_all_pages_and_filters_zero_sales_when_uncapped():
    from tools import dianxiaomi_listing_ranking_sync as sync

    calls: list[int] = []

    def fake_fetch_page(day: date, page_no: int, page_size: int):
        calls.append(page_no)
        start = (page_no - 1) * page_size
        return {
            "code": 0,
            "data": {
                "page": {
                    "pageNo": page_no,
                    "pageSize": page_size,
                    "totalSize": 6,
                    "totalPage": 3,
                    "list": [
                        {
                            "productId": str(start + index + 1),
                            "productName": f"Product {start + index + 1}",
                            "paidProductCount": 0 if start + index + 1 in {2, 4} else 10 - start - index,
                        }
                        for index in range(page_size)
                    ],
                }
            },
        }

    rows, stats = sync.collect_top_rankings_for_date(
        date(2026, 4, 23),
        fetch_page=fake_fetch_page,
        target_rows=0,
        page_size=2,
    )

    assert calls == [1, 2, 3]
    assert [row["rank_position"] for row in rows] == [1, 3, 5, 6]
    assert [row["product_id"] for row in rows] == ["1", "3", "5", "6"]
    assert stats["pages_fetched"] == 3
    assert stats["api_total_size"] == 6
    assert stats["rows_fetched"] == 4


def test_daily_target_date_defaults_to_yesterday():
    from tools import dianxiaomi_listing_ranking_sync as sync

    assert sync.resolve_daily_target_date(today=date(2026, 5, 12), offset_days=1) == date(2026, 5, 11)
    assert sync.resolve_daily_target_date(today=date(2026, 5, 12), offset_days=0) == date(2026, 5, 12)


def test_resolve_rolling_dates_includes_today_by_default():
    from tools import dianxiaomi_listing_ranking_sync as sync

    dates = sync.resolve_rolling_dates(today=date(2026, 5, 12), rolling_days=7, offset_days=0)

    assert [item.isoformat() for item in dates] == [
        "2026-05-06",
        "2026-05-07",
        "2026-05-08",
        "2026-05-09",
        "2026-05-10",
        "2026-05-11",
        "2026-05-12",
    ]


def test_main_runs_windows_local_mysql_guard_before_scheduled_task_record(monkeypatch):
    import pytest
    from tools import dianxiaomi_listing_ranking_sync as sync

    def block_local_mysql():
        raise RuntimeError("blocked local mysql")

    def fail_start_run(_task_code):
        raise AssertionError("start_run must not run before the local MySQL guard")

    monkeypatch.setattr(sync, "guard_against_windows_local_mysql", block_local_mysql)
    monkeypatch.setattr(sync.scheduled_tasks, "start_run", fail_start_run)

    with pytest.raises(RuntimeError, match="blocked local mysql"):
        sync.main(["--mode", "date", "--target-date", "2026-04-23"])
