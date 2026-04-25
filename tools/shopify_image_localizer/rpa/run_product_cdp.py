from __future__ import annotations

"""Run carousel and detail-image replacement for one Shopify product.

This is the production batch path:
1. fetch localized material from the production bootstrap API;
2. replace carousel images in EZ Product Image Translate;
3. replace detail-description images in Translate & Adapt by updating the
   whole translated body_html value in one save.
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer import api_client, cancellation, downloader, settings, storage
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp, taa_cdp


DEFAULT_STORE_DOMAIN = "newjoyloo.com"
LANGUAGE_LABELS = {
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
}


def _normalize_src(src: str) -> str:
    value = str(src or "").strip()
    if value.startswith("//"):
        return f"https:{value}"
    return value


def _storefront_json_url(product_code: str, *, locale: str = "", store_domain: str = DEFAULT_STORE_DOMAIN) -> str:
    normalized_locale = str(locale or "").strip().strip("/")
    prefix = f"/{normalized_locale}" if normalized_locale else ""
    return f"https://{store_domain}{prefix}/products/{product_code}.js"


def fetch_storefront_product(
    product_code: str,
    *,
    locale: str = "",
    store_domain: str = DEFAULT_STORE_DOMAIN,
    timeout_s: int = 20,
) -> dict[str, Any]:
    url = _storefront_json_url(product_code, locale=locale, store_domain=store_domain)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    product = payload.get("product") if isinstance(payload, dict) and isinstance(payload.get("product"), dict) else payload
    if not isinstance(product, dict) or not product.get("id"):
        raise RuntimeError(f"failed to fetch storefront product JSON: {url}")
    return product


def product_image_sources(product: dict[str, Any]) -> list[str]:
    images = product.get("images") or []
    srcs: list[str] = []
    for image in images:
        if isinstance(image, str):
            src = image
        elif isinstance(image, dict):
            src = image.get("src") or image.get("url") or ""
        else:
            src = ""
        src = _normalize_src(src)
        if src:
            srcs.append(src)
    return srcs


def _localized_by_token(localized_images: list[dict]) -> dict[str, list[dict[str, Any]]]:
    return taa_cdp.build_localized_candidates(localized_images)


def _choose_carousel_candidate(
    slot_idx: int,
    src: str,
    candidates_by_token: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    token = ez_cdp.md5_token(src)
    if not token:
        return None
    candidates = candidates_by_token.get(token) or []
    if not candidates:
        return None
    exact = [row for row in candidates if row.get("source_index") == slot_idx]
    if exact:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    no_index = [row for row in candidates if row.get("source_index") is None]
    if len(no_index) == 1:
        return no_index[0]
    options = [f"{row.get('source_index')}:{row.get('filename')}" for row in candidates]
    raise ValueError(f"ambiguous carousel source for slot {slot_idx} token {token}: {options}")


def pair_carousel_images(localized_images: list[dict], product_images: list[dict] | list[str]) -> list[tuple[int, str]]:
    candidates_by_token = _localized_by_token(localized_images)
    pairs: list[tuple[int, str]] = []
    for idx, image in enumerate(product_images):
        if isinstance(image, str):
            src = image
        else:
            src = str(image.get("src") or image.get("url") or "")
        src = _normalize_src(src)
        if not src or src.lower().split("?", 1)[0].endswith(".gif"):
            continue
        candidate = _choose_carousel_candidate(idx, src, candidates_by_token)
        if candidate is None:
            continue
        pairs.append((idx, str(candidate["local_path"])))
    return pairs


def build_detail_source_index_map(
    body_html: str,
    reference_images: list[dict],
    *,
    carousel_image_count: int,
) -> dict[str, int]:
    reference_by_token: dict[str, list[int]] = {}
    for item in reference_images:
        filename = str(item.get("filename") or "")
        token = ez_cdp.md5_token(filename)
        source_index = taa_cdp.source_index_from_filename(filename)
        if token and source_index is not None:
            reference_by_token.setdefault(token, []).append(source_index)

    mapping: dict[str, int] = {}
    used_indices: set[int] = set()
    for src in taa_cdp.extract_image_srcs(body_html):
        token = ez_cdp.md5_token(src)
        if not token or token in mapping:
            continue
        candidates = sorted(set(reference_by_token.get(token) or []))
        if not candidates:
            continue
        detail_side = [idx for idx in candidates if idx >= carousel_image_count and idx not in used_indices]
        if detail_side:
            source_index = detail_side[0]
        else:
            unused = [idx for idx in candidates if idx not in used_indices]
            if len(unused) != 1:
                continue
            source_index = unused[0]
        mapping[token] = source_index
        used_indices.add(source_index)
    return mapping


def fetch_bootstrap_ready(
    *,
    product_code: str,
    lang: str,
    timeout_s: int,
    shopify_product_id: str = "",
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, Any]:
    cfg = settings.load_runtime_config()
    deadline = time.time() + timeout_s
    attempt = 0
    last_error: Exception | None = None
    while True:
        cancellation.throw_if_cancelled(cancel_token)
        attempt += 1
        try:
            payload = api_client.fetch_bootstrap(
                cfg["base_url"],
                cfg["api_key"],
                product_code,
                lang,
                shopify_product_id=shopify_product_id,
            )
            localized_count = len(payload.get("localized_images") or [])
            if localized_count > 0:
                print(f"[bootstrap] READY attempt={attempt} localized={localized_count}")
                return payload
            last_error = RuntimeError("bootstrap returned no localized images")
        except api_client.ApiError as exc:
            last_error = exc
            error_code = str(exc.payload.get("error") or "")
            print(f"[bootstrap] {exc.status_code} {error_code}: {exc}")
            if error_code == "shopify_product_id_missing":
                raise
        except Exception as exc:
            last_error = exc
            print(f"[bootstrap] attempt={attempt} failed: {exc}")
        if time.time() >= deadline:
            break
        cancellation.cancellable_sleep(cancel_token, 5)
    raise TimeoutError(f"bootstrap not ready for {product_code}/{lang}: {last_error}") from last_error


def download_localized(
    product_code: str,
    lang: str,
    bootstrap: dict[str, Any],
    *,
    cancel_token: cancellation.CancellationToken | None = None,
) -> tuple[storage.Workspace, list[dict]]:
    workspace = storage.create_workspace(product_code, lang)
    localized_images = bootstrap.get("localized_images") or []
    print(f"[download] {len(localized_images)} image(s) -> {workspace.source_localized_dir}")
    downloaded = downloader.download_images(
        localized_images,
        workspace.source_localized_dir,
        retries=2,
        cancel_token=cancel_token,
    )
    return workspace, downloaded


def _extension_from_url(src: str) -> str:
    suffix = Path(urlparse(src).path).suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg", "png", "webp"}:
        return suffix
    return "jpg"


def add_original_detail_fallbacks(
    *,
    workspace: storage.Workspace,
    body_html: str,
    localized_images: list[dict],
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict]:
    candidates_by_token = _localized_by_token(localized_images)
    added: list[dict] = []
    for idx, src in enumerate(taa_cdp.extract_image_srcs(body_html)):
        cancellation.throw_if_cancelled(cancel_token)
        token = ez_cdp.md5_token(src)
        if not token or token in candidates_by_token:
            continue
        if src.lower().split("?", 1)[0].endswith(".gif"):
            continue
        ext = _extension_from_url(src)
        filename = f"fallback_original_from_url_en_{idx:02d}_{token}.{ext}"
        output_path = workspace.source_localized_dir / filename
        print(f"[detail] fallback original image for token={token}: {src}")
        request = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            output_path.write_bytes(response.read())
        row = {
            "id": f"fallback-{token}",
            "kind": "detail",
            "filename": filename,
            "url": src,
            "local_path": str(output_path),
            "fallback_original": True,
        }
        localized_images.append(row)
        candidates_by_token.setdefault(token, []).append(row)
        added.append(row)
    return added


def verify_storefront_body(
    product_code: str,
    *,
    locale: str,
    expected_urls: list[str],
    store_domain: str = DEFAULT_STORE_DOMAIN,
) -> dict[str, Any]:
    product = fetch_storefront_product(product_code, locale=locale, store_domain=store_domain)
    body_html = str(product.get("description") or product.get("body_html") or "")
    srcs = taa_cdp.extract_image_srcs(body_html)
    return {
        "product_id": str(product.get("id") or ""),
        "title": product.get("title"),
        "image_count": len(srcs),
        "expected_total": len(expected_urls),
        "expected_present": sum(1 for url in expected_urls if url in body_html),
        "old_non_shopify_count": sum(1 for src in srcs if "cdn.shopify.com/s/files/" not in src),
    }


def fetch_storefront_image_display_sizes(
    *,
    product_code: str,
    locale: str,
    store_domain: str,
    user_data_dir: str,
    port: int,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, dict[str, Any]]:
    cancellation.throw_if_cancelled(cancel_token)
    normalized_locale = str(locale or "").strip().strip("/")
    prefix = f"/{normalized_locale}" if normalized_locale else ""
    url = f"https://{store_domain}{prefix}/products/{product_code}"
    ez_cdp.ensure_cdp_chrome(user_data_dir, url, port=port)
    sizes: dict[str, dict[str, Any]] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(ez_cdp._cdp_ws_endpoint(port))
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            cancellation.throw_if_cancelled(cancel_token)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            cancellation.throw_if_cancelled(cancel_token)
            page.wait_for_timeout(4000)
            cancellation.throw_if_cancelled(cancel_token)
            rows = page.evaluate(
                """() => Array.from(document.images).map((img) => {
                    const rect = img.getBoundingClientRect();
                    return {
                        src: img.currentSrc || img.src || '',
                        width: Math.round(rect.width || 0),
                        height: Math.round(rect.height || 0),
                        naturalWidth: img.naturalWidth || 0,
                        naturalHeight: img.naturalHeight || 0,
                    };
                })"""
            )
            for row in rows or []:
                src = _normalize_src(str(row.get("src") or ""))
                if src and int(row.get("width") or 0) > 0:
                    sizes[src] = row
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
    cancellation.throw_if_cancelled(cancel_token)
    return sizes


def run(
    args: argparse.Namespace,
    *,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, Any]:
    cfg = settings.load_runtime_config()
    cancellation.throw_if_cancelled(cancel_token)
    source_product = fetch_storefront_product(args.product_code, store_domain=args.store_domain)
    cancellation.throw_if_cancelled(cancel_token)
    target_product = fetch_storefront_product(
        args.product_code,
        locale=args.shop_locale,
        store_domain=args.store_domain,
    )
    cancellation.throw_if_cancelled(cancel_token)
    product_id = str(args.product_id or source_product.get("id") or target_product.get("id") or "").strip()
    if not product_id:
        raise RuntimeError("Shopify product id not found")
    if str(target_product.get("id") or "") and str(target_product.get("id")) != product_id:
        raise RuntimeError(f"source/target product id mismatch: {product_id} vs {target_product.get('id')}")

    bootstrap = fetch_bootstrap_ready(
        product_code=args.product_code,
        lang=args.lang,
        timeout_s=args.bootstrap_timeout_s,
        shopify_product_id=product_id,
        cancel_token=cancel_token,
    )
    workspace, downloaded = download_localized(
        args.product_code,
        args.lang,
        bootstrap,
        cancel_token=cancel_token,
    )
    cancellation.throw_if_cancelled(cancel_token)

    result: dict[str, Any] = {
        "product_code": args.product_code,
        "lang": args.lang,
        "shop_locale": args.shop_locale,
        "shopify_product_id": product_id,
        "workspace": str(workspace.root),
        "download_dir": str(workspace.source_localized_dir),
        "carousel": None,
        "detail": None,
        "storefront": None,
    }

    product_images = product_image_sources(source_product)
    if not args.skip_carousel:
        cancellation.throw_if_cancelled(cancel_token)
        pairs = pair_carousel_images(downloaded, product_images)
        if not pairs:
            raise RuntimeError("no carousel image pairs found")
        print(f"[carousel] replacing {len(pairs)} slot(s)")
        ez_url = session.build_ez_url(product_id)
        carousel_results = ez_cdp.replace_many(
            ez_url=ez_url,
            user_data_dir=cfg["browser_user_data_dir"],
            pairs=pairs,
            language=args.language,
            replace_existing=not args.skip_existing_carousel,
            port=args.port,
            limit=args.carousel_limit if args.carousel_limit > 0 else None,
            cancel_token=cancel_token,
        )
        cancellation.throw_if_cancelled(cancel_token)
        result["carousel"] = {
            "requested": len(pairs),
            "results": carousel_results,
            "ok": sum(1 for row in carousel_results if row.get("status") == "ok"),
            "skipped": sum(1 for row in carousel_results if row.get("status") == "skipped"),
        }

    if not args.skip_detail:
        cancellation.throw_if_cancelled(cancel_token)
        detail_html = str(target_product.get("description") or target_product.get("body_html") or "")
        fallback_images: list[dict] = []
        if not args.no_original_detail_fallback:
            fallback_images = add_original_detail_fallbacks(
                workspace=workspace,
                body_html=detail_html,
                localized_images=downloaded,
                cancel_token=cancel_token,
            )
        display_size_by_src: dict[str, dict[str, Any]] = {}
        if not args.no_preserve_detail_size:
            display_size_by_src = fetch_storefront_image_display_sizes(
                product_code=args.product_code,
                locale=args.shop_locale,
                store_domain=args.store_domain,
                user_data_dir=cfg["browser_user_data_dir"],
                port=args.port,
                cancel_token=cancel_token,
            )
            print(f"[detail] captured display sizes for {len(display_size_by_src)} image(s)")
        source_index_map = taa_cdp.parse_source_index_map(args.source_index_map)
        if not source_index_map:
            source_index_map = build_detail_source_index_map(
                detail_html,
                bootstrap.get("reference_images") or [],
                carousel_image_count=len(product_images),
            )
        print(f"[detail] source-index map={source_index_map}")
        detail_result = taa_cdp.replace_detail_images(
            product_id=product_id,
            shop_locale=args.shop_locale,
            user_data_dir=cfg["browser_user_data_dir"],
            localized_images=downloaded,
            source_index_by_token=source_index_map,
            display_size_by_src=display_size_by_src,
            port=args.port,
            replace_shopify_cdn=args.replace_shopify_cdn,
            verify_reload=not args.no_detail_reload_verify,
            cancel_token=cancel_token,
        )
        cancellation.throw_if_cancelled(cancel_token)
        result["detail"] = {key: value for key, value in detail_result.items() if key != "verify"}
        result["detail"]["fallback_original_count"] = len(fallback_images)
        result["detail"]["fallback_originals"] = [
            {
                "token": row.get("token") or ez_cdp.md5_token(str(row.get("filename") or "")),
                "local_path": row.get("local_path"),
                "url": row.get("url"),
            }
            for row in fallback_images
        ]
        result["detail"]["verify"] = {
            key: value for key, value in detail_result.get("verify", {}).items() if key != "html"
        }
        expected_urls = [row["new"] for row in detail_result.get("replacements") or [] if row.get("new")]
        result["storefront"] = verify_storefront_body(
            args.product_code,
            locale=args.shop_locale,
            expected_urls=expected_urls,
            store_domain=args.store_domain,
        )
        cancellation.throw_if_cancelled(cancel_token)

    output_path = workspace.root / f"shopify_batch_{args.lang}_result.json"
    storage.write_json(output_path, result)
    print(f"[result] {output_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-code", required=True)
    parser.add_argument("--lang", default="it")
    parser.add_argument("--shop-locale", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--product-id", default="")
    parser.add_argument("--store-domain", default=DEFAULT_STORE_DOMAIN)
    parser.add_argument("--bootstrap-timeout-s", type=int, default=60)
    parser.add_argument("--port", type=int, default=ez_cdp.DEFAULT_CDP_PORT)
    parser.add_argument("--carousel-limit", type=int, default=0)
    parser.add_argument("--skip-carousel", action="store_true")
    parser.add_argument("--skip-detail", action="store_true")
    parser.add_argument("--skip-existing-carousel", action="store_true")
    parser.add_argument("--source-index-map", default="")
    parser.add_argument("--replace-shopify-cdn", action="store_true")
    parser.add_argument("--no-preserve-detail-size", action="store_true")
    parser.add_argument("--no-original-detail-fallback", action="store_true")
    parser.add_argument("--no-detail-reload-verify", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.lang = str(args.lang or "").strip().lower()
    args.shop_locale = str(args.shop_locale or args.lang).strip().lower()
    args.language = str(args.language or LANGUAGE_LABELS.get(args.lang) or args.lang).strip()
    try:
        run(args)
    except api_client.ApiError as exc:
        print(f"[blocked] bootstrap API {exc.status_code}: {json.dumps(exc.payload, ensure_ascii=False)}")
        raise SystemExit(2) from exc
    except TimeoutError as exc:
        print(f"[blocked] {exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
