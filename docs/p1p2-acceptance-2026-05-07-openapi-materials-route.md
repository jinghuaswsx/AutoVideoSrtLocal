# P1/P2 Acceptance Note - OpenAPI Materials Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/openapi_materials.py`.
- Moved route-level material list and push-log query dependencies behind `appcore.openapi_materials`.
- Kept the route responsible for API-key gating, request parameter parsing, service delegation, and OpenAPI response wrapping only.
- Preserved existing OpenAPI materials, push-items list, single-item, and by-keys response behavior.

Verification:

- RED was confirmed first against the missing `openapi_materials_store` route dependency, missing appcore query wrappers, and remaining route-level `appcore.db` import.
- GREEN focused tests:
  `tests/test_openapi_materials_routes.py`,
  `tests/test_openapi_materials_listing_service.py`,
  and `tests/test_architecture_boundaries.py::test_openapi_materials_route_db_dependencies_use_appcore_store`:
  `30 passed, 2 warnings`.
- Combined OpenAPI materials/push-items/architecture no-db regression:
  `tests/test_openapi_materials_routes.py`, `tests/test_openapi_materials_listing_service.py`,
  `tests/test_openapi_materials_serializers.py`, `tests/test_openapi_push_items_service.py`,
  and `tests/test_architecture_boundaries.py`:
  `234 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.
- Route direct DB dependency scan for `web/routes/openapi_materials.py` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
