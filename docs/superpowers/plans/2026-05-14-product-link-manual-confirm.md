# Product Link Manual Confirm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-link manual confirmation button in the product links modal so operators can mark a server-failed but browser-valid product URL as usable.

**Architecture:** Reuse the existing product link availability POST endpoint with a `manual_confirm` body flag. Persist manual confirmations through `appcore.link_availability` into the existing `media_product_link_availability` table, then render one frontend action per row using each row's `domain`.

**Tech Stack:** Python 3.12, Flask service builders, pytest, vanilla JS in `web/static/medias.js`.

---

### Task 1: Backend Manual Confirmation

**Files:**
- Modify: `appcore/link_availability.py`
- Modify: `web/services/media_link_check.py`
- Test: `tests/test_appcore_link_availability.py`
- Test: `tests/test_medias_link_availability_routes.py`

- [ ] Add a failing test that calls `link_availability.manual_confirm_result(product_id=7, lang="DE", domain="NewJoyLoo.com", link_url="https://newjoyloo.com/de/products/demo")` and asserts the captured SQL args include lowercase `de`, lowercase `newjoyloo.com`, `http_status=200`, `ok=1`, `error="manual_confirmed"`, and `elapsed_ms=0`.
- [ ] Run `pytest tests/test_appcore_link_availability.py::test_manual_confirm_result_marks_domain_ok -q` and verify it fails because `manual_confirm_result` does not exist.
- [ ] Implement `manual_confirm_result()` as a small wrapper around `upsert_result()`.
- [ ] Add a service route test that posts `{"domain":"omurio.com","manual_confirm":true}` and asserts only `omurio.com` is confirmed while the response still includes all enabled domains.
- [ ] Run the focused tests and verify they pass.

### Task 2: Frontend Per-Row Button

**Files:**
- Modify: `web/static/medias.js`
- Test: `tests/test_medias_shopify_image_status_routes.py`

- [ ] Add a failing string-level test that extracts `edProductLinksRowActions` and asserts it contains `data-product-links-action="confirm-link"` inside the row action builder.
- [ ] Run `pytest tests/test_medias_shopify_image_status_routes.py::test_product_links_modal_renders_per_row_manual_link_confirm_button -q` and verify it fails.
- [ ] Add a `确认链接正常` button to `edProductLinksRowActions(lang, item)` with `data-domain="${escapeHtml(item.domain)}"`.
- [ ] Add an `edConfirmProductLinkNormal(domain)` helper that POSTs `{ domain, manual_confirm: true }` to the existing link availability endpoint and refreshes modal state from the returned `items`.
- [ ] Route `confirm-link` in `edHandleProductLinksAction()` to the new helper.
- [ ] Update badge labeling so `ok=true` and `error==="manual_confirmed"` displays `人工确认正常`.
- [ ] Run the focused frontend string test and verify it passes.

### Task 3: Regression Verification

**Files:**
- Verify: `tests/test_appcore_link_availability.py`
- Verify: `tests/test_medias_link_availability_routes.py`
- Verify: `tests/test_media_product_mutations_ad_lang_precheck.py`
- Verify: `tests/test_medias_shopify_image_status_routes.py`

- [ ] Run:

```bash
pytest tests/test_appcore_link_availability.py \
       tests/test_medias_link_availability_routes.py \
       tests/test_media_product_mutations_ad_lang_precheck.py \
       tests/test_medias_shopify_image_status_routes.py -q
```

- [ ] Confirm the command exits 0 before reporting completion.
