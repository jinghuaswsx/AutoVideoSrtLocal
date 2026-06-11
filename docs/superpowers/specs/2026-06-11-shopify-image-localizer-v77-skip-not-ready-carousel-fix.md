# 2026-06-11 Shopify Image Localizer V7.7 Skip Not Ready and Carousel Ambiguity Fix

## Background

`handle-v-3987406-rjc` fails in Shopify Image Localizer V7.6 during the German carousel phase after localized images have already downloaded. The desktop log shows:

```text
ValueError: ambiguous carousel filename source for slot 6 key name:o1cn017uipld11mjwvsbe0w_1116044805-0-cib:
['4: ... from_url_en_04_...png', '13: ... from_url_en_13_...jpg']
```

This is the duplicate-source class that V7.4 intended to route into the visual fallback path. It must not fail the whole language before visual matching can inspect the current Shopify slot image.

The same batch workflow also still polls bootstrap until timeout when a target language's localized material is not ready. For batch "all languages" runs, a specific language that is not ready within the current bootstrap response should be skipped immediately because image translation material normally will not become ready during the next few minutes.

## Goal

Release Shopify Image Localizer V7.7 so `handle-v-3987406-rjc` can continue past ambiguous carousel filename matches, and batch runs skip not-ready languages without waiting for the long bootstrap timeout.

## Requirements

- Keep current successful carousel pairing behavior when token, source index, or source name has one deterministic candidate.
- When carousel source-name candidates remain ambiguous after preferred index checks, return no direct candidate for that slot and let the existing visual carousel fallback handle it.
- Do not silently choose the first ambiguous candidate.
- Treat explicit bootstrap not-ready conditions as language-level skip:
  - `localized images not ready` / `localized_images_not_ready`
  - `english references not ready` / `english_references_not_ready`
  - successful bootstrap payload with zero `localized_images`
- Keep hard failures hard:
  - missing Shopify product ID
  - product not found
  - invalid language
  - network/server errors without a not-ready code
- In GUI batch mode, a skipped language counts separately from success and failure, continues to the next language, and does not force a browser restart.
- Keep single-language runs visible to the operator as skipped/not-ready rather than pretending replacement succeeded.
- Bump `tools/shopify_image_localizer/version.py` from `7.6` to `7.7` after tests pass.
- Do not connect to Windows local MySQL for verification.

## Non-goals

- Do not change image generation or translation material production.
- Do not change Shopify Admin login, CDP startup, or packaging credential rules.
- Do not alter existing detail-image replacement matching except where it consumes a skipped bootstrap result.
- If detail-image replacement uploads and saves all expected new URLs, but the storefront still contains legacy non-Shopify images that had no localizable candidate or fallback source, report them as tolerated leftovers instead of failing the whole language. Keep failing when no expected replacement was produced, when expected new URLs are missing, or when persisted non-Shopify leftovers exceed the count of missing/unmatched detail images.
- Do not add a new background monitor for material readiness.

## Anchors

- `AGENTS.md`: worktree isolation, document-driven changes, no local Windows MySQL, focused pytest policy.
- `tools/shopify_image_localizer/CLAUDE.md`: desktop localizer CDP, batch language, and release gates.
- `docs/superpowers/specs/2026-06-09-shopify-image-localizer-v74-carousel-order-fix.md`: ambiguous carousel candidates must fall through to visual fallback instead of choosing an unsafe candidate.
- `docs/superpowers/specs/2026-06-10-shopify-image-localizer-cdp-handshake-recovery.md`: current V7.6 baseline.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: focused pytest policy.

## Verification

- Unit test proves ambiguous carousel source-name candidates return no direct pair so visual fallback receives the slot.
- Unit test proves bootstrap `localized_images_not_ready` raises a skip signal without sleeping/retrying.
- GUI unit test proves a skipped language continues to the next selected language, reports skipped separately, and does not restart Chrome.
- Unit test proves storefront detail verification tolerates only unmatched legacy leftovers after all expected replacements are present.
- Focused verification:

```bash
python scripts/pytest_related.py --base origin/master --run
python -m compileall tools/shopify_image_localizer
```

- Local debug run:
  - Start `python -m tools.shopify_image_localizer.main`.
  - Product: `handle-v-3987406-rjc`.
  - Shopify ID: `8606980374701`.
  - Select all languages.
  - Confirm German no longer fails at `ambiguous carousel filename source`.

- Release verification follows `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md` for V7.7.
