# Multi AV Sync Table-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multi-translate audio/video sync audit show only one ASR-ordered review table: ASR text, final translation/TTS, actual video visuals, and per-segment sync diagnosis.

**Architecture:** Keep the existing `omni_av_sync_audit.run_report_only()` data path for multi-translate, but narrow its LLM contract to table assembly instead of a full audit report. The shared workbench renderer uses the same table-only mode for multi-translate `analysis_only` reports and Omni `report_only` AV sync artifacts. Safe-auto internals can still keep issue/verification data, but report-only display is the ASR table.

**Tech Stack:** Python 3.12, Flask task artifacts, Jinja/vanilla JS workbench renderer, pytest.

---

### Task 1: Lock Multi-Translate Table Contract

**Files:**
- Modify: `tests/test_omni_av_sync_audit.py`
- Modify: `pipeline/omni_av_sync_audit.py`

- [ ] **Step 1: Write failing backend tests**

Add assertions that `run_report_only()` stores `audit_timeline` rows with only the user-facing table fields needed by multi-translate: `asr_text`, `target_text`, and `visual_observation`, and that the Gemini assess prompt says not to output summaries, recommendations, or issue lists.

- [ ] **Step 2: Run the focused tests**

Run: `pytest tests/test_omni_av_sync_audit.py::test_report_only_builds_asr_ordered_audit_timeline_with_visual_context -q`

- [ ] **Step 3: Implement multi-specific assess prompt/schema**

In `pipeline/omni_av_sync_audit.py`, branch on `cfg["project_type"] == "multi_translate"` so the assess prompt asks only for a `timeline` array and does not synthesize fallback `issues` from program candidates.

- [ ] **Step 4: Re-run focused backend tests**

Run: `pytest tests/test_omni_av_sync_audit.py::test_report_only_builds_asr_ordered_audit_timeline_with_visual_context -q`

### Task 2: Render Only The Table For Multi-Translate

**Files:**
- Modify: `tests/test_prompt_inspector_assets.py`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`

- [ ] **Step 1: Write failing frontend asset tests**

Assert that the AV sync renderer has a multi table-only branch, that table-only rows render `ASR 内容`, `正常翻译/TTS`, `视频画面`, and `问题诊断`, and that the table-only branch does not append `中文审计结论`, `诊断问题`, `复核通过问题`, `修正记录`, or `完整审计 JSON`.

- [ ] **Step 2: Run frontend asset tests**

Run: `pytest tests/test_prompt_inspector_assets.py -q`

- [ ] **Step 3: Implement table-only rendering**

Add `isAvSyncTableOnlyReport()` and pass a `tableOnly` flag into `renderAvSyncAuditTimelineRow()`. For table-only mode, render only the requested table fields plus the ASR/time heading.

### Task 2b: Sync Table-Only Report Mode To Omni

**Files:**
- Modify: `tests/test_omni_av_sync_audit.py`
- Modify: `tests/test_prompt_inspector_assets.py`
- Modify: `pipeline/omni_av_sync_audit.py`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: Write failing Omni tests**

Assert that Omni `report_only` audit rows preserve per-segment `sync_score` and `recommendation` from the model timeline, and that the workbench table-only predicate includes `artifact.mode === "report_only"` with an `audit_timeline`.

- [ ] **Step 2: Implement shared table-only behavior**

Extend the Omni assess schema/prompt to request `sync_score` and `recommendation` in timeline rows, merge those timeline hints into the final `audit_timeline`, and let `report_only` artifacts render through the same table-only branch.

- [ ] **Step 4: Re-run frontend asset tests**

Run: `pytest tests/test_prompt_inspector_assets.py -q`

### Task 3: Verify And Release

**Files:**
- No new files beyond the edits above.

- [ ] **Step 1: Run target tests**

Run: `pytest tests/test_omni_av_sync_audit.py tests/test_prompt_inspector_assets.py tests/test_runtime_multi_translate.py::test_step_av_sync_audit_uses_composed_hard_video tests/test_runtime_multi_translate.py::test_step_av_sync_audit_skips_when_composed_video_missing -q`

- [ ] **Step 2: Run syntax/check verification**

Run: `python -m compileall -q pipeline\omni_av_sync_audit.py`

Run: `git diff --check`

- [ ] **Step 3: Commit, push to master, deploy test and prod**

Push the branch to `master`, then deploy `/opt/autovideosrt-test` and `/opt/autovideosrt`, restart both services, and verify `active` plus HTTP `200/302`.
