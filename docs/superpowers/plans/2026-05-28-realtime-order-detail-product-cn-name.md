# Realtime Order Detail Product CN Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the internal Chinese product name below the English product name in realtime order detail rows.

**Architecture:** Keep the existing cover image column and product text column. Add `product_cn_names` to realtime order detail API rows by joining `media_products`, then render two text lines in the existing product-name cell.

**Tech Stack:** Python 3.12, Flask/Jinja template, pytest.

---

### Task 1: Add Tests

**Files:**
- Modify: `tests/test_order_analytics_realtime_profit_details.py`
- Modify: `tests/test_order_analytics_template_layout.py`

- [ ] Add a realtime detail SQL/response test that monkeypatches `appcore.order_analytics.realtime.query`, calls `_get_realtime_order_details`, and asserts the SQL contains `LEFT JOIN media_products mp ON mp.id = d.product_id`, selects `product_cn_names`, and returns the mocked Chinese name.
- [ ] Add the same coverage for `_get_realtime_order_details_for_range`.
- [ ] Add a template test asserting `row.product_cn_names` is used after the English product name in `renderRealtimeOrders`.
- [ ] Run:

```bash
pytest tests/test_order_analytics_template_layout.py tests/test_order_analytics_realtime_profit_details.py -q
```

Expected before implementation: the new tests fail because `product_cn_names` is missing.

### Task 2: Add Backend Field

**Files:**
- Modify: `appcore/order_analytics/realtime.py`

- [ ] In `_get_realtime_order_details`, join `media_products` and select `GROUP_CONCAT(DISTINCT NULLIF(mp.name, '') ORDER BY mp.name SEPARATOR ' / ') AS product_cn_names`.
- [ ] Add `"product_cn_names": row.get("product_cn_names")` to the returned detail dict.
- [ ] Make the same query and dict change in `_get_realtime_order_details_for_range`.
- [ ] Run the backend-focused new tests and confirm they pass.

### Task 3: Render Two-Line Product Name

**Files:**
- Modify: `web/templates/order_analytics.html`

- [ ] Add compact styles for `.oar-product-name-stack`, `.oar-product-name-en`, and `.oar-product-name-cn`.
- [ ] Add `addRealtimeProductNameCell(tr, englishName, chineseName, fallbackName)`.
- [ ] Replace the realtime order details `addTextCell(..., 'oar-product-cell')` call with the helper.
- [ ] Keep order profit details unchanged unless future requirements explicitly ask for the same display there.
- [ ] Run:

```bash
pytest tests/test_order_analytics_template_layout.py tests/test_order_analytics_realtime_profit_details.py -q
```

Expected final result: new tests pass. The existing unrelated profit-summary baseline failure may remain.
