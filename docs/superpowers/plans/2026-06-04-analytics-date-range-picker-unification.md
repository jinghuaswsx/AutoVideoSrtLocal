# Analytics Date Range Picker Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace separate start/end date pickers across the data analysis module with one confirmed date-range picker while preserving existing hidden values and API parameters.

**Architecture:** Add a small shared browser script that upgrades marked date-range containers into two-month range panels. Templates keep the old start/end input ids as hidden fields, so existing page scripts continue to read and write the same values. Tests assert markup, preserved ids, and script wiring.

**Tech Stack:** Flask/Jinja templates, vanilla JavaScript, pytest template/route tests.

---

### Task 1: Red Tests For Template Contract

**Files:**
- Modify: `tests/test_order_analytics_template_layout.py`
- Modify: `tests/test_order_analytics_ads.py`
- Modify: `tests/test_order_analytics_dianxiaomi_analysis.py`
- Modify: `tests/test_order_profit_routes.py`
- Modify: `tests/test_product_profit_routes.py`

- [ ] **Step 1: Add failing assertions**

Add tests that assert:

```python
assert 'data-analytics-date-range' in body
assert 'analytics_date_range_picker.js' in body
assert 'id="realtimeStartDate" type="hidden"' in body or 'type="hidden" id="realtimeStartDate"' in body
assert 'input type="date" id="realtimeStartDate"' not in body
```

Use equivalent assertions for `dxmStartDate`, `adStartDate`, `opDateFrom`, and `ppd-from`.

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py tests/test_order_analytics_ads.py tests/test_order_analytics_dianxiaomi_analysis.py tests/test_order_profit_routes.py tests/test_product_profit_routes.py -q
```

Expected: FAIL because templates still render direct `input type="date"` fields and do not include the shared script.

### Task 2: Shared Picker Script

**Files:**
- Create: `web/static/analytics_date_range_picker.js`

- [ ] **Step 1: Implement the shared picker**

Create `window.AnalyticsDateRangePicker` with `init`, `initAll`, and `syncAll`. It must parse ISO dates, render two months, support start/end clicks, swap reversed selections, confirm/cancel, close on outside click/Escape, write hidden inputs, dispatch `change`, and update trigger text.

- [ ] **Step 2: Include script in analytics templates**

Add:

```html
<script src="{{ url_for('static', filename='analytics_date_range_picker.js') }}"></script>
```

to `order_analytics.html`, `order_profit_dashboard.html`, and `product_profit_dashboard.html`.

### Task 3: Replace Date Range Markup

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `web/templates/order_profit_dashboard.html`
- Modify: `web/templates/product_profit_dashboard.html`

- [ ] **Step 1: Replace paired `input type="date"` controls**

For each start/end pair, render one `data-analytics-date-range` container and keep old ids as hidden inputs.

- [ ] **Step 2: Keep single-date inputs native**

Leave `weeklyRoasWeekStart` and `amsModalDate` as `input type="date"` because they are not date ranges.

- [ ] **Step 3: Initialize picker**

On each template after existing DOM init code:

```js
if (window.AnalyticsDateRangePicker) window.AnalyticsDateRangePicker.initAll();
```

After functions that programmatically write dates, call:

```js
if (window.AnalyticsDateRangePicker) window.AnalyticsDateRangePicker.syncAll();
```

### Task 4: Green Tests And Regression

**Files:**
- Modify tests from Task 1 if exact string order differs while preserving behavior assertions.

- [ ] **Step 1: Run focused template tests**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py tests/test_order_analytics_ads.py tests/test_order_analytics_dianxiaomi_analysis.py tests/test_order_profit_routes.py tests/test_product_profit_routes.py -q
```

Expected: PASS.

- [ ] **Step 2: Run data-analysis regression subset**

Run:

```bash
pytest tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_profit_aggregation.py \
       tests/test_order_analytics_ads.py \
       tests/test_product_profit_report.py \
       tests/characterization/test_order_analytics_baseline.py -q
```

Expected: PASS.

- [ ] **Step 3: Manual smoke**

Start a dev server on an unused local port, confirm `/order-analytics`, `/product-profit`, and `/order-profit` redirect unauthenticated users with HTTP 302 rather than 500.
