# Mingkong Import Progress Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clear modal progress experience for Mingkong `加入素材库`.

**Architecture:** Keep the current synchronous import API. Add a client-side modal state machine in `mk_selection.html` that visualizes preparation, server import, success, warnings, errors, and next actions without changing backend business behavior.

**Tech Stack:** Flask/Jinja template, vanilla JavaScript, pytest static template checks.

---

### Task 1: Template Coverage

**Files:**
- Test: `tests/test_mk_selection_routes.py`

- [x] Add a static test that reads `web/templates/mk_selection.html`.
- [x] Assert the template contains `mkiImportProgressModal`, `mkiImportProgressOpen`, the five step labels, `继续做小语种任务`, `去任务中心`, `去素材管理`, and the error element `mkiImportProgressError`.
- [x] Run `pytest tests/test_mk_selection_routes.py::test_mk_import_progress_modal_present -q` and confirm it fails before implementation.

### Task 2: Modal UI And State Helpers

**Files:**
- Modify: `web/templates/mk_selection.html`

- [x] Add modal CSS for a compact workflow dialog, step statuses, error panel, and success action row.
- [x] Add modal HTML after the existing translator modals.
- [x] Add JavaScript helpers:
  - `mkiImportProgressOpen(meta)`
  - `mkiImportProgressSetStep(stepKey, status, detail)`
  - `mkiImportProgressFail(message)`
  - `mkiImportProgressComplete(data, btn)`
  - `mkiImportProgressClose()`
  - `mkiImportProgressContinueTask()`
- [x] Wire `mkiHandleClick(btn)` to open/update the modal while preserving the existing API call and button state behavior.

### Task 3: Verification

**Files:**
- Test: `tests/test_mk_selection_routes.py`

- [x] Run `pytest tests/test_mk_selection_routes.py::test_mk_import_progress_modal_present -q`.
- [x] Run targeted existing template tests: `pytest tests/test_mk_selection_routes.py::test_mk_import_progress_modal_present tests/test_mk_selection_routes.py::test_mk_selection_import_success_warnings_are_toasted tests/test_mk_selection_routes.py::test_mk_selection_video_cards_use_single_preview_with_metrics tests/test_mk_selection_routes.py::test_mk_selection_video_cards_include_local_video_preview tests/test_mk_selection_routes.py::test_mk_selection_video_cards_include_cached_ad_status_icons_and_media_search_link tests/test_xuanpin_routes.py::test_xuanpin_mk_video_cards_use_backend_material_status_for_import_button -q`.
- [x] Run `python -m compileall appcore/mk_import.py`.
- [x] Run JS parse check: `node -e "... new Function(script) ..."`.
