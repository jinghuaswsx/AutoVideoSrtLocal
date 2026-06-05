# Analytics Date Range Picker Mobile Auto Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shared data-analysis date range picker fit mobile screens and auto-apply after the second date click.

**Architecture:** Keep one shared vanilla JS picker. The picker commits immediately after the end date is selected, while page templates map the existing `analytics-date-range:apply` event to their current refresh functions only where existing `change` listeners do not already load data.

**Tech Stack:** Flask/Jinja templates, vanilla JavaScript, pytest static contract tests.

---

### Task 1: Red Tests For Picker Contract

**Files:**
- Create: `tests/test_analytics_date_range_picker_asset.py`
- Modify: `tests/test_order_analytics_template_layout.py`
- Modify: `tests/test_order_profit_dashboard_assets.py`
- Modify: `tests/test_product_profit_dashboard_assets.py`

- [ ] **Step 1: Add static tests**

Add tests that read `web/static/analytics_date_range_picker.js` and assert:

```python
assert "Docs-anchor: docs/superpowers/specs/2026-06-05-analytics-date-range-picker-mobile-auto-apply-design.md" in script
assert "@media(max-width:640px)" in script
assert ".analytics-range-panel{position:fixed;" in script
assert "bottom:0;" in script
assert "max-height:min(78vh,680px);" in script
assert "第二个日期会自动生效" in script
assert "applyRange();" in select_day_block
assert "确认后生效" not in script
```

Add template tests that assert the three pages consume `analytics-date-range:apply` for ranges that do not already load on hidden input `change`.

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
pytest tests/test_analytics_date_range_picker_asset.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_order_profit_dashboard_assets.py \
       tests/test_product_profit_dashboard_assets.py -q
```

Expected: FAIL because the picker still uses mobile absolute positioning, waits for confirmation, and pages do not consume the apply event.

### Task 2: Shared Picker Auto Apply And Mobile Layout

**Files:**
- Modify: `web/static/analytics_date_range_picker.js`

- [ ] **Step 1: Replace mobile CSS**

Change the `max-width:640px` rule so `.analytics-range-panel` is fixed to the bottom of the viewport, uses token spacing on left/right, has safe-area padding, one-column calendars, and internal scrolling.

- [ ] **Step 2: Auto-apply after the second date**

In `selectDay`, after the second date is selected and reversed ranges are swapped, call `applyRange()` and return. Update helper text so it no longer says “确认后生效”.

- [ ] **Step 3: Run picker tests**

Run:

```bash
pytest tests/test_analytics_date_range_picker_asset.py -q
```

Expected: PASS.

### Task 3: Page Apply Event Bindings

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `web/templates/order_profit_dashboard.html`
- Modify: `web/templates/product_profit_dashboard.html`

- [ ] **Step 1: Bind missing order analytics loads**

Add one `handleAnalyticsDateRangeApply` listener. It should load only ranges without existing load-on-change behavior: country dashboard, true ROAS, DXM orders, ad overview, unmatched campaigns, ads level list/detail, and manual ad spend list. It must ignore realtime, new product launch, product dashboard, and Meta sync.

- [ ] **Step 2: Bind order-profit and product-profit loads**

On `/order-profit`, reload with `refreshAll()` when `opDateFrom/opDateTo` apply. On `/product-profit`, sync URL/highlight and call `reloadActiveTab()` when `ppd-from/ppd-to` apply.

- [ ] **Step 3: Run template tests**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py \
       tests/test_order_profit_dashboard_assets.py \
       tests/test_product_profit_dashboard_assets.py -q
```

Expected: PASS.

### Task 4: Regression

**Files:**
- No new files.

- [ ] **Step 1: Run focused data-analysis regression**

Run:

```bash
pytest tests/test_analytics_date_range_picker_asset.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_order_analytics_ads.py \
       tests/test_order_analytics_dianxiaomi_analysis.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_profit_dashboard_assets.py \
       tests/test_product_profit_dashboard_assets.py -q
```

Expected: PASS.

- [ ] **Step 2: Smoke unauthenticated route**

Start a local dev server on an unused port and confirm `/order-analytics` returns 302 for unauthenticated access, not 500.
