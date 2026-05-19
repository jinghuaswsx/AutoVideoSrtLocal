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
            "imageUrl": "https://cdn.example.test/listing-main.jpg",
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
        "product_code": "tooth-cleaner",
        "product_main_image_url": "https://cdn.example.test/listing-main.jpg",
        "product_main_image_object_key": None,
        "product_detail_images_json": None,
        "product_assets_error": None,
        "product_cn_name": "",
        "mk_first_material_name": "",
        "mk_first_material_path": "",
        "mk_first_material_url": "",
        "mk_material_error": None,
        "snapshot_date": date(2026, 4, 23),
        "rank_position": 7,
    }


def test_extract_product_page_assets_prefers_og_image_and_collects_detail_images():
    from tools import dianxiaomi_listing_ranking_sync as sync

    html = """
    <html>
      <head><meta property="og:image" content="//cdn.example.test/main.jpg"></head>
      <body>
        <div class="product__description">
          <img src="/details/one.jpg">
          <img data-src="//cdn.example.test/details/two.webp">
        </div>
      </body>
    </html>
    """

    assets = sync.extract_product_page_assets_from_html(
        html,
        base_url="https://shop.example.test/products/tooth-cleaner",
    )

    assert assets == {
        "main_image_url": "https://cdn.example.test/main.jpg",
        "detail_image_urls": [
            "https://shop.example.test/details/one.jpg",
            "https://cdn.example.test/details/two.webp",
        ],
    }


def test_extract_product_page_assets_prefers_first_carousel_image_over_og_image():
    from tools import dianxiaomi_listing_ranking_sync as sync

    html = """
    <html>
      <head><meta property="og:image" content="//cdn.example.test/og.jpg"></head>
      <body>
        <div class="product__media">
          <img src="//cdn.example.test/carousel-first.webp">
          <img src="//cdn.example.test/carousel-second.webp">
        </div>
      </body>
    </html>
    """

    assets = sync.extract_product_page_assets_from_html(
        html,
        base_url="https://shop.example.test/products/foil-covers",
    )

    assert assets["main_image_url"] == "https://cdn.example.test/carousel-first.webp"


def test_cache_product_main_image_writes_deterministic_selection_object_key():
    from tools import dianxiaomi_listing_ranking_sync as sync

    writes = []

    class FakeResponse:
        headers = {"content-type": "image/jpeg"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            del chunk_size
            yield b"image-bytes"

    object_key = sync.cache_product_main_image(
        "https://cdn.example.test/main.jpg?width=800",
        product_id="7540261912642",
        product_code="tooth-cleaner",
        storage_exists_fn=lambda _key: False,
        write_bytes_fn=lambda key, payload: writes.append((key, payload)),
        http_get_fn=lambda *args, **kwargs: FakeResponse(),
    )

    assert object_key.startswith("xuanpin/product-main-images/tooth-cleaner/")
    assert object_key.endswith(".jpg")
    assert writes == [(object_key, b"image-bytes")]


def test_cache_product_main_image_returns_key_when_local_write_survives_remote_backup_error():
    from tools import dianxiaomi_listing_ranking_sync as sync

    written_keys = set()

    class FakeResponse:
        headers = {"content-type": "image/jpeg"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            del chunk_size
            yield b"image-bytes"

    def fake_exists(key):
        return key in written_keys

    def fake_write(key, _payload):
        written_keys.add(key)
        raise RuntimeError("NoSuchBucket")

    object_key = sync.cache_product_main_image(
        "https://cdn.example.test/main.jpg",
        product_id="7540261912642",
        product_code="fitness-band",
        storage_exists_fn=fake_exists,
        write_bytes_fn=fake_write,
        http_get_fn=lambda *args, **kwargs: FakeResponse(),
    )

    assert object_key in written_keys


def test_extract_product_cn_name_from_mingkong_first_material():
    from tools import dianxiaomi_listing_ranking_sync as sync

    result = sync.find_first_mingkong_material_for_product_code(
        [
            {
                "id": 3719,
                "product_links": ["https://shop.example/products/other-product"],
                "videos": [{"name": "2025.01.01-错误产品-原素材.mp4", "path": "uploads/wrong.mp4"}],
            },
            {
                "id": 3720,
                "product_links": ["https://shop.example/products/fitness-band"],
                "videos": [
                    {
                        "name": "2025.12.25-健身脚蹬拉力器-原素材-指派-傅博.mp4",
                        "path": "uploads/fitness.mp4",
                    }
                ],
            },
        ],
        "fitness-band-rjc",
        base_url="https://os.wedev.vip",
    )

    assert result == {
        "product_cn_name": "健身脚蹬拉力器",
        "mk_first_material_name": "2025.12.25-健身脚蹬拉力器-原素材-指派-傅博.mp4",
        "mk_first_material_path": "uploads/fitness.mp4",
        "mk_first_material_url": "https://os.wedev.vip/medias/uploads/fitness.mp4",
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
