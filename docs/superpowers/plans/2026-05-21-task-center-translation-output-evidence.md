# Task Center Translation Output Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show task-center translation output evidence directly inside each child readiness row.

**Architecture:** Keep readiness calculation in `appcore.tasks` and add structured `evidence` arrays to existing `checks`. Render those evidence items in `web/templates/tasks_list.html` using the same authenticated media routes already used by review assets.

**Tech Stack:** Python 3.12, Flask routes, server-rendered Jinja template with inline JavaScript, pytest.

---

### Task 1: Backend Evidence Contract

**Files:**
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `appcore/tasks.py`

- [ ] **Step 1: Write the failing test**

Add assertions to `test_get_child_readiness_computes_payload` that expect `checks[].evidence` for video, cover, detail images, Shopify image status, and product links.

- [ ] **Step 2: Run the focused test**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_get_child_readiness_computes_payload -q`

Expected: FAIL because the current payload has no `evidence` field.

- [ ] **Step 3: Implement evidence builders**

Add small helpers in `appcore/tasks.py` for `link`, `video`, `image`, `text`, and `status` evidence. Reuse `_review_media_object_url`, `/medias/item-cover/<id>`, and `/medias/detail-image/<id>`.

- [ ] **Step 4: Verify backend**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_get_child_readiness_computes_payload -q`

Expected: PASS.

### Task 2: Frontend Evidence Renderer

**Files:**
- Modify: `tests/test_task_center_closure_assets.py`
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Write the failing template test**

Assert the template contains `function tcRenderReadinessEvidence`, `<video class="tc-readiness-video"`, `tc-readiness-image`, and link rendering for evidence.

- [ ] **Step 2: Run the focused template test**

Run: `pytest tests/test_task_center_closure_assets.py::test_task_center_timeline_renders_review_assets_in_steps -q`

Expected: FAIL because the renderer does not exist.

- [ ] **Step 3: Implement renderer**

Add CSS classes and JavaScript functions that render each evidence type below the corresponding readiness row.

- [ ] **Step 4: Verify frontend**

Run: `pytest tests/test_task_center_closure_assets.py::test_task_center_timeline_renders_review_assets_in_steps -q`

Expected: PASS.

### Task 3: Final Verification

**Files:**
- Verify: `appcore/tasks.py`
- Verify: `web/templates/tasks_list.html`
- Verify: `web/routes/tasks.py`

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_get_child_readiness_computes_payload tests/test_task_center_closure_assets.py::test_task_center_timeline_renders_review_assets_in_steps -q`

- [ ] **Step 2: Compile touched Python modules**

Run: `python -m compileall appcore/tasks.py web/routes/tasks.py`

- [ ] **Step 3: Review diff**

Run: `git diff --check`

Expected: no whitespace errors.
