# P1/P2 Acceptance Note - Multi Translate Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/multi_translate.py`.
- Removed the direct `appcore.db` import from the multi-language translation route.
- Reused shared `appcore.translation_route_store` as the narrow DB dependency adapter.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on the route module.
- Kept list/detail, upload/start, voice selection, subtitle preview, artifact download, task deletion, and resume behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_multi_translate_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Multi translate route regression:
  `tests/test_multi_translate_routes.py`:
  `62 passed, 2 warnings`.
- `python -m compileall web/routes/multi_translate.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
