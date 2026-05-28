# Ads AdSet/Ad Realtime Default Today

Date: 2026-05-28

## Context

Ads analysis has three list/detail levels: Campaign, Ad Set, and Ad. The realtime XHR channel can fetch Meta `/insights` at all three levels in one authenticated browser session, and the database already has:

- `meta_ad_realtime_daily_campaign_metrics`
- `meta_ad_realtime_daily_adset_metrics`
- `meta_ad_realtime_daily_ad_metrics`

The previous 2026-05-28 closed-day rule was too conservative for Ad Set and Ad. Those tabs must behave like Campaign for the current Meta business day.

## Required Behavior

1. Campaign, Ad Set, and Ad default list/detail date inputs to the current Meta business day.
2. Backend list APIs include today's realtime rows when `end_date >= current_meta_business_date()` for all three levels.
3. Backend detail APIs include today's latest realtime snapshot for all three levels.
4. Realtime rows must use the latest snapshot per `(business_date, ad_account_id)`, never a global `MAX(snapshot_at)`.
5. Realtime sync fetches `campaign`, `adset`, and `ad` levels through the XHR session. Reporting totals remain campaign-level to avoid triple-counting spend.

## Level Mapping

| Level | Daily table | Realtime table | Code column | Name column |
| --- | --- | --- | --- | --- |
| campaign | `meta_ad_daily_campaign_metrics` | `meta_ad_realtime_daily_campaign_metrics` | `normalized_campaign_code` | `campaign_name` |
| adset | `meta_ad_daily_adset_metrics` | `meta_ad_realtime_daily_adset_metrics` | `normalized_adset_code` | `adset_name` |
| ad | `meta_ad_daily_ad_metrics` | `meta_ad_realtime_daily_ad_metrics` | `normalized_ad_code` | `ad_name` |

## Rollout

After deployment, run one regular realtime sync with `--skip-dxm-fetch --meta-channel browser` to seed the current business day. The existing `roi_hourly_sync` schedule then refreshes these tables at the normal safe cadence.
