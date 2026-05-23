# Shopify Image Localizer EXE Automated Release & Configuration Standard

This document is the absolute source of truth (SOT) for packaging, configuring, and publishing the `Shopify Image Localizer` Windows EXE program module. It applies to all developer agents (Gemini, Claude Code, Codex) and is executed automatically by the system scheduler.

---

## 1. Core Code Integrations (Must-Have Fixes)

Every packaged version of the localizer must inherit the following core RPA/CDP fixes:
1. **Lazy-Loading Image Attributes**:
   - Theme templates often store high-res or deferred images under lazy-loading attributes.
   - The parser **must** replace all occurrences of `data-src`, `data-lazy-src`, `data-actual-src`, and custom image-source tags inside the `<img>` tag with the new localized URL.
2. **Responsive Image Stripping**:
   - Modern Shopify themes load responsive layouts using `srcset` and `data-srcset`.
   - The parser **must completely strip/delete** `srcset` and `data-srcset` attributes from the `<img>` tag. Stripping these forces the browser layout engine to render the single high-quality localized image specified in the `src` attribute, avoiding accidental loads of original English responsive assets.

*Reference Implementation*: [taa_cdp.py](file:///g:/Code/AutoVideoSrtLocal/tools/shopify_image_localizer/rpa/taa_cdp.py) -> `_replace_img_src_preserving_tag(...)`.

---

## 2. Mandatory Build Configurations

The packaged portable ZIP must be constructed with these precise environment configurations:
- **API Authentication Key**:
   - The packaging config `api_key` must **never** be blank, placeholders (`demo-key`, `changeme`), or hardcoded developer tokens.
   - It must be dynamically extracted from the production environment config `openapi_materials` DB key or `.env` record: `SHOPIFY_IMAGE_LOCALIZER_API_KEY`.
- **Default Browser User Profile**:
   - The Chrome User Data Profile directory in `shopify_image_localizer_config.json` and `shopify_image_localizer_default_config.json` must be hardcoded to `C:\chrome-shopify-image`.
- **JSON Encoding Guard**:
   - Configuration files (`shopify_image_localizer_config.json`, `shopify_image_localizer_default_config.json`, `release_manifest.json`) must be saved in **UTF-8 without BOM** encoding to prevent Python JSON-decoder parsing failures.
- **Traceability Manifest**:
   - `release_manifest.json` must record the exact `source_commit` hash of `origin/master` from which the package was compiled.

---

## 3. Remote Linux Wine Build Environment

The packaging compilation runs on the production Linux server under the Wine 11+ runtime.
- **Wine Prefix Ownership**:
  - The default Wine prefix directory is `/home/cjh/wine-shopify-build`.
  - To prevent directory ownership and prefix lock conflicts, all Wine PyInstaller compilation commands **must run under the user `cjh`**.
- **Execution Command Syntax**:
  ```bash
  sudo -i -u cjh bash -c "set -e && cd /opt/autovideosrt && bash scripts/build_shopify_image_localizer_wine.sh --release-standard-read --version <version> --release-note '<note>'"
  ```

---

## 4. Scheduled Automated Release Pipeline

The system registers an background APScheduler task to automate version checks and releases.
- **Task Code**: `shopify_image_localizer_auto_release`
- **Schedule**: Every 30 minutes (`*/30 * * * *`).
- **Workflow**:
  1. Checks `tools/shopify_image_localizer/version.py`'s `RELEASE_VERSION` on `master`.
  2. Compares it against currently published version in `system_settings` (`shopify_image_localizer_release`).
  3. If version is bumped, performs remote Wine build under user `cjh`.
  4. Copies ZIP to public downloads directory, registers release to the DB, and validates static URL accessibility.

---

## 5. Agent Verification Checklist (Cross-Agent Standard)

Whenever Gemini, Claude Code, or Codex initiates a release or build, they must execute the following order of actions:
1. Run pytest suite on related units:
   ```bash
   pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_release_web.py -q
   ```
2. Run the Wine compiler script with `--release-standard-read`.
3. Unzip the resulting artifact and inspect:
   - Config file content (`api_key` matches, `browser_user_data_dir == "C:\\chrome-shopify-image"`).
   - Verify encoding has no UTF-8 BOM.
4. Verify HTTP static link returns `200` or `206` via a quick Range-header `curl` request.
5. Advise the user to perform a sanity run check on a physical Windows device.
