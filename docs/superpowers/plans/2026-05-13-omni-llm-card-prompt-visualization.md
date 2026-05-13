# Omni LLM Card Prompt Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every omni detail step card that performs an LLM call expose the actual prompt/messages and request payload through the existing front-end prompt inspector.

**Architecture:** Keep the multi-compatible `llm_debug_refs` contract. Pipeline functions return `_llm_debug_calls`; runtime/profile layers persist them with `save_llm_debug_calls`; `_task_workbench` renders buttons without a new UI system.

**Tech Stack:** Python 3.12, Flask task state, existing `appcore.llm_debug_payloads`, existing `_task_workbench` prompt inspector, pytest.

---

### Task 1: Pipeline Debug Payloads

**Files:**
- Modify: `pipeline/translate_v2.py`
- Modify: `pipeline/av_source_normalize.py`
- Modify: `pipeline/shot_notes.py`
- Modify: `pipeline/av_translate.py`
- Modify: `appcore/tts_language_guard.py`
- Test: `tests/test_shot_notes.py`
- Test: `tests/test_av_translate.py`
- Test: `tests/test_av_source_normalize.py`
- Test: `tests/test_tts_language_guard.py`

- [x] Add failing tests asserting returned `_llm_debug_calls` contain messages and request payloads.
- [x] Run focused tests and confirm they fail on missing `_llm_debug_calls`.
- [x] Add minimal debug payload construction using `prompt_file_payload()`, `build_chat_request_payload()`, and `build_generate_request_payload()`.
- [x] Run focused tests and confirm they pass.

### Task 2: Runtime Persistence

**Files:**
- Modify: `appcore/runtime_omni_steps.py`
- Modify: `appcore/translate_profiles/av_sync_profile.py`
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Modify: `pipeline/duration_reconcile.py`
- Test: `tests/test_runtime_omni_dispatch.py`
- Test: `tests/test_sentence_translate_runtime.py`

- [x] Add failing tests/assertions covering debug refs under the correct step.
- [x] Run focused tests and confirm failure where `_llm_debug_calls` were missing.
- [x] Persist returned `_llm_debug_calls` with `save_llm_debug_calls()` and strip private debug keys before saving business artifacts.
- [x] Run focused tests and confirm they pass.

### Task 3: Front-End Contract

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Test: `tests/test_prompt_inspector_assets.py`

- [x] Add/adjust asset tests so omni dynamic LLM steps remain in `STEP_ORDER` and use `currentTask.llm_debug_refs`.
- [x] Keep existing prompt inspector rendering; only add labels if a new step label is missing.
- [x] Run `tests/test_prompt_inspector_assets.py`.

### Task 4: Verification

**Commands:**
- `pytest tests/test_shot_notes.py tests/test_av_translate.py tests/test_av_source_normalize.py tests/test_tts_language_guard.py -q`
- `pytest tests/test_runtime_omni_dispatch.py tests/test_sentence_translate_runtime.py tests/test_prompt_inspector_assets.py tests/test_omni_translate_routes.py -q`
- `python3 -m compileall appcore pipeline web tests -q`

**Manual check:**
- Start dev server on an idle port.
- Confirm unauthenticated `/omni-translate/<id>` redirects with 302.
- Log in with `testuser.md` credentials and confirm the detail page returns 200.
- For a newly rerun omni task, confirm cards with LLM refs show prompt inspector buttons and modal payloads.

**2026-05-13 note:** Local dev-server smoke in this worktree was blocked because `main:app`
startup attempted a local MySQL connection and failed with `root@localhost` 1045.
Per project rule, do not continue local MySQL verification; use route tests and a
test/prod environment with configured DB credentials for the browser check.
