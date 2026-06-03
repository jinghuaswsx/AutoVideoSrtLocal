# TABCUT Browser Login Management

Date: 2026-06-03

## Problem

TABCUT crawling can detect guest/login-required state and perform password login, but the crawler only reads `TABCUT_LOGIN_ACCOUNT` / `TABCUT_LOGIN_PASSWORD` style process environment variables. On the server these keys are not present, so real collection fails before data can be written. This also gives admins no runtime view of whether TABCUT auto-login succeeded, failed, or needs human verification.

## Design

Reuse the existing `browser_login_credentials` plaintext credential store instead of adding another secret table. The settings page should manage a default `TABCUT / tabcut` credential row next to the existing `DXM01-Meta / facebook` row.

Credential resolution order:

1. Environment variables keep highest priority for emergency override.
2. If environment credentials are absent, read enabled `browser_login_credentials(TABCUT, tabcut)`.
3. If both are absent, fail before API fetch and keep guest data out of the database.

Login status handling:

- TABCUT auto-login updates the same credential row with `last_login_status`, `last_error`, and `last_login_at`.
- Human verification remains a hard stop. The crawler must not continue in visitor mode.

## Verification

- Unit tests cover default TABCUT row rendering, DB-backed credential fallback, missing-credential failure status, and settings page labels.
- Focused TABCUT crawler/settings tests plus Python compile checks must pass before merge or release.
