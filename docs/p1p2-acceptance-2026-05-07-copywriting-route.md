# P1/P2 Acceptance Note - Copywriting Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/copywriting.py`.
- Removed the direct `appcore.db` import from the copywriting route.
- Added `appcore.copywriting_route_store` as the narrow connection adapter.
- Preserved the existing `get_connection` monkeypatch seam on the route module.
- Kept list/detail, upload, product input updates, preview, generate, segment rewrite, TTS, and artifact behavior unchanged.

Verification:

- RED was confirmed first against the remaining route-level DB import.
- GREEN focused architecture test:
  `tests/test_architecture_boundaries.py::test_copywriting_route_db_connection_uses_appcore_store`:
  `1 passed`.
- Copywriting route regression:
  `tests/test_web_service_tuning.py::test_copywriting_generate_uses_background_helper tests/test_web_service_tuning.py::test_copywriting_generate_rejects_duplicate_active_task tests/test_web_service_tuning.py::test_copywriting_tts_uses_active_guard tests/test_web_service_tuning.py::test_copywriting_tts_rejects_duplicate_active_task tests/test_security_upload_validation.py`:
  `47 passed, 2 warnings`.
- Copywriting architecture response regression:
  `tests/test_architecture_boundaries.py::test_copywriting_api_responses_live_outside_route_module tests/test_architecture_boundaries.py::test_copywriting_route_db_connection_uses_appcore_store`:
  `2 passed`.
- `python -m compileall web/routes/copywriting.py appcore/copywriting_route_store.py` passed.

Local MySQL:

- Not used. All local verification used static checks or monkeypatched no-db paths.
