# Voice Separation Card Placement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the voice-separation step card to the gap between audio extraction and the TTS voice selector on multi/omni translate detail pages.

**Architecture:** Keep `#step-separate` as the existing workbench step card inside `#pipelineCard .steps`. Use flex ordering to place it after `#step-extract`, and remove the previous DOM move that pulled it outside the pipeline before the voice selector.

**Tech Stack:** Jinja templates, vanilla JavaScript, pytest text-based template tests.

---

### Task 1: Ordered Voice-Separation Step

**Files:**
- Modify: `tests/test_translate_detail_shell_templates.py`
- Modify: `web/templates/_translate_detail_shell.html`
- Modify: `web/templates/_separation_card.html`

- [ ] **Step 1: Write the failing test**

Add a test that reads `_translate_detail_shell.html` and `_separation_card.html`, then asserts:

```python
assert "#pipelineCard .steps > #step-separate { order: -1; }" in shared
assert "voiceSel.parentNode.insertBefore(step, voiceSel)" not in separation
assert "moveSeparateBeforeVoiceSelector" not in separation
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_voice_separation_card_stays_after_audio_extract_before_tts_selector -q
```

Expected: FAIL because the shell has no `#step-separate` order rule and the separation script still inserts before `voice-selector-multi`.

- [ ] **Step 3: Write minimal implementation**

Add the CSS order rule beside `#step-extract`:

```css
#pipelineCard .steps > #step-separate { order: -1; }
```

Remove the DOM movement block from `_separation_card.html`:

```javascript
// No DOM reparenting for #step-separate; CSS order keeps it after audio extraction.
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py -q
```

Expected: PASS.

- [ ] **Step 5: Run detail-page regression tests**

Run:

```bash
pytest tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py tests/test_runtime_multi_asr_normalize.py -q
```

Expected: PASS, or report any pre-existing environment/database limitation separately with exact failure output.
