# TTS Speed Voice Settings Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve TTS speed assembly by varying ElevenLabs voice settings and adding one bounded fallback rewrite round after three speed candidates miss.

**Architecture:** Keep the existing duration-loop structure and segment assembly optimizer. Extend the ElevenLabs TTS wrapper to accept optional `stability` and `similarity_boost`, then have `_run_tts_duration_loop` attach conservative settings profiles to the three native speed attempts and continue for one extra rewrite round after the first stage-1 speed miss.

**Tech Stack:** Python 3.12, pytest, ElevenLabs SDK wrapper, existing `PipelineRunner`.

---

### Task 1: Voice Settings Propagation

**Files:**
- Modify: `pipeline/tts.py`
- Modify: `appcore/tts_engines/base.py`
- Modify: `appcore/tts_engines/elevenlabs.py`
- Test: `tests/test_tts_pipeline.py`
- Test: `tests/test_tts_engines.py`

- [ ] Add failing tests that `generate_segment_audio` passes `speed`, `stability`, and `similarity_boost` into `VoiceSettings`.
- [ ] Add failing engine delegation test for `regenerate_with_speed(..., stability=..., similarity_boost=...)`.
- [ ] Extend signatures and pass the settings through.
- [ ] Run `pytest tests/test_tts_pipeline.py::test_generate_segment_audio_passes_speed_and_voice_settings tests/test_tts_engines.py::test_elevenlabs_regenerate_with_speed_delegates_voice_settings -q`.

### Task 2: Speed Candidate Settings

**Files:**
- Modify: `appcore/runtime/_helpers.py`
- Modify: `appcore/runtime/__init__.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] Add a helper that returns attempt profiles: speed-only, balanced, varied.
- [ ] Add tests proving attempts 1/2/3 map to the expected stability and similarity values.
- [ ] Export the helper through `appcore.runtime`.
- [ ] Run `pytest tests/test_tts_duration_loop.py::TestSegmentCandidateAssembly -q`.

### Task 3: One-Round Fallback Loop

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] Add failing test: first stage-1 speed assembly miss triggers one additional rewrite/TTS round.
- [ ] Add failing test: fallback round can adopt a video-capped segment assembly.
- [ ] Add failing test: second stage-1 miss keeps the stage-1 audio and does not add a second fallback round.
- [ ] Convert the fixed `for` loop to a bounded dynamic loop with one extra fallback budget.
- [ ] Store fallback metadata in `tts_duration_rounds`.
- [ ] Run `pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut -q`.

### Task 4: Focused Verification

**Files:**
- Test-only.

- [ ] Run `pytest tests/test_tts_duration_loop.py tests/test_tts_pipeline.py tests/test_tts_engines.py tests/test_tts_generation_stats.py -q`.
- [ ] Confirm no local MySQL command or connection was used.
