from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "dianxiaomi_order_import.py"


def _load_module():
    assert MODULE_PATH.exists(), f"missing import module: {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("dianxiaomi_order_import", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iter_dates_includes_end_date():
    mod = _load_module()

    assert list(mod.iter_dates(date(2026, 1, 1), date(2026, 1, 3))) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
    ]


def test_build_order_payload_uses_pay_time_range_and_state():
    mod = _load_module()

    payload = mod.build_order_payload(date(2026, 4, 27), page_no=2, state="paid")

    assert payload["pageNo"] == 2
    assert payload["pageSize"] == 100
    assert payload["state"] == "paid"
    assert payload["startTime"] == "2026-04-27 00:00:00"
    assert payload["endTime"] == "2026-04-27 23:59:59"
    assert payload["orderField"] == "order_pay_time"


def test_extract_order_page_reads_list_and_total_page():
    mod = _load_module()
    payload = {
        "code": 0,
        "data": {
            "page": {
                "totalPage": 3,
                "pageNo": 1,
                "list": [{"id": "9001"}],
            }
        },
    }

    page = mod.extract_order_page(payload)

    assert page.total_page == 3
    assert page.page_no == 1
    assert page.orders == [{"id": "9001"}]


def test_run_import_dry_run_uses_fetchers_and_does_not_write(monkeypatch):
    mod = _load_module()
    written = []
    scope = mod.oa.DianxiaomiProductScope(
        by_shopify_id={"111": {"product_id": 1, "product_code": "demo", "site_code": "newjoy", "shopifyid": "111"}},
        by_handle={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy"},
    )
    monkeypatch.setattr(mod.oa, "build_dianxiaomi_product_scope", lambda sites: scope)
    monkeypatch.setattr(mod.oa, "normalize_dianxiaomi_order", lambda order, scope, profits: ([{
        "site_code": "newjoy",
        "dxm_package_id": "9001",
        "shopify_product_id": "111",
        "raw_order_json": order,
        "raw_line_json": {"productId": "111"},
    }], 0))
    monkeypatch.setattr(mod.oa, "upsert_dianxiaomi_order_lines", lambda batch_id, rows: written.append(rows) or {"affected": 1, "rows": len(rows)})
    monkeypatch.setattr(mod.oa, "start_dianxiaomi_order_import_batch", lambda *args: 42)
    monkeypatch.setattr(mod.oa, "finish_dianxiaomi_order_import_batch", lambda *args, **kwargs: None)

    report = mod.run_import(
        start_date=date(2026, 4, 27),
        end_date=date(2026, 4, 27),
        site_codes=["newjoy"],
        states=["paid"],
        fetch_orders=lambda day, page_no, state: {"code": 0, "data": {"page": {"totalPage": 1, "pageNo": 1, "list": [{"id": "9001"}]}}},
        fetch_profits=lambda package_ids: {"9001": {"profit": "1.00"}},
        dry_run=True,
    )

    assert report["summary"]["fetched_orders"] == 1
    assert report["summary"]["fetched_lines"] == 1
    assert report["summary"]["inserted_lines"] == 0
    assert written == []


def test_run_import_scans_shipped_state_per_day(monkeypatch):
    mod = _load_module()
    calls = []
    scope = mod.oa.DianxiaomiProductScope(
        by_shopify_id={"111": {"product_id": 1, "product_code": "demo", "site_code": "newjoy", "shopifyid": "111"}},
        by_handle={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy"},
    )
    monkeypatch.setattr(mod.oa, "build_dianxiaomi_product_scope", lambda sites: scope)
    monkeypatch.setattr(mod.oa, "normalize_dianxiaomi_order", lambda order, scope, profits: ([{
        "site_code": "newjoy",
        "dxm_package_id": order["id"],
        "shopify_product_id": "111",
        "raw_order_json": order,
        "raw_line_json": {"productId": "111"},
    }], 0))

    def fetch_orders(day, page_no, state):
        calls.append((day, page_no, state))
        return {
            "code": 0,
            "data": {
                "page": {
                    "totalPage": 1,
                    "pageNo": page_no,
                    "list": [{
                        "id": "9001",
                        "shippedTimeStr": "2026-04-27 14:47",
                        "productList": [{"productId": "111"}],
                    }],
                }
            },
        }

    report = mod.run_import(
        start_date=date(2026, 4, 27),
        end_date=date(2026, 4, 28),
        site_codes=["newjoy"],
        states=["shipped"],
        fetch_orders=fetch_orders,
        fetch_profits=lambda package_ids: {},
        dry_run=True,
    )

    assert calls == [(date(2026, 4, 27), 1, "shipped"), (date(2026, 4, 28), 1, "shipped")]
    assert report["summary"]["fetched_orders"] == 2


def test_order_in_date_range_rejects_missing_reference_date_for_range_scan():
    mod = _load_module()

    assert not mod._order_in_date_range({}, "shipped", date(2026, 4, 27), date(2026, 4, 28))
