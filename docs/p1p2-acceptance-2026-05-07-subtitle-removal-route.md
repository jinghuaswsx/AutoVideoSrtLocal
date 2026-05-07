# P1/P2 Acceptance Note - Subtitle Removal Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/subtitle_removal.py`.
- Removed the direct `appcore.db` import from the subtitle removal route.
- Added `appcore.subtitle_removal_route_store` as the narrow DB dependency adapter.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on the route module.
- Kept upload bootstrap/complete, Volc and local VSR backend selection, submit/resubmit, resume-poll, list filters, delete, local result artifacts, and page rendering behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_subtitle_removal_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Subtitle removal route regression:
  `tests/test_subtitle_removal_routes.py tests/test_web_routes.py -k subtitle_removal`:
  `70 passed, 118 deselected, 2 warnings`.
- `python -m compileall web/routes/subtitle_removal.py appcore/subtitle_removal_route_store.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
