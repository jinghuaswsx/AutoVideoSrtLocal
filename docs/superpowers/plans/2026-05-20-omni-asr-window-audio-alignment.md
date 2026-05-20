# Omni ASR Window Audio Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place translated sentence TTS only inside ASR speech windows, preserving source no-ASR lead-in, middle gaps, and tail as background or silence.

**Architecture:** Add a focused ASR-window scheduler in `pipeline/audio_stitch.py` and wire sentence-level AV paths to it. Keep the existing compact scheduler unchanged for old tests and callers. Pass source video duration into final TTS rebuilding so no-background fallback output keeps the original tail.

**Tech Stack:** Python 3.12, pytest, ffmpeg command wrappers.

---

### Task 1: Scheduler Tests

**Files:**
- Modify: `tests/test_audio_stitch.py`

- [x] Add `test_apply_asr_window_audio_schedule_preserves_initial_no_asr_gap`.
- [x] Add `test_apply_asr_window_audio_schedule_preserves_large_middle_gap_and_compacts_short_gap`.
- [x] Run `pytest tests/test_audio_stitch.py -q` and confirm the new tests fail because `apply_asr_window_audio_schedule` does not exist.

### Task 2: Scheduler Implementation

**Files:**
- Modify: `pipeline/audio_stitch.py`

- [x] Implement `apply_asr_window_audio_schedule(sentences, max_gap=0.25, preserve_gap_threshold=1.0)`.
- [x] Preserve source diagnostics and set `timeline_mode="asr_window_primary"`.
- [x] Run `pytest tests/test_audio_stitch.py -q` and confirm scheduler tests pass.

### Task 3: Runtime Wiring

**Files:**
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Modify: `appcore/runtime/__init__.py`
- Modify: `appcore/runtime/_av_helpers.py`
- Test: `tests/test_av_source_time_audio.py`

- [x] Add a failing test proving `_rebuild_tts_full_audio_from_segments(..., total_duration=48.181)` passes `-t 48.181` to ffmpeg.
- [x] Import and use `apply_asr_window_audio_schedule` in sentence-level TTS paths.
- [x] Pass source `video_duration` into `_rebuild_tts_full_audio_from_segments`.
- [x] Run `pytest tests/test_audio_stitch.py tests/test_av_source_time_audio.py -q`.

### Task 4: Final Verification

**Files:**
- No additional production files expected.

- [x] Run `pytest tests/test_audio_stitch.py tests/test_av_source_time_audio.py tests/test_av_subtitle_units.py -q`.
- [x] Run `git diff --check`.
- [x] Inspect the diff to ensure no unrelated production files, DB code, deployment scripts, or service restarts were touched.
