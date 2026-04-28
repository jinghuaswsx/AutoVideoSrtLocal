from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "shopifyid_dianxiaomi_sync.py"


def _load_module():
    assert MODULE_PATH.exists(), f"missing sync module: {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("shopifyid_dianxiaomi_sync", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_payload_uses_fixed_online_filters():
    mod = _load_module()

    payload = mod.build_payload(3)

    assert payload == {
        "sortName": 2,
        "pageNo": 3,
        "pageSize": 100,
        "total": 0,
        "sortValue": 0,
        "searchType": 1,
        "searchValue": "",
        "productSearchType": 0,
        "sellType": 0,
        "listingStatus": "Active",
        "shopId": "-1",
        "dxmState": "online",
        "dxmOfflineState": "",
        "fullCid": "",
    }


def test_auto_runtime_modes_use_windows_local_browser_and_ssh(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod.os, "name", "nt", raising=False)

    assert mod.resolve_browser_mode("auto") == "local-chrome"
    assert mod.resolve_db_mode("auto") == "ssh"


def test_auto_runtime_modes_use_server_browser_and_local_mysql_on_linux(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod.os, "name", "posix", raising=False)

    assert mod.resolve_browser_mode("auto") == "server-cdp"
    assert mod.resolve_db_mode("auto") == "local"


def test_extract_page_summary_reads_total_size_fields():
    mod = _load_module()

    payload = {
        "data": {
            "page": {
                "totalSize": 404,
                "totalPage": 5,
                "pageSize": 100,
                "pageNo": 1,
                "list": [],
            }
        }
    }

    assert mod.extract_page_summary(payload) == {
        "total_size": 404,
        "total_page": 5,
        "page_size": 100,
        "page_no": 1,
    }


def test_extract_products_returns_shopifyid_and_handle_rows():
    mod = _load_module()

    payload = {
        "data": {
            "page": {
                "list": [
                    {
                        "handle": "no-drip-honey-dispenser-rjc",
                        "shopifyProductId": "8560559554733",
                        "title": "No-Drip Honey Dispenser",
                        "shopId": "8477915",
                    }
                ]
            }
        }
    }

    assert mod.extract_products(payload) == [
        {
            "handle": "no-drip-honey-dispenser-rjc",
            "shopifyid": "8560559554733",
            "title": "No-Drip Honey Dispenser",
            "shop_id": "8477915",
        }
    ]


def test_ensure_dianxiaomi_success_accepts_zero_code():
    mod = _load_module()

    mod.ensure_dianxiaomi_success({"code": 0, "msg": "Successful"})


def test_build_remote_handle_map_reports_conflicts():
    mod = _load_module()

    remote_map, conflicts = mod.build_remote_handle_map(
        [
            {"handle": "demo-a", "shopifyid": "100"},
            {"handle": "demo-a", "shopifyid": "101"},
            {"handle": "demo-b", "shopifyid": "200"},
        ]
    )

    assert remote_map == {"demo-b": "200"}
    assert conflicts == [
        {
            "handle": "demo-a",
            "shopifyids": ["100", "101"],
            "status": "remote_conflict",
        }
    ]


def test_parse_remote_products_tsv_normalizes_blank_shopifyid():
    mod = _load_module()

    rows = mod.parse_remote_products_tsv("1\tdemo-a\t\n2\tdemo-b\t200\n")

    assert rows == [
        {"id": 1, "product_code": "demo-a", "shopifyid": None},
        {"id": 2, "product_code": "demo-b", "shopifyid": "200"},
    ]


def test_build_remote_batch_update_sql_wraps_updates_in_transaction():
    mod = _load_module()

    sql = mod.build_remote_batch_update_sql(
        [
            {"id": 1, "product_code": "demo-a", "shopifyid": "100"},
            {"id": 2, "product_code": "demo-b", "shopifyid": "200"},
        ]
    )

    assert sql == (
        "START TRANSACTION;\n"
        "UPDATE media_products SET shopifyid='100' WHERE id=1 AND deleted_at IS NULL AND (shopifyid IS NULL OR shopifyid='');\n"
        "UPDATE media_products SET shopifyid='200' WHERE id=2 AND deleted_at IS NULL AND (shopifyid IS NULL OR shopifyid='');\n"
        "COMMIT;\n"
    )


def test_plan_backfill_updates_distinguishes_update_unchanged_unmatched_and_conflict():
    mod = _load_module()

    remote_map = {
        "demo-a": "100",
        "demo-b": "200",
        "demo-c": "300",
    }
    local_products = [
        {"id": 1, "product_code": "demo-a", "shopifyid": None},
        {"id": 2, "product_code": "demo-b", "shopifyid": "200"},
        {"id": 3, "product_code": "demo-c", "shopifyid": "999"},
        {"id": 4, "product_code": "demo-d", "shopifyid": None},
    ]

    plan = mod.plan_backfill_updates(remote_map, local_products)

    assert plan["updates"] == [
        {"id": 1, "product_code": "demo-a", "shopifyid": "100"}
    ]
    assert plan["unchanged"] == [
        {"id": 2, "product_code": "demo-b", "shopifyid": "200", "status": "unchanged"}
    ]
    assert plan["conflicts"] == [
        {
            "id": 3,
            "product_code": "demo-c",
            "existing_shopifyid": "999",
            "incoming_shopifyid": "300",
            "status": "conflict",
        }
    ]
    assert plan["unmatched_remote"] == []
    assert plan["unmatched_local"] == [
        {"id": 4, "product_code": "demo-d", "status": "unmatched_local"}
    ]


def test_plan_backfill_updates_reports_remote_only_handles():
    mod = _load_module()

    plan = mod.plan_backfill_updates(
        {"demo-a": "100", "demo-only": "777"},
        [{"id": 1, "product_code": "demo-a", "shopifyid": None}],
    )

    assert plan["updates"] == [
        {"id": 1, "product_code": "demo-a", "shopifyid": "100"}
    ]
    assert plan["unmatched_remote"] == [
        {"product_code": "demo-only", "shopifyid": "777", "status": "unmatched_remote"}
    ]


def test_fetch_all_remote_products_aggregates_all_pages():
    mod = _load_module()
    calls = []

    def fake_fetch_page(page_no):
        calls.append(page_no)
        total_size = 404
        total_page = 5
        rows = [
            {
                "handle": f"demo-{page_no:02d}-{idx:02d}",
                "shopifyProductId": f"{page_no}{idx:03d}",
                "title": f"title-{page_no}-{idx}",
                "shopId": "8477915",
            }
            for idx in range(2)
        ]
        return {
            "data": {
                "page": {
                    "totalSize": total_size,
                    "totalPage": total_page,
                    "pageSize": 100,
                    "pageNo": page_no,
                    "list": rows,
                }
            }
        }

    summary, rows = mod.fetch_all_remote_products(fake_fetch_page)

    assert calls == [1, 2, 3, 4, 5]
    assert summary == {
        "total_size": 404,
        "total_page": 5,
        "page_size": 100,
        "page_no": 1,
    }
    assert len(rows) == 10
    assert rows[0]["handle"] == "demo-01-00"
    assert rows[-1]["handle"] == "demo-05-01"


def test_run_sync_applies_updates_and_writes_report(tmp_path):
    mod = _load_module()
    applied = []

    def fake_fetch_page(page_no):
        rows = {
            1: [
                {"handle": "demo-a", "shopifyProductId": "100", "title": "A", "shopId": "1"},
                {"handle": "demo-b", "shopifyProductId": "200", "title": "B", "shopId": "1"},
            ],
            2: [
                {"handle": "demo-c", "shopifyProductId": "300", "title": "C", "shopId": "1"},
                {"handle": "demo-only", "shopifyProductId": "777", "title": "Only", "shopId": "1"},
            ],
        }
        return {
            "data": {
                "page": {
                    "totalSize": 4,
                    "totalPage": 2,
                    "pageSize": 100,
                    "pageNo": page_no,
                    "list": rows[page_no],
                }
            }
        }

    def fake_apply_updates(items):
        applied.extend(items)

    report = mod.run_sync(
        fetch_page=fake_fetch_page,
        local_products=[
            {"id": 1, "product_code": "demo-a", "shopifyid": None},
            {"id": 2, "product_code": "demo-b", "shopifyid": "200"},
            {"id": 3, "product_code": "demo-c", "shopifyid": "999"},
        ],
        apply_updates=fake_apply_updates,
        output_dir=tmp_path,
        now_text="20260424-023000",
    )

    assert applied == [{"id": 1, "product_code": "demo-a", "shopifyid": "100"}]
    assert report["summary"] == {
        "total_size": 4,
        "total_page": 2,
        "fetched": 4,
        "matched": 3,
        "updated": 1,
        "unchanged": 1,
        "conflict": 1,
        "unmatched_local": 0,
        "unmatched_remote": 1,
        "remote_conflict": 0,
    }
    assert report["output_file"].endswith("shopifyid-dianxiaomi-sync-20260424-023000.json")
    payload = json.loads(Path(report["output_file"]).read_text(encoding="utf-8"))
    assert payload["summary"]["updated"] == 1
    assert payload["updates"] == [{"id": 1, "product_code": "demo-a", "shopifyid": "100"}]
    assert payload["conflicts"] == [
        {
            "id": 3,
            "product_code": "demo-c",
            "existing_shopifyid": "999",
            "incoming_shopifyid": "300",
            "status": "conflict",
        }
    ]
    assert payload["unmatched_remote"] == [
        {"product_code": "demo-only", "shopifyid": "777", "status": "unmatched_remote"}
    ]


def test_scheduled_task_table_sql_contains_run_tracking_fields():
    mod = _load_module()

    sql = mod.build_scheduled_task_runs_table_sql()

    assert "CREATE TABLE IF NOT EXISTS scheduled_task_runs" in sql
    assert "task_code VARCHAR(64)" in sql
    assert "status ENUM('running', 'success', 'failed')" in sql
    assert "summary_json JSON" in sql
    assert "idx_scheduled_task_runs_task_started" in sql


def test_sql_quote_escapes_single_quotes_and_null():
    mod = _load_module()

    assert mod._sql_quote(None) == "NULL"
    assert mod._sql_quote("SmartGearX's token") == "'SmartGearX''s token'"


def test_product_sync_success_allows_zero_failed_products():
    mod = _load_module()

    mod._assert_shopify_product_sync_success(
        "状态：已完成! 详情：店铺《Newjoyloo》同步完成，同步成功12个产品，同步失败0个"
    )


def test_product_sync_success_ignores_smartgearx_store_failure():
    mod = _load_module()

    mod._assert_shopify_product_sync_success(
        "状态：已完成! 详情：店铺《SmartGearX》同步失败，原因：店铺授权信息已过期！店铺《Newjoyloo》同步完成，同步成功14个产品，同步失败0个"
    )


def test_product_sync_success_rejects_unignored_store_level_failure():
    mod = _load_module()

    with pytest.raises(RuntimeError, match="Newjoyloo"):
        mod._assert_shopify_product_sync_success(
            "状态：已完成! 详情：店铺《Newjoyloo》同步失败，原因：店铺授权信息已过期！"
        )


def test_click_sync_products_button_retries_after_notice_overlay_cleanup():
    mod = _load_module()

    class EmptyLocator:
        def count(self):
            return 0

        def nth(self, index):
            raise AssertionError(f"unexpected nth({index})")

    class ButtonLocator:
        def __init__(self):
            self.clicks = 0

        def filter(self, *, has_text):
            assert has_text == "同步产品"
            return self

        def count(self):
            return 1

        def click(self, *, timeout):
            self.clicks += 1
            if self.clicks == 1:
                raise RuntimeError("notice iframe intercepts pointer events")

    class FakePage:
        def __init__(self):
            self.button = ButtonLocator()
            self.evaluated = False
            self.waits = []

        def locator(self, selector):
            if selector == "button":
                return self.button
            return EmptyLocator()

        def evaluate(self, script):
            assert "theNewestModalLabelFrame" in script
            self.evaluated = True
            return 1

        def wait_for_timeout(self, value):
            self.waits.append(value)

    page = FakePage()

    mod._click_sync_products_button(page)

    assert page.button.clicks == 2
    assert page.evaluated is True
    assert 300 in page.waits


def test_module_exposes_main_entrypoint_for_systemd_timer():
    mod = _load_module()

    assert callable(getattr(mod, "main", None))
