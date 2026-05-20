# Task Center Raw Niuma Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Niuma raw-video processing visible and require the task processor to review and accept the result before it is stored as the product raw-source video.

**Architecture:** Keep `task_events` as the status source. Change the watcher to write a result-ready event instead of replacing the English media item, then add explicit download and accept actions. Accepting a result creates or updates `media_raw_sources` with the same display filename as the English source video and never overwrites `media_items.object_key`.

**Tech Stack:** Python 3.12, Flask, pytest, existing `appcore.task_raw_video_processing`, `appcore.raw_video_pool`, and `web/routes/raw_video_pool.py`.

---

### Task 1: Preserve Niuma Results For Review

**Files:**
- Modify: `appcore/task_raw_video_processing.py`
- Test: `tests/test_task_raw_video_processing.py`

- [ ] Write a failing test showing `watch_niuma_processing()` writes `raw_niuma_result_ready` and does not call `mark_uploaded`.
- [ ] Implement `record_niuma_result_ready_for_parent_task()`.
- [ ] Change watcher `done` handling to call the ready recorder.
- [ ] Run `pytest tests/test_task_raw_video_processing.py -q`.

### Task 2: Accept Reviewed Niuma Result As Raw Source

**Files:**
- Modify: `appcore/task_raw_video_processing.py`
- Test: `tests/test_task_raw_video_processing.py`

- [ ] Write a failing test for `accept_niuma_result_for_parent_task()` creating/updating the product raw source from the latest ready result without writing to the parent English media item.
- [ ] Implement latest ready event lookup and accept flow through `task_raw_source_bridge`.
- [ ] Run `pytest tests/test_task_raw_video_processing.py -q`.

### Task 3: Raw Pool Projection And Result Download

**Files:**
- Modify: `appcore/raw_video_pool.py`
- Test: `tests/test_raw_video_pool_service_unit.py`

- [ ] Write failing tests for `niuma_result_ready`, submitted time, completed time, and failure detail projection.
- [ ] Add `stream_niuma_result_video()` and `accept_niuma_result()` service helpers.
- [ ] Run `pytest tests/test_raw_video_pool_service_unit.py -q`.

### Task 4: Flask Routes

**Files:**
- Modify: `web/routes/raw_video_pool.py`
- Modify: `web/services/raw_video_pool.py`
- Test: `tests/test_raw_video_pool_routes.py`

- [ ] Add `GET /raw-video-pool/api/task/<tid>/niuma-result/download`.
- [ ] Add `POST /raw-video-pool/api/task/<tid>/niuma-result/accept`.
- [ ] Run `pytest tests/test_raw_video_pool_routes.py -q`.

### Task 5: Page Actions

**Files:**
- Modify: `web/templates/raw_video_pool_list.html`
- Test: `tests/test_raw_video_pool_service_unit.py` or `tests/test_web_routes.py`

- [ ] Show progress detail text under the processing status.
- [ ] Show download/accept buttons only when `raw_processing_status === "niuma_result_ready"`.
- [ ] Keep manual upload available for in-progress tasks.
- [ ] Run route/template tests.

### Task 6: Verification

- [ ] Run `pytest tests/test_task_raw_video_processing.py tests/test_raw_video_pool_service_unit.py tests/test_raw_video_pool_routes.py -q`.
- [ ] Run `python -m compileall appcore/task_raw_video_processing.py appcore/raw_video_pool.py web/routes/raw_video_pool.py web/services/raw_video_pool.py`.
- [ ] Run `git diff --check`.
