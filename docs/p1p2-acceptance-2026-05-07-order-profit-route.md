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
- Follow-up feature: summary alerts now stay scoped to the selected date range.
  `unallocated_ad_spend_usd` is summed from date-range `meta_ad_daily_campaign_metrics`
  rows where `product_id IS NULL`, and the incomplete SKU card opens a date-range
  product list with `中文名 - product_code` links to `/medias/?q=<product_code>`.
- Follow-up feature: the `/order-profit` summary tab must show an actionable total
  profit number. The total profit is:
  `ok_profit + incomplete_estimated_profit - date_range_unallocated_ad_spend`.
  The existing `summary.ok.profit` remains available as the confirmed/complete-line
  subtotal. The summary payload must additionally expose an `overview` object with
  revenue, line count, confirmed profit, estimated profit, unallocated ad spend,
  total profit, and total margin percentage.
- The summary tab must explicitly mark estimated data sources instead of presenting
  every cost as equally certain. Required marks:
  Shopify fee uses strategy C estimates, return reserve is a 1% policy reserve,
  purchase fallback uses `revenue * 10%`, shipping fallback uses `revenue * 20%`,
  product `packet_cost_estimated` is an estimated shipping source, and unallocated
  campaign spend is pending product matching but is deducted in total profit.
- Follow-up UI restructure (2026-05-08): the summary tab top cards collapse from
  eight ad-hoc tiles into three first-class cards (总营收 / 总成本 / 总利润),
  each splitting into 已核算 + 未核算/估算 sub-rows with share-of-total chips.
  The reshuffle does not change any backend payload — three identities must
  hold from `/api/summary` data alone, and are checked numerically against
  real data on every release:
    1. `总营收 = summary.ok.revenue + summary.incomplete.revenue`
       (= `data.known_revenue_usd + data.unaccounted_revenue_usd`).
    2. `已核算成本 = (ok+inc) purchase_actual + shipping_cost_actual + ad_cost`;
       `估算成本 = shopify_fee + return_reserve + purchase_estimate + shipping_cost_estimate + unallocated_ad_spend`;
       `总成本 = 已核算成本 + 估算成本`.
    3. `总利润 = overview.total_profit_usd`; `已核算利润 = overview.confirmed_profit_usd`;
       `估算利润 = 总利润 − 已核算利润`. The cards must also satisfy
       `总营收 − 总成本 ≡ 总利润` (zero residual).
  「不完备 SKU 行 / 待配对广告 / 估算项」 demote to an auxiliary chip row
  beneath the three big cards. `opIncompleteCard` becomes a native `<button>`
  but keeps the same id, modal binding, and click target. The cost-breakdown
  table, estimate-marks table, and the four detail tabs (订单/亏损/完备性/配对)
  are intentionally untouched. The data caliber is also encoded as a Jinja
  comment block at the top of `web/templates/order_profit_dashboard.html`
  for future-proof anchoring.

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
- Date-range alert + incomplete-product modal regression:
  `tests/test_order_profit_routes.py tests/test_order_profit_aggregation.py tests/test_order_profit_dashboard_assets.py tests/test_cost_completeness_overview.py`:
  `39 passed`.
- Summary profit and estimate-mark RED was confirmed with:
  `tests/test_order_profit_aggregation.py::test_status_summary_aggregates_line_statuses_and_last_run`,
  `tests/test_order_profit_aggregation.py::test_status_summary_queries_estimated_cost_sources`,
  and
  `tests/test_order_profit_dashboard_assets.py::test_order_profit_dashboard_renders_total_profit_and_estimate_marks`:
  all failed before implementation because the payload had no `overview`,
  the SQL did not aggregate `cost_basis` estimate sources, and the template had
  no total-profit / estimate-mark elements.
- Summary profit and estimate-mark GREEN focused regression:
  `tests/test_order_profit_aggregation.py::test_status_summary_aggregates_line_statuses_and_last_run`,
  `tests/test_order_profit_aggregation.py::test_status_summary_queries_estimated_cost_sources`,
  `tests/test_order_profit_routes.py::test_order_profit_summary_route_uses_aggregate_payload`,
  and
  `tests/test_order_profit_dashboard_assets.py::test_order_profit_dashboard_renders_total_profit_and_estimate_marks`:
  `4 passed`.
- Summary profit and estimate-mark combined no-db regression:
  `tests/test_order_profit_routes.py`, `tests/test_order_profit_aggregation.py`,
  `tests/test_order_profit_response_service.py`, `tests/test_order_profit_dashboard_assets.py`,
  `tests/test_cost_completeness.py`, `tests/test_profit_repository.py`,
  `tests/test_profit_calculation.py`, and `tests/test_architecture_boundaries.py`:
  `294 passed`.
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
- Summary cards restructure GREEN regression (2026-05-08):
  `tests/test_order_profit_dashboard_assets.py tests/test_order_profit_routes.py
   tests/test_order_profit_response_service.py tests/test_order_profit_aggregation.py`:
  `44 passed`.
- Summary cards restructure dev-server smoke (2026-05-08):
  unauthenticated `GET /order-profit` returned `302`; logged-in `GET /order-profit`
  returned `200` with all nine card ids
  (`opTotalRevenue / opKnownRevenue / opUnaccountedRevenue / opTotalCost /
   opKnownCost / opEstimatedCost / opTotalProfit / opKnownProfit / opEstimatedProfit`)
  rendering, and `/order-profit/api/summary` arithmetic-checked
  `总营收 − 总成本 ≡ overview.total_profit_usd` with zero residual
  on real data (`999.73 − 347.53 = 652.20`).

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
