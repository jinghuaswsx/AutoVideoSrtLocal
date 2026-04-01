# Start Preparation Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an explicit “preparing source video” progress experience before task processing starts, so users do not think the page is frozen and cannot trigger repeated starts.

**Architecture:** Add a lightweight pre-start state to task data and emit it during source materialization, then render a modal-style progress card in the shared task workbench. The frontend will lock the start button immediately, show phase-based progress text, and dismiss the overlay once the real pipeline begins or an error occurs.

**Tech Stack:** Flask, in-memory task state with DB sync, shared task workbench template/JS/CSS, pytest.

---

## Task 1: Cover Pre-Start State With Tests

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_appcore_task_state.py`

- [ ] Add a failing route test asserting `/api/tasks/<task_id>/start` returns a preparation payload when the source must be materialized from TOS.
- [ ] Add a failing route test asserting repeated start clicks while preparation is in progress are rejected or ignored cleanly.
- [ ] Add a failing template/JS test asserting the shared workbench includes a preparation overlay and start-button lock handling.
- [ ] Add a failing task-state test for the new preparation fields.

## Task 2: Add Backend Preparation State

**Files:**
- Modify: `appcore/task_state.py`
- Modify: `web/routes/task.py`

- [ ] Add task-state fields for preparation status, message, and phase.
- [ ] Update the start route to mark preparation before TOS materialization begins.
- [ ] Clear preparation state once local materialization completes and the background pipeline starts.
- [ ] Return an explicit JSON payload that the frontend can use immediately after clicking start.
- [ ] Prevent duplicate start requests from starting the same task repeatedly while preparation is already underway.

## Task 3: Render Preparation Overlay In The Shared Workbench

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] Add a modal/overlay shell that visually matches the existing upload progress treatment.
- [ ] Show stage-based copy for:
  - validating configuration
  - preparing the source video from TOS
  - handing off to the pipeline
- [ ] Lock the start button immediately on click and ignore repeated clicks while preparing.
- [ ] Dismiss the overlay when the task transitions into a real pipeline step or shows an error.

## Task 4: Verify And Publish

**Files:**
- No new code files

- [ ] Run targeted tests for task state, task routes, and shared workbench rendering.
- [ ] Commit the feature branch changes.
- [ ] Push the branch and open a PR.
- [ ] Merge to `master`, deploy via `deploy/setup.sh`, and smoke-test the start flow on the server.
- [ ] Remove the local worktree/branch after deployment.
