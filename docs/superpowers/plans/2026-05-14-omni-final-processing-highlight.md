# Omni Final Processing Highlight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Omni sentence reconcile final processing result prominent in the task detail page, including final duration, speech+silence composition, tail padding, and truncation status.

**Architecture:** Extend the existing `final_compose_summary` diagnostics in `appcore/tts_strategies/sentence_reconcile.py`, then render those fields in the existing frontend `renderFinalComposeSummary` card. Keep all media generation behavior unchanged.

**Tech Stack:** Python 3.12, pytest, Flask/Jinja template scripts, vanilla JavaScript.

---

### Task 1: Backend Summary Fields

**Files:**
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Test: `tests/test_sentence_translate_runtime.py`

- [ ] **Step 1: Write the failing test**

Update `test_tts_step_records_fallback_final_compose_summary` to assert:

```python
assert summary["audio_content_duration"] == pytest.approx(1.2)
assert summary["tail_padding_duration"] == pytest.approx(0.0)
assert "最终输出" in summary["final_processing_label"]
assert "截断" in summary["final_processing_label"]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_tts_step_records_fallback_final_compose_summary -q
```

Expected: fail because `audio_content_duration`, `tail_padding_duration`, and `final_processing_label` are not present yet.

- [ ] **Step 3: Implement the minimal backend fields**

In `_build_final_compose_summary`, compute:

```python
audio_content_duration = max(
    _float_value(sentence.get("audio_end_time"), 0.0)
    for sentence in final_sentences
)
tail_padding_duration = max(0.0, final_output_audio_duration - audio_content_duration)
final_processing_label = (
    f"最终输出 {final_output_audio_duration:.1f}s = "
    f"口播 {effective_speech_duration:.1f}s + "
    f"句间静音 {silence_gap_duration:.1f}s + "
    f"尾部静音 {tail_padding_duration:.1f}s；"
    f"{'已截断 ' + str(truncated_seconds) + 's' if clipped_segments else '无截断'}"
)
```

Return the rounded fields in `final_compose_summary`.

- [ ] **Step 4: Re-run the focused test**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_tts_step_records_fallback_final_compose_summary -q
```

Expected: pass.

### Task 2: Frontend Highlight

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_translate_detail_shell_templates.py`

- [ ] **Step 1: Write the failing template assertions**

Add assertions that the script/style contain:

```python
assert "final-processing-banner" in script
assert "成品音轨" in script
assert "尾部静音补齐" in script
assert ".final-processing-banner" in styles
```

- [ ] **Step 2: Run the focused template test and verify it fails**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_sentence_reconcile_process_is_rendered_in_tts_duration_log -q
```

Expected: fail because the new frontend banner and labels do not exist.

- [ ] **Step 3: Implement the frontend banner**

In `renderFinalComposeSummary`, add a top banner before the metric grid:

```javascript
<div class="final-processing-banner ${clipped ? "is-clipped" : "is-clean"}">
  <div class="final-processing-kicker">最终处理</div>
  <div class="final-processing-main">${escapeHtml(summary.final_processing_label || fallbackLabel)}</div>
  <div class="final-processing-detail">按 audio_start_time 放置每句音频；句间静音补齐；尾部不足补静音；最后由 ffmpeg -t 限制到成品音轨时长。</div>
</div>
```

Rename or supplement the metric label `输出音轨时长` to `成品音轨时长`, and add `音频内容时长` and `尾部静音补齐` metrics from the new summary fields.

- [ ] **Step 4: Add restrained but visible CSS**

In `_task_workbench_styles.html`, style `.final-processing-banner` with a stronger border, pale warning/success background, and larger main text. Keep it inside the existing card, not as a nested card.

- [ ] **Step 5: Re-run template test**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_sentence_reconcile_process_is_rendered_in_tts_duration_log -q
```

Expected: pass.

### Task 3: Focused Verification

**Files:**
- Test only

- [ ] **Step 1: Run backend and template tests**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_tts_step_records_fallback_final_compose_summary tests/test_translate_detail_shell_templates.py::test_sentence_reconcile_process_is_rendered_in_tts_duration_log -q
```

Expected: both tests pass.

- [ ] **Step 2: Check working tree**

Run:

```bash
git status --short
```

Expected: only the spec, plan, backend, frontend, and focused tests are modified.
