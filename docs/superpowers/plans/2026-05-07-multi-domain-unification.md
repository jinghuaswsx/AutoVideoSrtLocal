# Multi-Domain Product Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make product links, push payloads, link checks, image confirmation, and Shopify Image Localizer tasks consistently honor per-product enabled domains.

**Architecture:** `appcore.product_link_domains` is the single resolver for product page URLs and stable domain-language status keys. Push/OpenAPI/link-check/Shopify image services consume resolver outputs instead of hard-coded `newjoyloo.com` URL templates. Image replacement remains per language, while link checks and human confirmation are tracked per `domain:lang`.

**Tech Stack:** Python service modules, Flask route services, existing JSON fields, MySQL migration already present, vanilla JS material-management frontend, pytest-first verification.

---

### Task 1: Resolver Contract

**Files:**
- Modify: `appcore/product_link_domains.py`
- Test: `tests/test_product_link_domains.py`

- [ ] Add tests for stable `status_key(domain, lang)`, URL row expansion for enabled domains, and parsing domain/lang from product URLs.
- [ ] Implement helpers: `domain_lang_key`, `parse_domain_lang_key`, `resolve_product_page_url_rows`, and product URL matching helpers.
- [ ] Keep `newjoyloo.com` as backward-compatible fallback only when the domain tables are unavailable.

### Task 2: Push Payloads

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `web/services/openapi_push_items.py`
- Test: `tests/test_product_link_push.py`
- Test: `tests/test_openapi_push_items_service.py`

- [ ] Add failing tests showing multi-domain `product_links` in direct product-link push, unsuitable product error links, material payload OpenAPI, and item payloads.
- [ ] Route every generated product URL through the resolver.
- [ ] Preserve legacy single-URL API helpers by returning the first resolved row.

### Task 3: Link Check By Domain-Language

**Files:**
- Modify: `appcore/medias.py`
- Modify: `web/services/media_link_check.py`
- Modify: `web/services/openapi_link_check.py`
- Test: `tests/test_appcore_medias_link_check_bootstrap.py`
- Test: `tests/test_media_link_check_service.py`
- Test: `tests/test_link_check_bootstrap_routes.py`

- [ ] Add tests for `omurio.com/de/products/<handle>` mapping to the same product through enabled domain configuration.
- [ ] Store product link-check task metadata under `domain:lang`; legacy `lang` lookups still read old data.
- [ ] Include `domain`, `status_key`, and resolved link row metadata in summaries and bootstrap responses.

### Task 4: Shopify Image Status By Domain-Language

**Files:**
- Modify: `appcore/shopify_image_tasks.py`
- Modify: `web/services/openapi_shopify_localizer.py`
- Modify: `web/services/media_shopify_image.py`
- Test: `tests/test_appcore_shopify_image_tasks.py`
- Test: `tests/test_openapi_shopify_localizer_service.py`
- Test: `tests/test_media_shopify_image_service.py`

- [ ] Add tests that `resolve_link_urls` returns all enabled domain URLs for a language.
- [ ] Keep replacement queue per language, but initialize/update link status rows for every enabled `domain:lang`.
- [ ] Let confirmation/unavailable/clear accept an optional domain and update exactly that domain-language status.
- [ ] Push readiness must require all enabled domain-language statuses to be confirmed/normal for non-English languages.

### Task 5: Shopify Image Localizer Desktop Contract

**Files:**
- Modify: `tools/shopify_image_localizer/api_client.py`
- Modify: `tools/shopify_image_localizer/controller.py`
- Test: `tests/test_shopify_image_localizer_batch_cdp.py`
- Test: `tests/test_shopify_image_worker_loop.py`

- [ ] Add tests that bootstrap/task claim payloads expose `link_urls` plus the legacy first `link_url`.
- [ ] Keep the RPA run itself unchanged unless a specific storefront URL is needed; it should receive the first link URL for compatibility and the full list for reporting.
- [ ] Worker completion should report one replacement result, while server updates all domain-language statuses to review state.

### Task 6: Material Management UI

**Files:**
- Modify: `web/static/medias.js`
- Modify: `web/routes/medias/_serializers.py`
- Test: `tests/test_product_link_push.py`
- Test: `tests/test_medias_link_check_routes.py`

- [ ] Serialize `link_check_tasks` and `shopify_image_status` with legacy lang data plus domain-language entries.
- [ ] Product detail serialization exposes the product's enabled link domains so the edit modal can render one Shopify image confirmation row per enabled domain even before `shopify_image_status_json` has a `domain:lang` entry.
- [ ] Show domain labels in link-check and Shopify image status blocks.
- [ ] Add domain selection to link check and Shopify image confirm/unavailable/requeue actions where needed.

### Task 7: Verification

**Files:**
- No production changes.

- [ ] Run focused pytest suites for product links, pushes, link check, Shopify image service, desktop localizer, and affected routes.
- [ ] Run `py_compile` for modified Python modules.
- [ ] Run `node --check web/static/medias.js`.
- [ ] Run `git diff --check`.
