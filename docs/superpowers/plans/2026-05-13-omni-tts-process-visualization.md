# Omni TTS Process Visualization Implementation Plan

> **Docs-anchor:** `docs/superpowers/specs/2026-05-13-omni-tts-process-visualization-design.md`

**Goal:** Make the Omni `sentence_reconcile` TTS stage visibly traceable on the task detail page, including first-pass audio generation progress, sentence rewrite start, sentence audio regeneration start, and detailed attempt tables/cards.

**Architecture:** Keep using the existing `tts_duration_round` socket/state channel. Add richer progress records in `pipeline.duration_reconcile` and `appcore.tts_strategies.sentence_reconcile`, then render those records in `_task_workbench_scripts.html` with focused CSS additions.

**Tech Stack:** Python 3.12, Flask/Jinja templates, pytest, existing task socket events.

---

### Task 1: Backend Progress Contract

**Files:**
- Modify: `pipeline/duration_reconcile.py`
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Test: `tests/test_duration_reconcile.py`

- [x] Emit `rewrite_start` before `av_translate.rewrite_one(...)`.
- [x] Emit `tts_regen_start` before `_regenerate_segment(...)`.
- [x] Add active attempt metadata to `_sentence_progress_payload`.
- [x] Emit `initial_audio_gen` snapshots during first-pass `synthesize_full(...)`.
- [x] Run focused duration reconcile tests.

### Task 2: Frontend Rendering

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_translate_detail_shell_templates.py`

- [x] Add live progress renderer for `sentence_reconcile`.
- [x] Filter `initial_audio_gen` out of sentence rows.
- [x] Add labels for `rewrite_start` and `tts_regen_start`.
- [x] Replace compact attempt text with readable attempt tables/cards.
- [x] Add scoped CSS for progress and attempt tables/cards.

### Task 3: Verification And Release

**Files:**
- Test-only and release commands.

- [x] Run focused tests for duration reconcile and task detail templates.
- [x] Run relevant Omni/runtime regression tests.
- [ ] Commit with `Docs-anchor`.
- [ ] Push to `master`.
- [ ] Deploy test and production services, then verify service active plus HTTP 200/302.
