# Omni AV Sync Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Omni-only AV sync audit step that lets Doubao diagnose candidate audio/visual sync issues, Gemini verify them, and optionally apply bounded sentence-level fixes before subtitle and compose.

**Architecture:** The feature is controlled by `plugin_config.av_sync_audit`, defaults to `off`, and only auto-applies fixes on the existing sentence-level chain: `av_sentence + sentence_reconcile + sentence_units`. The new pipeline module writes an artifact for every run; failed model calls or unsafe fixes degrade to report-only and never block Omni composition.

**Tech Stack:** Python, pytest, existing `appcore.task_state`, `appcore.llm_client`, Omni runtime dispatch, existing AV helper functions, OpenAI-compatible OpenRouter/Doubao adapters.

---

## Docs Anchor

- Spec: `docs/superpowers/specs/2026-05-07-omni-av-sync-audit-design.md`
- Existing Omni architecture: `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`
- Existing sentence sync constraints: `docs/superpowers/specs/2026-04-28-av-sync-v2-sentence-convergence-design.md`
- Doubao model entry: `docs/superpowers/specs/2026-05-06-doubao-seed-2-lite-design.md`

## File Map

- Modify `appcore/omni_plugin_config.py`: add `av_sync_audit` metadata, default, valid values, and safe-auto downgrade.
- Modify `appcore/runtime_omni.py`: insert `av_sync_audit` after `tts` and before `loudness_match` / `subtitle`; add runner shim.
- Modify `appcore/llm_use_cases.py`: register `omni_av_sync.diagnose` and `omni_av_sync.verify`.
- Modify `appcore/llm_providers/openrouter_adapter.py`: add Doubao `generate(media=...)` support through public temporary URLs.
- Create `pipeline/omni_av_sync_audit.py`: collect task inputs, call diagnose/verify, build report, and apply bounded fixes.
- Modify tests in `tests/test_omni_plugin_config.py`, `tests/test_runtime_omni_dispatch.py`, `tests/test_llm_use_cases_registry.py`, `tests/test_llm_providers_openrouter.py`.
- Create `tests/test_omni_av_sync_audit.py`.

## Task 1: Config Gate

**Files:**
- Modify: `tests/test_omni_plugin_config.py`
- Modify: `appcore/omni_plugin_config.py`

- [ ] **Step 1: Write failing tests**

Add assertions that `CAPABILITY_GROUPS` has 9 entries, `DEFAULT_PLUGIN_CONFIG["av_sync_audit"] == "off"`, valid values include `off/report_only/safe_auto`, and `safe_auto` downgrades to `report_only` unless the sentence-level chain is active.

- [ ] **Step 2: Verify red**

Run:

```bash
pytest tests/test_omni_plugin_config.py -q
```

Expected: failures mention missing `av_sync_audit` and old group count.

- [ ] **Step 3: Implement config**

Add a ninth capability group labelled `⑨ 音画同步审计`; add `"av_sync_audit": {"off", "report_only", "safe_auto"}` to radio validation; keep default `off`; after existing silent fixes, downgrade unsafe `safe_auto` to `report_only`.

- [ ] **Step 4: Verify green**

Run:

```bash
pytest tests/test_omni_plugin_config.py -q
```

Expected: all tests in this file pass.

## Task 2: Runtime Step Dispatch

**Files:**
- Modify: `tests/test_runtime_omni_dispatch.py`
- Modify: `appcore/runtime_omni.py`

- [ ] **Step 1: Write failing tests**

Add tests that a config with `av_sync_audit="report_only"` inserts the step after `tts` and before `loudness_match`, and a config with `off` keeps the existing step list unchanged.

- [ ] **Step 2: Verify red**

Run:

```bash
pytest tests/test_runtime_omni_dispatch.py::test_pipeline_inserts_av_sync_audit_after_tts_when_enabled -q
```

Expected: failure because the step does not exist.

- [ ] **Step 3: Implement runtime shim**

Insert `("av_sync_audit", lambda: self._step_av_sync_audit(task_id, video_path, task_dir))` after `tts` when config value is not `off`. Implement `_step_av_sync_audit` as a thin import/call into `pipeline.omni_av_sync_audit.run`.

- [ ] **Step 4: Verify green**

Run:

```bash
pytest tests/test_runtime_omni_dispatch.py -q
```

Expected: dispatch tests pass.

## Task 3: Use Cases And Doubao Generate

**Files:**
- Modify: `tests/test_llm_use_cases_registry.py`
- Modify: `tests/test_llm_providers_openrouter.py`
- Modify: `appcore/llm_use_cases.py`
- Modify: `appcore/llm_providers/openrouter_adapter.py`

- [ ] **Step 1: Write failing tests**

Add registry assertions for `omni_av_sync.diagnose` and `omni_av_sync.verify`. Add a Doubao generate adapter test proving video media is converted to a temporary public URL payload and schema mode parses JSON text.

- [ ] **Step 2: Verify red**

Run:

```bash
pytest tests/test_llm_use_cases_registry.py tests/test_llm_providers_openrouter.py -q
```

Expected: failures mention missing use cases and missing `DoubaoAdapter.generate`.

- [ ] **Step 3: Implement use cases and adapter**

Register diagnose as `doubao / doubao-seed-2-0-lite-260215 / doubao / tokens`; register verify as `openrouter / google/gemini-3-flash-preview / openrouter / tokens`. Implement Doubao generate with Ark Responses API content item types `input_text`, `input_image`, `input_video`; upload local media via `pipeline.storage.upload_file`; parse JSON when `response_schema` is present.

- [ ] **Step 4: Verify green**

Run:

```bash
pytest tests/test_llm_use_cases_registry.py tests/test_llm_providers_openrouter.py -q
```

Expected: use case and provider tests pass.

## Task 4: Audit Report And Safe Fixes

**Files:**
- Create: `tests/test_omni_av_sync_audit.py`
- Create: `pipeline/omni_av_sync_audit.py`

- [ ] **Step 1: Write failing tests**

Cover four behaviors: missing AV sentences writes `skipped_missing_av_sentences`; `report_only` stores diagnosis and verification without sentence mutation; `safe_auto` applies only Gemini-accepted medium/high issues within the count cap; a regenerated sentence that worsens duration ratio is rolled back.

- [ ] **Step 2: Verify red**

Run:

```bash
pytest tests/test_omni_av_sync_audit.py -q
```

Expected: import failure for the new module.

- [ ] **Step 3: Implement audit module**

Implement `run(runner, task_id, video_path, task_dir)`. The module reads state, normalizes AV sentences, calls `llm_client.invoke_generate("omni_av_sync.diagnose", media=[video_path])`, calls `llm_client.invoke_chat("omni_av_sync.verify")`, writes `task.artifacts["av_sync_audit"]` and `variants["av"]["av_sync_audit"]`, and applies only bounded single-sentence fixes when mode is `safe_auto`.

- [ ] **Step 4: Verify green**

Run:

```bash
pytest tests/test_omni_av_sync_audit.py -q
```

Expected: audit tests pass.

## Task 5: Focused Regression

**Files:**
- All modified files above.

- [ ] **Step 1: Run focused suite**

Run:

```bash
pytest tests/test_omni_plugin_config.py tests/test_runtime_omni_dispatch.py tests/test_llm_use_cases_registry.py tests/test_llm_client_invoke.py tests/test_llm_providers_openrouter.py tests/test_omni_av_sync_audit.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Inspect diff**

Run:

```bash
git diff -- docs/superpowers/specs/2026-05-07-omni-av-sync-audit-design.md docs/superpowers/plans/2026-05-07-omni-av-sync-audit.md appcore/omni_plugin_config.py appcore/runtime_omni.py appcore/llm_use_cases.py appcore/llm_providers/openrouter_adapter.py pipeline/omni_av_sync_audit.py tests/test_omni_plugin_config.py tests/test_runtime_omni_dispatch.py tests/test_llm_use_cases_registry.py tests/test_llm_providers_openrouter.py tests/test_omni_av_sync_audit.py
```

Expected: diff contains no unrelated edits and no production changes outside the planned Omni/LLM files.

## Self Review

- Spec coverage: every v0.1 requirement in `2026-05-07-omni-av-sync-audit-design.md` maps to a task above.
- Placeholder scan: no `TBD`, `TODO`, or implementation-free task remains in this plan.
- Type consistency: all config field names use `av_sync_audit`; modes use `off`, `report_only`, `safe_auto`; use cases use `omni_av_sync.diagnose` and `omni_av_sync.verify`.
