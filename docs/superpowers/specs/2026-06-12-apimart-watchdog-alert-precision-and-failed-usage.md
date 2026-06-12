# APIMART Watchdog Alert Precision And Failed Usage

Last updated: 2026-06-12

## Anchors

- `AGENTS.md`: code changes must be anchored in repository docs and verified with focused pytest.
- `docs/superpowers/specs/2026-05-15-apimart-balance-watchdog-design.md`: the APIMART balance watchdog compares remote APIMART balance movement against the local billing ledger.
- `docs/superpowers/specs/2026-04-20-ai-usage-billing-design.md`: APIMART image generation rows are recorded in `usage_logs` with pricebook cost.
- `db/migrations/2026_04_25_update_apimart_gpt_image_2_price.sql`: current APIMART `gpt-image-2` pricebook entry is `0.0408 CNY/image`.

## Problem

The Feishu scheduled-task alert currently renders raw `Decimal` values in the
APIMART usage-gap message, for example `16.42911111111111111111111111 USD`.
That makes the operational alert hard to scan.

The same alert can also overstate unexplained usage because the local comparison
only sums `usage_logs` rows where `provider='apimart'` and `success=1`. APIMART
image generation is asynchronous: once a task has been submitted upstream, a
local timeout, process shutdown, polling error, completed-image download failure,
or remote task failure may still correspond to APIMART balance movement. Those
rows are locally marked failed but should still be counted for the watchdog's
balance reconciliation.

## Requirements

- Feishu-facing APIMART watchdog money fields must render with one decimal
  place in alert messages.
- Summary JSON should keep precise `Decimal` values for analysis; formatting is
  only for human-readable messages.
- `local_apimart_usage_usd()` must include:
  - successful APIMART rows;
  - failed APIMART rows whose error indicates the request had likely reached the
    post-submit phase: timeout after APIMART submit, `signal=15`, APIMART task
    failure, APIMART polling failure, or APIMART image download failure.
- `local_apimart_usage_usd()` must continue to exclude failures that happened
  before a remote task could be created, such as APIMART submit HTTP failures or
  submit request connection failures.
- The APIMART pricebook entry is not changed by this fix. Production evidence on
  2026-06-12 showed that including likely charged failed rows reconciles the
  remote delta closely enough without changing `gpt-image-2` unit price.

## Verification

- `python -m pytest tests/test_apimart_balance_watchdog.py -q`
- `python scripts/pytest_related.py --base origin/master --run`

