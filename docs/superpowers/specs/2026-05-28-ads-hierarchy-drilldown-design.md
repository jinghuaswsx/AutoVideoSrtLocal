# Ads Hierarchy Drilldown

Date: 2026-05-28

## Context

Meta ads are a three-level hierarchy:

- Campaign
- Ad Set
- Ad

The ads analytics page already exposes the three levels as separate tabs, but operators need to move down the hierarchy from the row they are inspecting.

## Required Behavior

1. Clicking a Campaign row opens the Ad Set list that belongs to that Campaign.
2. Clicking an Ad Set row opens the Ad list that belongs to that Ad Set.
3. Clicking an Ad row keeps the existing per-day detail behavior.
4. The child list keeps the same date range and ad-account filter as the parent list.
5. Child lists must use today's realtime rows when the selected range includes the current Meta business day, matching the standalone Ad Set and Ad tabs.
6. Parent filtering must be applied to both the historical daily table and realtime snapshot table.

## API Contract

`/order-analytics/ads/list` accepts optional hierarchy parameters:

- `parent_level=campaign&parent_code=<normalized_campaign_code>` when `level=adset`
- `parent_level=adset&parent_code=<normalized_adset_code>` when `level=ad`

Unsupported parent/child combinations return `400 invalid_param`.

