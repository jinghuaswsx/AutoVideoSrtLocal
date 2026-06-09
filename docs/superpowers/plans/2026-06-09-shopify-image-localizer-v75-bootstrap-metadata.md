# Shopify Image Localizer V7.5 Bootstrap Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add non-breaking backend source metadata and client-side metadata preference so `pet-bath-brush-rjc` duplicate-token carousel candidates remain complete but no longer require filename guessing.

**Architecture:** The OpenAPI bootstrap service remains the API boundary and serializes source identity next to each image candidate. The desktop RPA candidate builders prefer those explicit fields, while retaining filename parsing for older responses and local fallback images.

**Tech Stack:** Python 3.12, Flask service layer, Shopify Image Localizer desktop RPA, pytest.

---

## File Structure

- Modify `web/services/openapi_shopify_localizer.py`: serialize `source_index`, `source_name_key`, `source_token`, and duplicate metadata; default bootstrap image listing to `medias.list_shopify_localizer_images`.
- Modify `web/routes/openapi_materials.py`: pass the Shopify-specific image list to the service.
- Modify `tools/shopify_image_localizer/rpa/taa_cdp.py`: prefer server metadata in token/source-index candidate builders.
- Modify `tools/shopify_image_localizer/rpa/run_product_cdp.py`: prefer server metadata in carousel name-key candidate builder and detail source-index map.
- Modify `tools/shopify_image_localizer/version.py`: bump `RELEASE_VERSION` to `7.5`.
- Test `tests/test_openapi_shopify_localizer_service.py`: backend metadata and duplicate annotation.
- Test `tests/test_openapi_materials_routes.py`: route delegates Shopify-specific image listing.
- Test `tests/test_shopify_image_localizer_batch_cdp.py`: client candidate builders prefer metadata.

## Tasks

### Task 1: Backend Metadata Red Test

- [ ] Add a failing service test that injects duplicate-token rows and asserts `source_index`, `source_name_key`, `source_token`, `source_duplicate_count`, and `source_duplicate`.
- [ ] Add a failing route test that asserts the bootstrap route passes `medias.list_shopify_localizer_images` instead of `medias.list_reference_images_for_lang`.
- [ ] Run:
  `pytest tests/test_openapi_shopify_localizer_service.py tests/test_openapi_materials_routes.py -q`
- [ ] Expected result before implementation: failures showing missing metadata and wrong route image-list function.

### Task 2: Backend Metadata Implementation

- [ ] Add small local helpers in `web/services/openapi_shopify_localizer.py` for source-index, source-name-key, and 32-hex token extraction.
- [ ] Extend `_serialize_detail_images()` to compute duplicate counts after filtering valid detail rows.
- [ ] Change default bootstrap image list function to `medias.list_shopify_localizer_images`.
- [ ] Change `web/routes/openapi_materials.py` to pass `medias.list_shopify_localizer_images`.
- [ ] Run:
  `pytest tests/test_openapi_shopify_localizer_service.py tests/test_openapi_materials_routes.py -q`
- [ ] Expected result after implementation: service and route tests pass.

### Task 3: Client Metadata Red Test

- [ ] Add a failing RPA test where candidate filenames are not parseable but server metadata has the correct `source_index`, `source_name_key`, and `source_token`.
- [ ] Run:
  `pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_uses_bootstrap_source_metadata_when_filename_is_ambiguous -q`
- [ ] Expected result before implementation: no pair or wrong pair because the client ignores server metadata.

### Task 4: Client Metadata Implementation

- [ ] Update `taa_cdp.build_localized_candidates()` to use `item["source_token"]` first and fall back to `ez_cdp.md5_token(filename)`.
- [ ] Update `taa_cdp.build_localized_candidates_by_source_index()` to use `item["source_index"]` first and fall back to filename parsing.
- [ ] Update `run_product_cdp._localized_by_source_name_key()` to use `item["source_name_key"]`, `item["source_index"]`, and `item["source_token"]` first.
- [ ] Update `run_product_cdp.build_detail_source_index_map()` to use reference metadata when present.
- [ ] Run:
  `pytest tests/test_shopify_image_localizer_batch_cdp.py -q`
- [ ] Expected result after implementation: existing carousel and detail tests still pass.

### Task 5: Version And Verification

- [ ] Set `tools/shopify_image_localizer/version.py` to `RELEASE_VERSION = "7.5"`.
- [ ] Run focused verification:
  `pytest tests/test_openapi_shopify_localizer_service.py tests/test_openapi_materials_routes.py tests/test_shopify_image_localizer_batch_cdp.py -q`
- [ ] Run release-adjacent verification:
  `pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_release_web.py -q`
- [ ] Run compile check:
  `python -m compileall web/services/openapi_shopify_localizer.py tools/shopify_image_localizer/rpa/taa_cdp.py tools/shopify_image_localizer/rpa/run_product_cdp.py`
- [ ] Commit with:
  `git commit -m "fix(shopify-localizer): add v7.5 bootstrap source metadata" -m "Docs-anchor: docs/superpowers/specs/2026-06-09-shopify-image-localizer-v75-bootstrap-metadata-design.md"`
