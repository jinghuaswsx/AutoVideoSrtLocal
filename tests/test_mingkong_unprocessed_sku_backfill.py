from __future__ import annotations

from tools import mingkong_unprocessed_sku_backfill as mod


def test_find_unprocessed_products_filters_configured_rows_but_allows_empty_base():
    captured = {}

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 1, "product_code": "sample-rjc"}]

    rows = mod.find_unprocessed_products(
        limit=5,
        product_id=1,
        product_code="sample-rjc",
        query_fn=fake_query,
    )

    assert rows == [{"id": 1, "product_code": "sample-rjc"}]
    assert "NOT EXISTS (" in captured["sql"]
    assert "FROM media_product_skus s" in captured["sql"]
    assert "NULLIF(TRIM(s.dianxiaomi_sku), '') IS NOT NULL" in captured["sql"]
    assert "NULLIF(TRIM(s.dianxiaomi_sku_code), '') IS NOT NULL" in captured["sql"]
    assert "NULLIF(TRIM(s.dianxiaomi_name), '') IS NOT NULL" not in captured["sql"]
    assert "COALESCE(s.manual_override, 0)=1" in captured["sql"]
    assert "COALESCE(p.archived, 0)=0" in captured["sql"]
    assert "(p.listing_status IS NULL OR p.listing_status=%s)" in captured["sql"]
    assert captured["args"] == (1, "sample-rjc", "上架", 5)


def test_build_default_targets_prefers_mingkong_procurement_and_existing_ids():
    payload = {
        "product": {"shopifyid": "shopify-product"},
        "items": [
            {
                "shopify_product_id": "",
                "shopify_variant_id": "variant-1",
                "shopify_sku": "front-sku",
                "variant_title": "Blue",
                "dianxiaomi_sku": "dxm-sku",
                "purchase_1688_url": "https://detail.1688.com/offer/123.html",
                "dxm03": {
                    "commodity": {"sku_code": "dxm-code", "name": "DXM 商品"},
                    "pairing": {"alibaba_product_id": "old-offer", "sku_id_alibaba": "old-sku-id"},
                },
                "mingkong": {
                    "sku": "mk-sku",
                    "product_sku": "mk-product-sku",
                    "sku_code": "mk-code",
                    "name": "明空商品",
                    "purchase_1688_url": "https://detail.1688.com/offer/456.html",
                    "alibaba_product_id": "456",
                    "sku_id_alibaba": "mk-sku-id",
                    "image_url": "https://example.test/a.jpg",
                },
            }
        ],
    }

    targets = mod.build_default_targets(payload)

    assert targets == [
        {
            "shopify_product_id": "shopify-product",
            "shopify_variant_id": "variant-1",
            "shopify_sku": "front-sku",
            "shopify_currency": "USD",
            "variant_title": "Blue",
            "dianxiaomi_sku": "mk-sku",
            "dianxiaomi_product_sku": "mk-product-sku",
            "dianxiaomi_sku_code": "mk-code",
            "dianxiaomi_name": "明空商品",
            "purchase_1688_url": "https://detail.1688.com/offer/456.html",
            "product_id_alibaba": "456",
            "sku_id_alibaba": "mk-sku-id",
            "image_url": "https://example.test/a.jpg",
        }
    ]


def test_configured_local_sku_row_count_ignores_empty_shopify_base_rows():
    rows = [
        {
            "shopify_variant_id": "variant-1",
            "shopify_variant_title": "Blue",
            "shopify_sku": "front-sku",
            "dianxiaomi_name": "Only a product title",
            "source": "mingkong_batch_sync_repaired",
        },
        {
            "shopify_variant_id": "variant-2",
            "dianxiaomi_sku": "dxm-sku",
            "source": "mingkong_batch_sync",
        },
        {
            "shopify_variant_id": "variant-3",
            "manual_override": 1,
            "source": "manual_edit",
        },
    ]

    assert mod.configured_local_sku_row_count(rows) == 2


def test_run_product_sync_skips_products_with_configured_local_skus(monkeypatch):
    monkeypatch.setattr(mod.medias, "list_product_skus", lambda _product_id: [{"id": 10, "dianxiaomi_sku": "dxm"}])
    monkeypatch.setattr(mod, "product_order_summary", lambda *_args, **_kwargs: {"total": 0})

    result = mod.run_product_sync(
        {"id": 1, "product_code": "sample-rjc", "name": "样品"},
        execute=True,
    )

    assert result["status"] == "skipped_configured_local_skus"
    assert result["message"] == "本地已有 1 条已配置 SKU 行，批量任务不处理"


def test_run_product_sync_replaces_existing_empty_base_rows_in_dry_run(monkeypatch):
    monkeypatch.setattr(
        mod.medias,
        "list_product_skus",
        lambda _product_id: [{"id": 10, "shopify_variant_id": "old-empty-base"}],
    )
    monkeypatch.setattr(
        mod.pairing,
        "build_workbench_payload",
        lambda *_args, **_kwargs: {
            "summary": {"source": "shopify_public_base"},
            "items": [{"shopify_variant_id": "variant-1", "variant_title": "Blue"}],
        },
    )
    monkeypatch.setattr(
        mod.pairing,
        "build_target_sku_import_pairs",
        lambda *_args, **_kwargs: [{"shopify_variant_id": "variant-1"}],
    )
    monkeypatch.setattr(mod, "product_order_summary", lambda *_args, **_kwargs: {"total": 0})

    result = mod.run_product_sync(
        {"id": 1, "product_code": "sample-rjc", "name": "样品"},
        execute=False,
    )

    assert result["status"] == "dry_run"
    assert result["existing_empty_base_count"] == 1
    assert result["local_sku_count"] == 1


def test_force_reset_no_orders_skips_product_when_orders_exist(monkeypatch):
    monkeypatch.setattr(mod.medias, "list_product_skus", lambda _product_id: [{"id": 10, "dianxiaomi_sku": "dxm"}])
    monkeypatch.setattr(
        mod,
        "product_order_summary",
        lambda *_args, **_kwargs: {"total": 2, "counts": {"shopify_sku": 2}},
    )

    result = mod.run_product_sync(
        {"id": 1, "product_code": "sample-rjc", "name": "样品"},
        execute=True,
        force_reset_no_orders=True,
    )

    assert result["status"] == "skipped_has_orders"
    assert result["order_summary"]["total"] == 2


def test_force_reset_no_orders_ignores_configured_rows_when_order_count_is_zero(monkeypatch):
    monkeypatch.setattr(mod.medias, "list_product_skus", lambda _product_id: [{"id": 10, "dianxiaomi_sku": "dxm"}])
    monkeypatch.setattr(mod, "product_order_summary", lambda *_args, **_kwargs: {"total": 0})
    monkeypatch.setattr(
        mod.pairing,
        "build_workbench_payload",
        lambda *_args, **_kwargs: {
            "summary": {"source": "mingkong_local_library"},
            "items": [{"shopify_variant_id": "variant-1", "variant_title": "Blue"}],
        },
    )
    monkeypatch.setattr(
        mod.pairing,
        "build_target_sku_import_pairs",
        lambda *_args, **_kwargs: [{"shopify_variant_id": "variant-1", "dianxiaomi_sku": "mk-dxm-sku"}],
    )

    result = mod.run_product_sync(
        {"id": 1, "product_code": "sample-rjc", "name": "样品"},
        execute=False,
        force_reset_no_orders=True,
    )

    assert result["status"] == "dry_run"
    assert result["configured_local_sku_count"] == 1
    assert result["local_sku_count"] == 1


def test_product_order_summary_checks_exact_raw_and_sku_keys():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        if "raw_line_json LIKE" in sql:
            return {"c": 0, "latest": None}
        if "product_sku IN" in sql:
            return {"c": 1, "latest": "2026-06-01 10:00:00"}
        if "FROM shopify_orders WHERE product_id" in sql:
            return {"c": 0, "latest": None}
        if "lineitem_sku IN" in sql:
            return {"c": 0, "latest": None}
        return {"c": 0, "latest": None}

    summary = mod.product_order_summary(
        {"id": 1, "product_code": "sample-rjc", "shopifyid": "shopify-product"},
        [{"shopify_variant_id": "variant-1", "shopify_sku": "front-sku", "dianxiaomi_sku": "dxm-sku"}],
        query_one_fn=fake_query_one,
    )

    assert summary["total"] == 1
    assert summary["counts"]["dianxiaomi_sku"] == 1
    assert summary["sku_key_count"] == 4
    assert any("product_code=%s" in sql for sql, _args in calls)
    assert any("raw_order_json LIKE" in sql for sql, _args in calls)
