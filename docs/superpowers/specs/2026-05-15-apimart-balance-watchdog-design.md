# APIMART Balance Watchdog Design

Last updated: 2026-05-15

## Context

APIMART keys are now protected by an IP whitelist and production traffic must go through the server's Clash/Mihomo VPN route. A stolen or misrouted key can still create cost before users notice it in the APIMART console.

This design adds an hourly server-side watchdog that checks APIMART balance movement against the local AI billing ledger and sends a Feishu alert when the two diverge materially.

## Goals

- Run on the production server every hour.
- Query APIMART balance with the configured `llm_provider_configs.apimart_image` key.
- Compare the APIMART `used_balance` delta since the last successful watchdog run with local `usage_logs` cost for successful APIMART calls in the same window.
- Mark the scheduled run as `failed` when usage is suspicious so the existing scheduled-task Feishu alert path notifies operations.
- Record every run in `scheduled_task_runs.summary_json` for later inspection.
- Keep APIMART traffic on the existing `apimart.ai -> Clash/Mihomo VPN` routing path; no local Windows MySQL or local MySQL fallback.

## Non-Goals

- Do not call image generation or any metered generation endpoint.
- Do not build a new alert channel.
- Do not create a separate dashboard page in this iteration.
- Do not depend on an APIMART detailed billing API unless one becomes publicly documented later.

## Data Sources

- APIMART official balance endpoint: `GET /v1/balance`.
- APIMART official account balance endpoint: `GET /v1/user/balance`.
- Local API billing data: `usage_logs` rows where `provider='apimart'`, `success=1`, and `called_at` is inside the watchdog comparison window.

## Detection Rules

Let:

- `remote_delta_usd = current.used_balance - previous.used_balance`
- `local_delta_usd = sum(usage_logs.cost_cny) / 7.2`
- `gap_usd = remote_delta_usd - local_delta_usd`
- `gap_ratio = gap_usd / max(remote_delta_usd, 0.01)`

Alert when any condition is true:

- balance query failed
- API key remaining balance or account remaining balance is lower than 1 USD
- `gap_usd >= 1.00` and `gap_ratio >= 0.20`

Small negative deltas are clamped to zero in summaries so APIMART corrections do not create false positives.

## Scheduler

Add APScheduler job:

- task code: `apimart_balance_watchdog`
- schedule: hourly, every hour
- runner: `appcore.apimart_balance_watchdog.run_scheduled_check`
- log table: `scheduled_task_runs`

The task must be registered in `appcore/scheduled_tasks.py` and `appcore/scheduler.py`, so the admin scheduled-task page can show logs and enable/disable state.

## Feishu Behavior

Use existing scheduled-task failure alerts with `failure_alert_immediate=true` for this task. The watchdog should finish the run as:

- `success`: APIMART balance query succeeded and no suspicious movement was found.
- `failed`: query failed, low balance, or suspicious external consumption detected.

Unlike high-frequency sample-based jobs, every watchdog failure is worth notifying because it can represent leaked spend. A later successful run triggers the existing recovery notification after any prior watchdog failure.

## Verification

- Unit tests cover APIMART response parsing, delta comparison, low-balance alert, local usage aggregation, scheduled run success/failure paths, and scheduler registration.
- Manual production verification runs `run_scheduled_check()` once and confirms a `scheduled_task_runs` row is written.
