# P1/P2 Acceptance Note - JA Translate Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/ja_translate.py`.
- Removed direct `appcore.db` imports from the Japanese translation route.
- Reused shared `appcore.translation_route_store` as the narrow DB dependency adapter.
- Preserved the existing `db_query`, `db_query_one`, and `db_execute` monkeypatch seams on the route module.
- Kept upload/start, list/detail, voice selection, rematch, subtitle preview, artifacts, soft-delete, and analysis-not-supported behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_ja_translate_route_db_dependencies_use_appcore_store`:
  `1 passed`.
- JA translate route regression:
  `tests/test_ja_translate_routes.py`:
  `9 passed, 2 warnings`.
- Full architecture boundary regression:
  `tests/test_architecture_boundaries.py`:
  `198 passed, 1 warning`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
