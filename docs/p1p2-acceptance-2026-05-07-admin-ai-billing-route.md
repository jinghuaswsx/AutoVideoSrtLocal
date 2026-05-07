# P1/P2 Acceptance Note - Admin AI Billing Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/admin_ai_billing.py`.
- Moved AI usage report SQL, detail filter option queries, group-by normalization, and payload size formatting into `appcore.usage_log`.
- Kept the route responsible for login/admin gating, request parsing, template rendering, payload endpoints, and CSV streaming only.
- Preserved existing admin/user scoping, pagination, CSV columns, and payload lookup behavior.

Verification:

- RED was confirmed first against the remaining route-level DB import and missing `appcore.usage_log` report helpers.
- GREEN focused tests:
  `tests/test_architecture_boundaries.py::test_admin_ai_billing_report_queries_live_in_appcore_usage_log`
  and `tests/test_ai_billing_routes.py::test_my_ai_usage_only_returns_current_user_rows`:
  `2 passed, 2 warnings`.
- AI billing route regression:
  `tests/test_ai_billing_routes.py`: `18 passed, 2 warnings`.
- Architecture focused regression:
  `tests/test_architecture_boundaries.py::test_admin_ai_billing_payload_responses_live_outside_route_module`,
  `tests/test_architecture_boundaries.py::test_admin_ai_billing_report_queries_live_in_appcore_usage_log`,
  and `tests/test_architecture_boundaries.py::test_settings_ai_pricing_db_access_lives_in_appcore_settings`:
  `3 passed`.
- `python -m compileall appcore/usage_log.py web/routes/admin_ai_billing.py` passed.
- Route direct DB and SQL dependency scan for `web/routes/admin_ai_billing.py` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
