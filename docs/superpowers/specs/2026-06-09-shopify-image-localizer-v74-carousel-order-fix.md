# 2026-06-09 Shopify Image Localizer V7.4 Carousel Order Fix

## Background

`pet-bath-brush-rjc` on `newjoyloo.com` has 11 Shopify carousel images in the storefront product JSON. The English detail body also reuses one carousel image, `Sb0d7cac6bbce4313a7ff2883a7818803D_1.webp`.

V7.3 fixed the V7.2 crash caused by duplicate token `b0d7cac6bbce4313a7ff2883a7818803d`, but live verification found the carousel replacement order was still wrong. The localized filenames keep the original source extension before the generated output extension, such as `from_url_en_02_1_...webp.jpg`. The current `source_name_key()` compares this as `1_...webp` against the storefront source key `1_...`, so filename matching fails and the code falls back to `source_index`.

For this product, source indexes are not carousel slot indexes because the source list contains two duplicated `b0d7` rows before the normal carousel sequence. The fallback therefore maps carousel slot 0 to `from_url_en_00` and slot 1 to `from_url_en_01`, shifting the carousel away from the English product order.

## Goal

Release V7.4 so `pet-bath-brush-rjc` can complete replacement and keep all 11 carousel slots aligned with the English product image order.

## Requirements

- Canonicalize source-name matching so wrapped filenames like `foo.webp.jpg`, `foo.webp.png`, and Shopify-uploaded `foo_webp.webp` compare to the original source basename `foo`.
- Prefer exact filename/name-key matches over source-index fallback for carousel pairing.
- When one carousel source token has multiple localized candidates and none matches the current slot or source index, do not silently choose the first candidate. Leave that slot unmatched so the existing visual fallback can match the actual Shopify image to the correct server reference.
- For duplicate-token sources such as `pet-bath-brush-rjc` carousel slot 7, do not hard-code a numeric `from_url_en_*` choice. The English reference rows and localized rows can come from different download/generation batches, so duplicate ordering may drift. The final choice must follow the existing visual chain: current Shopify slot image -> best English reference image -> best localized candidate.
- Keep normal carousel reruns default-safe by skipping slots that already have the target language marker. Add explicit `--force-existing-carousel` incident-repair mode so V7.4 can overwrite V7.3's wrong carousel mappings for `pet-bath-brush-rjc`.
- Keep the fix scoped to Shopify Image Localizer pairing, visual fallback routing, tests, and V7.4 release metadata.

## Anchors

- `AGENTS.md`: document-driven change, worktree isolation, Shopify Image Localizer release reference, and targeted pytest policy.
- `tools/shopify_image_localizer/CLAUDE.md`: Shopify Image Localizer development, carousel replacement, and release gates.
- `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`: Windows EXE release and ZIP verification standard.
- `docs/superpowers/specs/2026-06-08-shopify-image-localizer-taa-three-surface-consistency.md`: exact product-link and persisted replacement verification expectations.
- `docs/superpowers/specs/2026-06-09-shopify-image-localizer-v73-pet-bath-brush-carousel-fix.md`: previous V7.3 duplicate-token fallback that V7.4 supersedes.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: focused pytest policy.

## Verification

- Focused unit coverage for `source_name_key()` wrapped-extension normalization.
- Focused unit coverage for pet-bath-brush carousel pairing order.
- Focused unit coverage that duplicate token candidates without a slot/source-index match are routed to visual fallback instead of stable first-candidate selection.
- Focused unit coverage that default carousel replacement skips existing target-language markers while forced carousel replacement reprocesses them.
- `pytest tests/test_shopify_image_localizer_batch_cdp.py -q`
- Release package validation per `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`.
- Live run for `pet-bath-brush-rjc` and post-run verification that the 11 carousel mappings match the English product `.js` order.
