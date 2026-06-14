# Plan ③ 数据质量披露 + 断更监控 + SKU 督促 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让利润 KPI 真实区分「真实手续费 / 区域估算 / 待对账」，暴露 payments 与日汇率断更告警，并把缺采购价/物流的产品集中暴露督促补全。

**Architecture:** 复用既有 `data_quality.py` 的 check/build 框架（check = `{code,status,message}`，`build_data_quality` 取最差状态）、`estimate_marks`、`get_order_profit_incomplete_products`。后端新增两个断更 check + 手续费来源拆分（完整 TDD）；前端 `order_profit_dashboard.html` 按现有 `renderEstimateMarks` 模式接线展示（项目前端无单测，靠 webapp-testing 核对）。

**Tech Stack:** Python 3.12、pytest + monkeypatch、Jinja + 原生 JS。

**Spec:** `docs/superpowers/specs/2026-06-14-cost-accounting-real-data-first-design.md` §9 / §6.4。
**依赖:** Plan ②（手续费 source 落库）已完成——拆分依赖 `order_profit_lines.shopify_fee_source` 有真实值。

**已知约束:** `shopify_payments_transactions.transaction_date` 当前 100% NULL，故 payments 断更用 `imported_at`（有值）判断，不用交易日。

---

## File Structure

- **Modify** `appcore/order_analytics/data_quality.py`：新增 `check_payments_freshness` / `check_exchange_rate_freshness`，接入 `build_for_order_profit` 与 `run_recent_inspection`。
- **Modify** `appcore/order_analytics/order_profit_aggregation.py`：新增 `_query_shopify_fee_source_breakdown`，`estimate_marks["shopify_fee"]` 改为按来源拆分。
- **Modify** `tests/test_order_analytics_data_quality.py`：两个断更 check 单测。
- **Modify** `tests/test_order_profit_aggregation.py`：手续费来源拆分单测。
- **Modify** `web/templates/order_profit_dashboard.html`：展示手续费三类来源 + 断更告警 + 缺失产品督促（接线）。

---

## Task 1：payments / 汇率 断更 check

**Files:**
- Modify: `appcore/order_analytics/data_quality.py`
- Test: `tests/test_order_analytics_data_quality.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_order_analytics_data_quality.py`）

```python
def test_check_payments_freshness_stale_when_import_lag_exceeds_threshold(monkeypatch):
    from datetime import date, datetime
    from appcore.order_analytics import data_quality as dq

    monkeypatch.setattr(dq, "query_one", lambda sql, args=(): {
        "latest_import": datetime(2026, 6, 1, 10, 0, 0)
    })
    check = dq.check_payments_freshness(today=date(2026, 6, 14), stale_days=9)
    assert check["code"] == "payments_freshness"
    assert check["status"] == dq.STATUS_STALE
    assert check["lag_days"] == 13


def test_check_payments_freshness_ok_within_threshold(monkeypatch):
    from datetime import date, datetime
    from appcore.order_analytics import data_quality as dq

    monkeypatch.setattr(dq, "query_one", lambda sql, args=(): {
        "latest_import": datetime(2026, 6, 10, 10, 0, 0)
    })
    check = dq.check_payments_freshness(today=date(2026, 6, 14), stale_days=9)
    assert check["status"] == dq.STATUS_OK


def test_check_exchange_rate_freshness_stale(monkeypatch):
    from datetime import date
    from appcore.order_analytics import data_quality as dq

    monkeypatch.setattr(dq, "query_one", lambda sql, args=(): {"latest": date(2026, 6, 9)})
    check = dq.check_exchange_rate_freshness(today=date(2026, 6, 14), stale_days=2)
    assert check["code"] == "exchange_rate_freshness"
    assert check["status"] == dq.STATUS_STALE
    assert check["lag_days"] == 5
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_order_analytics_data_quality.py -k "payments_freshness or exchange_rate_freshness" -v`
Expected: FAIL，`module ... has no attribute 'check_payments_freshness'`

- [ ] **Step 3: 实现**（在 `data_quality.py` 的 `check_derived_profit_freshness` 之后新增）

```python
PAYMENTS_STALE_DAYS = 9          # 每周手动导一次，超过约 9 天未导入即告警
EXCHANGE_RATE_STALE_DAYS = 2     # 日汇率应每日同步


def check_payments_freshness(*, today: date | None = None, stale_days: int = PAYMENTS_STALE_DAYS) -> dict:
    """Shopify Payments 断更监控：用 imported_at（transaction_date 当前全 NULL）。"""
    today = today or current_meta_business_date()
    try:
        row = query_one(
            "SELECT MAX(imported_at) AS latest_import FROM shopify_payments_transactions"
        ) or {}
    except Exception as exc:  # noqa: BLE001
        return {"code": "payments_freshness", "status": STATUS_WARNING,
                "message": f"payments 水位查询失败：{exc}"}
    latest = _ensure_naive(row.get("latest_import"))
    if latest is None:
        return {"code": "payments_freshness", "status": STATUS_WARNING,
                "message": "无 Shopify Payments 数据，手续费全部走估算"}
    lag_days = (today - latest.date()).days
    if lag_days > stale_days:
        return {"code": "payments_freshness", "status": STATUS_STALE, "lag_days": lag_days,
                "latest_import_at": _isoformat(latest),
                "message": f"Shopify Payments 已 {lag_days} 天未导入，请上传最新 Payments/Transactions CSV"}
    return {"code": "payments_freshness", "status": STATUS_OK, "lag_days": lag_days,
            "latest_import_at": _isoformat(latest), "message": "payments 数据新鲜"}


def check_exchange_rate_freshness(*, today: date | None = None, stale_days: int = EXCHANGE_RATE_STALE_DAYS) -> dict:
    """日汇率断更监控：usd_cny_daily_exchange_rates 最新 rate_date 距今天数。"""
    today = today or current_meta_business_date()
    try:
        row = query_one("SELECT MAX(rate_date) AS latest FROM usd_cny_daily_exchange_rates") or {}
    except Exception as exc:  # noqa: BLE001
        return {"code": "exchange_rate_freshness", "status": STATUS_WARNING,
                "message": f"汇率水位查询失败：{exc}"}
    latest = _date_from_value(row.get("latest"))
    if latest is None:
        return {"code": "exchange_rate_freshness", "status": STATUS_WARNING,
                "message": "无日汇率数据，采购/物流换算走配置 fallback"}
    lag_days = (today - latest).days
    if lag_days > stale_days:
        return {"code": "exchange_rate_freshness", "status": STATUS_STALE, "lag_days": lag_days,
                "latest_rate_date": latest.isoformat(),
                "message": f"日汇率最新 {latest.isoformat()}，距今 {lag_days} 天未同步"}
    return {"code": "exchange_rate_freshness", "status": STATUS_OK, "lag_days": lag_days,
            "latest_rate_date": latest.isoformat(), "message": "日汇率新鲜"}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_order_analytics_data_quality.py -k "payments_freshness or exchange_rate_freshness" -v`
Expected: PASS（3 项）

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/data_quality.py tests/test_order_analytics_data_quality.py
git commit -m "feat(data-quality): payments/汇率 断更 check"
```

---

## Task 2：把断更 check 接入 order-profit 与巡检

**Files:**
- Modify: `appcore/order_analytics/data_quality.py`（`build_for_order_profit`、`run_recent_inspection`）
- Test: `tests/test_order_analytics_data_quality.py`

- [ ] **Step 1: 写失败测试**

```python
def test_build_for_order_profit_includes_freshness_checks(monkeypatch):
    from datetime import date
    from appcore.order_analytics import data_quality as dq

    monkeypatch.setattr(dq, "reconcile_ad_spend", lambda **k: {"code": "ad_spend_reconciled", "status": dq.STATUS_OK})
    monkeypatch.setattr(dq, "check_meta_ad_day_uniqueness", lambda **k: {"code": "meta_ad_day_uniqueness", "status": dq.STATUS_OK})
    monkeypatch.setattr(dq, "check_derived_profit_freshness", lambda **k: {"code": "derived_profit_freshness", "status": dq.STATUS_OK})
    monkeypatch.setattr(dq, "resolve_source_mode", lambda **k: dq.SOURCE_MODE_DAILY_FINAL)
    monkeypatch.setattr(dq, "fetch_watermarks", lambda: {})
    monkeypatch.setattr(dq, "check_payments_freshness", lambda **k: {"code": "payments_freshness", "status": dq.STATUS_STALE, "message": "stale"})
    monkeypatch.setattr(dq, "check_exchange_rate_freshness", lambda **k: {"code": "exchange_rate_freshness", "status": dq.STATUS_OK})

    result = dq.build_for_order_profit(date_from=date(2026, 6, 1), date_to=date(2026, 6, 13), allocated_ad_spend_usd=0.0)
    codes = {c["code"] for c in result["checks"]}
    assert "payments_freshness" in codes
    assert "exchange_rate_freshness" in codes
    assert result["status"] == dq.STATUS_STALE  # 断更降级冒泡到顶层
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_order_analytics_data_quality.py::test_build_for_order_profit_includes_freshness_checks -v`
Expected: FAIL（codes 不含 payments_freshness）

- [ ] **Step 3: 实现**

在 `build_for_order_profit` 的 `check_derived_profit_freshness(...)` append 之后，增加两行：

```python
    checks.append(check_payments_freshness())
    checks.append(check_exchange_rate_freshness())
```

在 `run_recent_inspection` 的 `days` 循环外（函数 `return` 之前），把断更 check 并入整体状态：

```python
    payments_check = check_payments_freshness(today=today)
    exchange_check = check_exchange_rate_freshness(today=today)
    for extra in (payments_check, exchange_check):
        if _STATUS_RANK.get(extra.get("status"), 0) > _STATUS_RANK.get(overall_status, 0):
            overall_status = extra["status"]
```

并把返回 dict 增加 `"freshness": [payments_check, exchange_check]`：

```python
    return {
        "generated_at": _now_iso(),
        "lookback_days": lookback_days,
        "status": overall_status,
        "days": days,
        "freshness": [payments_check, exchange_check],
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_order_analytics_data_quality.py -k "freshness or inspection" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/data_quality.py tests/test_order_analytics_data_quality.py
git commit -m "feat(data-quality): order-profit 与巡检接入 payments/汇率断更"
```

---

## Task 3：`estimate_marks` 手续费按真实来源拆分

**Files:**
- Modify: `appcore/order_analytics/order_profit_aggregation.py`
- Test: `tests/test_order_profit_aggregation.py`

- [ ] **Step 1: 写失败测试**

```python
def test_shopify_fee_source_breakdown_splits_actual_and_estimate(monkeypatch):
    from datetime import date
    from appcore.order_analytics import order_profit_aggregation as agg

    def fake_query(sql, args=()):
        assert "shopify_fee_source" in sql
        return [
            {"src": "actual_payment", "n": 60, "fee": 1200.0},
            {"src": "dynamic_region_rate", "n": 20, "fee": 500.0},
            {"src": "strategy_c_fallback", "n": 15, "fee": 300.0},
            {"src": "legacy", "n": 5, "fee": 80.0},
        ]

    monkeypatch.setattr(agg, "query", fake_query)
    bd = agg._query_shopify_fee_source_breakdown(date(2026, 6, 1), date(2026, 6, 13))

    assert bd["actual_payment"] == {"lines": 60, "amount_usd": 1200.0}
    assert bd["estimated"]["lines"] == 40  # dynamic + strategy_c + legacy
    assert bd["estimated"]["amount_usd"] == 880.0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_order_profit_aggregation.py::test_shopify_fee_source_breakdown_splits_actual_and_estimate -v`
Expected: FAIL，`has no attribute '_query_shopify_fee_source_breakdown'`

- [ ] **Step 3: 实现**

在 `order_profit_aggregation.py` 新增（放在 `get_order_profit_status_summary` 之前）：

```python
def _query_shopify_fee_source_breakdown(date_from: date, date_to: date) -> dict[str, Any]:
    """按 shopify_fee_source 拆分真实(actual_payment) vs 估算(其余)。"""
    rows = query(
        "SELECT COALESCE(p.shopify_fee_source, 'legacy') AS src, "
        "       COUNT(*) AS n, COALESCE(SUM(p.shopify_fee_usd), 0) AS fee "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        "WHERE d.meta_business_date BETWEEN %s AND %s "
        "GROUP BY src",
        (date_from, date_to),
    ) or []
    actual = {"lines": 0, "amount_usd": 0.0}
    est_lines = 0
    est_amount = 0.0
    by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        src = str(row.get("src") or "legacy")
        lines = int(row.get("n") or 0)
        amount = round(float(row.get("fee") or 0), 2)
        by_source[src] = {"lines": lines, "amount_usd": amount}
        if src == "actual_payment":
            actual = {"lines": lines, "amount_usd": amount}
        else:
            est_lines += lines
            est_amount += amount
    return {
        "actual_payment": actual,
        "estimated": {"lines": est_lines, "amount_usd": round(est_amount, 2)},
        "by_source": by_source,
    }
```

把 `get_order_profit_status_summary` 里 `estimate_marks` 的 `shopify_fee` 项（约第 1225-1230 行）替换为：

```python
    _fee_breakdown = _query_shopify_fee_source_breakdown(date_from, date_to)
    estimate_marks = {
        "shopify_fee": {
            "estimated": _fee_breakdown["estimated"]["lines"] > 0,
            "amount_usd": _round_money(_sum_summary(summary, "shopify_fee")),
            "lines": line_count,
            "label": "手续费（真实优先）",
            "actual_payment": _fee_breakdown["actual_payment"],
            "estimated_breakdown": _fee_breakdown["estimated"],
            "by_source": _fee_breakdown["by_source"],
        },
```
（其余 `purchase_fallback` / `return_reserve` 等键保持不动。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_order_profit_aggregation.py -k "shopify_fee_source_breakdown or status_summary" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/order_profit_aggregation.py tests/test_order_profit_aggregation.py
git commit -m "feat(profit): estimate_marks 手续费按真实/估算来源拆分"
```

---

## Task 4：前端展示接线（`order_profit_dashboard.html`）

**说明**：项目前端无单测基础设施，本任务按现有 `renderEstimateMarks`（约 850-880 行的 `marks` 数组渲染）与 `renderDataQualityBar` 模式接线；验证靠 Task 5 的 webapp-testing。

**Files:**
- Modify: `web/templates/order_profit_dashboard.html`

- [ ] **Step 1: 手续费来源三类展示**

在估算标记渲染处（现有把 `marks.shopify_fee` 渲染为单行「策略 C 估算」的地方），改为读取新结构 `marks.shopify_fee.actual_payment` 与 `marks.shopify_fee.estimated_breakdown`，渲染两行：
- 「真实手续费」`actual_payment.lines` 单 / `actual_payment.amount_usd`（chip：真实，绿色 `is-ok`）
- 「估算/待对账手续费」`estimated_breakdown.lines` 单 / `estimated_breakdown.amount_usd`（chip：待对账）

- [ ] **Step 2: 断更告警展示**

`renderDataQualityBar(data.data_quality)` 已渲染 `checks`/`warnings`；确认新 check `payments_freshness` / `exchange_rate_freshness` 的 `message` 会出现在数据质量条（无需额外代码，`_split_warnings_errors` 已把 stale 归入 warnings）。在页头说明区补一句：手续费/汇率断更会在数据质量条提示。

- [ ] **Step 3: 缺失产品督促入口**

确认「不完备产品」列表（`opIncompleteProductsList`，数据来自 `get_order_profit_incomplete_products`，每项含 `medias_search_url`）展示缺采购价/物流的产品并可跳转维护。若当前未展示采购/物流缺失原因，补 `missing_fields` 中文标签（采购价/物流成本）。

- [ ] **Step 4: Commit**

```bash
git add web/templates/order_profit_dashboard.html
git commit -m "feat(ui): 手续费真实/估算分列 + 断更提示 + 缺失产品督促"
```

---

## Task 5：验证（webapp-testing + 线上）

- [ ] **Step 1: 后端 focused 测试**

Run: `python3 scripts/pytest_related.py --base origin/master --run`
（或至少 `pytest tests/test_order_analytics_data_quality.py tests/test_order_profit_aggregation.py -q`）
Expected: PASS

- [ ] **Step 2: webapp-testing 核对前端**

用 webapp-testing 打开 `/order-profit`（或 `order_profit_dashboard`），核对：手续费分「真实/估算」两行、payments 断更时数据质量条显示提醒、不完备产品可跳转 medias。

- [ ] **Step 3: 线上数据质量条**

```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python -c "from appcore.order_analytics import data_quality as dq; print(dq.check_payments_freshness()); print(dq.check_exchange_rate_freshness())"'
```
Expected: 当前 payments（最后导入 6/6）应为 `stale`，提示导入；回填后汇率应 `ok`。

---

## Self-Review

- **Spec 覆盖**：§9「estimate_marks 按 source 区分」=Task 3；「前端区分真实/估算/待对账 + data_quality 断更」=Task 1+2+4；§6.4「缺失产品暴露督促」=Task 4 Step 3。
- **占位符**：后端 Task 1-3 含完整代码与命令；Task 4 为前端接线（项目无前端单测，明确按现有模式 + webapp-testing 验证，非占位）。
- **类型一致**：`check_payments_freshness(today=,stale_days=)` / `check_exchange_rate_freshness(...)` 在实现、接入、测试中签名一致；`_query_shopify_fee_source_breakdown(date_from,date_to)->{"actual_payment","estimated","by_source"}` 与测试断言、estimate_marks 用法一致。
- **约束**：payments 断更用 `imported_at`（`transaction_date` 全 NULL）——已在 Architecture 注明；修 `transaction_date` 不在本方案范围。
