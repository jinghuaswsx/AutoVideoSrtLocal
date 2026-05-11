# Ads Analytics Inline Search List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change Ads Analytics search in Overview, Campaign, Ad Set, and Ad tabs from autocomplete-style lookup into list queries that render matching records in each tab's existing bottom table.

**Architecture:** Keep the existing `/order-analytics/ad-summary` and `/order-analytics/ads/list` endpoints as list sources and add an optional `q` filter to both. The template reads each tab's search input when loading its list, sends `q`, and removes dropdown rendering while preserving row-click detail navigation for Campaign / Ad Set / Ad.

**Tech Stack:** Python 3.12, Flask routes, `appcore.order_analytics.meta_ads`, Jinja template inline JavaScript, pytest.

---

### Task 1: Backend List Query

**Files:**
- Modify: `tests/test_order_analytics_ads.py`
- Modify: `appcore/order_analytics/meta_ads.py`
- Modify: `web/routes/order_analytics.py`

- [ ] **Step 1: Write the failing Overview data-layer test**

Add a test that calls `oa.get_meta_ad_summary(start_date="2026-04-01", end_date="2026-05-03", q="water-blaster")` and asserts the generated SQL filters product name, media product code, campaign name, normalized campaign code, and matched product code.

- [ ] **Step 2: Write the failing Ads level data-layer test**

Add a test that calls `oa.get_ads_level_list("ad", q="water-blaster")` and asserts the generated SQL filters `ad_name`, `normalized_ad_code`, and `matched_product_code`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_order_analytics_ads.py::test_get_meta_ad_summary_filters_by_search_query tests/test_order_analytics_ads.py::test_get_ads_level_list_filters_by_search_query -q`

Expected: fail because `get_meta_ad_summary` and `get_ads_level_list` do not accept `q`.

- [ ] **Step 4: Implement minimal data-layer filtering**

Add `q: str | None = None` to both data functions, build optional SQL `AND (...)` clauses, and append LIKE params to the relevant queries.

- [ ] **Step 5: Add route passthrough tests and implementation**

Update route tests to assert `q` for `/ad-summary` and `/ads/list`, then pass `(request.args.get("q") or "").strip() or None` from both route handlers.

### Task 2: Frontend List Query

**Files:**
- Modify: `tests/test_order_analytics_ads.py`
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: Write failing template assertions**

Add a test that renders `/order-analytics` and asserts Overview plus Ads level toolbars contain `>查询<`, no `data-ads-search-results`, no `/order-analytics/ads/search`, that `loadAdSummary` appends `q=`, and that `adsLoadList` appends `q=`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_analytics_ads.py::test_ads_level_search_queries_bottom_list_without_dropdown -q`

Expected: fail because the template still includes the dropdown and `/ads/search` fetch.

- [ ] **Step 3: Implement minimal template and JS change**

Add the Overview search input, remove the dropdown container from level toolbars, change the relevant button text to `查询`, make `loadAdSummary` and `adsLoadList` read their current search inputs and append `q` when non-empty, and replace `adsBindSearch` with Enter-to-query binding.

### Task 3: Verification

**Files:**
- Test: `tests/test_order_analytics_ads.py`

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_order_analytics_ads.py -q`

- [ ] **Step 2: Run order analytics regression subset if focused tests pass**

Run: `pytest tests/test_order_analytics_data_quality.py tests/test_order_profit_aggregation.py tests/test_order_analytics_ads.py -q`
