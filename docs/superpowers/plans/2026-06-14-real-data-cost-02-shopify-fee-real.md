# Plan ② 手续费真实化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打开已实现但未启用的真实手续费链路，全量重算历史手续费为真实 Shopify payment fee（缺失则区域动态费率/策略C估算并标注），并让每周 payments 导入后自动重算把估算替换成真实值。

**Architecture:** 基础设施**已就绪**——真实优先 resolver（`shopify_fee_resolver.resolve_shopify_fee_for_order`：`actual_payment`→`dynamic_region_rate`→`strategy_c_fallback`）、动态快照生成（`shopify_fee_dynamic.refresh_fee_rate_snapshots`，已接在 `import_payments_csv` 内）、开关（`config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT`）、backfill 边界（`_should_skip_for_dynamic_fee_boundary`：开关生效日**之后**的订单本就会被覆盖重算）都已实现。本 plan 唯一新增代码是「payments 导入后自动重算受影响 `business_date` 范围」；其余是运维编排：把开关设到 ≤ 最早订单日 → 全量重算自然覆盖历史手续费。

**Tech Stack:** Python 3.12、pymysql、pytest + monkeypatch、`threading`（fire-and-forget 后台重算）。

**Spec:** `docs/superpowers/specs/2026-06-14-cost-accounting-real-data-first-design.md` §6.2 / §7。
**依赖:** Plan ①（汇率回填）已完成——全量重算需同时吃到真实日汇率。

---

## File Structure

- **Modify** `appcore/order_analytics/shopify_payments_import.py`：`import_payments_csv` 末尾计算受影响 `business_date` 范围 + 后台触发 backfill 重算；新增 `_affected_business_dates` / `_trigger_profit_recompute`。
- **Modify** `tests/test_shopify_payments_import.py`：受影响范围计算 + 触发重算的单测。
- **Modify** `tests/test_order_profit_backfill_dynamic_fee.py`：新增 characterization——开关设到最早日时历史行被覆盖（证明无需改 `_should_skip`）。
- **无生产逻辑改动**：`_should_skip_for_dynamic_fee_boundary`、resolver、快照生成均不动。

---

## Task 1：Characterization——证明「设开关到最早日 = 全量覆盖历史」

**目的**：spec 早先设想「改 `_should_skip_for_dynamic_fee_boundary` 允许全量覆盖」，实测其现有逻辑已满足——开关生效日**之后**的订单（含已落库历史行）不跳过、会被 upsert 覆盖。本任务用测试锁定这一行为，避免后续误改。

**Files:**
- Test: `tests/test_order_profit_backfill_dynamic_fee.py`

- [ ] **Step 1: 写测试**（追加到文件末尾）

```python
def test_history_lines_recomputed_when_effective_at_predates_all_orders(monkeypatch):
    # 开关设到 2026-01-01（早于最早订单 2/24）→ 所有历史订单 order_time >= 生效日
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-01-01T00:00:00+08:00")

    # 已落库历史行（existing_profit_line_id 有值）、订单时间 2/24 → 不应被跳过（会重算覆盖）
    assert not backfill._should_skip_for_dynamic_fee_boundary(
        {"order_paid_at": datetime(2026, 2, 24, 10, 0, 0), "existing_profit_line_id": 5}
    )
    # 注：「真正早于生效日的订单仍跳过」的边界保护已由现成的
    # test_should_skip_line_before_dynamic_effective_at 覆盖，此处不重复
    # （避免 naive datetime 被 is_dynamic_fee_effective 当 UTC 比较的时区陷阱）。
```

- [ ] **Step 2: 运行确认通过（characterization 直接 PASS）**

Run: `pytest tests/test_order_profit_backfill_dynamic_fee.py::test_history_lines_recomputed_when_effective_at_predates_all_orders -v`
Expected: PASS（证明现有逻辑已支持全量覆盖，无需改生产代码）

- [ ] **Step 3: Commit**

```bash
git add tests/test_order_profit_backfill_dynamic_fee.py
git commit -m "test(profit): 锁定设开关到最早日即全量覆盖历史手续费的行为"
```

---

## Task 2：payments 导入后自动重算受影响范围（自动替换）

**Files:**
- Modify: `appcore/order_analytics/shopify_payments_import.py`
- Test: `tests/test_shopify_payments_import.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_shopify_payments_import.py`）

```python
def test_import_payments_triggers_recompute_for_affected_business_dates(monkeypatch):
    import io
    from datetime import date
    from appcore.order_analytics import shopify_payments_import as mod

    monkeypatch.setattr(mod, "parse_payments_csv", lambda stream, source_csv="": [
        {"transaction_id": "t1", "transaction_date": "2026-03-01", "payout_id": "p1",
         "type": "charge", "order_name": "#3001", "presentment_currency": "EUR",
         "amount_usd": 40.0, "fee_usd": 2.5, "net_usd": 37.5, "card_brand": "visa",
         "inferred_card_origin": "DE", "inferred_tier": "B", "matches_standard": 1,
         "source_csv": "newjoyloo__x.csv", "raw_row_json": "{}"},
    ])
    monkeypatch.setattr(mod, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(mod, "refresh_fee_rate_snapshots", lambda **k: {"saved": 1})
    monkeypatch.setattr(mod, "query_one", lambda sql, args=(): {"a": date(2026, 2, 24), "b": date(2026, 6, 1)})

    triggered = {}
    monkeypatch.setattr(mod, "_trigger_profit_recompute", lambda f, t: triggered.update(f=f, t=t))

    stats = mod.import_payments_csv(io.StringIO("x"), source_csv="newjoyloo__x.csv")

    assert stats["affected_business_dates"] == {"from": "2026-02-24", "to": "2026-06-01"}
    assert triggered == {"f": date(2026, 2, 24), "t": date(2026, 6, 1)}


def test_import_payments_no_recompute_when_no_matching_orders(monkeypatch):
    import io
    from appcore.order_analytics import shopify_payments_import as mod

    monkeypatch.setattr(mod, "parse_payments_csv", lambda stream, source_csv="": [
        {"transaction_id": "t9", "transaction_date": "2026-03-01", "payout_id": "p",
         "type": "charge", "order_name": "#9999", "presentment_currency": "USD",
         "amount_usd": 10.0, "fee_usd": 0.6, "net_usd": 9.4, "card_brand": "visa",
         "inferred_card_origin": "US", "inferred_tier": "A", "matches_standard": 1,
         "source_csv": "newjoyloo__x.csv", "raw_row_json": "{}"},
    ])
    monkeypatch.setattr(mod, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(mod, "refresh_fee_rate_snapshots", lambda **k: {"saved": 0})
    monkeypatch.setattr(mod, "query_one", lambda sql, args=(): {"a": None, "b": None})

    calls = []
    monkeypatch.setattr(mod, "_trigger_profit_recompute", lambda f, t: calls.append((f, t)))

    stats = mod.import_payments_csv(io.StringIO("x"), source_csv="newjoyloo__x.csv")

    assert stats["affected_business_dates"] == {"from": None, "to": None}
    assert calls == []
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_shopify_payments_import.py -k recompute -v`
Expected: FAIL，`module ... has no attribute '_trigger_profit_recompute'` / `'_affected_business_dates'`

- [ ] **Step 3: 实现**

在 `appcore/order_analytics/shopify_payments_import.py` 顶部 import 区确保有（缺则补）：

```python
import logging
import threading
from appcore.db import query_one
```
（`log = logging.getLogger(__name__)` 若文件已有则不重复。）

在 `import_payments_csv` 之前新增两个 helper：

```python
def _affected_business_dates(order_names):
    """本次导入的 order_name 映射到店小秘订单的 meta_business_date 范围。"""
    candidates = set()
    for raw in order_names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        candidates.add(name)
        candidates.add(name[1:] if name.startswith("#") else f"#{name}")
    if not candidates:
        return None, None
    placeholders = ", ".join(["%s"] * len(candidates))
    row = query_one(
        f"SELECT MIN(meta_business_date) AS a, MAX(meta_business_date) AS b "
        f"FROM dianxiaomi_order_lines WHERE extended_order_id IN ({placeholders})",
        tuple(candidates),
    )
    if not row:
        return None, None
    return row.get("a"), row.get("b")


def _trigger_profit_recompute(date_from, date_to):
    """后台重算受影响 business_date 范围，让新 payments 的真实手续费替换估算（fire-and-forget）。"""
    def _run():
        try:
            from tools.order_profit_backfill import backfill
            backfill(date_from, date_to)
        except Exception:
            log.exception(
                "payments-triggered profit recompute failed (%s ~ %s)", date_from, date_to
            )

    threading.Thread(target=_run, name="payments-profit-recompute", daemon=True).start()
```

在 `import_payments_csv` 的 `return stats` 之前（即 `refresh_fee_rate_snapshots` 那段之后）插入：

```python
    order_names = [r.get("order_name") for r in rows if r.get("order_name")]
    bd_from, bd_to = _affected_business_dates(order_names)
    stats["affected_business_dates"] = {
        "from": bd_from.isoformat() if hasattr(bd_from, "isoformat") else bd_from,
        "to": bd_to.isoformat() if hasattr(bd_to, "isoformat") else bd_to,
    }
    if bd_from and bd_to:
        _trigger_profit_recompute(bd_from, bd_to)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_shopify_payments_import.py -k recompute -v`
Expected: PASS（2 项）

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/shopify_payments_import.py tests/test_shopify_payments_import.py
git commit -m "feat(payments): 导入后按受影响 business_date 后台重算，真实手续费替换估算"
```

---

## Task 3：运维启用与全量重算（线上，非代码）

**前置**：Task 1-2 + Plan ① 已部署线上；用户已导入一份最新 Payments/Transactions CSV。

- [ ] **Step 1: 设开关到最早订单日**（写入线上环境变量，与 gunicorn 同 env）

在线上服务 env 增加（最早订单 2026-02-24，留余量取月初）：
```
SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT=2026-01-01T00:00:00+08:00
```
> ⚠️ 改 env 需重启服务才生效——**重启动作必须由用户确认**，不自动执行。

- [ ] **Step 2: 导入最新 payments（用户操作）触发快照生成**

用户在「订单利润 / 产品盈亏 → 导入 Payments」上传最新 Shopify Payments/Transactions CSV。
验证快照已生成：
```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python -c "from appcore.db import query_one; print(query_one(\"SELECT COUNT(*) n, MAX(window_end_date) w FROM shopify_fee_rate_snapshots\"))"'
```
Expected: `n>0`、`w` 接近最新 payments 日。

- [ ] **Step 3: 全量重算（不传 --rmb，走真实日汇率 + 真实手续费）**

```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python tools/order_profit_backfill.py --from 2026-02-24 --to 2026-06-13'
```
> 不带 `--rmb`：逐单走日汇率链路（Plan ① 已回填）；开关已设 → resolver 走真实优先。耗时较长，建议低峰执行。

- [ ] **Step 4: 验证真实手续费覆盖**

```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python -c "from appcore.db import query; [print(r) for r in query(\"SELECT shopify_fee_source, COUNT(*) n, ROUND(SUM(shopify_fee_usd),0) fee FROM order_profit_lines GROUP BY shopify_fee_source ORDER BY n DESC\")]"'
```
Expected: 出现 `actual_payment` 行且覆盖匹配到 payments 的订单（约 63% GMV）；其余为 `dynamic_region_rate` / `strategy_c_fallback`（待对账，随后续 payments 收敛）。

---

## Self-Review

- **Spec 覆盖**：§6.2「设开关 + 快照 + 全量重算」=Task 1+3；「自动替换」=Task 2；「`_should_skip` 改动」经实测**不需要**，已在本 plan Architecture 与 spec §6.2 纠正为「设开关即可」。§7「手动周导 + 监控」中**监控**移至 Plan ③（断更告警与前端披露集中在 ③）。
- **占位符**：无；每个代码步含完整代码与命令。
- **类型一致**：`_affected_business_dates(order_names)->(date|None,date|None)`、`_trigger_profit_recompute(date_from,date_to)` 在实现与测试 monkeypatch 中签名一致；`stats["affected_business_dates"]` 结构在实现与断言一致。
- **风险**：后台线程 fire-and-forget——重算失败仅记日志、不影响导入响应；幂等可重跑。同步重算会拖慢 web 响应，故用线程。
