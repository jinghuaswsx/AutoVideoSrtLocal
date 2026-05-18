# Dianxiaomi Listing Ranking Sync

Last updated: 2026-05-12

Superseded for new collection behavior by `2026-05-18-dianxiaomi-full-listing-archive-design.md`.
This document remains the historical record for the original Top1000 implementation and DXM endpoint discovery.

## Goal

Mingkong selection uses Dianxiaomi DXM02-MK browser state to collect the Listing sales ranking. The ranking source is Dianxiaomi's `Listing销量` page:

- Page: `https://www.dianxiaomi.com/web/stat/salesStatistics`
- API: `POST https://www.dianxiaomi.com/api/stat/product/statSalesPageListNew.json`
- Browser runtime: DXM02-MK, CDP `http://127.0.0.1:9223`, profile `/data/autovideosrt/browser/profiles/mk-selection`

The collector locks a single natural day by passing identical `beginDate` and `endDate`, sorts by `paidProductCount` descending, and stores the first 1000 rows into `dianxiaomi_rankings`.

Note: although the page title is `Listing销量`, the DXM02-MK account's current frontend sends `stat/product/statSalesPageListNew.json` from this page. The newer `statBase/listingSales/statPageList.json` endpoint returns `您没有权限查看此数据！` for this account and is not the production source.

## Backfill

The backfill starts at `2026-04-23`. A date is incomplete when `dianxiaomi_rankings` has fewer than 1000 rows for that `snapshot_date`; the old 300-row snapshot is therefore treated as missing. The CLI supports one safe batch per run with `--max-days-per-run`, so an external loop can run one day every 3 minutes until all missing dates are complete.

## Daily Schedule

After backfill, the durable task is a systemd timer at Beijing time `12:40`:

- `autovideosrt-dianxiaomi-listing-ranking-sync.timer`
- `autovideosrt-dianxiaomi-listing-ranking-sync.service`
- Task code: `dianxiaomi_listing_ranking_sync`

The timer runs `--mode rolling --rolling-days 7 --daily-offset-days 0`, meaning the 12:40 run refreshes the latest seven natural dates including today. Each date is collected independently with `beginDate=endDate`, stores up to 1000 rows, and persists the rows that Dianxiaomi can currently return.

## Guardrails

- Do not use Windows local MySQL (`127.0.0.1:3306`) for checks, tests, or backfill.
- Use the server environment and DXM02-MK CDP state for real collection.
- Record every run in `scheduled_task_runs`.
- Register the timer in `appcore/scheduled_tasks.py` so it is visible in the Web scheduled task module.
