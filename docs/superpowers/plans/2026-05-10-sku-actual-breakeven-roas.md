# SKU Actual Breakeven ROAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily SKU-level actual breakeven ROAS snapshot and show it after the existing estimated ROAS in the material SKU detail table.

**Architecture:** Keep SKU master data in `xmyc_storage_skus` and store time-window metrics in a new `sku_actual_breakeven_roas_snapshots` table. A focused `appcore/sku_actual_roas.py` module owns window calculation, order aggregation, Payment-fee fallback, snapshot upsert, and latest snapshot reads. Material product listing serialization batch-loads latest SKU snapshots and the frontend renders one new column with source labels.

**Tech Stack:** Python 3.12, Flask service helpers, PyMySQL-style DB facade, MySQL SQL migration, vanilla JavaScript, pytest, systemd timer/service.

---

## Files

- Create: `db/migrations/2026_05_10_sku_actual_breakeven_roas_snapshots.sql` — snapshot table.
- Create: `appcore/sku_actual_roas.py` — daily window and SKU actual ROAS aggregation.
- Create: `tools/sku_actual_roas_snapshot.py` — CLI runner with `scheduled_task_runs` logging.
- Create: `deploy/server_browser/autovideosrt-sku-actual-roas.service` — systemd oneshot service.
- Create: `deploy/server_browser/autovideosrt-sku-actual-roas.timer` — daily 00:00 timer.
- Create: `tests/test_sku_actual_roas.py` — pure aggregation, repository, CLI-support tests.
- Modify: `web/services/media_products_listing.py` — batch-load latest actual ROAS by SKU.
- Modify: `web/routes/medias/_serializers.py` — include `actual_breakeven_roas` per product SKU.
- Modify: `web/static/medias.js` — add table column and renderer.
- Modify: `tests/test_media_products_listing_service.py` — assert batch snapshot wiring.
- Modify: `tests/test_material_roas_frontend.py` — assert table header and source labels.
- Modify: `appcore/scheduled_tasks.py` — register new timer in Web scheduled task module.
- Modify: `tests/test_appcore_scheduled_tasks.py` — assert registration.
- Modify: `AGENTS.md` — add the new doc anchor, runner, and focused verification command.

### Task 1: Migration

**Files:**
- Create: `db/migrations/2026_05_10_sku_actual_breakeven_roas_snapshots.sql`
- Test: `tests/test_sku_actual_roas.py`

- [ ] **Step 1: Write the failing migration test**

Add this test to `tests/test_sku_actual_roas.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_migration_declares_expected_table_and_indexes():
    sql = (ROOT / "db" / "migrations" / "2026_05_10_sku_actual_breakeven_roas_snapshots.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS sku_actual_breakeven_roas_snapshots" in sql
    assert "actual_breakeven_roas DECIMAL(12,4) NULL" in sql
    assert "fee_source ENUM('real','estimated_7pct','mixed')" in sql
    assert "UNIQUE KEY uk_sku_actual_roas_window (sku, window_start, window_end)" in sql
    assert "KEY idx_sku_actual_roas_latest (sku, computed_at)" in sql
    assert "docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md" in sql
```

- [ ] **Step 2: Run the migration test red**

Run: `pytest tests/test_sku_actual_roas.py::test_snapshot_migration_declares_expected_table_and_indexes -q`

Expected: FAIL because the migration file does not exist.

- [ ] **Step 3: Add the migration**

Create `db/migrations/2026_05_10_sku_actual_breakeven_roas_snapshots.sql`:

```sql
-- 2026-05-10: SKU actual breakeven ROAS daily snapshots
-- Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md

CREATE TABLE IF NOT EXISTS sku_actual_breakeven_roas_snapshots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(128) NOT NULL,
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  orders_count INT NOT NULL DEFAULT 0,
  units INT NOT NULL DEFAULT 0,
  revenue_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  purchase_cost_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  shipping_cost_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  shopify_fee_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  fee_source ENUM('real','estimated_7pct','mixed') NOT NULL DEFAULT 'estimated_7pct',
  actual_breakeven_roas DECIMAL(12,4) NULL,
  computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_run_id BIGINT NULL,
  summary_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_sku_actual_roas_window (sku, window_start, window_end),
  KEY idx_sku_actual_roas_latest (sku, computed_at),
  KEY idx_sku_actual_roas_window (window_start, window_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU rolling-window actual breakeven ROAS snapshots';
```

- [ ] **Step 4: Run the migration test green**

Run: `pytest tests/test_sku_actual_roas.py::test_snapshot_migration_declares_expected_table_and_indexes -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/2026_05_10_sku_actual_breakeven_roas_snapshots.sql tests/test_sku_actual_roas.py
git commit -m "feat(sku): add actual roas snapshot migration" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#数据模型"
```

### Task 2: Aggregation Core

**Files:**
- Create: `appcore/sku_actual_roas.py`
- Test: `tests/test_sku_actual_roas.py`

- [ ] **Step 1: Write failing tests for window and fee source behavior**

Append to `tests/test_sku_actual_roas.py`:

```python
from datetime import date

import pytest


def test_calculate_window_uses_rolling_30_stable_days():
    from appcore import sku_actual_roas

    assert sku_actual_roas.calculate_window(date(2026, 5, 10)) == (
        date(2026, 4, 9),
        date(2026, 5, 8),
    )


def test_aggregate_rows_prefers_real_payment_fee_and_marks_mixed():
    from appcore import sku_actual_roas

    rows = [
        {
            "dxm_package_id": "pkg-1",
            "extended_order_id": "#1001",
            "product_display_sku": "SKU-A",
            "quantity": 1,
            "line_amount": 20,
            "ship_amount": 4,
            "logistic_fee": 6,
            "purchase_price_cny": 35,
            "xmyc_unit_price": None,
            "product_purchase_price": None,
        },
        {
            "dxm_package_id": "pkg-2",
            "extended_order_id": "#1002",
            "product_display_sku": "SKU-A",
            "quantity": 2,
            "line_amount": 30,
            "ship_amount": 0,
            "logistic_fee": 8,
            "purchase_price_cny": 40,
            "xmyc_unit_price": None,
            "product_purchase_price": None,
        },
    ]

    snapshots = sku_actual_roas.aggregate_sku_rows(rows, {"#1001": 2.4}, rmb_per_usd=7)
    row = snapshots["SKU-A"]

    assert row["orders_count"] == 2
    assert row["units"] == 3
    assert row["revenue_usd"] == pytest.approx(54.0)
    assert row["shopify_fee_usd"] == pytest.approx(2.4 + 30 * 0.07)
    assert row["fee_source"] == "mixed"
    assert row["actual_breakeven_roas"] is not None


def test_aggregate_rows_uses_7pct_when_payment_missing_and_nulls_unprofitable_roas():
    from appcore import sku_actual_roas

    rows = [
        {
            "dxm_package_id": "pkg-1",
            "extended_order_id": "#1001",
            "product_display_sku": "SKU-B",
            "quantity": 1,
            "line_amount": 10,
            "ship_amount": 0,
            "logistic_fee": 200,
            "purchase_price_cny": 100,
            "xmyc_unit_price": None,
            "product_purchase_price": None,
        },
    ]

    row = sku_actual_roas.aggregate_sku_rows(rows, {}, rmb_per_usd=7)["SKU-B"]

    assert row["fee_source"] == "estimated_7pct"
    assert row["shopify_fee_usd"] == pytest.approx(0.7)
    assert row["actual_breakeven_roas"] is None
```

- [ ] **Step 2: Run the core tests red**

Run: `pytest tests/test_sku_actual_roas.py::test_calculate_window_uses_rolling_30_stable_days tests/test_sku_actual_roas.py::test_aggregate_rows_prefers_real_payment_fee_and_marks_mixed tests/test_sku_actual_roas.py::test_aggregate_rows_uses_7pct_when_payment_missing_and_nulls_unprofitable_roas -q`

Expected: FAIL because `appcore.sku_actual_roas` does not exist.

- [ ] **Step 3: Implement minimal core functions**

Create `appcore/sku_actual_roas.py` with:

```python
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from appcore.order_analytics.cost_allocation import allocate_shipping_to_line
from appcore.product_roas import get_configured_rmb_per_usd

ESTIMATED_FEE_RATE = Decimal("0.07")


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def calculate_window(
    run_date: date,
    *,
    window_days: int = 30,
    settlement_delay_days: int = 2,
) -> tuple[date, date]:
    window_end = run_date - timedelta(days=int(settlement_delay_days))
    window_start = window_end - timedelta(days=int(window_days) - 1)
    return window_start, window_end


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _positive_decimal(*values: Any) -> Decimal:
    for value in values:
        candidate = _decimal(value)
        if candidate > 0:
            return candidate
    return Decimal("0")


def _q4(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _fee_source(real_count: int, estimated_count: int) -> str:
    if real_count and estimated_count:
        return "mixed"
    if real_count:
        return "real"
    return "estimated_7pct"


def aggregate_sku_rows(
    rows: list[dict[str, Any]],
    real_fees_by_order: dict[str, Any],
    *,
    rmb_per_usd: Any | None = None,
) -> dict[str, dict[str, Any]]:
    rate = _decimal(rmb_per_usd if rmb_per_usd is not None else get_configured_rmb_per_usd())
    order_line_totals: dict[str, Decimal] = defaultdict(Decimal)
    order_shipping: dict[str, Decimal] = {}
    order_revenue: dict[str, Decimal] = defaultdict(Decimal)

    for row in rows:
        package_id = str(row.get("dxm_package_id") or "")
        line_amount = _decimal(row.get("line_amount"))
        order_line_totals[package_id] += line_amount
        order_shipping.setdefault(package_id, _decimal(row.get("ship_amount")))

    for package_id, line_total in order_line_totals.items():
        order_revenue[package_id] = line_total + order_shipping.get(package_id, Decimal("0"))

    buckets: dict[str, dict[str, Any]] = {}
    order_sets: dict[str, set[str]] = defaultdict(set)
    fee_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"real": 0, "estimated": 0})

    for row in rows:
        sku = str(row.get("product_display_sku") or "").strip()
        if not sku:
            continue
        package_id = str(row.get("dxm_package_id") or "")
        order_name = str(row.get("extended_order_id") or "").strip()
        line_amount = _decimal(row.get("line_amount"))
        quantity = int(row.get("quantity") or 0)
        shipping_alloc = Decimal(str(allocate_shipping_to_line(
            line_amount=float(line_amount),
            order_total_line_amount=float(order_line_totals.get(package_id, Decimal("0"))),
            order_shipping_usd=float(order_shipping.get(package_id, Decimal("0"))),
        )))
        revenue = line_amount + shipping_alloc
        purchase_cny = _positive_decimal(
            row.get("purchase_price_cny"),
            row.get("xmyc_unit_price"),
            row.get("product_purchase_price"),
        )
        purchase_usd = (purchase_cny * Decimal(quantity)) / rate if rate > 0 else Decimal("0")
        logistic_fee = _decimal(row.get("logistic_fee"))
        shipping_cost_cny = Decimal("0")
        order_total = order_line_totals.get(package_id, Decimal("0"))
        if logistic_fee > 0 and order_total > 0:
            shipping_cost_cny = logistic_fee * (line_amount / order_total)
        shipping_usd = shipping_cost_cny / rate if rate > 0 else Decimal("0")

        if order_name in real_fees_by_order and order_revenue.get(package_id, Decimal("0")) > 0:
            fee = _decimal(real_fees_by_order[order_name]) * (revenue / order_revenue[package_id])
            fee_counts[sku]["real"] += 1
        else:
            fee = revenue * ESTIMATED_FEE_RATE
            fee_counts[sku]["estimated"] += 1

        bucket = buckets.setdefault(sku, {
            "sku": sku,
            "orders_count": 0,
            "units": 0,
            "revenue_usd": Decimal("0"),
            "purchase_cost_usd": Decimal("0"),
            "shipping_cost_usd": Decimal("0"),
            "shopify_fee_usd": Decimal("0"),
        })
        order_sets[sku].add(package_id)
        bucket["units"] += quantity
        bucket["revenue_usd"] += revenue
        bucket["purchase_cost_usd"] += purchase_usd
        bucket["shipping_cost_usd"] += shipping_usd
        bucket["shopify_fee_usd"] += fee

    out: dict[str, dict[str, Any]] = {}
    for sku, bucket in buckets.items():
        revenue = bucket["revenue_usd"]
        costs = bucket["purchase_cost_usd"] + bucket["shipping_cost_usd"] + bucket["shopify_fee_usd"]
        available = revenue - costs
        roas = revenue / available if available > 0 else None
        counts = fee_counts[sku]
        out[sku] = {
            "sku": sku,
            "orders_count": len(order_sets[sku]),
            "units": int(bucket["units"]),
            "revenue_usd": _q4(revenue),
            "purchase_cost_usd": _q4(bucket["purchase_cost_usd"]),
            "shipping_cost_usd": _q4(bucket["shipping_cost_usd"]),
            "shopify_fee_usd": _q4(bucket["shopify_fee_usd"]),
            "fee_source": _fee_source(counts["real"], counts["estimated"]),
            "actual_breakeven_roas": _q4(roas) if roas is not None else None,
            "summary": {
                "real_fee_lines": counts["real"],
                "estimated_fee_lines": counts["estimated"],
            },
        }
    return out
```

- [ ] **Step 4: Run the core tests green**

Run: `pytest tests/test_sku_actual_roas.py::test_calculate_window_uses_rolling_30_stable_days tests/test_sku_actual_roas.py::test_aggregate_rows_prefers_real_payment_fee_and_marks_mixed tests/test_sku_actual_roas.py::test_aggregate_rows_uses_7pct_when_payment_missing_and_nulls_unprofitable_roas -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/sku_actual_roas.py tests/test_sku_actual_roas.py
git commit -m "feat(sku): calculate actual breakeven roas" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#核心口径"
```

### Task 3: Snapshot Persistence

**Files:**
- Modify: `appcore/sku_actual_roas.py`
- Test: `tests/test_sku_actual_roas.py`

- [ ] **Step 1: Write failing tests for DB loading, upsert, and latest read**

Append to `tests/test_sku_actual_roas.py`:

```python
from datetime import datetime


def test_compute_loads_orders_payments_and_upserts_snapshots(monkeypatch):
    from appcore import sku_actual_roas

    calls = {"execute": []}

    def fake_query(sql, params=()):
        if "FROM dianxiaomi_order_lines" in sql:
            assert params == (date(2026, 4, 9), date(2026, 5, 8))
            return [
                {
                    "dxm_package_id": "pkg-1",
                    "extended_order_id": "#1001",
                    "product_display_sku": "SKU-A",
                    "quantity": 1,
                    "line_amount": 20,
                    "ship_amount": 4,
                    "logistic_fee": 6,
                    "purchase_price_cny": 35,
                    "xmyc_unit_price": None,
                    "product_purchase_price": None,
                }
            ]
        if "FROM shopify_payments_transactions" in sql:
            assert params == ("#1001",)
            return [{"order_name": "#1001", "fee": 2.4}]
        raise AssertionError(sql)

    monkeypatch.setattr(sku_actual_roas, "query", fake_query)
    monkeypatch.setattr(sku_actual_roas, "execute", lambda sql, params: calls["execute"].append((sql, params)) or 1)

    result = sku_actual_roas.compute_sku_actual_breakeven_roas(
        date(2026, 4, 9),
        date(2026, 5, 8),
        rmb_per_usd=7,
        source_run_id=99,
    )

    assert result["skus"] == 1
    assert result["snapshots_written"] == 1
    assert "ON DUPLICATE KEY UPDATE" in calls["execute"][0][0]
    assert calls["execute"][0][1][0] == "SKU-A"
    assert calls["execute"][0][1][12] == 99


def test_get_latest_sku_actual_roas_returns_map(monkeypatch):
    from appcore import sku_actual_roas

    def fake_query(sql, params=()):
        assert "MAX(computed_at)" in sql
        assert params == ("SKU-A", "SKU-B")
        return [
            {
                "sku": "SKU-A",
                "window_start": date(2026, 4, 9),
                "window_end": date(2026, 5, 8),
                "orders_count": 2,
                "units": 3,
                "actual_breakeven_roas": 2.3456,
                "fee_source": "mixed",
                "computed_at": datetime(2026, 5, 10, 0, 0, 8),
            }
        ]

    monkeypatch.setattr(sku_actual_roas, "query", fake_query)

    out = sku_actual_roas.get_latest_sku_actual_roas(["SKU-A", "SKU-B"])

    assert out["SKU-A"]["value"] == 2.3456
    assert out["SKU-A"]["fee_source"] == "mixed"
    assert out["SKU-A"]["window_start"] == "2026-04-09"
    assert out["SKU-A"]["computed_at"] == "2026-05-10T00:00:08"
```

- [ ] **Step 2: Run persistence tests red**

Run: `pytest tests/test_sku_actual_roas.py::test_compute_loads_orders_payments_and_upserts_snapshots tests/test_sku_actual_roas.py::test_get_latest_sku_actual_roas_returns_map -q`

Expected: FAIL because persistence functions do not exist.

- [ ] **Step 3: Add persistence functions**

Extend `appcore/sku_actual_roas.py` with DB loading, real fee loading, upsert, `compute_sku_actual_breakeven_roas`, and `get_latest_sku_actual_roas`. Use `json.dumps(..., ensure_ascii=False, default=str)` for `summary_json`.

- [ ] **Step 4: Run persistence tests green**

Run: `pytest tests/test_sku_actual_roas.py::test_compute_loads_orders_payments_and_upserts_snapshots tests/test_sku_actual_roas.py::test_get_latest_sku_actual_roas_returns_map -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/sku_actual_roas.py tests/test_sku_actual_roas.py
git commit -m "feat(sku): persist actual roas snapshots" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#后端流程"
```

### Task 4: Material Listing Serialization

**Files:**
- Modify: `web/services/media_products_listing.py`
- Modify: `web/routes/medias/_serializers.py`
- Test: `tests/test_media_products_listing_service.py`

- [ ] **Step 1: Write failing service test**

Update `test_build_products_list_response_enriches_rows_and_preserves_filters` so `build_products_list_response` receives `get_latest_sku_actual_roas_fn`, asserts it is called with `["sku-a", "sku-b"]`, and the fake serializer sees `sku_actual_roas_index`.

Use this assertion:

```python
assert calls["actual_roas_skus"] == ["sku-a", "sku-b"]
assert serialized[0][3]["sku_actual_roas_index"] == {"sku-a": {"value": 2.1}}
```

- [ ] **Step 2: Run service test red**

Run: `pytest tests/test_media_products_listing_service.py::test_build_products_list_response_enriches_rows_and_preserves_filters -q`

Expected: FAIL because `get_latest_sku_actual_roas_fn` is not accepted or not passed to the serializer.

- [ ] **Step 3: Implement service and serializer wiring**

In `web/services/media_products_listing.py`, import `sku_actual_roas`, add optional `get_latest_sku_actual_roas_fn`, compute `sku_actual_roas_index`, and pass it into `serialize_product_fn`.

In `web/routes/medias/_serializers.py`, add `sku_actual_roas_index` to `_serialize_product` and `_serialize_product_skus`, and set each item’s `actual_breakeven_roas` from `dianxiaomi_sku`.

- [ ] **Step 4: Run service test green**

Run: `pytest tests/test_media_products_listing_service.py::test_build_products_list_response_enriches_rows_and_preserves_filters -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/services/media_products_listing.py web/routes/medias/_serializers.py tests/test_media_products_listing_service.py
git commit -m "feat(medias): include sku actual roas snapshots" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#api-与序列化"
```

### Task 5: Frontend SKU Column

**Files:**
- Modify: `web/static/medias.js`
- Test: `tests/test_material_roas_frontend.py`

- [ ] **Step 1: Write failing frontend static test**

Append to `tests/test_material_roas_frontend.py`:

```python
def test_sku_detail_modal_renders_actual_breakeven_roas_after_estimated_roas():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "<th>估算 ROAS</th>" in html
    assert "<th>实际保本 ROAS</th>" in html
    assert html.index("<th>估算 ROAS</th>") < html.index("<th>实际保本 ROAS</th>")
    assert "fmtActualBreakevenRoas" in js
    assert "actual_breakeven_roas" in js
    assert "真实手续费" in js
    assert "7%估算" in js
    assert "部分真实" in js
```

- [ ] **Step 2: Run frontend test red**

Run: `pytest tests/test_material_roas_frontend.py::test_sku_detail_modal_renders_actual_breakeven_roas_after_estimated_roas -q`

Expected: FAIL because the header and renderer do not exist.

- [ ] **Step 3: Add frontend renderer and column**

Update the SKU detail modal table header in `web/templates/medias_list.html` if the header lives there. Update `web/static/medias.js` `renderSkuDetailRow` to render `fmtActualBreakevenRoas(s.actual_breakeven_roas)` immediately after `fmtRoas(s.roas_calculation)`. Add a helper that maps `real` to `真实手续费`, `estimated_7pct` to `7%估算`, and `mixed` to `部分真实`.

- [ ] **Step 4: Run frontend test green**

Run: `pytest tests/test_material_roas_frontend.py::test_sku_detail_modal_renders_actual_breakeven_roas_after_estimated_roas -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/static/medias.js web/templates/medias_list.html tests/test_material_roas_frontend.py
git commit -m "feat(medias): show sku actual breakeven roas" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#前端展示"
```

### Task 6: Scheduled Runner

**Files:**
- Create: `tools/sku_actual_roas_snapshot.py`
- Create: `deploy/server_browser/autovideosrt-sku-actual-roas.service`
- Create: `deploy/server_browser/autovideosrt-sku-actual-roas.timer`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_sku_actual_roas.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] **Step 1: Write failing runner and task definition tests**

Append to `tests/test_sku_actual_roas.py`:

```python
def test_run_snapshot_records_scheduled_task_run(monkeypatch):
    from tools import sku_actual_roas_snapshot

    calls = []
    monkeypatch.setattr(sku_actual_roas_snapshot.scheduled_tasks, "start_run", lambda code: calls.append(("start", code)) or 42)
    monkeypatch.setattr(
        sku_actual_roas_snapshot.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        sku_actual_roas_snapshot.sku_actual_roas,
        "compute_sku_actual_breakeven_roas",
        lambda window_start, window_end, source_run_id=None: {
            "window_start": str(window_start),
            "window_end": str(window_end),
            "skus": 3,
            "snapshots_written": 3,
            "source_run_id": source_run_id,
        },
    )

    exit_code = sku_actual_roas_snapshot.run_snapshot(run_date=date(2026, 5, 10))

    assert exit_code == 0
    assert calls[0] == ("start", "sku_actual_breakeven_roas")
    assert calls[1][0] == "finish"
    assert calls[1][2]["status"] == "success"
    assert calls[1][2]["summary"]["window_start"] == "2026-04-09"
```

Append to `tests/test_appcore_scheduled_tasks.py`:

```python
def test_task_definitions_include_sku_actual_breakeven_roas():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["sku_actual_breakeven_roas"]
    assert task["schedule"] == "每天 00:00"
    assert task["source_ref"] == "autovideosrt-sku-actual-roas.timer"
    assert task["runner"] == "tools/sku_actual_roas_snapshot.py"
    assert task["log_table"] == "scheduled_task_runs"
```

- [ ] **Step 2: Run runner tests red**

Run: `pytest tests/test_sku_actual_roas.py::test_run_snapshot_records_scheduled_task_run tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_sku_actual_breakeven_roas -q`

Expected: FAIL because runner and task definition are missing.

- [ ] **Step 3: Implement runner, timer, and task definition**

Create `tools/sku_actual_roas_snapshot.py` with `TASK_CODE = "sku_actual_breakeven_roas"`, `run_snapshot()`, CLI args `--date`, `--window-days`, `--settlement-delay-days`, `--dry-run`, and `main()`.

Create service/timer under `deploy/server_browser/`. Timer uses `OnCalendar=*-*-* 00:00:00` and `Persistent=true`.

Add `sku_actual_breakeven_roas` to `appcore/scheduled_tasks.py` with systemd source metadata and `log_table="scheduled_task_runs"`.

- [ ] **Step 4: Run runner tests green**

Run: `pytest tests/test_sku_actual_roas.py::test_run_snapshot_records_scheduled_task_run tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_sku_actual_breakeven_roas -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/sku_actual_roas_snapshot.py deploy/server_browser/autovideosrt-sku-actual-roas.service deploy/server_browser/autovideosrt-sku-actual-roas.timer appcore/scheduled_tasks.py tests/test_sku_actual_roas.py tests/test_appcore_scheduled_tasks.py
git commit -m "feat(sku): schedule actual roas snapshot" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#定时任务"
```

### Task 7: Project Docs and Focused Verification

**Files:**
- Modify: `AGENTS.md`
- Test: focused pytest command

- [ ] **Step 1: Update project docs**

Add a short section to `AGENTS.md`:

```markdown
## SKU 实际保本 ROAS（2026-05-10 起）

- 设计：[docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md](docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md)。
- 每天北京时间 00:00 由 `tools/sku_actual_roas_snapshot.py` 计算 `D-31` 到 `D-2` 的滚动 30 天 SKU 实际保本 ROAS，写入 `sku_actual_breakeven_roas_snapshots`；素材管理 SKU 配对详情在“估算 ROAS”后展示“实际保本 ROAS”。
- 手续费优先用 `shopify_payments_transactions` 真实 Payment fee；未命中时用 7% 估算，前端标签显示 `真实手续费`、`7%估算` 或 `部分真实`。
- 改这条链路至少运行：`pytest tests/test_sku_actual_roas.py tests/test_sku_aggregates.py tests/test_media_products_listing_service.py tests/test_material_roas_frontend.py tests/test_appcore_scheduled_tasks.py -q`。
```

- [ ] **Step 2: Run focused verification**

Run:

```bash
pytest tests/test_sku_actual_roas.py tests/test_sku_aggregates.py tests/test_media_products_listing_service.py tests/test_material_roas_frontend.py tests/test_appcore_scheduled_tasks.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit docs**

```bash
git add AGENTS.md
git commit -m "docs(sku): document actual roas snapshot chain" -m "Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md#验收标准"
```

### Task 8: Final Verification

**Files:**
- Read-only verification

- [ ] **Step 1: Run final focused suite**

Run:

```bash
pytest tests/test_sku_actual_roas.py tests/test_sku_aggregates.py tests/test_media_products_listing_service.py tests/test_material_roas_frontend.py tests/test_appcore_scheduled_tasks.py -q
```

Expected: PASS.

- [ ] **Step 2: Inspect git status**

Run:

```bash
git status --short --branch
```

Expected: working tree clean after commits.

- [ ] **Step 3: Summarize implementation**

Report changed files, verification output, and any deployment follow-up for installing the new systemd timer.
