# AI Eval Observability Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the materials-page AI evaluation modal into a two-tab observability panel that previews request inputs and renders structured model results.

**Architecture:** Add a debug payload builder to `appcore/material_evaluation.py` that reuses the same prompt, schema, cover, video, and product-link selection as the real evaluator. Expose a lightweight preview endpoint and a full request endpoint in `web/routes/medias.py`; the full endpoint includes complete base64 media data for copying. Update `web/static/medias.js` to render “请求报文” and “结果” tabs inside the existing `EvalCountryTable` modal shell.

**Tech Stack:** Flask routes, existing `appcore.material_evaluation`, vanilla JavaScript, pytest route and asset tests.

---

### Task 1: Backend Request Preview And Full Payload

**Files:**
- Modify: `appcore/material_evaluation.py`
- Modify: `web/routes/medias.py`
- Test: `tests/test_medias_routes.py`

- [ ] **Step 1: Write failing route tests**

Add tests for:
- `GET /medias/api/products/<pid>/evaluate/request-preview` returning product, cover, video, prompts, schema, and full-payload endpoint URL.
- `GET /medias/api/products/<pid>/evaluate/request-payload` returning full media payload entries with base64 fields.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/test_medias_routes.py::test_manual_ai_evaluate_request_preview_returns_observable_inputs tests/test_medias_routes.py::test_manual_ai_evaluate_request_payload_includes_full_base64 -q`

Expected: fail with 404 or missing helper.

- [ ] **Step 3: Implement helper and routes**

Implement `build_request_debug_payload(product_id, include_base64=False)` using real evaluator inputs. Add routes with access checks.

- [ ] **Step 4: Run backend focused tests**

Run the two tests above and expect pass.

### Task 2: Frontend Two-Tab Modal

**Files:**
- Modify: `web/static/medias.js`
- Test: `tests/test_medias_ai_evaluation_modal_assets.py`

- [ ] **Step 1: Write failing asset tests**

Assert the script has:
- request/result tab controls,
- top timer bar,
- request preview rendering for image/video/link/prompt/schema,
- full “请求报文” detail modal with one-click copy,
- result tab using `EvalCountryTable.render`.

- [ ] **Step 2: Run test and verify fail**

Run: `pytest tests/test_medias_ai_evaluation_modal_assets.py -q`

- [ ] **Step 3: Implement frontend**

Fetch preview immediately after modal opens, render request tab, keep result tab for loading/result/failure, fetch full payload only when the button is clicked.

- [ ] **Step 4: Run frontend focused tests and JS syntax check**

Run:
- `node --check web/static/medias.js`
- `pytest tests/test_medias_ai_evaluation_modal_assets.py -q`

### Task 3: End-To-End Focused Verification

**Files:**
- Verify changed files.

- [ ] **Step 1: Run focused verification**

Run:
`node --check web/static/medias.js`
`pytest tests/test_medias_ai_evaluation_modal_assets.py tests/test_medias_routes.py::test_manual_ai_evaluate_request_preview_returns_observable_inputs tests/test_medias_routes.py::test_manual_ai_evaluate_request_payload_includes_full_base64 tests/test_medias_routes.py::test_manual_ai_evaluate_returns_llm_error_to_frontend tests/test_medias_routes.py::test_manual_ai_evaluate_runs_synchronously_on_click -q`

Expected: pass.
