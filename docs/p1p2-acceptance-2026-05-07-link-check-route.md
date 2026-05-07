# P1/P2 Acceptance Note - Link Check Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/link_check.py`.
- Moved link-check project listing, project lookup, rename, and soft-delete persistence into `appcore.project_state`.
- Kept the route responsible for login gating, request validation, file cleanup orchestration, store cache updates, runner startup, and response wrapping only.
- Preserved global link-check task behavior; no user_id filter was added to these existing global project queries.

Verification:

- RED was confirmed first against the missing `project_store` route dependency, missing appcore project helpers, and remaining route-level DB import.
- GREEN focused tests:
  `tests/test_link_check_project_routes.py`, `tests/test_project_state.py`,
  and `tests/test_architecture_boundaries.py::test_link_check_project_db_access_lives_in_appcore_project_state`:
  `29 passed, 2 warnings`.
- Combined link-check/project-state/architecture no-db regression:
  `tests/test_link_check_project_routes.py`, `tests/test_link_check_routes.py`,
  `tests/test_link_check_response_service.py`, `tests/test_link_check_locale.py`,
  `tests/test_project_state.py`, and `tests/test_architecture_boundaries.py`:
  `237 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.
- Route direct DB dependency scan for `web/routes/link_check.py` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
