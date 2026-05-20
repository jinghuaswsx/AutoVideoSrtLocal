# Mingkong Product Local Aggregate Stats Design

Last updated: 2026-05-20

## Context

`/xuanpin/mk#products` shows Mingkong product metrics in the product list:

- 视频数
- 明空消耗
- 广告数

Those values currently come from `dianxiaomi_rankings.mk_video_count`,
`dianxiaomi_rankings.mk_total_spends`, and `dianxiaomi_rankings.mk_total_ads`.
Those fields are legacy enrichment fields and can be empty or stale after the
Mingkong material archive moved to local scheduled snapshots.

For product `21-fitness-resistance-bands-4-tube-pedal-ankle-puller`, the live
Mingkong product detail has 55 non-hidden material rows for the exact product
code, while the product list can show 0 because it reads the legacy ranking
summary. The local material-card archive has fewer playable rows when Mingkong
returns rows without a normalized media path; those pathless rows still count
toward product-level video/material totals and ad counts.

## Scope

In scope:

1. Maintain product-level Mingkong aggregates during the existing local material
   snapshot job.
2. Run that maintenance twice per day at 05:00 and 17:00 Beijing time.
3. Product-level `video_count` counts all non-hidden Mingkong material rows from
   product detail, including rows without a normalized `video_path`.
4. Product-level `total_90_spend` is the sum of each non-hidden material row's
   90-day `spends` value.
5. Product-level `total_ads` is the sum of each non-hidden material row's
   `ads_count`.
6. Playable material-card tables continue to store only rows with a normalized
   `video_path`.
7. `/xuanpin/mk#products` reads local maintained aggregates from
   `mingkong_material_products` and falls back to legacy `dianxiaomi_rankings`
   fields only when no local aggregate exists yet.
8. After deployment, run one manual snapshot to create the first local aggregate
   data version.

Out of scope:

- Do not add per-page live Mingkong requests to the product list.
- Do not change material-card identity or playable video filtering.
- Do not connect to Windows local MySQL for verification.

## Data Model

Extend `mingkong_material_products` with product-level aggregate fields:

- `video_count`: all non-hidden Mingkong material rows from product detail.
- `path_video_count`: non-hidden rows with a normalized media path; this should
  match playable card rows for the product.
- `total_90_spend`: sum of all non-hidden material `spends` values.
- `total_ads`: sum of all non-hidden material `ads_count` values.

`material_count` remains the playable/path-backed card row count for backward
compatibility.

## Sync Behavior

For every matched Mingkong product:

1. Fetch the product detail endpoint.
2. Build playable material rows with the existing `flatten_materials_for_product`
   path filter.
3. Independently compute product aggregates from every non-hidden detail video,
   without requiring a normalized media path.
4. Upsert the aggregate fields into `mingkong_material_products`.

Failed or unmatched products write zero aggregates with their existing status.

## Product List API

The product list joins the latest successful `mingkong_material_sync_runs`
snapshot to `mingkong_material_products` by normalized product code. Returned
fields map as:

- `mk_video_count` = local `video_count`
- `mk_total_spends` = local `total_90_spend`
- `mk_total_ads` = local `total_ads`
- `mk_product_id` / `mk_product_name` prefer local Mingkong product identity

If no successful local product aggregate exists for a row, the API falls back to
the legacy `dianxiaomi_rankings` values or zero.

## Scheduler

`mingkong_material_daily_snapshot` remains the maintenance task. Its systemd
timer runs at:

- `05:00`
- `17:00`

The task remains registered in `appcore/scheduled_tasks.py` and controlled by
`autovideosrt-mingkong-material-daily-snapshot.timer`.

## Verification

Automated checks:

- Product aggregate helper counts pathless non-hidden rows while keeping playable
  card rows path-only.
- Snapshot product-status upsert writes aggregate columns.
- Product list SQL uses local Mingkong product aggregates and falls back to
  legacy values.
- Migration tests cover the new columns and index.
- Scheduler tests cover 05:00/17:00 metadata and systemd timer.

Manual production check after deploy:

- Run one `tools/mingkong_material_daily_snapshot.py` snapshot.
- Confirm product `21-fitness-resistance-bands-4-tube-pedal-ankle-puller` has
  product-level `video_count = 55` in local aggregate data after sync.
