# Task Center Raw Source Automation Implementation Plan

**Goal:** Close the task-center loop from Mingkong material selection to raw-video processing, manual review, product raw source storage, and downstream push readiness.

**Architecture:** Keep the selected English material as the parent task source. When a raw-video processor claims the parent task, automatically submit it to Niuma subtitle removal and poll for up to 10 minutes. The processed result still requires manual review; only approval creates or updates the product raw source. If the selected material has no product in the local library, create the product with `Product Code + "-RJC"` and block the flow unless the canonical Shopify product link is reachable.

**Tech Stack:** Python 3.12, Flask routes/templates, existing `tasks`, `raw_video_pool`, `medias`, `object_keys`, and subtitle-removal services.

## Task 1: Raw Source Bridge

**Files:**
- Create: `appcore/task_raw_source_bridge.py`
- Test: `tests/test_task_raw_source_bridge.py`

- [x] Add `ensure_raw_source_for_parent_task(task_id, actor_user_id)` that loads the parent task and English media item.
- [x] Copy the reviewed video into the product raw-source object key while preserving the English material filename.
- [x] Reuse the media cover or extract a first-frame cover fallback.
- [x] Upsert `media_raw_sources` for the same product and display filename.
- [x] Unit-test create, update, and missing-media-item failures.

## Task 2: Review Approval Gate

**Files:**
- Modify: `appcore/tasks.py`
- Test: `tests/test_task_raw_source_bridge.py`

- [x] Call the raw-source bridge inside `approve_raw()` before children are unblocked.
- [x] Write `raw_source_created` or `raw_source_updated` task events.
- [x] Keep rejected processed videos out of `media_raw_sources`.

## Task 3: Niuma Raw Video Automation

**Files:**
- Create: `appcore/task_raw_video_processing.py`
- Modify: `appcore/scheduled_tasks.py`
- Modify: `web/routes/tasks.py`
- Test: `tests/test_task_raw_video_processing.py`

- [x] Start a Niuma subtitle-removal task automatically when a processor claims a parent raw task.
- [x] Poll for up to 10 minutes and write `raw_niuma_submitted`, `raw_niuma_done`, `raw_niuma_failed`, or `raw_niuma_timeout` events.
- [x] Replace the parent task media file with the Niuma result and move the parent task to manual raw review.
- [x] Register the in-process watcher in the scheduled task inventory.

## Task 4: Manual Replacement And Pool Status

**Files:**
- Modify: `appcore/raw_video_pool.py`
- Modify: `web/templates/raw_video_pool_list.html`
- Test: `tests/test_raw_video_pool_service_unit.py`

- [x] Record `raw_manual_uploaded` with filename and size when a processor manually replaces the processed video.
- [x] Return `raw_source_status` as `not_ready`, `ready`, or `missing_media`.
- [x] Return `raw_processing_status` for Niuma and manual-upload progress.
- [x] Show raw processing status and raw-source status in the raw video pool.

## Task 5: Product Creation And Shopify Link Gate

**Files:**
- Modify: `appcore/mk_import.py`
- Modify: `web/routes/mk_import.py`
- Modify: `web/routes/tasks.py`
- Modify: `web/services/mk_import.py`
- Tests: `tests/test_appcore_mk_import.py`, `tests/test_mk_import_response_service.py`, `tests/test_mk_import_routes.py`, `tests/test_tasks_routes.py`

- [x] Create missing products with `Product Code + "-RJC"` and a canonical Shopify product link.
- [x] Probe the product link before product creation or task creation continues.
- [x] Block the flow with `product_link_unavailable` when Shopify has not published the URL yet.

## Task 6: Verification

- [x] `pytest tests/test_task_raw_video_processing.py tests/test_task_raw_source_bridge.py tests/test_raw_video_pool_service_unit.py -q`
- [x] `pytest tests/test_mk_import_response_service.py tests/test_mk_import_routes.py tests/test_tasks_routes.py::test_import_and_create_maps_product_link_unavailable tests/test_key_action_audit.py::test_task_claim_records_audit -q`
- [x] `pytest tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_server_and_app_timers -q`
- [x] `pytest tests/test_appcore_mk_import.py::test_normalize_strips_rjc_suffix tests/test_appcore_mk_import.py::test_normalize_no_suffix tests/test_appcore_mk_import.py::test_normalize_mixed_case_rjc tests/test_appcore_mk_import.py::test_normalize_empty_returns_empty tests/test_appcore_mk_import.py::test_create_product_payload_uses_rjc_product_code_and_link tests/test_appcore_mk_import.py::test_product_link_precheck_blocks_unavailable_link tests/test_appcore_mk_import.py::test_import_mk_video_checks_product_link_before_download tests/test_appcore_mk_import.py::test_exception_classes_exist tests/test_appcore_mk_import.py::test_list_imported_filenames_queries_media_items tests/test_appcore_mk_import.py::test_list_imported_filenames_returns_empty_without_db_for_empty_input tests/test_appcore_mk_import.py::test_download_mp4_streams_to_path tests/test_appcore_mk_import.py::test_download_mp4_404_raises -q`
- [x] `python -m compileall appcore/task_raw_source_bridge.py appcore/task_raw_video_processing.py appcore/tasks.py appcore/raw_video_pool.py appcore/mk_import.py appcore/scheduled_tasks.py web/routes/tasks.py web/routes/mk_import.py web/services/mk_import.py`

Do not run any check that connects to local Windows MySQL (`127.0.0.1:3306`).
