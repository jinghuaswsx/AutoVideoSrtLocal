# P1/P2 Acceptance Note - Copywriting Translate Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/copywriting_translate.py`.
- Moved copywriting-translate project creation into `appcore.project_state.create_copywriting_translate_project`.
- Kept the route responsible for login gating, request validation, state assembly, active-task registration, background runner startup, and response shaping only.
- Preserved the existing project `INSERT` SQL, queued status, project type, and state payload.

Verification:

- RED was confirmed first against the missing `project_store` patch point, missing appcore project creation helper, and remaining route-level DB import.
- GREEN focused tests:
  `tests/test_copywriting_translate_routes.py`,
  `tests/test_project_state.py::test_create_copywriting_translate_project_inserts_queued_state`,
  and `tests/test_architecture_boundaries.py::test_copywriting_translate_project_create_lives_in_appcore_project_state`:
  `10 passed, 2 warnings`.
- Combined route/service/project-state/architecture no-db regression:
  `tests/test_copywriting_translate_routes.py`, `tests/test_copywriting_translate_service.py`,
  `tests/test_project_state.py`, and `tests/test_architecture_boundaries.py`:
  `209 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
