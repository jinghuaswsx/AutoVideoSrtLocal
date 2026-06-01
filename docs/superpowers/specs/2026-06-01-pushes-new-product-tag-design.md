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

## Verification

- Unit/static tests cover the SQL field, serialized API field, template column,
  JavaScript row rendering, and CSS badge class.
- Tests must avoid local Windows MySQL. DB-backed tests can only run on the
  documented test or server environment.
