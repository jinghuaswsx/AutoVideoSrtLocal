# 2026-06-05 — Ads Realtime Parent Code Search

## Context

`/order-analytics/ads-view` uses `/order-analytics/ads/list` for the Campaign / Ad Set / Ad tabs.
When the selected range includes the current Meta business date, list APIs read the latest realtime
snapshot per `(business_date, ad_account_id)` as required by
`2026-05-28-ads-level-realtime-default-today.md`.

Operators commonly search the Ad tab with a product or campaign code. For current-day realtime rows,
`matched_product_code` is not stored in `meta_ad_realtime_daily_ad_metrics`; the product code is often
present in `normalized_campaign_code` or `normalized_adset_code`. The existing SQL filters only the
current level name/code plus `matched_product_code`, so valid current-day realtime Ad rows can be
filtered out before Python has a chance to apply product matching.

## Required Behavior

1. Campaign / Ad Set / Ad list search still matches the current level name, current level normalized
   code, and `matched_product_code`.
2. For realtime union rows, Ad Set search also matches `normalized_campaign_code`.
3. For realtime union rows, Ad search also matches `normalized_campaign_code` and
   `normalized_adset_code`.
4. The search expansion applies only to list filtering. Detail views, sync jobs, realtime import,
   date defaults, account filters, and hierarchy parent filters are unchanged.
5. Realtime rows still use the latest snapshot per `(business_date, ad_account_id)`, never a global
   `MAX(snapshot_at)`.

## Regression

Add a regression test for `get_ads_level_list("ad", q=<product-code>, start=end=current_meta_business_date())`
showing that the realtime union search condition includes parent realtime code fields. This protects
the screenshot case where current-day Ad rows exist but product-code search returns an empty list.

## Docs-anchor

- `docs/superpowers/specs/2026-05-11-ads-analytics-inline-search-list.md`
- `docs/superpowers/specs/2026-05-28-ads-level-realtime-default-today.md`
- `appcore/order_analytics/CLAUDE.md`
