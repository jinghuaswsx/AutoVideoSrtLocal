# 句级 TTS 响度校准 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in Omni sentence-level TTS loudness calibration toggle that aligns each TTS segment to separated vocals loudness before timeline assembly.

**Architecture:** Store the toggle in Omni `plugin_config`, expose it in the create modal and detail topbar, persist detail changes through an Omni-only API, and run calibration in `SentenceReconcileStrategy` before `_rebuild_tts_full_audio_from_segments()`. Reuse `appcore.audio_loudness.normalize_to_lufs()` and skip safely when `separation.vocals_lufs` is unavailable.

**Tech Stack:** Python 3.12, Flask routes/templates, pytest, ffmpeg-backed audio loudness helpers.

---

### Task 1: Plugin Config Schema

**Files:**
- Modify: `appcore/omni_plugin_config.py`
- Test: `tests/test_omni_plugin_config.py`

- [x] Add failing tests asserting `validate_plugin_config({})["sentence_tts_loudness_calibration"]` is `False` and string/boolean true values are accepted.
- [x] Run `python -m pytest tests/test_omni_plugin_config.py -q` and confirm the new tests fail because the field is missing.
- [x] Add `sentence_tts_loudness_calibration` to `CAPABILITY_GROUPS` as a checkbox defaulting to `False`.
- [x] Add the field to `_BOOL_FIELDS`.
- [x] Re-run `python -m pytest tests/test_omni_plugin_config.py -q` and confirm it passes.

### Task 2: Create Modal Toggle

**Files:**
- Modify: `web/templates/omni_translate_list.html`
- Test: `tests/test_web_routes_omni_create_modal.py`

- [x] Add failing static-template tests for the visible label `句级TTS响度校准`, input id `sentenceTtsLoudnessCalibration`, default unchecked state, and JS that writes `sentence_tts_loudness_calibration`.
- [x] Run `python -m pytest tests/test_web_routes_omni_create_modal.py -q` and confirm the new tests fail.
- [x] Add the toggle to the create modal near existing source language/config controls.
- [x] Update the modal JS so submitted `plugin_config` includes the checkbox value.
- [x] Re-run `python -m pytest tests/test_web_routes_omni_create_modal.py -q`.

### Task 3: Detail Topbar Toggle And Persistence API

**Files:**
- Modify: `web/templates/_translate_detail_shell.html`
- Modify: `web/routes/omni_translate.py`
- Test: `tests/test_translate_detail_shell_templates.py`
- Test: `tests/test_omni_translate_routes.py`

- [x] Add failing tests proving the detail shell renders `sentenceTtsLoudnessCalibrationCb` before `visibleToAllCb`, posts to `/api/omni-translate/<task_id>/sentence-tts-loudness-calibration`, and includes CSRF headers.
- [x] Add failing route tests proving the new PUT endpoint updates `state.plugin_config.sentence_tts_loudness_calibration`.
- [x] Run `python -m pytest tests/test_translate_detail_shell_templates.py tests/test_omni_translate_routes.py -q` and confirm the new tests fail.
- [x] Render the toggle only for Omni detail pages with `_force_restart_api == "/api/omni-translate"`.
- [x] Add JS change handling that PUTs the boolean value and reverts on failure.
- [x] Add `toggle_sentence_tts_loudness_calibration()` route with `@login_required` and `@admin_required`, load current config, validate it, persist with `update_project_state()` and `store.update()`, and return the normalized value.
- [x] Re-run the two test files.

### Task 4: Sentence-Level Audio Calibration

**Files:**
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Test: `tests/test_sentence_translate_runtime.py`

- [x] Add failing runtime tests that monkeypatch `normalize_to_lufs()` and `_rebuild_tts_full_audio_from_segments()` to assert final segment `tts_path` values are replaced when the toggle is enabled and `separation.vocals_lufs` exists.
- [x] Add failing runtime test asserting no loudnorm call when `vocals_lufs` is missing and a skipped summary is recorded.
- [x] Run `python -m pytest tests/test_sentence_translate_runtime.py -q` and confirm the new tests fail.
- [x] Implement a small helper in `sentence_reconcile.py` that reads the toggle from validated `plugin_config`, targets `separation.vocals_lufs`, writes calibrated files under `task_dir/tts_loudness_segments/`, replaces segment paths, and records `sentence_tts_loudness_calibration` summary in `final_compose_summary` / `av_debug`.
- [x] Re-run `python -m pytest tests/test_sentence_translate_runtime.py -q`.

### Task 5: Focused Verification

**Files:**
- No production edits.

- [x] Run `python -m pytest tests/test_omni_plugin_config.py tests/test_web_routes_omni_create_modal.py tests/test_translate_detail_shell_templates.py tests/test_omni_translate_routes.py tests/test_sentence_translate_runtime.py -q`.
- [x] Run `python -m pytest tests/test_audio_loudness.py -q`.
- [x] Skip local dev server: default local DB config is `127.0.0.1:3306`, and project rules forbid local MySQL access.
- [x] Verify via template tests that the Omni detail page topbar toggle appears immediately left of `对所有人可见` and the create modal toggle defaults off.
