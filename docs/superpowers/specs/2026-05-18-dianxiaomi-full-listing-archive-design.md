# Dianxiaomi Full Listing Archive

Last updated: 2026-05-18

## Context

The original `dianxiaomi_listing_ranking_sync` job stored Dianxiaomi Listing sales Top1000 rows per natural day. That made the Mingkong selection page depend on a truncated ranking and also made completeness checks use a fixed 1000-row threshold.

Mingkong video material discovery needs the product universe to be all listings with recent sales, not only the first 1000 by one-day rank. The raw Dianxiaomi snapshot must also not erase Mingkong enrichment columns such as `mk_product_id` or `mk_total_spends`; those values are enrichment state and are not produced by the Dianxiaomi fetch.

This document supersedes the Top1000 behavior in `2026-05-12-dianxiaomi-listing-ranking-sync.md` for future collection logic.

## Required Behavior

1. A snapshot date represents the rolling 7-day Dianxiaomi window ending on that date.
   - `snapshot_date = D`
   - Dianxiaomi request `beginDate = D - 6`
   - Dianxiaomi request `endDate = D`
2. The collector fetches the full result set returned by Dianxiaomi for that 7-day window.
   - It paginates until `totalPage` is reached or an empty page is returned.
   - There is no default Top1000 cap.
   - A manual safety cap may exist, but the scheduled job must run uncapped.
3. Only listings with recent sales are archived.
   - A row is eligible when normalized `sales_count > 0`.
   - `rank_position` remains the Dianxiaomi sort position for the current snapshot; it is not a global identity.
4. `dianxiaomi_rankings` remains the raw archive table for the current implementation.
   - The unique key stays `snapshot_date + product_id`.
   - Upsert must preserve any existing Mingkong enrichment columns that the Dianxiaomi fetch does not own.
   - Stale rows for the same snapshot date may be removed after a successful fetch so that each date is an exact archive of that 7-day window.
5. Backfill completeness no longer uses `1000` as the expected row count.
   - In uncapped full-archive mode, a date with at least one stored row is considered present.
   - Explicit refresh modes still re-fetch selected dates.
6. The Mingkong selection UI must let the user choose current or historical snapshot dates.
   - Product library and video material library must use the same selected `snapshot`.
   - The default remains the latest available snapshot.
7. The daily task remains registered as `dianxiaomi_listing_ranking_sync`.
   - Name and descriptions should say "近7天有销量全量归档", not "Top1000".
   - The systemd service should run rolling mode for the latest 7 snapshot dates and must not pass `--target-rows 1000`.

## Out Of Scope

- A separate cross-date overall leaderboard table is not required in this change.
- A new schema migration is not required unless the implementation needs fields that do not exist in `dianxiaomi_rankings`.
- Mingkong API matching/enrichment improvements are not part of this change; the goal here is to stop the raw Dianxiaomi archive from truncating or wiping data it does not own.

## Verification

- Unit tests cover rolling 7-day request dates, uncapped pagination, zero-sales filtering, and backfill completeness without the 1000 threshold.
- Service/route/template tests cover the snapshot date list and the page passing the selected snapshot into both product and video material APIs.
- Deployment verification must not use Windows local MySQL.
