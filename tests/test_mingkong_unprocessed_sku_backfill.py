from __future__ import annotations

from tools import mingkong_unprocessed_sku_backfill as mod


def test_find_unprocessed_products_filters_existing_sku_rows_and_processed_products():
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
    assert "LEFT JOIN media_product_skus s ON s.product_id=p.id" in captured["sql"]
    assert "s.id IS NULL" in captured["sql"]
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


def test_run_product_sync_skips_products_with_existing_local_skus(monkeypatch):
    monkeypatch.setattr(mod.medias, "list_product_skus", lambda _product_id: [{"id": 10}])

    result = mod.run_product_sync(
        {"id": 1, "product_code": "sample-rjc", "name": "样品"},
        execute=True,
    )

    assert result["status"] == "skipped_existing_local_skus"
    assert result["message"] == "本地已有 1 条 SKU 行，批量任务不处理"
