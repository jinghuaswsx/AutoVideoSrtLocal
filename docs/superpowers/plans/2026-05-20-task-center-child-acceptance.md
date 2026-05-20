# Task Center Child Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task-center child tasks jump to the correct medias product search page and expose a complete acceptance gate for translated deliverables.

**Architecture:** Keep acceptance computation in `appcore.tasks`; routes continue delegating to the service layer. The frontend renders a structured acceptance panel from `/tasks/api/child/<id>/readiness` and builds medias URLs from `product_code + task/lang context`.

**Tech Stack:** Python 3.12, Flask, Jinja template JavaScript, pytest.

---

### Task 1: Service Acceptance Payload

**Files:**
- Modify: `appcore/tasks.py`
- Test: `tests/test_appcore_tasks_supporting_data.py`

- [ ] Add failing tests for `product_code`, `media_search_url`, detail image gate, link availability gate, and structured `checks`.
- [ ] Implement helper functions in `appcore.tasks` for medias search URL, detail image status, product link status, and acceptance check serialization.
- [ ] Keep existing `readiness` booleans for compatibility while adding `checks` and `missing`.

### Task 2: Submit Gate

**Files:**
- Modify: `appcore/tasks.py`
- Test: `tests/test_appcore_tasks.py`

- [ ] Add a failing test proving `submit_child()` blocks when a new acceptance item such as `detail_images` is missing.
- [ ] Route `submit_child()` through the same acceptance helper used by `get_child_readiness()`.

### Task 3: Frontend Jump And Panel

**Files:**
- Modify: `web/templates/tasks_list.html`
- Test: `tests/test_tasks_routes.py`

- [ ] Add a failing template test proving child translation links preserve `q`, `from_task`, and `lang`.
- [ ] Add `product_code` to task and artifact API payloads.
- [ ] Update task detail rendering to show structured acceptance checks and build medias links through one URL helper.

### Task 4: Verification

**Files:**
- No code files.

- [ ] Run `pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py::test_task_center_child_translate_jump_uses_product_code_search tests/test_tasks_routes.py::test_child_readiness_delegates_to_tasks_service tests/test_tasks_routes.py::test_child_readiness_maps_missing_child_to_404 tests/test_appcore_tasks.py::test_submit_child_fails_when_detail_images_not_ready -q`.
- [ ] Run `python3 -m compileall appcore/tasks.py web/routes/tasks.py`.
- [ ] Do not run DB fixture tests that connect to local MySQL.
