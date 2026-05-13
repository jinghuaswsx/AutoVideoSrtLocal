# Multilingual Subtitle Safe Splitting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent oversized multilingual subtitle chunks from being truncated by splitting them into readable, semantically coherent chunks with aligned timelines.

**Architecture:** Add a focused `pipeline.subtitle_splitting` module that sits between subtitle ASR alignment and SRT generation. It uses language rules for line capacity, CPS, and weak boundary words, then returns complete, non-overlapping subtitle chunks for existing SRT and CapCut exporters.

**Tech Stack:** Python 3.12, pytest, existing `pipeline.subtitle`, `pipeline.subtitle_alignment`, `pipeline.languages`.

---

### Task 1: Regression Tests

**Files:**
- Create: `tests/test_subtitle_splitting.py`
- Modify: `tests/test_subtitle.py`

- [ ] **Step 1: Add failing splitter tests**

Create tests for the reported German sentence, word timestamp alignment, and proportional fallback. The tests should assert that every original word remains present, split piece timings are monotonic, and each German piece fits `38 * 2` characters.

- [ ] **Step 2: Add failing overflow safety test**

Extend `tests/test_subtitle.py` with a test proving `build_srt_from_chunks()` no longer drops the tail of a long text.

- [ ] **Step 3: Verify RED**

Run:

```powershell
pytest tests/test_subtitle_splitting.py tests/test_subtitle.py -q
```

Expected: new tests fail because `pipeline.subtitle_splitting` does not exist yet and/or overflow text is still truncated.

### Task 2: Splitter Module

**Files:**
- Create: `pipeline/subtitle_splitting.py`
- Modify: `pipeline/subtitle_alignment.py`

- [ ] **Step 1: Implement public API**

Add `split_oversized_subtitle_chunks(chunks, *, max_chars_per_line, max_lines, max_chars_per_second, weak_boundary_words=None)` returning a list of chunk dicts.

- [ ] **Step 2: Implement display-fit checks**

Use `pipeline.subtitle.format_subtitle_chunk_text()` and line-length checks to decide whether a piece is safe. A piece is unsafe if it exceeds line capacity, hard capacity, or CPS.

- [ ] **Step 3: Implement semantic split selection**

Prefer punctuation and weak-boundary-aware word splits near the target capacity. Forced word splits are allowed only when no semantic split fits.

- [ ] **Step 4: Implement time allocation**

Use matched `words` metadata when available. Fall back to proportional timing inside the original chunk range. Ensure increasing, non-overlapping timestamps.

- [ ] **Step 5: Preserve matched words during subtitle alignment**

Update `align_subtitle_chunks_to_asr()` so each aligned chunk carries its matched word timestamp list as `words`. Existing callers can ignore the extra metadata.

- [ ] **Step 6: Verify GREEN for splitter tests**

Run:

```powershell
pytest tests/test_subtitle_splitting.py -q
```

Expected: splitter tests pass.

### Task 3: SRT Overflow Safety

**Files:**
- Modify: `pipeline/subtitle.py`
- Test: `tests/test_subtitle.py`

- [ ] **Step 1: Replace silent truncation fallback**

Change `wrap_text()` so overflow text is not discarded. It may return more than `max_lines` as a last-resort fallback, but it must preserve every word.

- [ ] **Step 2: Verify SRT safety**

Run:

```powershell
pytest tests/test_subtitle.py tests/test_subtitle_param_compat.py -q
```

Expected: all subtitle formatter tests pass and overflow safety test passes.

### Task 4: Runtime Integration

**Files:**
- Modify: `appcore/runtime_multi.py`
- Modify: `appcore/runtime_omni_steps.py`
- Modify: `tests/test_runtime_multi_subtitle.py`

- [ ] **Step 1: Import and call splitter**

Call `split_oversized_subtitle_chunks()` after `align_subtitle_chunks_to_asr()` and before `build_srt_from_chunks()` in multi and omni subtitle stages.

- [ ] **Step 2: Pass language rules**

Pass `MAX_CHARS_PER_LINE`, `MAX_LINES`, `MAX_CHARS_PER_SECOND`, and `WEAK_STARTERS` from the resolved language module.

- [ ] **Step 3: Assert integration arguments**

Update runtime tests so they verify multi-translate passes language-specific limits into the splitter and feeds split chunks into SRT generation.

- [ ] **Step 4: Verify integration**

Run:

```powershell
pytest tests/test_runtime_multi_subtitle.py tests/test_subtitle_splitting.py tests/test_subtitle.py -q
```

Expected: all tests pass.

### Task 5: Final Verification

**Files:**
- No new files unless tests reveal a focused fix.

- [ ] **Step 1: Run focused suite**

Run:

```powershell
pytest tests/test_subtitle.py tests/test_subtitle_param_compat.py tests/test_subtitle_alignment.py tests/test_runtime_multi_subtitle.py tests/test_subtitle_splitting.py -q
```

Expected: all focused subtitle tests pass.

- [ ] **Step 2: Inspect diff**

Run:

```powershell
git diff -- docs/superpowers/specs/2026-05-13-multilingual-subtitle-safe-splitting-design.md docs/superpowers/plans/2026-05-13-multilingual-subtitle-safe-splitting.md pipeline/subtitle.py pipeline/subtitle_splitting.py appcore/runtime_multi.py appcore/runtime_omni_steps.py tests/test_subtitle.py tests/test_subtitle_splitting.py tests/test_runtime_multi_subtitle.py
```

Expected: only scoped subtitle splitting changes are present.
