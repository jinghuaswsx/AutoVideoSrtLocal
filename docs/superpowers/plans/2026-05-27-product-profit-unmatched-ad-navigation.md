# Product Profit Unmatched Ad Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the product-profit summary's unmatched ad spend clickable and show the matching global unmatched campaign details in Tab 4.

**Architecture:** Add a focused backend helper in `product_profit_ads.py` for global unmatched campaigns, route it through `ads.json` via `ads_scope=unmatched`, and add a frontend state flag that switches Tab 4 into global unmatched mode. Existing single-product ad detail behavior remains the default.

**Tech Stack:** Python 3.12, Flask, pytest, Jinja template JavaScript.

---

### Task 1: Backend Global Unmatched Ads

**Files:**
- Modify: `appcore/order_analytics/product_profit_ads.py`
- Test: `tests/test_product_profit_ads.py`

- [ ] **Step 1: Write failing tests**

Add tests for `generate_unmatched_ads_report`, including all-country SQL, country SQL, and excluding codes that `resolve_ad_product_match` can now resolve.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_product_profit_ads.py -q`

Expected: fails because `generate_unmatched_ads_report` is not defined.

- [ ] **Step 3: Implement helper**

Add `_load_unmatched_campaign_metrics` and `generate_unmatched_ads_report`. Return `{"accounts": [], "campaigns": [], "daily": [], "unmatched": [...]}` where each unmatched row includes account, campaign, spend, result count, purchase value, Meta ROAS, and last seen date.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_product_profit_ads.py -q`

Expected: product profit ads tests pass.

### Task 2: Route Scope

**Files:**
- Modify: `web/routes/product_profit_report.py`
- Test: `tests/test_product_profit_routes.py`

- [ ] **Step 1: Write failing route test**

Add a test that calls `/order-analytics/product-profit/ads.json?ads_scope=unmatched&date_from=2026-05-01&date_to=2026-05-07` without `product_id` and asserts the route calls `ppa.generate_unmatched_ads_report`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_product_profit_routes.py -q`

Expected: fails because the route still requires `product_id`.

- [ ] **Step 3: Implement route branch**

In `api_ads_json`, parse date and country first, then if `ads_scope == "unmatched"` call `generate_unmatched_ads_report`; otherwise keep existing `product_id` validation and single-product behavior.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_product_profit_routes.py -q`

Expected: product profit route tests pass.

### Task 3: Frontend Navigation

**Files:**
- Modify: `web/templates/product_profit_dashboard.html`
- Test: `tests/test_product_profit_dashboard_assets.py`

- [ ] **Step 1: Write failing template assertions**

Assert the template contains `ppd-unmatched-ad-link`, `ads_scope=unmatched`, `showGlobalUnmatchedAds`, and `globalUnmatchedAdsMode`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_product_profit_dashboard_assets.py -q`

Expected: fails because the frontend markers do not exist.

- [ ] **Step 3: Implement template changes**

Render the summary amount as a button when non-zero. The click handler sets global unmatched mode, switches to Tab 4, fetches `/ads.json?ads_scope=unmatched`, expands the unmatched details area, and hides single-product action buttons when no product is selected.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_product_profit_dashboard_assets.py -q`

Expected: dashboard asset tests pass.

### Task 4: Focused Verification

**Files:**
- No production changes.

- [ ] **Step 1: Run combined targeted tests**

Run: `pytest tests/test_product_profit_ads.py tests/test_product_profit_routes.py tests/test_product_profit_dashboard_assets.py -q`

Expected: all pass.

- [ ] **Step 2: Inspect git diff**

Run: `git diff -- appcore/order_analytics/product_profit_ads.py web/routes/product_profit_report.py web/templates/product_profit_dashboard.html tests/test_product_profit_ads.py tests/test_product_profit_routes.py tests/test_product_profit_dashboard_assets.py docs/superpowers/specs/2026-05-27-product-profit-unmatched-ad-navigation-design.md docs/superpowers/plans/2026-05-27-product-profit-unmatched-ad-navigation.md`

Expected: diff is limited to the planned docs, tests, helper, route, and template.
