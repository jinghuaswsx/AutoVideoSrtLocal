# 2026-06-12 Bulk Translate Manual Success Backfill Fallback

## Context

Product translation task pages can show a parent bulk-translate item as failed even when the child translation task has completed successfully. One observed production case was product `574`: the `omni_translate` child task finished, but parent backfill failed while updating an existing `media_items` row because the backfill SQL wrote a column that is not present in the production table.

This breaks both automatic backfill and the existing "rebackfill" action, because both paths share the same result-sync function.

## Decision

Bulk translation must have a manual success fallback for task items whose child task has actually completed:

1. Each child-backed task item that is no longer active may expose a "manual confirm success and backfill" action.
2. The action must reload the child task result and run the normal backfill path first.
3. The parent item may be marked `done` only after the backfill succeeds.
4. If backfill fails, the item remains failed/interrupted and the operator sees the real error.
5. The old trailing reset/retry/rebackfill buttons are not the operator-facing fallback. The UI should present one explicit manual success action instead of asking the operator to reset successful child work.

## Guardrail

Backfill code must not assume optional `media_items` columns are present. In particular, updating an existing media item must avoid schema-drift-only fields that are not required to bind the translated asset back to the product/task.

This guardrail is intentionally code-level and test-covered, because a database-only fix would not prevent the same class of failure when future backfill code writes another optional column.

## Acceptance

- A failed parent item whose child task is completed can be manually confirmed as successful.
- Manual confirmation backfills the translated asset to the product/material library before changing parent item status.
- The parent item records an audit event for manual confirmation.
- The task page no longer shows the trailing reset/rebackfill-style button for this fallback.
- Focused tests cover the SQL guardrail, runtime success/failure behavior, route delegation, and task-page UI action.
