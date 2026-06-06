# Pushes New Product Tag Design

Date: 2026-06-01

## Context

The push management list is operated at `media_items` granularity, but operators
need a product-level visual cue for products that have never been pushed before.
This helps prioritize first launches without changing the push state machine.

## Goal

Add a table column to `/pushes` that shows a large blue `新品` tag when the row's
product has no successful push history. Existing products remain unmarked.

## Product-Level Rule

A product is new when no item under the same `product_id` has any
`media_push_logs.status = 'success'` row.

This rule intentionally ignores the current row's `pushed_at/latest_push_id`
alone, because another language or older item for the same product may already
have been pushed successfully.

## Backend

- `appcore.pushes.list_items_for_push()` selects a product-level boolean
  `is_new_product_for_push`.
- The boolean is computed with a correlated `NOT EXISTS` over `media_items` and
  `media_push_logs`, scoped to the same product and successful push logs.
- `web.routes.pushes._serialize_row()` includes the boolean in the list API
  response.
- No schema change and no cache invalidation change are needed because the list
  query already joins product/item source state and push logs are durable history.

## Frontend

- `web/templates/pushes_list.html` adds a `标签` column after `产品`.
- `web/static/pushes.js` renders a `新品` badge only when
  `it.is_new_product_for_push` is true; otherwise the cell is empty.
- `web/static/pushes.css` gives the column a stable width and styles the badge as
  roughly double-size blue text without making old products noisy.

## New Product Filter

- `/pushes` adds a `新品标签` select in the filter toolbar with options
  `全部` / `新品` / `非新品`; the default is `全部`.
- The filter is persisted in the URL as `new_product`:
  - empty or missing: all rows
  - `1`: only rows whose product is new for push
  - `0`: only rows whose product is not new for push
- `/pushes/api/items` validates the same values and passes a boolean product-new
  filter to `appcore.pushes.list_items_for_push()`.
- `appcore.pushes.list_items_for_push()` applies the filter with the same
  product-level successful-push rule used by `is_new_product_for_push`, so the
  badge and filter cannot diverge.

## Verification

- Unit/static tests cover the SQL field, serialized API field, template column,
  JavaScript row rendering, CSS badge class, and `新品标签` filter persistence.
- Tests must avoid local Windows MySQL. DB-backed tests can only run on the
  documented test or server environment.
