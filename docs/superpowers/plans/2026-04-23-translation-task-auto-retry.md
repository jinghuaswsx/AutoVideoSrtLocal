# Translation Task Auto Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the product translation task page trigger safe backend synchronization every 5 seconds, including exactly one system auto-retry for recoverable child task failures.

**Architecture:** Add a small coordinator in `appcore.bulk_translate_runtime` that inspects bulk translation parent tasks, polls child tasks, starts at most one automatic retry per parent plan item, and then lets the existing scheduler/backfill path complete normal result sync. The product task API invokes this coordinator before returning projections, while the page polls every 5 seconds only while work remains unfinished.

**Tech Stack:** Python, Flask, pytest, existing `projects.state_json`, existing image and multi-translate runners, vanilla JavaScript.

---

### Task 1: Backend Auto Retry Coordinator

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove an image child with failed items is reset once, stores `system_auto_retry_count == 1`, and does not reset again when the retry count is already 1.

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_bulk_translate_runtime.py::test_sync_retries_failed_image_child_once tests/test_bulk_translate_runtime.py::test_sync_does_not_auto_retry_image_child_twice -q`

Expected: fail because the coordinator function does not exist.

- [ ] **Step 3: Implement minimal coordinator**

Add `sync_task_with_children_once(task_id, user_id=None)` plus small helpers in `appcore.bulk_translate_runtime`. The helper must store retry metadata on the parent item, reset only failed image child items, set child status to `queued`, start the image runner, and never exceed one automatic retry.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_bulk_translate_runtime.py::test_sync_retries_failed_image_child_once tests/test_bulk_translate_runtime.py::test_sync_does_not_auto_retry_image_child_twice -q`

Expected: pass.

### Task 2: Video Child Resume Once

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 1: Write failing test**

Add a test that a `multi_translate` child which failed after voice selection is resumed once from the failed post-voice step and records `system_auto_retry_count == 1`.

- [ ] **Step 2: Run test to verify RED**

Run: `pytest tests/test_bulk_translate_runtime.py::test_sync_resumes_failed_video_child_after_voice_once -q`

Expected: fail until the video retry path exists.

- [ ] **Step 3: Implement minimal video retry**

Detect post-voice failed steps from `alignment`, `translate`, `tts`, `subtitle`, `compose`, `export`, reset that step and later steps to `pending`, set child status to `running`, and call a restart callback that defaults to `web.services.multi_pipeline_runner.resume`.

- [ ] **Step 4: Run test to verify GREEN**

Run: `pytest tests/test_bulk_translate_runtime.py::test_sync_resumes_failed_video_child_after_voice_once -q`

Expected: pass.

### Task 3: Product Task API Integration

**Files:**
- Modify: `appcore/bulk_translate_projection.py`
- Modify: `web/routes/medias.py`
- Test: `tests/test_medias_translation_tasks_routes.py`

- [ ] **Step 1: Write failing route test**

Assert `/medias/api/products/<pid>/translation-tasks` invokes the coordinator for the product task rows before returning projection data.

- [ ] **Step 2: Run test to verify RED**

Run: `pytest tests/test_medias_translation_tasks_routes.py::test_product_translation_tasks_api_syncs_before_projection -q`

Expected: fail because the route currently only calls `list_product_tasks`.

- [ ] **Step 3: Implement route integration**

Add a focused `list_product_task_ids(user_id, product_id)` helper in projection and have the route call `sync_task_with_children_once` for each ID before `list_product_tasks`.

- [ ] **Step 4: Run test to verify GREEN**

Run: `pytest tests/test_medias_translation_tasks_routes.py::test_product_translation_tasks_api_syncs_before_projection -q`

Expected: pass.

### Task 4: 5 Second Polling

**Files:**
- Modify: `web/static/medias_translation_tasks.js`
- Test: `tests/test_medias_translation_assets.py`

- [ ] **Step 1: Write failing asset test**

Assert the task page script uses a 5000 ms poll interval and contains terminal-status logic so polling can stop after all tasks finish.

- [ ] **Step 2: Run test to verify RED**

Run: `pytest tests/test_medias_translation_assets.py::test_medias_translation_tasks_polls_every_five_seconds_until_terminal -q`

Expected: fail because current intervals are 8000 and 12000 ms.

- [ ] **Step 3: Implement polling update**

Use one `POLL_INTERVAL_MS = 5000`, avoid overlapping refreshes, and clear the timer when every loaded task has terminal status.

- [ ] **Step 4: Run test to verify GREEN**

Run: `pytest tests/test_medias_translation_assets.py::test_medias_translation_tasks_polls_every_five_seconds_until_terminal -q`

Expected: pass.

### Task 5: Final Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused suite**

Run: `pytest tests/test_bulk_translate_runtime.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py -q`

Expected: pass.

- [ ] **Step 2: Inspect git diff**

Run: `git diff -- appcore/bulk_translate_runtime.py appcore/bulk_translate_projection.py web/routes/medias.py web/static/medias_translation_tasks.js tests/test_bulk_translate_runtime.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py`

Expected: retry count is persisted, retry cap is hard-coded to one, and no loop can re-trigger automatic retries beyond that cap.
