# Dialogue Subtitle Omni Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Omni-equivalent subtitle font, size, and position controls to dialogue video translation, with preview coordinates matching final hard subtitle output.

**Architecture:** Keep dialogue A/B voice selection as the owning panel, and embed the same subtitle controls and preview semantics used by Omni. Persist normalized subtitle fields in `confirm_voices` before resuming the shared Omni V2 downstream alignment/subtitle/compose flow.

**Tech Stack:** Flask, Jinja templates, vanilla JavaScript, pytest, Node syntax check.

---

### Task 1: Route Tests For Dialogue Subtitle Persistence

**Files:**
- Modify: `tests/test_dialogue_translate_routes.py`
- Modify: `web/routes/dialogue_translate.py`

- [ ] **Step 1: Write a failing route test**

Add a test near the existing `test_dialogue_translate_confirm_voices_persists_selection_and_resumes_alignment` that posts:

```python
json={
    "selected_voice_by_speaker": {"A": "voice-a-2", "B": "voice-b"},
    "subtitle_font": "Oswald Bold",
    "subtitle_size": 22,
    "subtitle_position_y": 0.42,
    "subtitle_position": "bottom",
}
```

Assert the saved state and in-memory store contain those subtitle fields.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
pytest tests/test_dialogue_translate_routes.py::test_dialogue_translate_confirm_voices_persists_subtitle_settings -q
```

Expected: FAIL because `confirm_voices` does not persist subtitle settings yet.

- [ ] **Step 3: Implement route persistence**

In `web/routes/dialogue_translate.py`, import or use `normalize_confirm_voice_payload()` and call it with a synthetic `voice_id` so only subtitle normalization is reused. Persist `subtitle_font`, `subtitle_size`, `subtitle_position_y`, and `subtitle_position` on `state`, and pass them through `task_state.update()`.

- [ ] **Step 4: Re-run the focused test**

Run:

```bash
pytest tests/test_dialogue_translate_routes.py::test_dialogue_translate_confirm_voices_persists_subtitle_settings -q
```

Expected: PASS.

### Task 2: Detail Template And JavaScript Subtitle Controls

**Files:**
- Modify: `tests/test_dialogue_translate_routes.py`
- Modify: `web/templates/dialogue_translate_detail.html`
- Modify: `web/static/js/dialogue_translate_detail.js`

- [ ] **Step 1: Write a failing render/static test**

Extend the detail render/static assertions to require:

```python
assert 'id="dialogueSubtitleFont"' in body
assert 'id="dialogueSubtitlePositionY"' in body
assert "loadSubtitlePreviewPayload" in script
assert "subtitle_position_y" in script
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
pytest tests/test_dialogue_translate_routes.py::test_dialogue_translate_detail_renders_ab_panel tests/test_dialogue_translate_routes.py::test_dialogue_translate_detail_js_does_not_interpolate_task_state_with_inner_html -q
```

Expected: FAIL because the dialogue detail page has no subtitle controls or payload submission yet.

- [ ] **Step 3: Add dialogue subtitle UI**

Add a subtitle section inside `dialogueVoicePanel` with:

- `#dialogueSubtitleFont`
- `#dialogueSubtitleSizeGroup`
- `#dialogueSubtitlePositionY`
- `#dialogueSubtitlePreviewFrame`
- `#dialogueSubtitlePreviewVideo`
- `#dialogueSubtitlePreviewBlock`
- `#dialogueSubtitlePreviewNote`

Use `transform: translateY(-100%)` on the preview block so `subtitle_position_y` is the subtitle bottom edge.

- [ ] **Step 4: Add JavaScript control logic**

In `dialogue_translate_detail.js`, fetch `/subtitle-preview`, apply initial `subtitle_font`, `subtitle_size`, and `subtitle_position_y`, update preview on control changes, support dragging the preview block, and include subtitle fields in the `/confirm-voices` JSON body.

- [ ] **Step 5: Re-run frontend checks**

Run:

```bash
pytest tests/test_dialogue_translate_routes.py::test_dialogue_translate_detail_renders_ab_panel tests/test_dialogue_translate_routes.py::test_dialogue_translate_detail_js_does_not_interpolate_task_state_with_inner_html -q
node --check web/static/js/dialogue_translate_detail.js
```

Expected: all commands PASS.

### Task 3: Full Relevant Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run route suite**

Run:

```bash
pytest tests/test_dialogue_translate_routes.py tests/test_translate_detail_protocol.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 2: Check git diff**

Run:

```bash
git diff -- docs/superpowers/specs/2026-06-04-dialogue-subtitle-omni-alignment-design.md docs/superpowers/plans/2026-06-04-dialogue-subtitle-omni-alignment.md web/templates/dialogue_translate_detail.html web/static/js/dialogue_translate_detail.js web/routes/dialogue_translate.py tests/test_dialogue_translate_routes.py
```

Expected: diff only covers the documented scope.

