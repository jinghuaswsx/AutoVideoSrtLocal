# P1/P2 Acceptance Note - Settings AI Pricing Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/settings.py` AI pricing endpoints.
- Moved AI model price listing, lookup, create, update, delete, and row serialization into `appcore.settings`.
- Kept the route responsible for superadmin gating, request validation, response wrapping, and pricing cache invalidation only.
- Preserved the existing update behavior: `provider` and `model` are validated in the payload but not changed by the update SQL.

Verification:

- RED was confirmed first against missing `appcore.settings` AI price store helpers and remaining route-level DB import.
- GREEN focused tests:
  `tests/test_settings.py::test_list_ai_model_prices_serializes_rows`,
  `tests/test_settings.py::test_create_ai_model_price_inserts_and_returns_created_row`,
  `tests/test_settings.py::test_update_ai_model_price_updates_price_fields_and_returns_row`,
  `tests/test_settings.py::test_delete_ai_model_price_deletes_by_id`,
  `tests/test_architecture_boundaries.py::test_settings_ai_pricing_db_access_lives_in_appcore_settings`,
  and `tests/test_ai_billing_routes.py::test_ai_pricing_post_creates_row_and_invalidates_cache`:
  `6 passed, 2 warnings`.
- Settings and AI pricing no-db regression:
  `tests/test_settings.py` plus all AI pricing route tests in `tests/test_ai_billing_routes.py`:
  `20 passed, 2 warnings`.
- Route/service regression:
  `tests/test_ai_billing_routes.py`: `18 passed, 2 warnings`.
- Settings page regression:
  `tests/test_settings_routes_new.py`: `27 passed, 2 warnings`.
- Architecture regression:
  `tests/test_architecture_boundaries.py::test_settings_ai_pricing_responses_live_outside_route_module`
  and `tests/test_architecture_boundaries.py::test_settings_ai_pricing_db_access_lives_in_appcore_settings`:
  `2 passed`.
- `python -m compileall appcore/settings.py web/routes/settings.py` passed.
- Route direct DB dependency scan for `web/routes/settings.py` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
