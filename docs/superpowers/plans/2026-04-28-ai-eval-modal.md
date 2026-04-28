# AI Eval Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking "AI评估" on the materials page opens a request-status modal with a live timer, then shows structured results or a clear failure message.

**Architecture:** Keep the existing synchronous `/medias/api/products/<id>/evaluate` endpoint. Add a small modal controller in `web/static/medias.js` that reuses `window.EvalCountryTable.render()` for success output and owns timer, timeout, and failure text. Add asset-level tests that lock the new modal contract and timeout behavior.

**Tech Stack:** Vanilla JavaScript, existing Ocean Blue CSS tokens, pytest asset assertions.

---

### Task 1: Asset Test For Modal Contract

**Files:**
- Modify: `tests/test_medias_translation_assets.py`
- Modify: `web/static/medias.js`

- [ ] **Step 1: Write the failing test**

Add a test asserting that `medias.js` contains a dedicated AI evaluation modal controller, a 5 minute timeout, live elapsed seconds, "正在请求中", "本次评估失败", "服务器没有返回", and reuse of `EvalCountryTable.render`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_medias_translation_assets.py::test_medias_js_ai_evaluation_modal_shows_timer_result_and_timeout -q`

Expected: fail because the modal functions and timeout constant do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add modal helper functions near existing AI detail helpers and update `triggerAiEvaluate` to open the modal immediately, update it on success, and show failure on timeout or fetch errors.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_medias_translation_assets.py::test_medias_js_ai_evaluation_modal_shows_timer_result_and_timeout tests/test_pushes_ui_assets.py::test_pushes_and_medias_use_shared_ai_evaluation_detail_modal -q`

Expected: pass.

### Task 2: Route Regression

**Files:**
- Existing route remains: `web/routes/medias.py`
- Existing test remains: `tests/test_medias_routes.py`

- [ ] **Step 1: Run existing route tests**

Run: `pytest tests/test_medias_routes.py::test_manual_ai_evaluate_returns_llm_error_to_frontend tests/test_medias_routes.py::test_manual_ai_evaluate_runs_synchronously_on_click -q`

Expected: pass, proving frontend can still receive success and failure payloads.

### Task 3: Final Verification

**Files:**
- Verify changed assets and related tests.

- [ ] **Step 1: Run full focused verification**

Run: `pytest tests/test_medias_translation_assets.py tests/test_pushes_ui_assets.py tests/test_medias_routes.py::test_manual_ai_evaluate_returns_llm_error_to_frontend tests/test_medias_routes.py::test_manual_ai_evaluate_runs_synchronously_on_click -q`

Expected: pass.
