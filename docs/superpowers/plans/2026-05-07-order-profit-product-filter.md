# Order Profit Product Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a searchable product filter to the `/order-profit` order detail tab so users can narrow order rows, summary stats, and expanded SKU details to one product.

**Architecture:** Reuse the existing order-profit aggregation layer as the only DB query owner. The route parses `product_id` and passes it to the aggregation queries; the dashboard reuses the existing active product list endpoint to populate a searchable datalist-backed input.

**Tech Stack:** Flask routes, appcore order analytics SQL helpers, Jinja template with plain JavaScript, pytest no-db monkeypatch tests.

---

### Task 1: Backend Product Filter Contract

**Files:**
- Modify: `tests/test_order_profit_aggregation.py`
- Modify: `tests/test_order_profit_routes.py`
- Modify: `appcore/order_analytics/order_profit_aggregation.py`
- Modify: `web/routes/order_profit.py`

- [ ] **Step 1: Write aggregation failing tests**

Add tests showing `get_order_profit_list(..., product_id=123)` and `get_order_profit_summary_for_window(..., product_id=123)` include `p.product_id = %s` and append `123` before limit/offset args.

- [ ] **Step 2: Run aggregation tests to verify RED**

Run:

```bash
/opt/autovideosrt/venv/bin/pytest tests/test_order_profit_aggregation.py::test_list_filters_by_product_id tests/test_order_profit_aggregation.py::test_summary_window_filters_by_product_id -q
```

Expected: fail because the functions do not accept `product_id`.

- [ ] **Step 3: Implement aggregation product filter**

Update the two functions to accept `product_id: int | None = None`, add `AND p.product_id = %s` to their base `WHERE` clauses when present, and pass the same filter into the status-bucket query.

- [ ] **Step 4: Write route failing test**

Add a route test showing `/order-profit/api/orders?...&product_id=123` passes `product_id=123` to both aggregation helpers and returns `"filter_product_id": 123`.

- [ ] **Step 5: Implement route parsing**

Parse `product_id` from query args. Blank, missing, or non-positive values are treated as `None`; positive integers are passed through.

### Task 2: Searchable Product Filter UI

**Files:**
- Modify: `web/templates/order_profit_dashboard.html`

- [ ] **Step 1: Add controls**

Add a product search input beside the status filter. Use `<input list="opOrderProductOptions">`, a hidden `#opOrderProductId`, a clear button, and a datalist rendered from the products cache.

- [ ] **Step 2: Wire product loading**

Reuse `loadProductsCache()` and call it before order refreshes. Build the datalist from `product_code`, `name`, and `id`.

- [ ] **Step 3: Wire filtering**

When the user types an exact datalist option, store that product id and call `refreshOrders()`. Append `product_id` to `/order-profit/api/orders` only when a selected id is present. Clear button resets the product id and refreshes orders.

### Task 3: Verification

**Files:**
- Modify: `docs/p1p2-acceptance-2026-05-07-order-profit-route.md`

- [ ] **Step 1: Record verification in acceptance note**

Document the product filter behavior and the test commands used.

- [ ] **Step 2: Run focused regression**

Run:

```bash
/opt/autovideosrt/venv/bin/pytest tests/test_order_profit_routes.py tests/test_order_profit_aggregation.py tests/test_order_profit_response_service.py -q
```

Expected: all pass.

- [ ] **Step 3: Run boundary and syntax checks**

Run:

```bash
/opt/autovideosrt/venv/bin/pytest tests/test_architecture_boundaries.py::test_order_profit_api_responses_live_outside_route_module tests/test_architecture_boundaries.py::test_order_profit_route_db_access_lives_in_appcore_order_analytics -q
/opt/autovideosrt/venv/bin/python -m compileall appcore web tests -q
git diff --check
```

Expected: all pass.
