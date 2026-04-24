from __future__ import annotations

"""Run IT replacement in EZ Product Translate through external Chrome CDP.

Usage:
    python -m tools.shopify_image_localizer.rpa.run_it_cdp --limit 1
    python -m tools.shopify_image_localizer.rpa.run_it_cdp --cached-bootstrap tmp_probe/bootstrap_it_20260424_165225.json
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

from tools.shopify_image_localizer import api_client, downloader, settings, storage
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp


PRODUCT_CODE = "dino-glider-launcher-toy-rjc"
LANG = "it"
LANGUAGE = "Italian"
SHOPIFY_PRODUCT_ID = "8552296546477"
SHOPIFY_PRODUCT_JSON = "https://0ixug9-pv.myshopify.com/products/dino-glider-launcher-toy-rjc.json"


def _load_cached_bootstrap(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if "response" in payload:
        return payload["response"]["body"]
    return payload


def _fetch_bootstrap(product_code: str, lang: str, timeout_s: int, cached_bootstrap: str | None) -> dict:
    cfg = settings.load_runtime_config()
    start = time.time()
    attempt = 0
    last_error = ""
    while time.time() - start < timeout_s:
        attempt += 1
        try:
            bootstrap = api_client.fetch_bootstrap(cfg["base_url"], cfg["api_key"], product_code, lang)
            if bootstrap.get("localized_images"):
                print(f"[bootstrap] READY attempt={attempt} images={len(bootstrap['localized_images'])}")
                return bootstrap
        except Exception as exc:
            last_error = str(exc)
            if attempt == 1 or attempt % 5 == 0:
                print(f"[bootstrap] not ready attempt={attempt}: {last_error[:120]}")
        time.sleep(5)
    if cached_bootstrap:
        print(f"[bootstrap] live not ready; using cached response: {cached_bootstrap}")
        return _load_cached_bootstrap(cached_bootstrap)
    raise TimeoutError(f"bootstrap not ready in {timeout_s}s: {last_error}")


def _download_localized(product_code: str, lang: str, bootstrap: dict) -> list[dict]:
    workspace = storage.create_workspace(product_code, lang)
    localized_images = bootstrap.get("localized_images") or []
    print(f"[download] downloading {len(localized_images)} images to {workspace.source_localized_dir}")
    return downloader.download_images(localized_images, workspace.source_localized_dir, retries=2)


def _pair_with_shopify(localized_images: list[dict], shopify_product_json: str) -> list[tuple[int, str]]:
    os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
    os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
    with urllib.request.urlopen(shopify_product_json, timeout=15) as response:
        shopify_images = json.loads(response.read())["product"]["images"]
    by_hash = {
        ez_cdp.md5_token(str(item.get("filename") or "")): item
        for item in localized_images
        if ez_cdp.md5_token(str(item.get("filename") or ""))
    }
    pairs: list[tuple[int, str]] = []
    for idx, image in enumerate(shopify_images):
        token = ez_cdp.md5_token(str(image.get("src") or ""))
        matched = by_hash.get(token or "")
        if matched:
            local_path = str(matched["local_path"])
            pairs.append((idx, local_path))
            print(f"[pair] slot {idx} <- {Path(local_path).name}")
        else:
            print(f"[pair] slot {idx} <- NO MATCH {token}")
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-s", type=int, default=60)
    parser.add_argument("--cached-bootstrap", default="")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N paired slots")
    parser.add_argument("--skip-existing", action="store_true", help="Do not replace an existing Italian translation")
    parser.add_argument("--port", type=int, default=ez_cdp.DEFAULT_CDP_PORT)
    parser.add_argument("--product-code", default=PRODUCT_CODE)
    parser.add_argument("--lang", default=LANG)
    parser.add_argument("--language", default=LANGUAGE)
    parser.add_argument("--product-id", default=SHOPIFY_PRODUCT_ID)
    parser.add_argument("--shopify-product-json", default=SHOPIFY_PRODUCT_JSON)
    args = parser.parse_args()

    cfg = settings.load_runtime_config()
    ez_url = session.build_ez_url(args.product_id)

    bootstrap = _fetch_bootstrap(args.product_code, args.lang, args.timeout_s, args.cached_bootstrap or None)
    downloaded = _download_localized(args.product_code, args.lang, bootstrap)
    pairs = _pair_with_shopify(downloaded, args.shopify_product_json)
    if not pairs:
        raise RuntimeError("no image pairs found")

    limit = args.limit if args.limit > 0 else None
    print(f"[run] replacing {len(pairs) if limit is None else min(limit, len(pairs))} slot(s) via CDP")
    results = ez_cdp.replace_many(
        ez_url=ez_url,
        user_data_dir=cfg["browser_user_data_dir"],
        pairs=pairs,
        language=args.language,
        replace_existing=not args.skip_existing,
        port=args.port,
        limit=limit,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    ok = sum(1 for row in results if row.get("status") == "ok")
    print(f"[done] success={ok}/{len(results)}")


if __name__ == "__main__":
    main()
