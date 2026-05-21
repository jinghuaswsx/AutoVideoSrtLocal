# Manual Daily Ad Spend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "人工录入" sub-tab under 广告分析 where admins can enter daily total ad spend per ad account when Meta sync fails, fed into "总利润" KPI as an unallocated supplement.

**Architecture:** New `meta_ad_manual_daily_spend` table + DAO. `order_profit_aggregation` adds a per-`(date, ad_account_id)` supplement: when `sync_sum == 0`, manual value bumps `unallocated`. Three CRUD JSON routes under `web/routes/order_analytics.py`; UI is a fifth sub-tab in the existing 广告分析 panel with a list table + modal editor.

**Tech Stack:** Python 3.12 / Flask blueprint / pymysql / vanilla JS / pytest. Time zone `Asia/Shanghai` (`zoneinfo.ZoneInfo`).

**Spec:** [docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md](../specs/2026-05-09-manual-daily-ad-spend-design.md)

---

## File Structure

| Path | Action | Responsibility |
|------|--------|----------------|
| `db/migrations/2026_05_09_meta_ad_manual_daily_spend.sql` | create | Schema for new table |
| `appcore/order_analytics/manual_ad_spend.py` | create | DAO: upsert / list / delete / load_supplement_map |
| `appcore/order_analytics/order_profit_aggregation.py` | modify | Add manual supplement to unallocated bucket |
| `web/routes/order_analytics.py` | modify | 3 new endpoints (GET list, POST upsert, DELETE entry) |
| `web/templates/order_analytics.html` | modify | Add 5th sub-tab + table + modal |
| `tests/test_manual_ad_spend.py` | create | DAO + routes tests |
| `tests/test_order_profit_aggregation.py` | modify | Add 4 cases for supplement behavior |
| `CLAUDE.md` | modify | Add cognitive doc for the fallback rule |
| `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md` | modify | Append related-link to new spec |

Each file has one focused responsibility. DAO does pure SQL, route does HTTP + validation + audit, aggregation reads supplement map and adds to unallocated, template + JS does UI.

---

## Task 1: Migration — create `meta_ad_manual_daily_spend` table

**Files:**
- Create: `db/migrations/2026_05_09_meta_ad_manual_daily_spend.sql`

- [ ] **Step 1: Write the migration SQL**

Create `db/migrations/2026_05_09_meta_ad_manual_daily_spend.sql`:

```sql
-- Meta 广告费人工录入兜底表（admin 在「广告分析 → 人工录入」sub-tab 录入）。
--
-- 详细设计：docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md
--
-- 兜底语义：当某 (business_date, ad_account_id) 的 sync ad spend sum == 0 时，
-- 把这里的 spend_usd 加到 order_profit_aggregation 的 `unallocated` 桶，
-- 让"总利润"KPI 兜底不虚高。任何 sync > 0 时本表数据完全不参与计算。

CREATE TABLE IF NOT EXISTS meta_ad_manual_daily_spend (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  business_date DATE          NOT NULL,
  account_code  VARCHAR(64)   NOT NULL,
  ad_account_id VARCHAR(32)   NOT NULL,
  spend_usd     DECIMAL(14,4) NOT NULL,
  updated_by    INT NULL,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_date_account (business_date, account_code),
  KEY idx_date (business_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: Verify migration parses**

Run:
```bash
python -c "
from pathlib import Path
sql = Path('db/migrations/2026_05_09_meta_ad_manual_daily_spend.sql').read_text(encoding='utf-8')
import re
stmts = [s.strip() for s in re.split(r';\s*(?:\n|$)', sql, flags=re.MULTILINE) if s.strip() and not s.strip().startswith('--')]
print(f'parsed {len(stmts)} statement(s)')
assert len(stmts) >= 1, 'expected at least one CREATE TABLE statement'
print('OK')
"
```

Expected:
```
parsed 1 statement(s)
OK
```

- [ ] **Step 3: Commit**

```bash
git add db/migrations/2026_05_09_meta_ad_manual_daily_spend.sql
git commit -m "feat(order-analytics): add meta_ad_manual_daily_spend migration

Docs-anchor: docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md#数据模型"
```

---

## Task 2: DAO — `upsert_entries` happy path

**Files:**
- Create: `appcore/order_analytics/manual_ad_spend.py`
- Create: `tests/test_manual_ad_spend.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_manual_ad_spend.py`:

```python
"""Tests for appcore.order_analytics.manual_ad_spend DAO + routes.

详细设计：docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from appcore.db import get_conn
from appcore.order_analytics import manual_ad_spend


@pytest.fixture(autouse=True)
def _clean_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM meta_ad_manual_daily_spend")
        conn.commit()
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM meta_ad_manual_daily_spend")
        conn.commit()


def test_upsert_entries_inserts_new_rows():
    written = manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300.00"},
            {"account_code": "Omurio",    "ad_account_id": "1253003326160754", "spend_usd": "200.50"},
        ],
        updated_by=7,
    )
    assert written == 2

    rows = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))
    by_code = {r["account_code"]: r for r in rows}
    assert set(by_code) == {"newjoyloo", "Omurio"}
    assert by_code["newjoyloo"]["spend_usd"] == Decimal("300.0000")
    assert by_code["Omurio"]["spend_usd"] == Decimal("200.5000")
    assert by_code["newjoyloo"]["updated_by"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manual_ad_spend.py::test_upsert_entries_inserts_new_rows -v`
Expected: FAIL — `ModuleNotFoundError` or `AttributeError` on `manual_ad_spend.upsert_entries`.

- [ ] **Step 3: Write minimal DAO**

Create `appcore/order_analytics/manual_ad_spend.py`:

```python
"""Meta 广告费人工录入兜底 DAO。

详细设计：docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping

from appcore.db import get_conn

TABLE = "meta_ad_manual_daily_spend"


def upsert_entries(
    *,
    business_date: date,
    entries: Iterable[Mapping[str, object]],
    updated_by: int | None,
) -> int:
    """批量 upsert 同一天多个账户的人工录入。返回受影响行数（含 update）。

    每个 entry: {"account_code": str, "ad_account_id": str, "spend_usd": Decimal|str|float}
    """
    payload = []
    for entry in entries:
        account_code = str(entry["account_code"]).strip()
        ad_account_id = str(entry["ad_account_id"]).strip()
        spend = Decimal(str(entry["spend_usd"]))
        payload.append((business_date, account_code, ad_account_id, spend, updated_by))
    if not payload:
        return 0

    sql = f"""
        INSERT INTO {TABLE} (business_date, account_code, ad_account_id, spend_usd, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          ad_account_id = VALUES(ad_account_id),
          spend_usd     = VALUES(spend_usd),
          updated_by    = VALUES(updated_by)
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.executemany(sql, payload)
        conn.commit()
        return cur.rowcount


def list_range(date_from: date, date_to: date) -> list[dict]:
    """按 business_date DESC, account_code ASC 列出区间内所有人工录入行。"""
    sql = f"""
        SELECT id, business_date, account_code, ad_account_id, spend_usd,
               updated_by, updated_at, created_at
        FROM {TABLE}
        WHERE business_date BETWEEN %s AND %s
        ORDER BY business_date DESC, account_code ASC
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, (date_from, date_to))
        return list(cur.fetchall())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_manual_ad_spend.py::test_upsert_entries_inserts_new_rows -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/manual_ad_spend.py tests/test_manual_ad_spend.py
git commit -m "feat(order-analytics): add manual_ad_spend DAO upsert/list

Docs-anchor: docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md#dao-层"
```

---

## Task 3: DAO — `upsert_entries` updates existing row, partial entries

**Files:**
- Modify: `tests/test_manual_ad_spend.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_manual_ad_spend.py`:

```python
def test_upsert_updates_existing_row_and_preserves_created_at():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=7,
    )
    first = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))[0]

    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "250.00"}],
        updated_by=9,
    )
    after = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))[0]
    assert after["spend_usd"] == Decimal("250.0000")
    assert after["updated_by"] == 9
    assert after["created_at"] == first["created_at"]


def test_upsert_partial_entries_does_not_clear_others():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"},
            {"account_code": "Omurio",    "ad_account_id": "1253003326160754", "spend_usd": "200"},
        ],
        updated_by=7,
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "999"}],
        updated_by=9,
    )
    rows = {r["account_code"]: r for r in manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))}
    assert rows["newjoyloo"]["spend_usd"] == Decimal("999.0000")
    assert rows["Omurio"]["spend_usd"] == Decimal("200.0000")
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_manual_ad_spend.py -v`
Expected: All 3 tests pass. The DAO already implements both behaviors correctly (`ON DUPLICATE KEY UPDATE` doesn't touch `created_at`, partial executemany only affects rows in the payload).

- [ ] **Step 3: Commit**

```bash
git add tests/test_manual_ad_spend.py
git commit -m "test(order-analytics): cover manual_ad_spend upsert update + partial cases"
```

---

## Task 4: DAO — `delete_entry` + `load_supplement_map`

**Files:**
- Modify: `appcore/order_analytics/manual_ad_spend.py`
- Modify: `tests/test_manual_ad_spend.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_manual_ad_spend.py`:

```python
def test_delete_entry_returns_true_when_existed():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=7,
    )
    deleted = manual_ad_spend.delete_entry(business_date=date(2026, 5, 8), account_code="newjoyloo")
    assert deleted is True
    assert manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8)) == []


def test_delete_entry_returns_false_when_absent():
    deleted = manual_ad_spend.delete_entry(business_date=date(2026, 5, 8), account_code="ghost")
    assert deleted is False


def test_load_supplement_map_returns_keyed_by_date_and_account_id():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 7),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"},
        ],
        updated_by=7,
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "Omurio", "ad_account_id": "1253003326160754", "spend_usd": "200"},
        ],
        updated_by=7,
    )
    out = manual_ad_spend.load_supplement_map(date(2026, 5, 7), date(2026, 5, 8))
    assert out == {
        (date(2026, 5, 7), "1861285821213497"): Decimal("300.0000"),
        (date(2026, 5, 8), "1253003326160754"): Decimal("200.0000"),
    }


def test_load_supplement_map_filters_by_range():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 1),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=7,
    )
    out = manual_ad_spend.load_supplement_map(date(2026, 5, 7), date(2026, 5, 8))
    assert out == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manual_ad_spend.py -v -k "delete_entry or load_supplement_map"`
Expected: FAIL — `delete_entry` and `load_supplement_map` not defined.

- [ ] **Step 3: Implement the two new functions**

Append to `appcore/order_analytics/manual_ad_spend.py`:

```python
def delete_entry(*, business_date: date, account_code: str) -> bool:
    """删除一条人工录入。返回是否真的删了一行。"""
    sql = f"DELETE FROM {TABLE} WHERE business_date = %s AND account_code = %s"
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, (business_date, account_code))
        conn.commit()
        return cur.rowcount > 0


def load_supplement_map(date_from: date, date_to: date) -> dict[tuple[date, str], Decimal]:
    """供 order_profit_aggregation 调用：返回 {(business_date, ad_account_id): spend_usd}。"""
    sql = f"""
        SELECT business_date, ad_account_id, spend_usd
        FROM {TABLE}
        WHERE business_date BETWEEN %s AND %s
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, (date_from, date_to))
        return {(row["business_date"], row["ad_account_id"]): row["spend_usd"] for row in cur.fetchall()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manual_ad_spend.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/manual_ad_spend.py tests/test_manual_ad_spend.py
git commit -m "feat(order-analytics): add manual_ad_spend delete + load_supplement_map"
```

---

## Task 5: Aggregation supplement — sync_sum > 0 case (no-op)

**Files:**
- Modify: `appcore/order_analytics/order_profit_aggregation.py`
- Modify: `tests/test_order_profit_aggregation.py`

- [ ] **Step 1: Locate the function and verify current return shape**

Run:
```bash
grep -n "def get_order_profit_status_summary\|total_profit_usd\|unallocated" appcore/order_analytics/order_profit_aggregation.py | head -20
```

Read `appcore/order_analytics/order_profit_aggregation.py` from the line `def get_order_profit_status_summary` to its return statement. Identify:
- Where `unallocated` is computed (look for the SQL on `meta_ad_daily_campaign_metrics WHERE product_id IS NULL`).
- Where the per-`(date, ad_account_id)` sync sum is available, or where it must be computed.
- The return dict: confirm it has key `total_profit_usd`. Note the surrounding keys you will add `manual_unallocated_supplement_usd` next to (alphabetical or grouped with `unallocated`).

- [ ] **Step 2: Write the failing test (sync sum > 0, supplement skipped)**

Open `tests/test_order_profit_aggregation.py` and append (preserving any existing fixtures):

```python
from datetime import date
from decimal import Decimal

import pytest

from appcore.db import get_conn
from appcore.order_analytics import manual_ad_spend, order_profit_aggregation


@pytest.fixture
def _seed_meta_accounts(monkeypatch):
    """Stub get_all_accounts so the supplement loop sees deterministic accounts."""
    from appcore import meta_ad_accounts

    fake = [
        meta_ad_accounts.MetaAdAccount(
            code="newjoyloo", account_id="1861285821213497", business_id="b",
            csv_prefix="newjoyloo", store_codes=("newjoy",), enabled=True, label="Newjoyloo",
        ),
        meta_ad_accounts.MetaAdAccount(
            code="Omurio", account_id="1253003326160754", business_id="b",
            csv_prefix="Omurio", store_codes=("omurio",), enabled=True, label="Omurio",
        ),
    ]
    monkeypatch.setattr(meta_ad_accounts, "get_all_accounts", lambda: fake)


@pytest.fixture(autouse=True)
def _clean_manual_ad_spend():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM meta_ad_manual_daily_spend")
        conn.commit()
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM meta_ad_manual_daily_spend")
        conn.commit()


def test_supplement_skipped_when_sync_has_any_spend(_seed_meta_accounts, monkeypatch):
    """sync sum > 0 时即使有 manual 行也不该叠加。"""
    monkeypatch.setattr(
        order_profit_aggregation,
        "_load_sync_account_totals",
        lambda date_from, date_to: {
            (date(2026, 5, 8), "1861285821213497"): Decimal("0.01"),
        },
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"}],
        updated_by=1,
    )
    summary = order_profit_aggregation.get_order_profit_status_summary(
        date_from=date(2026, 5, 8), date_to=date(2026, 5, 8),
    )
    assert summary["manual_unallocated_supplement_usd"] == Decimal("0")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_order_profit_aggregation.py::test_supplement_skipped_when_sync_has_any_spend -v`
Expected: FAIL — either `KeyError: 'manual_unallocated_supplement_usd'` or `AttributeError: _load_sync_account_totals`.

- [ ] **Step 4: Implement supplement logic in `order_profit_aggregation.py`**

Add a helper near the top of the module (after existing helpers, before `get_order_profit_status_summary`):

```python
def _load_sync_account_totals(
    date_from: date, date_to: date,
) -> dict[tuple[date, str], Decimal]:
    """对每个 (business_date, ad_account_id) 算 sync ad spend 总和（含 product_id IS NULL 行）。

    优先走 meta_ad_daily_campaign_metrics（收盘日表）；当账户当天无任何收盘日行时，
    回退到 meta_ad_realtime_daily_campaign_metrics 的 latest snapshot per (date, account)
    （遵守 CLAUDE.md "按 (business_date, ad_account_id) 取最新 snapshot" 反事故规则）。
    """
    totals: dict[tuple[date, str], Decimal] = {}
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT business_date, ad_account_id, SUM(spend_usd) AS total
            FROM meta_ad_daily_campaign_metrics
            WHERE business_date BETWEEN %s AND %s
            GROUP BY business_date, ad_account_id
            """,
            (date_from, date_to),
        )
        for row in cur.fetchall():
            totals[(row["business_date"], str(row["ad_account_id"]))] = Decimal(row["total"] or 0)

        cur.execute(
            """
            SELECT m.business_date, m.ad_account_id, SUM(m.spend_usd) AS total
            FROM meta_ad_realtime_daily_campaign_metrics m
            INNER JOIN (
                SELECT business_date, ad_account_id, MAX(snapshot_at) AS latest
                FROM meta_ad_realtime_daily_campaign_metrics
                WHERE business_date BETWEEN %s AND %s
                GROUP BY business_date, ad_account_id
            ) latest_snap
              ON m.business_date = latest_snap.business_date
             AND m.ad_account_id = latest_snap.ad_account_id
             AND m.snapshot_at = latest_snap.latest
            GROUP BY m.business_date, m.ad_account_id
            """,
            (date_from, date_to),
        )
        for row in cur.fetchall():
            key = (row["business_date"], str(row["ad_account_id"]))
            if key not in totals:  # 仅当收盘日表无该账户行时回退
                totals[key] = Decimal(row["total"] or 0)
    return totals
```

In `get_order_profit_status_summary`, after `unallocated` is computed and BEFORE `total_profit` is calculated, insert:

```python
from appcore.order_analytics import manual_ad_spend  # at top of file if not already imported

# ... existing code that computes unallocated ...

sync_totals = _load_sync_account_totals(date_from, date_to)
manual_map = manual_ad_spend.load_supplement_map(date_from, date_to)
manual_supplement = Decimal("0")
for (business_date, ad_account_id), manual_spend in manual_map.items():
    sync_total = sync_totals.get((business_date, ad_account_id), Decimal("0"))
    if sync_total == 0:
        manual_supplement += manual_spend
unallocated = unallocated + manual_supplement
```

In the return dict, add:

```python
return {
    # ... existing keys ...
    "manual_unallocated_supplement_usd": manual_supplement,
    # ... existing total_profit_usd line, now using the supplemented unallocated ...
}
```

(Keep `total_profit_usd` formula as `confirmed_profit + estimated_profit - unallocated` — unchanged because we already mutated `unallocated`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_order_profit_aggregation.py::test_supplement_skipped_when_sync_has_any_spend -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appcore/order_analytics/order_profit_aggregation.py tests/test_order_profit_aggregation.py
git commit -m "feat(order-analytics): add manual ad spend supplement to total_profit

Sync sum > 0 case verified: supplement stays at 0 when any sync data exists.

Docs-anchor: docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md#兜底语义"
```

---

## Task 6: Aggregation supplement — sync_sum == 0 triggers, partial accounts, range filter

**Files:**
- Modify: `tests/test_order_profit_aggregation.py`

- [ ] **Step 1: Write the three additional failing tests**

Append to `tests/test_order_profit_aggregation.py`:

```python
def test_supplement_added_when_sync_sum_is_zero(_seed_meta_accounts, monkeypatch):
    """该账户该天 sync 完全无数据时，manual 值进 unallocated。"""
    monkeypatch.setattr(
        order_profit_aggregation, "_load_sync_account_totals",
        lambda date_from, date_to: {},  # sync 完全无行
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"},
            {"account_code": "Omurio",    "ad_account_id": "1253003326160754", "spend_usd": "200"},
        ],
        updated_by=1,
    )
    summary = order_profit_aggregation.get_order_profit_status_summary(
        date_from=date(2026, 5, 8), date_to=date(2026, 5, 8),
    )
    assert summary["manual_unallocated_supplement_usd"] == Decimal("500.0000")


def test_supplement_per_account_only_when_that_account_sync_zero(_seed_meta_accounts, monkeypatch):
    """混合：一个账户 sync > 0 → 不叠加；另一个 sync = 0 → 叠加该账户 manual。"""
    monkeypatch.setattr(
        order_profit_aggregation, "_load_sync_account_totals",
        lambda date_from, date_to: {
            (date(2026, 5, 8), "1253003326160754"): Decimal("204.12"),  # Omurio sync > 0
            # newjoyloo 不在 map 里 → 视为 0
        },
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"},
            {"account_code": "Omurio",    "ad_account_id": "1253003326160754", "spend_usd": "999"},
        ],
        updated_by=1,
    )
    summary = order_profit_aggregation.get_order_profit_status_summary(
        date_from=date(2026, 5, 8), date_to=date(2026, 5, 8),
    )
    assert summary["manual_unallocated_supplement_usd"] == Decimal("300.0000")


def test_supplement_filtered_by_query_range(_seed_meta_accounts, monkeypatch):
    """只在 [date_from, date_to] 内的 manual 行参与。"""
    monkeypatch.setattr(
        order_profit_aggregation, "_load_sync_account_totals", lambda date_from, date_to: {}
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 1),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "9999"}],
        updated_by=1,
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"}],
        updated_by=1,
    )
    summary = order_profit_aggregation.get_order_profit_status_summary(
        date_from=date(2026, 5, 8), date_to=date(2026, 5, 8),
    )
    assert summary["manual_unallocated_supplement_usd"] == Decimal("300.0000")
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_order_profit_aggregation.py -v -k supplement`
Expected: All 4 supplement tests pass (Task 5 implementation already covers these cases).

- [ ] **Step 3: Commit**

```bash
git add tests/test_order_profit_aggregation.py
git commit -m "test(order-analytics): cover manual supplement zero-trigger + per-account + range"
```

---

## Task 7: API GET `/order-analytics/manual-ad-spend/list`

**Files:**
- Modify: `web/routes/order_analytics.py`
- Modify: `tests/test_manual_ad_spend.py`

- [ ] **Step 1: Locate route helpers**

Run:
```bash
grep -n "permission_required\|_audit_order_analytics_action\|_json_response\|_json_safe\|from appcore.order_analytics" web/routes/order_analytics.py | head -20
```

Note the import block at the top — your new route imports `manual_ad_spend` from `appcore.order_analytics` and `meta_ad_accounts` (already imported per `meta_ad_accounts_get`).

- [ ] **Step 2: Write the failing route test**

Append to `tests/test_manual_ad_spend.py`:

```python
def test_list_route_returns_accounts_and_rows(client, login_admin):
    login_admin(client)
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"}],
        updated_by=1,
    )
    resp = client.get("/order-analytics/manual-ad-spend/list?from=2026-05-08&to=2026-05-08")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "accounts" in body and "rows" in body
    codes = [a["code"] for a in body["accounts"]]
    assert "newjoyloo" in codes
    assert any(r["business_date"] == "2026-05-08" for r in body["rows"])
    nj_entry = next(r for r in body["rows"] if r["business_date"] == "2026-05-08")["entries"]["newjoyloo"]
    assert nj_entry["manual_spend_usd"] == 300.0
```

The fixtures `client` and `login_admin` already exist in `tests/conftest.py` (used by `test_order_analytics_*` tests). If `login_admin` is not present there, copy the pattern from `tests/test_order_analytics_audit.py`.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_manual_ad_spend.py::test_list_route_returns_accounts_and_rows -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 4: Implement the GET route**

In `web/routes/order_analytics.py`, near the existing `meta_ad_accounts_get` (around line 455), add:

```python
@bp.route("/order-analytics/manual-ad-spend/list", methods=["GET"])
@login_required
@permission_required("data_analytics")
def manual_ad_spend_list():
    """列出区间内每天每账户的人工录入金额 + sync 状态对比。"""
    from datetime import date as _date
    try:
        date_from = _date.fromisoformat((request.args.get("from") or "").strip())
        date_to = _date.fromisoformat((request.args.get("to") or "").strip())
    except ValueError:
        return _json_response(error="invalid_range", detail="from/to must be YYYY-MM-DD"), 400
    if date_to < date_from:
        return _json_response(error="invalid_range", detail="to must be >= from"), 400
    if (date_to - date_from).days > 90:
        return _json_response(error="range_too_large", detail="max 90 days"), 400

    accounts = list(meta_ad_accounts.get_all_accounts())
    manual_rows = manual_ad_spend.list_range(date_from, date_to)
    sync_totals = order_profit_aggregation._load_sync_account_totals(date_from, date_to)

    # group manual_rows by date
    by_date: dict = {}
    for row in manual_rows:
        d = row["business_date"]
        by_date.setdefault(d, {})[row["account_code"]] = row

    # build rows for every date in [date_from, date_to] that has either manual or sync data
    out_dates = set(by_date)
    for (d, _aid), total in sync_totals.items():
        if total > 0:
            out_dates.add(d)
    rows = []
    for d in sorted(out_dates, reverse=True):
        entries = {}
        for acc in accounts:
            sync_spend = sync_totals.get((d, acc.account_id), Decimal("0"))
            manual_row = by_date.get(d, {}).get(acc.code)
            manual_val = float(manual_row["spend_usd"]) if manual_row else None
            if sync_spend > 0:
                effective = "sync"
            elif manual_val is not None:
                effective = "manual"
            else:
                effective = "none"
            entries[acc.code] = {
                "manual_spend_usd": manual_val,
                "sync_spend_usd": float(sync_spend),
                "effective": effective,
                "updated_by": manual_row["updated_by"] if manual_row else None,
                "updated_at": manual_row["updated_at"].isoformat() if manual_row else None,
            }
        # status: all sync>0 → sync; any manual & at least one sync==0 → manual; mixed → partial
        sync_states = [entries[a.code]["sync_spend_usd"] > 0 for a in accounts]
        has_manual = any(entries[a.code]["effective"] == "manual" for a in accounts)
        if all(sync_states):
            status = "sync"
        elif has_manual:
            status = "manual"
        else:
            status = "partial"
        rows.append({"business_date": d.isoformat(), "entries": entries, "sync_status": status})

    return _json_response(_json_safe({
        "accounts": [a.to_dict() for a in accounts],
        "rows": rows,
    }))
```

Add to the imports at the top of the file (if not already present):

```python
from decimal import Decimal
from appcore.order_analytics import manual_ad_spend, order_profit_aggregation
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_manual_ad_spend.py::test_list_route_returns_accounts_and_rows -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/routes/order_analytics.py tests/test_manual_ad_spend.py
git commit -m "feat(order-analytics): add GET /manual-ad-spend/list route"
```

---

## Task 8: API POST upsert + validation

**Files:**
- Modify: `web/routes/order_analytics.py`
- Modify: `tests/test_manual_ad_spend.py`

- [ ] **Step 1: Write the failing tests (happy path + 5 validation cases)**

Append to `tests/test_manual_ad_spend.py`:

```python
import json as _json


def _post(client, body: dict):
    return client.post(
        "/order-analytics/manual-ad-spend",
        data=_json.dumps(body),
        content_type="application/json",
    )


def test_upsert_route_happy_path(client, login_admin):
    login_admin(client)
    resp = _post(client, {
        "business_date": "2026-05-08",
        "entries": [
            {"account_code": "newjoyloo", "spend_usd": 300.0},
            {"account_code": "Omurio",    "spend_usd": 200.0},
        ],
    })
    assert resp.status_code == 200
    rows = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))
    assert {r["account_code"] for r in rows} == {"newjoyloo", "Omurio"}


def test_upsert_route_rejects_future_date(client, login_admin, monkeypatch):
    login_admin(client)
    # Pin "today" to 2026-05-09 in the route's tz comparison
    from web.routes import order_analytics as routes_oa
    monkeypatch.setattr(routes_oa, "_today_in_cst", lambda: date(2026, 5, 9))
    resp = _post(client, {
        "business_date": "2026-05-10",
        "entries": [{"account_code": "newjoyloo", "spend_usd": 100.0}],
    })
    assert resp.status_code == 400
    assert "future" in (resp.get_json().get("detail") or "").lower()


def test_upsert_route_rejects_unknown_account_code(client, login_admin):
    login_admin(client)
    resp = _post(client, {
        "business_date": "2026-05-08",
        "entries": [{"account_code": "ghost", "spend_usd": 100.0}],
    })
    assert resp.status_code == 400


def test_upsert_route_rejects_negative_spend(client, login_admin):
    login_admin(client)
    resp = _post(client, {
        "business_date": "2026-05-08",
        "entries": [{"account_code": "newjoyloo", "spend_usd": -1.0}],
    })
    assert resp.status_code == 400


def test_upsert_route_rejects_too_many_entries(client, login_admin):
    login_admin(client)
    resp = _post(client, {
        "business_date": "2026-05-08",
        "entries": [{"account_code": f"x{i}", "spend_usd": 1.0} for i in range(21)],
    })
    assert resp.status_code == 400


def test_upsert_route_requires_permission(client, login_user_no_data_analytics_perm):
    login_user_no_data_analytics_perm(client)
    resp = _post(client, {
        "business_date": "2026-05-08",
        "entries": [{"account_code": "newjoyloo", "spend_usd": 100.0}],
    })
    assert resp.status_code in (302, 403)
```

If `login_user_no_data_analytics_perm` is not in `tests/conftest.py`, copy the pattern from any existing test that asserts permission denial for `data_analytics`-gated routes (e.g., `tests/test_order_analytics_audit.py`); if no such pattern exists, define the fixture locally in this test file by logging in as a user without `data_analytics` permission.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manual_ad_spend.py -v -k upsert_route`
Expected: All FAIL — route does not exist.

- [ ] **Step 3: Implement the POST route + helper**

Add at the top of `web/routes/order_analytics.py` (with other helpers/imports):

```python
from zoneinfo import ZoneInfo

_CST = ZoneInfo("Asia/Shanghai")


def _today_in_cst() -> date:
    from datetime import datetime as _dt
    return _dt.now(_CST).date()
```

Then add the route, near the GET `/manual-ad-spend/list` route:

```python
_MAX_ENTRIES_PER_REQUEST = 20
_MAX_SPEND = Decimal("1e8")


@bp.route("/order-analytics/manual-ad-spend", methods=["POST"])
@login_required
@permission_required("data_analytics")
def manual_ad_spend_upsert():
    payload = request.get_json(silent=True) or {}
    raw_date = str(payload.get("business_date") or "").strip()
    try:
        business_date = date.fromisoformat(raw_date)
    except ValueError:
        return _json_response(error="invalid_date", detail="business_date must be YYYY-MM-DD"), 400
    if business_date > _today_in_cst():
        return _json_response(error="invalid_date", detail="business_date cannot be in the future"), 400

    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        return _json_response(error="invalid_payload", detail="entries must be a non-empty list"), 400
    if len(entries_raw) > _MAX_ENTRIES_PER_REQUEST:
        return _json_response(error="too_many_entries", detail=f"max {_MAX_ENTRIES_PER_REQUEST}"), 400

    accounts_by_code = {a.code: a for a in meta_ad_accounts.get_all_accounts()}
    cleaned: list[dict] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            return _json_response(error="invalid_entry", detail="entry must be an object"), 400
        code = str(entry.get("account_code") or "").strip()
        if code not in accounts_by_code:
            return _json_response(error="invalid_account", detail=f"unknown account_code: {code}"), 400
        try:
            spend = Decimal(str(entry.get("spend_usd")))
        except Exception:
            return _json_response(error="invalid_spend", detail="spend_usd must be a number"), 400
        if spend < 0 or spend > _MAX_SPEND:
            return _json_response(error="invalid_spend", detail="spend_usd out of range [0, 1e8]"), 400
        # quantize to 4 decimals (matches DECIMAL(14,4))
        spend = spend.quantize(Decimal("0.0001"))
        cleaned.append({
            "account_code": code,
            "ad_account_id": accounts_by_code[code].account_id,
            "spend_usd": spend,
        })

    written = manual_ad_spend.upsert_entries(
        business_date=business_date, entries=cleaned, updated_by=current_user.id,
    )
    _audit_order_analytics_action(
        "order_analytics_manual_ad_spend_upserted",
        target_type="manual_ad_spend",
        detail={"business_date": business_date.isoformat(),
                "entries": [{"account_code": e["account_code"], "spend_usd": str(e["spend_usd"])} for e in cleaned]},
    )
    return _json_response({"ok": True, "written": written})
```

(Imports already added in Task 7. `current_user` is already imported in this file per `_audit_order_analytics_action`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manual_ad_spend.py -v -k upsert_route`
Expected: All 6 upsert_route tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/routes/order_analytics.py tests/test_manual_ad_spend.py
git commit -m "feat(order-analytics): add POST /manual-ad-spend with validation + audit"
```

---

## Task 9: API DELETE entry + audit

**Files:**
- Modify: `web/routes/order_analytics.py`
- Modify: `tests/test_manual_ad_spend.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_manual_ad_spend.py`:

```python
def test_delete_route_idempotent(client, login_admin):
    login_admin(client)
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=1,
    )
    resp1 = client.delete("/order-analytics/manual-ad-spend?business_date=2026-05-08&account_code=newjoyloo")
    assert resp1.status_code == 200
    assert resp1.get_json().get("deleted") is True
    # second delete still 200, deleted=False
    resp2 = client.delete("/order-analytics/manual-ad-spend?business_date=2026-05-08&account_code=newjoyloo")
    assert resp2.status_code == 200
    assert resp2.get_json().get("deleted") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manual_ad_spend.py::test_delete_route_idempotent -v`
Expected: FAIL — 404 (route does not exist).

- [ ] **Step 3: Implement the DELETE route**

In `web/routes/order_analytics.py`, after the POST route:

```python
@bp.route("/order-analytics/manual-ad-spend", methods=["DELETE"])
@login_required
@permission_required("data_analytics")
def manual_ad_spend_delete():
    raw_date = str(request.args.get("business_date") or "").strip()
    code = str(request.args.get("account_code") or "").strip()
    try:
        business_date = date.fromisoformat(raw_date)
    except ValueError:
        return _json_response(error="invalid_date", detail="business_date must be YYYY-MM-DD"), 400
    if not code:
        return _json_response(error="invalid_account", detail="account_code required"), 400

    deleted = manual_ad_spend.delete_entry(business_date=business_date, account_code=code)
    _audit_order_analytics_action(
        "order_analytics_manual_ad_spend_deleted",
        target_type="manual_ad_spend",
        detail={"business_date": business_date.isoformat(), "account_code": code, "deleted": deleted},
    )
    return _json_response({"ok": True, "deleted": deleted})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_manual_ad_spend.py::test_delete_route_idempotent -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/routes/order_analytics.py tests/test_manual_ad_spend.py
git commit -m "feat(order-analytics): add DELETE /manual-ad-spend (idempotent)"
```

---

## Task 10: UI — sub-tab HTML scaffold + table skeleton

**Files:**
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: Locate the 广告分析 sub-tab list**

Run:
```bash
grep -n 'data-tab="ads"\|sub-tab\|data-subtab\|ads-overview\|ads-campaign\|ads-adset\|ads-ad' web/templates/order_analytics.html | head -30
```

Note the existing sub-tab markup pattern (button group + corresponding panel divs). Mirror it.

- [ ] **Step 2: Add the new sub-tab button + empty panel**

Find the sub-tab button strip inside the 广告分析 panel (the one containing `Campaign / Ad Set / Ad`). Append a new button matching the existing pattern:

```html
<button type="button" class="ads-subtab-btn" data-subtab="ads-manual-input" role="tab" aria-selected="false">人工录入</button>
```

Find the corresponding panel container (sibling of the existing sub-panels). Append:

```html
<section class="ads-subtab-panel" data-subtab="ads-manual-input" hidden>
  <div class="ads-manual-input">
    <header class="ads-manual-input__header">
      <label>日期范围
        <input type="date" id="ads-manual-input-from">
        至
        <input type="date" id="ads-manual-input-to">
      </label>
      <button type="button" id="ads-manual-input-refresh" class="btn btn-secondary">刷新</button>
      <button type="button" id="ads-manual-input-add" class="btn btn-primary">+ 新增 / 编辑</button>
    </header>
    <table class="ads-manual-input__table" id="ads-manual-input-table">
      <thead>
        <tr id="ads-manual-input-thead-row">
          <th>业务日</th>
          <!-- account columns injected by JS -->
          <th>Sync 状态</th>
          <th>更新人</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody id="ads-manual-input-tbody"></tbody>
    </table>
    <div id="ads-manual-input-empty" class="ads-manual-input__empty" hidden>
      区间内无数据。
    </div>
  </div>
</section>

<dialog id="ads-manual-input-modal" class="ads-manual-input__modal">
  <form id="ads-manual-input-form" method="dialog">
    <h3>录入广告费</h3>
    <label>业务日 <input type="date" id="ads-manual-input-modal-date" required></label>
    <div id="ads-manual-input-modal-fields"><!-- per-account spend inputs injected by JS --></div>
    <div class="ads-manual-input__modal-actions">
      <button type="button" id="ads-manual-input-modal-cancel">取消</button>
      <button type="submit" id="ads-manual-input-modal-save" class="btn btn-primary">保存</button>
    </div>
  </form>
</dialog>
```

- [ ] **Step 3: Render in dev server, confirm tab appears**

Run:
```bash
PYTHONUNBUFFERED=1 venv/bin/python main.py --port 5090 &
DEV_PID=$!
until curl -s http://127.0.0.1:5090/healthz >/dev/null 2>&1 || curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5090/ | grep -qE "^(200|302)$"; do sleep 1; done
echo "dev server ready"
```

Use [testuser.md](../../../testuser.md) admin creds to log in via curl or browser, navigate to `/order-analytics`, click 广告分析 → click 人工录入. Confirm:
- Button "人工录入" appears at the end of the sub-tab strip.
- Clicking it shows the empty panel (date inputs, refresh, + button, empty table).
- No 500/console error.

Stop dev server:
```bash
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 4: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "feat(order-analytics): add 人工录入 sub-tab scaffold"
```

---

## Task 11: UI — JS: load list + render table

**Files:**
- Modify: `web/templates/order_analytics.html` (the existing inline `<script>` block, or a new `<script>` near the bottom of the 广告分析 panel)

- [ ] **Step 1: Locate the existing inline JS block for 广告分析**

Run:
```bash
grep -n 'ads-subtab\|ads-overview\|ads-campaign\|adsListLoad\|adsTabActivate\|/order-analytics/ads/' web/templates/order_analytics.html | head -30
```

Pick the inline JS block that handles other ads sub-tabs. Add the following code at the end of that block (or in a new `<script>` block at the bottom of `order_analytics.html`).

- [ ] **Step 2: Add the JS list-loader**

```javascript
(function() {
  const $ = (id) => document.getElementById(id);
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';
  let cachedAccounts = [];

  function todayCST() {
    const now = new Date();
    const cst = new Date(now.getTime() + (now.getTimezoneOffset() + 8 * 60) * 60000);
    return cst.toISOString().slice(0, 10);
  }
  function defaultFrom() {
    const d = new Date();
    d.setDate(d.getDate() - 13);
    const cst = new Date(d.getTime() + (d.getTimezoneOffset() + 8 * 60) * 60000);
    return cst.toISOString().slice(0, 10);
  }

  function fmtUsd(n) {
    if (n === null || n === undefined) return '—';
    return '$' + Number(n).toFixed(2);
  }

  function statusBadge(status) {
    const map = {
      sync:    { cls: 'status-sync',    label: '●sync' },
      partial: { cls: 'status-partial', label: '●部分sync' },
      manual:  { cls: 'status-manual',  label: '○手动兜底' },
    };
    const x = map[status] || { cls: '', label: status };
    return `<span class="${x.cls}">${x.label}</span>`;
  }

  async function loadList() {
    const from = $('ads-manual-input-from').value || defaultFrom();
    const to = $('ads-manual-input-to').value || todayCST();
    $('ads-manual-input-from').value = from;
    $('ads-manual-input-to').value = to;
    const resp = await fetch(`/order-analytics/manual-ad-spend/list?from=${from}&to=${to}`, { credentials: 'same-origin' });
    if (!resp.ok) {
      console.error('manual-ad-spend list failed', resp.status);
      return;
    }
    const body = await resp.json();
    cachedAccounts = body.accounts || [];

    // header
    const headRow = $('ads-manual-input-thead-row');
    [...headRow.querySelectorAll('th.acct-col')].forEach((el) => el.remove());
    const refTh = headRow.children[1]; // insert before "Sync 状态"
    cachedAccounts.forEach((acc) => {
      const th = document.createElement('th');
      th.className = 'acct-col';
      th.textContent = acc.label || acc.code;
      headRow.insertBefore(th, refTh);
    });

    // body
    const tbody = $('ads-manual-input-tbody');
    tbody.innerHTML = '';
    const rows = body.rows || [];
    if (!rows.length) {
      $('ads-manual-input-empty').hidden = false;
      return;
    }
    $('ads-manual-input-empty').hidden = true;
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      tr.dataset.businessDate = row.business_date;
      tr.innerHTML = `<td>${row.business_date}</td>`;
      cachedAccounts.forEach((acc) => {
        const e = row.entries[acc.code] || {};
        const display = e.effective === 'sync' ? `${fmtUsd(e.sync_spend_usd)} (sync)`
          : e.effective === 'manual' ? `${fmtUsd(e.manual_spend_usd)} ✏`
          : '—';
        tr.innerHTML += `<td class="acct-col">${display}</td>`;
      });
      tr.innerHTML += `<td>${statusBadge(row.sync_status)}</td>`;
      const anyManual = cachedAccounts.find((a) => row.entries[a.code]?.effective === 'manual');
      tr.innerHTML += `<td>${anyManual ? (row.entries[anyManual.code].updated_by || '—') : '—'}</td>`;
      tr.innerHTML += `<td>
        <button type="button" class="ams-edit" data-date="${row.business_date}">编辑</button>
        ${anyManual ? `<button type="button" class="ams-delete" data-date="${row.business_date}">删除</button>` : ''}
      </td>`;
      tbody.appendChild(tr);
    });
  }

  // expose for modal/handlers in next task
  window.__amsLoadList = loadList;
  window.__amsAccounts = () => cachedAccounts;

  // initial wiring
  document.addEventListener('DOMContentLoaded', () => {
    if (!$('ads-manual-input-from')) return;
    $('ads-manual-input-from').value = defaultFrom();
    $('ads-manual-input-to').value = todayCST();
    $('ads-manual-input-refresh')?.addEventListener('click', loadList);
  });

  // also load when sub-tab becomes visible (find the existing sub-tab activation hook)
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-subtab="ads-manual-input"]');
    if (btn && btn.tagName === 'BUTTON') {
      setTimeout(loadList, 50);
    }
  });
})();
```

- [ ] **Step 3: Manual smoke test in dev server**

```bash
PYTHONUNBUFFERED=1 venv/bin/python main.py --port 5090 &
DEV_PID=$!
sleep 5
```

Log in as admin, go to `/order-analytics` → 广告分析 → 人工录入. Verify:
- Date inputs default to "today minus 13 days" → today.
- Click "刷新" → table renders account columns dynamically.
- If `meta_ad_realtime_daily_campaign_metrics` has any rows in range, you see those days listed; manual rows (none yet) show "—".
- No console errors.

Stop dev server:
```bash
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 4: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "feat(order-analytics): wire 人工录入 sub-tab list rendering"
```

---

## Task 12: UI — JS: modal save + delete

**Files:**
- Modify: `web/templates/order_analytics.html` (same JS block)

- [ ] **Step 1: Add modal open/save/delete logic**

Append inside the same IIFE in `order_analytics.html` (right after the `loadList` function, before the closing `})()`):

```javascript
function openModal(forDate) {
  const accounts = window.__amsAccounts() || [];
  const fields = $('ads-manual-input-modal-fields');
  fields.innerHTML = '';
  accounts.forEach((acc) => {
    const wrap = document.createElement('label');
    wrap.style.display = 'block';
    wrap.style.margin = '8px 0';
    wrap.innerHTML = `${acc.label || acc.code} (${acc.account_id})${acc.enabled ? '' : ' (已停用)'}<br>
      $ <input type="number" step="0.01" min="0" data-acct-code="${acc.code}" placeholder="留空=不录">`;
    fields.appendChild(wrap);
  });
  $('ads-manual-input-modal-date').value = forDate || todayCST();
  // prefill from current row if exists
  if (forDate) {
    const tr = document.querySelector(`#ads-manual-input-tbody tr[data-business-date="${forDate}"]`);
    if (tr) {
      // Re-fetch the row data via cache: simplest is just call list and read; or re-call list. We'll trust user-friendly empty-prefill here.
    }
  }
  $('ads-manual-input-modal').showModal();
}

async function saveModal(ev) {
  ev.preventDefault();
  const business_date = $('ads-manual-input-modal-date').value;
  if (!business_date) return;
  const inputs = [...$('ads-manual-input-modal-fields').querySelectorAll('input[data-acct-code]')];
  const entries = inputs
    .filter((inp) => inp.value !== '')
    .map((inp) => ({ account_code: inp.dataset.acctCode, spend_usd: parseFloat(inp.value) }));
  if (!entries.length) {
    $('ads-manual-input-modal').close();
    return;
  }
  const resp = await fetch('/order-analytics/manual-ad-spend', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify({ business_date, entries }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    alert('保存失败: ' + (body.detail || resp.status));
    return;
  }
  $('ads-manual-input-modal').close();
  await loadList();
}

async function deleteEntry(business_date, account_code) {
  if (!confirm(`确认删除 ${business_date} / ${account_code} 的人工录入？`)) return;
  const url = `/order-analytics/manual-ad-spend?business_date=${business_date}&account_code=${encodeURIComponent(account_code)}`;
  const resp = await fetch(url, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': csrfToken },
  });
  if (!resp.ok) {
    alert('删除失败: ' + resp.status);
    return;
  }
  await loadList();
}

document.addEventListener('DOMContentLoaded', () => {
  $('ads-manual-input-add')?.addEventListener('click', () => openModal(null));
  $('ads-manual-input-form')?.addEventListener('submit', saveModal);
  $('ads-manual-input-modal-cancel')?.addEventListener('click', () => $('ads-manual-input-modal').close());
  $('ads-manual-input-tbody')?.addEventListener('click', async (ev) => {
    const editBtn = ev.target.closest('.ams-edit');
    if (editBtn) {
      openModal(editBtn.dataset.date);
      return;
    }
    const delBtn = ev.target.closest('.ams-delete');
    if (delBtn) {
      // delete iterates per-account-with-manual:
      const tr = delBtn.closest('tr');
      const accounts = window.__amsAccounts() || [];
      // pick the first manual account for that row; UX iteration: confirm per-account
      // (simple: prompt user to pick which account by re-fetching, OR delete all manual rows for that date)
      for (const acc of accounts) {
        const cellIdx = 1 + accounts.indexOf(acc);
        const cellText = tr.children[cellIdx]?.textContent || '';
        if (cellText.includes('✏')) {
          await deleteEntry(delBtn.dataset.date, acc.code);
        }
      }
    }
  });
});
```

- [ ] **Step 2: End-to-end smoke test in dev server**

```bash
PYTHONUNBUFFERED=1 venv/bin/python main.py --port 5090 &
DEV_PID=$!
sleep 5
```

Log in as admin. In 广告分析 → 人工录入:
1. Click "+ 新增 / 编辑" → modal opens with date input + one row per account.
2. Fill `newjoyloo=300`, leave others blank, save.
3. Modal closes, table refreshes; that row shows `$300.00 ✏` for newjoyloo, sync status `○手动兜底`.
4. Click 编辑 on that row → modal opens (currently empty prefill — known limitation, can be improved later).
5. Click 删除 → confirms → row updates / disappears.
6. Open another browser tab → `/medias` 产品看板 → "总利润" KPI reflects the supplement when sync is 0 for today.

Stop dev server:
```bash
kill $DEV_PID 2>/dev/null
```

- [ ] **Step 3: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "feat(order-analytics): wire 人工录入 modal save + delete"
```

---

## Task 13: Cognitive doc — CLAUDE.md anchor

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Append a new section to CLAUDE.md**

Append to `CLAUDE.md` (before any closing fences, after the "Meta 广告多账户同步" section):

```markdown
## Meta 广告费人工录入兜底（2026-05-09 起）

详细设计：[docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md](docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md)

- 表 `meta_ad_manual_daily_spend`：admin 在「广告分析 → 人工录入」录入 `(business_date, account_code, ad_account_id, spend_usd)`；唯一键 `(business_date, account_code)`，upsert 写入。
- 兜底语义：在 `appcore/order_analytics/order_profit_aggregation.get_order_profit_status_summary` 里，对每个 `(business_date, ad_account_id)` 比 sync sum：sync sum 严格 `> 0` → 完全忽略本表；sync sum `== 0` 或无行 → 把本表 `spend_usd` 加进 `unallocated`。
- **per-product 数据不变**：本表不下沉到产品级广告费分摊；产品看板 per-product「已分摊广告费」继续从 `meta_ad_daily_*` / `meta_ad_realtime_*` 来。
- 改 `order_profit_aggregation` 时**不要删除 `_load_sync_account_totals` 与 manual_ad_spend.load_supplement_map 的调用**，否则 sync 失败时「总利润」KPI 会再次虚高。回归测试：`pytest tests/test_order_profit_aggregation.py -k supplement -q`。
- 路由权限：`@login_required + @permission_required("data_analytics")`，与 `/order-analytics/meta-ad-accounts` 同款；CSRF 走 `layout.html` 的 meta 注入。
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): record manual ad spend fallback anchor"
```

---

## Task 14: Cross-link in 2026-05-07 spec

**Files:**
- Modify: `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md`

- [ ] **Step 1: Append related-link**

Find the bottom of the file (or any "## 相关文档" section). If a "相关文档" section exists, add a bullet; otherwise append:

```markdown

## 相关文档

- [Meta 广告费人工录入兜底（2026-05-09）](2026-05-09-manual-daily-ad-spend-design.md) — sync 失败时的兜底入口
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md
git commit -m "docs(specs): cross-link 2026-05-07 multi-account → 2026-05-09 manual fallback"
```

---

## Task 15: Full regression + end-to-end self-verification

**Files:** none (verification only)

- [ ] **Step 1: Run all related test suites**

```bash
venv/bin/python -m pytest tests/test_manual_ad_spend.py tests/test_order_profit_aggregation.py tests/test_order_analytics_audit.py -v
```

Expected: all green. If `test_order_profit_aggregation.py` has unrelated failures, check whether they predate this branch (`git log --diff-filter=M tests/test_order_profit_aggregation.py | head`); if pre-existing, file separately and proceed. Otherwise fix.

- [ ] **Step 2: End-to-end dev-server check**

```bash
PYTHONUNBUFFERED=1 venv/bin/python main.py --port 5090 &
DEV_PID=$!
sleep 5
```

As admin in browser:
1. Visit `/medias`, note "总利润" KPI value (call it `K0`).
2. Visit `/order-analytics` → 广告分析 → 人工录入.
3. Pick a date where today's sync is failing (per the conversation context, today 2026-05-09 sync is 0 for newjoyloo_bak).
4. Add manual `newjoyloo=$500` for that date, save. Confirm row appears with `$500.00 ✏`.
5. Refresh `/medias` → "总利润" KPI should be `K0 - $500` (assuming that date is in `/medias` default range and `_load_sync_account_totals` returns 0 for newjoyloo on that date).
6. In 人工录入, edit the row → change to `$300`, save → KPI updates to `K0 - $300`.
7. Delete the row → KPI returns to `K0`.

```bash
kill $DEV_PID 2>/dev/null
```

If any step fails, return to the relevant task and debug before merging.

- [ ] **Step 3: Push + deploy (per CLAUDE.md "本机部署" path A)**

```bash
git fetch origin master
git rebase origin/master
git push origin HEAD:master
sudo bash -c '
set -e
cd /opt/autovideosrt
git pull origin master --ff-only
systemctl restart autovideosrt
sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "/order-analytics: HTTP %{http_code}\n" http://127.0.0.1/order-analytics
'
```

Expected: `active`, HTTP 302 (login redirect, route exists). Migration auto-applies on restart per `appcore.db_migrations.ensure_up_to_date`.

- [ ] **Step 4: Post-deploy smoke**

Log in to `http://172.16.254.106/order-analytics` as admin → 广告分析 → 人工录入 → verify the sub-tab loads and "+ 新增/编辑" works on prod.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ 数据模型 → Task 1
- ✅ 兜底语义 → Tasks 5–6
- ✅ 3 个 API endpoints → Tasks 7–9
- ✅ DAO 4 个函数 (`upsert_entries`, `list_range`, `delete_entry`, `load_supplement_map`) → Tasks 2–4
- ✅ UI sub-tab + 表格 + modal → Tasks 10–12
- ✅ 文档锚点 (CLAUDE.md + spec cross-link) → Tasks 13–14
- ✅ 测试 (DAO + routes + aggregation) → distributed across Tasks 2–6, 7–9
- ✅ E2E 自检 + 部署 → Task 15

**Type/signature consistency:**
- DAO `upsert_entries(business_date=, entries=, updated_by=)` — keyword-only, used same way in all callers (route, tests).
- DAO `delete_entry(business_date=, account_code=)` — keyword-only, matches route + tests.
- `_load_sync_account_totals(date_from, date_to) -> dict[(date, str), Decimal]` — same signature in route handler and aggregation.

**Known limitations (documented inline):**
- Modal "edit" prefill is empty (a future polish; the user can re-enter values). Acknowledged in Task 12 step 2.
- `login_user_no_data_analytics_perm` fixture may need to be defined locally if not in conftest (Task 8).
