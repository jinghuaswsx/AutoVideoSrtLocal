# P1/P2 Acceptance Note - Prompt Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/prompt.py`.
- Moved user prompt default seeding and default content sync into `appcore.prompt_library.ensure_user_prompt_defaults`.
- Moved user prompt list/create/get/update/delete DB access into `appcore.prompt_library`.
- Kept the route responsible for login gating, request JSON parsing, field trimming, not-found/default-delete decisions, and response shaping only.
- Preserved the existing SQL and response semantics to reduce behavior risk.

Verification:

- RED was confirmed first against the missing appcore user prompt DAO functions and the remaining route-level DB import.
- GREEN focused tests:
  `tests/test_prompt_routes.py`, `tests/test_prompt_library.py`, `tests/test_task_prompts_service.py`,
  and `tests/test_architecture_boundaries.py::test_prompt_route_db_access_lives_in_appcore_prompt_library`:
  `21 passed, 2 warnings`.
- Prompt route/service regression:
  `tests/test_prompt_routes.py`, `tests/test_prompt_library.py`, `tests/test_prompt_response_service.py`,
  and `tests/test_task_prompts_service.py`: `23 passed, 2 warnings`.
- Full architecture boundary regression: `tests/test_architecture_boundaries.py`: `185 passed, 1 warning`.
- Wider focused no-db regression including both prompt architecture tests: `25 passed, 2 warnings`.
- Combined prompt + architecture no-db regression: `208 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
