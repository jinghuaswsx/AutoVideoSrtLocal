# P1/P2 Acceptance Note - Video Creation Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/video_creation.py`.
- Removed the direct `appcore.db` import from the video creation route.
- Added `appcore.video_creation_route_store` as the narrow DB dependency adapter.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on the route module.
- Kept list/detail, upload validation, background generation, regenerate active-task guard, asset deletion, and response behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_video_creation_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Video creation route regression:
  `tests/test_video_creation_routes.py tests/test_security_upload_validation.py tests/test_web_service_tuning.py::test_video_creation_upload_uses_background_helper tests/test_web_service_tuning.py::test_video_creation_regenerate_uses_active_guard tests/test_web_service_tuning.py::test_video_creation_regenerate_rejects_duplicate_active_task`:
  `47 passed, 2 warnings`.
- `python -m compileall web/routes/video_creation.py appcore/video_creation_route_store.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
