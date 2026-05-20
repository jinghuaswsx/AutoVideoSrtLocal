# Conditional ASR Gap Audio Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve ASR no-speech windows across English redub, Omni translate, and Multi translate without changing Multi behavior for continuous-speech videos.

**Architecture:** Add shared ASR gap analysis in `pipeline.audio_stitch`. Keep `sentence_reconcile` scheduling as-is. In the shared five-round TTS loop, conditionally target the active speech budget and rebuild the final audio on ASR windows only when large gaps are detected.

**Tech Stack:** Python 3.12, pytest, ffmpeg-backed audio helpers.

---

### Task 1: Gap Analysis Helper

**Files:**
- Modify: `pipeline/audio_stitch.py`
- Test: `tests/test_audio_stitch.py`

- [x] Add tests for continuous ASR windows returning disabled gap analysis.
- [x] Add tests for leading, middle, and trailing gaps returning enabled gap analysis and active speech budget.
- [x] Implement `analyze_asr_window_gaps(...)` with `preserve_gap_threshold=1.0` and `max_gap=0.25`.
- [x] Run `pytest tests/test_audio_stitch.py -q`.

### Task 2: Conditional Five-Round Runtime

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Test: `tests/test_runtime_multi_translate.py`

- [x] Add a test proving Multi no-gap tasks still call `_run_tts_duration_loop` with full `video_duration`.
- [x] Add a test proving Multi gap tasks call `_run_tts_duration_loop` with the active speech budget.
- [x] Add a test proving Multi gap tasks rebuild final audio with ASR-window scheduling, write `audio_timeline_mode="asr_window_conditional"`, and omit `timeline_manifest`.
- [x] Implement conditional gap analysis before each variant loop result.
- [x] Run `pytest tests/test_runtime_multi_translate.py -q`.

### Task 3: Sentence-Level Coverage

**Files:**
- Modify: `tests/test_english_redub_runtime.py`
- Modify: `tests/test_sentence_translate_runtime.py`

- [x] Add or extend tests proving English redub sentence reconcile remains `asr_window_primary`.
- [x] Keep existing Omni sentence reconcile tests passing.
- [x] Run `pytest tests/test_english_redub_runtime.py tests/test_sentence_translate_runtime.py -q`.

### Task 4: Verification

**Files:**
- Modify: `pipeline/capcut.py`
- Test: `tests/test_capcut_export.py`

- [x] Add a test proving generated CapCut `draft_content.json` mutes video-track audio.
- [x] Post-process pyJianYingDraft output so video segments have `volume=0.0` while TTS and ambience tracks stay active.

### Task 5: Verification

**Files:**
- No new production files.

- [x] Run focused audio/runtime tests.
- [x] Run `python3 -m py_compile` for touched runtime modules.
- [x] Run `git diff --check`.
