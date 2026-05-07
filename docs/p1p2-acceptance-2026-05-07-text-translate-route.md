# P1/P2 Acceptance Note - Text Translate Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/text_translate.py`.
- Removed direct `appcore.db` imports from the text translation route.
- Added `appcore.text_translate_store` as the narrow DB dependency adapter for existing list/detail/create/translate/delete route behavior.
- Preserved the existing `web.routes.text_translate.db_query`, `db_query_one`, and `db_execute` monkeypatch seams used by no-db route tests.
- Kept SQL, LLM invocation, project-state persistence, and HTTP response behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import and missing `appcore.text_translate_store`.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_text_translate_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- Text translate no-db route and response regression:
  `tests/test_text_translate_routes.py tests/test_text_translate_response_service.py`:
  `10 passed, 2 warnings`.
- Full architecture boundary regression:
  `tests/test_architecture_boundaries.py`:
  `194 passed, 1 warning`.
- `python -m compileall appcore/text_translate_store.py web/routes/text_translate.py` passed.

Local MySQL:

- Not used. All local tests for this change use static checks or monkeypatched no-db paths.
