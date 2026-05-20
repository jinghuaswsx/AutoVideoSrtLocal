# Omni State Hydration Race Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Omni detail polling from erasing active in-memory task fields and let compose recover from already generated subtitle/audio preview paths.

**Architecture:** Keep the route cache policy narrow: DB rows authorize access and hydrate cold/terminal tasks, but active in-process tasks remain authoritative. Add a small compose preflight helper that resolves required variant media paths from variant, top-level, and preview state before invoking `compose_video`.

**Tech Stack:** Python 3.12, Flask route tests, pytest, existing `appcore.task_state`.

---

### Task 1: Route Hydration Guard

**Files:**
- Modify: `web/routes/omni_translate.py`
- Test: `tests/test_omni_translate_routes.py`

- [ ] **Step 1: Write the failing test**

Add a test that seeds `task_state._tasks["task-hydrate-race"]` with `status="running"` and `variants.av.srt_path`, patches `_query_viewable_project` to return a stale DB state without that field, calls `GET /api/omni-translate/task-hydrate-race`, and asserts the response still contains the local `variants.av.srt_path`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_omni_translate_routes.py::test_omni_task_get_keeps_active_memory_state_over_stale_db_hydrate -q`

Expected: FAIL because `_get_viewable_task` currently returns the stale DB snapshot.

- [ ] **Step 3: Implement the guard**

Add a helper that treats tasks with `status` not in terminal states or any step in `running`, `queued`, or `waiting` as active. In `_get_viewable_task`, after the DB row confirms visibility, return the local active task instead of hydrating it with the DB snapshot.

- [ ] **Step 4: Run the route test**

Run: `pytest tests/test_omni_translate_routes.py::test_omni_task_get_keeps_active_memory_state_over_stale_db_hydrate -q`

Expected: PASS.

### Task 2: Compose Variant Media Preflight

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Test: `tests/test_runtime_omni_dispatch.py`

- [ ] **Step 1: Write the failing test**

Add a test that creates an Omni-like task whose selected `av` variant has `tts_audio_path` but no `srt_path`, while `preview_files.srt` points at an existing SRT. Patch `compose_video` and run `_step_compose`; assert the patched compose call receives the SRT path and task state persists `variants.av.srt_path`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_runtime_omni_dispatch.py::test_compose_recovers_av_srt_path_from_preview_file -q`

Expected: FAIL with `KeyError: 'srt_path'`.

- [ ] **Step 3: Implement the preflight helper**

Add a private helper near `_step_compose` that receives `task`, `variant`, `variants`, and `variant_state`, resolves `tts_audio_path` and `srt_path`, updates the variant state when it repairs a field, and raises `RuntimeError` with clear text when a required path is still missing.

- [ ] **Step 4: Run the compose test**

Run: `pytest tests/test_runtime_omni_dispatch.py::test_compose_recovers_av_srt_path_from_preview_file -q`

Expected: PASS.

### Task 3: Focused Regression Suite

**Files:**
- Verify only.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_omni_translate_routes.py tests/test_runtime_omni_dispatch.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Compile touched modules**

Run: `python3 -m compileall web/routes/omni_translate.py appcore/runtime/_pipeline_runner.py`

Expected: exit code 0.
