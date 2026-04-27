# 产品看板 V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/order-analytics` 加一个 "产品看板" Tab（设为默认），让 admin 每天打开就看到产品级订单 / 广告花费 / ROAS + 环比，辅助决策"哪些产品该补素材"。

**Architecture:** 纯查询聚合，复用现有 `shopify_orders` / `meta_ad_campaign_metrics` / `media_products` / `media_items` 表，不新增 schema。后端在 `appcore/order_analytics.py` 增加 1 个主入口 `get_dashboard()` + 7 个 helper。Blueprint 增加 1 个端点。模板增加 1 个 Tab。

**Tech Stack:** Python 3 + Flask + MySQL（现有 `appcore.db.query` / `query_one`）+ Jinja2 模板 + 原生 JS（无前端框架）+ pytest。

**Spec:** [docs/superpowers/specs/2026-04-26-product-dashboard-design.md](../specs/2026-04-26-product-dashboard-design.md)

---

## File Structure

### Created
- `tests/test_order_analytics_dashboard.py` — service 层单元测试

### Modified
- `appcore/order_analytics.py` — 加 `get_dashboard()` + 7 个 helper（追加到文件末尾）
- `web/routes/order_analytics.py` — 加 `GET /order-analytics/dashboard` 端点
- `web/templates/order_analytics.html` — 加 "产品看板" Tab（默认 Tab）+ 工具栏 + 表格 + JS
- `tests/test_order_analytics_ads.py` — 加 dashboard 路由测试（沿用该文件命名，避免新建 `test_order_analytics_routes.py` 与现状冲突）

### Schema 字段锁死（实施时不要再 grep）
- `shopify_orders.created_at_order` (DATETIME) — 时间字段；按日聚合用 `DATE(created_at_order)`
- `shopify_orders.billing_country` (VARCHAR(8)) — 国家代码
- `shopify_orders.product_id` — FK 到 `media_products.id`
- `shopify_orders.shopify_order_id` — 订单数 = `COUNT(DISTINCT shopify_order_id)`
- `shopify_orders.lineitem_quantity` + `lineitem_price` — 件数 + 收入 = `SUM(lineitem_quantity * lineitem_price)`
- `meta_ad_campaign_metrics.spend_usd` / `purchase_value_usd` / `result_count` (= Meta 购买次数) / `report_start_date` / `report_end_date` / `product_id`
- `meta_ad_campaign_metrics` **无 country 字段** — 国家筛选启用时整列降级（`ad_data_available=False`）
- `media_products.id` / `name` / `product_code` / `archived` (`= 0` 表示未归档) / `deleted_at` (NULL 表示未删除)
- `media_items.product_id` / `lang` / `deleted_at`

### 测试 fixture（现有，沿用）
- `authed_client_no_db` — 路由测试用（patch DB 的 admin 客户端）
- service 测试用 `monkeypatch.setattr(oa, "query", fake_query)` / `monkeypatch.setattr(oa, "query_one", fake_query_one)` 模式

---

## Task 1: helper `_compute_pct_change`

**Files:**
- Create: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py` (append at end)

- [ ] **Step 1: Write the failing test**

Create `tests/test_order_analytics_dashboard.py`:
```python
from __future__ import annotations

from appcore import order_analytics as oa


def test_compute_pct_change_normal():
    assert oa._compute_pct_change(120, 100) == 20.0
    assert oa._compute_pct_change(80, 100) == -20.0


def test_compute_pct_change_both_zero():
    assert oa._compute_pct_change(0, 0) == 0.0


def test_compute_pct_change_prev_zero_now_positive():
    # 无法计算百分比时返回 None（前端显示 "新增" 或 "-"）
    assert oa._compute_pct_change(50, 0) is None


def test_compute_pct_change_now_zero_prev_positive():
    assert oa._compute_pct_change(0, 100) == -100.0


def test_compute_pct_change_handles_none_inputs():
    assert oa._compute_pct_change(None, 100) == -100.0
    assert oa._compute_pct_change(100, None) is None
    assert oa._compute_pct_change(None, None) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_order_analytics_dashboard.py::test_compute_pct_change_normal -v
```
Expected: FAIL with `AttributeError: module 'appcore.order_analytics' has no attribute '_compute_pct_change'`.

- [ ] **Step 3: Write minimal implementation**

Append to `appcore/order_analytics.py`:
```python
# ── 产品看板 V1 ───────────────────────────────────────────

def _compute_pct_change(now, prev) -> float | None:
    """环比百分比。返回 None 表示无法计算（prev=0 且 now>0）。"""
    now_v = float(now or 0)
    prev_v = float(prev or 0)
    if prev_v == 0 and now_v == 0:
        return 0.0
    if prev_v == 0:
        return None
    return round((now_v - prev_v) / prev_v * 100, 2)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_order_analytics_dashboard.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _compute_pct_change helper (V1 task 1/14)"
```

---

## Task 2: helper `_resolve_period_range`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_order_analytics_dashboard.py`:
```python
from datetime import date


def test_resolve_period_range_full_past_month():
    start, end = oa._resolve_period_range("month", year=2026, month=3, today=date(2026, 4, 26))
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 31)


def test_resolve_period_range_current_month_truncates_to_yesterday():
    start, end = oa._resolve_period_range("month", year=2026, month=4, today=date(2026, 4, 26))
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 25)  # 昨日


def test_resolve_period_range_iso_week():
    # 2026 ISO week 17 = 2026-04-20 (Mon) ~ 2026-04-26 (Sun)
    start, end = oa._resolve_period_range("week", year=2026, week=17, today=date(2026, 5, 1))
    assert start == date(2026, 4, 20)
    assert end == date(2026, 4, 26)


def test_resolve_period_range_current_week_truncates_to_yesterday():
    start, end = oa._resolve_period_range("week", year=2026, week=17, today=date(2026, 4, 23))
    assert start == date(2026, 4, 20)
    assert end == date(2026, 4, 22)


def test_resolve_period_range_day():
    start, end = oa._resolve_period_range("day", date_str="2026-04-25", today=date(2026, 4, 26))
    assert start == date(2026, 4, 25)
    assert end == date(2026, 4, 25)


def test_resolve_period_range_invalid_period_raises():
    import pytest
    with pytest.raises(ValueError, match="invalid period"):
        oa._resolve_period_range("year", year=2026, today=date(2026, 4, 26))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k resolve_period_range
```
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Append to `appcore/order_analytics.py`:
```python
import calendar


def _resolve_period_range(
    period: str,
    *,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """返回 (start, end) 闭区间。

    - month: 该月 1 日 ~ 月末；若为当月，end = 昨日（不含今天）
    - week: ISO 周一 ~ 周日；若为当周，end = 昨日
    - day: date_str ~ date_str
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    if period == "month":
        if not year or not month:
            raise ValueError("year and month required for period=month")
        start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end = date(year, month, last_day)
        if start <= today <= end:
            end = yesterday if yesterday >= start else start
        return start, end

    if period == "week":
        if not year or not week:
            raise ValueError("year and week required for period=week")
        # ISO week: %G-%V-%u; %u=1 = Monday
        start = datetime.strptime(f"{year}-{week:02d}-1", "%G-%V-%u").date()
        end = start + timedelta(days=6)
        if start <= today <= end:
            end = yesterday if yesterday >= start else start
        return start, end

    if period == "day":
        if not date_str:
            raise ValueError("date required for period=day")
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return d, d

    raise ValueError(f"invalid period: {period}")
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k resolve_period_range
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _resolve_period_range helper (V1 task 2/14)"
```

---

## Task 3: helper `_resolve_compare_range`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_resolve_compare_range_full_month_to_prev_full_month():
    start, end = oa._resolve_compare_range(
        date(2026, 3, 1), date(2026, 3, 31), "month"
    )
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_resolve_compare_range_partial_month_to_prev_same_day():
    # 当月 4-1 ~ 4-25（截至昨日）→ 上月 3-1 ~ 3-25
    start, end = oa._resolve_compare_range(
        date(2026, 4, 1), date(2026, 4, 25), "month"
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 25)


def test_resolve_compare_range_week_to_prev_week():
    start, end = oa._resolve_compare_range(
        date(2026, 4, 20), date(2026, 4, 26), "week"
    )
    assert start == date(2026, 4, 13)
    assert end == date(2026, 4, 19)


def test_resolve_compare_range_partial_week_to_same_length_prev_week():
    # 当周 4-20 ~ 4-22（截至周三）→ 上周 4-13 ~ 4-15
    start, end = oa._resolve_compare_range(
        date(2026, 4, 20), date(2026, 4, 22), "week"
    )
    assert start == date(2026, 4, 13)
    assert end == date(2026, 4, 15)


def test_resolve_compare_range_day_to_prev_day():
    start, end = oa._resolve_compare_range(date(2026, 4, 25), date(2026, 4, 25), "day")
    assert start == date(2026, 4, 24)
    assert end == date(2026, 4, 24)
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k resolve_compare_range
```
Expected: FAIL (function not defined).

- [ ] **Step 3: Implementation**

Append to `appcore/order_analytics.py`:
```python
from dateutil.relativedelta import relativedelta  # already in deps via 现有模块


def _resolve_compare_range(start: date, end: date, period: str) -> tuple[date, date]:
    """返回上一个同长度切片。"""
    if period == "month":
        prev_start = start - relativedelta(months=1)
        # 切片长度 = end - start，让 prev_end = prev_start + (end - start)
        prev_end = prev_start + (end - start)
        return prev_start, prev_end

    if period == "week":
        prev_start = start - timedelta(days=7)
        prev_end = prev_start + (end - start)
        return prev_start, prev_end

    if period == "day":
        prev = start - timedelta(days=1)
        return prev, prev

    raise ValueError(f"invalid period: {period}")
```

如果 `dateutil` 不可用（grep 现有代码确认 — 大概率已有）：
```bash
grep -r "from dateutil" appcore/ web/ | head -3
```
若未安装，改用纯 datetime：
```python
def _resolve_compare_range(start: date, end: date, period: str) -> tuple[date, date]:
    if period == "month":
        # 减一个月：直接调整 month 字段
        prev_year = start.year - (1 if start.month == 1 else 0)
        prev_month = 12 if start.month == 1 else start.month - 1
        prev_start = date(prev_year, prev_month, start.day)
        prev_end = prev_start + (end - start)
        return prev_start, prev_end
    if period == "week":
        prev_start = start - timedelta(days=7)
        return prev_start, prev_start + (end - start)
    if period == "day":
        prev = start - timedelta(days=1)
        return prev, prev
    raise ValueError(f"invalid period: {period}")
```

实施时优先用 dateutil（如已存在）；否则用 fallback。

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k resolve_compare_range
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _resolve_compare_range helper (V1 task 3/14)"
```

---

## Task 4: helper `_aggregate_orders_by_product`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_aggregate_orders_by_product_returns_dict_keyed_by_product_id(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"product_id": 42, "orders": 10, "units": 12, "revenue": 240.5},
            {"product_id": 99, "orders": 3, "units": 3, "revenue": 60.0},
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country=None)

    assert 42 in result and 99 in result
    assert result[42]["orders"] == 10
    assert result[42]["units"] == 12
    assert result[42]["revenue"] == 240.5
    assert "DATE(created_at_order)" in captured["sql"]
    assert "billing_country" not in captured["sql"]  # 无国家筛选时不带
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 25))


def test_aggregate_orders_by_product_with_country_filter(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country="DE")

    assert "billing_country" in captured["sql"]
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 25), "DE")


def test_aggregate_orders_by_product_skips_null_product_id(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": None, "orders": 5, "units": 5, "revenue": 100.0},
        {"product_id": 42, "orders": 2, "units": 2, "revenue": 40.0},
    ])
    result = oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country=None)
    assert list(result.keys()) == [42]
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k aggregate_orders_by_product
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append:
```python
def _aggregate_orders_by_product(
    start: date, end: date, *, country: str | None = None
) -> dict[int, dict]:
    """按产品聚合订单。返回 {product_id: {orders, units, revenue}}。"""
    sql = (
        "SELECT product_id, "
        "COUNT(DISTINCT shopify_order_id) AS orders, "
        "SUM(lineitem_quantity) AS units, "
        "SUM(lineitem_quantity * lineitem_price) AS revenue "
        "FROM shopify_orders "
        "WHERE DATE(created_at_order) BETWEEN %s AND %s "
    )
    args: tuple = (start, end)
    if country:
        sql += "AND billing_country = %s "
        args = (start, end, country)
    sql += "GROUP BY product_id"

    rows = oa_query(sql, args) if False else query(sql, args)  # noqa
    out: dict[int, dict] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid is None:
            continue
        out[int(pid)] = {
            "orders": int(r.get("orders") or 0),
            "units": int(r.get("units") or 0),
            "revenue": float(r.get("revenue") or 0),
        }
    return out
```

（实施提示：`oa_query` 那行是误写，删掉只保留 `query(sql, args)`。`query` 已在 module top-level import）

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k aggregate_orders_by_product
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _aggregate_orders_by_product helper (V1 task 4/14)"
```

---

## Task 5: helper `_aggregate_ads_by_product`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_aggregate_ads_by_product_full_coverage_only(monkeypatch):
    """决策 #7：只纳入完全被 [start, end] 覆盖的广告报表。"""
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"product_id": 42, "spend": 1200.5, "purchases": 130, "purchase_value": 4500.0},
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))

    # SQL 必须用 'report_start_date >= start AND report_end_date <= end'（完全覆盖语义）
    assert "report_start_date >= %s" in captured["sql"]
    assert "report_end_date <= %s" in captured["sql"]
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 30))
    assert result[42]["spend"] == 1200.5
    assert result[42]["purchases"] == 130
    assert result[42]["purchase_value"] == 4500.0


def test_aggregate_ads_by_product_skips_null_product_id(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": None, "spend": 100.0, "purchases": 5, "purchase_value": 0.0},
        {"product_id": 42, "spend": 200.0, "purchases": 10, "purchase_value": 600.0},
    ])
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))
    assert list(result.keys()) == [42]


def test_aggregate_ads_by_product_decimals_to_floats(monkeypatch):
    from decimal import Decimal
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": 42, "spend": Decimal("1200.50"), "purchases": Decimal("130"),
         "purchase_value": Decimal("4500.00")},
    ])
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))
    assert result[42]["spend"] == 1200.5
    assert isinstance(result[42]["spend"], float)
    assert result[42]["purchases"] == 130
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k aggregate_ads_by_product
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append:
```python
def _aggregate_ads_by_product(start: date, end: date) -> dict[int, dict]:
    """按产品聚合广告。仅纳入 [report_start_date, report_end_date] 完全
    被 [start, end] 覆盖的报表（决策 #7）。
    返回 {product_id: {spend, purchases, purchase_value}}。"""
    sql = (
        "SELECT product_id, "
        "SUM(spend_usd) AS spend, "
        "SUM(result_count) AS purchases, "
        "SUM(purchase_value_usd) AS purchase_value "
        "FROM meta_ad_campaign_metrics "
        "WHERE report_start_date >= %s AND report_end_date <= %s "
        "GROUP BY product_id"
    )
    rows = query(sql, (start, end))
    out: dict[int, dict] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid is None:
            continue
        out[int(pid)] = {
            "spend": float(r.get("spend") or 0),
            "purchases": int(r.get("purchases") or 0),
            "purchase_value": float(r.get("purchase_value") or 0),
        }
    return out
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k aggregate_ads_by_product
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _aggregate_ads_by_product helper with full-coverage semantics (V1 task 5/14)"
```

---

## Task 6: helper `_count_media_items_by_product`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_count_media_items_by_product_groups_by_lang(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": 42, "lang": "en", "n": 1},
        {"product_id": 42, "lang": "de", "n": 2},
        {"product_id": 99, "lang": "en", "n": 1},
    ])
    result = oa._count_media_items_by_product()
    assert result[42] == {"en": 1, "de": 2}
    assert result[99] == {"en": 1}


def test_count_media_items_by_product_filters_deleted(monkeypatch):
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    oa._count_media_items_by_product()
    assert "deleted_at IS NULL" in captured["sql"]
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k count_media_items_by_product
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append:
```python
def _count_media_items_by_product() -> dict[int, dict[str, int]]:
    """SELECT product_id, lang, COUNT(*) FROM media_items WHERE deleted_at IS NULL
       GROUP BY product_id, lang"""
    rows = query(
        "SELECT product_id, lang, COUNT(*) AS n FROM media_items "
        "WHERE deleted_at IS NULL "
        "GROUP BY product_id, lang"
    )
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid is None:
            continue
        out.setdefault(int(pid), {})[r.get("lang") or ""] = int(r.get("n") or 0)
    return out
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k count_media_items_by_product
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _count_media_items_by_product helper (V1 task 6/14)"
```

---

## Task 7: helper `_join_and_compute_dashboard_rows`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_join_and_compute_filters_zero_zero_products():
    """决策 #12: orders=0 + spend=0 的产品被剔除。"""
    products = {
        42: {"id": 42, "name": "Glow", "product_code": "glow-rjc"},
        99: {"id": 99, "name": "Other", "product_code": "other-rjc"},
        7:  {"id": 7,  "name": "Zero", "product_code": "zero"},
    }
    orders_now  = {42: {"orders": 10, "units": 12, "revenue": 200.0}}
    orders_prev = {42: {"orders": 8,  "units": 10, "revenue": 150.0}}
    ads_now     = {99: {"spend": 100.0, "purchases": 5, "purchase_value": 250.0}}
    ads_prev    = {99: {"spend": 80.0,  "purchases": 4, "purchase_value": 200.0}}
    items       = {42: {"en": 1}, 99: {"en": 1}}

    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now=orders_now, orders_prev=orders_prev,
        ads_now=ads_now, ads_prev=ads_prev,
        items=items,
        ad_data_available=True,
    )

    pids = {r["product_id"] for r in rows}
    assert pids == {42, 99}  # 7 被剔除


def test_join_and_compute_roas_uses_shopify_revenue_over_spend():
    """决策 #13: ROAS = Shopify 收入 / Meta 花费。"""
    products = {42: {"id": 42, "name": "X", "product_code": "x"}}
    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now={42: {"orders": 5, "units": 5, "revenue": 500.0}},
        orders_prev={42: {"orders": 3, "units": 3, "revenue": 300.0}},
        ads_now={42: {"spend": 100.0, "purchases": 10, "purchase_value": 999.0}},
        ads_prev={42: {"spend": 80.0,  "purchases": 8,  "purchase_value": 800.0}},
        items={42: {"en": 1}},
        ad_data_available=True,
    )
    assert rows[0]["roas"] == 5.0          # 500 / 100，不是 999 / 100
    assert rows[0]["roas_prev"] == 3.75    # 300 / 80


def test_join_and_compute_ad_unavailable_drops_ad_columns():
    products = {42: {"id": 42, "name": "X", "product_code": "x"}}
    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now={42: {"orders": 5, "units": 5, "revenue": 500.0}},
        orders_prev={},
        ads_now={}, ads_prev={},
        items={42: {"en": 1}},
        ad_data_available=False,
    )
    assert rows[0]["ad_data_available"] is False
    assert rows[0]["spend"] is None
    assert rows[0]["roas"] is None
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k join_and_compute
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append:
```python
def _join_and_compute_dashboard_rows(
    *,
    products: dict[int, dict],
    orders_now: dict[int, dict],
    orders_prev: dict[int, dict],
    ads_now: dict[int, dict],
    ads_prev: dict[int, dict],
    items: dict[int, dict[str, int]],
    ad_data_available: bool,
) -> list[dict]:
    """合并 4 个数据源 + 媒体素材数 + 计算 ROAS / 环比百分比。
    决策 #12 剔除两边都 0 的产品。"""
    rows: list[dict] = []
    candidate_ids = set(orders_now.keys()) | set(ads_now.keys())
    for pid in candidate_ids:
        if pid not in products:
            # 产品已被删除/归档，跳过
            continue
        prod = products[pid]
        o_now = orders_now.get(pid, {})
        o_prev = orders_prev.get(pid, {})
        a_now = ads_now.get(pid, {})
        a_prev = ads_prev.get(pid, {})

        orders = int(o_now.get("orders") or 0)
        spend = float(a_now.get("spend") or 0)
        if orders == 0 and spend == 0:
            continue  # 决策 #12

        revenue = float(o_now.get("revenue") or 0)
        revenue_prev = float(o_prev.get("revenue") or 0)
        spend_prev = float(a_prev.get("spend") or 0)
        roas = (revenue / spend) if spend > 0 else None
        roas_prev = (revenue_prev / spend_prev) if spend_prev > 0 else None

        row = {
            "product_id": pid,
            "product_code": prod.get("product_code"),
            "product_name": prod.get("name"),
            "orders": orders,
            "orders_prev": int(o_prev.get("orders") or 0),
            "orders_pct": _compute_pct_change(orders, o_prev.get("orders")),
            "units": int(o_now.get("units") or 0),
            "units_prev": int(o_prev.get("units") or 0),
            "units_pct": _compute_pct_change(o_now.get("units"), o_prev.get("units")),
            "revenue": round(revenue, 2),
            "revenue_prev": round(revenue_prev, 2),
            "revenue_pct": _compute_pct_change(revenue, revenue_prev),
            "media_items_by_lang": items.get(pid, {}),
            "ad_data_available": ad_data_available,
        }
        if ad_data_available:
            row.update({
                "spend": round(spend, 2),
                "spend_prev": round(spend_prev, 2),
                "spend_pct": _compute_pct_change(spend, spend_prev),
                "meta_purchases": int(a_now.get("purchases") or 0),
                "meta_purchases_prev": int(a_prev.get("purchases") or 0),
                "meta_purchases_pct": _compute_pct_change(
                    a_now.get("purchases"), a_prev.get("purchases")
                ),
                "roas": round(roas, 2) if roas is not None else None,
                "roas_prev": round(roas_prev, 2) if roas_prev is not None else None,
                "roas_pct": _compute_pct_change(roas, roas_prev),
            })
        else:
            row.update({
                "spend": None, "spend_prev": None, "spend_pct": None,
                "meta_purchases": None, "meta_purchases_prev": None, "meta_purchases_pct": None,
                "roas": None, "roas_prev": None, "roas_pct": None,
            })
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k join_and_compute
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): _join_and_compute_dashboard_rows helper (V1 task 7/14)"
```

---

## Task 8: 主入口 `get_dashboard()`

**Files:**
- Modify: `tests/test_order_analytics_dashboard.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_get_dashboard_month_view_happy_path(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 12, "revenue": 500.0}
    } if s == date(2026, 4, 1) else {
        42: {"orders": 8, "units": 10, "revenue": 400.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 100.0, "purchases": 10, "purchase_value": 500.0}
    } if s == date(2026, 4, 1) else {
        42: {"spend": 80.0, "purchases": 8, "purchase_value": 400.0}
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1, "de": 2}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow Set", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="month", year=2026, month=4,
        today=date(2026, 4, 26), compare=True,
    )

    assert result["period"]["start"] == "2026-04-01"
    assert result["period"]["end"] == "2026-04-25"
    assert result["compare_period"]["start"] == "2026-03-01"
    assert result["compare_period"]["end"] == "2026-03-25"
    assert len(result["products"]) == 1
    assert result["products"][0]["product_id"] == 42
    assert result["products"][0]["roas"] == 5.0
    assert result["country"] is None
    assert result["summary"]["total_orders"] == 10
    assert result["summary"]["total_revenue"] == 500.0
    assert result["summary"]["total_spend"] == 100.0


def test_get_dashboard_day_view_no_ads_data(monkeypatch):
    """决策 #3: 日视图不显示广告。"""
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 3, "units": 3, "revenue": 90.0}
    })
    # _aggregate_ads_by_product 不该被调用
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: pytest.fail("should not be called"))
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow", "product_code": "glow-rjc"}
    ])
    import pytest

    result = oa.get_dashboard(period="day", date_str="2026-04-25", today=date(2026, 4, 26))
    assert result["products"][0]["ad_data_available"] is False
    assert result["products"][0]["spend"] is None


def test_get_dashboard_country_filter_drops_ads(monkeypatch):
    """决策 #8 + meta_ad 表无 country 字段：国家筛选启用时广告整列降级。"""
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    ad_called = []
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: ad_called.append(1) or {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="month", year=2026, month=4, country="DE",
        today=date(2026, 4, 26),
    )
    assert ad_called == []
    assert result["products"][0]["ad_data_available"] is False
    assert result["country"] == "DE"


def test_get_dashboard_search_filter(monkeypatch):
    """搜索按 product_code / name 过滤，仅传给 SQL，service 端不再做 in-memory filter。"""
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 42, "name": "Glow", "product_code": "glow-rjc"}]

    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", fake_query)

    oa.get_dashboard(period="month", year=2026, month=4, search="glow", today=date(2026, 4, 26))
    assert "name LIKE" in captured["sql"] or "product_code LIKE" in captured["sql"]
    assert "%glow%" in captured["args"]


def test_get_dashboard_default_sort_spend_desc_for_month(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 10, "revenue": 200.0},
        99: {"orders": 5, "units": 5, "revenue": 100.0},
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 50.0, "purchases": 5, "purchase_value": 100.0},
        99: {"spend": 200.0, "purchases": 10, "purchase_value": 500.0},
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en":1}, 99: {"en":1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "A", "product_code": "a"},
        {"id": 99, "name": "B", "product_code": "b"},
    ])

    result = oa.get_dashboard(period="month", year=2026, month=4, today=date(2026, 4, 26))
    # 默认按花费降序 → 99 在前
    assert [p["product_id"] for p in result["products"]] == [99, 42]
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v -k get_dashboard
```
Expected: FAIL (function not defined).

- [ ] **Step 3: Implementation**

Append:
```python
_DASHBOARD_SORT_FIELDS = {
    "spend": "spend", "revenue": "revenue", "orders": "orders",
    "units": "units", "roas": "roas",
}


def get_dashboard(
    *,
    period: str,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    country: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    compare: bool = True,
    search: str | None = None,
    today: date | None = None,
) -> dict:
    """产品看板查询主入口。详见 spec。"""
    today = today or date.today()
    start, end = _resolve_period_range(
        period, year=year, month=month, week=week, date_str=date_str, today=today
    )

    # 周/月支持广告；日视图不查广告（决策 #3）
    # 国家筛选启用时广告整列降级（meta_ad 表无 country 字段）
    ad_data_available = period in ("week", "month") and not country

    orders_now = _aggregate_orders_by_product(start, end, country=country)
    ads_now = _aggregate_ads_by_product(start, end) if ad_data_available else {}

    orders_prev: dict[int, dict] = {}
    ads_prev: dict[int, dict] = {}
    compare_period = None
    if compare:
        prev_start, prev_end = _resolve_compare_range(start, end, period)
        orders_prev = _aggregate_orders_by_product(prev_start, prev_end, country=country)
        ads_prev = _aggregate_ads_by_product(prev_start, prev_end) if ad_data_available else {}
        compare_period = {
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "label": _format_period_label(prev_start, prev_end, period),
        }

    items = _count_media_items_by_product()

    candidate_ids = set(orders_now.keys()) | set(ads_now.keys())
    products = _load_products(candidate_ids, search=search)

    rows = _join_and_compute_dashboard_rows(
        products=products,
        orders_now=orders_now, orders_prev=orders_prev,
        ads_now=ads_now, ads_prev=ads_prev,
        items=items,
        ad_data_available=ad_data_available,
    )

    # 排序
    sort_key = sort_by if sort_by in _DASHBOARD_SORT_FIELDS else (
        "spend" if ad_data_available else "revenue"
    )
    reverse = (sort_dir == "desc")
    rows.sort(key=lambda r: (r.get(sort_key) is None, r.get(sort_key) or 0), reverse=reverse)

    summary = _summarize_dashboard(rows, ad_data_available)

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": _format_period_label(start, end, period),
        },
        "compare_period": compare_period,
        "country": country,
        "products": rows,
        "summary": summary,
    }


def _format_period_label(start: date, end: date, period: str) -> str:
    if period == "month":
        if start.day == 1 and end.day == calendar.monthrange(start.year, start.month)[1]:
            return f"{start.year} 年 {start.month} 月"
        return f"{start.year} 年 {start.month} 月（{start.day}-{end.day} 日）"
    if period == "week":
        return f"{start.isoformat()} ~ {end.isoformat()}"
    return start.isoformat()


def _load_products(ids: set[int], *, search: str | None = None) -> dict[int, dict]:
    """查询产品基础信息。search 启用时按 name / product_code LIKE 过滤；
    不启用 search 时按 ids IN 限制（性能优化）。"""
    if search:
        like = f"%{search}%"
        rows = query(
            "SELECT id, name, product_code FROM media_products "
            "WHERE (archived = 0 OR archived IS NULL) AND deleted_at IS NULL "
            "AND (name LIKE %s OR product_code LIKE %s)",
            (like, like),
        )
    elif ids:
        placeholders = ", ".join(["%s"] * len(ids))
        rows = query(
            f"SELECT id, name, product_code FROM media_products "
            f"WHERE id IN ({placeholders})",
            tuple(ids),
        )
    else:
        rows = []
    return {int(r["id"]): r for r in rows}


def _summarize_dashboard(rows: list[dict], ad_data_available: bool) -> dict:
    total_orders = sum(r.get("orders") or 0 for r in rows)
    total_revenue = round(sum(r.get("revenue") or 0 for r in rows), 2)
    summary = {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
    }
    if ad_data_available:
        total_spend = round(sum(r.get("spend") or 0 for r in rows), 2)
        summary["total_spend"] = total_spend
        summary["total_meta_purchases"] = sum(r.get("meta_purchases") or 0 for r in rows)
        summary["total_roas"] = round(total_revenue / total_spend, 2) if total_spend > 0 else None
    else:
        summary["total_spend"] = None
        summary["total_meta_purchases"] = None
        summary["total_roas"] = None
    return summary
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_dashboard.py -v
```
Expected: 全部 passed（包括之前 7 个 task 的）。

- [ ] **Step 5: Commit**

```bash
git add tests/test_order_analytics_dashboard.py appcore/order_analytics.py
git commit -m "feat(dashboard): get_dashboard main entry (V1 task 8/14)"
```

---

## Task 9: 路由 `GET /order-analytics/dashboard`

**Files:**
- Modify: `web/routes/order_analytics.py`
- Modify: `tests/test_order_analytics_ads.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_order_analytics_ads.py`:
```python
def test_dashboard_endpoint_admin_only_redirects_when_anonymous():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    response = client.get("/order-analytics/dashboard")
    # 未登录 → 302 重定向到登录
    assert response.status_code in (302, 401)


def test_dashboard_endpoint_default_returns_json(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.order_analytics.oa.get_dashboard",
        lambda **kwargs: {
            "period": {"start": "2026-04-01", "end": "2026-04-25", "label": "2026 年 4 月（1-25 日）"},
            "compare_period": {"start": "2026-03-01", "end": "2026-03-25", "label": "..."},
            "country": None,
            "products": [],
            "summary": {"total_orders": 0, "total_revenue": 0, "total_spend": 0, "total_roas": None},
        },
    )
    response = authed_client_no_db.get("/order-analytics/dashboard?period=month&year=2026&month=4")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["period"]["start"] == "2026-04-01"
    assert payload["products"] == []


def test_dashboard_endpoint_invalid_period_returns_400(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics/dashboard?period=year")
    assert response.status_code == 400
    assert "invalid_period" in response.get_data(as_text=True)


def test_dashboard_endpoint_passes_country_filter(authed_client_no_db, monkeypatch):
    captured = {}
    def fake_dashboard(**kwargs):
        captured.update(kwargs)
        return {"period": {"start": "2026-04-01", "end": "2026-04-25", "label": "x"},
                "compare_period": None, "country": "DE", "products": [], "summary": {}}
    monkeypatch.setattr("web.routes.order_analytics.oa.get_dashboard", fake_dashboard)

    response = authed_client_no_db.get(
        "/order-analytics/dashboard?period=month&year=2026&month=4&country=DE"
    )
    assert response.status_code == 200
    assert captured["country"] == "DE"
    assert captured["period"] == "month"
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_ads.py -v -k dashboard
```
Expected: FAIL (endpoint not registered).

- [ ] **Step 3: Implementation**

In `web/routes/order_analytics.py`, add new route (recommend placing near `/order-analytics/stats`):
```python
@bp.route("/order-analytics/dashboard")
@login_required
@admin_required
def dashboard():
    """产品看板：每日产品级订单 + 广告 + ROAS + 环比。"""
    period = (request.args.get("period") or "month").strip().lower()
    if period not in ("day", "week", "month"):
        return jsonify(error="invalid_period",
                       detail="period must be one of day/week/month"), 400

    try:
        data = oa.get_dashboard(
            period=period,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
            week=request.args.get("week", type=int),
            date_str=request.args.get("date") or None,
            country=(request.args.get("country") or "").strip() or None,
            sort_by=(request.args.get("sort_by") or "").strip() or None,
            sort_dir=(request.args.get("sort_dir") or "desc").strip().lower(),
            compare=(request.args.get("compare") or "true").strip().lower() != "false",
            search=(request.args.get("search") or "").strip() or None,
        )
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("dashboard query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500

    return jsonify(_json_safe(data))
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_ads.py -v -k dashboard
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add web/routes/order_analytics.py tests/test_order_analytics_ads.py
git commit -m "feat(dashboard): GET /order-analytics/dashboard endpoint (V1 task 9/14)"
```

---

## Task 10: 模板 - 加 "产品看板" Tab（默认 Tab）

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `tests/test_order_analytics_ads.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_order_analytics_ads.py`:
```python
def test_dashboard_tab_is_default(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-tab="dashboard"' in body
    assert 'id="panelDashboard"' in body
    # 默认 Tab：dashboard 有 active class，import 没有
    # 实施时确认现有 active class 命名（搜 "is-active" 或 "active"）
    assert "panelDashboard" in body


def test_dashboard_tab_label_chinese(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert "产品看板" in response.get_data(as_text=True)
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_order_analytics_ads.py -v -k dashboard_tab
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Read first to find existing tab structure:
```bash
grep -n 'data-tab=' web/templates/order_analytics.html | head -10
```

Then in `web/templates/order_analytics.html`:
1. 找到 Tab 列表区域（含 `data-tab="ads"` 等），把"产品看板"加在**第一位**并标为 active
2. 找到 panel 区域（含 `id="panelAds"` 等），加 `<section id="panelDashboard">...` 在**第一位**
3. 把现有默认 active Tab（应为 "import" 或 "analysis"）的 active class 移到 dashboard

模板片段（根据现有 class 系统调整）：
```html
<!-- Tab 头 -->
<button class="oa-tab is-active" data-tab="dashboard">产品看板</button>
<button class="oa-tab" data-tab="import">订单导入</button>
<button class="oa-tab" data-tab="analysis">订单分析</button>
<button class="oa-tab" data-tab="ads">广告分析</button>

<!-- Panel -->
<section id="panelDashboard" class="oa-panel is-active">
  <!-- 工具栏 + 表格在 task 11/12/13 填入 -->
  <div id="dashboardToolbar"><!-- task 11 --></div>
  <div id="dashboardSummary"><!-- task 12 --></div>
  <div id="dashboardTable"><!-- task 12 --></div>
</section>
```

实施时根据 grep 出的现有 class 命名调整（`oa-tab` 可能叫别的）。**不要改其他 panel 的 active 状态切换 JS 逻辑**——只改默认渲染的 active class 标记。

- [ ] **Step 4: Run test**

```bash
pytest tests/test_order_analytics_ads.py -v -k dashboard_tab
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add web/templates/order_analytics.html tests/test_order_analytics_ads.py
git commit -m "feat(dashboard): add 产品看板 tab as default (V1 task 10/14)"
```

---

## Task 11: 模板 - 工具栏（粒度切换 / 时间选择 / 国家 / 搜索）

**Files:**
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: 编写工具栏 HTML**

在 task 10 创建的 `<div id="dashboardToolbar">` 内填入：
```html
<div id="dashboardToolbar" class="oad-toolbar">
  <div class="oad-toolbar-row">
    <div class="oad-segmented" role="tablist" aria-label="时间粒度">
      <button class="oad-seg is-active" data-period="month">月</button>
      <button class="oad-seg" data-period="week">周</button>
      <button class="oad-seg" data-period="day">日</button>
    </div>

    <div id="oadDatePicker" class="oad-datepicker">
      <!-- 月模式：年月双下拉 -->
      <div data-period-input="month">
        <select id="oadYear"></select>
        <select id="oadMonth"></select>
      </div>
      <!-- 周模式：年 + 周次 -->
      <div data-period-input="week" hidden>
        <select id="oadWeekYear"></select>
        <select id="oadWeek"></select>
      </div>
      <!-- 日模式：日历 -->
      <div data-period-input="day" hidden>
        <input type="date" id="oadDate" />
      </div>
    </div>

    <select id="oadCountry">
      <option value="">所有国家</option>
      <!-- 由 JS fetch 填充 -->
    </select>

    <input id="oadSearch" type="text" placeholder="产品名 / product_code" />

    <button id="oadRefresh" class="oad-btn-primary">刷新</button>
  </div>
</div>

<style>
  .oad-toolbar { padding: var(--space-4) 0; border-bottom: 1px solid var(--border); }
  .oad-toolbar-row { display: flex; gap: var(--space-3); align-items: center; flex-wrap: wrap; }
  .oad-segmented { display: inline-flex; border: 1px solid var(--border-strong);
                   border-radius: var(--radius-md); overflow: hidden; }
  .oad-seg { padding: 4px 12px; height: 28px; background: white; border: 0;
             border-right: 1px solid var(--border); cursor: pointer; }
  .oad-seg:last-child { border-right: 0; }
  .oad-seg.is-active { background: var(--accent); color: var(--accent-fg); }
  .oad-datepicker select, .oad-datepicker input,
  #oadCountry, #oadSearch { height: 32px; padding: 0 10px;
                            border: 1px solid var(--border-strong); border-radius: var(--radius); }
  .oad-btn-primary { height: 32px; padding: 0 16px; background: var(--accent);
                     color: var(--accent-fg); border: 0; border-radius: var(--radius); cursor: pointer; }
  .oad-btn-primary:hover { background: var(--accent-hover); }
</style>
```

- [ ] **Step 2: 编写工具栏 JS（控制状态 + 触发查询）**

在 panelDashboard 模板末尾加 `<script>`（或挂在已有 page-level JS 块里）：
```html
<script>
(function() {
  const oad = {
    state: {
      period: 'month',
      year: new Date().getFullYear(),
      month: new Date().getMonth() + 1,  // 1-12
      week: null,
      date: null,
      country: '',
      search: '',
      sort_by: '',
      sort_dir: 'desc',
    },
  };
  window.oad = oad;  // 暴露给后续 task 12/13 使用

  function fillYearMonth() {
    const ys = document.getElementById('oadYear');
    const ms = document.getElementById('oadMonth');
    const cur = new Date().getFullYear();
    for (let y = cur - 2; y <= cur; y++) {
      const o = new Option(y + ' 年', y); if (y === oad.state.year) o.selected = true; ys.add(o);
    }
    for (let m = 1; m <= 12; m++) {
      const o = new Option(m + ' 月', m); if (m === oad.state.month) o.selected = true; ms.add(o);
    }
  }

  function fillWeekYearAndWeeks() {
    const ys = document.getElementById('oadWeekYear');
    const ws = document.getElementById('oadWeek');
    const cur = new Date().getFullYear();
    for (let y = cur - 2; y <= cur; y++) {
      const o = new Option(y + ' 年', y); if (y === cur) o.selected = true; ys.add(o);
    }
    for (let w = 1; w <= 53; w++) {
      const o = new Option('第 ' + w + ' 周', w); ws.add(o);
    }
  }

  function fillDateInput() {
    const d = new Date(); d.setDate(d.getDate() - 1);  // 默认昨日
    const iso = d.toISOString().slice(0, 10);
    document.getElementById('oadDate').value = iso;
    oad.state.date = iso;
  }

  async function fillCountries() {
    // 复用现有 endpoint 或加新的；如不存在用 hardcoded fallback
    try {
      const r = await fetch('/order-analytics/countries');
      if (!r.ok) throw new Error();
      const list = await r.json();
      const sel = document.getElementById('oadCountry');
      list.forEach(c => sel.add(new Option(c, c)));
    } catch {
      // fallback: 不填，用户手输
    }
  }

  function switchPeriod(p) {
    oad.state.period = p;
    document.querySelectorAll('.oad-seg').forEach(b =>
      b.classList.toggle('is-active', b.dataset.period === p));
    document.querySelectorAll('[data-period-input]').forEach(d =>
      d.hidden = d.dataset.periodInput !== p);
    oad.refresh();
  }

  oad.refresh = async function() {
    const params = new URLSearchParams({
      period: oad.state.period,
      compare: 'true',
    });
    if (oad.state.period === 'month') {
      oad.state.year = parseInt(document.getElementById('oadYear').value);
      oad.state.month = parseInt(document.getElementById('oadMonth').value);
      params.set('year', oad.state.year);
      params.set('month', oad.state.month);
    } else if (oad.state.period === 'week') {
      oad.state.year = parseInt(document.getElementById('oadWeekYear').value);
      oad.state.week = parseInt(document.getElementById('oadWeek').value);
      params.set('year', oad.state.year);
      params.set('week', oad.state.week);
    } else {
      oad.state.date = document.getElementById('oadDate').value;
      params.set('date', oad.state.date);
    }
    if (oad.state.country) params.set('country', oad.state.country);
    if (oad.state.search) params.set('search', oad.state.search);
    if (oad.state.sort_by) {
      params.set('sort_by', oad.state.sort_by);
      params.set('sort_dir', oad.state.sort_dir);
    }

    oad.renderLoading();
    try {
      const r = await fetch('/order-analytics/dashboard?' + params.toString());
      if (!r.ok) {
        const err = await r.json().catch(() => ({error: 'unknown'}));
        throw new Error(err.detail || err.error);
      }
      const data = await r.json();
      oad.renderTable(data);
    } catch (e) {
      oad.renderError(e.message);
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    fillYearMonth();
    fillWeekYearAndWeeks();
    fillDateInput();
    fillCountries();
    document.querySelectorAll('.oad-seg').forEach(b =>
      b.addEventListener('click', () => switchPeriod(b.dataset.period)));
    ['oadYear','oadMonth','oadWeekYear','oadWeek','oadDate'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => oad.refresh());
    });
    document.getElementById('oadCountry').addEventListener('change', e => {
      oad.state.country = e.target.value; oad.refresh();
    });
    document.getElementById('oadSearch').addEventListener('keydown', e => {
      if (e.key === 'Enter') { oad.state.search = e.target.value.trim(); oad.refresh(); }
    });
    document.getElementById('oadRefresh').addEventListener('click', () => oad.refresh());
    oad.refresh();  // 初次加载
  });
})();
</script>
```

注意：`renderTable` / `renderLoading` / `renderError` 由 task 12 / 13 实现，这里先占位调用。Task 11 只验证工具栏 HTML 已写入。

- [ ] **Step 3: 添加 placeholder render 函数避免 task 11 单跑时控制台报错**

在工具栏 JS 末尾（仍在 IIFE 内）加：
```javascript
oad.renderLoading = function() {
  document.getElementById('dashboardTable').innerHTML = '<div class="oad-loading">加载中…</div>';
};
oad.renderError = function(msg) {
  document.getElementById('dashboardTable').innerHTML =
    '<div class="oad-error">加载失败：' + (msg || '未知错误') + '</div>';
};
oad.renderTable = function(data) {
  // task 12 覆盖
  document.getElementById('dashboardTable').innerHTML =
    '<pre>' + JSON.stringify(data.products, null, 2) + '</pre>';
};
```

- [ ] **Step 4: 手工验证（无自动测试）**

启动测试服务器并访问 `/order-analytics`：
```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  "cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test"
```
浏览器访问 `https://test.[domain]/order-analytics` 确认：
- 默认 Tab = 产品看板
- 工具栏 4 个 segmented + 选择器 + 国家 + 搜索 + 刷新 全部渲染
- 切换月/周/日，对应输入显示/隐藏
- 月份选择变化 → 控制台看到 fetch 请求带正确参数

- [ ] **Step 5: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "feat(dashboard): toolbar (period switcher, date picker, country, search) (V1 task 11/14)"
```

---

## Task 12: 模板 - 表格 + 环比箭头 + 排序

**Files:**
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: 编写表格 HTML 骨架**

在 `<section id="panelDashboard">` 内的 `<div id="dashboardSummary">` + `<div id="dashboardTable">` 处填入容器（已在 task 10 加过空容器）。这个 task 只用 JS 渲染，不需要静态 HTML。

但要补充 CSS：
```html
<style>
  .oad-summary { display: flex; gap: var(--space-6); padding: var(--space-4); 
                 background: var(--bg-subtle); border-radius: var(--radius-md);
                 margin: var(--space-4) 0; }
  .oad-summary-item { font-size: var(--text-sm); color: var(--fg-muted); }
  .oad-summary-item strong { display: block; font-size: var(--text-xl);
                              color: var(--fg); margin-bottom: 2px; }
  .oad-table { width: 100%; border-collapse: collapse; }
  .oad-table th, .oad-table td { padding: 10px var(--space-3); text-align: left;
                                  border-bottom: 1px solid var(--border);
                                  font-size: var(--text-sm); }
  .oad-table thead th { background: var(--bg-subtle); cursor: pointer;
                         user-select: none; font-weight: 600; }
  .oad-table thead th:hover { background: var(--bg-muted); }
  .oad-pct-up { color: var(--success); }
  .oad-pct-down { color: var(--danger); }
  .oad-pct-flat { color: var(--fg-subtle); }
  .oad-cell-empty { color: var(--fg-subtle); }
  .oad-loading, .oad-error { padding: var(--space-6); text-align: center;
                              color: var(--fg-muted); }
  .oad-error { color: var(--danger-fg); background: var(--danger-bg);
                border-radius: var(--radius-md); }
  .oad-empty { padding: var(--space-7); text-align: center; color: var(--fg-muted); }
  .oad-items-tag { display: inline-block; padding: 2px 6px; border-radius: var(--radius-sm);
                    background: var(--bg-muted); font-size: var(--text-xs);
                    margin-right: 2px; }
  .oad-row-actions { display: flex; gap: 4px; }
  .oad-row-actions a { font-size: var(--text-xs); padding: 4px 8px;
                        border-radius: var(--radius-sm); border: 1px solid var(--border-strong);
                        color: var(--accent); text-decoration: none; }
  .oad-row-actions a:hover { background: var(--bg-muted); }
</style>
```

- [ ] **Step 2: 替换 placeholder render 函数**

把 task 11 的 `oad.renderTable` placeholder 替换为完整实现：
```javascript
function fmtPct(pct) {
  if (pct === null || pct === undefined) return '<span class="oad-pct-flat">-</span>';
  if (pct === 0) return '<span class="oad-pct-flat">0%</span>';
  if (pct > 0) return '<span class="oad-pct-up">↑' + pct.toFixed(1) + '%</span>';
  return '<span class="oad-pct-down">↓' + Math.abs(pct).toFixed(1) + '%</span>';
}

function fmtMoney(v) {
  if (v === null || v === undefined) return '<span class="oad-cell-empty">-</span>';
  return '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function fmtNum(v) {
  if (v === null || v === undefined) return '<span class="oad-cell-empty">-</span>';
  return Number(v).toLocaleString('en-US');
}

function fmtRoas(v) {
  if (v === null || v === undefined) return '<span class="oad-cell-empty">-</span>';
  return Number(v).toFixed(2);
}

function fmtItems(map) {
  if (!map || Object.keys(map).length === 0) return '<span class="oad-cell-empty">无</span>';
  return Object.entries(map).map(([lang, n]) =>
    '<span class="oad-items-tag">' + lang.toUpperCase() + '×' + n + '</span>').join('');
}

oad.renderTable = function(data) {
  const tableEl = document.getElementById('dashboardTable');
  const summaryEl = document.getElementById('dashboardSummary');

  // 顶部 summary
  const s = data.summary || {};
  summaryEl.innerHTML = `
    <div class="oad-summary">
      <div class="oad-summary-item"><strong>${fmtNum(s.total_orders)}</strong>订单</div>
      <div class="oad-summary-item"><strong>${fmtMoney(s.total_revenue)}</strong>收入</div>
      <div class="oad-summary-item"><strong>${fmtMoney(s.total_spend)}</strong>花费</div>
      <div class="oad-summary-item"><strong>${fmtRoas(s.total_roas)}</strong>ROAS</div>
    </div>
    <div style="font-size: var(--text-xs); color: var(--fg-muted); padding: 0 var(--space-4);">
      时段: ${data.period.label}
      ${data.compare_period ? '· 对比: ' + data.compare_period.label : ''}
      ${data.country ? '· 国家: ' + data.country : ''}
    </div>
  `;

  if (!data.products || data.products.length === 0) {
    tableEl.innerHTML = '<div class="oad-empty">该时段暂无产品有订单或投放</div>';
    return;
  }

  // 表头（列点击切换排序）
  const SORTABLE = {orders:'orders', units:'units', revenue:'revenue',
                    spend:'spend', roas:'roas'};
  const adAvailable = data.products[0].ad_data_available;
  const headers = [
    {key: null, label: '产品'},
    {key: 'orders', label: '订单'},
    {key: 'units', label: '件数'},
    {key: 'revenue', label: '收入'},
    {key: 'spend', label: '花费', adOnly: true},
    {key: null, label: 'Meta 购买', adOnly: true},
    {key: 'roas', label: 'ROAS', adOnly: true},
    {key: null, label: '素材'},
    {key: null, label: '操作'},
  ];

  const thead = headers
    .filter(h => !h.adOnly || adAvailable)
    .map(h => {
      if (!h.key) return `<th>${h.label}</th>`;
      const arrow = oad.state.sort_by === h.key
        ? (oad.state.sort_dir === 'desc' ? ' ↓' : ' ↑') : '';
      return `<th data-sort="${h.key}">${h.label}${arrow}</th>`;
    }).join('');

  const tbody = data.products.map(p => {
    const cells = [
      `<td><div style="font-weight:500">${p.product_name || '(no name)'}</div>
           <div style="font-size:var(--text-xs);color:var(--fg-subtle)">${p.product_code || ''}</div></td>`,
      `<td>${fmtNum(p.orders)} ${fmtPct(p.orders_pct)}</td>`,
      `<td>${fmtNum(p.units)} ${fmtPct(p.units_pct)}</td>`,
      `<td>${fmtMoney(p.revenue)} ${fmtPct(p.revenue_pct)}</td>`,
    ];
    if (adAvailable) {
      cells.push(
        `<td>${fmtMoney(p.spend)} ${fmtPct(p.spend_pct)}</td>`,
        `<td>${fmtNum(p.meta_purchases)} ${fmtPct(p.meta_purchases_pct)}</td>`,
        `<td>${fmtRoas(p.roas)} ${fmtPct(p.roas_pct)}</td>`,
      );
    }
    cells.push(
      `<td>${fmtItems(p.media_items_by_lang)}</td>`,
      `<td><div class="oad-row-actions" data-pid="${p.product_id}"
              data-pcode="${p.product_code || ''}">
            <a href="#" data-action="orders">订单</a>
            <a href="#" data-action="ads">广告</a>
            <a href="#" data-action="medias">素材</a>
        </div></td>`
    );
    return `<tr>${cells.join('')}</tr>`;
  }).join('');

  tableEl.innerHTML = `
    <table class="oad-table">
      <thead><tr>${thead}</tr></thead>
      <tbody>${tbody}</tbody>
    </table>
  `;

  // 表头排序点击
  tableEl.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (oad.state.sort_by === key) {
        oad.state.sort_dir = oad.state.sort_dir === 'desc' ? 'asc' : 'desc';
      } else {
        oad.state.sort_by = key;
        oad.state.sort_dir = 'desc';
      }
      oad.refresh();
    });
  });
  // 操作按钮 — 由 task 13 实现 click handler
};
```

- [ ] **Step 3: 手工验证**

部署到测试服务器（同 task 11 命令）→ 浏览器访问 → 确认：
- 表格正常渲染产品行
- 环比 ↑ ↓ 颜色正确（绿/红/灰）
- 列头点击排序，再点反向
- 国家选 DE 时，广告/Meta/ROAS 列**消失**（adAvailable=false）
- 空数据显示"该时段暂无产品..."
- 操作列 3 个按钮已渲染（点击暂无反应，task 13 加 handler）

- [ ] **Step 4: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "feat(dashboard): table render with pct change arrows and sort (V1 task 12/14)"
```

---

## Task 13: 模板 - 操作列按钮跳转 + 错误态完善

**Files:**
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: 添加按钮 click handler**

在 `oad.renderTable` 末尾加：
```javascript
  // 操作按钮跳转
  tableEl.querySelectorAll('.oad-row-actions a').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const root = a.closest('.oad-row-actions');
      const pid = root.dataset.pid;
      const action = a.dataset.action;
      if (action === 'orders') {
        // 切到订单分析 Tab，预填月份
        document.querySelector('[data-tab="analysis"]').click();
        const ev = new CustomEvent('oad-jump-orders',
          {detail: {product_id: pid, year: oad.state.year, month: oad.state.month}});
        document.dispatchEvent(ev);
      } else if (action === 'ads') {
        document.querySelector('[data-tab="ads"]').click();
        const ev = new CustomEvent('oad-jump-ads', {detail: {product_id: pid}});
        document.dispatchEvent(ev);
      } else if (action === 'medias') {
        // 跳到素材库
        window.open('/medias?product_id=' + pid, '_blank');
      }
    });
  });
}; // 结束 oad.renderTable
```

注意：现有"订单分析" / "广告分析" Tab 是否监听 `oad-jump-orders` / `oad-jump-ads` 事件**未做集成**——V1 仅做"切 Tab"，参数预填留作 V2。如果切 Tab 时已有逻辑刷新数据，效果是用户在新 Tab 里看到默认筛选数据，再自己手动筛产品。**这是预期的 V1 行为**（spec 6.4 已明确：跳转目标不支持预填时不强加接口）。

如果想 V1 就支持预填，可以在订单分析 / 广告分析 Tab 现有 JS 中监听这两个事件：
```javascript
// 在订单分析 Tab JS 中（实施时 grep 现有逻辑加）
document.addEventListener('oad-jump-orders', e => {
  const {product_id, year, month} = e.detail;
  // 调用现有 fetchMonthly(year, month, product_id)
});
```

是否做这一步**留给 task 14 验收时定**：如果预填很简单（现有 Tab 已有 setProductFilter 等接口），加上；否则 V1 不做。

- [ ] **Step 2: 完善错误态 + 加载态 UI**

替换 task 11 的 placeholder 实现：
```javascript
oad.renderLoading = function() {
  document.getElementById('dashboardTable').innerHTML =
    '<div class="oad-loading">加载中…</div>';
};
oad.renderError = function(msg) {
  document.getElementById('dashboardTable').innerHTML =
    `<div class="oad-error">
       加载失败：${msg || '未知错误'}
       <button id="oadRetry" style="margin-left:12px" class="oad-btn-primary">重试</button>
     </div>`;
  const btn = document.getElementById('oadRetry');
  if (btn) btn.addEventListener('click', () => oad.refresh());
};
```

- [ ] **Step 3: 手工验证**

部署 + 访问，确认：
- 点"订单"按钮 → Tab 切到"订单分析"（即使无预填也算 V1 通过）
- 点"广告"按钮 → Tab 切到"广告分析"
- 点"素材"按钮 → 新窗口打开 `/medias?product_id=...`
- 模拟错误：手工把后端 endpoint 暂时改返回 500 → 看到红色错误条 + 重试按钮 → 点重试可恢复

- [ ] **Step 4: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "feat(dashboard): row action buttons + error state with retry (V1 task 13/14)"
```

---

## Task 14: 端到端手工验收

**Files:** 无代码改动，仅验证。

- [ ] **Step 1: 部署到测试服务器**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -m pytest tests/test_order_analytics_dashboard.py tests/test_order_analytics_ads.py -q 2>&1 | tail -20'
```

期望：所有测试通过。

- [ ] **Step 2: 浏览器测试 happy path**

访问测试服务器的 `/order-analytics` URL（admin 登录）：

1. 默认 Tab = 产品看板，工具栏可见，月视图选当前月
2. 表格自动加载，至少 1 个产品行（如本月有数据）
3. 切换月份选其它月 → 表格刷新，环比指向上月
4. 切换到"周"粒度 → 输入区切到周次选择，刷新
5. 切换到"日"粒度 → 输入切到日历，刷新；广告/Meta/ROAS 列**消失**
6. 切回月，国家筛选选 DE → 广告列消失（meta_ad 表无国家字段，决策 #8）
7. 搜索"glow" → 只剩匹配的产品行
8. 列头点"订单"列 → 按订单数排序；再点反向
9. 操作列 3 按钮：
   - 订单 → 切到"订单分析" Tab
   - 广告 → 切到"广告分析" Tab
   - 素材 → 新窗口 `/medias`
10. 现有"订单导入" / "订单分析" / "广告分析" Tab **不受影响**

- [ ] **Step 3: 边界场景测试**

- 选未来月份（如 2027-01）→ "该时段暂无产品有订单或投放" 空态
- 选 1 年前（已无广告报表）→ 月视图广告列显示 "-"，订单正常
- 关掉网络再点刷新 → 红色错误条 + 重试按钮

- [ ] **Step 4: 完成报告 + Commit**

记录验收结果到 spec 末尾或在本 plan 加一个 "## 验收" 节：

```markdown
## 验收（2026-XX-XX）

- 部署：测试服务器 172.30.254.14
- 测试：pytest 全通过，N 个测试用例
- 手工 happy path：✅ 全部通过
- 边界场景：✅ 全部通过
- 已知 V1 限制：
  - 操作列跳转后不预填筛选条件（V2 待加）
  - 国家筛选启用时广告整列降级（meta_ad 表 schema 限制）
  - 当月数据 = 截至昨日（不含今天，spec 决策 #11）
```

```bash
git add docs/superpowers/plans/2026-04-26-product-dashboard.md
git commit -m "docs(dashboard): V1 acceptance complete (V1 task 14/14)"
```

---

## Self-Review Checklist

执行实施前、实施完成后各跑一次：

### Spec 覆盖
- [x] 决策 #1 产品级 → task 7/8 join 与 SQL 都按 product_id 聚合
- [x] 决策 #2 三档粒度 → task 2 _resolve_period_range
- [x] 决策 #3 日视图无广告 → task 8 get_dashboard 中 `ad_data_available = period in ('week','month')`
- [x] 决策 #4 周/月含 ROAS → task 8 同上
- [x] 决策 #5 默认 Tab → task 10
- [x] 决策 #6 环比 → task 1 + 3 + 7 + 8
- [x] 决策 #7 完全覆盖 → task 5 SQL `report_start_date >= AND report_end_date <=`
- [x] 决策 #8 国家筛选 → task 4 SQL + task 8 ads 降级逻辑
- [x] 决策 #9 默认排序 → task 8 fallback 逻辑
- [x] 决策 #10 不新增表 → 全 plan 无 migration
- [x] 决策 #11 默认本月（截至昨日）→ task 2
- [x] 决策 #12 两边 0 排除 → task 7
- [x] 决策 #13 ROAS = 收入 / 花费 → task 7 测试 `test_join_and_compute_roas_uses_shopify_revenue_over_spend`

### 类型一致性
- service 函数名：`get_dashboard` / `_resolve_period_range` / `_resolve_compare_range` / `_aggregate_orders_by_product` / `_aggregate_ads_by_product` / `_count_media_items_by_product` / `_join_and_compute_dashboard_rows` / `_compute_pct_change` / `_format_period_label` / `_load_products` / `_summarize_dashboard` — 全 plan 一致
- DB 字段名：`shopify_orders.created_at_order`, `billing_country`, `lineitem_quantity`, `lineitem_price`, `shopify_order_id`, `product_id` — 全 plan 一致
- meta_ad 字段：`spend_usd`, `purchase_value_usd`, `result_count`, `report_start_date`, `report_end_date`, `product_id` — 全 plan 一致
- 前端命名空间：`oad*` — 全 plan 一致
- 前端关键 ID：`panelDashboard`, `dashboardToolbar`, `dashboardSummary`, `dashboardTable`, `oadYear` 等 — 全 plan 一致

### Placeholder 扫描
- 没有 TBD / TODO / "implement later"
- task 11 中 `renderTable` 占位 → task 12 替换；task 13 进一步覆盖 — 显式标注 transition，不算 placeholder
- task 13 的"预填留作 V2"是显式 V1 取舍，不是 placeholder
