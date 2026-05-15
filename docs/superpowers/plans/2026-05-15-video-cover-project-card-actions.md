# Video Cover Project Card Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update `/video-cover` project cards to use 180x270 first-frame covers with copy/delete actions.

**Architecture:** Keep the change scoped to the existing video cover route, store, template, and tests. The route owns create/duplicate/delete behavior, the store owns project queries and soft delete SQL, and the template mirrors the multi-translate card/menu interaction without extracting a shared component.

**Tech Stack:** Python 3.12, Flask, Jinja, pytest, existing `pipeline.ffutil.extract_thumbnail`, existing project cleanup service.

---

### Task 1: Store Query and Soft Delete

**Files:**
- Modify: `appcore/video_cover_project_store.py`
- Test: `tests/test_video_cover_project_store.py`

- [x] Add a failing test showing `list_projects(..., owner_name_expr="COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)")` uses that expression as `creator_name`.
- [x] Add a failing test showing `soft_delete_project("task-1", user_id=7, is_admin=True)` omits `user_id` from the SQL scope.
- [x] Implement optional `owner_name_expr` on `list_projects`.
- [x] Implement `soft_delete_project`.
- [x] Run `pytest tests/test_video_cover_project_store.py -q`.

### Task 2: Card Template and Menu

**Files:**
- Modify: `web/templates/video_cover_list.html`
- Test: `tests/test_video_cover_generation.py`

- [x] Extend `test_video_cover_page_renders_project_list_for_admin` to assert `180x270` cover CSS, `/api/tasks/<id>/thumbnail`, all-white empty cover markup, creator/time/status footer, and `复制项目` / `删除项目` menu actions.
- [x] Update the template CSS and markup to render a `180px` wide card with a `180x270` cover region.
- [x] Add JS functions `toggleProjectMenu`, `deleteProject`, and `duplicateProject`, all using the CSRF meta token.
- [x] Run `pytest tests/test_video_cover_generation.py::test_video_cover_page_renders_project_list_for_admin -q`.

### Task 3: Create Thumbnail Behavior

**Files:**
- Modify: `web/routes/video_cover.py`
- Test: `tests/test_video_cover_generation.py`

- [x] Extend create-route tests to assert the thumbnail extractor receives the `180x270` crop filter.
- [x] Add a failing create-route test where thumbnail extraction raises and the project still persists with empty `thumbnail_path`.
- [x] Add a small route helper for extracting a card thumbnail using the fixed filter and empty-string fallback.
- [x] Run the two create-route tests with `pytest`.

### Task 4: Delete and Duplicate Routes

**Files:**
- Modify: `web/routes/video_cover.py`
- Modify: `appcore/video_cover_project_store.py`
- Test: `tests/test_video_cover_generation.py`

- [x] Add a failing delete-route test that verifies cleanup and soft delete are called for a visible project.
- [x] Add a failing duplicate-route test that verifies source video/product image are copied, initial state is rebuilt, and background processing starts.
- [x] Implement `DELETE /video-cover/api/<task_id>`.
- [x] Implement `POST /video-cover/api/<task_id>/duplicate`.
- [x] Run route tests with `pytest`.

### Task 5: Final Verification

**Files:**
- Verify only.

- [x] Run `pytest tests/test_video_cover_project_store.py tests/test_video_cover_generation.py -q`.
- [x] Run `python3 -m compileall web/routes/video_cover.py appcore/video_cover_project_store.py`.
- [x] Review `git diff --check`.
