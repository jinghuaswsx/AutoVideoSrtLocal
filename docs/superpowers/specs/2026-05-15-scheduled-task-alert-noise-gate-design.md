# Scheduled Task Alert Noise Gate Design

Last updated: 2026-05-28

## Background

Scheduled task alerts currently page the operations chat on the first failed run, then repeat by a small failure streak interval. That is too noisy for intermittent upstream failures, stale-run cleanup, and low-volume batches.

## Goal

Only surface scheduled task alarms when there is enough evidence that the service or batch workflow is broadly unavailable.

## Rules

- A single failed run must not trigger Feishu or the global Web failure banner.
- Run-streak alerts require at least 20 consecutive failed terminal runs for the same task.
- Batch/sample alerts require more than 20 attempts and a failure rate greater than 80%.
- After an alert has been emitted for a task, repeated alerts for the same failed streak are suppressed for
  12 hours by default. The interval can be overridden with `feishu_alerts.failure_repeat_hours`.
- Recovery alerts are sent only after a prior failure state that would have been alert-worthy under these rules.
- Low-volume failed rows remain in `scheduled_task_runs` for audit, but they do not page operators.

## Implementation Notes

- `appcore.feishu_alerts.should_dispatch_failure()` owns the consecutive-run throttle and floors repeat reminders to the 20-run threshold.
- `appcore.feishu_alerts` stores the last emitted failure alert per task in `system_settings` so both Feishu
  dispatch and the global Web banner honor the same 12-hour cooldown.
- `appcore.scheduled_tasks` owns task-run evidence extraction from `summary_json`, including common counters such as `total`, `processed`, `scanned`, `downloaded`, and `failed`.
- Meta hot-post video localization keeps its existing daily aggregation semantics, but uses the shared threshold constants.
- `latest_failure_alert()` uses the same alert-worthiness gate as Feishu so the page banner does not show suppressed single failures.

## Verification

Focused tests cover:

- First failed run suppression.
- Dispatch at 20 consecutive failures.
- Sample failure-rate dispatch only when attempts are greater than 20 and failure rate is greater than 80%.
- Recovery suppression when no prior alert-worthy failure existed.
