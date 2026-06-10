from __future__ import annotations

import json
from datetime import datetime

from tools import mingkong_unprocessed_sku_backfill as mod


def test_list_recent_products_for_full_sync_uses_created_at_cutoff_without_configured_exclusion():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return [{"id": 11, "product_code": "P-11", "created_at": "2026-05-28 10:00:00"}]

    rows = mod.list_recent_products_for_full_sync(
        days=15,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 11, 12, 0, 0),
    )

    assert rows[0]["id"] == 11
    assert "mp.created_at >= %s" in captured["sql"]
    assert "NOT EXISTS" not in captured["sql"]
    assert captured["params"][0] == "2026-05-27 12:00:00"


def test_list_recent_products_for_full_sync_can_include_archived_and_unlisted_products():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    mod.list_recent_products_for_full_sync(
        days=15,
        include_archived=True,
        listed_only=False,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 11, 12, 0, 0),
    )

    assert "COALESCE(mp.archived, 0)=0" not in captured["sql"]
    assert "mp.listing_status" not in captured["sql"]


def test_run_recent_15d_full_sync_batch_calls_complete_runner_with_protection(monkeypatch):
    products = [{"id": 1, "product_code": "P1"}, {"id": 2, "product_code": "P2"}]
    calls = []
    monkeypatch.setattr(mod, "list_recent_products_for_full_sync", lambda **kwargs: products)

    def fake_run_product_sync(product, **kwargs):
        calls.append((product, kwargs))
        return {
            "product_id": product["id"],
            "product_code": product["product_code"],
            "status": "ok",
            "new_fillable_sku_count": 1,
            "protected_local_sku_count": 1,
            "replicate": {"summary": {"created_count": 1, "existing_count": 0}},
            "confirm": {"summary": {"confirmed_count": 1}},
            "yuncang": {
                "summary": {"added_count": 1, "existing_count": 0},
                "purchase_price_status": "updated",
            },
            "skus": [{"logistics_packaging": {"status": "updated"}}],
        }

    monkeypatch.setattr(mod, "run_product_sync", fake_run_product_sync)

    report = mod.run_recent_15d_full_sync_batch(days=15, execute=True)

    assert report["mode"] == "execute"
    assert report["summary"]["candidate_product_count"] == 2
    assert report["summary"]["completed_product_count"] == 2
    assert report["summary"]["synced_sku_count"] == 2
    assert report["summary"]["protected_sku_count"] == 2
    assert report["summary"]["dxm03_replicated_sku_count"] == 2
    assert report["summary"]["yuncang_added_sku_count"] == 2
    assert report["summary"]["purchase_price_updated_product_count"] == 2
    assert report["summary"]["logistics_packaging_updated_sku_count"] == 2
    assert all(kwargs["protect_configured_local_skus"] is True for _product, kwargs in calls)


def test_write_recent_full_sync_report_writes_json_file(tmp_path):
    report = {"mode": "plan", "summary": {"candidate_product_count": 1}, "products": []}

    path = mod.write_recent_full_sync_report(report, output_dir=tmp_path)

    assert path.exists()
    assert path.name.startswith("mingkong-recent-15d-full-sync-plan-")
    assert json.loads(path.read_text(encoding="utf-8"))["summary"]["candidate_product_count"] == 1


def test_full_sync_cli_runs_plan_mode_and_prints_report_path(monkeypatch, tmp_path, capsys):
    from tools import mingkong_recent_15d_full_sync as cli

    monkeypatch.setattr(
        cli.backfill,
        "run_recent_15d_full_sync_batch",
        lambda **kwargs: {"mode": "plan", "summary": {"candidate_product_count": 1}, "products": []},
    )
    monkeypatch.setattr(cli.backfill, "write_recent_full_sync_report", lambda report: tmp_path / "report.json")

    rc = cli.main([])

    out = capsys.readouterr().out
    assert rc == 0
    assert "report.json" in out
    assert '"candidate_product_count": 1' in out


def test_old_local_pairing_cli_is_deprecated(capsys):
    from tools import mingkong_local_pairing_15d as old_cli

    rc = old_cli.main([])

    out = capsys.readouterr().out
    assert rc == 2
    assert "mingkong_recent_15d_full_sync.py" in out
