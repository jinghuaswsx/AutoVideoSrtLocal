# Pushes List Fast Path Design

Date: 2026-05-22

## Context

The `/pushes/api/items` list endpoint is slow when filtering by `status=pending`.
The route currently loads every SQL candidate, computes dynamic readiness/status for
each row, then filters and paginates in memory. On production data, the first page
returns 20 rows but serializes hundreds of candidates first.

## Scope

This fast path is a short-term production fix before the durable cache-table design.
It keeps dynamic status semantics unchanged and avoids schema changes.

## Changes

1. Do not include `quality_check` in list API rows.
   The push modal already calls `/pushes/api/items/<id>/payload`, and that payload
   response still includes the latest quality check.

2. Reuse readiness when computing status.
   Add a status helper that accepts precomputed readiness so each row does not call
   `compute_readiness()` twice.

3. Batch-prefetch list dependencies for candidate rows.
   For the current candidate set, load copywriting presence, valid English push
   text presence, failed latest push logs, and active push-rework overrides in bulk.
   Serialization then reads these maps instead of issuing per-row queries.

## Non-Goals

- No cache table in this quick fix.
- No database schema migration.
- No change to push payload, quality-check retry, or push writeback behavior.

## Verification

- No-DB route tests cover the list API no longer loading quality checks.
- Unit tests cover readiness reuse and bulk prefetch behavior.
- Production verification uses the logged-in `/pushes/api/items?status=pending...`
  response time before and after deployment.
