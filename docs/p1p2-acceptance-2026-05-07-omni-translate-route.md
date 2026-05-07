# P1/P2 Acceptance Note - Omni Translate Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/omni_translate.py`.
- Removed the direct `appcore.db` import from the omni translation route.
- Reused shared `appcore.translation_route_store` as the narrow DB dependency adapter.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on the route module.
- Kept list/detail, upload/start, source-language correction, resume, LLM debug, and artifact behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_omni_translate_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Omni translate route regression:
  `tests/test_omni_translate_routes.py`:
  `17 passed, 2 warnings`.
- `python -m compileall web/routes/omni_translate.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
