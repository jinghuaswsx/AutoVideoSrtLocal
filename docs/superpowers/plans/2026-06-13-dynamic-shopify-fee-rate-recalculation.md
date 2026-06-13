# Dynamic Shopify Fee Rate Recalculation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dynamic Shopify fee-rate mechanism that only affects orders at or after `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT`, while keeping historical `order_profit_lines` unchanged.

**Architecture:** Add persisted regional fee-rate snapshots from Shopify Payments imports, then route new profit calculations and realtime fallback estimates through one resolver. The resolver chooses `actual_payment` first, `dynamic_region_rate` second, and the existing strategy C estimate last. Backfill and incremental jobs skip pre-effective orders so old profit rows are not overwritten.

**Tech Stack:** Python 3.12 / Flask / MySQL-compatible migrations / pytest / existing `appcore.order_analytics` repository helpers.

---

## Anchors And Scope

**Docs anchor:** `docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md`

**Related rules:**
- `AGENTS.md`: document-driven changes and no main worktree pollution.
- `appcore/order_analytics/CLAUDE.md`: realtime dashboard, site filtering, data quality, and focused test expectations.
- `docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md`: existing strategy C fee rules.

**Files to create:**
- `appcore/order_analytics/shopify_fee_dynamic.py`: region mapping, snapshot row building, snapshot persistence, best snapshot lookup.
- `appcore/order_analytics/shopify_fee_resolver.py`: shared order-level fee resolver and effective-time parsing.
- `db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql`: snapshot table, profit-line trace columns, and `shopify_payments_transactions.transaction_date` for stable snapshot windows.
- `tests/test_shopify_fee_dynamic.py`: dynamic snapshot and resolver tests.
- `tests/test_order_profit_backfill_dynamic_fee.py`: effective-boundary and resolver integration tests.
- `tests/test_dynamic_shopify_fee_migration.py`: migration text guard.

**Files to modify:**
- `appcore/order_analytics/profit_calculation.py`: consume a pre-resolved order fee and return trace fields.
- `appcore/order_analytics/profit_repository.py`: persist new trace columns.
- `appcore/order_analytics/shopify_payments_import.py`: refresh snapshots after a successful CSV import.
- `tools/order_profit_backfill.py`: fetch order identifiers, skip legacy orders, call the shared resolver once per order.
- `tools/order_profit_incremental.py`: no direct logic change expected; it inherits the guarded backfill path. Keep a smoke test or import check after backfill changes.
- `appcore/order_analytics/realtime.py`: use the resolver for rows without stored profit data and expose source counts.
- `tests/test_profit_calculation.py`: line allocation and trace fields.
- `tests/test_profit_repository.py`: repository writes new columns.
- `tests/test_shopify_payments_import.py`: import triggers snapshot refresh.
- `tests/test_order_analytics_realtime_profit_details.py`: realtime fallback source and summary counts.

**Out of scope:**
- No historical recalculation before `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT`.
- No Shopify Admin API or BIN lookup.
- No change to purchase, logistics, returns, or ad-spend allocation formulas.

---

## Data Contract

Use these names consistently across all tasks.

```python
FEE_SOURCE_ACTUAL_PAYMENT = "actual_payment"
FEE_SOURCE_DYNAMIC_REGION_RATE = "dynamic_region_rate"
FEE_SOURCE_STRATEGY_C_FALLBACK = "strategy_c_fallback"
FEE_SOURCE_LEGACY_STRATEGY_C = "legacy_strategy_c"

REGION_US = "us"
REGION_EUROPE = "europe"
REGION_OTHER = "other"
```

Order-level resolver return shape:

```python
{
    "shopify_fee_usd": 1.23,
    "shopify_tier": "dynamic_region_rate",
    "presentment_currency": "EUR",
    "shopify_fee_source": "dynamic_region_rate",
    "shopify_fee_rate": 0.072413,
    "shopify_fee_rate_region": "europe",
    "shopify_fee_rate_window_start": date(2026, 5, 30),
    "shopify_fee_rate_window_end": date(2026, 6, 5),
    "shopify_fee_basis": {
        "strategy_version": "dynamic_shopify_fee_v1",
        "order_total_revenue_usd": 20.0,
        "order_fee_usd": 1.7483,
        "fixed_fee_usd": 0.30,
        "snapshot_id": 12,
    },
}
```

Line-level allocation must add:

```python
"line_allocation_ratio": 0.5
```

inside `shopify_fee_basis_json` / `cost_basis["shopify_fee_basis"]`.

---

### Task 1: Add Migration For Snapshots And Trace Columns

**Files:**
- Create: `db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql`
- Create: `tests/test_dynamic_shopify_fee_migration.py`

- [x] **Step 1.1: Write the migration text test**

Create `tests/test_dynamic_shopify_fee_migration.py`:

```python
from pathlib import Path


MIGRATION = Path("db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql")


def test_dynamic_shopify_fee_migration_contains_snapshot_table_and_trace_columns():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists shopify_fee_rate_snapshots" in sql
    assert "store_code" in sql
    assert "region" in sql
    assert "effective_rate" in sql
    assert "variable_rate" in sql
    assert "sample_status" in sql
    assert "source_csvs_json" in sql

    assert "alter table order_profit_lines" in sql
    assert "shopify_fee_source" in sql
    assert "shopify_fee_rate" in sql
    assert "shopify_fee_rate_region" in sql
    assert "shopify_fee_rate_window_start" in sql
    assert "shopify_fee_rate_window_end" in sql
    assert "shopify_fee_basis_json" in sql

    assert "alter table shopify_payments_transactions" in sql
    assert "transaction_date" in sql


def test_dynamic_shopify_fee_migration_has_lookup_indexes():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "idx_fee_snapshots_lookup" in sql
    assert "idx_profit_fee_source" in sql
```

- [x] **Step 1.2: Run the new test and confirm it fails**

Run:

```bash
pytest tests/test_dynamic_shopify_fee_migration.py -q
```

Expected: FAIL with `FileNotFoundError` for `db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql`.

- [x] **Step 1.3: Add the migration**

Create `db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql`:

```sql
CREATE TABLE IF NOT EXISTS shopify_fee_rate_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    store_code VARCHAR(32) NOT NULL,
    region VARCHAR(16) NOT NULL,
    window_start_date DATE NOT NULL,
    window_end_date DATE NOT NULL,
    window_days INT NOT NULL,
    orders_count INT NOT NULL DEFAULT 0,
    amount_usd DECIMAL(18, 4) NOT NULL DEFAULT 0,
    fee_usd DECIMAL(18, 4) NOT NULL DEFAULT 0,
    effective_rate DECIMAL(12, 8) NOT NULL DEFAULT 0,
    fixed_fee_per_order DECIMAL(10, 4) NOT NULL DEFAULT 0.3000,
    variable_rate DECIMAL(12, 8) NOT NULL DEFAULT 0,
    source_csvs_json JSON NULL,
    sample_status VARCHAR(32) NOT NULL,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_fee_snapshots_lookup (store_code, region, window_end_date, sample_status),
    KEY idx_fee_snapshots_computed_at (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE order_profit_lines
    ADD COLUMN shopify_fee_source VARCHAR(32) NULL,
    ADD COLUMN shopify_fee_rate DECIMAL(12, 8) NULL,
    ADD COLUMN shopify_fee_rate_region VARCHAR(16) NULL,
    ADD COLUMN shopify_fee_rate_window_start DATE NULL,
    ADD COLUMN shopify_fee_rate_window_end DATE NULL,
    ADD COLUMN shopify_fee_basis_json JSON NULL,
    ADD KEY idx_profit_fee_source (shopify_fee_source);

ALTER TABLE shopify_payments_transactions
    ADD COLUMN transaction_date VARCHAR(64) NULL,
    ADD KEY idx_shopify_payments_transaction_date (transaction_date);
```

If the target MySQL rejects duplicate `ADD COLUMN` on a rerun, replace the `ALTER TABLE` with the repository's established idempotent pattern after checking existing migration conventions. Do not change column names.

- [x] **Step 1.4: Run the migration test**

Run:

```bash
pytest tests/test_dynamic_shopify_fee_migration.py -q
```

Expected: PASS.

- [x] **Step 1.5: Commit**

```bash
git add db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql tests/test_dynamic_shopify_fee_migration.py
git commit -m "feat: add dynamic shopify fee schema" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 2: Implement Dynamic Snapshot Builder

**Files:**
- Create: `appcore/order_analytics/shopify_fee_dynamic.py`
- Create/modify: `tests/test_shopify_fee_dynamic.py`

- [x] **Step 2.1: Write failing tests for region mapping and snapshot math**

Create `tests/test_shopify_fee_dynamic.py` with these tests first:

```python
from __future__ import annotations

from datetime import date

from appcore.order_analytics.shopify_fee_dynamic import (
    SAMPLE_STATUS_INSUFFICIENT,
    SAMPLE_STATUS_OK_30D,
    SAMPLE_STATUS_OK_7D,
    build_snapshot_row,
    infer_store_code_from_source_csv,
    region_for_presentment_currency,
    select_snapshot_window,
)


def test_region_for_presentment_currency():
    assert region_for_presentment_currency("USD") == "us"
    assert region_for_presentment_currency("eur") == "europe"
    assert region_for_presentment_currency("GBP") == "europe"
    assert region_for_presentment_currency("JPY") == "other"
    assert region_for_presentment_currency(None) == "other"


def test_infer_store_code_from_source_csv():
    assert infer_store_code_from_source_csv("newjoyloo__newjoyloo0606.csv") == "newjoy"
    assert infer_store_code_from_source_csv("Omurio__omurio0606.csv") == "omurio"
    assert infer_store_code_from_source_csv("") == "all"


def test_build_snapshot_row_keeps_fixed_fee_separate():
    row = build_snapshot_row(
        store_code="newjoy",
        region="europe",
        window_start_date=date(2026, 5, 30),
        window_end_date=date(2026, 6, 5),
        window_days=7,
        orders_count=3290,
        amount_usd=88165.45,
        fee_usd=6649.36,
        source_csvs=["newjoyloo__newjoyloo0606.csv"],
        sample_status=SAMPLE_STATUS_OK_7D,
    )

    assert row["store_code"] == "newjoy"
    assert row["region"] == "europe"
    assert row["effective_rate"] == round(6649.36 / 88165.45, 8)
    expected_variable_rate = (6649.36 - 3290 * 0.30) / 88165.45
    assert row["variable_rate"] == round(expected_variable_rate, 8)
    assert row["fixed_fee_per_order"] == 0.30
    assert row["source_csvs_json"] == ["newjoyloo__newjoyloo0606.csv"]


def test_select_snapshot_window_prefers_sufficient_7d():
    selected = select_snapshot_window(
        seven_day={"orders_count": 100, "amount_usd": 1000.0, "fee_usd": 70.0},
        thirty_day={"orders_count": 500, "amount_usd": 5000.0, "fee_usd": 350.0},
    )
    assert selected["sample_status"] == SAMPLE_STATUS_OK_7D
    assert selected["window_days"] == 7


def test_select_snapshot_window_uses_30d_when_7d_is_small():
    selected = select_snapshot_window(
        seven_day={"orders_count": 99, "amount_usd": 990.0, "fee_usd": 70.0},
        thirty_day={"orders_count": 300, "amount_usd": 3000.0, "fee_usd": 210.0},
    )
    assert selected["sample_status"] == SAMPLE_STATUS_OK_30D
    assert selected["window_days"] == 30


def test_select_snapshot_window_marks_insufficient():
    selected = select_snapshot_window(
        seven_day={"orders_count": 40, "amount_usd": 400.0, "fee_usd": 28.0},
        thirty_day={"orders_count": 299, "amount_usd": 2990.0, "fee_usd": 209.3},
    )
    assert selected["sample_status"] == SAMPLE_STATUS_INSUFFICIENT
    assert selected["window_days"] == 30
```

- [x] **Step 2.2: Run tests and confirm import failure**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'appcore.order_analytics.shopify_fee_dynamic'`.

- [x] **Step 2.3: Implement mapping and pure snapshot helpers**

Create `appcore/order_analytics/shopify_fee_dynamic.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping

from appcore.db import execute, query


REGION_US = "us"
REGION_EUROPE = "europe"
REGION_OTHER = "other"

SAMPLE_STATUS_OK_7D = "ok_7d"
SAMPLE_STATUS_OK_30D = "ok_30d"
SAMPLE_STATUS_INSUFFICIENT = "insufficient"

EUROPE_PRESENTMENT_CURRENCIES = {
    "EUR",
    "GBP",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "BGN",
}

MIN_7D_ORDERS = 100
MIN_30D_ORDERS = 300
FIXED_FEE_PER_ORDER = Decimal("0.30")


def _round_float(value: Decimal, places: str = "0.00000001") -> float:
    return float(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def region_for_presentment_currency(currency: str | None) -> str:
    normalized = (currency or "").strip().upper()
    if normalized == "USD":
        return REGION_US
    if normalized in EUROPE_PRESENTMENT_CURRENCIES:
        return REGION_EUROPE
    return REGION_OTHER


def infer_store_code_from_source_csv(source_csv: str | None) -> str:
    name = Path(source_csv or "").name.lower()
    if name.startswith("newjoyloo__"):
        return "newjoy"
    if name.startswith("omurio__"):
        return "omurio"
    return "all"


def select_snapshot_window(
    *,
    seven_day: Mapping[str, Any],
    thirty_day: Mapping[str, Any],
) -> dict[str, Any]:
    seven_orders = int(seven_day.get("orders_count") or 0)
    if seven_orders >= MIN_7D_ORDERS:
        selected = dict(seven_day)
        selected["window_days"] = 7
        selected["sample_status"] = SAMPLE_STATUS_OK_7D
        return selected

    thirty_orders = int(thirty_day.get("orders_count") or 0)
    selected = dict(thirty_day)
    selected["window_days"] = 30
    selected["sample_status"] = (
        SAMPLE_STATUS_OK_30D
        if thirty_orders >= MIN_30D_ORDERS
        else SAMPLE_STATUS_INSUFFICIENT
    )
    return selected


def build_snapshot_row(
    *,
    store_code: str,
    region: str,
    window_start_date: date,
    window_end_date: date,
    window_days: int,
    orders_count: int,
    amount_usd: Any,
    fee_usd: Any,
    source_csvs: Iterable[str],
    sample_status: str,
) -> dict[str, Any]:
    amount = _to_decimal(amount_usd)
    fee = _to_decimal(fee_usd)
    orders = int(orders_count or 0)
    effective_rate = Decimal("0") if amount <= 0 else fee / amount
    variable_fee = fee - (Decimal(orders) * FIXED_FEE_PER_ORDER)
    if variable_fee < 0:
        variable_fee = Decimal("0")
    variable_rate = Decimal("0") if amount <= 0 else variable_fee / amount

    return {
        "store_code": store_code,
        "region": region,
        "window_start_date": window_start_date,
        "window_end_date": window_end_date,
        "window_days": int(window_days),
        "orders_count": orders,
        "amount_usd": float(amount),
        "fee_usd": float(fee),
        "effective_rate": _round_float(effective_rate),
        "fixed_fee_per_order": float(FIXED_FEE_PER_ORDER),
        "variable_rate": _round_float(variable_rate),
        "source_csvs_json": list(source_csvs),
        "sample_status": sample_status,
    }
```

- [x] **Step 2.4: Run pure helper tests**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py -q
```

Expected: PASS for the tests added in Step 2.1.

- [x] **Step 2.5: Add persistence and lookup tests**

Append to `tests/test_shopify_fee_dynamic.py`:

```python
from appcore.order_analytics.shopify_fee_dynamic import (
    load_best_fee_rate_snapshot,
    save_fee_rate_snapshots,
)


def test_save_fee_rate_snapshots_inserts_rows(monkeypatch):
    calls = []

    def fake_execute(sql, params=None):
        calls.append((sql, params))

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.execute", fake_execute)

    row = build_snapshot_row(
        store_code="newjoy",
        region="us",
        window_start_date=date(2026, 5, 30),
        window_end_date=date(2026, 6, 5),
        window_days=7,
        orders_count=389,
        amount_usd=12258.58,
        fee_usd=473.62,
        source_csvs=["newjoyloo__newjoyloo0606.csv"],
        sample_status=SAMPLE_STATUS_OK_7D,
    )

    save_fee_rate_snapshots([row])

    assert len(calls) == 1
    assert "insert into shopify_fee_rate_snapshots" in calls[0][0].lower()
    assert calls[0][1][0] == "newjoy"
    assert calls[0][1][1] == "us"


def test_load_best_fee_rate_snapshot_prefers_store_region(monkeypatch):
    queries = []

    def fake_query(sql, params=None):
        queries.append(params)
        return [
            {
                "id": 9,
                "store_code": "newjoy",
                "region": "europe",
                "window_start_date": date(2026, 5, 30),
                "window_end_date": date(2026, 6, 5),
                "orders_count": 3290,
                "effective_rate": 0.07542,
                "variable_rate": 0.06422,
                "fixed_fee_per_order": 0.30,
                "sample_status": SAMPLE_STATUS_OK_7D,
            }
        ]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)

    snapshot = load_best_fee_rate_snapshot("newjoy", "europe")

    assert snapshot["id"] == 9
    assert snapshot["store_code"] == "newjoy"
    assert queries[0] == ("newjoy", "europe")


def test_load_best_fee_rate_snapshot_falls_back_to_all_store_scope(monkeypatch):
    calls = []

    def fake_query(sql, params=None):
        calls.append(params)
        if params[0] == "newjoy":
            return []
        return [
            {
                "id": 22,
                "store_code": "all",
                "region": "other",
                "window_start_date": date(2026, 5, 30),
                "window_end_date": date(2026, 6, 5),
                "orders_count": 400,
                "effective_rate": 0.064,
                "variable_rate": 0.052,
                "fixed_fee_per_order": 0.30,
                "sample_status": SAMPLE_STATUS_OK_7D,
            }
        ]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)

    snapshot = load_best_fee_rate_snapshot("newjoy", "other")

    assert snapshot["id"] == 22
    assert calls == [("newjoy", "other"), ("all", "other")]
```

- [x] **Step 2.6: Implement persistence and lookup**

Append to `appcore/order_analytics/shopify_fee_dynamic.py`:

```python
def save_fee_rate_snapshots(rows: Iterable[Mapping[str, Any]]) -> int:
    saved = 0
    sql = """
        INSERT INTO shopify_fee_rate_snapshots (
            store_code,
            region,
            window_start_date,
            window_end_date,
            window_days,
            orders_count,
            amount_usd,
            fee_usd,
            effective_rate,
            fixed_fee_per_order,
            variable_rate,
            source_csvs_json,
            sample_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    for row in rows:
        execute(
            sql,
            (
                row["store_code"],
                row["region"],
                row["window_start_date"],
                row["window_end_date"],
                row["window_days"],
                row["orders_count"],
                row["amount_usd"],
                row["fee_usd"],
                row["effective_rate"],
                row["fixed_fee_per_order"],
                row["variable_rate"],
                json.dumps(row.get("source_csvs_json") or [], ensure_ascii=False),
                row["sample_status"],
            ),
        )
        saved += 1
    return saved


def _load_snapshot_for_store_region(store_code: str, region: str) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT
            id,
            store_code,
            region,
            window_start_date,
            window_end_date,
            window_days,
            orders_count,
            amount_usd,
            fee_usd,
            effective_rate,
            fixed_fee_per_order,
            variable_rate,
            source_csvs_json,
            sample_status,
            computed_at
        FROM shopify_fee_rate_snapshots
        WHERE store_code = %s
          AND region = %s
          AND sample_status IN ('ok_7d', 'ok_30d')
        ORDER BY window_end_date DESC, computed_at DESC, id DESC
        LIMIT 1
        """,
        (store_code, region),
    )
    return dict(rows[0]) if rows else None


def load_best_fee_rate_snapshot(store_code: str | None, region: str) -> dict[str, Any] | None:
    normalized_store = (store_code or "").strip().lower() or "all"
    snapshot = _load_snapshot_for_store_region(normalized_store, region)
    if snapshot is not None:
        return snapshot
    if normalized_store != "all":
        return _load_snapshot_for_store_region("all", region)
    return None
```

- [x] **Step 2.7: Run tests**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py -q
```

Expected: PASS.

- [x] **Step 2.8: Commit**

```bash
git add appcore/order_analytics/shopify_fee_dynamic.py tests/test_shopify_fee_dynamic.py
git commit -m "feat: add dynamic shopify fee snapshots" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 3: Build Shared Shopify Fee Resolver

**Files:**
- Create: `appcore/order_analytics/shopify_fee_resolver.py`
- Modify: `tests/test_shopify_fee_dynamic.py`

- [x] **Step 3.1: Add resolver tests for priority and effective boundary**

Append to `tests/test_shopify_fee_dynamic.py`:

```python
from datetime import datetime

from appcore.order_analytics.shopify_fee_resolver import (
    FEE_SOURCE_ACTUAL_PAYMENT,
    FEE_SOURCE_DYNAMIC_REGION_RATE,
    FEE_SOURCE_LEGACY_STRATEGY_C,
    FEE_SOURCE_STRATEGY_C_FALLBACK,
    is_dynamic_fee_effective,
    resolve_shopify_fee_for_order,
)


def test_is_dynamic_fee_effective_uses_configured_boundary(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    assert not is_dynamic_fee_effective(datetime(2026, 6, 13, 0, 59, 59))
    assert is_dynamic_fee_effective(datetime(2026, 6, 13, 1, 0, 0))


def test_resolver_returns_legacy_strategy_for_pre_effective_order(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="US",
        site_code="newjoy",
        order_names=["#1001"],
        order_time=datetime(2026, 6, 12, 23, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_LEGACY_STRATEGY_C
    assert result["shopify_fee_usd"] > 0


def test_resolver_prefers_actual_payment(monkeypatch):
    monkeypatch.delenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", raising=False)

    def fake_query(sql, params=None):
        return [{"fee_usd": 1.19, "transaction_ids": "11,12"}]

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fake_query)

    result = resolve_shopify_fee_for_order(
        amount=22.13,
        buyer_country="DE",
        site_code="newjoy",
        order_names=["#2001", "2001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_ACTUAL_PAYMENT
    assert result["shopify_fee_usd"] == 1.19
    assert result["shopify_fee_basis"]["matched_payment_transaction_ids"] == ["11", "12"]


def test_resolver_uses_dynamic_region_rate_when_no_actual_payment(monkeypatch):
    monkeypatch.delenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", raising=False)

    def fake_query(sql, params=None):
        return []

    def fake_snapshot(store_code, region):
        assert store_code == "newjoy"
        assert region == "europe"
        return {
            "id": 9,
            "store_code": "newjoy",
            "region": "europe",
            "window_start_date": date(2026, 5, 30),
            "window_end_date": date(2026, 6, 5),
            "effective_rate": 0.07542,
            "variable_rate": 0.06422,
            "fixed_fee_per_order": 0.30,
            "sample_status": SAMPLE_STATUS_OK_7D,
        }

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", fake_query)
    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.load_best_fee_rate_snapshot", fake_snapshot)

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="DE",
        site_code="newjoy",
        order_names=["#3001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_DYNAMIC_REGION_RATE
    assert result["shopify_fee_usd"] == 6.72
    assert result["shopify_fee_rate"] == 0.07542
    assert result["shopify_fee_rate_region"] == "europe"
    assert result["shopify_fee_basis"]["snapshot_id"] == 9


def test_resolver_falls_back_to_strategy_c(monkeypatch):
    monkeypatch.delenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", raising=False)
    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.query", lambda sql, params=None: [])
    monkeypatch.setattr("appcore.order_analytics.shopify_fee_resolver.load_best_fee_rate_snapshot", lambda store_code, region: None)

    result = resolve_shopify_fee_for_order(
        amount=100,
        buyer_country="US",
        site_code="newjoy",
        order_names=["#4001"],
        order_time=datetime(2026, 6, 13, 10, 0, 0),
    )

    assert result["shopify_fee_source"] == FEE_SOURCE_STRATEGY_C_FALLBACK
    assert result["shopify_fee_usd"] > 0
    assert result["shopify_fee_basis"]["fallback_reason"] == "no_actual_payment_or_dynamic_snapshot"
```

- [x] **Step 3.2: Run resolver tests and confirm failure**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `appcore.order_analytics.shopify_fee_resolver`.

- [x] **Step 3.3: Implement resolver**

Create `appcore/order_analytics/shopify_fee_resolver.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from appcore.db import query
from config import Config

from .shopify_fee import (
    estimate_fee_for_buyer_country,
    infer_presentment_currency_from_country,
)
from .shopify_fee_dynamic import (
    FIXED_FEE_PER_ORDER,
    load_best_fee_rate_snapshot,
    region_for_presentment_currency,
)


FEE_SOURCE_ACTUAL_PAYMENT = "actual_payment"
FEE_SOURCE_DYNAMIC_REGION_RATE = "dynamic_region_rate"
FEE_SOURCE_STRATEGY_C_FALLBACK = "strategy_c_fallback"
FEE_SOURCE_LEGACY_STRATEGY_C = "legacy_strategy_c"
STRATEGY_VERSION = "dynamic_shopify_fee_v1"


def _round_money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _parse_effective_at() -> datetime | None:
    raw = getattr(Config, "SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", None)
    if not raw:
        import os
        raw = os.getenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT")
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).strip())
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def is_dynamic_fee_effective(order_time: datetime | None) -> bool:
    effective_at = _parse_effective_at()
    if effective_at is None:
        return True
    if order_time is None:
        return True
    comparable = order_time
    if comparable.tzinfo is not None:
        comparable = comparable.astimezone(timezone.utc).replace(tzinfo=None)
    return comparable >= effective_at


def _normalized_order_names(order_names: Iterable[str | None]) -> list[str]:
    values: list[str] = []
    for raw in order_names:
        name = str(raw or "").strip()
        if not name:
            continue
        values.append(name)
        if name.startswith("#"):
            values.append(name[1:])
        else:
            values.append(f"#{name}")
    return sorted(set(values))


def _load_actual_payment_fee(order_names: Iterable[str | None]) -> dict[str, Any] | None:
    names = _normalized_order_names(order_names)
    if not names:
        return None
    placeholders = ", ".join(["%s"] * len(names))
    rows = query(
        f"""
        SELECT
            SUM(ABS(fee_usd)) AS fee_usd,
            GROUP_CONCAT(id ORDER BY id) AS transaction_ids
        FROM shopify_payments_transactions
        WHERE type = 'charge'
          AND order_name IN ({placeholders})
        """,
        tuple(names),
    )
    if not rows:
        return None
    row = rows[0]
    fee = _to_decimal(row.get("fee_usd"))
    if fee <= 0:
        return None
    ids = [part for part in str(row.get("transaction_ids") or "").split(",") if part]
    return {"fee_usd": _round_money(fee), "transaction_ids": ids}


def _strategy_c_result(
    *,
    amount: Any,
    buyer_country: str | None,
    source: str,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    estimate = estimate_fee_for_buyer_country(amount, buyer_country)
    basis = {
        "strategy_version": STRATEGY_VERSION,
        "order_total_revenue_usd": float(_to_decimal(amount)),
        "order_fee_usd": estimate["fee"],
    }
    if fallback_reason:
        basis["fallback_reason"] = fallback_reason
    return {
        "shopify_fee_usd": estimate["fee"],
        "shopify_tier": estimate.get("tier"),
        "presentment_currency": estimate.get("presentment_currency"),
        "shopify_fee_source": source,
        "shopify_fee_rate": None,
        "shopify_fee_rate_region": region_for_presentment_currency(estimate.get("presentment_currency")),
        "shopify_fee_rate_window_start": None,
        "shopify_fee_rate_window_end": None,
        "shopify_fee_basis": basis,
    }


def resolve_shopify_fee_for_order(
    *,
    amount: Any,
    buyer_country: str | None,
    site_code: str | None,
    order_names: Iterable[str | None],
    order_time: datetime | None,
) -> dict[str, Any]:
    if not is_dynamic_fee_effective(order_time):
        return _strategy_c_result(
            amount=amount,
            buyer_country=buyer_country,
            source=FEE_SOURCE_LEGACY_STRATEGY_C,
            fallback_reason="before_dynamic_fee_effective_at",
        )

    actual = _load_actual_payment_fee(order_names)
    presentment_currency = infer_presentment_currency_from_country(buyer_country)
    region = region_for_presentment_currency(presentment_currency)
    amount_d = _to_decimal(amount)
    if actual is not None:
        return {
            "shopify_fee_usd": actual["fee_usd"],
            "shopify_tier": FEE_SOURCE_ACTUAL_PAYMENT,
            "presentment_currency": presentment_currency,
            "shopify_fee_source": FEE_SOURCE_ACTUAL_PAYMENT,
            "shopify_fee_rate": None if amount_d <= 0 else float((_to_decimal(actual["fee_usd"]) / amount_d).quantize(Decimal("0.00000001"))),
            "shopify_fee_rate_region": region,
            "shopify_fee_rate_window_start": None,
            "shopify_fee_rate_window_end": None,
            "shopify_fee_basis": {
                "strategy_version": STRATEGY_VERSION,
                "order_total_revenue_usd": float(amount_d),
                "order_fee_usd": actual["fee_usd"],
                "matched_payment_transaction_ids": actual["transaction_ids"],
            },
        }

    snapshot = load_best_fee_rate_snapshot(site_code, region)
    if snapshot is not None:
        variable_fee = amount_d * _to_decimal(snapshot["variable_rate"])
        fee = variable_fee + _to_decimal(snapshot.get("fixed_fee_per_order") or FIXED_FEE_PER_ORDER)
        fee_usd = _round_money(fee)
        return {
            "shopify_fee_usd": fee_usd,
            "shopify_tier": FEE_SOURCE_DYNAMIC_REGION_RATE,
            "presentment_currency": presentment_currency,
            "shopify_fee_source": FEE_SOURCE_DYNAMIC_REGION_RATE,
            "shopify_fee_rate": float(snapshot["effective_rate"]),
            "shopify_fee_rate_region": region,
            "shopify_fee_rate_window_start": snapshot["window_start_date"],
            "shopify_fee_rate_window_end": snapshot["window_end_date"],
            "shopify_fee_basis": {
                "strategy_version": STRATEGY_VERSION,
                "order_total_revenue_usd": float(amount_d),
                "order_fee_usd": fee_usd,
                "fixed_fee_usd": float(snapshot.get("fixed_fee_per_order") or FIXED_FEE_PER_ORDER),
                "snapshot_id": snapshot["id"],
                "snapshot_sample_status": snapshot.get("sample_status"),
            },
        }

    return _strategy_c_result(
        amount=amount,
        buyer_country=buyer_country,
        source=FEE_SOURCE_STRATEGY_C_FALLBACK,
        fallback_reason="no_actual_payment_or_dynamic_snapshot",
    )
```

- [x] **Step 3.4: Add Config attribute**

Modify `config.py` inside `class Config`:

```python
    SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT = os.getenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "")
```

- [x] **Step 3.5: Run resolver tests**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py -q
```

Expected: PASS.

- [x] **Step 3.6: Commit**

```bash
git add appcore/order_analytics/shopify_fee_resolver.py appcore/order_analytics/shopify_fee_dynamic.py tests/test_shopify_fee_dynamic.py config.py
git commit -m "feat: resolve dynamic shopify fees for new orders" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 4: Let Profit Calculation Consume Resolved Order Fees

**Files:**
- Modify: `appcore/order_analytics/profit_calculation.py`
- Modify: `tests/test_profit_calculation.py`

- [x] **Step 4.1: Add failing tests for allocated resolved fee**

Append to `tests/test_profit_calculation.py`:

```python
def test_calculate_line_profit_uses_resolved_order_shopify_fee_with_allocation():
    result = calculate_line_profit(
        {
            "dxm_order_line_id": "L-dynamic-1",
            "product_id": 10,
            "buyer_country": "DE",
            "line_amount_usd": 40.0,
            "shipping_allocated_usd": 0.0,
            "order_total_revenue_usd": 100.0,
            "purchase_cost_cny": 70.0,
            "shipping_cost_cny": 0.0,
            "ad_cost_usd": 0.0,
            "quantity": 1,
            "shopify_fee_result": {
                "shopify_fee_usd": 6.72,
                "shopify_tier": "dynamic_region_rate",
                "presentment_currency": "EUR",
                "shopify_fee_source": "dynamic_region_rate",
                "shopify_fee_rate": 0.07542,
                "shopify_fee_rate_region": "europe",
                "shopify_fee_rate_window_start": "2026-05-30",
                "shopify_fee_rate_window_end": "2026-06-05",
                "shopify_fee_basis": {
                    "strategy_version": "dynamic_shopify_fee_v1",
                    "order_total_revenue_usd": 100.0,
                    "order_fee_usd": 6.72,
                    "snapshot_id": 9,
                },
            },
        },
        rmb_per_usd=Decimal("7.0"),
    )

    assert result["shopify_fee_usd"] == Decimal("2.6880")
    assert result["shopify_fee_source"] == "dynamic_region_rate"
    assert result["shopify_fee_rate"] == 0.07542
    assert result["shopify_fee_rate_region"] == "europe"
    assert result["shopify_fee_rate_window_start"] == "2026-05-30"
    assert result["shopify_fee_rate_window_end"] == "2026-06-05"
    assert result["cost_basis"]["shopify_fee_basis"]["line_allocation_ratio"] == 0.4
    assert result["cost_basis"]["shopify_fee_basis"]["snapshot_id"] == 9
```

- [x] **Step 4.2: Run the focused test and confirm failure**

Run:

```bash
pytest tests/test_profit_calculation.py::test_calculate_line_profit_uses_resolved_order_shopify_fee_with_allocation -q
```

Expected: FAIL because `calculate_line_profit` ignores `shopify_fee_result`.

- [x] **Step 4.3: Modify `calculate_line_profit` fee block**

In `appcore/order_analytics/profit_calculation.py`, keep existing strategy C behavior as fallback, but before calling `estimate_fee_for_buyer_country`, add:

```python
    fee_result = line.get("shopify_fee_result") or {}
    if fee_result:
        order_revenue = _to_decimal(line.get("order_total_revenue_usd"))
        allocation_base = order_revenue if order_revenue > 0 else revenue_usd
        allocation_ratio = Decimal("0") if allocation_base <= 0 else revenue_usd / allocation_base
        order_fee_usd = _to_decimal(fee_result.get("shopify_fee_usd"))
        shopify_fee_usd = _q4(order_fee_usd * allocation_ratio)
        presentment_currency = fee_result.get("presentment_currency")
        shopify_tier = fee_result.get("shopify_tier")
        shopify_fee_source = fee_result.get("shopify_fee_source")
        shopify_fee_rate = fee_result.get("shopify_fee_rate")
        shopify_fee_rate_region = fee_result.get("shopify_fee_rate_region")
        shopify_fee_rate_window_start = fee_result.get("shopify_fee_rate_window_start")
        shopify_fee_rate_window_end = fee_result.get("shopify_fee_rate_window_end")
        shopify_fee_basis = dict(fee_result.get("shopify_fee_basis") or {})
        shopify_fee_basis["line_allocation_ratio"] = float(
            allocation_ratio.quantize(Decimal("0.000001"))
        )
    else:
        fee = estimate_fee_for_buyer_country(revenue_usd, buyer_country)
        shopify_fee_usd = _q4(_to_decimal(fee.get("fee")))
        presentment_currency = fee.get("presentment_currency")
        shopify_tier = fee.get("tier")
        shopify_fee_source = "strategy_c_fallback"
        shopify_fee_rate = None
        shopify_fee_rate_region = None
        shopify_fee_rate_window_start = None
        shopify_fee_rate_window_end = None
        shopify_fee_basis = {
            "strategy_version": "strategy_c_legacy",
            "order_total_revenue_usd": float(revenue_usd),
            "order_fee_usd": float(shopify_fee_usd),
            "line_allocation_ratio": 1.0,
        }
```

Use the existing variable names in the file. If the existing code already has a fee block with different names, replace that block with this structure and keep later profit math unchanged.

- [x] **Step 4.4: Add trace fields to returned dict**

In the returned result dict from `calculate_line_profit`, add:

```python
        "shopify_fee_source": shopify_fee_source,
        "shopify_fee_rate": shopify_fee_rate,
        "shopify_fee_rate_region": shopify_fee_rate_region,
        "shopify_fee_rate_window_start": shopify_fee_rate_window_start,
        "shopify_fee_rate_window_end": shopify_fee_rate_window_end,
```

Inside `cost_basis`, add:

```python
            "shopify_fee_source": shopify_fee_source,
            "shopify_fee_basis": shopify_fee_basis,
```

- [x] **Step 4.5: Run profit calculation tests**

Run:

```bash
pytest tests/test_profit_calculation.py -q
```

Expected: PASS.

- [x] **Step 4.6: Commit**

```bash
git add appcore/order_analytics/profit_calculation.py tests/test_profit_calculation.py
git commit -m "feat: allocate resolved shopify fees in profit calculation" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 5: Persist Shopify Fee Trace Fields

**Files:**
- Modify: `appcore/order_analytics/profit_repository.py`
- Modify: `tests/test_profit_repository.py`

- [ ] **Step 5.1: Add repository test for new columns**

Append to `tests/test_profit_repository.py`:

```python
def test_upsert_profit_line_persists_shopify_fee_trace_columns(monkeypatch):
    executed = {}

    def fake_execute(sql, values):
        executed["sql"] = sql
        executed["values"] = values

    monkeypatch.setattr("appcore.order_analytics.profit_repository.execute", fake_execute)

    upsert_profit_line(
        {
            "dxm_order_line_id": "L-trace-1",
            "product_id": 1,
            "buyer_country": "DE",
            "presentment_currency": "EUR",
            "shopify_tier": "dynamic_region_rate",
            "line_amount_usd": 40.0,
            "shipping_allocated_usd": 0.0,
            "revenue_usd": 40.0,
            "shopify_fee_usd": 2.688,
            "ad_cost_usd": 0.0,
            "purchase_usd": 10.0,
            "shipping_cost_usd": 0.0,
            "return_reserve_usd": 0.4,
            "profit_usd": 26.912,
            "status": "ok",
            "missing_fields": [],
            "cost_basis": {"shopify_fee_basis": {"snapshot_id": 9}},
            "shopify_fee_source": "dynamic_region_rate",
            "shopify_fee_rate": 0.07542,
            "shopify_fee_rate_region": "europe",
            "shopify_fee_rate_window_start": "2026-05-30",
            "shopify_fee_rate_window_end": "2026-06-05",
        },
        business_date=date(2026, 6, 13),
        paid_at=None,
        source_run_id=88,
    )

    sql = executed["sql"].lower()
    assert "shopify_fee_source" in sql
    assert "shopify_fee_basis_json" in sql
    assert "dynamic_region_rate" in executed["values"]
```

- [ ] **Step 5.2: Run repository test and confirm failure**

Run:

```bash
pytest tests/test_profit_repository.py::test_upsert_profit_line_persists_shopify_fee_trace_columns -q
```

Expected: FAIL because `_PROFIT_LINE_COLUMNS` does not include the new columns.

- [ ] **Step 5.3: Extend `_PROFIT_LINE_COLUMNS`**

In `appcore/order_analytics/profit_repository.py`, append these columns after `source_run_id`:

```python
    "shopify_fee_source",
    "shopify_fee_rate",
    "shopify_fee_rate_region",
    "shopify_fee_rate_window_start",
    "shopify_fee_rate_window_end",
    "shopify_fee_basis_json",
```

- [ ] **Step 5.4: Extend `values`**

In `upsert_profit_line`, append:

```python
        line_result.get("shopify_fee_source"),
        line_result.get("shopify_fee_rate"),
        line_result.get("shopify_fee_rate_region"),
        line_result.get("shopify_fee_rate_window_start"),
        line_result.get("shopify_fee_rate_window_end"),
        json.dumps(
            (line_result.get("cost_basis") or {}).get("shopify_fee_basis") or {},
            ensure_ascii=False,
            default=str,
        ),
```

- [ ] **Step 5.5: Run repository tests**

Run:

```bash
pytest tests/test_profit_repository.py -q
```

Expected: PASS.

- [ ] **Step 5.6: Commit**

```bash
git add appcore/order_analytics/profit_repository.py tests/test_profit_repository.py
git commit -m "feat: persist shopify fee trace fields" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 6: Integrate Resolver Into Profit Backfill Without Rewriting History

**Files:**
- Modify: `tools/order_profit_backfill.py`
- Create: `tests/test_order_profit_backfill_dynamic_fee.py`

- [ ] **Step 6.1: Add focused tests for effective boundary and resolver call**

Create `tests/test_order_profit_backfill_dynamic_fee.py`:

```python
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from tools import order_profit_backfill as backfill


def test_should_skip_line_before_dynamic_effective_at(monkeypatch):
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-06-13T09:00:00+08:00")

    assert backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 6, 12, 23, 59, 59)}
    )
    assert not backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 6, 13, 1, 0, 0)}
    )


def test_process_line_passes_resolved_fee_to_profit_calculation(monkeypatch):
    captured = {}

    def fake_calculate_line_profit(line_input, **kwargs):
        captured["line_input"] = line_input
        return {"status": "ok", "dxm_order_line_id": line_input["dxm_order_line_id"]}

    monkeypatch.setattr(backfill, "calculate_line_profit", fake_calculate_line_profit)

    fee_result = {
        "shopify_fee_usd": 6.72,
        "shopify_tier": "dynamic_region_rate",
        "presentment_currency": "EUR",
        "shopify_fee_source": "dynamic_region_rate",
        "shopify_fee_rate": 0.07542,
        "shopify_fee_rate_region": "europe",
        "shopify_fee_basis": {"snapshot_id": 9},
    }

    result = backfill._process_line(
        {
            "dxm_order_line_id": "L1",
            "dxm_package_id": "P1",
            "product_id": 1,
            "buyer_country": "DE",
            "line_amount": 40.0,
            "quantity": 1,
            "purchase_price": 70.0,
            "ship_amount": 0.0,
            "site_code": "newjoy",
            "extended_order_id": "#3001",
            "package_number": "3001",
            "order_paid_at": datetime(2026, 6, 13, 10, 0, 0),
        },
        order_total_amount=100.0,
        order_shipping=0.0,
        sku_units_cache={},
        sku_spend_cache={},
        rmb_per_usd=Decimal("7.0"),
        return_reserve_rate=Decimal("0.01"),
        exchange_rate_basis=None,
        shopify_fee_result=fee_result,
    )

    assert result["status"] == "ok"
    assert captured["line_input"]["shopify_fee_result"] is fee_result
    assert captured["line_input"]["order_total_revenue_usd"] == 100.0
```

- [ ] **Step 6.2: Run tests and confirm failure**

Run:

```bash
pytest tests/test_order_profit_backfill_dynamic_fee.py -q
```

Expected: FAIL because `_should_skip_for_dynamic_fee_boundary` and `_process_line(..., shopify_fee_result=...)` do not exist.

- [ ] **Step 6.3: Add imports and boundary helper**

In `tools/order_profit_backfill.py`, add:

```python
from appcore.order_analytics.shopify_fee_resolver import (
    is_dynamic_fee_effective,
    resolve_shopify_fee_for_order,
)
```

Add helper near `_resolve_business_date`:

```python
def _resolve_order_time(line: dict) -> datetime | None:
    return (
        line.get("order_paid_at")
        or line.get("attribution_time_at")
        or line.get("order_created_at")
    )


def _should_skip_for_dynamic_fee_boundary(line: dict) -> bool:
    order_time = _resolve_order_time(line)
    return not is_dynamic_fee_effective(order_time)
```

- [ ] **Step 6.4: Extend `_LINE_QUERY` selected fields**

Add these selected columns to `_LINE_QUERY`:

```sql
        o.site_code,
        o.extended_order_id,
        o.package_number,
        o.attribution_time_at,
        o.order_created_at,
```

If the local order table uses different aliases, inspect the existing query and choose the table alias that already provides `buyer_country`, `order_paid_at`, and package fields. Keep these output key names exactly.

- [ ] **Step 6.5: Accept and forward `shopify_fee_result` in `_process_line`**

Change `_process_line` signature:

```python
    exchange_rate_basis: dict[str, Any] | None = None,
    shopify_fee_result: dict[str, Any] | None = None,
) -> dict:
```

Add to the `line_input` dict passed to `calculate_line_profit`:

```python
        "site_code": line.get("site_code"),
        "extended_order_id": line.get("extended_order_id"),
        "package_number": line.get("package_number"),
        "order_total_revenue_usd": order_total_amount,
        "shopify_fee_result": shopify_fee_result,
```

- [ ] **Step 6.6: Resolve once per package in `backfill`**

Inside the existing loop that processes grouped lines, create a cache before iterating lines:

```python
        fee_result_cache: dict[str, dict[str, Any]] = {}
```

Before calling `_process_line`, add:

```python
            if _should_skip_for_dynamic_fee_boundary(line):
                skipped += 1
                continue

            package_id = str(line.get("dxm_package_id") or "")
            if package_id not in fee_result_cache:
                fee_result_cache[package_id] = resolve_shopify_fee_for_order(
                    amount=order_total_amounts.get(package_id, 0.0) + order_shipping.get(package_id, 0.0),
                    buyer_country=line.get("buyer_country"),
                    site_code=line.get("site_code"),
                    order_names=[
                        line.get("extended_order_id"),
                        line.get("package_number"),
                        package_id,
                    ],
                    order_time=_resolve_order_time(line),
                )
            result = _process_line(
                line,
                order_total_amount=order_total_amounts.get(package_id, 0.0),
                order_shipping=order_shipping.get(package_id, 0.0),
                sku_units_cache=sku_units_cache,
                sku_spend_cache=sku_spend_cache,
                rmb_per_usd=rmb_per_usd,
                return_reserve_rate=return_reserve_rate,
                exchange_rate_basis=exchange_rate_basis,
                shopify_fee_result=fee_result_cache[package_id],
            )
```

Use the existing local variable names for `skipped`, `order_total_amounts`, and `order_shipping`. If the current function uses different names, keep the existing counters and add a new `legacy_fee_boundary_skipped` key to the run summary.

- [ ] **Step 6.7: Record source counts in run summary**

Maintain:

```python
        fee_source_counts: dict[str, int] = defaultdict(int)
```

After resolving a fee result:

```python
                source = fee_result_cache[package_id].get("shopify_fee_source") or "unknown"
                fee_source_counts[source] += 1
```

In the `finish_profit_run(... summary_json=...)` payload, include:

```python
            "shopify_fee_source_counts": dict(fee_source_counts),
            "legacy_fee_boundary_skipped": skipped,
```

- [ ] **Step 6.8: Run backfill tests**

Run:

```bash
pytest tests/test_order_profit_backfill_dynamic_fee.py tests/test_profit_calculation.py tests/test_profit_repository.py -q
```

Expected: PASS.

- [ ] **Step 6.9: Commit**

```bash
git add tools/order_profit_backfill.py tests/test_order_profit_backfill_dynamic_fee.py
git commit -m "feat: apply dynamic shopify fees in profit backfill" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 7: Refresh Fee Snapshots After Payments CSV Import

**Files:**
- Modify: `appcore/order_analytics/shopify_fee_dynamic.py`
- Modify: `appcore/order_analytics/shopify_payments_import.py`
- Modify: `tests/test_shopify_payments_import.py`
- Modify: `tests/test_shopify_fee_dynamic.py`

- [ ] **Step 7.1: Add snapshot refresh unit test**

Append to `tests/test_shopify_fee_dynamic.py`:

```python
from appcore.order_analytics.shopify_fee_dynamic import refresh_fee_rate_snapshots


def test_refresh_fee_rate_snapshots_groups_by_store_and_region(monkeypatch):
    saved_rows = []

    def fake_query(sql, params=None):
        if "max(transaction_date)" in sql.lower():
            return [{"max_date": date(2026, 6, 6)}]
        if "date_sub" in sql.lower() and params and params[1] == 6:
            return [
                {
                    "store_code": "newjoy",
                    "region": "europe",
                    "orders_count": 3290,
                    "amount_usd": 88165.45,
                    "fee_usd": 6649.36,
                }
            ]
        return [
            {
                "store_code": "newjoy",
                "region": "europe",
                "orders_count": 9000,
                "amount_usd": 240000.0,
                "fee_usd": 18000.0,
            }
        ]

    def fake_save(rows):
        saved_rows.extend(rows)
        return len(rows)

    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.query", fake_query)
    monkeypatch.setattr("appcore.order_analytics.shopify_fee_dynamic.save_fee_rate_snapshots", fake_save)

    result = refresh_fee_rate_snapshots(source_csvs=["newjoyloo__newjoyloo0606.csv"])

    assert result["saved"] == 1
    assert saved_rows[0]["store_code"] == "newjoy"
    assert saved_rows[0]["region"] == "europe"
    assert saved_rows[0]["sample_status"] == SAMPLE_STATUS_OK_7D
```

- [ ] **Step 7.2: Add import trigger test**

Append to `tests/test_shopify_payments_import.py`:

```python
def test_import_payments_csv_refreshes_fee_rate_snapshots(monkeypatch):
    refreshed = []

    def fake_refresh(source_csvs=None):
        refreshed.append(source_csvs)
        return {"saved": 3}

    monkeypatch.setattr(
        "appcore.order_analytics.shopify_payments_import.refresh_fee_rate_snapshots",
        fake_refresh,
    )

    csv_data = "\n".join(
        [
            "Transaction Date,Type,Order,Amount,Fee,Net,Presentment Currency",
            "2026-06-06,charge,#1001,10.00,-0.80,9.20,USD",
        ]
    )

    result = import_payments_csv(io.StringIO(csv_data), source_csv="newjoyloo__newjoyloo0606.csv")

    assert result["inserted"] == 1
    assert refreshed == [["newjoyloo__newjoyloo0606.csv"]]
```

If `tests/test_shopify_payments_import.py` does not already import `io`, add `import io`.

- [ ] **Step 7.3: Run tests and confirm failure**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py::test_refresh_fee_rate_snapshots_groups_by_store_and_region tests/test_shopify_payments_import.py::test_import_payments_csv_refreshes_fee_rate_snapshots -q
```

Expected: FAIL because `refresh_fee_rate_snapshots` is missing and import does not call it.

- [ ] **Step 7.4: Implement aggregate refresh**

Append to `appcore/order_analytics/shopify_fee_dynamic.py`:

```python
def _load_max_transaction_date(source_csvs: list[str]) -> date | None:
    source_filter = ""
    params: list[Any] = []
    if source_csvs:
        placeholders = ", ".join(["%s"] * len(source_csvs))
        source_filter = f"AND source_csv IN ({placeholders})"
        params.extend(source_csvs)
    rows = query(
        f"""
        SELECT MAX(DATE(transaction_date)) AS max_date
        FROM shopify_payments_transactions
        WHERE type = 'charge'
          {source_filter}
        """,
        tuple(params),
    )
    return rows[0].get("max_date") if rows else None


def _load_window_aggregates(
    *,
    window_end_date: date,
    window_days: int,
    source_csvs: list[str],
) -> list[dict[str, Any]]:
    source_filter = ""
    params: list[Any] = [window_end_date, window_days - 1, window_end_date]
    if source_csvs:
        placeholders = ", ".join(["%s"] * len(source_csvs))
        source_filter = f"AND source_csv IN ({placeholders})"
        params.extend(source_csvs)
    return query(
        f"""
        SELECT
            CASE
                WHEN LOWER(source_csv) LIKE 'newjoyloo__%%' THEN 'newjoy'
                WHEN LOWER(source_csv) LIKE 'omurio__%%' THEN 'omurio'
                ELSE 'all'
            END AS store_code,
            CASE
                WHEN UPPER(presentment_currency) = 'USD' THEN 'us'
                WHEN UPPER(presentment_currency) IN ('EUR','GBP','CHF','SEK','NOK','DKK','PLN','CZK','HUF','RON','BGN') THEN 'europe'
                ELSE 'other'
            END AS region,
            COUNT(DISTINCT order_name) AS orders_count,
            SUM(ABS(amount_usd)) AS amount_usd,
            SUM(ABS(fee_usd)) AS fee_usd
        FROM shopify_payments_transactions
        WHERE type = 'charge'
          AND DATE(transaction_date) BETWEEN DATE_SUB(%s, INTERVAL %s DAY) AND %s
          {source_filter}
        GROUP BY store_code, region
        """,
        tuple(params),
    )


def refresh_fee_rate_snapshots(source_csvs: Iterable[str] | None = None) -> dict[str, Any]:
    source_list = [str(item) for item in (source_csvs or []) if str(item or "").strip()]
    window_end = _load_max_transaction_date(source_list)
    if window_end is None:
        return {"saved": 0, "reason": "no_charge_transactions"}

    seven_rows = {
        (row["store_code"], row["region"]): row
        for row in _load_window_aggregates(
            window_end_date=window_end,
            window_days=7,
            source_csvs=source_list,
        )
    }
    thirty_rows = {
        (row["store_code"], row["region"]): row
        for row in _load_window_aggregates(
            window_end_date=window_end,
            window_days=30,
            source_csvs=source_list,
        )
    }
    keys = sorted(set(seven_rows) | set(thirty_rows))
    rows: list[dict[str, Any]] = []
    for store_code, region in keys:
        selected = select_snapshot_window(
            seven_day=seven_rows.get((store_code, region), {}),
            thirty_day=thirty_rows.get((store_code, region), {}),
        )
        rows.append(
            build_snapshot_row(
                store_code=store_code,
                region=region,
                window_start_date=window_end - timedelta(days=selected["window_days"] - 1),
                window_end_date=window_end,
                window_days=selected["window_days"],
                orders_count=selected.get("orders_count") or 0,
                amount_usd=selected.get("amount_usd") or 0,
                fee_usd=selected.get("fee_usd") or 0,
                source_csvs=source_list,
                sample_status=selected["sample_status"],
            )
        )
    saved = save_fee_rate_snapshots(rows)
    return {"saved": saved, "window_end_date": window_end, "source_csvs": source_list}
```

Also update the module import:

```python
from datetime import date, timedelta
```

- [ ] **Step 7.5: Save transaction date and call refresh after import**

In `appcore/order_analytics/shopify_payments_import.py`, add:

```python
from .shopify_fee_dynamic import refresh_fee_rate_snapshots
```

In `parse_payments_csv`, add this field to `row`:

```python
            "transaction_date": norm.get("Transaction Date"),
```

In the `INSERT INTO shopify_payments_transactions (...)` column list, add `transaction_date` after `transaction_id`:

```python
        "  transaction_id, transaction_date, payout_id, type, order_name, presentment_currency, "
```

Add the extra placeholder in the values list and update the duplicate assignment:

```python
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  transaction_date=VALUES(transaction_date), payout_id=VALUES(payout_id), type=VALUES(type), "
```

Pass the value in `execute(...)`:

```python
            row["transaction_id"], row["transaction_date"], row["payout_id"], row["type"],
```

After rows have been inserted and before returning the import result, add:

```python
    snapshot_result = refresh_fee_rate_snapshots(source_csvs=[source_csv] if source_csv else None)
    result["fee_rate_snapshots"] = snapshot_result
```

Use the existing result variable name. If the function returns a dict literal directly, convert it to a local `result` dict first.

- [ ] **Step 7.6: Run import and dynamic tests**

Run:

```bash
pytest tests/test_shopify_fee_dynamic.py tests/test_shopify_payments_import.py -q
```

Expected: PASS.

- [ ] **Step 7.7: Commit**

```bash
git add appcore/order_analytics/shopify_fee_dynamic.py appcore/order_analytics/shopify_payments_import.py tests/test_shopify_fee_dynamic.py tests/test_shopify_payments_import.py
git commit -m "feat: refresh dynamic fee rates after payments import" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 8: Use Resolver For Realtime Rows Without Stored Profit Lines

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Modify: `tests/test_order_analytics_realtime_profit_details.py`

- [ ] **Step 8.1: Add realtime fallback test**

Append to `tests/test_order_analytics_realtime_profit_details.py`:

```python
def test_format_realtime_order_profit_rows_uses_dynamic_resolver_for_uncomputed_order(monkeypatch):
    resolved_calls = []

    def fake_resolver(**kwargs):
        resolved_calls.append(kwargs)
        return {
            "shopify_fee_usd": 6.72,
            "shopify_tier": "dynamic_region_rate",
            "presentment_currency": "EUR",
            "shopify_fee_source": "dynamic_region_rate",
            "shopify_fee_rate": 0.07542,
            "shopify_fee_rate_region": "europe",
            "shopify_fee_rate_window_start": "2026-05-30",
            "shopify_fee_rate_window_end": "2026-06-05",
            "shopify_fee_basis": {"snapshot_id": 9},
        }

    monkeypatch.setattr(
        "appcore.order_analytics.realtime.resolve_shopify_fee_for_order",
        fake_resolver,
    )

    rows = _format_realtime_order_profit_rows(
        [
            {
                "dxm_package_id": "P1",
                "package_number": "3001",
                "extended_order_id": "#3001",
                "site_code": "newjoy",
                "buyer_country": "DE",
                "order_paid_at": datetime(2026, 6, 13, 10, 0, 0),
                "total_revenue": 100.0,
                "profit_line_count": 0,
                "stored_shopify_fee_total": None,
                "purchase_cost_usd": 0.0,
                "purchase_estimate_usd": 0.0,
                "logistics_cost_usd": 0.0,
                "logistics_estimate_usd": 0.0,
                "ad_cost_usd": 0.0,
                "return_reserve_usd": 1.0,
                "refund_deduction_usd": 0.0,
                "profit_deduction_usd": 0.0,
            }
        ],
        day_start=datetime(2026, 6, 13),
    )

    assert rows[0]["shopify_fee_total_usd"] == 6.72
    assert rows[0]["shopify_fee_source"] == "dynamic_region_rate"
    assert rows[0]["shopify_fee_rate_region"] == "europe"
    assert resolved_calls[0]["site_code"] == "newjoy"
```

If this test file uses a row factory, put these fields in that factory instead of duplicating a large dict.

- [ ] **Step 8.2: Add source summary test**

Append:

```python
def test_build_order_profit_summary_counts_shopify_fee_sources():
    summary = _build_order_profit_summary(
        [
            {
                "total_revenue": 100.0,
                "refund_deduction_usd": 0.0,
                "return_reserve_usd": 1.0,
                "profit_deduction_usd": 0.0,
                "purchase_cost_usd": 20.0,
                "purchase_estimate_usd": 0.0,
                "logistics_cost_usd": 5.0,
                "logistics_estimate_usd": 0.0,
                "shopify_fee_total_usd": 6.72,
                "shopify_fee_source": "dynamic_region_rate",
                "shopify_fee_rate_window_end": "2026-06-05",
                "ad_cost_usd": 10.0,
                "purchase_cost_missing": False,
                "logistics_cost_missing": False,
            },
            {
                "total_revenue": 50.0,
                "refund_deduction_usd": 0.0,
                "return_reserve_usd": 0.5,
                "profit_deduction_usd": 0.0,
                "purchase_cost_usd": 10.0,
                "purchase_estimate_usd": 0.0,
                "logistics_cost_usd": 2.0,
                "logistics_estimate_usd": 0.0,
                "shopify_fee_total_usd": 1.95,
                "shopify_fee_source": "actual_payment",
                "shopify_fee_rate_window_end": None,
                "ad_cost_usd": 5.0,
                "purchase_cost_missing": False,
                "logistics_cost_missing": False,
            },
        ],
        total_ad_spend_usd=15.0,
    )

    assert summary["shopify_fee_source_counts"] == {
        "dynamic_region_rate": 1,
        "actual_payment": 1,
    }
    assert summary["shopify_fee_source_amounts"]["dynamic_region_rate"] == 6.72
    assert summary["shopify_fee_rate_watermark"] == "2026-06-05"
```

- [ ] **Step 8.3: Run realtime tests and confirm failure**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py -q
```

Expected: FAIL because realtime still calls `split_shopify_fee_for_order` for uncomputed rows and summary source fields do not exist.

- [ ] **Step 8.4: Import resolver in realtime module**

In `appcore/order_analytics/realtime.py`, add:

```python
from .shopify_fee_resolver import resolve_shopify_fee_for_order
```

- [ ] **Step 8.5: Select stored trace fields in realtime SQL**

In `_get_realtime_order_profit_details` and range variant if present, add aggregate columns:

```sql
        GROUP_CONCAT(DISTINCT p.shopify_fee_source ORDER BY p.shopify_fee_source) AS stored_shopify_fee_sources,
        MAX(p.shopify_fee_rate_region) AS stored_shopify_fee_rate_region,
        MAX(p.shopify_fee_rate_window_start) AS stored_shopify_fee_rate_window_start,
        MAX(p.shopify_fee_rate_window_end) AS stored_shopify_fee_rate_window_end,
```

Keep the existing `SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total` priority.

- [ ] **Step 8.6: Use resolver for uncomputed rows**

In `_format_realtime_order_profit_rows`, replace the uncomputed-order `split_shopify_fee_for_order(...)` total with:

```python
        if profit_line_count > 0:
            shopify_fee_total = float(row.get("stored_shopify_fee_total") or 0.0)
            shopify_fee_source = row.get("stored_shopify_fee_sources") or "stored_profit_line"
            shopify_fee_rate_region = row.get("stored_shopify_fee_rate_region")
            shopify_fee_rate_window_start = row.get("stored_shopify_fee_rate_window_start")
            shopify_fee_rate_window_end = row.get("stored_shopify_fee_rate_window_end")
        else:
            fee_result = resolve_shopify_fee_for_order(
                amount=total_revenue,
                buyer_country=row.get("buyer_country"),
                site_code=row.get("site_code"),
                order_names=[
                    row.get("extended_order_id"),
                    row.get("package_number"),
                    row.get("dxm_package_id"),
                ],
                order_time=row.get("order_paid_at") or row.get("attribution_time_at") or row.get("order_created_at"),
            )
            shopify_fee_total = float(fee_result.get("shopify_fee_usd") or 0.0)
            shopify_fee_source = fee_result.get("shopify_fee_source")
            shopify_fee_rate_region = fee_result.get("shopify_fee_rate_region")
            shopify_fee_rate_window_start = fee_result.get("shopify_fee_rate_window_start")
            shopify_fee_rate_window_end = fee_result.get("shopify_fee_rate_window_end")
```

When appending the formatted row, include:

```python
            "shopify_fee_source": shopify_fee_source,
            "shopify_fee_rate_region": shopify_fee_rate_region,
            "shopify_fee_rate_window_start": shopify_fee_rate_window_start,
            "shopify_fee_rate_window_end": shopify_fee_rate_window_end,
```

Keep any component split fields that existing UI depends on. For dynamic or actual totals, component fields may use the old split proportions for display only, but `shopify_fee_total_usd` must be the resolver or stored total.

- [ ] **Step 8.7: Add summary source counts**

In `_empty_order_profit_summary`, add:

```python
        "shopify_fee_source_counts": {},
        "shopify_fee_source_amounts": {},
        "shopify_fee_rate_watermark": None,
```

In `_build_order_profit_summary`, initialize before the row loop:

```python
    fee_source_counts: dict[str, int] = {}
    fee_source_amounts: dict[str, float] = {}
    fee_rate_watermark = None
```

Inside the row loop:

```python
        fee_source = row.get("shopify_fee_source") or "unknown"
        fee_source_counts[fee_source] = fee_source_counts.get(fee_source, 0) + 1
        fee_source_amounts[fee_source] = fee_source_amounts.get(fee_source, 0.0) + float(row.get("shopify_fee_total_usd") or 0.0)
        watermark = row.get("shopify_fee_rate_window_end")
        if watermark and (fee_rate_watermark is None or str(watermark) > str(fee_rate_watermark)):
            fee_rate_watermark = str(watermark)
```

Before returning:

```python
    summary["shopify_fee_source_counts"] = fee_source_counts
    summary["shopify_fee_source_amounts"] = {
        key: round(value, 2) for key, value in fee_source_amounts.items()
    }
    summary["shopify_fee_rate_watermark"] = fee_rate_watermark
```

- [ ] **Step 8.8: Run realtime focused tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py tests/test_order_profit_aggregation.py -q
```

Expected: PASS.

- [ ] **Step 8.9: Commit**

```bash
git add appcore/order_analytics/realtime.py tests/test_order_analytics_realtime_profit_details.py
git commit -m "feat: use dynamic shopify fee estimates in realtime dashboard" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 9: Add Data Quality Warnings For Fee Fallbacks

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Modify: `tests/test_order_analytics_realtime_profit_details.py`

- [ ] **Step 9.1: Add data quality warning test**

Append:

```python
def test_order_profit_summary_marks_strategy_c_fallback_warning():
    summary = _build_order_profit_summary(
        [
            {
                "total_revenue": 20.0,
                "refund_deduction_usd": 0.0,
                "return_reserve_usd": 0.2,
                "profit_deduction_usd": 0.0,
                "purchase_cost_usd": 5.0,
                "purchase_estimate_usd": 0.0,
                "logistics_cost_usd": 1.0,
                "logistics_estimate_usd": 0.0,
                "shopify_fee_total_usd": 1.0,
                "shopify_fee_source": "strategy_c_fallback",
                "shopify_fee_rate_window_end": None,
                "ad_cost_usd": 2.0,
                "purchase_cost_missing": False,
                "logistics_cost_missing": False,
            }
        ],
        total_ad_spend_usd=2.0,
    )

    warnings = summary["data_quality"]["warnings"]
    assert any("strategy_c_fallback" in warning for warning in warnings)
```

If `_build_order_profit_summary` currently does not own `data_quality`, put this assertion on the route payload helper that wraps the summary. Keep the warning text:

```text
shopify_fee_strategy_c_fallback_present
```

- [ ] **Step 9.2: Run warning test and confirm failure**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py -k strategy_c_fallback_warning -q
```

Expected: FAIL because the warning is absent.

- [ ] **Step 9.3: Add warning generation**

Where realtime summary data quality is assembled, add:

```python
    if summary.get("shopify_fee_source_counts", {}).get("strategy_c_fallback"):
        data_quality.setdefault("warnings", []).append(
            "shopify_fee_strategy_c_fallback_present"
        )
```

If the module has a centralized `build_order_analytics_payload_response` call, pass this warning through the existing `data_quality` object instead of creating a separate shape.

- [ ] **Step 9.4: Run realtime tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_data_quality.py -q
```

Expected: PASS.

- [ ] **Step 9.5: Commit**

```bash
git add appcore/order_analytics/realtime.py tests/test_order_analytics_realtime_profit_details.py
git commit -m "feat: report shopify fee fallback quality warnings" -m "Docs-anchor: docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md"
```

---

### Task 10: Focused Verification And Smoke Checks

**Files:**
- Modify only if a verification failure exposes a real defect in files touched above.

- [ ] **Step 10.1: Run repository targeted pytest helper**

Run:

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

Expected: related tests run and pass. If the script reports no direct targets, run Step 10.2.

- [ ] **Step 10.2: Run minimum focused suite**

Run:

```bash
pytest tests/test_shopify_fee.py \
       tests/test_shopify_fee_buyer_country_integration.py \
       tests/test_shopify_fee_dynamic.py \
       tests/test_profit_calculation.py \
       tests/test_profit_repository.py \
       tests/test_shopify_payments_import.py \
       tests/test_order_profit_backfill_dynamic_fee.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_profit_aggregation.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_dynamic_shopify_fee_migration.py -q
```

Expected: PASS.

- [ ] **Step 10.3: Import smoke**

Run:

```bash
python3 - <<'PY'
from appcore.order_analytics.shopify_fee_resolver import resolve_shopify_fee_for_order
from appcore.order_analytics.shopify_fee_dynamic import region_for_presentment_currency
from tools.order_profit_backfill import _should_skip_for_dynamic_fee_boundary

assert region_for_presentment_currency("USD") == "us"
assert callable(resolve_shopify_fee_for_order)
assert callable(_should_skip_for_dynamic_fee_boundary)
print("dynamic-shopify-fee imports ok")
PY
```

Expected:

```text
dynamic-shopify-fee imports ok
```

- [ ] **Step 10.4: Dev server route smoke**

Start a local dev server on an unused port:

```bash
PORT=5099 python -m web.app
```

In another shell:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5099/order-analytics/realtime-overview
```

Expected: `302` when unauthenticated, not `500`.

- [ ] **Step 10.5: Final git status**

Run:

```bash
git status --short
```

Expected: only intentional changes are present. `paseo.json` may be unrelated and should not be committed unless it was already part of the task.

---

## Self-Review Checklist

- [ ] Spec section 5 is covered by Task 3 and Task 6: `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` prevents historical overwrite.
- [ ] Spec section 6 is covered by Task 2: USD, Europe currencies, and other region mapping.
- [ ] Spec section 7 is covered by Task 1, Task 2, and Task 7: `shopify_fee_rate_snapshots` plus 7d/30d sample status.
- [ ] Spec section 8 is covered by Task 3: `actual_payment` > `dynamic_region_rate` > `strategy_c_fallback`.
- [ ] Spec section 9 is covered by Task 4 and Task 5: trace fields are calculated and persisted.
- [ ] Spec section 10 is covered by Task 8 and Task 9: realtime uses resolver and exposes source summaries.
- [ ] Spec section 11 is covered by Task 6 and Task 7: import refreshes snapshots, backfill skips legacy orders.
- [ ] Spec section 12 is covered by Task 8 and Task 9: fallback counts and warnings are available to the API payload.
- [ ] Full historical recalculation is absent from this plan by design.
