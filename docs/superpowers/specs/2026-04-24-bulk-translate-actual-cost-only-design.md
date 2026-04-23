# Bulk Translate Actual-Cost-Only Design

**Date:** 2026-04-24

## Goal

Remove all pre-run cost estimation from bulk translation task creation flows. Bulk translation tasks should only record and display actual cost after child tasks complete successfully.

## Problem

The current implementation computes `estimate` during task creation and surfaces it in both the legacy bulk translate UI and the medias translation modal. This has two issues:

1. The estimate is often inaccurate for the current raw-source-driven orchestration.
2. The estimate path is now on the request critical path, so stale SQL assumptions can fail task creation with HTTP 500.

The user expectation is simpler: create the task immediately, execute it, and show the actual cost once work has really completed.

## Non-Goals

- Do not redesign the actual-cost formula in this change.
- Do not change child-task pricing rules beyond using the existing roll-up logic.
- Do not refactor unrelated bulk translate orchestration or projection structures.

## Behavioral Changes

### 1. Task creation no longer estimates cost

- `appcore.bulk_translate_runtime.create_bulk_translate_task()` must stop calling the estimator.
- The task audit trail must stop writing `estimated_cost_cny` on create.
- New tasks must initialize `cost_tracking.actual` only.

### 2. Pre-run estimate API is removed as a pricing source

- `POST /api/bulk-translate/estimate` should no longer return calculated pricing.
- The endpoint should remain callable for compatibility, but respond with a stable, explicit "estimate disabled" payload rather than running estimator logic.
- No request path that creates a task may depend on the estimator.

### 3. UI no longer shows estimate before execution

- The medias translation modal should create tasks directly without estimate or estimate-based confirmation text.
- The legacy bulk translate dialog should stop auto-estimating and stop asking users to confirm based on estimated price.
- Task detail/list/admin views should show actual cost only. Before completion, the UI should communicate that actual cost is generated after successful completion.

### 4. Actual cost remains post-facto

- Existing `_roll_up_cost()` behavior remains the source of truth for actual pricing.
- Actual cost should continue to increase only when successful child-task results are synced.
- Failed, cancelled, or not-yet-finished work must not produce a final price.

## Data Contract

`state.cost_tracking` should be treated as:

```json
{
  "actual": {
    "copy_tokens_used": 0,
    "image_processed": 0,
    "video_minutes_processed": 0.0,
    "actual_cost_cny": 0.0
  }
}
```

Backward compatibility:

- Readers must tolerate missing `estimate`.
- Existing historical tasks that still contain `estimate` should continue rendering safely, but new rendering should not rely on it.

## Files In Scope

- `appcore/bulk_translate_runtime.py`
- `web/routes/bulk_translate.py`
- `appcore/bulk_translate_projection.py`
- `web/static/medias_translate_modal.js`
- `web/static/bulk_translate_ui.js`
- `web/static/bulk_translate_detail.js`
- Related route/runtime/UI tests

## Verification Strategy

- Route tests prove task creation no longer depends on estimation.
- Runtime tests prove new task state stores only actual cost tracking.
- UI-facing tests prove detail/list rendering no longer expects estimate text.
- Focused pytest suite around medias raw-source translation and bulk translate runtime/routes must stay green.
