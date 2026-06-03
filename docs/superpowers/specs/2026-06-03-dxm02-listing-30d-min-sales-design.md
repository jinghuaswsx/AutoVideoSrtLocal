# DXM02 Listing 30-Day Minimum Sales Design

Last updated: 2026-06-03

## Goal

DXM02-MK is the Dianxiaomi account for Mingkong selection. Its Listing archive should collect all listings with 30-day `paidProductCount > 10`, and Mingkong material video-spend sync should use that latest archived source set.

## Current State

- `tools/dianxiaomi_listing_ranking_sync.py` collects Dianxiaomi Listing sales through DXM02-MK CDP.
- The scheduled unit currently uses a 7-day window, rolling 7 snapshot dates, and stores only Top500 rows.
- `appcore.mingkong_materials.latest_top_products()` reads the latest `dianxiaomi_rankings` snapshot and defaults to 500 rows.
- `tools/mingkong_material_daily_snapshot.py` defaults `source_limit=500`, so downstream Mingkong material sync is capped even if the archive contains more rows.

## New Source Definition

- Snapshot window: 30 days ending on `snapshot_date`.
- Included rows: rows with `product_id` and `sales_count > 10`.
- Row cap: none by default. `target_rows=0` remains the uncapped mode.
- Ranking order remains Dianxiaomi's `paidProductCount` descending order.
- Snapshot date should be the current day for the daily systemd job.

## Implementation

- Add a minimum sales filter to `collect_top_rankings_for_date`, defaulting to `DEFAULT_MIN_SALES_COUNT = 10`.
- Set `DEFAULT_SNAPSHOT_WINDOW_DAYS = 30` and `DEFAULT_TARGET_ROWS = 0` for the DXM02 Listing archive job.
- Update CLI and service arguments to use `--mode daily --daily-offset-days 0 --snapshot-window-days 30 --target-rows 0 --min-sales-count 10`.
- Update scheduled task descriptions from "near 7 days Top500" to "near 30 days sales greater than 10".
- Change Mingkong material source loading so the default source limit is uncapped, while still allowing explicit small limits for manual/debug runs.

## Verification

- Unit tests cover the 30-day payload window, minimum sales filtering, uncapped default collection, systemd arguments, scheduled task metadata, and Mingkong source loading.
- No Windows local MySQL checks are used. Database-sensitive behavior is tested through monkeypatches.
