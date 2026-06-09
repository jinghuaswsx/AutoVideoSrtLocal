from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mingkong_product_library_migration_declares_tables_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_09_mingkong_product_library.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "mingkong_product_library_sync_runs",
        "mingkong_products",
        "mingkong_product_variants",
        "mingkong_combo_components",
        "mingkong_procurement_links",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in body

    for key in [
        "uk_mk_products_shopify_product",
        "uk_mk_variant_shopify_variant",
        "uk_mk_combo_component",
        "uk_mk_proc_pairing_row",
        "idx_mk_products_product_code",
        "idx_mk_variant_pair_key",
        "idx_mk_proc_sku",
    ]:
        assert key in body


def test_mingkong_product_library_migration_keeps_long_source_urls_as_text():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_09_mingkong_product_library.sql"
    ).read_text(encoding="utf-8")

    assert "source_url TEXT NULL" in body
    assert "dxm_source_url TEXT NULL" in body
    assert "purchase_1688_url TEXT NULL" in body
    assert "ALTER TABLE mingkong_products" in body
    assert "MODIFY source_url TEXT NULL" in body
    assert "ALTER TABLE mingkong_product_variants" in body
    assert "MODIFY dxm_source_url TEXT NULL" in body
    assert "ALTER TABLE mingkong_procurement_links" in body
    assert "MODIFY purchase_1688_url TEXT NULL" in body


def test_mingkong_product_library_migration_allows_long_sku_values():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_09_mingkong_product_library.sql"
    ).read_text(encoding="utf-8")

    for column in [
        "shopify_sku",
        "pair_key",
        "dxm_sku",
        "dxm_product_sku",
        "combo_dxm_sku",
        "component_sku",
        "sku",
    ]:
        assert f"{column} VARCHAR(512)" in body
    assert "MODIFY shopify_sku VARCHAR(512)" in body
    assert "MODIFY pair_key VARCHAR(512)" in body
    assert "MODIFY dxm_sku VARCHAR(512)" in body
    assert "MODIFY combo_dxm_sku VARCHAR(512)" in body
    assert "MODIFY component_sku VARCHAR(512)" in body
    assert "MODIFY sku VARCHAR(512)" in body


def test_mingkong_product_library_scheduler_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_product_library_sync")

    assert task["code"] == "mingkong_product_library_sync"
    assert task["schedule"] == "每周一 04:00（北京时间）"
    assert task["source_ref"] == "autovideosrt-mingkong-product-library-sync.timer"
    assert task["runner"] == "tools/mingkong_product_library_sync.py --days 0"
    assert task["log_table"] == "mingkong_product_library_sync_runs"


def test_mingkong_product_library_systemd_timer_is_weekly():
    body = (
        ROOT
        / "deploy"
        / "server_browser"
        / "autovideosrt-mingkong-product-library-sync.timer"
    ).read_text(encoding="utf-8")

    assert "OnCalendar=Mon *-*-* 04:00:00" in body
    assert "OnCalendar=*-*-* 04:00:00" not in body


def test_mingkong_product_library_prefers_procurement_candidate_without_shopify_id():
    from appcore import mingkong_product_library as library

    candidates = [
        {"id": 1, "mk_shopify_product_id": "shop-1", "procurement_count": 0},
        {"id": 2, "mk_shopify_product_id": "shop-2", "procurement_count": 5},
        {"id": 3, "mk_shopify_product_id": "shop-3", "procurement_count": 0},
    ]

    assert library._selected_candidate_product_ids(candidates, set()) == [2]


def test_mingkong_product_library_keeps_other_candidates_after_shopify_match():
    from appcore import mingkong_product_library as library

    candidates = [
        {"id": 1, "mk_shopify_product_id": "matched", "procurement_count": 0},
        {"id": 2, "mk_shopify_product_id": "richer", "procurement_count": 0},
        {"id": 3, "mk_shopify_product_id": "other", "procurement_count": 0},
    ]

    assert library._selected_candidate_product_ids(candidates, {"matched"}) == [1, 2, 3]


def test_mingkong_product_library_candidate_ids_include_per_domain_cache(monkeypatch):
    from appcore import mingkong_product_library as library

    def fake_query(sql, args):
        if "FROM media_product_shopify_ids" in sql:
            assert args == (747,)
            return [{"shopify_product_id": "domain-shopify-product"}]
        if "FROM mingkong_material_products" in sql:
            return []
        if "FROM dianxiaomi_product_assets" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(library, "query", fake_query)

    assert library._candidate_shopify_ids(
        {"id": 747, "product_code": "sample-rjc", "shopifyid": "legacy-shopify-product"}
    ) == {"legacy-shopify-product", "domain-shopify-product"}


def test_mingkong_product_library_dedupes_repeated_shopify_products_by_dxm_sku():
    from appcore import mingkong_product_library as library

    rows = [
        {"id": 10, "dxm_sku": "0422-14563244", "mk_shopify_product_id": "shop-a"},
        {"id": 11, "dxm_sku": "0422-14568837", "mk_shopify_product_id": "shop-a"},
        {"id": 20, "dxm_sku": "0422-14563244", "mk_shopify_product_id": "shop-b"},
        {"id": 21, "dxm_sku": "0422-14568837", "mk_shopify_product_id": "shop-b"},
    ]

    deduped = library._dedupe_variant_rows_by_dxm_sku(rows)

    assert [row["id"] for row in deduped] == [10, 11]


def test_mingkong_product_library_dedupe_prefers_rows_with_components():
    from appcore import mingkong_product_library as library

    rows = [
        {
            "id": 10,
            "dxm_sku": "0422-14563009",
            "mk_shopify_product_id": "shop-without-components",
            "_component_count": 0,
        },
        {
            "id": 20,
            "dxm_sku": "0422-14563009",
            "mk_shopify_product_id": "shop-with-components",
            "_component_count": 1,
        },
    ]

    deduped = library._dedupe_variant_rows_by_dxm_sku(rows)

    assert [row["id"] for row in deduped] == [20]


def test_mingkong_product_library_does_not_promote_pair_key_to_dxm_sku(monkeypatch):
    from appcore import mingkong_product_library as library

    variant_sql = {}
    procurement_calls = []

    monkeypatch.setattr(
        library,
        "list_library_candidates_for_product",
        lambda _product, limit=10: [{"id": 1, "product_code": "sample"}],
    )
    monkeypatch.setattr(library, "_candidate_shopify_ids", lambda _product: set())
    monkeypatch.setattr(library, "_selected_candidate_product_ids", lambda _candidates, _ids: [1])

    def fake_query(sql, _args):
        if "FROM mingkong_product_variants" in sql:
            variant_sql["sql"] = sql
            return [
                {
                    "id": 10,
                    "mingkong_product_id": 1,
                    "mk_shopify_product_id": "shopify-product",
                    "mk_shopify_variant_id": "variant-1",
                    "variant_title": "Blue",
                    "shopify_sku": "front-sku",
                    "pair_key": "front-sku",
                    "dxm_sku": "",
                    "dxm_sku_code": "",
                    "dxm_product_sku": "",
                    "dxm_name": "",
                    "mk_title": "Sample",
                    "mk_main_image_url": "",
                    "is_combo": 0,
                }
            ]
        if "FROM mingkong_combo_components" in sql:
            return []
        raise AssertionError(sql)

    def fake_procurement(skus):
        procurement_calls.append(list(skus))
        return {}

    monkeypatch.setattr(library, "query", fake_query)
    monkeypatch.setattr(library, "_procurement_for_skus", fake_procurement)

    rows = library.sku_rows_from_library({"product_code": "sample-rjc"})

    assert "proc.sku = NULLIF(v.dxm_sku, '')" in variant_sql["sql"]
    assert rows[0]["shopify_sku"] == "front-sku"
    assert rows[0]["dianxiaomi_sku"] == ""
    assert procurement_calls[0] == []


def test_mingkong_product_library_accepts_public_shopify_variant_payloads():
    from appcore import mingkong_product_library as library

    rows = library.variant_payloads_from_shopify_row({
        "shopifyProductId": "8591109816493",
        "variants": [
            {
                "shopify_variant_id": "46041796313261",
                "shopify_sku": "0422-14562851",
                "shopify_variant_title": 'Purple / Extended (27.6") / Family Pack (Set of 3)',
                "shopify_price": 12.95,
                "shopify_compare_at_price": 16.83,
                "shopify_inventory_quantity": 995879,
                "shopify_weight_grams": 90.0,
            }
        ],
    })

    assert rows == [
        {
            "mk_shopify_product_id": "8591109816493",
            "mk_shopify_variant_id": "46041796313261",
            "variant_title": 'Purple / Extended (27.6") / Family Pack (Set of 3)',
            "shopify_sku": "0422-14562851",
            "pair_key": "0422-14562851",
            "shopify_price": 12.95,
            "shopify_compare_at_price": 16.83,
            "shopify_inventory_quantity": 995879,
            "shopify_weight_grams": 90.0,
            "raw_json": {
                "shopify_variant_id": "46041796313261",
                "shopify_sku": "0422-14562851",
                "shopify_variant_title": 'Purple / Extended (27.6") / Family Pack (Set of 3)',
                "shopify_price": 12.95,
                "shopify_compare_at_price": 16.83,
                "shopify_inventory_quantity": 995879,
                "shopify_weight_grams": 90.0,
            },
        }
    ]


def test_mingkong_product_library_builds_full_base_from_product_link():
    from appcore import mingkong_product_library as library

    fetched_urls = []

    def fake_fetch(url):
        fetched_urls.append(url)
        return {
            "id": 8591109816493,
            "handle": "hygienic-silicone-back-scrub-rjc",
            "title": "Hygienic Silicone Back Scrub",
            "variants": [
                {
                    "id": 46041795559597,
                    "sku": "0422-14563244",
                    "price": 1295,
                    "compare_at_price": 1683,
                    "inventory_quantity": 995879,
                    "grams": 90,
                    "title": 'Blue / Standard (23.6") / 1 Pack (Single)',
                    "featured_image": {"src": "//cdn.shopify.com/blue.jpg"},
                }
            ],
        }

    rows = library.public_shopify_sku_rows_from_product(
        {
            "product_link": "https://0ixug9-pv.myshopify.com/products/hygienic-silicone-back-scrub-rjc",
            "shopifyid": "8591109816493",
        },
        fetch_json_fn=fake_fetch,
    )

    assert fetched_urls == [
        "https://0ixug9-pv.myshopify.com/products/hygienic-silicone-back-scrub-rjc.js"
    ]
    assert rows[0]["shopify_product_id"] == "8591109816493"
    assert rows[0]["shopify_variant_id"] == "46041795559597"
    assert rows[0]["shopify_sku"] == "0422-14563244"
    assert rows[0]["shopify_price"] == 12.95
    assert rows[0]["shopify_compare_at_price"] == 16.83
    assert rows[0]["shopify_weight_grams"] == 90.0
    assert rows[0]["image_url"] == "https://cdn.shopify.com/blue.jpg"
    assert rows[0]["dianxiaomi_sku"] == ""
