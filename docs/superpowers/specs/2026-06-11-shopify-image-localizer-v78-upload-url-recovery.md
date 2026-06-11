# 2026-06-11 Shopify Image Localizer V7.8 Upload URL Recovery

## Background

`insulating-base-in-solid-wood-rjc` on `newjoyloo.com` fails during French TAA
detail-image replacement after the detail plan has already matched seven images.
The desktop log stops at:

```text
RuntimeError: uploaded CDN URL not found for
20260611_41e12eac_20260611_b7a82361_from_url_en_11_1_945b8773-c9d0-4f0d-9be0-01d866496fcd.png
```

The Shopify storefront detail HTML uses UUID-style filenames such as
`1_945b8773-c9d0-4f0d-9be0-01d866496fcd.jpg`. Bootstrap metadata can still
pair these images by `source_index` and `source_name_key`, but `source_token`
is `null` because there is no 28+ character continuous hex token. The current
TAA upload detector only accepts uploaded CDN URLs when the URL contains the
local basename, local stem, or long token, so it can miss a successful upload
when Shopify renames the file.

## Goal

Release V7.8 so TAA detail uploads can recover the newly uploaded Shopify CDN
URL for UUID-style source images that have no long source token, while still
rejecting stale CDN URLs from previous uploads.

## Requirements

- Keep the existing strict token/name matching behavior when it can identify the
  current upload.
- Before setting the file input, capture the image URLs currently visible in the
  insert-image modal.
- After upload, when strict name/token matching finds no URL, choose a newly
  visible Shopify CDN image URL that was not present before the upload.
- Only use the modal-diff fallback when exactly one new Shopify CDN URL appears.
- Do not accept arbitrary prior CDN network events.
- Preserve the existing `uploaded CDN URL not found` failure when there is no
  strict match and no unique new modal image.
- Do not change detail-image pairing, carousel pairing, image generation,
  Bootstrap API semantics, or TAA save verification.
- Bump `tools/shopify_image_localizer/version.py` from `7.7` to `7.8`.
- Do not connect to Windows local MySQL for verification.

## Anchors

- `AGENTS.md`: project-local worktree isolation, document-driven changes, no
  Windows local MySQL, focused pytest policy.
- `tools/shopify_image_localizer/CLAUDE.md`: TAA detail success is based on
  current TAA upload, save, and readable HTML.
- `docs/superpowers/specs/2026-06-08-shopify-image-localizer-taa-three-surface-consistency.md`:
  persisted TAA detail HTML must contain the new URLs.
- `docs/superpowers/specs/2026-06-09-shopify-image-localizer-v75-bootstrap-metadata-design.md`:
  UUID-style candidates can be paired through source metadata instead of token.
- `docs/superpowers/specs/2026-06-11-shopify-image-localizer-v77-skip-not-ready-carousel-fix.md`:
  V7.7 is the current desktop baseline.
- `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`:
  release packaging and verification standard.

## Verification

- Unit test proves a tokenless upload returns the one new Shopify CDN image from
  the modal when network events do not include a strict filename match.
- Existing unit test still proves stale previous CDN URLs are rejected.
- Focused test command:

```bash
python -m pytest tests/test_shopify_image_localizer_batch_cdp.py::test_taa_upload_image_rejects_unmatched_previous_cdn_url tests/test_shopify_image_localizer_batch_cdp.py::test_taa_upload_image_recovers_single_new_modal_cdn_url_without_token -q
```

- Broader localizer verification:

```bash
python -m pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_release_web.py -q
python -m compileall tools/shopify_image_localizer
```

- Release V7.8 using the release standard and verify the published zip is
  reachable by HTTP range request.
