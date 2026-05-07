# P1/P2 Acceptance Note - Translate Lab Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/translate_lab.py`.
- Removed direct `appcore.db` imports from the translate lab route.
- Added `appcore.translate_lab_store` as the narrow DB dependency adapter for existing list/detail/delete/deprecated-create support paths.
- Preserved the existing `web.routes.translate_lab.db_query`, `db_query_one`, and `db_execute` monkeypatch seams used by no-db route tests.
- Kept runtime, file streaming, admin sync/embed, and deprecated create endpoint behavior unchanged.
- Updated three stale route tests to match the current deprecated create endpoint contract: `POST /api/translate-lab` returns `410 Gone` before old validation/create logic.

Verification:

- RED was confirmed first against the remaining route-level DB import and missing `appcore.translate_lab_store`.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_translate_lab_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Translate lab route regression:
  `tests/test_translate_lab_routes.py`:
  `23 passed, 2 warnings`.
- Translate lab response/deprecation plus full architecture boundary regression:
  `tests/test_architecture_boundaries.py tests/test_translate_lab_response_service.py tests/test_translate_lab_deprecated_ui.py`:
  `208 passed, 2 warnings`.
- `python -m compileall appcore/translate_lab_store.py web/routes/translate_lab.py tests/test_translate_lab_routes.py` passed.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. All local tests for this change use static checks or monkeypatched no-db paths.
