# Product Profit Tab 5 Country Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tab ⑤ “产品国家分析”, convert product-profit tabs to pill buttons under the page title, and make the filter bar show controls specific to the active tab.

**Architecture:** Keep the implementation front-end focused in `web/templates/product_profit_dashboard.html`. Reuse existing `report.json` for the new matrix; no database or API changes are required.

**Tech Stack:** Flask/Jinja template, vanilla JavaScript, existing product-profit routes, pytest static asset tests.

---

### Task 1: Lock UI Contract With Failing Tests

**Files:**
- Modify: `tests/test_product_profit_dashboard_assets.py`

- [x] **Step 1: Add tests**

Add tests that assert:
- the tab nav appears before the filter bar;
- Tab ⑤ button and panel exist;
- `ppd-product-country-matrix`, `loadProductCountryTab()`, and `renderProductCountryMatrix` exist;
- Tab filter controls use `data-filter-control`;
- the tabs include pill styling via `border-radius: 999px`.

- [x] **Step 2: Run red test**

Run:

```bash
.venv/bin/python -m pytest tests/test_product_profit_dashboard_assets.py -q
```

Expected: FAIL because Tab ⑤ and the pill/filter contract do not exist yet.

### Task 2: Implement Template Structure and Filter Switching

**Files:**
- Modify: `web/templates/product_profit_dashboard.html`

- [x] **Step 1: Move the tab nav above the filter bar**

Place `<nav class="ppd-tabs" role="tablist">` directly below `<main class="ppd-main">`, before `<section class="ppd-filters">`.

- [x] **Step 2: Convert tabs to pills**

Update `.ppd-tabs` / `.ppd-tab` CSS to use pill buttons: flex gap, no underline, `border-radius: 999px`, active `var(--primary-color)` background and white text.

- [x] **Step 3: Add filter control metadata**

Add `data-filter-control` to product, country, date, query, and download controls. Add JavaScript `setFilterControlsForTab(tabName)` to hide controls that do not apply to the active tab.

### Task 3: Implement Tab ⑤ Matrix

**Files:**
- Modify: `web/templates/product_profit_dashboard.html`

- [x] **Step 1: Add Tab ⑤ HTML**

Add a tab button `data-tab="product-country"` and a panel `data-panel="product-country"` with empty/loading/error/content states and table `id="ppd-product-country-matrix"`.

- [x] **Step 2: Add data loader**

Add `loadProductCountryTab()` that requires a concrete product, validates dates, fetches `/order-analytics/product-profit/report.json`, and calls `renderProductCountryMatrix(data)`.

- [x] **Step 3: Add matrix renderer**

Aggregate `data.by_country` by `buyer_country`, sort countries by `revenue_usd` descending, and render rows for 订单量、销售额、利润、ROAS.

### Task 4: Verify and Commit

**Files:**
- Modify: `docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md`
- Modify: `docs/superpowers/plans/2026-05-08-product-profit-tab5-country-analysis.md`
- Modify: `tests/test_product_profit_dashboard_assets.py`
- Modify: `web/templates/product_profit_dashboard.html`

- [x] **Step 1: Run tests**

```bash
.venv/bin/python -m pytest tests/test_product_profit_dashboard_assets.py tests/test_product_profit_routes.py -q
```

- [x] **Step 2: Run JS syntax check**

```bash
awk 'BEGIN{flag=0} /^<script>$/{flag=1; next} /^<\/script>$/{flag=0} flag{print}' web/templates/product_profit_dashboard.html | node --check -
```

- [x] **Step 3: Run diff whitespace check**

```bash
git diff --check
```

- [x] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md docs/superpowers/plans/2026-05-08-product-profit-tab5-country-analysis.md tests/test_product_profit_dashboard_assets.py web/templates/product_profit_dashboard.html
git commit -m "feat(product-profit): add product country analysis tab" -m "Docs-anchor: docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md#10-tab-⑤-产品国家分析-新增"
```
