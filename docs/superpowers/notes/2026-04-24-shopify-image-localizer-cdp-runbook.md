# Shopify Image Localizer CDP Runbook

Updated: 2026-04-24

## Production API

This feature must use production only:

```text
http://172.30.254.14/openapi/medias/shopify-image-localizer/bootstrap
```

Do not use the `:8080` test service for this workflow.

## Main Runner

Use the combined runner for carousel plus detail images:

```powershell
python -m tools.shopify_image_localizer.rpa.run_product_cdp --product-code sonic-lens-refresher-rjc --lang it --bootstrap-timeout-s 60 --skip-existing-carousel --replace-shopify-cdn
```

Behavior:

- Fetches Shopify product id from the storefront JSON.
- Fetches localized image material from the production bootstrap API.
- Runs EZ Product Image Translate first for carousel images.
- Runs Translate & Adapt second for `body_html` detail images.
- Replaces the translated detail HTML in one save.
- Preserves the existing `<img>` tag and style; for small displayed images, captures the storefront rendered width and writes it back as `width: Npx; max-width: 100%; height: auto;`.
- For static detail images that exist on the storefront but are missing from the server material library, downloads the current original image and uploads it to Shopify CDN as a fallback instead of mismatching it with another translated asset.
- Is idempotent: reruns should skip carousel images that already have the target language and skip detail images already localized on Shopify CDN.

## Verified Sample

Product:

```text
sonic-lens-refresher-rjc
lang=it
shopify_product_id=8559391932589
```

Important production data fix applied:

- `media_products.product_code='sonic-lens-refresher-rjc'`
- `shopifyid` was missing and was set to `8559391932589`

Material status:

- Initial bootstrap failed with `409 shopify_product_id_missing`.
- After DB fix, bootstrap exposed the real blocker: `409 localized images not ready`.
- A production image-translate task generated Italian detail assets.
- Final bootstrap returned ready: 20 reference images, 16 localized images.

Replacement result:

- Carousel: 11 slots have Italian translations. Idempotent rerun skipped all 11 as existing.
- Detail page: 10 `<img>` tags, 0 non-Shopify external image URLs after replacement.
- Detail static replacements:
  - `4c1b964e7f14f17aaf40126d843b393b` -> Italian asset
  - `dcd82b9a379559d5e186a02a6c6402e7` -> Italian asset
  - `0e131aa5fdc9483e473de0c1559e7ce6` -> Italian asset
  - `47bfbe011e38846379be9dc32e420868` -> Italian asset
  - `e91c999470fd206bac418a40a6d21c2fad3252bc` -> Italian asset
  - `d4455d8c67044f5c6a9a122179cb4c34e07e767d` -> fallback original uploaded to Shopify CDN because no English reference/localized material exists for it in the server library.
- GIF detail images were left unchanged.
- Payment/trust small images retained rendered widths.

Result artifact:

```text
sonic-lens-refresher-rjc/it/shopify_batch_it_result.json
```

## Verification Commands

Syntax and focused tests:

```powershell
python -m py_compile tools\shopify_image_localizer\settings.py tools\shopify_image_localizer\gui.py tools\shopify_image_localizer\rpa\ez_cdp.py tools\shopify_image_localizer\rpa\taa_cdp.py tools\shopify_image_localizer\rpa\run_product_cdp.py
pytest tests\test_shopify_image_localizer_batch_cdp.py -q
```

Storefront spot check:

```powershell
python - <<'PY'
import json, re, urllib.request
url = 'https://newjoyloo.com/it/products/sonic-lens-refresher-rjc.js'
req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,*/*'})
data = json.loads(urllib.request.urlopen(req, timeout=30).read())
html = data.get('description') or data.get('body_html') or ''
srcs = [m.group(2) for m in re.finditer(r'<img\b[^>]*\bsrc\s*=\s*([\"\'])(.*?)\1', html, re.I)]
print('image_count', len(srcs))
print('external_non_shopify', [src for src in srcs if 'cdn.shopify.com/s/files/' not in src])
PY
```

Expected for the verified sample:

```text
image_count 10
external_non_shopify []
```

## Notes

- External Chrome with CDP is viable when Chrome is launched as the user's normal profile and Playwright connects to the existing CDP endpoint. This path worked for both EZ and TAA.
- Playwright-owned Chrome remains unreliable for Shopify embedded apps.
- The extension route is still unnecessary for this workflow.
- PyAutoGUI should be kept as fallback, not the primary route.
- The TAA detail path should keep using whole-HTML replacement, not per-image rich editor clicking.
