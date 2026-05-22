# Pushes Status Cache Design

Date: 2026-05-22

## Context

The first fast-path reduced `/pushes/api/items` latency by removing list-time
quality checks, reusing readiness, and batch-prefetching dynamic status inputs.
The endpoint still computes status/readiness for the whole candidate set whenever
it needs to filter by status.

## Goal

Materialize each push item's computed `status` and `readiness` into a database
cache table so list requests usually read one cache map instead of recomputing
dynamic state.

## Schema

Add `media_push_status_cache`:

- `item_id` primary key.
- Snapshot columns for common filters/debugging: `product_id`, `task_id`, `lang`,
  `latest_push_id`, `pushed_at`, `skip_push`.
- `status` and `readiness_json` as the materialized UI state.
- `cache_version`, `computed_at`, `created_at`, `updated_at`.
- Indexes on `status`, `(lang, status)`, `product_id`, and `computed_at`.

## Refresh Model

- APScheduler job `push_status_cache_refresh` refreshes all current push
  candidates every 2 minutes.
- The list API calls `status_cache_for_rows(rows)`, which loads cache rows in
  bulk and refreshes missing or stale rows in one batch.
- Push write operations (`success`, `failure`, `reset`, `skip`, `unskip`) refresh
  the affected item immediately after changing source state.

## Request Path

1. Query the same candidate rows as today.
2. Load cached status/readiness for all candidate item IDs.
3. Serialize rows from cache when available.
4. Fall back to dynamic batch computation only for missing/stale cache rows.
5. Apply status filter in memory using cached statuses.

This keeps behavior compatible while making the common path stable and cheap.

## Non-Goals

- No semantic change to readiness/status rules.
- No quality-check cache in list rows.
- No SQL-level status filtering in this phase; that can be a later optimization
  once cache warm-up and invalidation are proven in production.
