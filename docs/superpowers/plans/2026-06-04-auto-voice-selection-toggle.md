# 大模型自动音色选择开关 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-on `auto_voice_selection` plugin config switch so AI-ranked voice matching can either continue automatically or stop for manual review.

**Architecture:** Extend the existing Omni plugin config validator and create-modal state, then branch `DialogueTranslateRunner._step_voice_match_ab()` after ranking. The runtime stores the same `speaker_profiles` in both modes; only selected voice persistence and step status differ.

**Tech Stack:** Python 3.12, Flask/Jinja templates, pytest, existing task_state runtime.

---

### Task 1: Config And Create Modal

**Files:**
- Modify: `appcore/omni_plugin_config.py`
- Modify: `web/templates/omni_translate_list.html`
- Test: `tests/test_omni_plugin_config.py`
- Test: `tests/test_web_routes_omni_create_modal.py`

- [ ] Add failing tests asserting `auto_voice_selection` defaults to `True`, appears in the create modal, is checked by default, and is written into `__omniPresetState.currentCfg`.
- [ ] Add `auto_voice_selection` to `CAPABILITY_GROUPS` and `_BOOL_FIELDS`.
- [ ] Add the modal toggle before `sentenceTtsLoudnessCalibration`, and set `currentCfg.auto_voice_selection` during submit.
- [ ] Run `pytest tests/test_omni_plugin_config.py tests/test_web_routes_omni_create_modal.py -q`.

### Task 2: Runtime Branch

**Files:**
- Modify: `appcore/runtime_dialogue.py`
- Test: `tests/test_dialogue_runtime.py`

- [ ] Add failing tests for `auto_voice_selection=True` and `False`.
- [ ] Add small helper logic to select the rank-1 candidate for speakers A and B.
- [ ] When both speakers have selected voices and the switch is on, write `selected_voice_by_speaker`, set `voice_match_ab=done`, clear review step, and leave status running.
- [ ] When switch is off or selection is incomplete, keep the existing waiting manual behavior.
- [ ] Run `pytest tests/test_dialogue_runtime.py -q`.

### Task 3: Route Regression

**Files:**
- Test: `tests/test_omni_translate_create_with_plugin_config.py`
- Test: `tests/test_dialogue_translate_routes.py`

- [ ] Verify start routes accept the new config field through existing validator.
- [ ] Run `pytest tests/test_omni_translate_create_with_plugin_config.py tests/test_dialogue_translate_routes.py -q`.

### Task 4: Full Relevant Verification

**Files:**
- All files above.

- [ ] Run `pytest tests/test_omni_plugin_config.py tests/test_web_routes_omni_create_modal.py tests/test_dialogue_runtime.py tests/test_dialogue_translate_routes.py tests/test_omni_translate_create_with_plugin_config.py -q`.
- [ ] Confirm `git diff --check` exits 0.
