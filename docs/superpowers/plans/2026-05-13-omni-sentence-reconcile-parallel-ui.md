# Omni Sentence Reconcile Parallel UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sentence-level TTS convergence visibly progress from fixed sentence slots and run sentence workers with a default 5-concurrency pool.

**Architecture:** Keep the public `reconcile_duration()` API, split per-sentence state handling from orchestration, and serialize final output by original sentence position. Move convergence UI into the existing TTS duration log and remove obsolete standalone panels.

**Tech Stack:** Python 3.12, pytest, Flask/Jinja templates, browser-side JavaScript in `_task_workbench_scripts.html`.

---

### Task 1: Backend Parallel Reconcile

**Files:**
- Modify: `pipeline/duration_reconcile.py`
- Test: `tests/test_duration_reconcile.py`

- [ ] Write a failing test that calls `reconcile_duration(max_sentence_workers=2)` with three out-of-range sentences, blocks two `rewrite_one()` calls until both start, and asserts the first two sentence positions start before either finishes.
- [ ] Write a failing test that captures progress records and asserts all `phase="queued"` records are emitted in sentence order before worker progress.
- [ ] Implement a private per-sentence helper that contains the existing single-sentence loop.
- [ ] Add a coordinator using `ThreadPoolExecutor(max_workers=max_sentence_workers)` and return final sentences sorted by position.
- [ ] Run `pytest tests/test_duration_reconcile.py -q`.

### Task 2: Model Binding

**Files:**
- Modify: `appcore/llm_use_cases.py`
- Test: `tests/test_llm_use_cases_registry.py`

- [ ] Update the failing registry expectation so `video_translate.av_rewrite` uses `google/gemini-3-flash-preview`.
- [ ] Change the default model in `USE_CASES`.
- [ ] Run `pytest tests/test_llm_use_cases_registry.py -q`.

### Task 3: TTS Card UI

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_web_routes.py`
- Test: `tests/test_translate_detail_shell_templates.py`

- [ ] Update template tests to assert the standalone `avInsightsPanel` and `avConvergencePanel` are absent.
- [ ] Update tests to assert `renderAvInsights()` and `renderAvConvergence()` are no longer called.
- [ ] Keep `renderSentenceReconcileDurationLog()` in the TTS preview path and support queued/running/done sentence states.
- [ ] Remove obsolete standalone panel markup, script calls, and unused CSS selectors.
- [ ] Keep `avSubtitleUnitsPanel` markup after the TTS step area and retain `renderAvSubtitleUnits()`.
- [ ] Run `pytest tests/test_web_routes.py tests/test_translate_detail_shell_templates.py -q`.

### Task 4: Integrated Verification

**Files:**
- No new production files.

- [ ] Run `pytest tests/test_duration_reconcile.py tests/test_llm_use_cases_registry.py tests/test_web_routes.py tests/test_translate_detail_shell_templates.py -q`.
- [ ] Start a local dev server only if the test route verification requires a live HTTP check; do not restart production services.
