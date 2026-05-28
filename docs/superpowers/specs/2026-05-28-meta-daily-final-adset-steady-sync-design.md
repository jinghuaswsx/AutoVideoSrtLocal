# 2026-05-28 - Meta daily-final Ad Set steady sync

## Context

The Ads analysis page has Campaign, Ad Set, and Ad tabs. Campaign and Ad daily-final data continue to sync through `meta_daily_final`, but `meta_ad_daily_adset_metrics` only has historical backfill rows through 2026-05-09 because the regular daily-final service runs without the Ad Set option.

## Decision

- Regular daily-final sync must include Campaign, Ad Set, and Ad levels.
- The CLI exposes `--include-adsets`; systemd sync and check units must pass it explicitly.
- The scheduled-task registry must describe the three-level daily-final behavior so the Web task center matches deployed timers.
- Backfill missing Ad Set rows from 2026-05-10 through the latest fully closed Meta business day with a paced server-side run: one business day at a time, verify after small batches, and leave the browser/CDP lock free between attempts.

## Verification

- Unit tests assert the CLI forwards `include_adsets=True`.
- Unit tests assert both daily-final systemd units include `--include-adsets`.
- Server verification checks `scheduled_task_runs` summaries and `meta_ad_daily_adset_metrics` watermarks after deployment/backfill.
