# 退款核验（后端核心）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Shopify Payments 的真实退款（经人工核验）以 `max(实测, 1%)` 口径接进实时大盘与产品盈亏核算，走独立 override 表，不污染原始数据。

**Architecture:** 新增两张 override 表（批次 + 明细）与一个 `refund_verification.py` 模块负责解析/关联/批次状态机/覆盖计算；核算层新增 `_apply_refund_verification_adjustments`，与现有 `_apply_realtime_ad_cost_adjustments` 同构，在 `realtime.py` 与 `order_profit_aggregation.py` 出口按包裹回填 `return_reserve` 的 delta、同步下调 profit；6 个 API 端点驱动导入→核对→应用→回滚。

**Tech Stack:** Python 3.12 / Flask / MySQL(InnoDB) / pytest；模块走 `appcore.order_analytics` 的 `_facade()` query/execute 模式；migration 启动时由 `appcore/db_migrations.py` 自动应用。

**Spec:** `docs/superpowers/specs/2026-06-13-refund-verification-design.md`

---

## File Structure

- Create `db/migrations/2026_06_13_refund_verification_tables.sql` — 两张 override 表
- Create `appcore/order_analytics/refund_verification.py` — 解析、关联、批次 CRUD、覆盖计算（单一职责）
- Create `tests/test_refund_verification.py` — 模块单测
- Create `tests/test_refund_verification_routes.py` — 端点测试
- Modify `appcore/order_analytics/__init__.py` — re-export 新模块公开符号
- Modify `appcore/order_analytics/realtime.py` — 出口调用覆盖
- Modify `appcore/order_analytics/order_profit_aggregation.py` — 出口调用覆盖
- Modify `web/routes/order_analytics.py` — 6 个端点 + cache 失效

---

## Task 1: Migration — 两张 override 表

**Files:**
- Create: `db/migrations/2026_06_13_refund_verification_tables.sql`

- [ ] **Step 1: 写 migration SQL**

```sql
CREATE TABLE IF NOT EXISTS refund_verification_batches (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',   -- pending | applied | discarded
  source_files JSON,
  site_code VARCHAR(16) DEFAULT NULL,
  matched_count INT NOT NULL DEFAULT 0,
  unmatched_count INT NOT NULL DEFAULT 0,
  anomaly_count INT NOT NULL DEFAULT 0,
  total_refund_usd DECIMAL(12,4) NOT NULL DEFAULT 0,
  current_reserve_usd DECIMAL(12,4) NOT NULL DEFAULT 0,
  delta_usd DECIMAL(12,4) NOT NULL DEFAULT 0,
  created_by VARCHAR(64) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied_at DATETIME DEFAULT NULL,
  KEY idx_rvb_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款核验批次';

CREATE TABLE IF NOT EXISTS refund_verifications (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  extended_order_id VARCHAR(128) NOT NULL,
  site_code VARCHAR(16) DEFAULT NULL,
  refund_amount_usd DECIMAL(12,4) DEFAULT NULL,    -- NULL = 缺金额(回退1%)
  refund_source VARCHAR(16) DEFAULT NULL,          -- payments | order_status | both
  order_financial_status VARCHAR(32) DEFAULT NULL,
  matched_package_ids JSON,
  match_status VARCHAR(16) NOT NULL DEFAULT 'matched',  -- matched | unmatched | anomaly
  note VARCHAR(255) DEFAULT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_rv_batch (batch_id),
  KEY idx_rv_order_status (extended_order_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款核验明细(订单级)';
```

- [ ] **Step 2: 起 dev server 验证 migration 自动应用，无报错**

Run: `python -c "import appcore.db_migrations as m; m.apply_pending_migrations()"`（若该函数名不同，以 `appcore/db_migrations.py` 实际入口为准）
Expected: 日志含 `applying` 且无异常；再次运行不重复应用。

- [ ] **Step 3: Commit**

```bash
git add db/migrations/2026_06_13_refund_verification_tables.sql
git commit -m "feat(refund-verification): add override tables migration"
```

---

## Task 2: 模块骨架 + Payments 退款聚合 + 订单状态提取

**Files:**
- Create: `appcore/order_analytics/refund_verification.py`
- Modify: `appcore/order_analytics/__init__.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试 — Payments 退款聚合 + 订单状态提取**

```python
import io
from appcore import order_analytics as oa
from appcore.order_analytics import refund_verification as rv


def test_aggregate_payments_refunds_sums_abs_by_order():
    payments = [
        {"type": "refund", "order_name": "#23863", "amount_usd": -56.89},
        {"type": "refund", "order_name": "#23863", "amount_usd": -10.0},
        {"type": "chargeback", "order_name": "#100", "amount_usd": -20.0},
        {"type": "charge", "order_name": "#200", "amount_usd": 30.0},
    ]
    out = rv.aggregate_payment_refunds(payments)
    assert out["23863"] == 66.89   # 多笔求和、负数取绝对值、订单号去 #
    assert out["100"] == 20.0       # chargeback 计入
    assert "200" not in out          # charge 不算退款


def test_extract_order_refund_statuses():
    orders = [
        {"order_name": "#300", "financial_status": "refunded"},
        {"order_name": "#301", "financial_status": "partially_refunded"},
        {"order_name": "#302", "financial_status": "paid"},
    ]
    out = rv.extract_order_refund_statuses(orders)
    assert out == {"300": "refunded", "301": "partially_refunded"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification.py -v`
Expected: FAIL（`module refund_verification has no attribute ...`）

- [ ] **Step 3: 写模块骨架 + 两个函数**

```python
"""退款核验：解析 Shopify Payments/订单退款、关联店小秘订单、批次状态机与核算覆盖。

设计：docs/superpowers/specs/2026-06-13-refund-verification-design.md
DB 入口走 module-level facade（与 shopify_payments_import.py 同款），
让测试 monkeypatch.setattr(oa, "query", ...) 透传到本模块。
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


_REFUND_TYPES = ("refund", "chargeback")
_REFUND_FIN_STATUSES = ("refunded", "partially_refunded")


def _normalize_order_name(name: Any) -> str:
    return str(name or "").strip().lstrip("#").strip()


def aggregate_payment_refunds(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Payments 行按订单号聚合真实退款额（refund/chargeback 取绝对值求和）。"""
    out: dict[str, float] = {}
    for r in rows:
        if (r.get("type") or "").lower() not in _REFUND_TYPES:
            continue
        order = _normalize_order_name(r.get("order_name"))
        amount = r.get("amount_usd")
        if not order or amount in (None, ""):
            continue
        out[order] = round(out.get(order, 0.0) + abs(float(amount)), 4)
    return out


def extract_order_refund_statuses(rows: list[dict[str, Any]]) -> dict[str, str]:
    """订单 CSV 行取退款状态（refunded/partially_refunded）。"""
    out: dict[str, str] = {}
    for r in rows:
        status = (r.get("financial_status") or "").strip().lower()
        if status not in _REFUND_FIN_STATUSES:
            continue
        order = _normalize_order_name(r.get("order_name"))
        if order:
            out[order] = status
    return out
```

- [ ] **Step 4: re-export，让 `oa.refund_verification` / facade 可用**

在 `appcore/order_analytics/__init__.py` 末尾加：

```python
from . import refund_verification  # noqa: E402,F401
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_refund_verification.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add appcore/order_analytics/refund_verification.py appcore/order_analytics/__init__.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): parse payments refunds and order statuses"
```

---

## Task 3: 订单关联 + 分类（matched / unmatched / anomaly）

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试**

```python
def test_build_verification_rows_classifies(monkeypatch):
    # 店小秘库里有 #23863(单包裹) 与 #301(单包裹)，无 #999
    def fake_query(sql, args=()):
        return [
            {"extended_order_id": "23863", "dxm_package_id": "PKG-A",
             "site_code": "newjoy", "revenue": 50.0},
            {"extended_order_id": "301", "dxm_package_id": "PKG-B",
             "site_code": "newjoy", "revenue": 40.0},
        ]
    monkeypatch.setattr(oa, "query", fake_query)

    refunds = {"23863": 66.89, "999": 12.0}      # 999 不在库 -> unmatched
    statuses = {"301": "refunded"}                # 301 仅状态、无金额 -> anomaly
    rows = rv.build_verification_rows(refunds, statuses)
    by_order = {r["extended_order_id"]: r for r in rows}

    assert by_order["23863"]["match_status"] == "anomaly"   # 退款 66.89 > 营收 50 -> anomaly
    assert by_order["23863"]["refund_amount_usd"] == 66.89
    assert by_order["23863"]["matched_package_ids"] == ["PKG-A"]
    assert by_order["301"]["match_status"] == "anomaly"      # 有状态无金额
    assert by_order["301"]["refund_amount_usd"] is None
    assert by_order["999"]["match_status"] == "unmatched"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification.py::test_build_verification_rows_classifies -v`
Expected: FAIL（`no attribute 'build_verification_rows'`）

- [ ] **Step 3: 实现关联 + 分类**

```python
def _load_order_packages(order_ids: list[str]) -> dict[str, dict[str, Any]]:
    """按 extended_order_id 取店小秘订单的包裹集合、站点、营收。"""
    if not order_ids:
        return {}
    placeholders = ",".join(["%s"] * len(order_ids))
    rows = query(
        "SELECT d.extended_order_id, d.dxm_package_id, d.site_code, "
        "       (COALESCE(SUM(d.line_amount),0) + "
        "        COALESCE(MAX(d.ship_amount),0)) AS revenue "
        "FROM dianxiaomi_order_lines d "
        f"WHERE d.extended_order_id IN ({placeholders}) "
        "GROUP BY d.extended_order_id, d.dxm_package_id, d.site_code",
        tuple(order_ids),
    ) or []
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        oid = str(r.get("extended_order_id") or "")
        if not oid:
            continue
        entry = out.setdefault(oid, {"packages": [], "site_code": r.get("site_code"), "revenue": 0.0})
        entry["packages"].append(str(r.get("dxm_package_id")))
        entry["revenue"] += float(r.get("revenue") or 0.0)
    return out


def build_verification_rows(
    refunds: dict[str, float],
    statuses: dict[str, str],
) -> list[dict[str, Any]]:
    """把退款金额 + 状态合成核验明细行并分类。"""
    order_ids = sorted(set(refunds) | set(statuses))
    pkg_map = _load_order_packages(order_ids)
    rows: list[dict[str, Any]] = []
    for oid in order_ids:
        amount = refunds.get(oid)
        fin_status = statuses.get(oid)
        source = ("both" if amount is not None and fin_status else
                  "payments" if amount is not None else "order_status")
        entry = pkg_map.get(oid)
        if not entry:
            match_status, note, packages, site = "unmatched", "订单号不在店小秘库", [], None
        else:
            packages = entry["packages"]
            site = entry["site_code"]
            revenue = entry["revenue"]
            if amount is None:
                match_status, note = "anomaly", "有退款状态但缺 Payments 金额，回退 1%"
            elif amount > revenue:
                match_status, note = "anomaly", f"退款 {amount} 大于订单营收 {round(revenue,2)}"
            else:
                match_status, note = "matched", None
        rows.append({
            "extended_order_id": oid,
            "site_code": site,
            "refund_amount_usd": amount,
            "refund_source": source,
            "order_financial_status": fin_status,
            "matched_package_ids": packages,
            "match_status": match_status,
            "note": note,
        })
    return rows
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_refund_verification.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): associate orders and classify rows"
```

---

## Task 4: 批次创建（import）+ 持久化 + 核对摘要

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试 — create_batch 写库并返回摘要**

```python
def test_create_batch_persists_and_summarizes(monkeypatch):
    executed = []
    def fake_execute(sql, args=()):
        executed.append((sql, args))
        return 1
    def fake_query(sql, args=()):
        if "extended_order_id IN" in sql:
            return [{"extended_order_id": "23863", "dxm_package_id": "PKG-A",
                     "site_code": "newjoy", "revenue": 100.0}]
        if "SUM(p.return_reserve_usd)" in sql:   # 当前 1% 计提
            return [{"extended_order_id": "23863", "reserve": 1.0}]
        if "LAST_INSERT_ID" in sql:
            return [{"id": 7}]
        return []
    monkeypatch.setattr(oa, "execute", fake_execute)
    monkeypatch.setattr(oa, "query", fake_query)

    summary = rv.create_batch(
        refunds={"23863": 56.89}, statuses={}, source_files={"payments_csv": "p.csv"},
        created_by="admin",
    )
    assert summary["matched_count"] == 1
    assert summary["total_refund_usd"] == 56.89
    assert summary["current_reserve_usd"] == 1.0
    assert summary["delta_usd"] == 55.89
    assert summary["batch_id"] == 7
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification.py::test_create_batch_persists_and_summarizes -v`
Expected: FAIL（`no attribute 'create_batch'`）

- [ ] **Step 3: 实现 create_batch + 当前计提查询**

```python
def _load_current_reserve(order_ids: list[str]) -> dict[str, float]:
    """这些订单当前 order_profit_lines 里的 1% 计提合计（订单级）。"""
    if not order_ids:
        return {}
    placeholders = ",".join(["%s"] * len(order_ids))
    rows = query(
        "SELECT d.extended_order_id, SUM(p.return_reserve_usd) AS reserve "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        f"WHERE d.extended_order_id IN ({placeholders}) "
        "GROUP BY d.extended_order_id",
        tuple(order_ids),
    ) or []
    return {str(r["extended_order_id"]): float(r.get("reserve") or 0.0) for r in rows}


def create_batch(
    *,
    refunds: dict[str, float],
    statuses: dict[str, str],
    source_files: dict[str, Any],
    created_by: str | None,
    site_code: str | None = None,
) -> dict[str, Any]:
    rows = build_verification_rows(refunds, statuses)
    order_ids = [r["extended_order_id"] for r in rows if r["match_status"] != "unmatched"]
    reserve_map = _load_current_reserve(order_ids)

    matched = [r for r in rows if r["match_status"] == "matched"]
    total_refund = round(sum(r["refund_amount_usd"] or 0.0 for r in matched), 4)
    current_reserve = round(sum(reserve_map.get(r["extended_order_id"], 0.0) for r in matched), 4)
    counts = {
        "matched_count": sum(1 for r in rows if r["match_status"] == "matched"),
        "unmatched_count": sum(1 for r in rows if r["match_status"] == "unmatched"),
        "anomaly_count": sum(1 for r in rows if r["match_status"] == "anomaly"),
    }
    execute(
        "INSERT INTO refund_verification_batches "
        "(status, source_files, site_code, matched_count, unmatched_count, anomaly_count, "
        " total_refund_usd, current_reserve_usd, delta_usd, created_by) "
        "VALUES ('pending', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (json.dumps(source_files, ensure_ascii=False), site_code,
         counts["matched_count"], counts["unmatched_count"], counts["anomaly_count"],
         total_refund, current_reserve, round(total_refund - current_reserve, 4), created_by),
    )
    batch_id = int((query("SELECT LAST_INSERT_ID() AS id") or [{}])[0].get("id"))
    for r in rows:
        execute(
            "INSERT INTO refund_verifications "
            "(batch_id, extended_order_id, site_code, refund_amount_usd, refund_source, "
            " order_financial_status, matched_package_ids, match_status, note, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')",
            (batch_id, r["extended_order_id"], r["site_code"], r["refund_amount_usd"],
             r["refund_source"], r["order_financial_status"],
             json.dumps(r["matched_package_ids"], ensure_ascii=False),
             r["match_status"], r["note"]),
        )
    return {"batch_id": batch_id, "total_refund_usd": total_refund,
            "current_reserve_usd": current_reserve,
            "delta_usd": round(total_refund - current_reserve, 4), **counts}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_refund_verification.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): create batch and reconciliation summary"
```

---

## Task 5: 批次状态机（apply / discard / revert）

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试**

```python
def test_apply_and_revert_batch_flip_status(monkeypatch):
    calls = []
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): calls.append((sql, args)))
    rv.apply_batch(7)
    rv.revert_batch(7)
    rv.discard_batch(8)
    assert any("status='applied'" in s and "batches" in s for s, _ in calls)
    assert any("status='applied'" in s and "refund_verifications" in s for s, _ in calls)
    assert any("status='discarded'" in s for s, _ in calls)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification.py::test_apply_and_revert_batch_flip_status -v`
Expected: FAIL

- [ ] **Step 3: 实现状态机**

```python
def apply_batch(batch_id: int) -> None:
    execute("UPDATE refund_verification_batches SET status='applied', "
            "applied_at=NOW() WHERE id=%s AND status='pending'", (batch_id,))
    execute("UPDATE refund_verifications SET status='applied' "
            "WHERE batch_id=%s", (batch_id,))


def revert_batch(batch_id: int) -> None:
    execute("UPDATE refund_verification_batches SET status='discarded' "
            "WHERE id=%s AND status='applied'", (batch_id,))
    execute("UPDATE refund_verifications SET status='discarded' "
            "WHERE batch_id=%s", (batch_id,))


def discard_batch(batch_id: int) -> None:
    execute("UPDATE refund_verification_batches SET status='discarded' "
            "WHERE id=%s AND status='pending'", (batch_id,))
    execute("UPDATE refund_verifications SET status='discarded' "
            "WHERE batch_id=%s", (batch_id,))
```

> 回退语义：`revert_batch` 把该批 verifications 标 `discarded` 后，
> `load_refund_verification_adjustments` 按 `status='applied'` 取每个订单 `MAX(id)`，
> 会自动回退到该订单更早的 applied 批次或 1%（spec 的"次新 applied 或 1%"语义）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_refund_verification.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): batch state machine apply/discard/revert"
```

---

## Task 6: 覆盖计算 + 接入 realtime.py

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Modify: `appcore/order_analytics/realtime.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试 — 覆盖按包裹分摊、取 max**

```python
def test_load_refund_adjustments_max_and_prorate(monkeypatch):
    def fake_query(sql, args=()):
        if "status='applied'" in sql and "refund_verifications" in sql:
            return [{"extended_order_id": "23863", "refund_amount_usd": 30.0}]
        if "order_profit_lines" in sql:   # 订单含两包裹，各 1% = 0.6 / 0.4，营收 60/40
            return [
                {"extended_order_id": "23863", "dxm_package_id": "P1", "reserve": 0.6, "revenue": 60.0},
                {"extended_order_id": "23863", "dxm_package_id": "P2", "reserve": 0.4, "revenue": 40.0},
            ]
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    deltas = rv.load_refund_verification_adjustments()["package_deltas"]
    # reserve_sum=1.0, R=min(30,100)=30, effective=max(30,1)=30, delta_total=29
    # 按营收比例: P1=29*0.6=17.4, P2=29*0.4=11.6
    assert round(deltas["P1"], 2) == 17.4
    assert round(deltas["P2"], 2) == 11.6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification.py::test_load_refund_adjustments_max_and_prorate -v`
Expected: FAIL

- [ ] **Step 3: 实现覆盖计算 + apply 辅助**

```python
def load_refund_verification_adjustments() -> dict[str, Any]:
    """取最新 applied 退款，按订单算 max(实测,1%)，把增量 delta 按包裹营收比例分摊。"""
    refund_rows = query(
        "SELECT v.extended_order_id, v.refund_amount_usd "
        "FROM refund_verifications v "
        "JOIN (SELECT extended_order_id, MAX(id) AS mid FROM refund_verifications "
        "      WHERE status='applied' AND refund_amount_usd IS NOT NULL "
        "      GROUP BY extended_order_id) latest "
        "  ON latest.mid = v.id",
    ) or []
    refunds = {str(r["extended_order_id"]): float(r["refund_amount_usd"]) for r in refund_rows}
    if not refunds:
        return {"package_deltas": {}}

    placeholders = ",".join(["%s"] * len(refunds))
    pkg_rows = query(
        "SELECT d.extended_order_id, d.dxm_package_id, "
        "       SUM(p.return_reserve_usd) AS reserve, SUM(p.revenue_usd) AS revenue "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        f"WHERE d.extended_order_id IN ({placeholders}) "
        "GROUP BY d.extended_order_id, d.dxm_package_id",
        tuple(refunds.keys()),
    ) or []

    by_order: dict[str, list[dict[str, Any]]] = {}
    for r in pkg_rows:
        by_order.setdefault(str(r["extended_order_id"]), []).append(r)

    package_deltas: dict[str, float] = {}
    for oid, pkgs in by_order.items():
        reserve_sum = sum(float(p.get("reserve") or 0.0) for p in pkgs)
        revenue_sum = sum(float(p.get("revenue") or 0.0) for p in pkgs)
        refund = min(refunds[oid], revenue_sum) if revenue_sum > 0 else refunds[oid]
        effective = max(refund, reserve_sum)
        delta_total = effective - reserve_sum
        if delta_total <= 0:
            continue
        for p in pkgs:
            pid = str(p.get("dxm_package_id"))
            if revenue_sum > 0:
                share = float(p.get("revenue") or 0.0) / revenue_sum
            else:
                share = 1.0 / len(pkgs)
            package_deltas[pid] = round(package_deltas.get(pid, 0.0) + delta_total * share, 4)
    return {"package_deltas": package_deltas}


def apply_refund_adjustments_to_details(details: list[dict[str, Any]]) -> None:
    """把退款 delta 加到包裹行的 return_reserve，并同步下调 profit（realtime 字段名）。"""
    if not details:
        return
    try:
        deltas = load_refund_verification_adjustments()["package_deltas"]
    except Exception:
        return
    if not deltas:
        return
    for row in details:
        delta = float(deltas.get(str(row.get("dxm_package_id") or "")) or 0.0)
        if not delta:
            continue
        row["return_reserve_usd"] = round(float(row.get("return_reserve_usd") or 0.0) + delta, 4)
        for k in ("order_profit_usd", "order_profit_with_estimate_usd"):
            if row.get(k) is not None:
                row[k] = round(float(row.get(k) or 0.0) - delta, 2)
```

- [ ] **Step 4: 接入 realtime.py 的订单盈亏明细出口**

在 `realtime.py` 中每处调用 `_apply_realtime_ad_cost_adjustments(details, ...)` 之后，紧跟一行（共 `realtime.py:636 / 1031 / 1262` 三处订单盈亏明细出口）：

```python
            oa.refund_verification.apply_refund_adjustments_to_details(details)
```

（`oa` 即 `appcore.order_analytics`；若该文件内已 `from . import ...`，用模块内可达的引用，例如 `from .refund_verification import apply_refund_adjustments_to_details` 后直接调用。）

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_refund_verification.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add appcore/order_analytics/refund_verification.py appcore/order_analytics/realtime.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): compute and apply override in realtime"
```

---

## Task 7: 接入 order_profit_aggregation.py（产品盈亏链路）

**Files:**
- Modify: `appcore/order_analytics/order_profit_aggregation.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试 — aggregation 链路字段名（return_reserve / profit_with_estimate）也被覆盖**

```python
def test_apply_adjustments_aggregation_fieldnames(monkeypatch):
    monkeypatch.setattr(rv, "load_refund_verification_adjustments",
                        lambda: {"package_deltas": {"P1": 5.0}})
    details = [{"dxm_package_id": "P1", "return_reserve": 0.6,
                "profit": 10.0, "profit_with_estimate": 8.0}]
    rv.apply_refund_adjustments_to_details_aggregation(details)
    assert round(details[0]["return_reserve"], 2) == 5.6
    assert round(details[0]["profit"], 2) == 5.0
    assert round(details[0]["profit_with_estimate"], 2) == 3.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification.py::test_apply_adjustments_aggregation_fieldnames -v`
Expected: FAIL

- [ ] **Step 3: 实现 aggregation 版（字段名不同）**

在 `refund_verification.py` 增：

```python
def apply_refund_adjustments_to_details_aggregation(details: list[dict[str, Any]]) -> None:
    """order_profit_aggregation 链路：字段名为 return_reserve / profit / profit_with_estimate。"""
    if not details:
        return
    try:
        deltas = load_refund_verification_adjustments()["package_deltas"]
    except Exception:
        return
    if not deltas:
        return
    for row in details:
        delta = float(deltas.get(str(row.get("dxm_package_id") or "")) or 0.0)
        if not delta:
            continue
        row["return_reserve"] = round(float(row.get("return_reserve") or 0.0) + delta, 4)
        for k in ("profit", "profit_with_estimate"):
            if row.get(k) is not None:
                row[k] = round(float(row.get(k) or 0.0) - delta, 2)
```

> 实现前先 `grep -n "dxm_package_id" appcore/order_analytics/order_profit_aggregation.py` 确认 `get_order_profit_list` 组装的明细行确实带 `dxm_package_id` 与上述利润字段名；若实际字段名不同，以该文件为准调整本函数的 key。

- [ ] **Step 4: 在 `order_profit_aggregation.py` 的 `get_order_profit_list` 出口（紧跟 `_apply_realtime_ad_cost_adjustments` 之后，约 `:506 / :644 / :910` 三处）调用**

```python
        from .refund_verification import apply_refund_adjustments_to_details_aggregation
        apply_refund_adjustments_to_details_aggregation(details)
```

- [ ] **Step 5: 跑测试确认通过 + 跑产品盈亏回归**

Run: `pytest tests/test_refund_verification.py tests/test_product_profit_report.py tests/test_order_profit_aggregation.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add appcore/order_analytics/refund_verification.py appcore/order_analytics/order_profit_aggregation.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): apply override in product profit aggregation"
```

---

## Task 8: API 端点（import / list / detail / apply / discard / revert）+ 缓存失效

**Files:**
- Modify: `web/routes/order_analytics.py`
- Test: `tests/test_refund_verification_routes.py`

- [ ] **Step 1: 写失败测试 — 端点权限 + import 流程**

```python
import io
import pytest
from web.app import create_app   # 若工厂名不同，以 web/app.py 实际入口为准


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_import_requires_login(client):
    resp = client.post("/order-analytics/refund-verify/import")
    assert resp.status_code in (302, 401, 403)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_refund_verification_routes.py -v`
Expected: FAIL（404，端点未定义）

- [ ] **Step 3: 实现 6 个端点**

在 `web/routes/order_analytics.py` 顶部 import 处加 `from appcore.order_analytics import refund_verification as rv` 与（已存在的）`parse_payments_csv`；新增：

```python
@bp.route("/order-analytics/refund-verify/import", methods=["POST"])
@login_required
@permission_required("data_analytics")
def refund_verify_import():
    from appcore.order_analytics.shopify_payments_import import parse_payments_csv
    pay_file = request.files.get("payments_csv")
    order_file = request.files.get("orders_csv")
    if not pay_file:
        return _json_response(error="invalid_param", detail="payments_csv is required"), 400
    refunds = rv.aggregate_payment_refunds(
        parse_payments_csv(io.StringIO(pay_file.read().decode("utf-8-sig")),
                           source_csv=pay_file.filename or "")
    )
    statuses = {}
    if order_file:
        import csv as _csv
        reader = _csv.DictReader(io.StringIO(order_file.read().decode("utf-8-sig")))
        order_rows = [{"order_name": r.get("Name") or r.get("Order"),
                       "financial_status": r.get("Financial Status")} for r in reader]
        statuses = rv.extract_order_refund_statuses(order_rows)
    summary = rv.create_batch(
        refunds=refunds, statuses=statuses,
        source_files={"payments_csv": pay_file.filename,
                      "orders_csv": getattr(order_file, "filename", None)},
        created_by=getattr(current_user, "username", None),
        site_code=(request.form.get("site_code") or "").strip().lower() or None,
    )
    return _json_response(_json_safe(summary))


@bp.route("/order-analytics/refund-verify/batches")
@login_required
@permission_required("data_analytics")
def refund_verify_batches():
    rows = oa.query(
        "SELECT id, status, source_files, matched_count, unmatched_count, anomaly_count, "
        "total_refund_usd, current_reserve_usd, delta_usd, created_by, created_at, applied_at "
        "FROM refund_verification_batches ORDER BY id DESC LIMIT 100") or []
    return _json_response(_json_safe({"batches": rows}))


@bp.route("/order-analytics/refund-verify/batches/<int:batch_id>")
@login_required
@permission_required("data_analytics")
def refund_verify_batch_detail(batch_id: int):
    rows = oa.query(
        "SELECT extended_order_id, site_code, refund_amount_usd, refund_source, "
        "order_financial_status, matched_package_ids, match_status, note, status "
        "FROM refund_verifications WHERE batch_id=%s ORDER BY match_status, extended_order_id",
        (batch_id,)) or []
    return _json_response(_json_safe({"batch_id": batch_id, "rows": rows}))


@bp.route("/order-analytics/refund-verify/batches/<int:batch_id>/apply", methods=["POST"])
@login_required
@permission_required("data_analytics")
def refund_verify_apply(batch_id: int):
    rv.apply_batch(batch_id)
    from appcore.order_analytics import realtime_cache
    realtime_cache.invalidate_all()
    return _json_response({"ok": True, "batch_id": batch_id})


@bp.route("/order-analytics/refund-verify/batches/<int:batch_id>/discard", methods=["POST"])
@login_required
@permission_required("data_analytics")
def refund_verify_discard(batch_id: int):
    rv.discard_batch(batch_id)
    return _json_response({"ok": True, "batch_id": batch_id})


@bp.route("/order-analytics/refund-verify/batches/<int:batch_id>/revert", methods=["POST"])
@login_required
@permission_required("data_analytics")
def refund_verify_revert(batch_id: int):
    rv.revert_batch(batch_id)
    from appcore.order_analytics import realtime_cache
    realtime_cache.invalidate_all()
    return _json_response({"ok": True, "batch_id": batch_id})
```

> 实现前确认两点：① `realtime_cache` 是否有 `invalidate_all`（见 `appcore/order_analytics/realtime_cache.py` 与 `/order-analytics/realtime-cache/invalidate` 端点实际用的失效函数名），用实际函数名；② `_json_response` / `_json_safe` / `current_user` / `oa` 在该文件已导入（本文件其它端点已在用）。订单 CSV 的列名 `Name` / `Financial Status` 以 Shopify 订单导出实际表头为准。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_refund_verification_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/routes/order_analytics.py tests/test_refund_verification_routes.py
git commit -m "feat(refund-verification): add import/list/apply/revert endpoints"
```

---

## Task 9: data_quality 顶层 + 回归与手测

**Files:**
- Modify: `web/routes/order_analytics.py`
- Test: 现有 order_analytics 测试集

- [ ] **Step 1: 给 batches/detail 两个 GET 端点的 JSON 顶层补 `data_quality`**

参考本文件其它端点的 `_attach_realtime_data_quality` / data_quality 注入方式，给 `refund_verify_batches` 与 `refund_verify_batch_detail` 的返回体加 `data_quality`（最简：`{"status": "ok"}`，与现有 `data_quality.py` 口径一致）。

- [ ] **Step 2: 跑模块回归测试**

Run:
```bash
pytest tests/test_order_profit_aggregation.py tests/test_product_profit_report.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_data_quality.py \
       tests/characterization/test_order_analytics_baseline.py \
       tests/test_refund_verification.py tests/test_refund_verification_routes.py -q
```
Expected: 全部 PASS（确认覆盖逻辑没破坏既有退款/利润口径）

- [ ] **Step 3: 起 dev server 手测一条闭环**

Run: `python -m web.app`（空闲端口），登录后：
- `POST /order-analytics/refund-verify/import` 传一个含 refund 行的 Payments CSV → 返回 `batch_id` + 摘要
- `POST …/<id>/apply` → 200；`GET /order-analytics/realtime-overview?include_profit_summary=1` 观察 `return_reserve_usd` / 利润是否按 `max` 修正
- `POST …/<id>/revert` → 200；再查大盘恢复 1% 口径
Expected: 数字按预期变化、回滚可恢复。

- [ ] **Step 4: Commit**

```bash
git add web/routes/order_analytics.py
git commit -m "feat(refund-verification): attach data_quality and finalize backend"
```

---

## Self-Review（写完计划后已核对）

- **Spec 覆盖**：数据源解析(Task2)、关联分类(Task3)、批次+摘要(Task4)、状态机(Task5)、max 覆盖+realtime(Task6)、产品盈亏链路(Task7)、端点+缓存(Task8)、data_quality+回归(Task9)。UI/Tab 属 Plan 2（前端），本计划不含。
- **占位扫描**：无 TODO/伪代码；每个改代码步骤都给了完整真实代码。
- **类型/命名一致**：`load_refund_verification_adjustments` 返回 `{"package_deltas": {...}}`，realtime 版 `apply_refund_adjustments_to_details` 与 aggregation 版 `apply_refund_adjustments_to_details_aggregation` 分别匹配两条链路字段名，全程一致。
- **需实现者就地确认的现有符号**（已在步骤内标注）：`db_migrations` 入口名、`realtime_cache` 失效函数名、`web.app` 工厂名、订单 CSV 表头、aggregation 明细字段名。
