# P1/P2 Acceptance Note - Video Review Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/video_review.py`.
- Removed the direct `appcore.db` import from the video review route.
- Added `appcore.video_review_route_store` as the narrow DB dependency adapter.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on the route module.
- Kept list/detail, upload validation, review start, duplicate active-task rejection, prompt APIs, delete flow, and video artifact serving behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_video_review_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Video review route and response regression:
  `tests/test_video_review_routes.py tests/test_video_review_response_service.py tests/test_security_upload_validation.py tests/test_web_service_tuning.py::test_video_review_start_review_uses_background_helper tests/test_web_service_tuning.py::test_video_review_start_review_rejects_duplicate_active_task`:
  `49 passed, 2 warnings`.
- `python -m compileall web/routes/video_review.py appcore/video_review_route_store.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
