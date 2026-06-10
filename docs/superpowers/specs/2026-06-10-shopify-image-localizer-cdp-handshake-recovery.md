# 2026-06-10 Shopify Image Localizer CDP Handshake Recovery

## Background

An operator screenshot from Shopify Image Localizer V7.3 shows a German carousel replacement failing before any image operation starts:

```text
BrowserType.connect_over_cdp: Timeout 180000ms exceeded.
Call log:
- <ws connecting> ws://127.0.0.1:7777/devtools/browser/...
- <ws connected> ws://127.0.0.1:7777/devtools/browser/...
```

The CDP port was reachable and the WebSocket opened, but Playwright did not complete the browser handshake. The current desktop flow treats `http://127.0.0.1:7777/json/version` plus profile command-line match as enough health, then lets `connect_over_cdp()` use the long default timeout. When Chrome or the profile is stuck, the GUI can spend about three minutes on EZ preloading and another three minutes on the real carousel connection before marking the language failed.

## Goal

Release Shopify Image Localizer V7.6 so the desktop tool recovers once from a stuck CDP browser handshake instead of waiting for the long Playwright default and failing the language.

## Requirements

- Keep existing browser reuse behavior for healthy batch runs.
- Add a bounded CDP connect timeout for Playwright `connect_over_cdp()` calls.
- When the first Playwright CDP handshake fails, kill the CDP port owner and the target profile browser, restart the managed CDP Chrome, and retry once.
- Preserve the existing `C:\chrome-shopify-image` profile and per-domain profile rules.
- Make the recovery shared by EZ carousel, TAA detail, storefront-size probes, and managed-tab helpers that connect through the localizer CDP path.
- Do not connect to Windows local MySQL for verification.
- Bump `tools/shopify_image_localizer/version.py` to `7.6` after the regression test is green.

## Non-goals

- Do not change carousel/detail image pairing rules from V7.3, V7.4, or V7.5.
- Do not change Shopify Image Localizer bootstrap API payloads.
- Do not change EXE packaging credentials or release JSON schema.
- Do not add long-running background browser monitors.

## Anchors

- `AGENTS.md`: document-driven changes, project-local worktree isolation, no local Windows MySQL, focused pytest policy.
- `tools/shopify_image_localizer/CLAUDE.md`: Shopify Image Localizer CDP reuse and release gates.
- `docs/superpowers/specs/2026-05-11-shopify-image-localizer-cdp-reconnect-fix.md`: prior CDP reconnect incident and success criteria.
- `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`: EXE and portable ZIP release standard.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: focused pytest policy.

## Verification

- Regression test: `replace_many()` restarts the managed CDP Chrome and retries once when Playwright CDP handshake times out after WebSocket connect.
- Existing Shopify Image Localizer CDP tests still pass.
- Focused verification:

```bash
python scripts/pytest_related.py --base origin/master --run
python -m compileall tools/shopify_image_localizer
```

- Release verification follows `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md` for V7.6.
