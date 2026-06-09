# 2026-06-09 Shopify Image Localizer V7.5 Bootstrap Metadata Design

## Background

V7.4 fixed the live `pet-bath-brush-rjc` carousel order by normalizing wrapped source filenames and routing unresolved duplicate-token carousel slots to visual fallback. The live incident also exposed a server/API hardening gap: the Shopify Image Localizer bootstrap response still returns a flat candidate pool with only `id`, `kind`, `filename`, and `url`.

For `pet-bath-brush-rjc`, the storefront has 11 carousel images, while the backend candidate pool can contain 13 detail rows because the English source download set includes duplicate-source-token rows and non-carousel detail/trust rows. Returning 13 rows is valid; forcing the backend to deduplicate down to 11 would remove useful candidates and make visual fallback weaker. The bug class is not "too many downloaded images"; it is "the client has to infer source identity from ambiguous filenames after download."

## Goal

Release V7.5 as a hardening release so backend bootstrap data carries enough source identity for the desktop client to pair carousel/detail candidates deterministically, while keeping the full candidate pool available for visual fallback.

## Requirements

- Keep the backend candidate pool complete. Do not force it to match storefront carousel count and do not drop duplicate-token rows only because a duplicate token exists.
- Use the Shopify-specific image list for bootstrap defaults so GIF rows are filtered before reaching the desktop tool.
- Add non-breaking metadata to each serialized detail candidate:
  - `source_index`: integer parsed from `from_url_en_<n>_`, or `null`.
  - `source_name_key`: normalized source-name key compatible with the V7.4 wrapped-extension rules, or `null`.
  - `source_token`: the desktop matcher-compatible source token parsed from filename, or `null`. This follows existing `ez_cdp.md5_token()` behavior and accepts 28+ continuous hex characters so legacy Shopify/Freshify names such as `Sb0d7...D_1.webp` keep matching the desktop client.
  - `source_duplicate_count`: number of serialized candidates in the same language response sharing `source_token`.
  - `source_duplicate`: boolean true when `source_duplicate_count > 1`.
- Keep existing fields unchanged (`id`, `kind`, `filename`, `url`) so older clients remain compatible.
- Update desktop candidate builders to prefer server-provided `source_index`, `source_name_key`, and `source_token` when present, falling back to filename parsing for older bootstrap responses and local fallback files.
- Preserve V7.4 duplicate-token behavior: if duplicate-token carousel candidates still cannot be resolved by source index or name key, do not silently choose the first candidate; allow visual fallback to decide.
- Bump the desktop release version to V7.5 after tests pass.

## Non-goals

- Do not change Shopify storefront scraping or carousel image counting.
- Do not change image generation, translation, or CDN upload behavior.
- Do not remove existing visual fallback.
- Do not connect to local Windows MySQL for verification.

## Anchors

- `AGENTS.md`: document-driven change, worktree isolation, Shopify Image Localizer release reference, no local MySQL, focused pytest policy.
- `tools/shopify_image_localizer/CLAUDE.md`: Shopify Image Localizer module development and release gates.
- `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`: EXE and portable ZIP release standard.
- `docs/superpowers/specs/2026-06-09-shopify-image-localizer-v74-carousel-order-fix.md`: V7.4 incident root cause and carousel pairing rules.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: focused pytest policy.

## Verification

- Service unit test proves bootstrap serialization adds source metadata, keeps duplicate rows, annotates duplicate tokens, and filters GIFs through the Shopify-specific image list.
- Client unit test proves candidate builders prefer backend `source_index`, `source_name_key`, and `source_token` metadata over filename inference.
- Existing carousel duplicate-token tests keep passing.
- Focused test command:
  `pytest tests/test_openapi_shopify_localizer_service.py tests/test_openapi_materials_routes.py tests/test_shopify_image_localizer_batch_cdp.py -q`
- Packaging/release web tests run before release:
  `pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py tests/test_shopify_image_localizer_release_web.py -q`
