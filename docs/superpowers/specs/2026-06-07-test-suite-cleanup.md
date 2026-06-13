# 2026-06-07 Test Suite Cleanup

## Anchors

- `AGENTS.md`: project test entrypoint is `pytest -q`; verification after changes starts with related pytest files and ends with service-level checks when routes are affected.
- `docs/superpowers/notes/2026-04-21-pytest-baseline-failures.md`: historical baseline failures are evidence of tests whose contracts drifted from current behavior.
- `docs/superpowers/notes/2026-04-22-local-server-acceptance-checklist.md`: separates unit/integration pytest baseline from manual smoke against a deployed test environment.

## Goal

Keep the project test suite effective and necessary. Remove tests and smoke helpers that are no longer executable, meaningful, or maintained. Update tests that still protect current behavior but drifted because APIs, UI contracts, or fixtures changed.

## Cleanup Rules

Delete a test or script when at least one condition is true:

- It requires unavailable live credentials, a local MySQL service, a real browser session, or a deployed server while being collected as normal pytest.
- It asserts a retired UI/API contract and no current spec or production code still supports that contract.
- It duplicates stronger coverage in a current unit/integration test without adding a distinct boundary or regression.
- It is a one-off debug helper or historical smoke script with no stable inputs, no documentation owner, and no safe default execution path.
- It always fails at import or collection time because the referenced module, symbol, fixture, or dependency no longer exists.

Adjust instead of deleting when:

- The covered behavior is still present and important, but names, payload shapes, or fakes drifted.
- The test can be made deterministic by patching I/O, DB, browser, network, or clock dependencies.
- The file is the only coverage for authentication, permissions, schema migrations, scheduled tasks, billing, LLM provider routing, task recovery, or destructive operations.

Keep manual/e2e helpers only when they are clearly marked, excluded from default pytest collection, and document their required environment.

Default pytest collection excludes:

- `tests/audio`, `tests/e2e`, and `tests/manual` unless `AUTOVIDEOSRT_RUN_EXTERNAL_TESTS=1`.
- Root-level smoke/e2e files listed in `tests/conftest.py::_EXTERNAL_TEST_FILES` unless `AUTOVIDEOSRT_RUN_EXTERNAL_TESTS=1`.
- Known live-DB test modules unless `AUTOVIDEOSRT_RUN_LIVE_DB_TESTS=1`.

This keeps `pytest -q` deterministic in a local/CI worktree and prevents accidental connections to `127.0.0.1:3306`.

Deleted in this cleanup:

- `tests/test_link_check_gemini.py`: imported removed module `appcore.link_check_gemini`.
- `tests/test_link_check_ui_assets.py`: asserted retired link-check DOM/CSS strings after the current UI moved to the newer template/static contract.
- `tools/repair_legacy_pushes.py`: one-off repair helper with hard-coded server addresses and no safe default execution path.
- Stale architecture-boundary assertions in `tests/test_architecture_boundaries.py` that protected already-migrated route/service split rules instead of current code boundaries.
- Chmod/root-permission failure-path tests in `tests/test_meta_daily_final_export_dir.py`; they are invalid when the suite runs as root.
- Obsolete static UI snapshot assertions in `tests/test_task_center_closure_assets.py` and `tests/test_web_routes.py` that pinned retired DOM order, CSS tokens, or old JavaScript string assembly.
- Retired TOS-download branch tests in `tests/test_web_routes.py`; `web.services.artifact_download` now serves local artifacts only and keeps TOS hooks as no-op compatibility shims.

Adjusted in this cleanup:

- Default pytest collection now separates manual/e2e/live-DB tests behind explicit environment flags.
- No-DB Flask fixtures stub current media, SKU, ROAS, MK-binding, and Meta-hot-post dependencies so route tests do not touch local MySQL.
- LLM provider/channel tests were updated from retired `gemini_vertex_adc` / legacy image-channel names to current `gemini_vertex`, `cloud`, `apimart`, and OpenRouter contracts.
- Runtime, TTS, AV-localize, image-translate, quality-assessment, and task-notification tests were adjusted to current payload shapes and to stub non-essential DB/LLM side effects.
- `_blank` link security coverage was kept, and production templates were fixed to include `rel="noopener noreferrer"`.

## Validation

1. `pytest --collect-only -q`
2. Focused pytest files for every changed or removed area.
3. `pytest -q` as the final acceptance run.
4. If full pytest exposes remaining failures, continue fixing or remove/adjust tests according to the cleanup rules above.

Final acceptance on 2026-06-07:

- Collection: `7578 tests collected`.
- Focused changed-area regressions: `794 passed`.
- `tests/test_web_routes.py`: `157 passed, 3 skipped`.
- Full default suite: `7572 passed, 8 skipped, 10 warnings in 576.79s`.

Post-merge acceptance notes:

- After merging `origin/master`, realtime order-profit tests follow the calibrated Shopify fee forward-estimate multiplier from `docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md`.
- Stored Shopify fee totals remain authoritative; component display values are ratio-allocated from the calibrated computed split, so rounding may move one cent between platform and currency-conversion components while total fee and profit stay unchanged.
- 2026-06-13 release gate: `tests/test_weekly_ai_screenshot.py` was moved to `tests/manual/test_weekly_ai_screenshot.py` as an external Playwright screenshot helper. It requires a deployed server, browser binaries, `AUTOVIDEOSRT_SMOKE_ADMIN_PASSWORD`, and a local artifact path, so default pytest collection and runtime hard-coded IP scans exclude it.
