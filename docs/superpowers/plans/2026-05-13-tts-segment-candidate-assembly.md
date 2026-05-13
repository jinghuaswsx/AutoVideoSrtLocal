# TTS Segment Candidate Assembly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble final TTS audio from per-segment candidates so the final audio fits inside the source video duration and stays as close as possible to it.

**Architecture:** Add pure helper functions for speed candidate generation and segment-combination selection. The duration loop will collect original and native-speed segment candidates, assemble the best combination through a small concat helper, and only finish when the assembled duration lands in `[video_duration - 1s, video_duration]`.

**Tech Stack:** Python 3.12, pytest, existing `PipelineRunner`, `pipeline.tts`, ffmpeg concat.

---

### Task 1: Pure Helpers

**Files:**
- Modify: `appcore/runtime/_helpers.py`
- Modify: `appcore/runtime/__init__.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] Add tests for `_speedup_candidate_speeds`.
- [ ] Add tests for `_select_segment_candidate_assembly`.
- [ ] Implement both helpers and export them.
- [ ] Run `pytest tests/test_tts_duration_loop.py::TestSpeedupWindow tests/test_tts_duration_loop.py::TestSegmentCandidateAssembly -q`.

### Task 2: Audio Assembly

**Files:**
- Modify: `pipeline/tts.py`
- Test: runtime tests mock this helper directly.

- [ ] Add `assemble_full_audio_from_segments(segments, output_dir, variant)`.
- [ ] Write concat list from selected segment paths.
- [ ] Return assembled full audio path and selected segment metadata.

### Task 3: Duration Loop Integration

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] Add failing test: final overshoot uses segment assembly when the second speed candidate produces a video-capped combination.
- [ ] Add failing test: final overshoot keeps the original converged audio when all combinations exceed video duration.
- [ ] Add failing test: old shortcut branch continues to the next rewrite when speed candidates cannot assemble into `[v-1, v]`.
- [ ] Implement shared candidate collection and assembly logic for both branches.
- [ ] Run focused duration-loop tests.

### Task 4: UI Metadata

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Test: `tests/test_translate_detail_shell_templates.py`

- [ ] Add display text for segment assembly adoption and miss states.
- [ ] Keep old speedup card behavior compatible with existing records.
- [ ] Run `pytest tests/test_translate_detail_shell_templates.py -q`.

### Task 5: Verification

**Files:**
- Test-only.

- [ ] Run `pytest tests/test_tts_duration_loop.py tests/test_tts_generation_stats.py tests/test_translate_detail_shell_templates.py -q`.
- [ ] Confirm no local MySQL command or connection was used.
