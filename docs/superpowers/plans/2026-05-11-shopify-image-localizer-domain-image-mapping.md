# Shopify Image Localizer Domain Image Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Shopify Image Localizer reuse the default-domain translated image set when replacing carousel and detail images on any additional product domain.

**Architecture:** Add a focused desktop-side mapping module that treats the default domain as canonical and maps each target-domain image source back to canonical source indexes/tokens. Wire that mapping into existing carousel pairing and TAA detail replacement without duplicating image files or changing server-side translation output. Add a GUI report button so operators can inspect the generated mapping before running replacement.

**Tech Stack:** Python 3.12, Tkinter, existing Shopify storefront `.js` fetches, existing EZ/TAA CDP automation, pytest, Wine/PyInstaller release helper.

---

### Task 1: Mapping Model And Tests

**Files:**
- Create: `tools/shopify_image_localizer/domain_image_mapping.py`
- Modify: `tests/test_shopify_image_localizer_batch_cdp.py`

- [ ] Add tests for carousel alias mapping:
  - default URL `https://cdn.shopify.com/s/files/1/default/files/aaa.jpg?v=1`
  - target URL `https://cdn.shopify.com/s/files/1/other/files/zzz.jpg?v=2`
  - localized filename `from_url_en_00_aaa.jpg`
  - expected pair maps target slot `0` to localized source index `0`.
- [ ] Add tests for detail alias mapping:
  - target detail HTML uses target CDN token.
  - source index map points target token to canonical detail index.
  - `taa_cdp.plan_body_html_replacements()` chooses the localized image by source index.
- [ ] Run the targeted tests and confirm they fail before implementation:
  `pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_uses_domain_alias_source_index tests/test_shopify_image_localizer_batch_cdp.py::test_detail_plan_uses_domain_alias_source_index_when_token_differs -q`

### Task 2: Mapping Implementation

**Files:**
- Create: `tools/shopify_image_localizer/domain_image_mapping.py`
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- Modify: `tools/shopify_image_localizer/rpa/taa_cdp.py`

- [ ] Implement `DomainImageMapping`, `ImageAlias`, `build_domain_image_mapping()`, and `summarize_domain_image_mapping()`.
- [ ] Extend `pair_carousel_images()` and `_choose_carousel_candidate()` to consume optional mapping aliases.
- [ ] Extend detail source index map generation so target-domain tokens/name keys can resolve to canonical source indexes.
- [ ] Change `plan_body_html_replacements()` so token mismatch can still choose by mapped source index.
- [ ] Re-run the failing tests and confirm they pass.

### Task 3: Run Flow Integration

**Files:**
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- Test: `tests/test_shopify_image_localizer_batch_cdp.py`

- [ ] In `run()`, fetch the default-domain English product as canonical when `args.store_domain != settings.DEFAULT_SHOPIFY_DOMAIN`.
- [ ] Build the mapping before carousel pairing and detail planning.
- [ ] Add mapping summary fields into the result JSON under `domain_image_mapping`.
- [ ] Keep default-domain runs unchanged by returning an empty/default mapping.

### Task 4: GUI Mapping Report

**Files:**
- Modify: `tools/shopify_image_localizer/controller.py`
- Modify: `tools/shopify_image_localizer/gui.py`
- Test: `tests/test_shopify_image_localizer_domains.py`

- [ ] Add `controller.preview_domain_image_mapping(product_code, shopify_domain)` that fetches default and target English storefront products and returns a summary.
- [ ] Add a “映射管理” button near EZ/TAA buttons.
- [ ] Button opens a modal report with carousel/detail mapped counts and low-confidence/missing rows.
- [ ] Disable the button while a replacement task is running.

### Task 5: Version And Release

**Files:**
- Modify: `tools/shopify_image_localizer/version.py`
- Use: `scripts/build_shopify_image_localizer_wine.sh`

- [ ] Bump `RELEASE_VERSION` from `3.32` to the next version.
- [ ] Run focused pytest and py_compile checks.
- [ ] Build and publish with:
  `bash scripts/build_shopify_image_localizer_wine.sh --version <next-version> --release-note "多域名图片映射：第二/更多域名复用默认域名翻译图自动换图"`
- [ ] Verify the helper reports HTTP `200` or `206` for the uploaded zip.

