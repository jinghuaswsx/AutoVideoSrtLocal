# 2026-05-28 - Ads Ad Set/Ad default to latest closed day

## Context

The Ads analysis page has Campaign, Ad Set, and Ad sub-tabs. Campaign can include the current Meta business day through `meta_ad_realtime_daily_campaign_metrics`; Ad Set and Ad only read daily final tables and therefore cannot show the current business day until the daily-final sync has completed.

## Decision

- Keep Campaign list/detail defaults on the current Meta business day.
- Default Ad Set and Ad list/detail ranges to the latest closed Meta business day, defined as `orderAnalyticsMetaCalendar.today() - 1`.
- Users can still click the "today" quick range on Ad Set or Ad; that state should keep the existing warning explaining that current-day data is unavailable for those levels.
- Manual ad-spend rendering must not depend on helper functions scoped inside another script closure.

## Verification

- Template regression tests must assert that Ad Set/Ad defaults call level-aware date helpers and that the quick-range highlight is synced after initialization.
- Template regression tests must assert that manual ad-spend rendering uses a helper available inside its own closure.
