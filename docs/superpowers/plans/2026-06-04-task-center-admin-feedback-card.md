# Task Center Admin Feedback Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make administrator rejection feedback immediately visible in task details with a red bordered card, large screenshots, and an in-page full-size image modal.

**Architecture:** Keep the change in the existing task-center template. Reuse current `task.last_reason`, task events, and `push_rework_rejected.image_urls`; add small pure frontend helpers to extract the latest feedback and render the card/modal. No backend contract or database change.

**Tech Stack:** Flask/Jinja template, vanilla JavaScript, pytest template assertions.

---

### Task 1: Template Regression Test

**Files:**
- Modify: `tests/test_tasks_routes.py`
- Verify: `pytest tests/test_tasks_routes.py::test_task_detail_admin_feedback_card_highlights_rejection_with_modal -q`

- [ ] **Step 1: Write the failing test**

```python
def test_task_detail_admin_feedback_card_highlights_rejection_with_modal(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert ".tc-admin-feedback-card" in body
    assert "function tcLatestAdminFeedback" in body
    assert "function tcRenderAdminFeedbackCard" in body
    assert "管理员反馈" in body
    assert "push_rework_rejected" in body
    assert "payload.image_urls" in body
    assert "tcOpenFeedbackImageModal" in body
    assert "tcFeedbackImageModal" in body
    assert "keydown" in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tasks_routes.py::test_task_detail_admin_feedback_card_highlights_rejection_with_modal -q`

Expected: FAIL because the card/modal helpers are not present.

### Task 2: Feedback Card And Modal UI

**Files:**
- Modify: `web/templates/tasks_list.html`
- Verify: `pytest tests/test_tasks_routes.py::test_task_detail_admin_feedback_card_highlights_rejection_with_modal -q`

- [ ] **Step 1: Add focused CSS**

Add classes for `.tc-admin-feedback-card`, `.tc-admin-feedback-images`, `.tc-admin-feedback-image-btn`, `.tc-feedback-image-modal`, and `.tc-feedback-image-dialog`. Use existing `--tc-danger`, `--tc-danger-bg`, `--tc-border`, and `--tc-bg` tokens.

- [ ] **Step 2: Add modal markup**

Add one modal near existing task-center modals:

```html
<div id="tcFeedbackImageModal" class="tc-feedback-image-modal" aria-hidden="true">
  <div class="tc-feedback-image-dialog" role="dialog" aria-modal="true" aria-label="管理员反馈截图">
    <button type="button" class="tc-feedback-image-close" onclick="tcCloseFeedbackImageModal()">关闭</button>
    <img id="tcFeedbackImageFull" class="tc-feedback-image-full" alt="管理员反馈截图">
  </div>
</div>
```

- [ ] **Step 3: Add extraction/render helpers**

Add `tcLatestAdminFeedback(task, events)`, `tcRenderAdminFeedbackCard(task, events)`, `tcOpenFeedbackImageModal(url, label)`, and `tcCloseFeedbackImageModal()`. The extractor scans events from newest to oldest and prefers `push_rework_rejected`, then `rejected`, then `task.last_reason` containing `管理员已拒绝`.

- [ ] **Step 4: Insert card into detail header**

In `tcRenderDetail(task, events, reviewAssets)`, compute `const adminFeedback = tcRenderAdminFeedbackCard(task, events);` and render it below the existing status/meta block. Replace the old plain `tc-detail-reason` line when feedback is present.

- [ ] **Step 5: Reuse modal in timeline screenshots**

Change `tcRenderEventTimeline()` screenshot anchors into buttons that call `tcOpenFeedbackImageModal(url, '打回截图')`.

- [ ] **Step 6: Run the focused test**

Run: `pytest tests/test_tasks_routes.py::test_task_detail_admin_feedback_card_highlights_rejection_with_modal -q`

Expected: PASS.

### Task 3: Focused Verification

**Files:**
- Verify: `tests/test_tasks_routes.py`
- Verify: `web/routes/tasks.py`

- [ ] **Step 1: Run route/template tests**

Run: `pytest tests/test_tasks_routes.py -q`

Expected: PASS.

- [ ] **Step 2: Compile Python route**

Run: `python3 -m compileall web/routes/tasks.py`

Expected: exit code 0.

- [ ] **Step 3: Review diff**

Run: `git diff -- docs/superpowers/specs/2026-06-04-task-center-admin-feedback-card-design.md docs/superpowers/plans/2026-06-04-task-center-admin-feedback-card.md tests/test_tasks_routes.py web/templates/tasks_list.html`

Expected: only docs, the focused test, and task-center template changed.
