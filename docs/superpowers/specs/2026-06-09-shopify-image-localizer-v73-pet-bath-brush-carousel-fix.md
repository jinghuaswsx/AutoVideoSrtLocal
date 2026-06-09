# 2026-06-09 Shopify Image Localizer V7.3 Pet Bath Brush Carousel Fix

## Background

`pet-bath-brush-rjc` on `newjoyloo.com` fails in Shopify Image Localizer V7.2 during carousel pairing before it can finish German, French, or Italian replacement.

The failure is deterministic. The storefront product has carousel slot 7 using source token `b0d7cac6bbce4313a7ff2883a7818803d`, while the bootstrap localized images contain two localized candidates for the same source token: `from_url_en_00` and `from_url_en_01`. V7.2 treats this as an ambiguous carousel source and raises before the run can continue.

## Goal

Release V7.3 so `pet-bath-brush-rjc` can complete the full replacement run successfully.

## Requirements

- Keep carousel replacement deterministic when multiple localized candidates share the same source token.
- Preserve existing exact slot/source-index preference when the carousel slot matches one candidate directly.
- When the source token is exact but the carousel slot index does not match any candidate, pick a stable same-token candidate instead of raising `ambiguous carousel source`.
- Keep the fix scoped to Shopify Image Localizer carousel pairing and its tests.
- Verify with focused Shopify Image Localizer tests, not full default pytest unless a release/merge gate requires it.

## Anchors

- `tools/shopify_image_localizer/CLAUDE.md`: Shopify Image Localizer development, testing, and release gates.
- `docs/superpowers/specs/2026-06-08-shopify-image-localizer-taa-three-surface-consistency.md`: V7.x exact product-link and three-surface verification expectations.
- `docs/superpowers/specs/2026-05-11-shopify-image-localizer-domain-image-mapping.md`: carousel/detail image source identity and fallback rules.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: focused pytest policy.

## Verification

- `pytest tests/test_shopify_image_localizer_batch_cdp.py::test_pair_carousel_images_uses_same_token_fallback_when_duplicate_detail_sources_do_not_match_slot -q`
- `pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_domains.py -q`
- `python -m compileall tools/shopify_image_localizer`
- V7.3 release package validation per `tools/shopify_image_localizer/CLAUDE.md`.
- Live run for `pet-bath-brush-rjc` on `newjoyloo.com` for German, French, and Italian, with product-link verification for the material-library URLs.
