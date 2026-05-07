# Meta Ads Final Sync Account Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Meta final daily sync, product-profit ad allocation, and the data-analysis UI use the same multi-account plus store mapping configuration.

**Architecture:** Keep `system_settings.meta_ad_accounts` as the single source of truth. Extend `appcore.meta_ad_accounts` with validated `store_codes`, expose a small `/order-analytics/meta-ad-accounts` API, update `/order-analytics` with an account-management Tab, and make `tools/meta_daily_final_sync.py` loop over enabled accounts like `tools/roi_hourly_sync.py`.

**Tech Stack:** Flask, Jinja, vanilla JavaScript, PyMySQL helpers, pytest, systemd-triggered Python scripts.

---

## File Structure

- Modify `appcore/meta_ad_accounts.py`: add `store_codes` parsing, validation, JSON serialization, available store constants, and site-to-account helper.
- Modify `tools/meta_daily_final_sync.py`: replace single-account globals in the main flow with `MetaAdAccount` loop support.
- Modify `appcore/order_analytics/product_profit_report.py`: read site-to-account mapping from `appcore.meta_ad_accounts`.
- Modify `web/routes/order_analytics.py`: add GET/POST account-management endpoints with audit logging.
- Modify `web/templates/order_analytics.html`: add top/mobile Tab, panel, and JS renderer for account management.
- Modify `db/migrations/2026_05_07_meta_ad_accounts_setting.sql`: seed `store_codes` into the JSON.
- Modify `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md` and `CLAUDE.md`: keep docs as the code anchor.
- Add/modify tests in `tests/test_roi_hourly_sync_meta_multi_account.py`, `tests/test_meta_server_sync_tools.py`, `tests/test_order_analytics_ads.py`, and `tests/test_product_profit_report.py`.

## Task 1: Account Config Model

- [x] Add failing tests that parse `store_codes`, normalize `act_` account IDs, reject duplicate `code`, and build `{site_code: [account_id]}`.
- [x] Run `pytest tests/test_roi_hourly_sync_meta_multi_account.py -q` and confirm the new tests fail because `MetaAdAccount` has no `store_codes`.
- [x] Implement `store_codes` in `MetaAdAccount`, `_coerce_account`, `_env_default_account`, `set_accounts`, and a `site_account_map(enabled_only=True)` helper.
- [x] Run `pytest tests/test_roi_hourly_sync_meta_multi_account.py -q`.

## Task 2: Final Daily Sync Multi-Account

- [x] Add failing tests in `tests/test_meta_server_sync_tools.py` showing `run_final_sync()` exports two accounts into account subdirectories, imports each account with its own `account_id`, and marks partial account failure as `failed` while preserving successful account results.
- [x] Run `pytest tests/test_meta_server_sync_tools.py -q` and confirm failures point at single-account final sync behavior.
- [x] Change `tools/meta_daily_final_sync.py` so `_run_meta_ads_export`, `_account_id`, `_replace_campaign_daily_rows`, `_replace_ad_daily_rows`, `already_successful`, and `run_final_sync` take account context.
- [x] Change `_refresh_final_roas_snapshot` to sum all `meta_ad_daily_campaign_metrics` rows for the business date instead of filtering one account.
- [x] Run `pytest tests/test_meta_server_sync_tools.py -q`.

## Task 3: Data Analysis Account API And Tab

- [x] Add failing route/template tests proving `/order-analytics` renders `data-tab="adAccounts"` and the GET/POST `/order-analytics/meta-ad-accounts` API reads/saves `store_codes`.
- [x] Run the focused route tests and confirm they fail on missing Tab/API.
- [x] Add the Flask endpoints in `web/routes/order_analytics.py` and wire them to `appcore.meta_ad_accounts`.
- [x] Add the Tab, panel, table, empty/loading/error states, and save flow in `web/templates/order_analytics.html` using existing Ocean Blue tokens.
- [x] Run the focused route/template tests.

## Task 4: Product Profit Mapping

- [x] Add a failing test showing `_recalc_ad_cost` uses account IDs produced from `meta_ad_accounts.store_codes`, including multiple accounts for one store.
- [x] Run `pytest tests/test_product_profit_report.py -q` and confirm the new test fails against the hardcoded mapping.
- [x] Replace `SITE_TO_AD_ACCOUNT` with a helper that asks `meta_ad_accounts.site_account_map()` and sums spend for all mapped accounts.
- [x] Run `pytest tests/test_product_profit_report.py -q`.

## Task 5: Regression

- [x] Run `pytest tests/test_roi_hourly_sync.py tests/test_roi_hourly_sync_meta_multi_account.py tests/test_meta_server_sync_tools.py tests/test_order_analytics_ads.py tests/test_product_profit_report.py -q`.
- [x] Run `python -m py_compile appcore/meta_ad_accounts.py tools/meta_daily_final_sync.py web/routes/order_analytics.py appcore/order_analytics/product_profit_report.py`.
- [x] Inspect `git diff --check`.
