# 长期亏损品报警 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/ad-alerts` 新增「长期亏损品」子 Tab，按真实成本逐项扣（缺成本用销售额 8%/17% 估算兜底）算产品级盈亏，用「近7天亏损 ÷ 近30天盈利 > 10%（或长期净亏）」判定报警，排除新品、按近7天消耗降序。

**Architecture:** 新建独立模块 `appcore/ad_long_term_loss.py` 承载配置/数据聚合/判定/入口，复用 `order_analytics` 的 `order_profit_lines` 表与 `product_profit_list._load_ad_spend`。路由挂在现有 `web/routes/ad_alerts.py`，前端复用 `web/templates/ad_alerts.html` 的 Tab 框架。判定逻辑做成纯函数便于 TDD。

**Tech Stack:** Python 3、Flask、PyMySQL（`appcore.db.query`）、pytest（monkeypatch facade）、原生 JS 前端。

**Spec:** `docs/superpowers/specs/2026-06-14-ad-alert-long-term-loss-product-design.md`

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `appcore/ad_long_term_loss.py` | 配置读取 + 产品级窗口盈亏聚合 + 判定 + 入口 + 数据类 | 新建 |
| `appcore/ad_alert_actions.py` | 新增 `SCOPE_LONG_TERM_LOSS` + target key | 改 |
| `web/routes/ad_alerts.py` | `/api/long-term-loss` endpoint + 序列化 + tab 路由 | 改 |
| `web/templates/ad_alerts.html` | 新增「长期亏损品」Tab（markup + JS） | 改 |
| `appcore/ad_alert_daily_report.py` | 飞书推送增加长期亏损品榜 | 改 |
| `tests/test_ad_long_term_loss.py` | 判定 / 聚合 / 入口单测 | 新建 |
| `tests/test_ad_alert_actions.py` | scope 校验（若已存在则追加） | 改/新建 |

判定纯函数与数据聚合分离：判定只吃数字、无 IO，便于穷举边界；数据层负责 SQL 与估算，用 facade monkeypatch 测。

---

## Task 1: action scope 支持 long_term_loss

**Files:**
- Modify: `appcore/ad_alert_actions.py:13-15`（SCOPE 常量与 VALID_SCOPES）
- Modify: `appcore/ad_alert_actions.py`（新增 `long_term_loss_target_key`，挨着 `high_loss_target_key`）
- Test: `tests/test_ad_alert_actions.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_alert_actions.py
from appcore import ad_alert_actions


def test_long_term_loss_scope_is_valid():
    assert "long_term_loss" in ad_alert_actions.VALID_SCOPES
    assert ad_alert_actions.SCOPE_LONG_TERM_LOSS == "long_term_loss"


def test_long_term_loss_target_key_uses_product_id():
    assert ad_alert_actions.long_term_loss_target_key(123) == "product:123"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_alert_actions.py -v`
Expected: FAIL（`AttributeError: ... SCOPE_LONG_TERM_LOSS`）

- [ ] **Step 3: 实现**

`appcore/ad_alert_actions.py:13-15` 改为：

```python
SCOPE_HIGH_LOSS = "high_loss"
SCOPE_LANGUAGE = "language"
SCOPE_LONG_TERM_LOSS = "long_term_loss"
VALID_SCOPES = (SCOPE_HIGH_LOSS, SCOPE_LANGUAGE, SCOPE_LONG_TERM_LOSS)
```

在 `high_loss_target_key` 函数后新增：

```python
def long_term_loss_target_key(product_id: Any) -> str:
    return f"product:{int(product_id)}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_alert_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_alert_actions.py tests/test_ad_alert_actions.py
git commit -m "feat(ad-alert): add long_term_loss action scope"
```

---

## Task 2: 配置读取 get_ltl_config

**Files:**
- Create: `appcore/ad_long_term_loss.py`
- Test: `tests/test_ad_long_term_loss.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_long_term_loss.py
from appcore import ad_long_term_loss as ltl


def test_get_ltl_config_defaults(monkeypatch):
    monkeypatch.setattr(ltl.system_settings, "get_setting", lambda key: None)
    cfg = ltl.get_ltl_config()
    assert cfg["long_days"] == 30
    assert cfg["recent_days"] == 7
    assert cfg["loss_ratio"] == 0.10
    assert cfg["min_active_days"] == 10
    assert cfg["min_spend_7d"] == 50.0
    assert cfg["min_loss_7d"] == 20.0
    assert cfg["est_cost_rate"] == 0.08
    assert cfg["est_shipping_rate"] == 0.17


def test_get_ltl_config_reads_override(monkeypatch):
    overrides = {"ad_alert_ltl_loss_ratio": "0.2", "ad_alert_ltl_min_active_days": "14"}
    monkeypatch.setattr(ltl.system_settings, "get_setting", lambda key: overrides.get(key))
    cfg = ltl.get_ltl_config()
    assert cfg["loss_ratio"] == 0.2
    assert cfg["min_active_days"] == 14
    assert cfg["long_days"] == 30  # 未覆盖项回落默认
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_long_term_loss.py -v`
Expected: FAIL（`ModuleNotFoundError: appcore.ad_long_term_loss`）

- [ ] **Step 3: 实现模块骨架 + 配置**

```python
# appcore/ad_long_term_loss.py
"""长期亏损品报警：产品级真实利润 + 波动豁免规则。

Docs-anchor: docs/superpowers/specs/2026-06-14-ad-alert-long-term-loss-product-design.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from appcore import settings as system_settings
from appcore.db import query
from appcore.order_analytics._helpers import current_meta_business_date

log = logging.getLogger(__name__)

# (setting_key, default, caster)
_LTL_SETTINGS: dict[str, tuple[str, float, type]] = {
    "long_days": ("ad_alert_ltl_long_days", 30, int),
    "recent_days": ("ad_alert_ltl_recent_days", 7, int),
    "loss_ratio": ("ad_alert_ltl_loss_ratio", 0.10, float),
    "min_active_days": ("ad_alert_ltl_min_active_days", 10, int),
    "min_spend_7d": ("ad_alert_ltl_min_spend_7d", 50.0, float),
    "min_loss_7d": ("ad_alert_ltl_min_loss_7d", 20.0, float),
    "est_cost_rate": ("ad_alert_ltl_est_cost_rate", 0.08, float),
    "est_shipping_rate": ("ad_alert_ltl_est_shipping_rate", 0.17, float),
}


def get_ltl_config() -> dict[str, float]:
    cfg: dict[str, float] = {}
    for name, (key, default, caster) in _LTL_SETTINGS.items():
        raw = None
        try:
            raw = system_settings.get_setting(key)
        except Exception:
            raw = None
        if raw is None or str(raw).strip() == "":
            cfg[name] = default
            continue
        try:
            cfg[name] = caster(str(raw).strip())
        except (TypeError, ValueError):
            cfg[name] = default
    return cfg
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_long_term_loss.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_long_term_loss.py tests/test_ad_long_term_loss.py
git commit -m "feat(ad-alert): long-term-loss module config reader"
```

---

## Task 3: 判定纯函数 judge_long_term_loss

**Files:**
- Modify: `appcore/ad_long_term_loss.py`（追加数据类与判定函数）
- Test: `tests/test_ad_long_term_loss.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_long_term_loss.py （追加）
def test_judge_recent_not_loss_is_skipped():
    v = ltl.judge_long_term_loss(profit_7d=5.0, profit_30d=-100.0, loss_ratio=0.10)
    assert v.alert is False
    assert v.verdict is None


def test_judge_long_term_net_loss_alerts():
    v = ltl.judge_long_term_loss(profit_7d=-30.0, profit_30d=-10.0, loss_ratio=0.10)
    assert v.alert is True
    assert v.verdict == "long_term_net_loss"
    assert v.loss_7d == 30.0
    assert v.loss_ratio is None


def test_judge_erodes_profit_over_threshold():
    # 近7天亏100，近30天赚500 → 20% > 10% → 报
    v = ltl.judge_long_term_loss(profit_7d=-100.0, profit_30d=500.0, loss_ratio=0.10)
    assert v.alert is True
    assert v.verdict == "erodes_profit"
    assert v.loss_7d == 100.0
    assert round(v.loss_ratio, 4) == 0.2


def test_judge_small_loss_is_volatility():
    # 近7天亏10，近30天赚500 → 2% <= 10% → 放行
    v = ltl.judge_long_term_loss(profit_7d=-10.0, profit_30d=500.0, loss_ratio=0.10)
    assert v.alert is False
    assert v.verdict is None
    assert round(v.loss_ratio, 4) == 0.02


def test_judge_ratio_boundary_equal_is_volatility():
    # 恰好等于阈值 → 不报（用 > 严格大于）
    v = ltl.judge_long_term_loss(profit_7d=-50.0, profit_30d=500.0, loss_ratio=0.10)
    assert v.alert is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_long_term_loss.py -k judge -v`
Expected: FAIL（`AttributeError: judge_long_term_loss`）

- [ ] **Step 3: 实现数据类与判定**

在 `appcore/ad_long_term_loss.py` 追加：

```python
@dataclass
class LtlVerdict:
    alert: bool
    verdict: str | None  # "long_term_net_loss" | "erodes_profit" | None
    loss_7d: float
    loss_ratio: float | None


def judge_long_term_loss(
    *, profit_7d: float, profit_30d: float, loss_ratio: float
) -> LtlVerdict:
    """对单品的窗口盈亏做判定。详见 spec「判定规则」。"""
    if profit_7d >= 0:
        return LtlVerdict(False, None, 0.0, None)
    loss_7d = -profit_7d
    if profit_30d <= 0:
        return LtlVerdict(True, "long_term_net_loss", loss_7d, None)
    ratio = loss_7d / profit_30d
    if ratio > loss_ratio:
        return LtlVerdict(True, "erodes_profit", loss_7d, ratio)
    return LtlVerdict(False, None, loss_7d, ratio)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_long_term_loss.py -k judge -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_long_term_loss.py tests/test_ad_long_term_loss.py
git commit -m "feat(ad-alert): long-term-loss judgement pure function"
```

---

## Task 4: 产品级窗口盈亏聚合 _load_window_metrics

**Files:**
- Modify: `appcore/ad_long_term_loss.py`（追加聚合函数）
- Test: `tests/test_ad_long_term_loss.py`

口径：订单侧从 `order_profit_lines` 聚合 revenue/fee/return_reserve，货物与物流按行 `missing_fields` 决定真实值或估算（缺货物→`revenue×est_cost_rate`，缺物流→`revenue×est_shipping_rate`）；广告费用 `product_profit_list._load_ad_spend`（全额，含 realtime fallback）；`active_days` 用 `meta_ad_daily_ad_metrics` 近30天有 spend 的去重天数。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_long_term_loss.py （追加）
from datetime import date
from decimal import Decimal


def test_load_window_metrics_uses_real_and_estimated_cost(monkeypatch):
    business_date = date(2026, 6, 14)
    cfg = ltl.get_ltl_config()

    # 订单侧聚合行：品100完备(status ok)，品200缺成本(走估算)
    order_rows = [
        {
            "product_id": 100, "product_code": "P100", "product_name": "完备品",
            "product_main_image": None,
            "revenue_7d": 1000.0, "fee_7d": 30.0, "purchase_7d": 200.0,
            "shipping_7d": 150.0, "rr_7d": 10.0,
            "revenue_30d": 5000.0, "fee_30d": 150.0, "purchase_30d": 1000.0,
            "shipping_30d": 750.0, "rr_30d": 50.0,
            "has_estimated": 0,
            "first_active_date": date(2026, 5, 1), "last_active_date": business_date,
        },
        {
            "product_id": 200, "product_code": "P200", "product_name": "缺成本品",
            "product_main_image": None,
            "revenue_7d": 1000.0, "fee_7d": 30.0, "purchase_7d": 80.0,   # 已按8%估
            "shipping_7d": 170.0, "rr_7d": 10.0,                          # 已按17%估
            "revenue_30d": 4000.0, "fee_30d": 120.0, "purchase_30d": 320.0,
            "shipping_30d": 680.0, "rr_30d": 40.0,
            "has_estimated": 1,
            "first_active_date": date(2026, 5, 10), "last_active_date": business_date,
        },
    ]
    active_rows = [
        {"product_id": 100, "active_days": 28},
        {"product_id": 200, "active_days": 20},
    ]

    def fake_query(sql, params=None):
        if "FROM order_profit_lines" in sql:
            return order_rows
        if "active_days" in sql:
            return active_rows
        return []

    monkeypatch.setattr(ltl, "query", fake_query)
    monkeypatch.setattr(ltl, "ensure_open_day_profit_lines_fresh", lambda a, b: None)
    monkeypatch.setattr(
        ltl, "_load_ad_spend",
        lambda d_from, d_to, country=None: (
            {100: Decimal("700"), 200: Decimal("900")}
            if (d_to - d_from).days <= cfg["recent_days"]
            else {100: Decimal("3500"), 200: Decimal("3600")}
        ),
    )

    metrics = ltl._load_window_metrics(business_date, cfg)
    m100 = metrics[100]
    # profit_7d = 1000 - 30 - 200 - 150 - 10 - 700 = -90
    assert round(m100.profit_7d, 2) == -90.0
    # profit_30d = 5000 - 150 - 1000 - 750 - 50 - 3500 = -450
    assert round(m100.profit_30d, 2) == -450.0
    assert m100.spend_7d == 700.0
    assert m100.active_days == 28
    assert m100.has_estimated_cost is False
    assert metrics[200].has_estimated_cost is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_long_term_loss.py -k window -v`
Expected: FAIL（`AttributeError: _load_window_metrics`）

- [ ] **Step 3: 实现聚合**

在 `appcore/ad_long_term_loss.py` 顶部 import 区追加：

```python
from appcore.order_analytics._open_day_freshness import ensure_open_day_profit_lines_fresh
from appcore.order_analytics.product_profit_list import _load_ad_spend
```

追加数据类与函数：

```python
@dataclass
class WindowMetric:
    product_id: int
    product_code: str
    product_name: str
    product_main_image: str | None
    revenue_7d: float
    profit_7d: float
    revenue_30d: float
    profit_30d: float
    spend_7d: float
    active_days: int
    has_estimated_cost: bool
    first_active_date: date | None
    last_active_date: date | None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_window_metrics(business_date: date, cfg: dict[str, float]) -> dict[int, WindowMetric]:
    long_days = int(cfg["long_days"])
    recent_days = int(cfg["recent_days"])
    d30 = business_date - timedelta(days=long_days - 1)
    d7 = business_date - timedelta(days=recent_days - 1)

    ensure_open_day_profit_lines_fresh(d30, business_date)

    params = {
        "d30": d30,
        "d7": d7,
        "today": business_date,
        "cost_rate": cfg["est_cost_rate"],
        "ship_rate": cfg["est_shipping_rate"],
    }
    # 缺货物→revenue*cost_rate；缺物流→revenue*ship_rate。missing_fields 是 JSON 文本，用 LIKE 判断。
    rows = query(
        """
        SELECT
          opl.product_id,
          MAX(mp.product_code) AS product_code,
          MAX(mp.name) AS product_name,
          MAX(mp.main_image) AS product_main_image,
          MIN(dol.meta_business_date) AS first_active_date,
          MAX(dol.meta_business_date) AS last_active_date,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN opl.revenue_usd ELSE 0 END) AS revenue_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN opl.shopify_fee_usd ELSE 0 END) AS fee_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN opl.return_reserve_usd ELSE 0 END) AS rr_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN
                CASE WHEN opl.missing_fields LIKE '%%purchase_price%%'
                     THEN opl.revenue_usd * %(cost_rate)s ELSE opl.purchase_usd END
              ELSE 0 END) AS purchase_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN
                CASE WHEN opl.missing_fields LIKE '%%shipping_cost%%'
                     THEN opl.revenue_usd * %(ship_rate)s ELSE opl.shipping_cost_usd END
              ELSE 0 END) AS shipping_7d,
          SUM(opl.revenue_usd) AS revenue_30d,
          SUM(opl.shopify_fee_usd) AS fee_30d,
          SUM(opl.return_reserve_usd) AS rr_30d,
          SUM(CASE WHEN opl.missing_fields LIKE '%%purchase_price%%'
                   THEN opl.revenue_usd * %(cost_rate)s ELSE opl.purchase_usd END) AS purchase_30d,
          SUM(CASE WHEN opl.missing_fields LIKE '%%shipping_cost%%'
                   THEN opl.revenue_usd * %(ship_rate)s ELSE opl.shipping_cost_usd END) AS shipping_30d,
          MAX(CASE WHEN opl.status <> 'ok' THEN 1 ELSE 0 END) AS has_estimated
        FROM order_profit_lines opl
        JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id
        LEFT JOIN media_products mp ON mp.id = opl.product_id
        WHERE dol.meta_business_date BETWEEN %(d30)s AND %(today)s
          AND opl.product_id IS NOT NULL
        GROUP BY opl.product_id
        """,
        params,
    )

    active_rows = query(
        """
        SELECT product_id,
               COUNT(DISTINCT COALESCE(meta_business_date, report_date)) AS active_days
        FROM meta_ad_daily_ad_metrics
        WHERE COALESCE(meta_business_date, report_date) BETWEEN %(d30)s AND %(today)s
          AND product_id IS NOT NULL
          AND COALESCE(spend_usd, 0) > 0
        GROUP BY product_id
        """,
        params,
    )
    active_by_pid = {int(r["product_id"]): int(r["active_days"] or 0) for r in active_rows or []}

    spend_7d_by_pid = _load_ad_spend(d7, business_date)
    spend_30d_by_pid = _load_ad_spend(d30, business_date)

    out: dict[int, WindowMetric] = {}
    for r in rows or []:
        pid = int(r["product_id"])
        spend_7d = float(spend_7d_by_pid.get(pid, 0) or 0)
        spend_30d = float(spend_30d_by_pid.get(pid, 0) or 0)
        rev_7d = _safe_float(r.get("revenue_7d"))
        rev_30d = _safe_float(r.get("revenue_30d"))
        profit_7d = (
            rev_7d - _safe_float(r.get("fee_7d")) - _safe_float(r.get("purchase_7d"))
            - _safe_float(r.get("shipping_7d")) - _safe_float(r.get("rr_7d")) - spend_7d
        )
        profit_30d = (
            rev_30d - _safe_float(r.get("fee_30d")) - _safe_float(r.get("purchase_30d"))
            - _safe_float(r.get("shipping_30d")) - _safe_float(r.get("rr_30d")) - spend_30d
        )
        out[pid] = WindowMetric(
            product_id=pid,
            product_code=str(r.get("product_code") or ""),
            product_name=str(r.get("product_name") or ""),
            product_main_image=r.get("product_main_image"),
            revenue_7d=round(rev_7d, 2),
            profit_7d=round(profit_7d, 2),
            revenue_30d=round(rev_30d, 2),
            profit_30d=round(profit_30d, 2),
            spend_7d=round(spend_7d, 2),
            active_days=active_by_pid.get(pid, 0),
            has_estimated_cost=bool(r.get("has_estimated")),
            first_active_date=r.get("first_active_date"),
            last_active_date=r.get("last_active_date"),
        )
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_long_term_loss.py -k window -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_long_term_loss.py tests/test_ad_long_term_loss.py
git commit -m "feat(ad-alert): product window profit aggregation with 8/17% cost fallback"
```

---

## Task 5: 入口 get_long_term_loss_products（组合判定/门槛/排序/action）

**Files:**
- Modify: `appcore/ad_long_term_loss.py`（追加 `LongTermLossItem`、`_product_detail_url`、入口）
- Test: `tests/test_ad_long_term_loss.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_long_term_loss.py （追加）
def _wm(pid, profit_7d, profit_30d, spend_7d, active_days, has_est=False):
    return ltl.WindowMetric(
        product_id=pid, product_code=f"P{pid}", product_name=f"品{pid}",
        product_main_image=None, revenue_7d=1000.0, profit_7d=profit_7d,
        revenue_30d=5000.0, profit_30d=profit_30d, spend_7d=spend_7d,
        active_days=active_days, has_estimated_cost=has_est,
        first_active_date=date(2026, 5, 1), last_active_date=date(2026, 6, 14),
    )


def test_get_products_filters_sorts_and_excludes_new(monkeypatch):
    business_date = date(2026, 6, 14)
    metrics = {
        1: _wm(1, profit_7d=-200.0, profit_30d=-50.0, spend_7d=800.0, active_days=28),  # 长期净亏，消耗大
        2: _wm(2, profit_7d=-100.0, profit_30d=300.0, spend_7d=500.0, active_days=28),  # 33% 侵蚀，报
        3: _wm(3, profit_7d=-10.0, profit_30d=500.0, spend_7d=600.0, active_days=28),   # 2% 波动，放行
        4: _wm(4, profit_7d=-300.0, profit_30d=-100.0, spend_7d=900.0, active_days=3),  # 新品，排除
        5: _wm(5, profit_7d=-25.0, profit_30d=-5.0, spend_7d=30.0, active_days=28),     # 消耗<50，过滤
    }
    monkeypatch.setattr(ltl, "current_meta_business_date", lambda: business_date)
    monkeypatch.setattr(ltl, "_load_window_metrics", lambda bd, cfg: metrics)
    monkeypatch.setattr(ltl, "_attach_consecutive_loss_days", lambda items, bd, cfg: None)
    monkeypatch.setattr(ltl.system_settings, "get_setting", lambda key: None)

    from appcore import ad_alert_actions
    monkeypatch.setattr(ad_alert_actions, "get_actions", lambda scope, keys: {})

    bd, items = ltl.get_long_term_loss_products(limit=10)
    ids = [it.product_id for it in items]
    assert ids == [1, 2]  # 3波动/4新品/5小额 都被排除；按 spend_7d 降序 1(800)>2(500)
    assert items[0].verdict == "long_term_net_loss"
    assert items[1].verdict == "erodes_profit"
    assert items[0].loss_7d == 200.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_long_term_loss.py -k get_products -v`
Expected: FAIL（`AttributeError: get_long_term_loss_products`）

- [ ] **Step 3: 实现入口**

在 `appcore/ad_long_term_loss.py` 追加（`_attach_consecutive_loss_days` 在 Task 6 实现，这里先给空安全占位由 Task 6 替换 —— 为避免占位，Task 6 会先于本步合并；本步实现引用它）：

```python
from urllib.parse import urlencode

from appcore import ad_alert_actions


@dataclass
class LongTermLossItem:
    product_id: int
    product_code: str
    product_name: str
    product_main_image: str | None
    spend_7d: float
    profit_7d: float
    loss_7d: float
    profit_30d: float
    loss_ratio: float | None
    verdict: str
    active_days: int
    consecutive_loss_days: int
    first_active_date: str | None
    has_estimated_cost: bool
    detail_url: str
    action: dict[str, Any] | None = None


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)[:10]


def _product_detail_url(product_id: int, start: date, end: date) -> str:
    params = {
        "tab": "product-profit",
        "product_id": str(product_id),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return "/order-analytics?" + urlencode(params)


def get_long_term_loss_products(
    *, search: str | None = None, limit: int = 30, include_handled: bool = False
) -> tuple[date, list[LongTermLossItem]]:
    business_date = current_meta_business_date()
    cfg = get_ltl_config()
    safe_limit = max(1, min(int(limit or 30), 100))
    d30 = business_date - timedelta(days=int(cfg["long_days"]) - 1)

    metrics = _load_window_metrics(business_date, cfg)
    search_l = (search or "").strip().lower()

    candidates: list[LongTermLossItem] = []
    for wm in metrics.values():
        if wm.active_days < cfg["min_active_days"]:
            continue
        v = judge_long_term_loss(
            profit_7d=wm.profit_7d, profit_30d=wm.profit_30d, loss_ratio=cfg["loss_ratio"]
        )
        if not v.alert:
            continue
        if wm.spend_7d < cfg["min_spend_7d"] or v.loss_7d < cfg["min_loss_7d"]:
            continue
        if search_l and search_l not in (wm.product_code or "").lower() and search_l not in (wm.product_name or "").lower():
            continue
        candidates.append(
            LongTermLossItem(
                product_id=wm.product_id,
                product_code=wm.product_code or str(wm.product_id),
                product_name=wm.product_name,
                product_main_image=wm.product_main_image,
                spend_7d=wm.spend_7d,
                profit_7d=wm.profit_7d,
                loss_7d=round(v.loss_7d, 2),
                profit_30d=wm.profit_30d,
                loss_ratio=round(v.loss_ratio, 4) if v.loss_ratio is not None else None,
                verdict=v.verdict or "",
                active_days=wm.active_days,
                consecutive_loss_days=0,
                first_active_date=_iso_date(wm.first_active_date),
                has_estimated_cost=wm.has_estimated_cost,
                detail_url=_product_detail_url(wm.product_id, d30, business_date),
            )
        )

    candidates.sort(key=lambda it: (it.spend_7d, it.loss_7d), reverse=True)

    keys = [ad_alert_actions.long_term_loss_target_key(it.product_id) for it in candidates]
    try:
        action_map = ad_alert_actions.get_actions(ad_alert_actions.SCOPE_LONG_TERM_LOSS, keys)
    except Exception:
        log.warning("long term loss action lookup failed", exc_info=True)
        action_map = {}

    kept: list[LongTermLossItem] = []
    for it, key in zip(candidates, keys):
        it.action = action_map.get(key)
        if not include_handled and it.action is not None:
            continue
        kept.append(it)
        if len(kept) >= safe_limit:
            break

    _attach_consecutive_loss_days(kept, business_date, cfg)
    return business_date, kept
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_long_term_loss.py -k get_products -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_long_term_loss.py tests/test_ad_long_term_loss.py
git commit -m "feat(ad-alert): long-term-loss entry with filter/sort/action"
```

---

## Task 6: 连续亏损天数（利润口径）

**Files:**
- Modify: `appcore/ad_long_term_loss.py`（实现 `_attach_consecutive_loss_days`）
- Test: `tests/test_ad_long_term_loss.py`

口径：从 `business_date` 往回，逐日「订单侧日利润 − 当日 daily 广告 spend」< 0 计为亏损日，遇到非亏损日或无数据停止。daily 广告口径（当天 realtime 未计入，为近似展示值）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_long_term_loss.py （追加）
def test_attach_consecutive_loss_days(monkeypatch):
    business_date = date(2026, 6, 14)
    cfg = ltl.get_ltl_config()
    # 品1：6/14 亏、6/13 亏、6/12 赚 → 连续2天
    order_daily = [
        {"product_id": 1, "d": date(2026, 6, 14), "revenue": 100.0, "fee": 3.0, "purchase": 10.0, "shipping": 17.0, "rr": 1.0},
        {"product_id": 1, "d": date(2026, 6, 13), "revenue": 100.0, "fee": 3.0, "purchase": 10.0, "shipping": 17.0, "rr": 1.0},
        {"product_id": 1, "d": date(2026, 6, 12), "revenue": 500.0, "fee": 3.0, "purchase": 10.0, "shipping": 17.0, "rr": 1.0},
    ]
    ad_daily = {(1, date(2026, 6, 14)): 200.0, (1, date(2026, 6, 13)): 200.0, (1, date(2026, 6, 12)): 50.0}

    def fake_query(sql, params=None):
        return order_daily if "FROM order_profit_lines" in sql else []
    monkeypatch.setattr(ltl, "query", fake_query)
    monkeypatch.setattr(ltl, "_load_daily_ad_spend_map", lambda d_from, d_to: ad_daily)

    items = [ltl.LongTermLossItem(
        product_id=1, product_code="P1", product_name="品1", product_main_image=None,
        spend_7d=400.0, profit_7d=-50.0, loss_7d=50.0, profit_30d=100.0, loss_ratio=None,
        verdict="long_term_net_loss", active_days=28, consecutive_loss_days=0,
        first_active_date="2026-05-01", has_estimated_cost=False, detail_url="",
    )]
    ltl._attach_consecutive_loss_days(items, business_date, cfg)
    assert items[0].consecutive_loss_days == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_long_term_loss.py -k consecutive -v`
Expected: FAIL（`AttributeError: _load_daily_ad_spend_map` / `_attach_consecutive_loss_days` 缺实现）

- [ ] **Step 3: 实现**

```python
def _load_daily_ad_spend_map(d_from: date, d_to: date) -> dict[tuple[int, date], float]:
    rows = query(
        """
        SELECT COALESCE(meta_business_date, report_date) AS d, product_id,
               COALESCE(SUM(spend_usd), 0) AS spend
        FROM meta_ad_daily_ad_metrics
        WHERE COALESCE(meta_business_date, report_date) BETWEEN %(d_from)s AND %(d_to)s
          AND product_id IS NOT NULL
        GROUP BY COALESCE(meta_business_date, report_date), product_id
        """,
        {"d_from": d_from, "d_to": d_to},
    )
    out: dict[tuple[int, date], float] = {}
    for r in rows or []:
        d = r.get("d")
        pid = r.get("product_id")
        if d is None or pid is None:
            continue
        if hasattr(d, "date"):
            d = d.date()
        out[(int(pid), d)] = _safe_float(r.get("spend"))
    return out


def _attach_consecutive_loss_days(
    items: list["LongTermLossItem"], business_date: date, cfg: dict[str, float]
) -> None:
    if not items:
        return
    long_days = int(cfg["long_days"])
    d30 = business_date - timedelta(days=long_days - 1)
    pids = [it.product_id for it in items]
    placeholders = ",".join(["%s"] * len(pids))
    rows = query(
        f"""
        SELECT opl.product_id, dol.meta_business_date AS d,
          SUM(opl.revenue_usd) AS revenue,
          SUM(opl.shopify_fee_usd) AS fee,
          SUM(opl.return_reserve_usd) AS rr,
          SUM(CASE WHEN opl.missing_fields LIKE '%%purchase_price%%'
                   THEN opl.revenue_usd * %s ELSE opl.purchase_usd END) AS purchase,
          SUM(CASE WHEN opl.missing_fields LIKE '%%shipping_cost%%'
                   THEN opl.revenue_usd * %s ELSE opl.shipping_cost_usd END) AS shipping
        FROM order_profit_lines opl
        JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id
        WHERE opl.product_id IN ({placeholders})
          AND dol.meta_business_date BETWEEN %s AND %s
        GROUP BY opl.product_id, dol.meta_business_date
        """,
        (cfg["est_cost_rate"], cfg["est_shipping_rate"], *pids, d30, business_date),
    )
    order_by_pid_day: dict[int, dict[date, float]] = {}
    for r in rows or []:
        d = r.get("d")
        if hasattr(d, "date"):
            d = d.date()
        pid = int(r["product_id"])
        order_profit = (
            _safe_float(r.get("revenue")) - _safe_float(r.get("fee"))
            - _safe_float(r.get("purchase")) - _safe_float(r.get("shipping"))
            - _safe_float(r.get("rr"))
        )
        order_by_pid_day.setdefault(pid, {})[d] = order_profit

    ad_map = _load_daily_ad_spend_map(d30, business_date)
    for it in items:
        day = business_date
        loss_days = 0
        for _ in range(long_days):
            order_profit = order_by_pid_day.get(it.product_id, {}).get(day)
            if order_profit is None:
                break
            daily_profit = order_profit - ad_map.get((it.product_id, day), 0.0)
            if daily_profit >= 0:
                break
            loss_days += 1
            day -= timedelta(days=1)
        it.consecutive_loss_days = loss_days
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_long_term_loss.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_long_term_loss.py tests/test_ad_long_term_loss.py
git commit -m "feat(ad-alert): consecutive loss days (profit basis)"
```

---

## Task 7: API endpoint /api/long-term-loss

**Files:**
- Modify: `web/routes/ad_alerts.py`（新增 endpoint + `_long_term_loss_item_to_dict`，挨着 `api_high_loss_ads`/`_high_loss_ad_item_to_dict`）
- Test: `tests/test_ad_alerts_routes.py`（若无则新建；参照现有路由测试风格）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_alerts_routes.py （追加；沿用项目既有 Flask test client fixture）
def test_api_long_term_loss_returns_items(client, monkeypatch, admin_login):
    from datetime import date
    from appcore import ad_long_term_loss as ltl

    item = ltl.LongTermLossItem(
        product_id=1, product_code="P1", product_name="品1", product_main_image=None,
        spend_7d=800.0, profit_7d=-200.0, loss_7d=200.0, profit_30d=-50.0, loss_ratio=None,
        verdict="long_term_net_loss", active_days=28, consecutive_loss_days=3,
        first_active_date="2026-05-01", has_estimated_cost=True, detail_url="/order-analytics?x=1",
    )
    monkeypatch.setattr(
        "appcore.ad_long_term_loss.get_long_term_loss_products",
        lambda **kw: (date(2026, 6, 14), [item]),
    )
    resp = client.get("/ad-alerts/api/long-term-loss?limit=10")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["business_date"] == "2026-06-14"
    assert data["total"] == 1
    assert data["estimated_product_count"] == 1
    assert data["items"][0]["verdict"] == "long_term_net_loss"
    assert data["items"][0]["loss_7d"] == 200.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_alerts_routes.py -k long_term_loss -v`
Expected: FAIL（404 或 endpoint 不存在）

- [ ] **Step 3: 实现 endpoint**

在 `web/routes/ad_alerts.py` import 区确认 `from appcore import ad_long_term_loss`（缺则加）。在 `api_high_loss_ads` 后新增：

```python
@bp.route("/api/long-term-loss")
@login_required
@admin_required
def api_long_term_loss():
    """长期亏损品 JSON API。"""
    search = (request.args.get("q") or "").strip() or None
    try:
        limit = int(request.args.get("limit") or 30)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid limit"}), 400
    business_date, items = ad_long_term_loss.get_long_term_loss_products(
        search=search,
        limit=limit,
        include_handled=_parse_include_handled(request.args.get("include_handled")),
    )
    return jsonify({
        "business_date": business_date.isoformat(),
        "items": [_long_term_loss_item_to_dict(it) for it in items],
        "total": len(items),
        "estimated_product_count": sum(1 for it in items if it.has_estimated_cost),
    })
```

在文件序列化区（挨着 `_high_loss_ad_item_to_dict`）新增：

```python
def _long_term_loss_item_to_dict(item: ad_long_term_loss.LongTermLossItem) -> dict[str, Any]:
    return {
        "product_id": item.product_id,
        "product_code": item.product_code,
        "product_name": item.product_name,
        "product_main_image": item.product_main_image,
        "spend_7d": item.spend_7d,
        "profit_7d": item.profit_7d,
        "loss_7d": item.loss_7d,
        "profit_30d": item.profit_30d,
        "loss_ratio": item.loss_ratio,
        "verdict": item.verdict,
        "active_days": item.active_days,
        "consecutive_loss_days": item.consecutive_loss_days,
        "first_active_date": item.first_active_date,
        "has_estimated_cost": item.has_estimated_cost,
        "detail_url": item.detail_url,
        "action": item.action,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_alerts_routes.py -k long_term_loss -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/routes/ad_alerts.py tests/test_ad_alerts_routes.py
git commit -m "feat(ad-alert): /api/long-term-loss endpoint"
```

---

## Task 8: 前端「长期亏损品」Tab

**Files:**
- Modify: `web/templates/ad_alerts.html`（Tab 按钮、面板 markup、JS 加载/渲染；参照现有 `adAlertHighLoss*` 实现）
- Verify: webapp-testing（Playwright）

注意：前端无 pytest，用 webapp-testing 验证。复制现有高额亏损 Tab 的结构改字段即可，保持 Ocean Blue 设计系统（见 `web/static/CLAUDE.md`）。

- [ ] **Step 1: 加 Tab 按钮**

在 `.oc-ad-alert-tabs` 容器（现有「广告预警/问题广告/高额亏损」按钮处）追加：

```html
<button class="oc-ad-alert-tab" type="button" data-tab="long_loss">长期亏损品</button>
```

- [ ] **Step 2: 加面板 markup**

在高额亏损面板（`adAlertHighLossList` 所在 section）后追加：

```html
<section class="oc-ad-alert-panel" id="adAlertLongLossPanel" hidden>
  <div class="oc-ad-alert-highloss-toolbar">
    <div class="oc-ad-alert-business-date" id="adAlertLongLossDate">Meta 业务日：加载中</div>
    <div class="oc-ad-alert-search-wrap">
      <input class="oc-ad-alert-search" id="adAlertLongLossSearch" type="search" placeholder="搜索商品名称或 product code">
      <button class="oc-ad-alert-btn" type="button" id="adAlertLongLossRefresh">查询</button>
    </div>
  </div>
  <div class="oc-ad-alert-highloss-note">长期亏损品：近30天有投放(≥10天)的品，近7天在亏，且近7天亏损吃掉近30天利润10%以上，或近30天本身净亏；含估算成本的品已标注。</div>
  <div class="oc-ad-alert-state" id="adAlertLongLossEstimateHint" hidden></div>
  <div class="oc-ad-alert-list" id="adAlertLongLossList"></div>
</section>
```

- [ ] **Step 3: 加 JS 加载/渲染**

在现有脚本（`renderHighLossAds` 附近）追加，并把 `long_loss` 接入既有 Tab 切换逻辑（找到切 Tab 的 `switchTab`/`data-tab` 处理处，新增分支调用 `loadLongLossAds()`）：

```javascript
function loadLongLossAds() {
  var list = document.getElementById('adAlertLongLossList');
  if (!list) return;
  list.innerHTML = '<div class="oc-ad-alert-state loading">加载中</div>';
  var params = new URLSearchParams();
  var search = document.getElementById('adAlertLongLossSearch');
  if (search && search.value.trim()) params.set('q', search.value.trim());
  fetch('/ad-alerts/api/long-term-loss?' + params.toString(), { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var dateEl = document.getElementById('adAlertLongLossDate');
      if (dateEl) dateEl.textContent = 'Meta 业务日：' + (data.business_date || '—') + ' · ' + (data.total || 0) + ' 条';
      var hint = document.getElementById('adAlertLongLossEstimateHint');
      if (hint) {
        if (data.estimated_product_count > 0) {
          hint.hidden = false;
          hint.textContent = '其中 ' + data.estimated_product_count + ' 个品成本缺失、用估算判定，建议在产品盈亏页补录成本。';
        } else { hint.hidden = true; }
      }
      renderLongLossAds(data.items || []);
    })
    .catch(function () {
      list.innerHTML = '<div class="oc-ad-alert-state">加载失败</div>';
    });
}

function renderLongLossAds(items) {
  var list = document.getElementById('adAlertLongLossList');
  if (!list) return;
  if (!items.length) {
    list.innerHTML = '<div class="oc-ad-alert-state">当前没有命中长期亏损规则的品</div>';
    return;
  }
  list.innerHTML = items.map(function (item) {
    var verdictLabel = item.verdict === 'long_term_net_loss' ? '长期净亏' : '亏损侵蚀利润';
    var lossDays = Number(item.consecutive_loss_days || 0);
    var lossBadge = lossDays > 0
      ? '<span class="oc-ad-alert-badge oc-ad-alert-severe">连续亏损 ' + lossDays + ' 天</span>' : '';
    var estBadge = item.has_estimated_cost
      ? '<span class="oc-ad-alert-badge">含估算</span>' : '';
    var ratio = item.loss_ratio != null ? (item.loss_ratio * 100).toFixed(0) + '%' : '—';
    return ''
      + '<article class="oc-ad-alert-card oc-ad-alert-highloss-card" role="button" tabindex="0" data-detail-url="' + html(item.detail_url || '') + '">'
      + '  <div class="oc-ad-alert-card-content">'
      + '    <div class="oc-ad-alert-card-title">' + html(item.product_name || item.product_code) + ' '
      + '      <span class="oc-ad-alert-badge oc-ad-alert-severe">' + verdictLabel + '</span> ' + lossBadge + ' ' + estBadge + '</div>'
      + '    <div class="oc-ad-alert-highloss-metrics">'
      + '      <span>近7天消耗 <strong>$' + Number(item.spend_7d || 0).toFixed(0) + '</strong></span>'
      + '      <span>近7天亏损 <strong>$' + Number(item.loss_7d || 0).toFixed(0) + '</strong></span>'
      + '      <span>近30天利润 <strong>$' + Number(item.profit_30d || 0).toFixed(0) + '</strong></span>'
      + '      <span>亏损占比 <strong>' + ratio + '</strong></span>'
      + '      <span>在投 <strong>' + Number(item.active_days || 0) + ' 天</strong></span>'
      + '      <span>首投 ' + html(item.first_active_date || '—') + '</span>'
      + '    </div>'
      + '  </div>'
      + '</article>';
  }).join('');
}
```

- [ ] **Step 4: webapp-testing 验证**

用 webapp-testing 启动本地服务，登录后访问 `/ad-alerts/?tab=long_loss`，截图确认：Tab 可切换、列表渲染、判定标签/含估算标显示、估算提示条按数据出现。

- [ ] **Step 5: Commit**

```bash
git add web/templates/ad_alerts.html
git commit -m "feat(ad-alert): long-term-loss products tab (frontend)"
```

---

## Task 9: 页面 Tab 路由参数

**Files:**
- Modify: `web/routes/ad_alerts.py`（`list_page`/`alerts_page_route`，支持 `?tab=long_loss` 把 `active_tab` 传给模板）
- Verify: webapp-testing

- [ ] **Step 1: 找到现有 tab → active_tab 传参处**

`web/routes/ad_alerts.py` 的 `list_page()` / `alerts_page_route()` 已根据 query 决定 `active_tab` 渲染模板。确认其取值集合，新增允许 `long_loss`。

- [ ] **Step 2: 实现**

在解析 tab 的位置（render_template 传 `active_tab=...` 处）把 `long_loss` 纳入白名单，例如：

```python
tab = (request.args.get("tab") or "").strip().lower()
active_tab = tab if tab in {"problem", "high_loss", "long_loss"} else "alerts"
```

（按现有实际变量名对齐；若现有用 if/elif 串，则补一个 `elif tab == "long_loss": active_tab = "long_loss"` 分支。）

- [ ] **Step 3: webapp-testing 验证**

访问 `/ad-alerts/?tab=long_loss`，确认直接落在「长期亏损品」Tab。

- [ ] **Step 4: Commit**

```bash
git add web/routes/ad_alerts.py
git commit -m "feat(ad-alert): long_loss tab url param"
```

---

## Task 10: 飞书每日推送长期亏损品榜

**Files:**
- Modify: `appcore/ad_alert_daily_report.py`（`tick_once` 增加长期亏损品榜文本）
- Test: `tests/test_ad_alert_daily_report.py`（若无则新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ad_alert_daily_report.py （追加）
from datetime import date
from appcore import ad_alert_daily_report as report
from appcore import ad_long_term_loss as ltl


def test_build_long_loss_report_text():
    items = [ltl.LongTermLossItem(
        product_id=1, product_code="P1", product_name="品1", product_main_image=None,
        spend_7d=800.0, profit_7d=-200.0, loss_7d=200.0, profit_30d=-50.0, loss_ratio=None,
        verdict="long_term_net_loss", active_days=28, consecutive_loss_days=3,
        first_active_date="2026-05-01", has_estimated_cost=True, detail_url="",
    )]
    text = report.build_long_loss_report_text(date(2026, 6, 14), items)
    assert "长期亏损品" in text
    assert "品1" in text
    assert "200" in text  # 近7天亏损
    assert "连续亏损 3 天" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ad_alert_daily_report.py -k long_loss -v`
Expected: FAIL（`AttributeError: build_long_loss_report_text`）

- [ ] **Step 3: 实现**

在 `appcore/ad_alert_daily_report.py` 追加（并在 `tick_once` 成功分支后追加一段：取 `ad_long_term_loss.get_long_term_loss_products(limit=REPORT_LIMIT)`，非空则 `feishu_alerts.send_text_message(build_long_loss_report_text(...))`）：

```python
def build_long_loss_report_text(business_date, items) -> str:
    lines = [f"【长期亏损品】{business_date.strftime('%m-%d')} Top {len(items)}"]
    for i, it in enumerate(items, start=1):
        label = "长期净亏" if it.verdict == "long_term_net_loss" else "亏损侵蚀利润"
        parts = [
            f"{i}. {it.product_name or it.product_code}（{label}）",
            f"近7天消耗 ${it.spend_7d:.0f}",
            f"近7天亏 ${it.loss_7d:.0f}",
        ]
        if it.consecutive_loss_days > 0:
            parts.append(f"连续亏损 {it.consecutive_loss_days} 天")
        if it.has_estimated_cost:
            parts.append("含估算")
        lines.append(" ｜ ".join(parts))
    lines.append("查看明细：/ad-alerts/?tab=long_loss")
    return "\n".join(lines)
```

在 `tick_once` 顶部 import 区加 `from appcore import ad_long_term_loss`。在现有高额亏损推送发送后追加：

```python
        try:
            _bd, ll_items = ad_long_term_loss.get_long_term_loss_products(limit=REPORT_LIMIT)
            if ll_items:
                feishu_alerts.send_text_message(
                    build_long_loss_report_text(_bd, ll_items), config=feishu_config
                )
        except Exception:
            log.warning("long term loss feishu push failed", exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_ad_alert_daily_report.py -k long_loss -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_alert_daily_report.py tests/test_ad_alert_daily_report.py
git commit -m "feat(ad-alert): daily feishu push for long-term-loss products"
```

---

## Task 11: 收尾验证

- [ ] **Step 1: 跑改动相关测试**

Run:
```bash
python3 scripts/pytest_related.py --base origin/master --run
```
若脚本不可用：
```bash
pytest tests/test_ad_long_term_loss.py tests/test_ad_alert_actions.py \
       tests/test_ad_alerts_routes.py tests/test_ad_alert_daily_report.py -q
```
Expected: 全 PASS

- [ ] **Step 2: 文档自检**

- 在 `AGENTS.md`「主题指引」追加本 spec 引用。
- `appcore/ad_alerts.py` 文件头 docs anchors 列表追加本 spec（保持模块文档可追溯）。

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md appcore/ad_alerts.py
git commit -m "docs(ad-alert): link long-term-loss spec"
```

---

## Self-Review

**Spec coverage：**
- 维度=产品级 → Task 4/5 ✓
- 真实成本逐项扣 + 8%/17% 估算兜底 → Task 4（按 `missing_fields` 套率）✓
- 判定规则（近7天亏 ÷ 近30天盈 >10% / 长期净亏） → Task 3 ✓
- 排除新品（active_days<10） → Task 5 ✓
- 噪音门槛（消耗≥$50、亏损≥$20） → Task 5 ✓
- 排序（近7天消耗降序） → Task 5 ✓
- 配置项可调 → Task 2 ✓
- 含估算标注 + 提示 → Task 4(has_estimated)/Task 7(count)/Task 8(hint) ✓
- 连续亏损天数（利润口径） → Task 6 ✓
- action workflow → Task 1/5 ✓
- API → Task 7；前端 Tab → Task 8/9；飞书推送 → Task 10 ✓
- 现有三 Tab 不动 → 全程仅新增，无修改既有逻辑 ✓

**Placeholder scan：** 无 TBD/TODO；每个代码步给出完整代码。Task 5 引用的 `_attach_consecutive_loss_days` 在 Task 6 实现——执行顺序上 Task 5 提交后该函数尚未定义会导致入口运行期报错，但**单测（Task 5）已 monkeypatch 掉它**，故测试通过；Task 6 补齐真实实现。subagent-driven 执行时按编号顺序即可。

**Type consistency：** `WindowMetric` / `LtlVerdict` / `LongTermLossItem` 字段在 Task 3-8 间一致；`judge_long_term_loss` 关键字参数 `profit_7d/profit_30d/loss_ratio` 在 Task 3 定义、Task 5 调用一致；`get_long_term_loss_products` 返回 `(date, list[LongTermLossItem])` 在 Task 5/7/10 一致。

**已知近似（非缺陷，spec 已许可）：** `active_days` 与 `consecutive_loss_days` 用 daily 表、当天 realtime 未计入；窗口判定用 `_load_ad_spend`（含 realtime）准确。
