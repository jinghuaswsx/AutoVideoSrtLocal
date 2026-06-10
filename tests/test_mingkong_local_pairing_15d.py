from __future__ import annotations

import json
from datetime import datetime

from tools import mingkong_unprocessed_sku_backfill as mod


def test_list_recent_products_for_local_pairing_uses_created_at_cutoff_without_configured_exclusion():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "id": 11,
                "product_code": "P-11",
                "product_name": "Product 11",
                "product_link": "https://admin.shopify.com/store/x/products/11",
                "shopifyid": "11",
                "created_at": "2026-05-28 10:00:00",
            }
        ]

    rows = mod.list_recent_products_for_local_pairing(
        days=15,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 10, 12, 0, 0),
    )

    assert rows[0]["id"] == 11
    assert "mp.created_at >= %s" in captured["sql"]
    assert "NOT EXISTS" not in captured["sql"]
    assert captured["params"][0] == "2026-05-26 12:00:00"


def test_list_recent_products_for_local_pairing_can_include_archived_and_unlisted_products():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    mod.list_recent_products_for_local_pairing(
        days=15,
        include_archived=True,
        listed_only=False,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 10, 12, 0, 0),
    )

    assert "COALESCE(mp.archived, 0) = 0" not in captured["sql"]
    assert "mp.listing_status" not in captured["sql"]


def test_upsert_local_pairing_pairs_skips_configured_variants_and_does_not_delete_stale_rows():
    executed = []

    def fake_execute(sql, params=()):
        executed.append((sql, params))
        return 1

    pairs = [
        {
            "shopify_product_id": "shopify-1",
            "shopify_variant_id": "variant-protected",
            "shopify_sku": "BASE-A",
            "dianxiaomi_sku": "MK-A",
            "dianxiaomi_product_sku": "MK-P-A",
            "dianxiaomi_sku_code": "MK-C-A",
            "dianxiaomi_name": "Configured",
        },
        {
            "shopify_product_id": "shopify-1",
            "shopify_variant_id": "variant-open",
            "shopify_sku": "BASE-B",
            "dianxiaomi_sku": "MK-B",
            "dianxiaomi_product_sku": "MK-P-B",
            "dianxiaomi_sku_code": "MK-C-B",
            "dianxiaomi_name": "Open",
        },
        {
            "shopify_product_id": "shopify-1",
            "shopify_variant_id": "variant-new",
            "shopify_sku": "BASE-C",
            "dianxiaomi_sku": "",
            "dianxiaomi_product_sku": "",
            "dianxiaomi_sku_code": "",
            "dianxiaomi_name": "",
        },
    ]
    existing_rows = [
        {
            "id": 101,
            "shopify_variant_id": "variant-protected",
            "source": "manual",
            "dianxiaomi_sku": "KEEP",
        },
        {
            "id": 102,
            "shopify_variant_id": "variant-open",
            "source": "shopify_base",
            "dianxiaomi_sku": "",
        },
        {
            "id": 103,
            "shopify_variant_id": "variant-stale",
            "source": "shopify_base",
            "dianxiaomi_sku": "",
        },
    ]

    result = mod.upsert_local_pairing_pairs(
        product_id=1,
        pairs=pairs,
        existing_rows=existing_rows,
        protected_variant_ids={"variant-protected"},
        execute_fn=fake_execute,
    )

    assert result == {"updated": 1, "inserted": 1, "skipped_protected": 1}
    flattened = "\n".join(sql for sql, _params in executed)
    assert "DELETE FROM media_product_skus" not in flattened
    assert all("variant-protected" not in str(params) for _sql, params in executed)


def test_run_product_local_pairing_preserves_configured_rows_and_reports_partial(monkeypatch):
    product = {"id": 7, "product_code": "P7", "product_name": "P7", "product_link": "https://shopify/p/7"}
    existing_rows = [
        {"id": 1, "shopify_variant_id": "v1", "source": "manual", "dianxiaomi_sku": "KEEP"},
        {"id": 2, "shopify_variant_id": "v2", "source": "shopify_base", "dianxiaomi_sku": ""},
    ]

    monkeypatch.setattr(mod.medias, "list_product_skus", lambda product_id: existing_rows)
    monkeypatch.setattr(
        mod.pairing,
        "build_workbench_payload",
        lambda _product, _existing_rows, **kwargs: {
            "items": [
                {"shopify_variant_id": "v1", "shopify_sku": "BASE-1"},
                {"shopify_variant_id": "v2", "shopify_sku": "BASE-2"},
                {"shopify_variant_id": "v3", "shopify_sku": "BASE-3"},
            ],
            "mingkong_procurement": {},
            "existing_sku_ids": {},
        },
    )
    monkeypatch.setattr(
        mod,
        "build_default_targets",
        lambda _payload: [
            {"shopify_variant_id": "v1", "dianxiaomi_sku": "MK-1"},
            {"shopify_variant_id": "v2", "dianxiaomi_sku": "MK-2"},
            {"shopify_variant_id": "v3", "dianxiaomi_sku": ""},
        ],
    )
    monkeypatch.setattr(
        mod.pairing,
        "build_target_sku_import_pairs",
        lambda _product, _items, _targets: [
            {"shopify_variant_id": "v1", "shopify_sku": "BASE-1", "dianxiaomi_sku": "MK-1"},
            {"shopify_variant_id": "v2", "shopify_sku": "BASE-2", "dianxiaomi_sku": "MK-2"},
            {"shopify_variant_id": "v3", "shopify_sku": "BASE-3", "dianxiaomi_sku": ""},
        ],
    )

    result = mod.run_product_local_pairing(product, execute=False)

    assert result["status"] == "partial"
    assert result["summary"]["synced_sku_count"] == 1
    assert result["summary"]["preserved_sku_count"] == 1
    assert result["summary"]["blank_base_sku_count"] == 1
    assert {row["action"] for row in result["sku_details"]} == {
        "preserved_existing_local_config",
        "synced_from_mingkong",
        "blank_base_no_mingkong_data",
    }


def test_run_product_local_pairing_execute_does_not_call_dxm03_or_yuncang(monkeypatch):
    calls = []
    product = {"id": 8, "product_code": "P8", "product_name": "P8", "product_link": "https://shopify/p/8"}

    monkeypatch.setattr(mod.medias, "list_product_skus", lambda product_id: [])
    monkeypatch.setattr(
        mod.pairing,
        "build_workbench_payload",
        lambda _product, _existing_rows, **kwargs: {
            "items": [],
            "mingkong_procurement": {},
            "existing_sku_ids": {},
        },
    )
    monkeypatch.setattr(
        mod,
        "build_default_targets",
        lambda _payload: [{"shopify_variant_id": "v1", "dianxiaomi_sku": "MK-1"}],
    )
    monkeypatch.setattr(
        mod.pairing,
        "build_target_sku_import_pairs",
        lambda _product, _items, _targets: [
            {"shopify_variant_id": "v1", "shopify_sku": "BASE-1", "dianxiaomi_sku": "MK-1"}
        ],
    )
    monkeypatch.setattr(
        mod,
        "upsert_local_pairing_pairs",
        lambda **kwargs: calls.append(kwargs) or {"updated": 0, "inserted": 1, "skipped_protected": 0},
    )
    monkeypatch.setattr(
        mod.pairing,
        "replicate_mingkong_skus_to_dxm03",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DXM03 replicate called")),
    )
    monkeypatch.setattr(
        mod.pairing,
        "confirm_dxm03_pairing",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DXM03 confirm called")),
    )
    monkeypatch.setattr(
        mod.dianxiaomi_yuncang,
        "add_product_skus_to_yuncang",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yuncang called")),
    )

    result = mod.run_product_local_pairing(product, execute=True)

    assert result["status"] == "completed"
    assert result["write_result"] == {"updated": 0, "inserted": 1, "skipped_protected": 0}
    assert len(calls) == 1


def test_run_local_pairing_batch_aggregates_product_and_sku_counts(monkeypatch):
    products = [{"id": 1}, {"id": 2}, {"id": 3}]
    monkeypatch.setattr(mod, "list_recent_products_for_local_pairing", lambda **kwargs: products)
    results = [
        {"status": "completed", "summary": {"synced_sku_count": 2, "preserved_sku_count": 0, "blank_base_sku_count": 0}},
        {"status": "partial", "summary": {"synced_sku_count": 1, "preserved_sku_count": 1, "blank_base_sku_count": 1}},
        {"status": "suspended", "summary": {"synced_sku_count": 0, "preserved_sku_count": 0, "blank_base_sku_count": 2}},
    ]
    monkeypatch.setattr(mod, "run_product_local_pairing", lambda product, **kwargs: results.pop(0))

    report = mod.run_local_pairing_batch(days=15, execute=False)

    assert report["summary"]["candidate_product_count"] == 3
    assert report["summary"]["completed_product_count"] == 1
    assert report["summary"]["partial_product_count"] == 1
    assert report["summary"]["suspended_product_count"] == 1
    assert report["summary"]["synced_sku_count"] == 3
    assert report["summary"]["blank_base_sku_count"] == 3


def test_write_local_pairing_report_writes_json_file(tmp_path):
    report = {
        "mode": "plan",
        "summary": {"synced_sku_count": 1},
        "products": [],
    }

    path = mod.write_local_pairing_report(report, output_dir=tmp_path)

    assert path.exists()
    assert path.name.startswith("mingkong-local-pairing-15d-plan-")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["summary"]["synced_sku_count"] == 1


def test_cli_main_runs_plan_mode_and_prints_report_path(monkeypatch, tmp_path, capsys):
    from tools import mingkong_local_pairing_15d as cli

    monkeypatch.setattr(
        cli.backfill,
        "run_local_pairing_batch",
        lambda **kwargs: {"mode": "plan", "summary": {"synced_sku_count": 1}, "products": []},
    )
    monkeypatch.setattr(cli.backfill, "write_local_pairing_report", lambda report: tmp_path / "report.json")

    rc = cli.main([])

    out = capsys.readouterr().out
    assert rc == 0
    assert "report.json" in out
    assert '"synced_sku_count": 1' in out
