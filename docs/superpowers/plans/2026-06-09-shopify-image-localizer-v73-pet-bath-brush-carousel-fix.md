# Shopify Image Localizer V7.3 Pet Bath Brush Carousel Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release Shopify Image Localizer V7.3 so `pet-bath-brush-rjc` completes image replacement successfully.

**Architecture:** The fix stays in the carousel pairing layer. Exact source-index matches continue to win; when the current carousel slot shares an exact source token with multiple localized candidates but none matches the slot index, pairing falls back to a stable same-token candidate instead of raising and stopping the whole language run.

**Tech Stack:** Python 3.12, pytest, Playwright/CDP, Shopify Image Localizer.

---

### Task 1: Reproduce The Pet Bath Brush Pairing Failure

**Files:**
- Modify: `tests/test_shopify_image_localizer_batch_cdp.py`

- [ ] **Step 1: Write the failing test**

Add:

```python
def test_pair_carousel_images_uses_same_token_fallback_when_duplicate_detail_sources_do_not_match_slot():
    token = "b0d7cac6bbce4313a7ff2883a7818803d"
    product_images = [
        {"src": f"https://cdn.shopify.com/files/slot-{idx}.gif"}
        for idx in range(7)
    ] + [
        {"src": f"https://cdn.shopify.com/files/S{token.upper()}_1.webp?v=1"}
    ]
    localized_images = [
        {
            **_localized(f"20260608_617388c9_20260608_db0bb5b9_from_url_en_00_S{token.upper()}_1.webp.jpg"),
            "id": "detail-28755",
        },
        {
            **_localized(f"20260608_eda96020_20260608_4c54de21_from_url_en_01_S{token.upper()}_1.webp.jpg"),
            "id": "detail-28756",
        },
    ]

    pairs = run_product_cdp.pair_carousel_images(localized_images, product_images)

    assert pairs == [
        (
            7,
            str(Path("C:/tmp") / f"20260608_617388c9_20260608_db0bb5b9_from_url_en_00_S{token.upper()}_1.webp.jpg"),
        )
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_uses_same_token_fallback_when_duplicate_detail_sources_do_not_match_slot -q
```

Expected: FAIL with `ValueError: ambiguous carousel source`.

### Task 2: Implement Stable Same-Token Fallback

**Files:**
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`

- [ ] **Step 1: Add the minimal fallback**

In `_choose_carousel_candidate`, after exact zero-based and one-based source-index checks and before raising ambiguity, return the first sorted candidate when the token is exact but no candidate matches the slot index. This mirrors the detail-image token fallback behavior in `taa_cdp.choose_localized_image`.

- [ ] **Step 2: Run the RED test again**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_uses_same_token_fallback_when_duplicate_detail_sources_do_not_match_slot -q
```

Expected: PASS.

### Task 3: Version Bump And Focused Verification

**Files:**
- Modify: `tools/shopify_image_localizer/version.py`

- [ ] **Step 1: Bump version to 7.3**

Set:

```python
RELEASE_VERSION = "7.3"
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_domains.py -q
python -m compileall tools/shopify_image_localizer
```

Expected: all focused tests pass; compileall succeeds.

### Task 4: Build And Live Verify V7.3

**Files:**
- Use: `scripts/build_shopify_image_localizer_wine.sh`
- Use: `tools/shopify_image_localizer/CLAUDE.md`
- Use: `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`

- [ ] **Step 1: Build V7.3**

Run the release-standard build command with `--release-standard-read --version 7.3`.

- [ ] **Step 2: Launch V7.3**

Start the new V7.3 executable and verify the window title shows `v7.3`.

- [ ] **Step 3: Run `pet-bath-brush-rjc`**

Run the batch replacement for German, French, and Italian on `newjoyloo.com`, product ID `8602533626029`.

- [ ] **Step 4: Verify success**

Confirm the app reports successful languages and no `ambiguous carousel source` error. Verify material-library product links for:

```text
https://newjoyloo.com/de/products/pet-bath-brush-rjc
https://newjoyloo.com/fr/products/pet-bath-brush-rjc
https://newjoyloo.com/it/products/pet-bath-brush-rjc
```
