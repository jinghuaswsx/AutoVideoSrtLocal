from __future__ import annotations

from argparse import Namespace

from tools import mingkong_weekly_sync_orchestrator as mod


def test_classify_plan_splits_ready_base_and_empty_results():
    assert mod.classify_plan({"new_fillable_sku_count": 2, "local_sku_count": 5}) == "ready"
    assert mod.classify_plan({"new_fillable_sku_count": 0, "local_sku_count": 5}) == "base_only"
    assert mod.classify_plan({"new_fillable_sku_count": 0, "local_sku_count": 0}) == "no_pairs"
    assert mod.classify_plan({"status": "error", "local_sku_count": 5}) == "error"


def test_should_execute_plan_respects_phase_and_sku_limits():
    ready = {"new_fillable_sku_count": 3, "local_sku_count": 20}
    base = {"new_fillable_sku_count": 0, "local_sku_count": 20, "existing_empty_base_count": 0}
    existing_base = {"new_fillable_sku_count": 0, "local_sku_count": 20, "existing_empty_base_count": 4}

    assert mod.should_execute_plan(ready, phase="ready", max_sku_rows=80)
    assert not mod.should_execute_plan(ready, phase="ready", max_sku_rows=10)
    assert not mod.should_execute_plan(base, phase="ready", max_sku_rows=80)
    assert mod.should_execute_plan(base, phase="base", max_sku_rows=80)
    assert not mod.should_execute_plan(existing_base, phase="base", max_sku_rows=80)
    assert mod.should_execute_plan(existing_base, phase="base", max_sku_rows=80, base_refresh_existing=True)


def test_run_orchestrator_executes_only_selected_ready_products(monkeypatch, tmp_path):
    products = [
        {"id": 1, "product_code": "ready-rjc", "created_at": "2026-06-09 10:00:00"},
        {"id": 2, "product_code": "base-rjc", "created_at": "2026-06-09 10:00:00"},
        {"id": 3, "product_code": "large-rjc", "created_at": "2026-06-09 10:00:00"},
    ]
    monkeypatch.setattr(mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(mod.backfill, "find_unprocessed_products", lambda **_kwargs: products)
    monkeypatch.setattr(mod, "_sleep_seconds", lambda _seconds: None)

    calls = []

    def fake_run_product_sync(product, *, execute, **_kwargs):
        calls.append((product["id"], execute))
        if product["id"] == 1:
            return {
                "product_id": 1,
                "product_code": "ready-rjc",
                "status": "dry_run" if not execute else "ok",
                "local_sku_count": 5,
                "new_fillable_sku_count": 2,
            }
        if product["id"] == 2:
            return {
                "product_id": 2,
                "product_code": "base-rjc",
                "status": "dry_run",
                "local_sku_count": 6,
                "new_fillable_sku_count": 0,
            }
        return {
            "product_id": 3,
            "product_code": "large-rjc",
            "status": "dry_run",
            "local_sku_count": 120,
            "new_fillable_sku_count": 4,
        }

    monkeypatch.setattr(mod.backfill, "run_product_sync", fake_run_product_sync)
    args = Namespace(
        phase="ready",
        execute=True,
        scan_limit=80,
        max_products=25,
        max_sku_rows=80,
        created_within_days=0,
        include_archived=False,
        include_unlisted=False,
        force_refresh_mingkong=False,
        overwrite_existing_pairing=False,
        protect_configured_local_skus=True,
        base_refresh_existing=False,
        product_delay_seconds=0,
        plan_delay_seconds=0,
    )

    report = mod.run_orchestrator(args)

    assert report["summary"]["candidate_count"] == 3
    assert report["summary"]["selected_count"] == 1
    assert report["summary"]["executed_count"] == 1
    assert calls == [(1, False), (1, True), (2, False), (3, False)]
