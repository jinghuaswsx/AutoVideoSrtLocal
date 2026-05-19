from __future__ import annotations


def test_select_candidate_products_groups_missing_assets_by_product_url():
    from tools import backfill_dianxiaomi_product_assets as backfill

    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return [
            {
                "product_id": "gid-1",
                "product_name": "Fitness Band",
                "product_url": "https://shop.example/products/fitness-band-rjc",
                "ranking_rows": 12,
            }
        ]

    rows = backfill.select_candidate_products(
        limit=25,
        query_fn=fake_query,
        snapshot_date_from="2026-05-18",
        snapshot_date_to="2026-05-18",
    )

    assert rows[0]["product_code"] == "fitness-band"
    sql, args = calls[0]
    assert "product_assets_synced_at IS NULL" in sql
    assert "GROUP BY" in sql
    assert "product_url" in sql
    assert args == ("2026-05-18", "2026-05-18", 25)


def test_select_candidate_products_force_omits_missing_assets_predicate():
    from tools import backfill_dianxiaomi_product_assets as backfill

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    backfill.select_candidate_products(limit=10, query_fn=fake_query, force=True)

    assert "product_assets_synced_at IS NULL" not in captured["sql"]
    assert captured["args"] == (10,)


def test_update_backfilled_product_by_url_writes_asset_fields_to_all_matching_rows():
    from tools import backfill_dianxiaomi_product_assets as backfill

    calls = []

    def fake_execute(sql, args=()):
        calls.append((sql, args))
        return 7

    changed = backfill.update_backfilled_product(
        {
            "product_url": "https://shop.example/products/fitness-band-rjc",
            "product_id": "7540261912642",
        },
        {
            "product_code": "fitness-band",
            "product_main_image_url": "https://cdn.example/main.jpg",
            "product_main_image_object_key": "xuanpin/product-main-images/fitness/main.jpg",
            "product_detail_images_json": '["https://cdn.example/detail.jpg"]',
            "product_assets_error": None,
            "product_cn_name": "健身脚蹬拉力器",
            "mk_first_material_name": "2025.12.25-健身脚蹬拉力器-原素材-指派-傅博.mp4",
            "mk_first_material_path": "uploads/fitness.mp4",
            "mk_first_material_url": "https://os.wedev.vip/medias/uploads/fitness.mp4",
            "mk_material_error": None,
        },
        execute_fn=fake_execute,
    )

    assert changed == 7
    sql, args = calls[0]
    assert "product_assets_synced_at=NOW()" in sql
    assert "WHERE product_url = %s" in sql
    assert args[-1] == "https://shop.example/products/fitness-band-rjc"
    assert args[0] == "fitness-band"
    assert args[5] == "健身脚蹬拉力器"


def test_update_backfilled_product_without_url_marks_product_id_rows():
    from tools import backfill_dianxiaomi_product_assets as backfill

    calls = []

    changed = backfill.update_backfilled_product(
        {"product_url": "", "product_id": "7540261912642"},
        {"product_code": "", "product_assets_error": "missing product_url"},
        execute_fn=lambda sql, args=(): calls.append((sql, args)) or 3,
    )

    assert changed == 3
    sql, args = calls[0]
    assert "WHERE product_id = %s AND (product_url IS NULL OR product_url = '')" in sql
    assert args[-1] == "7540261912642"
    assert args[4] == "missing product_url"


def test_run_backfill_dry_run_does_not_enrich_or_update():
    from tools import backfill_dianxiaomi_product_assets as backfill

    def fail_enrich(*_args, **_kwargs):
        raise AssertionError("dry-run must not fetch product pages or Mingkong")

    def fail_update(*_args, **_kwargs):
        raise AssertionError("dry-run must not update database")

    summary = backfill.run_backfill(
        limit=2,
        batch_size=10,
        dry_run=True,
        select_candidates_fn=lambda limit, **_kwargs: [
            {"product_id": "1", "product_name": "A", "product_url": "https://shop/products/a", "product_code": "a", "ranking_rows": 5},
            {"product_id": "2", "product_name": "B", "product_url": "https://shop/products/b", "product_code": "b", "ranking_rows": 6},
        ][:limit],
        enrich_rows_fn=fail_enrich,
        update_product_fn=fail_update,
    )

    assert summary["dry_run"] is True
    assert summary["products_seen"] == 2
    assert summary["ranking_rows_matched"] == 11
    assert summary["products_updated"] == 0


def test_run_backfill_dry_run_all_mode_stops_after_seen_batch():
    from tools import backfill_dianxiaomi_product_assets as backfill

    calls = []

    def same_candidates(limit, **_kwargs):
        calls.append(limit)
        return [
            {"product_id": "1", "product_name": "A", "product_url": "https://shop/products/a", "product_code": "a", "ranking_rows": 5},
        ]

    summary = backfill.run_backfill(
        limit=0,
        batch_size=10,
        dry_run=True,
        select_candidates_fn=same_candidates,
        enrich_rows_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not enrich")),
        update_product_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not update")),
    )

    assert summary["products_seen"] == 1
    assert calls == [10, 10]


def test_run_backfill_updates_each_unique_product_once_and_counts_rows():
    from tools import backfill_dianxiaomi_product_assets as backfill

    updates = []

    def fake_enrich(rows, **_kwargs):
        out = []
        for row in rows:
            enriched = dict(row)
            enriched.update(
                {
                    "product_main_image_object_key": f"key/{row['product_code']}.jpg",
                    "product_cn_name": f"中文-{row['product_code']}",
                }
            )
            out.append(enriched)
        return out

    def fake_update(product, enriched):
        updates.append((product, enriched))
        return int(product["ranking_rows"])

    summary = backfill.run_backfill(
        limit=2,
        batch_size=10,
        select_candidates_fn=lambda limit, **_kwargs: [
            {"product_id": "1", "product_name": "A", "product_url": "https://shop/products/a", "product_code": "a", "ranking_rows": 5},
            {"product_id": "2", "product_name": "B", "product_url": "https://shop/products/b", "product_code": "b", "ranking_rows": 6},
        ][:limit],
        enrich_rows_fn=fake_enrich,
        update_product_fn=fake_update,
    )

    assert summary["products_seen"] == 2
    assert summary["products_updated"] == 2
    assert summary["ranking_rows_updated"] == 11
    assert [item[1]["product_cn_name"] for item in updates] == ["中文-a", "中文-b"]


def test_run_backfill_marks_failed_product_so_it_will_not_repeat_forever():
    from tools import backfill_dianxiaomi_product_assets as backfill

    updates = []

    def fail_enrich(_rows, **_kwargs):
        raise RuntimeError("page timeout")

    def fake_update(product, enriched):
        updates.append((product, enriched))
        return int(product["ranking_rows"])

    summary = backfill.run_backfill(
        limit=0,
        batch_size=10,
        select_candidates_fn=lambda limit, **_kwargs: [
            {"product_id": "1", "product_name": "A", "product_url": "https://shop/products/a", "product_code": "a", "ranking_rows": 5},
        ][:limit],
        enrich_rows_fn=fail_enrich,
        update_product_fn=fake_update,
    )

    assert summary["products_seen"] == 1
    assert summary["products_failed"] == 1
    assert summary["ranking_rows_updated"] == 5
    assert updates[0][1]["product_assets_error"] == "page timeout"
