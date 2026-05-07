# P1/P2 Acceptance Note - Order Profit Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/order_profit.py`.
- Moved order-profit summary, line listing, loss-alert, and manual-match product queries into `appcore.order_analytics.order_profit_aggregation`.
- Kept the route responsible for login/permission gating, request parameter parsing, and response wrapping only.
- Preserved existing dashboard endpoint payload shapes for summary, lines, loss alerts, and products-for-match.

Verification:

- RED was confirmed first against the missing appcore query helpers and remaining route-level DB import.
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
