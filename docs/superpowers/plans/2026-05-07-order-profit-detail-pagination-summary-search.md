# Order Profit Detail Pagination Summary Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 100-row pagination, all-range summary totals with missing-cost estimates, auto-query date controls, and product search filtering to the realtime `订单盈亏明细` view.

**Architecture:** Keep `/order-analytics/realtime-overview` as the single data endpoint. Extend the realtime aggregation helpers with optional `product_id`, `page`, and `page_size`; return paged rows plus an all-filter summary. Render the controls in `web/templates/order_analytics.html` with vanilla JavaScript, reusing the existing `/order-analytics/search` product lookup.

**Tech Stack:** Flask, PyMySQL-style query wrappers, Jinja templates, vanilla JavaScript, pytest.

---

## Docs Anchor

- `docs/superpowers/specs/2026-05-07-order-profit-detail-tab-design.md#2026-05-07-增量需求分页汇总估算和产品筛选`
- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md#实时大盘改版贴齐国家看板的密度与时间选择`
- `AGENTS.md#Frontend Design System — Ocean Blue Admin`

## File Structure

- Modify `appcore/order_analytics/realtime.py`
  - Add `product_id`, `page`, `page_size` arguments to realtime overview.
  - Filter order and profit detail SQL by `d.product_id`.
  - Page `order_profit_details` at 100 rows.
  - Return `order_profit_summary` based on all filtered rows, using purchase 10% and logistics 20% estimates where missing.
- Modify `web/routes/order_analytics.py`
  - Parse `product_id`, `page`, and `page_size`.
  - Forward them to `oa.get_realtime_roas_overview`.
- Modify `web/templates/order_analytics.html`
  - Rename `刷新` to `查询`.
  - Add realtime product search controls.
  - Send selected `product_id` and current page to realtime requests.
  - Render summary strip and pagination controls in the `订单盈亏明细` panel.
- Modify tests:
  - `tests/test_order_analytics_realtime_profit_details.py`
  - `tests/test_order_analytics_true_roas.py`

No migration. No scheduled task change. No CHANGELOG exists in repo root.

## Task 1: Backend Contract Tests

- [ ] Add failing tests in `tests/test_order_analytics_realtime_profit_details.py` for:
  - `page=2&page_size=100` produces `LIMIT 100 OFFSET 100`.
  - `product_id=42` adds `d.product_id = %s`.
  - `order_profit_summary` totals all filtered rows, not only paged rows.
  - missing purchase uses `total_revenue * 0.10`; missing logistics uses `total_revenue * 0.20`.
- [ ] Add route forwarding test in `tests/test_order_analytics_true_roas.py` for `product_id`, `page`, and `page_size`.
- [ ] Run the focused tests and confirm they fail for missing signature/fields.

## Task 2: Backend Implementation

- [ ] Update `appcore/order_analytics/realtime.py` signatures:
  - `get_realtime_roas_overview(..., product_id=None, page=1, page_size=100)`.
  - `_get_realtime_order_details*` and `_get_realtime_order_profit_details*` accept `product_id`.
- [ ] Add SQL condition builder for realtime order queries:
  - base filters stay `site_code IN ('newjoy', 'omurio')`.
  - append `AND d.product_id = %s` or `AND product_id = %s` only when product filter is set.
- [ ] Refactor profit row formatting so paged rows and summary share the same missing-cost logic.
- [ ] Return:
  - `order_profit_details`
  - `order_profit_details_page`
  - `order_profit_summary`
- [ ] Update `web/routes/order_analytics.py` parsing and validation.
- [ ] Run backend focused tests until green.

## Task 3: Frontend Tests

- [ ] Add template regression tests in `tests/test_order_analytics_true_roas.py` for:
  - realtime button text is `查询`.
  - product search controls exist in realtime toolbar.
  - subtab request includes `product_id`, `page`, `page_size=100`, and `include_details=1`.
  - summary strip IDs exist.
  - pagination button IDs exist.
- [ ] Run the template tests and confirm they fail before template edits.

## Task 4: Frontend Implementation

- [ ] Update `web/templates/order_analytics.html`:
  - change `realtimeRefresh` text to `查询`.
  - add product search input, selected-product chip, and clear button in the realtime toolbar.
  - add summary strip in `realtimeSubProfitDetails` header.
  - add pagination controls below the profit table.
- [ ] Update JS:
  - maintain `realtimeState.productId`, `productLabel`, and `profitPage`.
  - date preset/input changes call `loadRealtimeOverview()` and reset `profitPage=1`.
  - product selection resets page and reloads top cards + sub tabs.
  - `loadRealtimeTopCards()` sends `product_id`.
  - `loadRealtimeSubTabs()` sends `product_id`, `page`, `page_size=100`, `include_details=1`.
  - render summary and pagination from response.
- [ ] Run frontend template tests until green.

## Task 5: Verification

- [ ] Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_true_roas.py -q
```

- [ ] Run:

```bash
python -m compileall web appcore tests -q
```

- [ ] Run:

```bash
git diff --check
```

- [ ] Inspect `git diff --stat` and confirm changed files match this plan.
