# Omni Sentence-Level Alignment Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Omni sentence-level translation one-to-one, semantically complete, duration-aligned, and subtitle-safe.

**Architecture:** Add source-anchor coverage metadata to `pipeline.av_translate`, make `pipeline.duration_reconcile` repair semantic coverage before accepting timing, and pass AV sentence-unit subtitles through the existing multilingual safe splitter.

**Tech Stack:** Python 3.12, pytest, existing `llm_client` JSON schema calls, existing subtitle language rule modules.

---

### Task 1: AV Translation Coverage Contract

**Files:**
- Modify: `pipeline/av_translate.py`
- Test: `tests/test_av_translate.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert `_build_translate_messages()` includes `must_keep_terms`, the schema requires coverage fields, and merged output preserves coverage metadata.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_av_translate.py -q`

Expected: coverage-related assertions fail before implementation.

- [ ] **Step 3: Implement source anchors and schema fields**

Add deterministic source-anchor extraction, include `must_keep_terms` in each sentence input, update prompt text, and merge `covered_source_terms`, `omitted_source_terms`, and `coverage_ok`.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_av_translate.py -q`

Expected: all tests in `tests/test_av_translate.py` pass.

### Task 2: Semantic Repair Before Duration Acceptance

**Files:**
- Modify: `pipeline/av_translate.py`
- Modify: `pipeline/duration_reconcile.py`
- Test: `tests/test_duration_reconcile.py`

- [ ] **Step 1: Write failing tests**

Add a test where initial TTS duration is inside `[0.95, 1.05]` but the sentence has `coverage_ok=false` and `omitted_source_terms`. The expected behavior is a `repair_coverage` rewrite and regenerated TTS before final acceptance.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_duration_reconcile.py -q`

Expected: the new semantic-repair test fails because current code accepts duration-only `ok`.

- [ ] **Step 3: Implement semantic gate**

Preserve coverage metadata, detect unsafe coverage, call `av_translate.rewrite_one(..., direction="repair_coverage", return_sentence=True)`, update coverage fields from the rewrite response, and mark unresolved coverage as `warning_semantic`.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_duration_reconcile.py -q`

Expected: duration tests pass.

### Task 3: Sentence-Unit Subtitle Safe Splitting

**Files:**
- Modify: `appcore/translate_profiles/av_sync_profile.py`
- Test: `tests/test_runtime_omni_dispatch.py`

- [ ] **Step 1: Write failing test**

Add a sentence-unit subtitle test that monkeypatches `split_oversized_subtitle_chunks` and asserts the AV subtitle path calls it before `build_srt_from_chunks`.

- [ ] **Step 2: Run RED**

Run: `pytest tests/test_runtime_omni_dispatch.py -q`

Expected: the new subtitle splitter assertion fails before implementation.

- [ ] **Step 3: Implement splitter call**

Resolve target-language rules, pass subtitle units through `split_oversized_subtitle_chunks`, then call `build_srt_from_chunks()` with language line settings.

- [ ] **Step 4: Run GREEN**

Run: `pytest tests/test_runtime_omni_dispatch.py -q`

Expected: runtime dispatch tests pass.

### Task 4: Focused Verification

**Files:**
- Read: `docs/superpowers/specs/2026-05-13-omni-sentence-level-alignment-guard-design.md`

- [ ] **Step 1: Run focused suite**

Run:

```bash
pytest tests/test_av_translate.py tests/test_duration_reconcile.py tests/test_runtime_omni_dispatch.py tests/test_subtitle_splitting.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Check diff hygiene**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.
