# 退款核验 v2（冲减营收）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Shopify Payments 的真实退款（经人工核验）以**营收冲减（contra-revenue）**口径接进实时大盘与产品盈亏核算。已核验订单：营收扣减退款额、取消 1% 准备金、ROAS 真实化；未核验订单维持原 1%。

**Architecture:** 退款真值从 `shopify_payments_transactions`（已有表，按 `transaction_id` 去重累计）聚合；新增 `refund_verification_batches/verifications` 两张 override 表存核验结果；核算出口新增 `_apply_refund_verification_adjustments` 在行级冲减营收 + 归零准备金，汇总层 ROAS 自然跟着变。

**Tech Stack:** Python 3.12 / Flask / MySQL(InnoDB) / pytest

**Spec:** `docs/superpowers/specs/2026-06-13-refund-verification-design.md`（v2 修订 2026-06-14）

**已完成（不在本计划内）：**
- `appcore/order_analytics/refund_verification.py`：`aggregate_payment_refunds` + `extract_order_refund_statuses` + `_normalize_order_name`（commit `404a64bf`）
- `web/routes/order_analytics.py`：6 个端点骨架（commit `8d4fa515`，需 Task 7 修正 import 流程）

---

## File Structure

- Create `db/migrations/2026_06_13_refund_verification_tables.sql`
- Modify `appcore/order_analytics/refund_verification.py` — 加关联/批次/覆盖计算
- Modify `web/routes/order_analytics.py` — 修正 import 端点流程（先写表再从表聚合）
- Modify `appcore/order_analytics/realtime.py` — 出口调用覆盖
- Modify `appcore/order_analytics/order_profit_aggregation.py` — 出口调用覆盖
- Modify `tests/test_refund_verification.py` — 补全测试

---

## Task 1: Migration

**Files:**
- Create: `db/migrations/2026_06_13_refund_verification_tables.sql`

- [ ] **Step 1: 写 migration SQL**

```sql
CREATE TABLE IF NOT EXISTS refund_verification_batches (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
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
  refund_amount_usd DECIMAL(12,4) DEFAULT NULL,
  refund_source VARCHAR(16) DEFAULT NULL,
  order_financial_status VARCHAR(32) DEFAULT NULL,
  matched_package_ids JSON,
  match_status VARCHAR(16) NOT NULL DEFAULT 'matched',
  note VARCHAR(255) DEFAULT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_rv_batch (batch_id),
  KEY idx_rv_order_status (extended_order_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款核验明细(订单级)';
```

- [ ] **Step 2: Commit**

```bash
git add db/migrations/2026_06_13_refund_verification_tables.sql
git commit -m "feat(refund-verification): add override tables migration"
```

---

## Task 2: 从 shopify_payments_transactions 累计聚合退款

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试**

```python
def test_aggregate_refunds_from_db(monkeypatch):
    """退款真值应从 shopify_payments_transactions 表按 transaction_id 去重累计。"""
    def fake_query(sql, args=()):
        if "shopify_payments_transactions" in sql:
            return [
                {"order_name": "#23863", "total_refund": 66.89},
                {"order_name": "#100",   "total_refund": 20.0},
            ]
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    out = rv.aggregate_refunds_from_db(site_code="newjoy")
    assert out["23863"] == 66.89
    assert out["100"] == 20.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_refund_verification.py::test_aggregate_refunds_from_db -v`

- [ ] **Step 3: 实现**

在 `refund_verification.py` 中加：

```python
def aggregate_refunds_from_db(*, site_code: str | None = None) -> dict[str, float]:
    """从 shopify_payments_transactions 按 order_name 聚合全量退款（去重累计）。

    该表以 transaction_id 为唯一键，import_payments_csv 做 upsert，
    增量或全量导入多少次都不会重复——所以这里直接 SUM 即可。
    """
    where = "WHERE type IN ('refund','chargeback') AND order_name IS NOT NULL"
    args: tuple = ()
    if site_code:
        where += " AND source_csv LIKE %s"
        args = (f"%{site_code}%",)
    rows = query(
        "SELECT order_name, SUM(ABS(COALESCE(amount_usd, 0))) AS total_refund "
        f"FROM shopify_payments_transactions {where} "
        "GROUP BY order_name",
        args,
    ) or []
    return {_normalize_order_name(r["order_name"]): round(float(r["total_refund"]), 4)
            for r in rows if r.get("order_name")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_refund_verification.py -v`

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): aggregate refunds from db cumulative"
```

---

## Task 3: 关联 + 分类（带 site_code 跨店隔离）

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试**

```python
def test_build_verification_rows_classifies_with_site(monkeypatch):
    def fake_query(sql, args=()):
        return [
            {"extended_order_id": "23863", "dxm_package_id": "PKG-A",
             "site_code": "newjoy", "revenue": 50.0},
            {"extended_order_id": "301", "dxm_package_id": "PKG-B",
             "site_code": "newjoy", "revenue": 40.0},
        ]
    monkeypatch.setattr(oa, "query", fake_query)

    refunds = {"23863": 66.89, "999": 12.0}
    statuses = {"301": "refunded"}
    rows = rv.build_verification_rows(refunds, statuses, site_code="newjoy")
    by_order = {r["extended_order_id"]: r for r in rows}
    assert by_order["23863"]["match_status"] == "anomaly"   # 退款 > 营收
    assert by_order["23863"]["refund_amount_usd"] == 66.89
    assert by_order["301"]["match_status"] == "anomaly"      # 有状态无金额
    assert by_order["999"]["match_status"] == "unmatched"
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

```python
def _load_order_packages(order_ids: list[str], *, site_code: str | None = None) -> dict[str, dict[str, Any]]:
    if not order_ids:
        return {}
    placeholders = ",".join(["%s"] * len(order_ids))
    args: list[Any] = list(order_ids)
    site_filter = ""
    if site_code:
        site_filter = " AND d.site_code = %s"
        args.append(site_code)
    rows = query(
        "SELECT d.extended_order_id, d.dxm_package_id, d.site_code, "
        "       (COALESCE(SUM(d.line_amount),0) + COALESCE(MAX(d.ship_amount),0)) AS revenue "
        "FROM dianxiaomi_order_lines d "
        f"WHERE d.extended_order_id IN ({placeholders}){site_filter} "
        "GROUP BY d.extended_order_id, d.dxm_package_id, d.site_code",
        tuple(args),
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
    *,
    site_code: str | None = None,
) -> list[dict[str, Any]]:
    order_ids = sorted(set(refunds) | set(statuses))
    pkg_map = _load_order_packages(order_ids, site_code=site_code)
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
                match_status, note = "anomaly", "有退款状态但缺 Payments 金额"
            elif amount > revenue:
                match_status, note = "anomaly", f"退款 {amount} 大于订单营收 {round(revenue,2)}"
            else:
                match_status, note = "matched", None
        rows.append({
            "extended_order_id": oid, "site_code": site,
            "refund_amount_usd": amount, "refund_source": source,
            "order_financial_status": fin_status,
            "matched_package_ids": packages, "match_status": match_status, "note": note,
        })
    return rows
```

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): associate orders with site_code isolation"
```

---

## Task 4: 批次 CRUD + 状态机

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

- [ ] **Step 1: 写失败测试**

```python
def test_create_batch(monkeypatch):
    executed = []
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append(sql))
    monkeypatch.setattr(oa, "query", lambda sql, args=():
        [{"extended_order_id": "23863", "dxm_package_id": "P1", "site_code": "newjoy", "revenue": 100.0}]
        if "dianxiaomi_order_lines" in sql else
        [{"extended_order_id": "23863", "reserve": 1.0}]
        if "order_profit_lines" in sql else
        [{"id": 7}]
    )
    summary = rv.create_batch(
        refunds={"23863": 56.89}, statuses={},
        source_files={"payments_csv": "p.csv"}, created_by="admin", site_code="newjoy",
    )
    assert summary["batch_id"] == 7
    assert summary["matched_count"] == 1


def test_apply_discard_revert(monkeypatch):
    calls = []
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): calls.append(sql))
    rv.apply_batch(7)
    rv.discard_batch(8)
    rv.revert_batch(9)
    assert any("applied" in s and "batches" in s for s in calls)
    assert any("discarded" in s for s in calls)
```

- [ ] **Step 2–4: 实现 `create_batch`, `apply_batch`, `discard_batch`, `revert_batch`，跑测试通过**

```python
def _load_current_reserve(order_ids: list[str]) -> dict[str, float]:
    if not order_ids:
        return {}
    ph = ",".join(["%s"] * len(order_ids))
    rows = query(
        "SELECT d.extended_order_id, SUM(p.return_reserve_usd) AS reserve "
        "FROM order_profit_lines p JOIN dianxiaomi_order_lines d ON d.id=p.dxm_order_line_id "
        f"WHERE d.extended_order_id IN ({ph}) GROUP BY d.extended_order_id",
        tuple(order_ids),
    ) or []
    return {str(r["extended_order_id"]): float(r.get("reserve") or 0) for r in rows}


def create_batch(*, refunds, statuses, source_files, created_by, site_code=None):
    rows = build_verification_rows(refunds, statuses, site_code=site_code)
    matched_ids = [r["extended_order_id"] for r in rows if r["match_status"] == "matched"]
    reserve_map = _load_current_reserve(matched_ids)
    matched = [r for r in rows if r["match_status"] == "matched"]
    total_refund = round(sum(r["refund_amount_usd"] or 0 for r in matched), 4)
    cur_reserve = round(sum(reserve_map.get(r["extended_order_id"], 0) for r in matched), 4)
    counts = {s: sum(1 for r in rows if r["match_status"] == s)
              for s in ("matched", "unmatched", "anomaly")}
    execute(
        "INSERT INTO refund_verification_batches "
        "(status,source_files,site_code,matched_count,unmatched_count,anomaly_count,"
        "total_refund_usd,current_reserve_usd,delta_usd,created_by) "
        "VALUES ('pending',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (json.dumps(source_files, ensure_ascii=False), site_code,
         counts["matched"], counts["unmatched"], counts["anomaly"],
         total_refund, cur_reserve, round(total_refund - cur_reserve, 4), created_by),
    )
    bid = int((query("SELECT LAST_INSERT_ID() AS id") or [{}])[0].get("id"))
    for r in rows:
        execute(
            "INSERT INTO refund_verifications "
            "(batch_id,extended_order_id,site_code,refund_amount_usd,refund_source,"
            "order_financial_status,matched_package_ids,match_status,note,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
            (bid, r["extended_order_id"], r["site_code"], r["refund_amount_usd"],
             r["refund_source"], r["order_financial_status"],
             json.dumps(r["matched_package_ids"], ensure_ascii=False),
             r["match_status"], r["note"]),
        )
    return {"batch_id": bid, "total_refund_usd": total_refund,
            "current_reserve_usd": cur_reserve,
            "delta_usd": round(total_refund - cur_reserve, 4),
            "matched_count": counts["matched"], "unmatched_count": counts["unmatched"],
            "anomaly_count": counts["anomaly"]}


def apply_batch(batch_id):
    execute("UPDATE refund_verification_batches SET status='applied',applied_at=NOW() WHERE id=%s AND status='pending'", (batch_id,))
    execute("UPDATE refund_verifications SET status='applied' WHERE batch_id=%s", (batch_id,))

def discard_batch(batch_id):
    execute("UPDATE refund_verification_batches SET status='discarded' WHERE id=%s AND status='pending'", (batch_id,))
    execute("UPDATE refund_verifications SET status='discarded' WHERE batch_id=%s", (batch_id,))

def revert_batch(batch_id):
    execute("UPDATE refund_verification_batches SET status='discarded' WHERE id=%s AND status='applied'", (batch_id,))
    execute("UPDATE refund_verifications SET status='discarded' WHERE batch_id=%s", (batch_id,))
```

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): batch CRUD and state machine"
```

---

## Task 5: 覆盖计算（核心 v2：冲减营收 + 归零准备金）

**Files:**
- Modify: `appcore/order_analytics/refund_verification.py`
- Test: `tests/test_refund_verification.py`

这是 v2 的核心改动。旧版往 `return_reserve` 加 delta，新版从 `total_revenue` 冲减退款并把 `return_reserve` / `profit_deduction` 归零。

- [ ] **Step 1: 写失败测试**

```python
def test_apply_refund_contra_revenue(monkeypatch):
    """已核验订单：营收冲减、准备金归零、profit 连动下调。"""
    def fake_query(sql, args=()):
        if "refund_verifications" in sql:
            return [{"extended_order_id": "23863", "refund_amount_usd": 30.0}]
        if "order_profit_lines" in sql:
            return [
                {"extended_order_id": "23863", "dxm_package_id": "P1",
                 "reserve": 0.6, "revenue": 60.0},
                {"extended_order_id": "23863", "dxm_package_id": "P2",
                 "reserve": 0.4, "revenue": 40.0},
            ]
        return []
    monkeypatch.setattr(oa, "query", fake_query)

    details = [
        {"dxm_package_id": "P1", "total_revenue": 60.0,
         "return_reserve_usd": 0.6, "profit_deduction_usd": 0.6,
         "order_profit_usd": 40.0, "order_profit_with_estimate_usd": 38.0},
        {"dxm_package_id": "P2", "total_revenue": 40.0,
         "return_reserve_usd": 0.4, "profit_deduction_usd": 0.4,
         "order_profit_usd": 25.0, "order_profit_with_estimate_usd": 23.0},
    ]
    total_deducted = rv.apply_refund_adjustments_to_details(details)

    # refund_capped=min(30,100)=30; P1 share=0.6 → 18, P2 share=0.4 → 12
    assert details[0]["total_revenue"] == 42.0   # 60 - 18
    assert details[1]["total_revenue"] == 28.0   # 40 - 12
    assert details[0]["return_reserve_usd"] == 0  # 归零
    assert details[1]["return_reserve_usd"] == 0
    assert details[0]["profit_deduction_usd"] == 0
    # profit: 原 40 + 取消准备金 0.6 - 营收冲减 18 = 22.6
    assert round(details[0]["order_profit_usd"], 2) == 22.6
    assert round(total_deducted, 2) == 30.0


def test_apply_refund_unverified_untouched():
    """未核验订单不受影响。"""
    details = [
        {"dxm_package_id": "P-OTHER", "total_revenue": 100.0,
         "return_reserve_usd": 1.0, "profit_deduction_usd": 1.0,
         "order_profit_usd": 50.0, "order_profit_with_estimate_usd": 48.0},
    ]
    # 无 monkeypatch → query 会失败或返空 → 函数应安全跳过
    # 但为了单测隔离，显式传空 deltas
    rv._apply_contra_revenue(details, {})
    assert details[0]["total_revenue"] == 100.0
    assert details[0]["return_reserve_usd"] == 1.0
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现覆盖计算**

```python
def load_refund_verification_adjustments() -> dict[str, dict[str, float]]:
    """取最新 applied 退款，返回 {extended_order_id: refund_amount_usd}。"""
    rows = query(
        "SELECT v.extended_order_id, v.refund_amount_usd "
        "FROM refund_verifications v "
        "JOIN (SELECT extended_order_id, MAX(id) AS mid FROM refund_verifications "
        "      WHERE status='applied' AND refund_amount_usd IS NOT NULL "
        "      GROUP BY extended_order_id) latest ON latest.mid = v.id",
    ) or []
    return {str(r["extended_order_id"]): float(r["refund_amount_usd"]) for r in rows}


def _load_package_details_for_refund(order_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """取订单的包裹级 revenue + reserve，用于计算冲减分摊。"""
    if not order_ids:
        return {}
    ph = ",".join(["%s"] * len(order_ids))
    rows = query(
        "SELECT d.extended_order_id, d.dxm_package_id, "
        "       SUM(p.return_reserve_usd) AS reserve, SUM(p.revenue_usd) AS revenue "
        "FROM order_profit_lines p JOIN dianxiaomi_order_lines d ON d.id=p.dxm_order_line_id "
        f"WHERE d.extended_order_id IN ({ph}) GROUP BY d.extended_order_id, d.dxm_package_id",
        tuple(order_ids),
    ) or []
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["extended_order_id"]), []).append(r)
    return out


def _compute_contra_revenue_deltas() -> dict[str, dict[str, float]]:
    """返回 {dxm_package_id: {"revenue_deduct": X, "reserve_release": Y}}。"""
    refund_map = load_refund_verification_adjustments()
    if not refund_map:
        return {}
    pkg_map = _load_package_details_for_refund(list(refund_map.keys()))
    out: dict[str, dict[str, float]] = {}
    for oid, pkgs in pkg_map.items():
        refund = refund_map.get(oid)
        if refund is None:
            continue
        revenue_sum = sum(float(p.get("revenue") or 0) for p in pkgs)
        refund_capped = min(refund, revenue_sum) if revenue_sum > 0 else 0.0
        if refund_capped <= 0:
            continue
        for p in pkgs:
            pid = str(p.get("dxm_package_id"))
            rev = float(p.get("revenue") or 0)
            share = rev / revenue_sum if revenue_sum > 0 else 1.0 / len(pkgs)
            reserve = float(p.get("reserve") or 0)
            out[pid] = {
                "revenue_deduct": round(refund_capped * share, 4),
                "reserve_release": round(reserve, 4),
            }
    return out


def _apply_contra_revenue(details: list[dict[str, Any]], deltas: dict[str, dict[str, float]]) -> float:
    """行级冲减。返回本次冲减营收总额（用于修正 ROAS 分子）。"""
    total_deducted = 0.0
    for row in details:
        pid = str(row.get("dxm_package_id") or "")
        d = deltas.get(pid)
        if not d:
            continue
        rev_deduct = d["revenue_deduct"]
        reserve_release = d["reserve_release"]
        row["total_revenue"] = round(float(row.get("total_revenue") or 0) - rev_deduct, 2)
        row["return_reserve_usd"] = 0
        row["profit_deduction_usd"] = 0
        net_impact = rev_deduct - reserve_release
        for k in ("order_profit_usd", "order_profit_with_estimate_usd"):
            if row.get(k) is not None:
                row[k] = round(float(row.get(k) or 0) - net_impact, 2)
        total_deducted += rev_deduct
    return round(total_deducted, 2)


def apply_refund_adjustments_to_details(details: list[dict[str, Any]]) -> float:
    """实时大盘链路出口调用。返回冲减总额。"""
    if not details:
        return 0.0
    try:
        deltas = _compute_contra_revenue_deltas()
    except Exception:
        return 0.0
    return _apply_contra_revenue(details, deltas)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_refund_verification.py -v`

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/refund_verification.py tests/test_refund_verification.py
git commit -m "feat(refund-verification): contra-revenue override computation"
```

---

## Task 6: 接入 realtime.py + order_profit_aggregation.py

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Modify: `appcore/order_analytics/order_profit_aggregation.py`

在两条核算链路的订单盈亏明细出口，紧跟 `_apply_realtime_ad_cost_adjustments` 之后调用退款覆盖，并把冲减总额从 summary 的 ROAS 分子里减掉。

- [ ] **Step 1: 找到 realtime.py 的调用点**

Run: `grep -n "_apply_realtime_ad_cost_adjustments(" appcore/order_analytics/realtime.py`

每处 `_apply_realtime_ad_cost_adjustments(details, ...)` 之后加：

```python
        from .refund_verification import apply_refund_adjustments_to_details
        refund_deducted = apply_refund_adjustments_to_details(details)
```

然后在对应的 `_build_order_profit_summary(details, ...)` 返回之后、汇总 ROAS 计算前（若 summary 里有 `revenue_with_shipping`），从中减掉 `refund_deducted`：

```python
        # 退款冲减 ROAS 分子
        if refund_deducted > 0:
            for k in ("order_revenue", "revenue_with_shipping", "total_revenue_usd"):
                if k in summary:
                    summary[k] = round(float(summary.get(k) or 0) - refund_deducted, 2)
            if summary.get("ad_spend"):
                summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
```

> **实现者注意**：realtime.py 两条链路结构不同（`get_realtime_roas_overview` vs `_get_realtime_order_profit_details_for_range`）。在明细链路，`refund_deducted` 只修行不修 summary（summary 由汇总函数自动从冲减后的行重算）。在 overview 链路，行级修完后 `_build_order_profit_summary` 会重新从行累加 `total_revenue_usd`，所以行级冲减自然生效；但 `summary["order_revenue"]` / `summary["revenue_with_shipping"]` / `summary["true_roas"]` 来自独立的订单统计路径（`:3541-3594`），需要额外减。**先 `grep` 确认哪些 summary 字段需要手动减**。

- [ ] **Step 2: 同样接入 order_profit_aggregation.py**

找到 `_apply_realtime_ad_cost_adjustments` 调用点，紧跟加退款覆盖。`order_profit_aggregation.py` 的行级字段名可能不同（`return_reserve` / `profit` / `profit_with_estimate`），需要一个 aggregation 版 wrapper：

```python
def apply_refund_adjustments_to_details_aggregation(details: list[dict[str, Any]]) -> float:
    """order_profit_aggregation 链路版本。字段名映射后调同一套 delta 计算。"""
    if not details:
        return 0.0
    try:
        deltas = _compute_contra_revenue_deltas()
    except Exception:
        return 0.0
    total = 0.0
    for row in details:
        pid = str(row.get("dxm_package_id") or "")
        d = deltas.get(pid)
        if not d:
            continue
        rev_deduct = d["revenue_deduct"]
        reserve_release = d["reserve_release"]
        row["total_revenue"] = round(float(row.get("total_revenue") or 0) - rev_deduct, 2)
        row["return_reserve"] = 0
        net_impact = rev_deduct - reserve_release
        for k in ("profit", "profit_with_estimate"):
            if row.get(k) is not None:
                row[k] = round(float(row.get(k) or 0) - net_impact, 2)
        total += rev_deduct
    return round(total, 2)
```

> 实现前先 `grep -n "dxm_package_id\|return_reserve\b" appcore/order_analytics/order_profit_aggregation.py | head` 确认行级字段名。

- [ ] **Step 3: 跑回归测试**

Run:
```bash
python3 -m pytest tests/test_order_profit_aggregation.py tests/test_product_profit_report.py tests/test_refund_verification.py -q
```

- [ ] **Step 4: Commit**

```bash
git add appcore/order_analytics/realtime.py appcore/order_analytics/order_profit_aggregation.py appcore/order_analytics/refund_verification.py
git commit -m "feat(refund-verification): wire contra-revenue into realtime and product profit"
```

---

## Task 7: 修正端点 import 流程（先写表再从表聚合）

**Files:**
- Modify: `web/routes/order_analytics.py`

当前端点是直接从上传文件聚合退款。改成：先调 `import_payments_csv` 写入 `shopify_payments_transactions`（已有函数，upsert 去重），再调 `aggregate_refunds_from_db` 从表中聚合——无论增量还是全量都不丢不重。

- [ ] **Step 1: 修改 `refund_verify_import` 端点**

把现有的：
```python
    refunds = rv.aggregate_payment_refunds(
        parse_payments_csv(io.StringIO(pay_file.read().decode("utf-8-sig")),
                           source_csv=pay_file.filename or "")
    )
```

替换为：
```python
    import_payments_csv(io.StringIO(pay_file.read().decode("utf-8-sig")),
                        source_csv=pay_file.filename or "")
    site_code = (request.form.get("site_code") or "").strip().lower() or None
    refunds = rv.aggregate_refunds_from_db(site_code=site_code)
```

并确保 `import_payments_csv` 在文件顶部或函数内 import。

- [ ] **Step 2: 语法验证**

Run: `python3 -c "import ast; ast.parse(open('web/routes/order_analytics.py').read()); print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add web/routes/order_analytics.py
git commit -m "feat(refund-verification): import writes to db first, then aggregates from db"
```

---

## Task 8: 回归 + 手测

- [ ] **Step 1: 跑模块回归测试**

```bash
python3 -m pytest tests/test_order_profit_aggregation.py tests/test_product_profit_report.py \
       tests/test_order_analytics_realtime_site_filter.py tests/test_order_analytics_data_quality.py \
       tests/test_refund_verification.py -q
```

- [ ] **Step 2: 如果有失败，定位并修复（不能是退款覆盖引入的回归）**

- [ ] **Step 3: 最终 commit**

```bash
git add -A && git commit -m "chore(refund-verification): regression fixes" --allow-empty
```
