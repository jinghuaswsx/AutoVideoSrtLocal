# P1/P2 Acceptance Note - Order Profit Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/order_profit.py`.
- Moved order-profit summary, line listing, loss-alert, and manual-match product queries into `appcore.order_analytics.order_profit_aggregation`.
- Kept the route responsible for login/permission gating, request parameter parsing, and response wrapping only.
- Preserved existing dashboard endpoint payload shapes for summary, lines, loss alerts, and products-for-match.
- Follow-up bug fix: order detail rows must return JSON columns in client-ready shapes.
  `order_profit_lines.missing_fields` is stored as JSON and may be returned by the
  DB driver as a JSON string; `/order-profit/api/orders/<dxm_package_id>` must expose
  it as a list so the dashboard can render incomplete line details without a browser
  `join is not a function` alert.
- Follow-up feature: the order detail tab supports product filtering. The dashboard
  uses a searchable product input backed by active `media_products`; selected
  `product_id` is sent to `/order-profit/api/orders`, and order rows, summary stats,
  and expanded SKU lines are scoped to that product.
- Follow-up UI refinement: the Campaign pairing product selector must be tall enough
  for repeated manual matching work and searchable after click/focus. Search matches
  both `media_products.product_code` and the Chinese product name returned by the
  active-products cache, while saving still submits the selected `product_id` to the
  existing manual-match endpoint.

Verification:

- RED was confirmed first against the missing appcore query helpers and remaining route-level DB import.
- Follow-up RED was confirmed with
  `tests/test_order_profit_aggregation.py::test_detail_normalizes_json_columns_for_lines`:
  `missing_fields` still came back as the JSON string
  `["purchase_price", "shipping_cost"]`.
- Follow-up GREEN focused regression:
  `tests/test_order_profit_routes.py tests/test_order_profit_aggregation.py tests/test_order_profit_response_service.py`:
  `29 passed`.
- Follow-up order-profit architecture checks passed:
  `tests/test_architecture_boundaries.py::test_order_profit_api_responses_live_outside_route_module`
  and
  `tests/test_architecture_boundaries.py::test_order_profit_route_db_access_lives_in_appcore_order_analytics`:
  `2 passed`.
- Product filter RED was confirmed with:
  `tests/test_order_profit_aggregation.py::test_list_filters_by_product_id`,
  `tests/test_order_profit_aggregation.py::test_summary_window_filters_by_product_id`,
  and
  `tests/test_order_profit_routes.py::test_order_profit_orders_route_passes_product_filter`:
  all failed before implementation.
- Product filter GREEN focused regression:
  `tests/test_order_profit_routes.py tests/test_order_profit_aggregation.py tests/test_order_profit_response_service.py`:
  `33 passed`.
- Product filter template render smoke test:
  `tests/test_order_menu_permissions.py::test_order_profit_permission_grant_allows_user_page_access`:
  `1 passed`.
- Campaign pairing product selector RED was confirmed with
  `tests/test_order_profit_dashboard_assets.py::test_order_profit_campaign_product_picker_is_searchable_and_tall`:
  failed before implementation because `.op-product-picker-trigger` was absent.
- Campaign pairing product selector GREEN focused regression:
  `tests/test_order_profit_dashboard_assets.py tests/test_order_profit_routes.py tests/test_order_profit_aggregation.py tests/test_order_profit_response_service.py`:
  `35 passed`.
- Campaign pairing inline script syntax check:
  `sed -n '/<script>/,/<\\/script>/p' web/templates/order_profit_dashboard.html | sed '1d;$d' | node --check -`:
  passed.
- GREEN focused tests:
  `tests/test_order_profit_routes.py`, `tests/test_order_profit_aggregation.py`,
  and `tests/test_architecture_boundaries.py::test_order_profit_route_db_access_lives_in_appcore_order_analytics`:
  `26 passed, 2 warnings`.
- Combined order-profit/analytics/architecture no-db regression:
  `tests/test_order_profit_routes.py`, `tests/test_order_profit_aggregation.py`,
  `tests/test_order_profit_response_service.py`, `tests/test_cost_completeness.py`,
  `tests/test_profit_repository.py`, `tests/test_profit_calculation.py`,
  and `tests/test_architecture_boundaries.py`:
  `250 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.
- Route direct DB dependency scan for `web/routes/order_profit.py` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
