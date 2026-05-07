# P1/P2 Acceptance Note - Task Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/task.py`.
- Removed direct `appcore.db` imports from the task route.
- Added `appcore.task_route_store` as the narrow DB dependency adapter for existing task route service callables.
- Preserved the existing `web.routes.task.db_query_one` and `web.routes.task.db_execute` monkeypatch seams used by route tests.
- Passed the existing cleanup dependencies explicitly for delete routes so current test and dependency injection behavior stays intact.

Verification:

- RED was confirmed first against the remaining route-level DB import and missing `appcore.task_route_store`.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_task_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Task service no-db regression:
  `tests/test_task_upload_service.py`, `tests/test_task_thumbnail_service.py`,
  `tests/test_task_deletion_service.py`, `tests/test_task_rename_service.py`,
  `tests/test_task_resume_service.py`, and `tests/test_task_analysis_service.py`:
  `23 passed, 1 warning`.
- Task route no-db regression:
  selected upload/delete/thumbnail/rename/resume route tests from `tests/test_web_routes.py`
  plus `tests/test_task_routes.py::test_av_resume_clears_stale_error_and_keeps_db_type_translation`:
  `10 passed, 2 warnings`.
- Task route regression:
  `tests/test_task_routes.py`: `9 passed, 2 warnings`.
- Full architecture boundary regression:
  `tests/test_architecture_boundaries.py`: `193 passed, 1 warning`.
- `python -m compileall appcore/task_route_store.py web/routes/task.py` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
