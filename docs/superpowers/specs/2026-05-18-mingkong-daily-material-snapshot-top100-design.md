# Mingkong Daily Material Snapshot Top100 Design

Last updated: 2026-05-19

## Context

`/xuanpin/mk` already has a live Mingkong video material subtab. That subtab queries
`/api/marketing/medias` when the operator opens the page, so it is useful for browsing,
but it cannot answer daily questions such as:

- Which videos consumed the most yesterday?
- Which videos newly entered yesterday's Top100?
- How much did each video's Mingkong 90-day spend increase from the previous snapshot?

Operations need a local historical material library for the latest Dianxiaomi Listing
Top300 products. The library must archive daily Mingkong material state and derive a
daily "yesterday spend Top100" list from the difference between consecutive 90-day spend
snapshots.

## Scope

Build a scheduled, local, historical Mingkong material library for `/xuanpin/mk`.

In scope:

1. Every day at 06:00 and 18:00 Beijing time, start one long-running sync run.
2. Select the latest available Dianxiaomi Listing snapshot and take `rank_position <= 300`.
3. For each selected product, derive the product code/handle from `product_url`.
4. Query Mingkong `/api/marketing/medias?q=<product_code>` and store the full visible
   video material list for that product.
   A Mingkong search-result product is a match only when its Mingkong-side
   `product_code`/`code`/`handle` or product-link tail exactly equals the requested
   product code. Search-result links ending in `-rjc` must not be normalized into
   a match for the base code.
5. Store daily per-material snapshots of the Mingkong cumulative 90-day spend value.
6. Compare the current snapshot with the previous-date material snapshot whose timestamp
   is closest to 24 hours earlier, then compute the latest one-day spend delta.
7. Persist a daily "昨天消耗前100" result table.
8. Change the `/xuanpin/mk` `视频素材库` inner tab so clicking it shows the latest
   locally archived material card list, including localized cover/video preview URLs and
   stored spend data. It must not depend on a live Mingkong request at click time.
9. Add a `/xuanpin/mk` `昨天消耗前100` inner tab that reads the persisted Top100
   result instead of calling Mingkong live.

Out of scope:

- Do not remove the existing Mingkong media proxy endpoints; the local cards still use
  them for previewing stored Mingkong paths when local media objects are absent.
- Do not add LLM ranking or subjective material scoring.
- Do not connect to Windows local MySQL for verification. Database checks must run on
  the server/test environment according to project rules.

## Source Of Top300 Products

The product source is the latest locally archived Dianxiaomi Listing ranking:

```sql
SELECT MAX(snapshot_date) FROM dianxiaomi_rankings;
```

The run uses that latest available `snapshot_date`, even if it is not the same calendar
date as the 06:00 run date. It then selects:

```sql
WHERE snapshot_date = <latest_snapshot_date>
ORDER BY rank_position ASC
LIMIT 300
```

The run records both:

- `snapshot_date`: the local material snapshot date being produced.
- `snapshot_at`: the local material snapshot timestamp for the run.
- `snapshot_slot`: the daily bucket, `0600` or `1800`, used to keep two snapshots per day.
- `ranking_snapshot_date`: the Dianxiaomi Listing snapshot used as the Top300 source.

## Scheduler

Register one scheduled task in `appcore/scheduled_tasks.py`.

- Code: `mingkong_material_daily_snapshot`
- Name: `明空素材每日快照`
- Schedule: every day at 06:00 and 18:00 Beijing time.
- Source type: `systemd`.
- Source ref: `autovideosrt-mingkong-material-daily-snapshot.timer`.
- Runner: `tools/mingkong_material_daily_snapshot.py`.
- Log table: `scheduled_task_runs`.

The job is two scheduled daily runs, not a task that wakes every 10 minutes all day.

Expected runtime is about 5 hours for 300 products.

The runner behavior:

1. Start a `scheduled_task_runs` row.
2. Load the latest Dianxiaomi Top300 queue.
3. Process products in internal batches of 10.
4. After every 1-2 products, sleep 30 seconds.
5. Continue until the 300-product queue is finished.
6. Finalize material snapshots and generate the Top100 archive.
7. Finish the run with summary counters.

If a previous run for the same `snapshot_date + snapshot_slot` is already complete,
a new automatic run must not duplicate the work. Manual repair is out of scope for this
change; the first version relies on safe idempotent upserts and clear run logs.

## Mingkong Fetch And Matching

For each Dianxiaomi row:

1. Parse the Shopify handle from `product_url` path segment after `/products/`.
2. Strip a trailing `-rjc` or `_rjc` for search/matching.
3. Request:

```text
GET <wedev_base_url>/api/marketing/medias?page=1&q=<handle>&source=&level=&show_attention=0
```

4. Use existing synced wedev credentials from `pushes.build_localized_texts_headers()`.
5. Treat `is_guest=true` or login-expired messages as authentication failure for the run.
6. Pick matching Mingkong products using the existing live-material rules:
   - exact product link tail match first;
   - then higher visible-video total spend;
   - then higher ad count;
   - then newer Mingkong product id.
7. Store all visible videos from the matched Mingkong product, not only the first few.

Hidden videos and videos without a normalized path are skipped.

## Material Identity

A stable material key is required for daily diffing. The key should be deterministic and
not depend on local database ids:

```text
sha256(product_code + "|" + mk_product_id + "|" + normalized_video_path)
```

If `mk_product_id` is absent, use an empty string in that position and still include the
normalized video path. The normalized video path is the primary video identity.

## Data Model

Add migrations for these tables.

### `mingkong_material_sync_runs`

Tracks one daily Top300 sync.

Required fields:

- `id`
- `snapshot_date`
- `snapshot_at`
- `snapshot_slot`
- `ranking_snapshot_date`
- `status`
- `source_product_limit`
- `source_product_count`
- `processed_product_count`
- `material_count`
- `failed_product_count`
- `summary_json`
- `error_message`
- `started_at`
- `finished_at`

Unique key:

- `uk_snapshot_slot (snapshot_date, snapshot_slot)`

### `mingkong_material_products`

Stores per-run product processing state.

Required fields:

- `id`
- `run_id`
- `snapshot_date`
- `snapshot_at`
- `snapshot_slot`
- `ranking_snapshot_date`
- `rank_position`
- `product_code`
- `shopify_product_id`
- `product_name`
- `product_url`
- `store`
- `sales_count`
- `order_count`
- `revenue_main`
- `mk_product_id`
- `mk_product_name`
- `mk_product_link`
- `status`
- `material_count`
- `error_message`
- `processed_at`

Unique key:

- `uk_run_product (run_id, product_code)`

### `mingkong_material_daily_snapshots`

Stores one row per visible Mingkong video per material snapshot date.

Required fields:

- `id`
- `snapshot_date`
- `snapshot_at`
- `snapshot_slot`
- `ranking_snapshot_date`
- `run_id`
- `material_key`
- `product_code`
- `rank_position`
- `shopify_product_id`
- `product_name`
- `product_url`
- `mk_product_id`
- `mk_product_name`
- `mk_product_link`
- `main_image`
- `video_name`
- `video_path`
- `video_image_path`
- `cumulative_90_spend`
- `video_ads_count`
- `video_author`
- `video_upload_time`
- `video_duration_seconds`
- `mk_video_metadata_json`
- `created_at`
- `updated_at`

`mk_video_metadata_json` must preserve the raw Mingkong `spends` text (for example
`3.05万` or `1.50千`). Read paths may use that raw value to recover display and
ranking values if an older snapshot row has `cumulative_90_spend = 0` because of
an earlier parser bug.

Unique key:

- `uk_snapshot_material (snapshot_at, material_key)`

Indexes:

- `(snapshot_at, cumulative_90_spend)`
- `(product_code, snapshot_at)`
- `(material_key, snapshot_at)`

### `mingkong_material_daily_top100`

Stores the archived "昨天消耗前100" result for each snapshot date.

Required fields:

- `id`
- `snapshot_date`
- `snapshot_at`
- `snapshot_slot`
- `previous_snapshot_date`
- `previous_snapshot_at`
- `previous_snapshot_slot`
- `comparison_interval_seconds`
- `ranking_snapshot_date`
- `rank_position`
- `display_position`
- `material_key`
- `product_code`
- `source_product_rank_position`
- `shopify_product_id`
- `product_name`
- `product_url`
- `mk_product_id`
- `mk_product_name`
- `mk_product_link`
- `main_image`
- `video_name`
- `video_path`
- `video_image_path`
- `previous_cumulative_90_spend`
- `current_cumulative_90_spend`
- `yesterday_spend_delta`
- `video_ads_count`
- `is_new_material`
- `is_new_top100_entry`
- `created_at`

Unique key:

- `uk_snapshot_material (snapshot_at, material_key)`

Indexes:

- `(snapshot_at, display_position)`
- `(snapshot_at, yesterday_spend_delta)`
- `(material_key, snapshot_at)`

## Delta Calculation

After each 06:00 or 18:00 material snapshot is stored:

1. Find previous snapshot candidates from `mingkong_material_daily_snapshots` where
   `snapshot_date < current_snapshot_date`.
2. Choose the previous candidate whose `snapshot_at` is closest to exactly 24 hours
   before the current `snapshot_at`. For example, an 18:00 current snapshot should prefer
   the previous day's 18:00 snapshot over the previous day's 06:00 snapshot.
3. Store the chosen previous snapshot in Top100 rows as `previous_snapshot_date`,
   `previous_snapshot_at`, `previous_snapshot_slot`, and `comparison_interval_seconds`.
4. Join current rows to previous rows by `material_key`.
5. Compute:

```text
yesterday_spend_delta =
  max(0, current.cumulative_90_spend - previous.cumulative_90_spend)
```

6. If no previous row exists for the material:
   - `previous_cumulative_90_spend = NULL`
   - `is_new_material = 1`
   - `yesterday_spend_delta = current.cumulative_90_spend` for ranking purposes
7. If the raw difference is negative, clamp to `0` and keep enough summary data for
   operators to notice possible upstream reset behavior.
8. Take the Top100 by `yesterday_spend_delta DESC`, tie-breaking by current cumulative
   spend, ads count, product rank, and material key.

## New Top100 Entry Calculation

`is_new_top100_entry` compares Top100 membership with the previous archived Top100:

- Current Top100 contains `material_key`.
- Previous Top100 for the chosen `previous_snapshot_at` does not contain `material_key`.

This is different from `is_new_material`. A video can be old in the material library but
new to the daily Top100.

## Display Sorting

The `/xuanpin/mk` new inner tab is named:

```text
昨天消耗前100
```

It reads the latest archived Top100 by default.

Display order:

1. `is_new_top100_entry = 1` first.
2. Then `yesterday_spend_delta DESC`.
3. Then `current_cumulative_90_spend DESC`.
4. Then `video_ads_count DESC`.
5. Then original Top100 `rank_position ASC`.

The persisted `rank_position` keeps the pure spend-delta rank. `display_position` is
stored as the UI order after new-entry prioritization.

## API And UI

Add an admin-only API for the local archived `视频素材库` tab:

```text
GET /xuanpin/api/mk-material-library
```

Query parameters:

- `snapshot`: optional snapshot date; default latest material snapshot date.
- `snapshot_at`: optional exact snapshot timestamp; default latest material snapshot time.
- `page`: default `1`.
- `page_size`: default `100`, max `100`.
- `keyword`: optional product/material search term.

Response fields:

- `items`
- `snapshot`
- `snapshot_at`
- `total`
- `run_summary`

Add an admin-only API for the archived daily Top100:

```text
GET /xuanpin/api/mk-yesterday-top100
```

Query parameters:

- `snapshot`: optional snapshot date; default latest archived Top100 date.
- `snapshot_at`: optional exact snapshot timestamp; default latest archived Top100 time.
- `page`: default `1`.
- `page_size`: default `100`, max `100`.

Response fields:

- `items`
- `snapshot`
- `snapshot_at`
- `previous_snapshot`
- `previous_snapshot_at`
- `total`
- `run_summary`

The page changes stay inside `mk_selection.html`:

- Keep `产品库`.
- Change `视频素材库` to read local archived snapshot rows and render localized cover
  and data video cards when clicked.
- Add `昨天消耗前100`.
- Both material card tabs use local archived rows only.
- Cards reuse the existing Mingkong media proxy paths for cover/video preview.
- Existing `加入素材库` and `做小语种` actions are available when metadata is sufficient.

## Error Handling

- Missing wedev credentials: fail the run with a clear error in `scheduled_task_runs`.
- Expired Mingkong login: fail fast so operators refresh credentials.
- Per-product request failure: record product status failed, increment failure count, and
  continue unless failures exceed a conservative threshold such as 50 consecutive failures.
- Missing product handle: record skipped product state.
- Empty Mingkong match: record no-match product state.
- No previous snapshot: generate Top100 from current cumulative spend, mark entries as new.

## Verification

Use TDD for implementation.

Focused automated checks:

- Migration tests assert all four tables and key indexes exist.
- Service tests cover latest Dianxiaomi Top300 selection from `dianxiaomi_rankings`.
- Service tests cover Mingkong fetch flattening and upsert of all visible videos.
- Service tests cover delta calculation, new-material handling, negative-delta clamp, and
  new Top100 membership.
- Scheduler tests cover 06:00/18:00 registration and scheduled task registry metadata.
- Route tests cover `/xuanpin/api/mk-material-library` local archived material listing.
- Route tests cover `/xuanpin/api/mk-yesterday-top100` admin behavior.
- Template tests cover local `视频素材库` behavior and the new `昨天消耗前100` inner tab.

Manual/server verification:

- Do not use Windows local MySQL.
- Run DB migration and focused pytest on the server/test environment.
- Start the dev server and verify unauthenticated `/xuanpin/mk` returns 302.
- Log in as admin and verify `/xuanpin/mk` returns 200 and the new tab reads archived data.
