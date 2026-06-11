# Shopify Image Localizer V7.7 Skip Not Ready and Carousel Ambiguity Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `handle-v-3987406-rjc` carousel ambiguity failure, skip not-ready batch languages immediately, and publish Shopify Image Localizer V7.7.

**Architecture:** Keep the existing batch pipeline. Convert ambiguous carousel filename matches into an unmatched slot so the current visual fallback path can decide, and introduce a typed skip exception for explicit bootstrap not-ready responses that the GUI batch loop can classify separately from hard failures.

**Tech Stack:** Python 3.12, Tkinter GUI, Playwright CDP automation, pytest, PyInstaller/Wine release pipeline.

---

### Task 1: Carousel Ambiguity Falls Through to Visual Fallback

**Files:**
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- Test: `tests/test_shopify_image_localizer_batch_cdp.py`

- [x] **Step 1: Write the failing test**

Add a test that builds two localized candidates with the same `source_name_key` but different `source_index` values, then asserts the carousel pair list omits the ambiguous slot instead of raising.

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_leaves_ambiguous_name_candidates_for_visual_fallback -q
```

Expected: FAIL because `_choose_carousel_name_candidate()` raises `ValueError`.

- [x] **Step 3: Implement the minimal fix**

Change `_choose_carousel_name_candidate()` so unresolved multi-index candidates return `None`. Keep deterministic single-candidate and exact-index behavior unchanged.

- [x] **Step 4: Run the test and verify GREEN**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_leaves_ambiguous_name_candidates_for_visual_fallback -q
```

Expected: PASS.

### Task 2: Bootstrap Not Ready Becomes a Language Skip

**Files:**
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- Test: `tests/test_shopify_image_localizer_batch_cdp.py`

- [x] **Step 1: Write the failing tests**

Add tests that:
- `fetch_bootstrap_ready()` raises a typed skip exception immediately for `ApiError(409, {"error": "localized images not ready"})` and the underscore variant.
- `fetch_bootstrap_ready()` raises the same skip exception for a successful payload with empty `localized_images`.

- [x] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py::test_fetch_bootstrap_ready_skips_localized_images_not_ready_without_polling tests/test_shopify_image_localizer_batch_cdp.py::test_fetch_bootstrap_ready_skips_empty_localized_images_without_polling -q
```

Expected: FAIL because current code retries until timeout.

- [x] **Step 3: Implement the minimal fix**

Add `BootstrapNotReadySkip` in `run_product_cdp.py` with `product_code`, `lang`, `reason`, and `message`. Raise it immediately for explicit not-ready codes and empty localized images. Preserve `shopify_product_id_missing` as a hard API error.

- [x] **Step 4: Run the tests and verify GREEN**

Run the same two tests. Expected: PASS.

### Task 3: GUI Batch Counts Skipped Languages Separately

**Files:**
- Modify: `tools/shopify_image_localizer/gui.py`
- Test: `tests/test_shopify_image_localizer_gui.py`

- [x] **Step 1: Write the failing test**

Add a GUI batch test where the first language raises `BootstrapNotReadySkip` and the second language succeeds. Assert both languages are attempted, skipped count is displayed, failure count remains zero, and Chrome is not restarted for the skip.

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
pytest tests/test_shopify_image_localizer_gui.py::test_gui_batch_skips_not_ready_language_without_restart_and_continues -q
```

Expected: FAIL because skips are currently treated as failures and trigger restart.

- [x] **Step 3: Implement the minimal fix**

Import or reference `run_product_cdp.BootstrapNotReadySkip` through `controller.run_product_cdp`. In `_run_batch()`, catch it before generic `Exception`, append a result with `skipped=True`, increment `skipped_count`, log a concise skip message, and continue without setting `restart_browser_before_next_language`.

- [x] **Step 4: Run the test and verify GREEN**

Run the same GUI test. Expected: PASS.

### Task 4: Detail Verification Tolerates Unmatched Legacy Leftovers

**Files:**
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- Test: `tests/test_shopify_image_localizer_batch_cdp.py`

- [x] **Step 1: Write the failing test**

Add a contract test where all expected replacement URLs are present, but storefront verification still sees old non-Shopify detail images that correspond to missing/unmatched detail candidates.

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py::test_detail_contract_tolerates_unmatched_legacy_non_shopify_leftovers tests/test_shopify_image_localizer_batch_cdp.py::test_detail_contract_fails_when_old_leftovers_exceed_missing_candidates -q
```

Expected: FAIL for the tolerated-leftover case because current code treats any persisted non-Shopify image as a hard failure.

- [x] **Step 3: Implement the minimal fix**

Allow persisted non-Shopify leftovers only when expected replacement URLs are all present and the leftover count does not exceed the count of missing/unmatched detail candidates.

- [x] **Step 4: Run the test and verify GREEN**

Run the same contract tests plus adjacent detail verification tests. Expected: PASS.

### Task 5: Version Bump and Focused Verification

**Files:**
- Modify: `tools/shopify_image_localizer/version.py`

- [x] **Step 1: Bump version**

Set:

```python
RELEASE_VERSION = "7.7"
```

- [x] **Step 2: Run focused tests**

Run:

```bash
python scripts/pytest_related.py --base origin/master --run
python -m compileall tools/shopify_image_localizer
```

Expected: focused tests pass and compileall succeeds.

### Task 6: Local Product Debug and Release

**Files:**
- Read: `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`
- Read: `tools/shopify_image_localizer/CLAUDE.md`

- [x] **Step 1: Start local desktop tool**

Run:

```bash
python -m tools.shopify_image_localizer.main
```

Use product `handle-v-3987406-rjc`, Shopify ID `8606980374701`, and select all languages.

- [x] **Step 2: Verify behavior**

Confirm German no longer fails with `ambiguous carousel filename source`, and any not-ready language is recorded as skipped while later languages continue.

- [ ] **Step 3: Release V7.7**

Follow `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md` and `tools/shopify_image_localizer/CLAUDE.md`: build the portable release, validate config/API key/ZIP, update release metadata, and publish the next automatic image localizer version.
