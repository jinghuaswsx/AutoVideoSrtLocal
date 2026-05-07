# P1/P2 Acceptance Note - Pushes Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/pushes.py`.
- Removed direct downstream `requests.post` calls from the pushes route.
- Added `appcore.pushes.post_json_payload` as the shared downstream JSON POST adapter.
- Kept route responsibilities limited to auth, request checks, audit, push log persistence, and Flask response wrapping.
- Preserved current success, downstream HTTP error, and network error response shapes.

Verification:

- RED confirmed first:
  - `tests/test_architecture_boundaries.py::test_pushes_route_downstream_http_lives_in_appcore_pushes` failed while `web/routes/pushes.py` still imported and called `requests`.
  - `tests/test_appcore_pushes.py::test_post_json_payload_success`
    and `tests/test_appcore_pushes.py::test_post_json_payload_network_error` failed before the appcore adapter existed.
- GREEN:
  - `python -m pytest tests\test_architecture_boundaries.py -q`:
    `216 passed, 1 warning`.
  - `python -m pytest tests\test_appcore_pushes.py::test_post_json_payload_success tests\test_appcore_pushes.py::test_post_json_payload_network_error tests\test_pushes_routes.py::test_push_route_delegates_downstream_post_to_appcore_helper_no_db tests\test_pushes_routes.py::test_push_localized_texts_route_delegates_downstream_post_to_appcore_helper_no_db tests\test_pushes_routes.py::test_push_product_links_from_pushes_modal_success tests\test_pushes_responses_service.py tests\test_pushes_ui_assets.py -q`:
    `19 passed, 2 warnings`.
  - `python -m compileall appcore\pushes.py web\routes\pushes.py tests\test_appcore_pushes.py tests\test_pushes_routes.py tests\test_architecture_boundaries.py -q` passed.

Local MySQL:

- Full DB-backed push route tests were not run locally because this Windows workspace defaults to `127.0.0.1:3306`, which is forbidden by project rules.
- A newly added no-db route test initially missed the `get_push_target_url` monkeypatch and attempted to read `system_settings`; this failed immediately against `127.0.0.1:3306`. The test was corrected to patch `web.routes.pushes.pushes.get_push_target_url` explicitly, and the no-db route checks then passed.
