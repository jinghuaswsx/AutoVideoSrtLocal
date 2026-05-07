# P1/P2 Acceptance Note - Image Translate Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/image_translate.py`.
- Removed direct `appcore.db` imports from the image translation route, including the list page's local query import.
- Added `appcore.image_translate_store` as the narrow DB dependency adapter for image translate route reads and soft-delete writes.
- Preserved the existing `web.routes.image_translate.db_query`, `db_query_one`, and `db_execute` monkeypatch seams used by no-db route tests.
- Kept upload bootstrap/complete, retry, artifact download, list/detail, runner dispatch, and delete behavior unchanged.
- Updated stale no-db tests to patch the route seam instead of `appcore.db.query`, preventing accidental Windows local MySQL access after the dependency boundary cleanup.

Verification:

- RED was confirmed first against the remaining route-level DB imports and missing `appcore.image_translate_store`.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_image_translate_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Image translate route/response regression:
  `tests/test_image_translate_routes.py tests/test_image_translate_response_routes.py tests/test_image_translate_response_service.py`:
  `64 passed, 2 warnings`.
- Full architecture boundary regression:
  `tests/test_architecture_boundaries.py`:
  `196 passed, 1 warning`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used for accepted verification. Two stale route tests initially attempted to reach `127.0.0.1:3306` because they patched `appcore.db.query`; those tests were corrected to patch `web.routes.image_translate.db_query`, then rerun successfully with no local DB dependency.
