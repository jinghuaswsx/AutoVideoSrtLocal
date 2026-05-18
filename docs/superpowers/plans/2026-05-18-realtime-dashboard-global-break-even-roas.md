# Realtime Dashboard Global Break-Even ROAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a realtime dashboard KPI and API field for global break-even ROAS that follows the selected date range, product filter, and store filter.

**Architecture:** Reuse the existing `order_profit_summary` aggregation in `appcore/order_analytics/realtime.py` so all existing realtime overview branches inherit the field. Render the value from the existing top-card request in `web/templates/order_analytics.html`; no new route, SQL path, or table is needed.

**Tech Stack:** Python 3.12, Flask route response JSON, pytest, Jinja template with inline JavaScript.

---

### Task 1: Backend Field and Rounding

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Create: `tests/test_order_analytics_realtime_break_even_roas.py`

- [ ] **Step 1: Write failing backend tests**

Create `tests/test_order_analytics_realtime_break_even_roas.py` with tests that import:

```python
from appcore.order_analytics.realtime import (
    _build_order_profit_summary,
    _build_order_profit_summary_from_status,
    _empty_order_profit_summary,
)
```

The tests must assert:

```python
def test_empty_order_profit_summary_has_global_break_even_roas_default_none():
    summary = _empty_order_profit_summary()
    assert summary["global_break_even_roas"] is None
```

```python
def test_global_break_even_roas_rounds_up_to_three_decimals():
    summary = _build_order_profit_summary(
        [_row(total_revenue=100, purchase=30, logistics=0, shopify_fee=4.94)],
        total_ad_spend_usd=0,
    )
    assert summary["global_break_even_roas"] == 1.538
```

```python
def test_global_break_even_roas_keeps_exact_third_decimal():
    summary = _build_order_profit_summary(
        [_row(total_revenue=1537, purchase=537, logistics=0, shopify_fee=0)],
        total_ad_spend_usd=0,
    )
    assert summary["global_break_even_roas"] == 1.537
```

```python
def test_global_break_even_roas_returns_none_when_available_ad_spend_not_positive():
    summary = _build_order_profit_summary(
        [_row(total_revenue=100, purchase=100, logistics=0, shopify_fee=0)],
        total_ad_spend_usd=0,
    )
    assert summary["global_break_even_roas"] is None
```

```python
def test_global_break_even_roas_from_status_summary():
    status = {
        "total_revenue_usd": 100.0,
        "purchase_cost_with_estimate_usd": 30.0,
        "shipping_cost_with_estimate_usd": 0.0,
        "unallocated_ad_spend_usd": 0.0,
        "overview": {"line_count": 1, "total_profit_usd": 50.0},
        "summary": {
            "ok": {"shopify_fee": 4.94},
            "incomplete": {},
        },
        "estimated": {"lines": 0},
    }
    summary = _build_order_profit_summary_from_status(status, order_count=1)
    assert summary["global_break_even_roas"] == 1.538
```

- [ ] **Step 2: Verify backend tests fail**

Run:

```bash
pytest tests/test_order_analytics_realtime_break_even_roas.py -q
```

Expected: FAIL because `global_break_even_roas` is not yet present.

- [ ] **Step 3: Implement backend helper and field**

In `appcore/order_analytics/realtime.py`, add `Decimal` and `ROUND_CEILING` imports:

```python
from decimal import Decimal, ROUND_CEILING, InvalidOperation
```

Add a helper:

```python
def _global_break_even_roas(summary: dict[str, Any]) -> float | None:
    try:
        revenue = Decimal(str(summary.get("total_revenue_usd") or 0))
        available_ad_spend = (
            revenue
            - Decimal(str(summary.get("profit_deduction_usd") or 0))
            - Decimal(str(summary.get("purchase_cost_with_estimate_usd") or 0))
            - Decimal(str(summary.get("logistics_cost_with_estimate_usd") or 0))
            - Decimal(str(summary.get("shopify_fee_total_usd") or 0))
        )
    except (InvalidOperation, ValueError):
        return None
    if revenue <= 0 or available_ad_spend <= 0:
        return None
    return float((revenue / available_ad_spend).quantize(
        Decimal("0.001"),
        rounding=ROUND_CEILING,
    ))
```

Add `"global_break_even_roas": None` to `_empty_order_profit_summary()`.

After the existing `profit_with_estimate_margin_pct` calculation in both `_build_order_profit_summary()` and `_build_order_profit_summary_from_status()`, assign:

```python
summary["global_break_even_roas"] = _global_break_even_roas(summary)
```

- [ ] **Step 4: Verify backend tests pass**

Run:

```bash
pytest tests/test_order_analytics_realtime_break_even_roas.py -q
```

Expected: all tests pass.

### Task 2: Frontend KPI Rendering

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `tests/test_order_analytics_template_layout.py`

- [ ] **Step 1: Write failing frontend static tests**

Append tests to `tests/test_order_analytics_template_layout.py`:

```python
def test_realtime_global_break_even_roas_kpi_is_rendered():
    panel = _realtime_panel_source()

    assert 'id="realtimeGlobalBreakEvenRoas"' in panel
    assert "全局保本 ROAS" in panel
```

```python
def test_realtime_global_break_even_roas_js_uses_three_decimals():
    template = _template_source()

    assert "profitSummary.global_break_even_roas" in template
    assert "globalBreakEvenRoasValue.toFixed(3)" in template
    assert "realtimeGlobalBreakEvenRoas" in template
```

- [ ] **Step 2: Verify frontend tests fail**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py -q
```

Expected: FAIL because the KPI node and JS are not yet present.

- [ ] **Step 3: Add KPI markup**

In `web/templates/order_analytics.html`, add one top-card item near the existing ROAS and profit cards:

```html
<div class="oa-stat-card">
  <div class="oa-stat-label">全局保本 ROAS</div>
  <div class="oa-stat-value" id="realtimeGlobalBreakEvenRoas">-</div>
  <div class="oa-stat-sub">盈亏平衡点</div>
</div>
```

- [ ] **Step 4: Add KPI rendering code**

In `loadRealtimeTopCards()`, after `var profitSummary = data.order_profit_summary || {};`, render:

```javascript
var globalBreakEvenRoasEl = document.getElementById('realtimeGlobalBreakEvenRoas');
if (globalBreakEvenRoasEl) {
  var globalBreakEvenRoasValue = Number(profitSummary.global_break_even_roas);
  globalBreakEvenRoasEl.textContent = Number.isFinite(globalBreakEvenRoasValue)
    ? globalBreakEvenRoasValue.toFixed(3)
    : '-';
}
```

- [ ] **Step 5: Verify frontend tests pass**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py -q
```

Expected: all tests pass.

### Task 3: Regression Verification and Commit

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused regression suite**

Run:

```bash
pytest tests/test_order_analytics_realtime_break_even_roas.py \
       tests/test_order_analytics_realtime_profit_margin.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_responses_service.py \
       tests/test_order_analytics_dashboard.py \
       tests/characterization/test_order_analytics_baseline.py \
       -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Inspect git diff**

Run:

```bash
git diff -- appcore/order_analytics/realtime.py web/templates/order_analytics.html tests/test_order_analytics_realtime_break_even_roas.py tests/test_order_analytics_template_layout.py
```

Expected: only the backend field, frontend KPI, and tests are changed.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git add appcore/order_analytics/realtime.py web/templates/order_analytics.html tests/test_order_analytics_realtime_break_even_roas.py tests/test_order_analytics_template_layout.py docs/superpowers/plans/2026-05-18-realtime-dashboard-global-break-even-roas.md
git commit -m "feat: add realtime global break-even ROAS"
```
