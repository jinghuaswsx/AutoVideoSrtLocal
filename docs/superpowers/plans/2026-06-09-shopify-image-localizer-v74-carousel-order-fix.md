# Shopify Image Localizer V7.4 Carousel Order Fix Plan

## Goal

Release V7.4 and verify `pet-bath-brush-rjc` replaces all 11 carousel images in the correct English product order.

## Plan

1. Add regression tests for wrapped source filename matching and duplicate-token visual fallback routing.
2. Fix source-name canonicalization in `tools/shopify_image_localizer/rpa/taa_cdp.py`.
3. Change carousel duplicate-token handling in `tools/shopify_image_localizer/rpa/run_product_cdp.py` so uncertain duplicate-token slots are left unmatched for visual fallback.
4. Bump `tools/shopify_image_localizer/version.py` to `7.4`.
5. Run focused Shopify Image Localizer tests and compile checks.
6. Merge to `master`, publish the V7.4 portable ZIP using the release-standard Wine build, and verify ZIP config/static range access.
7. Run `pet-bath-brush-rjc` replacement and inspect the 11 carousel mappings against the English `.js` order.

## Docs Anchor

`docs/superpowers/specs/2026-06-09-shopify-image-localizer-v74-carousel-order-fix.md`
