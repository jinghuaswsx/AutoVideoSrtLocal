# P1/P2 Acceptance Note - Medias Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/medias/__init__.py` and `web/routes/medias/_helpers.py`.
- Removed direct `appcore.db` imports from the media route facade and helper module.
- Added `appcore.media_route_store` as the narrow DB query adapter.
- Preserved the existing `db_query` monkeypatch seam on `web.routes.medias`.
- Kept MK selection schema detection, media helper behavior, and blueprint route registration unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB imports.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_medias_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Medias no-db regression:
  `tests/test_media_helpers.py tests/test_mk_selection_routes.py tests/characterization/test_medias_routes_baseline.py`:
  `25 passed, 2 warnings`.
- `python -m compileall web/routes/medias/__init__.py web/routes/medias/_helpers.py appcore/media_route_store.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
