# 产品盈亏看板 4 Tab 改造 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/product-profit` 重构为 4 Tab 单页（产品列表 / 订单明细 / 国家看板 / 广告明细），保留现有所有数据视图，新增全产品聚合 + 广告 campaign 明细。

**Architecture:** 后端新增 2 个聚合模块（`product_profit_list.py` / `product_profit_ads.py`）+ 4 个 REST 端点；前端在现有 `product_profit_dashboard.html` 基础上加 Tab 切换 UI，把现有 5 个区块完整迁入 Tab ②，新增 Tab ① ③ ④ 渲染。所有改动集中在 `feature/product-profit-tabs-redesign` worktree。

**Tech Stack:** Python (Flask), SQL (MySQL), Vanilla JS + Jinja2, Ocean Blue Admin design tokens, pytest。

**Spec:** [docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md](../specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md)

---

## 任务总览

| # | 任务 | 文件 |
|---|---|---|
| 1 | 后端 - 产品列表聚合 | `appcore/order_analytics/product_profit_list.py` (新) |
| 2 | 后端 - `/list.json` + `/list.xlsx` 端点 | `web/routes/product_profit_report.py` (改) |
| 3 | 后端 - 广告明细聚合 | `appcore/order_analytics/product_profit_ads.py` (新) |
| 4 | 后端 - `/ads.json` + `/ads/manual-match` 端点 | `web/routes/product_profit_report.py` (改) |
| 5 | 前端 - Tab 切换骨架 | `web/templates/product_profit_dashboard.html` (改) |
| 6 | 前端 - Tab ② 包裹现有 5 区块 + 全局筛选 | 同上 |
| 7 | 前端 - Tab ① 产品列表 | 同上 |
| 8 | 前端 - Tab ③ 国家看板增强 | 同上 |
| 9 | 前端 - Tab ④ 广告明细 | 同上 |
| 10 | 前端 - URL state 同步 + 国家筛选下拉填充 | 同上 |
| 11 | 视觉收尾 + 响应式 | 同上 |
| 12 | 端到端验证 + 部署测试环境 | — |
| 13 | 前端跟进 - 产品选择 modal | `web/templates/product_profit_dashboard.html` + `tests/test_product_profit_dashboard_assets.py` |

每个 Task 走 TDD：先写失败测试 → 运行确认失败 → 实现 → 运行确认通过 → commit。

## Task 13: 前端跟进 — 产品选择 modal

**Files:**
- Modify: `web/templates/product_profit_dashboard.html`
- Modify: `tests/test_product_profit_dashboard_assets.py`
- Spec anchor: `docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md#5-顶部全局筛选条`

- [ ] **Step 13.1: 写失败测试**

在 `tests/test_product_profit_dashboard_assets.py` 中断言模板包含产品 modal DOM、搜索框、打开后 focus、空搜索展示全部产品、按 `product_code` 排序，顶部产品输入框桌面端 `min-width: 480px`，以及选具体产品后跳「订单明细」、选「全部产品」后回「产品列表」。

- [ ] **Step 13.2: 运行测试确认失败**

Run: `pytest tests/test_product_profit_dashboard_assets.py -q`

Expected: 当前模板仍使用 `datalist`，缺少 modal 片段，测试失败。

- [ ] **Step 13.3: 实现最小前端改动**

把顶部产品输入框改为只读 modal trigger；新增 modal DOM/CSS；在 `loadProducts()` 中保留 `productLabelToId` / `productCodeToLabel` 映射，同时维护 `productPickerItems`，按 `product_code` 排序后渲染；实现 `openProductModal()` / `closeProductModal()` / `renderProductPickerResults()` / `selectProductFromPicker()`。`selectProductFromPicker()` 选中具体产品时调用 `switchTab('orders')`，选中「全部产品」时调用 `switchTab('list')`。

- [ ] **Step 13.4: 运行测试确认通过**

Run: `pytest tests/test_product_profit_dashboard_assets.py tests/test_product_profit_routes.py -q`

Expected: 模板资产测试和产品盈亏路由测试全部通过。

---

## Task 1: 后端 — 产品列表聚合 `product_profit_list.py`

**Files:**
- Create: `appcore/order_analytics/product_profit_list.py`
- Test: `tests/test_product_profit_list.py`
- Reference (read only): `appcore/order_analytics/product_profit_report.py` (DB facade pattern + 字段口径), `appcore/order_analytics/cost_completeness.py` (`check_sku_cost_completeness`)

### Step 1.1: 写失败测试 — 空数据返回空 rows + 0 summary

- [ ] 创建 `tests/test_product_profit_list.py`：

```python
"""产品盈亏列表（全产品聚合）测试。"""
from datetime import date
from unittest.mock import patch

from appcore.order_analytics import product_profit_list as ppl


def test_generate_list_empty_returns_empty_rows():
    """无订单数据 → rows=[]，summary 全 0。"""
    with patch.object(ppl, "query") as q, patch.object(ppl, "query_one") as q1:
        q.return_value = []
        q1.return_value = None
        result = ppl.generate_list(
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
            country=None,
        )
    assert result["rows"] == []
    assert result["summary"]["product_count"] == 0
    assert result["summary"]["total_revenue_usd"] == 0
    assert result["summary"]["total_profit_usd"] == 0
    assert result["summary"]["overall_roas"] is None
```

### Step 1.2: 运行测试确认失败

```bash
cd g:/Code/AutoVideoSrtLocal-product-profit-tabs
pytest tests/test_product_profit_list.py -v
```
Expected: `ModuleNotFoundError: No module named 'appcore.order_analytics.product_profit_list'`

### Step 1.3: 创建 module 骨架 + 复制 facade 模式

- [ ] 创建 `appcore/order_analytics/product_profit_list.py`：

```python
"""产品盈亏列表（全产品聚合，给 Tab ① 用）。

按日期范围 + 国家维度聚合每个 media_product，输出每个产品的：
订单数 / 收入 / 物流费 / 采购费 / 广告费 / ROAS / 利润 / 利润率 / 成本完备性。

数据口径与 product_profit_report.generate_report() 单产品口径完全一致：
- 订单 / 收入 / 各项费用：order_profit_lines + dianxiaomi_order_lines JOIN
- 广告费：按订单 site_code → ad_account 1:1 映射，按当日 units 分摊
- 利润 = revenue - shopify_fee - ad_cost - purchase - shipping - return_reserve
"""
from __future__ import annotations

import io
import logging
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .cost_completeness import check_sku_cost_completeness
from .product_profit_report import SITE_TO_AD_ACCOUNT

log = logging.getLogger(__name__)


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def generate_list(
    *,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> dict[str, Any]:
    """生成全产品聚合列表 + summary。

    Args:
        date_from / date_to: 日期范围（按 order_profit_lines.business_date 过滤）
        country: 可选国家过滤（buyer_country）；None / "" / "all" 视为不过滤

    Returns:
        {
          "rows": [
            {
              "product_id": int, "product_code": str, "name": str,
              "order_count": int,
              "revenue_usd": float,
              "shipping_cost_usd": float, "shipping_pct": float,
              "purchase_usd": float, "purchase_pct": float,
              "ad_cost_usd": float, "ad_pct": float,
              "roas": float | None,
              "profit_usd": float, "profit_pct": float,
              "cost_completeness": "ok" | "incomplete" | "partial",
            }, ...
          ],
          "summary": {
            "product_count": int,
            "total_orders": int,
            "total_revenue_usd": float,
            "total_profit_usd": float,
            "overall_roas": float | None,
          }
        }
    """
    rows: list[dict[str, Any]] = []
    summary = {
        "product_count": 0,
        "total_orders": 0,
        "total_revenue_usd": 0.0,
        "total_profit_usd": 0.0,
        "overall_roas": None,
    }
    return {"rows": rows, "summary": summary}
```

### Step 1.4: 运行测试确认通过

```bash
pytest tests/test_product_profit_list.py::test_generate_list_empty_returns_empty_rows -v
```
Expected: PASS

### Step 1.5: 写失败测试 — 单产品聚合产出 9 列字段

- [ ] 在测试文件追加：

```python
def test_generate_list_single_product_aggregates_columns():
    """单产品 + 2 笔订单 → 9 列字段全部正确（订单数 / 收入 / 各项费用 / 占比 / ROAS / 利润 / 完备）。"""
    fake_lines = [
        {
            "product_id": 100, "product_code": "ABC", "name": "Test Product",
            "business_date": date(2026, 5, 5),
            "buyer_country": "VN", "site_code": "newjoy",
            "revenue_usd": Decimal("50.00"), "shopify_fee_usd": Decimal("2.00"),
            "purchase_usd": Decimal("10.00"), "shipping_cost_usd": Decimal("3.00"),
            "return_reserve_usd": Decimal("0.50"),
            "quantity": 1,
        },
        {
            "product_id": 100, "product_code": "ABC", "name": "Test Product",
            "business_date": date(2026, 5, 6),
            "buyer_country": "VN", "site_code": "newjoy",
            "revenue_usd": Decimal("50.00"), "shopify_fee_usd": Decimal("2.00"),
            "purchase_usd": Decimal("10.00"), "shipping_cost_usd": Decimal("3.00"),
            "return_reserve_usd": Decimal("0.50"),
            "quantity": 1,
        },
    ]
    fake_ads = {
        # (date, ad_account_id) → spend_usd
        (date(2026, 5, 5), "2110407576446225"): Decimal("8.00"),
        (date(2026, 5, 6), "2110407576446225"): Decimal("8.00"),
    }
    fake_product_costs = {
        100: {"purchase_price": Decimal("3.00"), "packet_cost_actual": Decimal("1.50")},
    }
    # patch 的细节按实现暴露的内部 helper 决定（_load_lines / _load_ad_spend / _load_product_costs）
    with patch.object(ppl, "_load_lines", return_value=fake_lines), \
         patch.object(ppl, "_load_ad_spend", return_value=fake_ads), \
         patch.object(ppl, "_load_product_costs", return_value=fake_product_costs):
        result = ppl.generate_list(
            date_from=date(2026, 5, 1), date_to=date(2026, 5, 7), country=None,
        )
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["product_id"] == 100
    assert row["product_code"] == "ABC"
    assert row["order_count"] == 2
    assert row["revenue_usd"] == 100.0
    assert row["ad_cost_usd"] == 16.0      # 全产品占用全站广告
    assert row["roas"] == 100.0 / 16.0
    # profit = 100 - 4 - 16 - 20 - 6 - 1 = 53
    assert row["profit_usd"] == 53.0
    assert row["cost_completeness"] == "ok"
    assert result["summary"]["product_count"] == 1
    assert result["summary"]["total_revenue_usd"] == 100.0
    assert result["summary"]["overall_roas"] == 100.0 / 16.0
```

### Step 1.6: 运行测试确认失败

```bash
pytest tests/test_product_profit_list.py::test_generate_list_single_product_aggregates_columns -v
```
Expected: FAIL（`AttributeError: module ... has no attribute '_load_lines'`）

### Step 1.7: 实现核心聚合逻辑

- [ ] 在 `product_profit_list.py` 的 `generate_list()` 之前加三个 loader：

```python
def _load_lines(date_from: date, date_to: date, country: str | None) -> list[dict[str, Any]]:
    """加载日期范围内所有产品的订单行（不限定单一 product_id）。"""
    sql = (
        "SELECT "
        "  opl.product_id, mp.product_code, mp.name, "
        "  opl.business_date, opl.buyer_country, "
        "  opl.revenue_usd, opl.shopify_fee_usd, opl.purchase_usd, "
        "  opl.shipping_cost_usd, opl.return_reserve_usd, "
        "  dol.site_code, dol.quantity "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "JOIN media_products mp ON mp.id = opl.product_id "
        "WHERE opl.business_date BETWEEN %s AND %s "
    )
    params: list[Any] = [date_from, date_to]
    if country and country.lower() not in ("", "all"):
        sql += " AND opl.buyer_country = %s "
        params.append(country.upper())
    sql += " ORDER BY opl.product_id, opl.business_date"
    return query(sql, tuple(params))


def _load_ad_spend(date_from: date, date_to: date) -> dict[tuple[date, str], Decimal]:
    """日期 × 广告账户 → spend_usd。"""
    rows = query(
        "SELECT date, ad_account_id, SUM(spend_usd) AS spend "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE date BETWEEN %s AND %s "
        "GROUP BY date, ad_account_id",
        (date_from, date_to),
    )
    return {(r["date"], r["ad_account_id"]): Decimal(r["spend"] or 0) for r in rows}


def _load_product_costs(product_ids: list[int]) -> dict[int, dict[str, Any]]:
    """加载产品成本字段（用于 cost_completeness 检查）。"""
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT id, purchase_price, packet_cost_actual, packet_cost_estimated "
        f"FROM media_products WHERE id IN ({placeholders})",
        tuple(product_ids),
    )
    return {r["id"]: r for r in rows}


def _load_site_units(date_from: date, date_to: date) -> dict[tuple[date, str], int]:
    """每天 × 每站点的全产品总 units，用于按 units 比例分摊广告费。

    与 product_profit_report._load_site_daily_units 的差别：这里是全产品维度，
    不限定 product_id。
    """
    rows = query(
        "SELECT opl.business_date, dol.site_code, SUM(dol.quantity) AS units "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.business_date BETWEEN %s AND %s "
        "GROUP BY opl.business_date, dol.site_code",
        (date_from, date_to),
    )
    return {(r["business_date"], r["site_code"]): int(r["units"] or 0) for r in rows}
```

### Step 1.8: 实现 generate_list 聚合主体

- [ ] 替换 `generate_list()` body 为：

```python
def generate_list(*, date_from, date_to, country=None):
    lines = _load_lines(date_from, date_to, country)
    if not lines:
        return {
            "rows": [],
            "summary": {
                "product_count": 0, "total_orders": 0,
                "total_revenue_usd": 0.0, "total_profit_usd": 0.0,
                "overall_roas": None,
            },
        }

    ad_spend = _load_ad_spend(date_from, date_to)
    site_units = _load_site_units(date_from, date_to)
    product_ids = list({l["product_id"] for l in lines})
    product_costs = _load_product_costs(product_ids)

    # 按产品分组聚合
    by_product: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "product_id": 0, "product_code": "", "name": "",
        "order_keys": set(),  # (dxm_package_id) 去重计数订单
        "revenue": Decimal("0"), "shopify_fee": Decimal("0"),
        "purchase": Decimal("0"), "shipping_cost": Decimal("0"),
        "return_reserve": Decimal("0"),
        "ad_cost": Decimal("0"),
    })

    for line in lines:
        pid = line["product_id"]
        bucket = by_product[pid]
        bucket["product_id"] = pid
        bucket["product_code"] = line["product_code"]
        bucket["name"] = line.get("name") or ""
        bucket["revenue"] += Decimal(line.get("revenue_usd") or 0)
        bucket["shopify_fee"] += Decimal(line.get("shopify_fee_usd") or 0)
        bucket["purchase"] += Decimal(line.get("purchase_usd") or 0)
        bucket["shipping_cost"] += Decimal(line.get("shipping_cost_usd") or 0)
        bucket["return_reserve"] += Decimal(line.get("return_reserve_usd") or 0)

        # 广告分摊：当日当站全部 spend × (本行 units / 当日当站全部 units)
        site_code = line.get("site_code")
        ad_account = SITE_TO_AD_ACCOUNT.get(site_code) if site_code else None
        if ad_account:
            day_spend = ad_spend.get((line["business_date"], ad_account), Decimal("0"))
            day_units = site_units.get((line["business_date"], site_code), 0)
            if day_units > 0:
                share = Decimal(line.get("quantity") or 0) / Decimal(day_units)
                bucket["ad_cost"] += day_spend * share

    # 转 rows
    rows = []
    total_orders = 0
    total_revenue = Decimal("0")
    total_profit = Decimal("0")
    total_ad = Decimal("0")
    for pid, b in sorted(by_product.items(), key=lambda kv: -kv[1]["revenue"]):
        revenue = b["revenue"]
        profit = (revenue - b["shopify_fee"] - b["ad_cost"]
                  - b["purchase"] - b["shipping_cost"] - b["return_reserve"])
        roas = float(revenue / b["ad_cost"]) if b["ad_cost"] > 0 else None
        order_count = sum(1 for l in lines if l["product_id"] == pid)  # 按订单行计数；如需按订单去重，改成 set(dxm_package_id)
        rows.append({
            "product_id": pid,
            "product_code": b["product_code"],
            "name": b["name"],
            "order_count": order_count,
            "revenue_usd": float(revenue),
            "shipping_cost_usd": float(b["shipping_cost"]),
            "shipping_pct": float(b["shipping_cost"] / revenue) if revenue > 0 else 0.0,
            "purchase_usd": float(b["purchase"]),
            "purchase_pct": float(b["purchase"] / revenue) if revenue > 0 else 0.0,
            "ad_cost_usd": float(b["ad_cost"]),
            "ad_pct": float(b["ad_cost"] / revenue) if revenue > 0 else 0.0,
            "roas": roas,
            "profit_usd": float(profit),
            "profit_pct": float(profit / revenue) if revenue > 0 else 0.0,
            "cost_completeness": check_sku_cost_completeness(product_costs.get(pid, {})).get("status", "incomplete"),
        })
        total_orders += order_count
        total_revenue += revenue
        total_profit += profit
        total_ad += b["ad_cost"]

    summary = {
        "product_count": len(rows),
        "total_orders": total_orders,
        "total_revenue_usd": float(total_revenue),
        "total_profit_usd": float(total_profit),
        "overall_roas": float(total_revenue / total_ad) if total_ad > 0 else None,
    }
    return {"rows": rows, "summary": summary}
```

注意：`check_sku_cost_completeness` 实际返回的字段名以 [appcore/order_analytics/cost_completeness.py:44](../../../appcore/order_analytics/cost_completeness.py#L44) 实现为准，执行时如发现 `status` 字段名是别的（如 `completeness` 或 `state`），改用真正的 key。

### Step 1.9: 运行测试确认通过

```bash
pytest tests/test_product_profit_list.py -v
```
Expected: 2 PASS

### Step 1.10: 加国家过滤测试

- [ ] 追加：

```python
def test_generate_list_country_filter_excludes_other_countries():
    """country='VN' 时只聚合越南订单。"""
    fake_lines = [
        {"product_id": 100, "product_code": "A", "name": "A", "business_date": date(2026, 5, 5),
         "buyer_country": "VN", "site_code": "newjoy", "revenue_usd": Decimal("50"),
         "shopify_fee_usd": Decimal("2"), "purchase_usd": Decimal("10"),
         "shipping_cost_usd": Decimal("3"), "return_reserve_usd": Decimal("0.5"), "quantity": 1},
    ]
    with patch.object(ppl, "_load_lines") as load:
        load.return_value = fake_lines
        with patch.object(ppl, "_load_ad_spend", return_value={}), \
             patch.object(ppl, "_load_site_units", return_value={}), \
             patch.object(ppl, "_load_product_costs", return_value={}):
            ppl.generate_list(date_from=date(2026, 5, 1), date_to=date(2026, 5, 7), country="VN")
    args, kwargs = load.call_args
    assert "VN" in (args + tuple(kwargs.values())) or any("VN" in str(a) for a in args)
```

### Step 1.11: 运行 + 修复（可能需要调 `_load_lines` 把 country 大小写归一化）

```bash
pytest tests/test_product_profit_list.py -v
```

### Step 1.12: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add appcore/order_analytics/product_profit_list.py tests/test_product_profit_list.py
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): add list aggregation module for tab 1

新增 product_profit_list.generate_list()：按日期范围 + 国家聚合全产品的订单数 / 收入 /
物流 / 采购 / 广告 / ROAS / 利润 / 利润率 / 成本完备性。口径与单产品 report 一致：
广告费按 site → ad_account 映射 + 当日 units 比例分摊。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 后端 — `/list.json` + `/list.xlsx` 端点

**Files:**
- Modify: `web/routes/product_profit_report.py`
- Test: `tests/test_product_profit_routes.py`（新建或追加到现有 test 文件，按仓库习惯）

### Step 2.1: 写失败测试 — list.json 路由 200 + 校验

- [ ] 追加测试（参考 [tests/test_product_profit_report.py](../../../tests/test_product_profit_report.py) 现有 pattern；如不存在则新建 `tests/test_product_profit_routes.py`）：

```python
def test_list_json_200_default_dates(client_with_login):
    """无日期参数 → 默认本月，200 返回 rows + summary。"""
    resp = client_with_login.get("/order-analytics/product-profit/list.json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rows" in data
    assert "summary" in data


def test_list_json_invalid_date_range_400(client_with_login):
    """date_from > date_to → 400。"""
    resp = client_with_login.get(
        "/order-analytics/product-profit/list.json"
        "?date_from=2026-06-01&date_to=2026-05-01"
    )
    assert resp.status_code == 400
```

### Step 2.2: 运行确认失败

```bash
pytest tests/test_product_profit_routes.py -v
```
Expected: FAIL（404 或路由未注册）

### Step 2.3: 加路由 handler 到 `web/routes/product_profit_report.py`

- [ ] 在 import 区追加：

```python
from appcore.order_analytics import product_profit_list as ppl
```

- [ ] 在文件末尾追加：

```python
@bp.route("/list.json", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_list_json():
    """全产品聚合列表（Tab ① 数据源）。

    Query:
      date_from (YYYY-MM-DD, default = month-start)
      date_to   (YYYY-MM-DD, default = today)
      country   (大写国家代码，可选；空 / "all" = 全部)
    """
    today = date.today()
    month_start = today.replace(day=1)
    date_from = _parse_date(request.args.get("date_from"), month_start)
    date_to = _parse_date(request.args.get("date_to"), today)
    if date_from > date_to:
        return jsonify({"error": "date_from > date_to"}), 400

    country = (request.args.get("country") or "").strip() or None
    result = ppl.generate_list(date_from=date_from, date_to=date_to, country=country)
    return jsonify(result)
```

### Step 2.4: 运行测试确认通过

```bash
pytest tests/test_product_profit_routes.py -v
```
Expected: 2 PASS

### Step 2.5: 实现 generate_list_xlsx + `/list.xlsx` 端点

- [ ] 在 `product_profit_list.py` 追加：

```python
def generate_list_xlsx(report: dict[str, Any]) -> bytes:
    """把 generate_list() 的结果导出为 xlsx。两个 sheet: "summary"、"products"。"""
    import openpyxl  # 同 product_profit_report.generate_xlsx 的 lazy import 模式
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "summary"
    s = report["summary"]
    ws1.append(["指标", "值"])
    ws1.append(["产品数", s["product_count"]])
    ws1.append(["订单数", s["total_orders"]])
    ws1.append(["收入(USD)", s["total_revenue_usd"]])
    ws1.append(["利润(USD)", s["total_profit_usd"]])
    ws1.append(["整体 ROAS", s["overall_roas"]])

    ws2 = wb.create_sheet("products")
    headers = ["产品代码", "产品名", "订单数", "收入(USD)",
               "物流(USD)", "物流占比", "采购(USD)", "采购占比",
               "广告(USD)", "广告占比", "ROAS", "利润(USD)", "利润率", "成本完备"]
    ws2.append(headers)
    for r in report["rows"]:
        ws2.append([
            r["product_code"], r["name"], r["order_count"], r["revenue_usd"],
            r["shipping_cost_usd"], r["shipping_pct"],
            r["purchase_usd"], r["purchase_pct"],
            r["ad_cost_usd"], r["ad_pct"],
            r["roas"] if r["roas"] is not None else "",
            r["profit_usd"], r["profit_pct"],
            r["cost_completeness"],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] 在 `web/routes/product_profit_report.py` 追加：

```python
@bp.route("/list.xlsx", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_list_xlsx():
    today = date.today()
    month_start = today.replace(day=1)
    date_from = _parse_date(request.args.get("date_from"), month_start)
    date_to = _parse_date(request.args.get("date_to"), today)
    if date_from > date_to:
        return jsonify({"error": "date_from > date_to"}), 400
    country = (request.args.get("country") or "").strip() or None
    report = ppl.generate_list(date_from=date_from, date_to=date_to, country=country)
    xlsx_bytes = ppl.generate_list_xlsx(report)
    filename = f"product_profit_list_{date_from.isoformat()}_{date_to.isoformat()}.xlsx"
    return send_file(
        io.BytesIO(xlsx_bytes), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
```

### Step 2.6: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/routes/product_profit_report.py appcore/order_analytics/product_profit_list.py tests/test_product_profit_routes.py
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): add /list.json + /list.xlsx endpoints

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 后端 — 广告明细聚合 `product_profit_ads.py`

**Files:**
- Create: `appcore/order_analytics/product_profit_ads.py`
- Test: `tests/test_product_profit_ads.py`
- Reference: 现有 campaign 匹配链路 `appcore/order_analytics/campaign_overrides.py` (`resolve_ad_product_match`, `manual_match_meta_ad_campaign`)

### Step 3.1: 写失败测试 — generate_ads_report 基本结构

- [ ] 创建 `tests/test_product_profit_ads.py`：

```python
"""产品广告明细聚合测试。"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from appcore.order_analytics import product_profit_ads as ppa


def test_generate_ads_report_empty():
    """无广告数据 → 返回空列表 + 0 汇总。"""
    with patch.object(ppa, "_load_campaign_metrics", return_value=[]), \
         patch.object(ppa, "_load_match_map", return_value={}), \
         patch.object(ppa, "_load_attributed_orders", return_value={}):
        result = ppa.generate_ads_report(
            product_id=100, date_from=date(2026, 5, 1), date_to=date(2026, 5, 7),
        )
    assert result["accounts"] == []
    assert result["campaigns"] == []
    assert result["unmatched"] == []
    assert result["daily"] == []
```

### Step 3.2: 创建 module 骨架

- [ ] 创建 `appcore/order_analytics/product_profit_ads.py`：

```python
"""产品广告明细（campaign 级）聚合，给 Tab ④ 用。

数据流：
  1. 从 meta_ad_realtime_daily_campaign_metrics 拉日期范围内所有 campaign 行
  2. 通过 resolve_ad_product_match() 把 campaign 关联到 product
  3. 仅保留 product_id == 当前产品 的 campaign（其余进 "unmatched" 区）
  4. 按 campaign 聚合花费 / 展示 / 点击 / 结果
  5. 拉同日同产品的 order_profit_lines 求归属订单 / 收入
  6. ROAS = 归属收入 / 花费；利润贡献 = 归属收入 - 花费 - 同日同产品的成本
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .campaign_overrides import resolve_ad_product_match

log = logging.getLogger(__name__)


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def _load_campaign_metrics(date_from: date, date_to: date) -> list[dict[str, Any]]:
    """拉 meta_ad_realtime_daily_campaign_metrics 在日期范围的所有行。"""
    return query(
        "SELECT date, ad_account_id, campaign_id, campaign_name, "
        "       spend_usd, impressions, clicks, results "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE date BETWEEN %s AND %s",
        (date_from, date_to),
    )


def _load_match_map(campaign_codes: set[str]) -> dict[str, int | None]:
    """campaign_code → product_id（命中），或 None（未匹配）。

    实际查询通过 resolve_ad_product_match() 逐个解析；这里独立函数方便测试 mock。
    """
    result: dict[str, int | None] = {}
    for code in campaign_codes:
        match = resolve_ad_product_match(code)  # 现有函数；签名/返回结构以实际为准
        result[code] = match.get("product_id") if match else None
    return result


def _load_attributed_orders(
    product_id: int, date_from: date, date_to: date
) -> dict[date, dict[str, Decimal]]:
    """同日同产品订单聚合：date → {revenue, purchase, shipping_cost, return_reserve}。"""
    rows = query(
        "SELECT business_date AS d, "
        "       SUM(revenue_usd) AS revenue, "
        "       SUM(purchase_usd) AS purchase, "
        "       SUM(shipping_cost_usd) AS shipping, "
        "       SUM(return_reserve_usd) AS reserve, "
        "       COUNT(DISTINCT dxm_order_line_id) AS order_count "
        "FROM order_profit_lines "
        "WHERE product_id = %s AND business_date BETWEEN %s AND %s "
        "GROUP BY business_date",
        (product_id, date_from, date_to),
    )
    return {
        r["d"]: {
            "revenue": Decimal(r["revenue"] or 0),
            "purchase": Decimal(r["purchase"] or 0),
            "shipping": Decimal(r["shipping"] or 0),
            "reserve": Decimal(r["reserve"] or 0),
            "order_count": int(r["order_count"] or 0),
        } for r in rows
    }


def generate_ads_report(
    *, product_id: int, date_from: date, date_to: date,
    country: str | None = None,
) -> dict[str, Any]:
    """生成广告明细报表（Tab ④ 数据源）。

    Returns:
        {
          "accounts": [
            {"ad_account_id": str, "label": str, "spend_usd": float,
             "impressions": int, "clicks": int, "roas": float | None}
          ],
          "campaigns": [
            {"ad_account_id": str, "campaign_id": str, "campaign_name": str,
             "spend_usd": float, "impressions": int, "clicks": int, "results": int,
             "ctr": float, "cpc": float | None,
             "attributed_order_count": int, "attributed_revenue_usd": float,
             "roas": float | None, "profit_contribution_usd": float}, ...
          ],
          "daily": [{"date": "YYYY-MM-DD", "spend_usd": float, "revenue_usd": float}, ...],
          "unmatched": [{"campaign_id": str, "campaign_name": str, "spend_usd": float}, ...]
        }
    """
    rows = _load_campaign_metrics(date_from, date_to)
    if not rows:
        return {"accounts": [], "campaigns": [], "daily": [], "unmatched": []}

    # 收集所有 campaign code（用于一次性建匹配表）
    codes = {r["campaign_name"] for r in rows if r.get("campaign_name")}
    match_map = _load_match_map(codes)
    attributed = _load_attributed_orders(product_id, date_from, date_to)

    # campaign 聚合
    by_campaign: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "ad_account_id": "", "campaign_id": "", "campaign_name": "",
        "spend": Decimal("0"), "impressions": 0, "clicks": 0, "results": 0,
    })
    daily_spend: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    unmatched: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "campaign_id": "", "campaign_name": "", "spend": Decimal("0"),
    })

    for r in rows:
        name = r.get("campaign_name") or ""
        matched_pid = match_map.get(name)
        if matched_pid != product_id:
            # 未归属本产品（包括未匹配 + 匹配到别的产品）
            if matched_pid is None:
                u = unmatched[r["campaign_id"]]
                u["campaign_id"] = r["campaign_id"]
                u["campaign_name"] = name
                u["spend"] += Decimal(r.get("spend_usd") or 0)
            continue
        b = by_campaign[r["campaign_id"]]
        b["ad_account_id"] = r["ad_account_id"]
        b["campaign_id"] = r["campaign_id"]
        b["campaign_name"] = name
        b["spend"] += Decimal(r.get("spend_usd") or 0)
        b["impressions"] += int(r.get("impressions") or 0)
        b["clicks"] += int(r.get("clicks") or 0)
        b["results"] += int(r.get("results") or 0)
        daily_spend[r["date"]] += Decimal(r.get("spend_usd") or 0)

    # campaign 行转 list + 计算衍生字段
    campaigns = []
    by_account: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "ad_account_id": "", "spend": Decimal("0"),
        "impressions": 0, "clicks": 0,
    })
    total_attributed_revenue = sum(a["revenue"] for a in attributed.values())
    total_spend = sum(b["spend"] for b in by_campaign.values())
    for cid, b in by_campaign.items():
        spend = b["spend"]
        # 简化口径：归属收入 = 该产品在 spend 出现的所有日子的收入按 spend 比例分配
        # 实际 PR 阶段如需更精细，可在该 campaign 出现的日子精确分摊。
        attributed_revenue_share = (
            (spend / total_spend) * total_attributed_revenue if total_spend > 0 else Decimal("0")
        )
        attributed_costs = sum(
            (a["purchase"] + a["shipping"] + a["reserve"]) * (spend / total_spend)
            for a in attributed.values()
        ) if total_spend > 0 else Decimal("0")
        ctr = float(b["clicks"]) / b["impressions"] if b["impressions"] > 0 else 0.0
        cpc = float(spend) / b["clicks"] if b["clicks"] > 0 else None
        roas = float(attributed_revenue_share / spend) if spend > 0 else None
        profit = attributed_revenue_share - spend - attributed_costs
        campaigns.append({
            "ad_account_id": b["ad_account_id"],
            "campaign_id": cid,
            "campaign_name": b["campaign_name"],
            "spend_usd": float(spend),
            "impressions": b["impressions"],
            "clicks": b["clicks"],
            "results": b["results"],
            "ctr": ctr,
            "cpc": cpc,
            "attributed_order_count": sum(a["order_count"] for a in attributed.values()) if total_spend > 0 else 0,
            "attributed_revenue_usd": float(attributed_revenue_share),
            "roas": roas,
            "profit_contribution_usd": float(profit),
        })
        acc = by_account[b["ad_account_id"]]
        acc["ad_account_id"] = b["ad_account_id"]
        acc["spend"] += spend
        acc["impressions"] += b["impressions"]
        acc["clicks"] += b["clicks"]

    campaigns.sort(key=lambda c: -c["spend_usd"])

    accounts = []
    for aid, a in by_account.items():
        # ad_account_id → label 通过 system_settings.meta_ad_accounts 反查；
        # 简化：先放空 label，前端按 SITE_TO_AD_ACCOUNT 反查（newjoyloo / Omurio）
        accounts.append({
            "ad_account_id": aid,
            "label": "",
            "spend_usd": float(a["spend"]),
            "impressions": a["impressions"],
            "clicks": a["clicks"],
            "roas": None,  # 账户级 ROAS 在前端汇总
        })

    daily = sorted([
        {"date": d.isoformat(), "spend_usd": float(s),
         "revenue_usd": float(attributed.get(d, {}).get("revenue", Decimal("0")))}
        for d, s in daily_spend.items()
    ], key=lambda x: x["date"])

    unmatched_list = sorted([
        {"campaign_id": cid, "campaign_name": u["campaign_name"], "spend_usd": float(u["spend"])}
        for cid, u in unmatched.items()
    ], key=lambda x: -x["spend_usd"])

    return {
        "accounts": accounts,
        "campaigns": campaigns,
        "daily": daily,
        "unmatched": unmatched_list,
    }
```

### Step 3.3: 运行测试确认通过

```bash
pytest tests/test_product_profit_ads.py -v
```
Expected: 1 PASS

### Step 3.4: 写匹配命中测试

- [ ] 追加：

```python
def test_generate_ads_report_matched_campaign_aggregates():
    """campaign 匹配到产品 → campaigns 列表有 1 行 + accounts 1 行 + daily 1 行。"""
    fake_metrics = [
        {"date": date(2026, 5, 5), "ad_account_id": "2110407576446225",
         "campaign_id": "111", "campaign_name": "ABC-rjc",
         "spend_usd": Decimal("8"), "impressions": 1000, "clicks": 50, "results": 5},
    ]
    fake_attributed = {
        date(2026, 5, 5): {
            "revenue": Decimal("50"), "purchase": Decimal("10"),
            "shipping": Decimal("3"), "reserve": Decimal("0.5"), "order_count": 1,
        }
    }
    with patch.object(ppa, "_load_campaign_metrics", return_value=fake_metrics), \
         patch.object(ppa, "_load_match_map", return_value={"ABC-rjc": 100}), \
         patch.object(ppa, "_load_attributed_orders", return_value=fake_attributed):
        result = ppa.generate_ads_report(
            product_id=100, date_from=date(2026, 5, 1), date_to=date(2026, 5, 7),
        )
    assert len(result["campaigns"]) == 1
    c = result["campaigns"][0]
    assert c["campaign_id"] == "111"
    assert c["spend_usd"] == 8.0
    assert c["roas"] == 50.0 / 8.0
    assert len(result["accounts"]) == 1
    assert len(result["daily"]) == 1
```

### Step 3.5: 运行 + 修复 + Commit

```bash
pytest tests/test_product_profit_ads.py -v
# 全 PASS 后：
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add appcore/order_analytics/product_profit_ads.py tests/test_product_profit_ads.py
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): add ads aggregation module for tab 4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 后端 — `/ads.json` + `/ads/manual-match` 端点

**Files:**
- Modify: `web/routes/product_profit_report.py`
- Test: `tests/test_product_profit_routes.py`

### Step 4.1: 写失败测试

- [ ] 追加：

```python
def test_ads_json_requires_product_id(client_with_login):
    """ads.json 没传 product_id → 400。"""
    resp = client_with_login.get("/order-analytics/product-profit/ads.json")
    assert resp.status_code == 400


def test_ads_json_with_product_id_200(client_with_login):
    """带合法 product_id → 200。"""
    resp = client_with_login.get(
        "/order-analytics/product-profit/ads.json?product_id=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "accounts" in data and "campaigns" in data
```

### Step 4.2: 添加路由

- [ ] 在 `web/routes/product_profit_report.py` import 区追加：

```python
from appcore.order_analytics import product_profit_ads as ppa
from appcore.order_analytics.campaign_overrides import manual_match_meta_ad_campaign
```

- [ ] 文件末尾追加：

```python
@bp.route("/ads.json", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_ads_json():
    try:
        product_id = int(request.args.get("product_id", "0"))
    except ValueError:
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "missing product_id"}), 400
    today = date.today()
    month_start = today.replace(day=1)
    date_from = _parse_date(request.args.get("date_from"), month_start)
    date_to = _parse_date(request.args.get("date_to"), today)
    if date_from > date_to:
        return jsonify({"error": "date_from > date_to"}), 400
    country = (request.args.get("country") or "").strip() or None
    result = ppa.generate_ads_report(
        product_id=product_id, date_from=date_from, date_to=date_to, country=country,
    )
    return jsonify(result)


@bp.route("/ads/manual-match", methods=["POST"])
@login_required
@permission_required("product_profit")
def api_ads_manual_match():
    """把一个未匹配 campaign 手工配对到当前产品。"""
    payload = request.get_json(silent=True) or {}
    campaign_code = (payload.get("campaign_code") or "").strip()
    try:
        product_id = int(payload.get("product_id") or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid product_id"}), 400
    if not campaign_code or product_id <= 0:
        return jsonify({"error": "missing campaign_code / product_id"}), 400
    try:
        manual_match_meta_ad_campaign(campaign_code, product_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("manual match failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"ok": True})
```

### Step 4.3: 运行测试确认通过

```bash
pytest tests/test_product_profit_routes.py -v
```
Expected: 全 PASS

### Step 4.4: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/routes/product_profit_report.py tests/test_product_profit_routes.py
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): add /ads.json + /ads/manual-match endpoints

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 前端 — Tab 切换骨架

**Files:**
- Modify: `web/templates/product_profit_dashboard.html`

### Step 5.1: 在 `<style>` 区追加 Tab 切换 CSS

- [ ] 在 `.ppd-filters` CSS 块之后插入（约 90 行附近）：

```css
/* Tab 切换器 */
.ppd-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border-main);
  margin-top: -8px;
}
.ppd-tab {
  padding: 10px 20px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-user-badge);
  cursor: pointer;
  border: none;
  background: transparent;
  border-bottom: 2px solid transparent;
  transition: color .12s, border-color .12s;
}
.ppd-tab:hover { color: var(--primary-color); }
.ppd-tab.active {
  color: var(--primary-color);
  border-bottom-color: var(--primary-color);
}
.ppd-tab-panel { display: none; }
.ppd-tab-panel.active { display: flex; flex-direction: column; gap: 20px; }
.ppd-empty-tab {
  padding: 60px 24px;
  text-align: center;
  color: var(--text-user-badge);
  font-size: 14px;
  background: #fff;
  border: 1px dashed var(--border-main);
  border-radius: 12px;
}
@media (max-width: 768px) {
  .ppd-tabs { overflow-x: auto; flex-wrap: nowrap; }
}
```

### Step 5.2: 在 HTML body（`.ppd-main` 内）筛选条之后插入 Tab 切换器 + 4 个 panel 容器

- [ ] 找到现有 `.ppd-main` 块，在 `.ppd-filters`（筛选条）之后、其他内容之前插入：

```html
<nav class="ppd-tabs" role="tablist">
  <button class="ppd-tab active" data-tab="list" role="tab">① 产品列表</button>
  <button class="ppd-tab" data-tab="orders" role="tab">② 订单明细</button>
  <button class="ppd-tab" data-tab="country" role="tab">③ 国家看板</button>
  <button class="ppd-tab" data-tab="ads" role="tab">④ 广告明细</button>
</nav>

<section class="ppd-tab-panel active" data-panel="list">
  <!-- Task 7 填充 -->
  <div class="ppd-empty-tab">产品列表（Task 7 填充）</div>
</section>

<section class="ppd-tab-panel" data-panel="orders">
  <!-- Task 6 把现有 5 个区块迁过来 -->
</section>

<section class="ppd-tab-panel" data-panel="country">
  <div class="ppd-empty-tab">国家看板（Task 8 填充）</div>
</section>

<section class="ppd-tab-panel" data-panel="ads">
  <div class="ppd-empty-tab">广告明细（Task 9 填充）</div>
</section>
```

### Step 5.3: 在 `<script>` 区追加 Tab 切换 JS

- [ ] 在现有 JS 末尾追加（IIFE 内）：

```javascript
// ========== Tab 切换 ==========
function switchTab(tabName) {
  document.querySelectorAll('.ppd-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tabName);
  });
  document.querySelectorAll('.ppd-tab-panel').forEach(p => {
    p.classList.toggle('active', p.dataset.panel === tabName);
  });
  // URL state（Task 10 接管）
  const url = new URL(window.location.href);
  url.searchParams.set('tab', tabName);
  history.replaceState(null, '', url.toString());
  // 触发当前 Tab 的数据加载（每个 Tab 独立 load 函数，避免无谓请求）
  if (tabName === 'list') loadListTab && loadListTab();
  else if (tabName === 'orders') loadOrdersTab && loadOrdersTab();
  else if (tabName === 'country') loadCountryTab && loadCountryTab();
  else if (tabName === 'ads') loadAdsTab && loadAdsTab();
}

document.querySelectorAll('.ppd-tab').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// 初始化：从 URL 读 tab 参数
const initialTab = new URLSearchParams(location.search).get('tab') || 'list';
switchTab(initialTab);
```

### Step 5.4: 手动测试 Tab 切换

```bash
cd g:/Code/AutoVideoSrtLocal-product-profit-tabs
python -m web.app  # 假设这是 dev server 启动方式；按仓库实际命令调
# 浏览器访问 http://127.0.0.1:5000/product-profit
# 用测试管理员账号登录（凭据见 testuser.md），确认 4 个 Tab 按钮可切换、URL ?tab= 同步
```

### Step 5.5: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): add tab switcher skeleton (4 tabs)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 前端 — Tab ② 包裹现有 5 区块 + 全局筛选

**目标：把现有的"总账卡片 / 站点切片 / 每日折线 / 国家柱状图 / 订单明细表"5 个区块完整迁入 Tab ② panel。同时把现有筛选条改成"全局筛选条"，加"全部产品"选项 + 国家筛选下拉。**

### Step 6.1: 给现有筛选条加"全部产品"选项 + 国家筛选下拉

- [ ] 在 `<select id="ppd-product-select">` 的 JS 填充逻辑里，把首选项改成 "全部产品"：

```javascript
// 现有 fetchProducts() 后处理：在最前插入 "全部"
products.unshift({ id: 0, product_code: 'ALL', name: '全部产品' });
```

- [ ] 在筛选条 HTML 加国家选择：

```html
<label>
  国家
  <select id="ppd-country-select">
    <option value="">全部</option>
  </select>
</label>
```

### Step 6.2: 把现有 5 个区块的 HTML 移入 `<section data-panel="orders">`

- [ ] 用编辑器把 `.ppd-stats / .ppd-sites / .ppd-charts / .ppd-table` 4 个块（以及它们之间任何辅助 div）整段 cut 出来，paste 到 `<section class="ppd-tab-panel" data-panel="orders">` 内。

  确认结构：

```html
<section class="ppd-tab-panel" data-panel="orders">
  <div id="ppd-orders-empty" class="ppd-empty-tab" style="display:none">
    请在顶部选择具体产品后查看订单明细。
  </div>
  <div id="ppd-orders-content">
    <div class="ppd-stats">…</div>
    <div class="ppd-sites">…</div>
    <div class="ppd-charts">…</div>
    <div class="ppd-table">…</div>
  </div>
</section>
```

### Step 6.3: 改现有 fetchReport() / renderReport() 为 loadOrdersTab()

- [ ] 找到现有调用 `/order-analytics/product-profit/report.json` 的函数，重命名为 `loadOrdersTab()`，并加产品 ID = 0 的空状态分支：

```javascript
async function loadOrdersTab() {
  const productId = parseInt(document.getElementById('ppd-product-select').value, 10) || 0;
  if (!productId) {
    document.getElementById('ppd-orders-empty').style.display = 'block';
    document.getElementById('ppd-orders-content').style.display = 'none';
    return;
  }
  document.getElementById('ppd-orders-empty').style.display = 'none';
  document.getElementById('ppd-orders-content').style.display = 'block';
  const params = new URLSearchParams({
    product_id: productId,
    date_from: document.getElementById('ppd-date-from').value,
    date_to:   document.getElementById('ppd-date-to').value,
  });
  const country = document.getElementById('ppd-country-select').value;
  if (country) params.set('country', country);
  const resp = await fetch(`/order-analytics/product-profit/report.json?${params}`);
  const data = await resp.json();
  // 调用现有渲染逻辑（renderStats / renderSites / renderCharts / renderTable）
  renderReport(data);
}
```

### Step 6.4: 现有"查询"按钮改成调 `switchTab(currentTab)`

- [ ] 把"查询"按钮的 onclick 改成：

```javascript
document.getElementById('ppd-search-btn').addEventListener('click', () => {
  // 重新加载当前 Tab
  const active = document.querySelector('.ppd-tab.active').dataset.tab;
  switchTab(active);
});
```

### Step 6.5: 端到端验证

- [ ] dev server 起来，浏览器访问 `/product-profit`：
  - 默认 Tab ① 占位提示
  - 切到 Tab ②，显示"请在顶部选择具体产品"
  - 选具体产品 + 点查询，旧版 5 区块全部正常渲染（数值与改造前一致）

### Step 6.6: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): wrap legacy 5 sections into tab 2 + global filters

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 前端 — Tab ① 产品列表

### Step 7.1: 替换 Tab ① 占位为表格容器

- [ ] 修改 `<section data-panel="list">`：

```html
<section class="ppd-tab-panel active" data-panel="list">
  <div class="ppd-list-summary" id="ppd-list-summary">
    <!-- JS 填："共 N 个产品 | 总收入 $X | 总利润 $Y | 整体 ROAS Z" -->
  </div>
  <div class="ppd-table">
    <table id="ppd-list-table">
      <thead>
        <tr>
          <th>产品</th>
          <th>订单数</th>
          <th>收入</th>
          <th>物流</th>
          <th>采购</th>
          <th>广告</th>
          <th>ROAS</th>
          <th>利润</th>
          <th>成本完备</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
    <button class="ppd-btn" id="ppd-list-xlsx">下载 Excel</button>
  </div>
</section>
```

### Step 7.2: 加 CSS — 占比 chip + 亏损行 + 完备徽章

- [ ] 在 `<style>` 追加：

```css
.ppd-pct-chip {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  font-size: 11px;
  background: var(--bg-muted, #f1f5f9);
  color: var(--text-user-badge);
  border-radius: 6px;
}
.ppd-row-loss { background: rgba(239, 68, 68, 0.06); }
.ppd-row-loss td { color: #b91c1c; }
.ppd-cc-badge {
  display: inline-block;
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 9999px;
}
.ppd-cc-ok { background: rgba(34, 197, 94, .12); color: #15803d; }
.ppd-cc-incomplete { background: rgba(245, 158, 11, .12); color: #b45309; }
.ppd-list-summary {
  font-size: 13px;
  color: var(--text-user-badge);
  display: flex;
  gap: 24px;
}
.ppd-list-summary span strong { color: var(--text-main); margin-left: 4px; }
.ppd-clickable-row { cursor: pointer; }
.ppd-clickable-row:hover { background: rgba(59, 130, 246, .04); }
```

### Step 7.3: 加 loadListTab() + renderList()

- [ ] 在 JS 区追加：

```javascript
async function loadListTab() {
  const params = new URLSearchParams({
    date_from: document.getElementById('ppd-date-from').value,
    date_to:   document.getElementById('ppd-date-to').value,
  });
  const country = document.getElementById('ppd-country-select').value;
  if (country) params.set('country', country);
  const resp = await fetch(`/order-analytics/product-profit/list.json?${params}`);
  const data = await resp.json();
  renderList(data);
}

function renderList(data) {
  const s = data.summary;
  document.getElementById('ppd-list-summary').innerHTML = `
    <span>产品数 <strong>${s.product_count}</strong></span>
    <span>订单数 <strong>${s.total_orders}</strong></span>
    <span>总收入 <strong>$${s.total_revenue_usd.toFixed(2)}</strong></span>
    <span>总利润 <strong>$${s.total_profit_usd.toFixed(2)}</strong></span>
    <span>整体 ROAS <strong>${s.overall_roas != null ? s.overall_roas.toFixed(2) : '—'}</strong></span>
  `;
  const tbody = document.querySelector('#ppd-list-table tbody');
  tbody.innerHTML = data.rows.map(r => {
    const lossClass = r.profit_usd < 0 ? 'ppd-row-loss' : '';
    const ccClass = r.cost_completeness === 'ok' ? 'ppd-cc-ok' : 'ppd-cc-incomplete';
    const ccLabel = r.cost_completeness === 'ok' ? '✓ 完备' : '⚠ 不完备';
    const roasStr = r.roas != null ? r.roas.toFixed(2) : '—';
    return `
      <tr class="${lossClass} ppd-clickable-row" data-product-id="${r.product_id}">
        <td>${escapeHtml(r.name || r.product_code)}</td>
        <td>${r.order_count}</td>
        <td>$${r.revenue_usd.toFixed(2)}</td>
        <td>$${r.shipping_cost_usd.toFixed(2)}<span class="ppd-pct-chip">${(r.shipping_pct * 100).toFixed(1)}%</span></td>
        <td>$${r.purchase_usd.toFixed(2)}<span class="ppd-pct-chip">${(r.purchase_pct * 100).toFixed(1)}%</span></td>
        <td>$${r.ad_cost_usd.toFixed(2)}<span class="ppd-pct-chip">${(r.ad_pct * 100).toFixed(1)}%</span></td>
        <td>${roasStr}</td>
        <td>$${r.profit_usd.toFixed(2)}<span class="ppd-pct-chip">${(r.profit_pct * 100).toFixed(1)}%</span></td>
        <td><span class="ppd-cc-badge ${ccClass}">${ccLabel}</span></td>
      </tr>`;
  }).join('');
  // 点击产品行 → 切到订单明细 Tab + 锁产品
  tbody.querySelectorAll('.ppd-clickable-row').forEach(tr => {
    tr.addEventListener('click', () => {
      document.getElementById('ppd-product-select').value = tr.dataset.productId;
      switchTab('orders');
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 下载 Excel
document.getElementById('ppd-list-xlsx').addEventListener('click', () => {
  const params = new URLSearchParams({
    date_from: document.getElementById('ppd-date-from').value,
    date_to:   document.getElementById('ppd-date-to').value,
  });
  const country = document.getElementById('ppd-country-select').value;
  if (country) params.set('country', country);
  window.location.href = `/order-analytics/product-profit/list.xlsx?${params}`;
});
```

### Step 7.4: 端到端验证

- [ ] dev server 跑起来，访问 `/product-profit`：
  - 默认 Tab ① 显示全产品列表
  - 9 列数据完整，亏损行红底
  - 点击某行 → 切到 Tab ②、产品下拉自动锁到该产品
  - 下载 Excel 正常

### Step 7.5: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): implement tab 1 product list + click-through to tab 2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 前端 — Tab ③ 国家看板增强

### Step 8.1: 替换 Tab ③ 占位为容器

- [ ] 修改 `<section data-panel="country">`：

```html
<section class="ppd-tab-panel" data-panel="country">
  <div id="ppd-country-empty" class="ppd-empty-tab" style="display:none">
    请在顶部选择具体产品后查看国家看板。
  </div>
  <div id="ppd-country-content">
    <div class="ppd-charts">
      <div class="ppd-chart-card" id="ppd-country-chart-card">
        <h3>国家分布</h3>
        <div id="ppd-country-bar"></div>
      </div>
    </div>
    <div id="ppd-country-detail" style="display:none">
      <div class="ppd-stats" id="ppd-country-stats"></div>
      <div class="ppd-chart-card"><h3>每日盈亏</h3><div id="ppd-country-daily"></div></div>
      <div class="ppd-table"><table id="ppd-country-orders"><thead></thead><tbody></tbody></table></div>
    </div>
  </div>
</section>
```

### Step 8.2: 加 loadCountryTab()

- [ ] 在 JS 追加：

```javascript
async function loadCountryTab() {
  const productId = parseInt(document.getElementById('ppd-product-select').value, 10) || 0;
  const empty = document.getElementById('ppd-country-empty');
  const content = document.getElementById('ppd-country-content');
  if (!productId) { empty.style.display = 'block'; content.style.display = 'none'; return; }
  empty.style.display = 'none'; content.style.display = 'block';

  // 复用 report.json，提取 by_country
  const params = new URLSearchParams({
    product_id: productId,
    date_from: document.getElementById('ppd-date-from').value,
    date_to:   document.getElementById('ppd-date-to').value,
  });
  const resp = await fetch(`/order-analytics/product-profit/report.json?${params}`);
  const data = await resp.json();
  renderCountryBar(data.by_country);

  const country = document.getElementById('ppd-country-select').value;
  if (country) {
    renderCountryDetail(data, country);
  } else {
    document.getElementById('ppd-country-detail').style.display = 'none';
  }
}

function renderCountryBar(byCountry) {
  // 按收入排序的简单柱状图（复用现有 renderCountryChart 的渲染逻辑；
  // 如该函数已存在则直接调用并补点击事件绑定）
  // 点击柱条 → 设置 ppd-country-select 并触发 loadCountryTab
}

function renderCountryDetail(data, country) {
  // 从 data.orders / data.daily 过滤该国家行 → 渲染 stats / 折线 / 订单表
  const detail = document.getElementById('ppd-country-detail');
  detail.style.display = 'block';
  // 实现细节：复用 Tab ② 的 renderStats/renderTable，但传入过滤后的子集
}
```

### Step 8.3: 国家筛选变化时重新加载当前 Tab

- [ ] 在 JS 加：

```javascript
document.getElementById('ppd-country-select').addEventListener('change', () => {
  const active = document.querySelector('.ppd-tab.active').dataset.tab;
  switchTab(active);
});
```

### Step 8.4: 端到端验证 + Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): implement tab 3 country dashboard with drill-down

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 前端 — Tab ④ 广告明细

### Step 9.1: 替换 Tab ④ 占位为容器

- [ ] 修改 `<section data-panel="ads">`：

```html
<section class="ppd-tab-panel" data-panel="ads">
  <div id="ppd-ads-empty" class="ppd-empty-tab" style="display:none">
    请在顶部选择具体产品后查看广告明细。
  </div>
  <div id="ppd-ads-content">
    <div class="ppd-stats" id="ppd-ads-accounts"></div>
    <div class="ppd-table">
      <h3 style="margin:0 0 12px;font-size:14px">Campaign 明细</h3>
      <table id="ppd-ads-campaigns">
        <thead><tr>
          <th>账户</th><th>Campaign</th><th>花费</th><th>展示</th><th>点击</th>
          <th>结果</th><th>CTR</th><th>CPC</th><th>归属订单</th><th>归属收入</th>
          <th>ROAS</th><th>利润贡献</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <details>
      <summary style="cursor:pointer;font-size:13px;color:var(--text-user-badge)">日趋势（花费 vs 收入）</summary>
      <div id="ppd-ads-daily" style="margin-top:12px"></div>
    </details>
    <details>
      <summary style="cursor:pointer;font-size:13px;color:var(--text-user-badge)">未匹配 campaign（手动配对到本产品）</summary>
      <table id="ppd-ads-unmatched" style="margin-top:12px">
        <thead><tr><th>Campaign</th><th>花费</th><th>操作</th></tr></thead>
        <tbody></tbody>
      </table>
    </details>
  </div>
</section>
```

### Step 9.2: 加 loadAdsTab()

```javascript
async function loadAdsTab() {
  const productId = parseInt(document.getElementById('ppd-product-select').value, 10) || 0;
  const empty = document.getElementById('ppd-ads-empty');
  const content = document.getElementById('ppd-ads-content');
  if (!productId) { empty.style.display = 'block'; content.style.display = 'none'; return; }
  empty.style.display = 'none'; content.style.display = 'block';

  const params = new URLSearchParams({
    product_id: productId,
    date_from: document.getElementById('ppd-date-from').value,
    date_to:   document.getElementById('ppd-date-to').value,
  });
  const country = document.getElementById('ppd-country-select').value;
  if (country) params.set('country', country);
  const resp = await fetch(`/order-analytics/product-profit/ads.json?${params}`);
  const data = await resp.json();
  renderAds(data, productId);
}

function renderAds(data, productId) {
  // 账户卡片
  document.getElementById('ppd-ads-accounts').innerHTML = data.accounts.map(a => `
    <div class="ppd-stat-card">
      <div class="ppd-stat-label">${a.label || a.ad_account_id}</div>
      <div class="ppd-stat-value">$${a.spend_usd.toFixed(2)}</div>
      <div class="ppd-stat-sub">${a.impressions.toLocaleString()} 展示 · ${a.clicks.toLocaleString()} 点击</div>
    </div>
  `).join('');
  // Campaign 表
  const ctbody = document.querySelector('#ppd-ads-campaigns tbody');
  ctbody.innerHTML = data.campaigns.map(c => `
    <tr>
      <td>${escapeHtml(c.ad_account_id)}</td>
      <td>${escapeHtml(c.campaign_name)}</td>
      <td>$${c.spend_usd.toFixed(2)}</td>
      <td>${c.impressions.toLocaleString()}</td>
      <td>${c.clicks.toLocaleString()}</td>
      <td>${c.results}</td>
      <td>${(c.ctr * 100).toFixed(2)}%</td>
      <td>${c.cpc != null ? '$' + c.cpc.toFixed(2) : '—'}</td>
      <td>${c.attributed_order_count}</td>
      <td>$${c.attributed_revenue_usd.toFixed(2)}</td>
      <td>${c.roas != null ? c.roas.toFixed(2) : '—'}</td>
      <td>$${c.profit_contribution_usd.toFixed(2)}</td>
    </tr>
  `).join('');
  // 未匹配区
  const utbody = document.querySelector('#ppd-ads-unmatched tbody');
  utbody.innerHTML = data.unmatched.map(u => `
    <tr>
      <td>${escapeHtml(u.campaign_name)}</td>
      <td>$${u.spend_usd.toFixed(2)}</td>
      <td><button class="ppd-btn ppd-ads-match" data-campaign="${escapeHtml(u.campaign_name)}">配对到本产品</button></td>
    </tr>
  `).join('');
  utbody.querySelectorAll('.ppd-ads-match').forEach(btn => {
    btn.addEventListener('click', async () => {
      const campaignCode = btn.dataset.campaign;
      const ok = confirm(`确认把 "${campaignCode}" 配对到当前产品？`);
      if (!ok) return;
      const resp = await fetch('/order-analytics/product-profit/ads/manual-match', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign_code: campaignCode, product_id: productId }),
      });
      if (resp.ok) loadAdsTab();
      else alert('配对失败：' + (await resp.text()));
    });
  });
}
```

### Step 9.3: 端到端验证 + Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): implement tab 4 ads detail + manual match

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 前端 — URL state 同步 + 国家筛选下拉填充

### Step 10.1: 国家下拉根据数据动态填充

- [ ] 在 `loadOrdersTab()` 拿到 `data.by_country` 后调用：

```javascript
function refreshCountryOptions(byCountry) {
  const sel = document.getElementById('ppd-country-select');
  const current = sel.value;
  const countries = (byCountry || []).map(c => c.country).filter(Boolean).sort();
  sel.innerHTML = '<option value="">全部</option>' +
    countries.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  if (countries.includes(current)) sel.value = current;
}
```

### Step 10.2: 全局 URL state 同步

- [ ] 添加：

```javascript
function syncUrlState() {
  const url = new URL(window.location.href);
  const sel = document.getElementById('ppd-product-select');
  const country = document.getElementById('ppd-country-select').value;
  url.searchParams.set('product_id', sel.value || '');
  url.searchParams.set('country', country);
  url.searchParams.set('date_from', document.getElementById('ppd-date-from').value);
  url.searchParams.set('date_to', document.getElementById('ppd-date-to').value);
  url.searchParams.set('tab', document.querySelector('.ppd-tab.active').dataset.tab);
  history.replaceState(null, '', url.toString());
}

// 把 syncUrlState() 接到 5 个变化点：tab 切换、产品下拉、国家下拉、日期变化、查询按钮
```

### Step 10.3: 页面加载时从 URL 还原状态

- [ ] 在 fetchProducts() 完成后追加：

```javascript
const urlParams = new URLSearchParams(location.search);
if (urlParams.get('product_id')) document.getElementById('ppd-product-select').value = urlParams.get('product_id');
if (urlParams.get('country')) document.getElementById('ppd-country-select').value = urlParams.get('country');
if (urlParams.get('date_from')) document.getElementById('ppd-date-from').value = urlParams.get('date_from');
if (urlParams.get('date_to')) document.getElementById('ppd-date-to').value = urlParams.get('date_to');
```

### Step 10.4: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "feat(product-profit): URL state sync for shareable filters

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: 视觉收尾 + 响应式

### Step 11.1: 颜色 hue 自检（禁紫色）

- [ ] grep 全文 `oklch\(`、`hsl\(`、`#`，确认所有 hue 在 200-240 / 红色 (warning/danger) / 绿色 (success)：

```bash
grep -nE 'oklch\(|hsl\(|rgba?\(' g:/Code/AutoVideoSrtLocal-product-profit-tabs/web/templates/product_profit_dashboard.html
```

- [ ] 任何 hue > 245 或紫色（`#a855f7`、`#7c3aed`、`indigo-*`、`violet-*`、`purple-*`）改成 `--accent` (oklch 56% 0.16 230) 或 `--cyan`。

### Step 11.2: 响应式 < 768px 单列

- [ ] 在 `<style>` 末尾追加：

```css
@media (max-width: 768px) {
  .ppd-main { padding: 12px; }
  .ppd-filters { flex-direction: column; align-items: stretch; }
  .ppd-stats { grid-template-columns: 1fr; }
  .ppd-charts { grid-template-columns: 1fr; }
  table { font-size: 12px; }
}
```

### Step 11.3: Commit

```bash
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs add web/templates/product_profit_dashboard.html
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs commit -m "polish(product-profit): purge non-blue hues + mobile single column

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 端到端验证 + 部署测试环境

### Step 12.1: 跑完整 pytest

```bash
cd g:/Code/AutoVideoSrtLocal-product-profit-tabs
pytest tests/test_product_profit_list.py tests/test_product_profit_ads.py tests/test_product_profit_routes.py -v
```
Expected: 全 PASS。如有失败，回到对应 Task 修。

### Step 12.2: dev server 端到端 Playwright 验证

- [ ] 启动 dev server：

```bash
cd g:/Code/AutoVideoSrtLocal-product-profit-tabs
# 仓库实际启动命令（按 readme/web/app.py 的入口）
python -m web.app  # 或 flask --app web.app run --port 5090
```

- [ ] 用 [testuser.md](../../../testuser.md) 凭据登录。验证：
  - 默认进入 Tab ①，全产品列表显示
  - 切到 Tab ② / ③ / ④（未选产品）→ 都给空状态提示
  - 选具体产品 → ②③④ 全部正常
  - Tab ① 点击产品行 → 切到 Tab ②、产品下拉锁定
  - 未匹配 campaign 配对按钮 → POST 成功后 reload
  - 修改日期 / 国家 / 产品 → URL 同步、刷新页面状态保留

### Step 12.3: 合并到 master + push

```bash
# 在 worktree 里
git -C g:/Code/AutoVideoSrtLocal-product-profit-tabs status   # 确认干净
# 切回主 worktree
cd g:/Code/AutoVideoSrtLocal
git checkout master
git merge --no-ff feature/product-profit-tabs-redesign -m "merge feature/product-profit-tabs-redesign"
git push origin master
```

### Step 12.4: 部署到测试环境（先测后线上）

按 [CLAUDE.md](../../../CLAUDE.md) §发布流程的 Path B（Windows 工作站）：

```powershell
ssh -i "$env:USERPROFILE\.ssh\CC.pem" -o BatchMode=yes root@172.30.254.14 @'
set -e
cd /opt/autovideosrt-test
git config --global --add safe.directory /opt/autovideosrt-test || true
git pull origin master --ff-only
systemctl restart autovideosrt-test
sleep 3
systemctl is-active autovideosrt-test
curl -s -o /dev/null -w "TEST HTTP %{http_code}\n" http://127.0.0.1:8080/product-profit
'@
```

期望 `active` + HTTP `302`（跳 login）。

### Step 12.5: 测试环境 UI 自验

- [ ] 浏览器访问 `http://172.30.254.14:8080/product-profit`，admin 登录，过一遍 4 个 Tab。

### Step 12.6: 清理 worktree（按 CLAUDE.md 硬规则）

```bash
cd g:/Code/AutoVideoSrtLocal
git worktree remove ../AutoVideoSrtLocal-product-profit-tabs
git branch -d feature/product-profit-tabs-redesign
```

---

## 自检（执行前看一遍，作者已 review）

**Spec 覆盖**：
- §6 产品列表 9 列 → Task 1, 7 ✅
- §7 订单明细零丢失 → Task 6 ✅
- §8 国家看板下钻 → Task 8 ✅
- §9 广告明细 + 未匹配区 → Task 3, 4, 9 ✅
- §11 4 个新端点 → Task 2, 4 ✅
- §12 视觉规范 → Task 5, 11 ✅
- §14 验收标准 → Task 12 step 12.2 ✅
- §15 ad_set 未来迭代（不在本 plan，已在 spec 标记）✅

**已知细节限制**（执行时按真实代码校准）：
- `cost_completeness.check_sku_cost_completeness()` 返回字段名以 [appcore/order_analytics/cost_completeness.py:44](../../../appcore/order_analytics/cost_completeness.py#L44) 实际实现为准（plan 假设是 `status`，若实际是 `state` 改字段引用）
- `resolve_ad_product_match()` 实际签名 / 返回结构以 [appcore/order_analytics/campaign_overrides.py](../../../appcore/order_analytics/campaign_overrides.py) 实现为准
- `meta_ad_realtime_daily_campaign_metrics` 表的列名 / `system_settings.meta_ad_accounts` JSON 字段以最新 schema 为准
- 现有 `product_profit_dashboard.html` 的 5 个区块迁移时按真实 DOM 结构调整，本 plan 给的 outline 是参考

**类型一致性**：`product_id`（int）、`date_from / date_to`（`datetime.date`）、`country`（str | None，大写）、API 返回字段名跨 task 统一。
