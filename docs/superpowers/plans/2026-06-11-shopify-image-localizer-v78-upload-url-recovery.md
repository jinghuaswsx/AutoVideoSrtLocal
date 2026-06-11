# Shopify Image Localizer V7.8 Upload URL Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix tokenless TAA detail-image uploads and publish Shopify Image Localizer V7.8.

**Architecture:** Keep pairing and save verification unchanged. Add a narrow upload URL recovery path inside `TaaSession.upload_image()` that diffs modal image URLs before and after upload when strict filename/token matching cannot find the new CDN URL.

**Tech Stack:** Python 3.12, Playwright/CDP, pytest, Wine/PyInstaller release pipeline.

---

### Task 1: Regression Test

**Files:**
- Modify: `tests/test_shopify_image_localizer_batch_cdp.py`

- [ ] **Step 1: Add failing test**

Add `test_taa_upload_image_recovers_single_new_modal_cdn_url_without_token`.
The fake CDP emits only stale unmatched network events. The fake modal returns
one old Shopify CDN URL before upload and that old URL plus one new Shopify CDN
URL after upload. Assert `upload_image()` returns the new URL.

- [ ] **Step 2: Run red test**

Run:

```bash
python -m pytest tests/test_shopify_image_localizer_batch_cdp.py::test_taa_upload_image_recovers_single_new_modal_cdn_url_without_token -q
```

Expected before implementation: fail with `uploaded CDN URL not found`.

### Task 2: Upload URL Recovery

**Files:**
- Modify: `tools/shopify_image_localizer/rpa/taa_cdp.py`

- [ ] **Step 1: Implement modal URL helpers**

Add helper methods that return Shopify CDN image URLs visible in the TAA insert
image modal and compare before/after URL sets.

- [ ] **Step 2: Use fallback only for a unique new modal URL**

In `upload_image()`, capture modal URLs before `_set_file_input()`. If strict
network and strict modal matching fail, diff the modal URLs and accept exactly
one new Shopify CDN URL. Keep raising the existing error otherwise.

- [ ] **Step 3: Run green tests**

Run:

```bash
python -m pytest tests/test_shopify_image_localizer_batch_cdp.py::test_taa_upload_image_rejects_unmatched_previous_cdn_url tests/test_shopify_image_localizer_batch_cdp.py::test_taa_upload_image_recovers_single_new_modal_cdn_url_without_token -q
```

Expected: both tests pass.

### Task 3: Version and Verification

**Files:**
- Modify: `tools/shopify_image_localizer/version.py`

- [ ] **Step 1: Bump version**

Set `RELEASE_VERSION = "7.8"`.

- [ ] **Step 2: Run focused localizer tests**

Run:

```bash
python -m pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_release_web.py -q
python -m compileall tools/shopify_image_localizer
```

Expected: pytest exit 0 and compileall exit 0.

### Task 4: Release

**Files:**
- Commit: V7.8 fix and docs

- [ ] **Step 1: Commit and merge/push**

Commit with a message containing:

```text
Docs-anchor: docs/superpowers/specs/2026-06-11-shopify-image-localizer-v78-upload-url-recovery.md
```

- [ ] **Step 2: Build V7.8 portable release**

Run the documented Wine release command for version `7.8`.

- [ ] **Step 3: Verify release artifact**

Verify zipped configs, BOM status, source commit, and range-curl HTTP status
according to the release standard.
