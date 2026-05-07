# P1/P2 Acceptance Note - DE/FR Translate Routes

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/de_translate.py` and `web/routes/fr_translate.py`.
- Removed direct `appcore.db` imports from both legacy translation route modules.
- Added shared `appcore.translation_route_store` as the narrow DB dependency adapter for route-level list/detail/delete/analysis DB access.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on both route modules.
- Kept local multipart upload, TOS-complete deprecation responses, task restart/start/resume, artifact access, and soft-delete behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB imports and missing `appcore.translation_route_store`.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_de_fr_translate_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- DE/FR focused no-db regression:
  `tests/test_tos_upload_routes.py tests/test_security_upload_validation.py`
  plus selected DE/FR detail tests from `tests/test_web_routes.py`:
  `51 passed, 2 warnings`.
- Full architecture boundary regression:
  `tests/test_architecture_boundaries.py`:
  `197 passed, 1 warning`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
