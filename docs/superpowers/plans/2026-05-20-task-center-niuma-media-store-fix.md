# Task Center Niuma Media Store Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix task-center Niuma auto-submit so it can read source videos stored in `local_media_storage`.

**Architecture:** Keep the task-center raw-video automation flow unchanged. Change only the local media path resolver in `appcore/task_raw_video_processing.py` so it uses the same `local_media_storage` first, `UPLOAD_DIR` fallback pattern already used by raw-source bridge and raw-video pool code.

**Tech Stack:** Python 3.12, pytest, existing task-center and subtitle-removal services.

---

### Task 1: Regression Test

**Files:**
- Modify: `tests/test_task_raw_video_processing.py`

- [x] **Step 1: Add a failing test**

Add `test_start_niuma_processing_resolves_local_media_storage_source()` that monkeypatches `processing.local_media_storage.exists()` and `processing.local_media_storage.safe_local_path_for()` so the source exists only through `local_media_storage`, then calls `start_niuma_processing_for_parent_task()`.

- [x] **Step 2: Run the focused test**

Run: `pytest tests/test_task_raw_video_processing.py::test_start_niuma_processing_resolves_local_media_storage_source -q`

Expected before implementation: failure because `processing.local_media_storage` is not imported or the resolver ignores it.

### Task 2: Resolver Fix

**Files:**
- Modify: `appcore/task_raw_video_processing.py`
- Test: `tests/test_task_raw_video_processing.py`

- [x] **Step 1: Implement minimal resolver change**

Import `local_media_storage` and update `_resolve_media_item_path(object_key)` to:

```python
try:
    if local_media_storage.exists(object_key):
        return local_media_storage.safe_local_path_for(object_key)
except Exception:
    pass
upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
return Path(upload_dir) / str(object_key or "")
```

- [x] **Step 2: Run focused tests**

Run: `pytest tests/test_task_raw_video_processing.py -q`

Expected after implementation: all tests in the file pass.

- [x] **Step 3: Compile the changed module**

Run: `python3 -m compileall appcore/task_raw_video_processing.py`

Expected: command exits 0.
