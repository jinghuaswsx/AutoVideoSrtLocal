# TTS Preview Rate Prior Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use voice preview ASR speed as a cold-start TTS speed prior, then prefer real measured TTS speed once available.

**Architecture:** Add a focused resolver in `pipeline/speech_rate_model.py` that keeps `get_rate()` as real TTS only and exposes an effective rate lookup with source metadata. Wire existing character-budget callers to the effective lookup and update sentence-reconcile to write real TTS samples after measured audio durations are available.

**Tech Stack:** Python 3.12, Flask task runtime, pytest, MySQL via `appcore.db`.

---

### Task 1: Effective Speech-Rate Resolver

**Files:**
- Modify: `pipeline/speech_rate_model.py`
- Test: `tests/test_speech_rate_model.py`

- [ ] Write tests for actual-over-preview precedence and preview-prior fallback.
- [ ] Run the new tests and verify they fail before implementation.
- [ ] Implement preview URL hash lookup and `get_rate_with_source`.
- [ ] Run `pytest tests/test_speech_rate_model.py -q`.

### Task 2: Character Range Callers

**Files:**
- Modify: `pipeline/av_translate.py`
- Modify: `appcore/runtime_omni_steps.py`
- Modify: `appcore/runtime_english_redub.py`
- Test: `tests/test_av_translate.py`
- Test: `tests/test_english_redub_runtime.py`

- [ ] Write tests proving preview prior changes initial target character ranges.
- [ ] Run the focused tests and verify they fail before implementation.
- [ ] Replace first-pass character-budget reads with the effective resolver.
- [ ] Run the focused tests.

### Task 3: Real TTS Sample Updates

**Files:**
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Test: `tests/test_sentence_translate_runtime.py`

- [ ] Write a test proving sentence-reconcile records measured TTS speed.
- [ ] Run the focused test and verify it fails before implementation.
- [ ] Add a small helper that updates `voice_speech_rate` from measured TTS segments.
- [ ] Run the focused test and related sentence-reconcile tests.

### Task 4: Verification

**Files:**
- Run only

- [ ] Run `pytest tests/test_speech_rate_model.py tests/test_av_translate.py tests/test_english_redub_runtime.py tests/test_sentence_translate_runtime.py -q`.
- [ ] Run `git diff --check`.

### Task 5: Multi-Language First-Round TTS Diagnostics

**Files:**
- Modify: `pipeline/ja_translate.py`
- Modify: `appcore/runtime/_pipeline_runner.py`
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_ja_translate_pipeline.py`
- Test: `tests/test_tts_duration_loop.py`
- Test: `tests/test_translate_detail_shell_templates.py`

- [ ] Write tests for Japanese multi translate using the effective preview prior.
- [ ] Write tests for shared multi-language TTS round-1 diagnostics and measured-rate recording.
- [ ] Write template tests for visible speech-rate diagnostics.
- [ ] Run focused tests and verify they fail before implementation.
- [ ] Add round-1 speech-rate metadata and UI rendering.
- [ ] Run focused tests, previous speech-rate tests, and `git diff --check`.
