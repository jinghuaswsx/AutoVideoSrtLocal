# DXM02 Listing 30-Day Minimum Sales Design

Last updated: 2026-06-10

## Goal

DXM02-MK is the Dianxiaomi account for Mingkong selection. Its Listing archive should collect all listings with 30-day `paidProductCount > 10`, and Mingkong material video-spend sync should use the latest archived source set with a daily Top500 cap.

## Current State

- `tools/dianxiaomi_listing_ranking_sync.py` collects Dianxiaomi Listing sales through DXM02-MK CDP.
- The scheduled unit currently uses a 7-day window, rolling 7 snapshot dates, and stores only Top500 rows.
- `appcore.mingkong_materials.latest_top_products()` reads the latest `dianxiaomi_rankings` snapshot and supports both uncapped reads and explicit caps.
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
- Keep `latest_top_products(limit=0)` uncapped for manual/debug reads, but make the daily Mingkong material snapshot runner default to `--source-limit 500`.
- Before the Top500 product sync begins, probe the Mingkong material API every 10 seconds until it is healthy, waiting for at most 1 hour. If the health gate times out, cancel the current snapshot round without processing product rows.
- Daily Mingkong material snapshot must process the Top500 product sync queue serially with at least 1 second between products. The interval applies only to the Top500 product sync loop; detail, login-retry, cover-cache, and other internal requests for a product are not additionally throttled by this rule.

## Verification

- Unit tests cover the 30-day payload window, minimum sales filtering, uncapped Listing archive collection, Top500 Mingkong daily sync source cap, pre-sync Mingkong health gate, systemd arguments, scheduled task metadata, and the 1-second product-loop interval.
- No Windows local MySQL checks are used. Database-sensitive behavior is tested through monkeypatches.
