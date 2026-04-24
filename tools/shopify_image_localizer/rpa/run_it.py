"""End-to-end runner: ensure Chrome -> fetch+download it images -> RPA replace 9 slots.

Usage:
    python -m tools.shopify_image_localizer.rpa.run_it
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from tools.shopify_image_localizer import api_client, downloader, settings, storage
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_pyautogui as rpa


PRODUCT_CODE = "dino-glider-launcher-toy-rjc"
LANG = "it"
EZ_URL = (
    f"https://admin.shopify.com/store/0ixug9-pv/apps/"
    f"ez-product-image-translate/product/8552296546477"
)
SHOPIFY_PRODUCT_JSON = (
    "https://0ixug9-pv.myshopify.com/products/dino-glider-launcher-toy-rjc.json"
)


def _md5(s: str) -> str | None:
    m = re.search(r"([a-f0-9]{28,})", (s or "").lower())
    return m.group(1) if m else None


def ensure_chrome() -> None:
    cfg = settings.load_runtime_config()
    user_data_dir = cfg["browser_user_data_dir"]
    if session.is_chrome_running_for_profile(user_data_dir):
        print(f"[chrome] already running with profile {user_data_dir}")
        # 重新打开 EZ 这个 url（同 profile 多次 chrome.exe 会塞 tab 给现有实例）
        session.open_urls_in_chrome(user_data_dir, [EZ_URL])
    else:
        print(f"[chrome] launching with profile {user_data_dir}")
        session.start_chrome(user_data_dir, initial_urls=[EZ_URL])
    print("[chrome] waiting 12s for EZ to load")
    time.sleep(12)


def fetch_and_download_it(timeout_s: int = 600) -> list[dict]:
    """Retry bootstrap until OK; on the very first OK response immediately
    download all localized images to local workspace. Returns list of downloaded items.
    """
    cfg = settings.load_runtime_config()
    ws = storage.create_workspace(PRODUCT_CODE, LANG)
    print(f"[bootstrap] workspace={ws.source_localized_dir}")
    start = time.time()
    attempt = 0
    while time.time() - start < timeout_s:
        attempt += 1
        try:
            b = api_client.fetch_bootstrap(cfg["base_url"], cfg["api_key"], PRODUCT_CODE, LANG)
            if b.get("localized_images"):
                print(f"[bootstrap] attempt {attempt} (t={time.time()-start:.1f}s): READY {len(b['localized_images'])} imgs")
                # 立即下载，不要漏掉短窗口
                loc = downloader.download_images(b["localized_images"], ws.source_localized_dir)
                print(f"[bootstrap] downloaded {len(loc)} imgs")
                return loc
        except Exception as exc:
            if attempt % 5 == 1:
                print(f"[bootstrap] attempt {attempt} (t={time.time()-start:.1f}s): {str(exc)[:80]}")
        time.sleep(5)
    raise TimeoutError(f"it bootstrap never ready in {timeout_s}s")


def pair_with_shopify(loc_images: list[dict]) -> list[tuple[int, str]]:
    """从 Shopify 公开 product json 拿 9 张图顺序，按 hash 配对到本地 it 图。"""
    os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
    os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
    with urllib.request.urlopen(SHOPIFY_PRODUCT_JSON, timeout=15) as r:
        shop_imgs = json.loads(r.read())["product"]["images"]
    by_hash = {_md5(x["filename"]): x for x in loc_images if _md5(x["filename"])}
    pairs: list[tuple[int, str]] = []
    for idx, s in enumerate(shop_imgs):
        h = _md5(s["src"])
        match = by_hash.get(h or "")
        if match:
            pairs.append((idx, match["local_path"]))
            print(f"  slot {idx} <- {os.path.basename(match['local_path'])}")
        else:
            print(f"  slot {idx} <- NO MATCH (hash {h})")
    return pairs


def main() -> None:
    print("=" * 70)
    print("Shopify image localizer — end-to-end IT replacement")
    print("=" * 70)
    print()

    print("[1/4] ensuring Chrome on EZ page...")
    ensure_chrome()
    print()

    print("[2/4] retrying it bootstrap until ready, then downloading...")
    loc = fetch_and_download_it()
    print()

    print("[3/4] pairing with Shopify product images...")
    pairs = pair_with_shopify(loc)
    if len(pairs) < 9:
        print(f"[warn] only {len(pairs)} pairs (expected 9); proceeding")
    print()

    print(f"[4/4] running RPA on {len(pairs)} pairs — press ESC to abort at any time")
    print("       Chrome must stay un-minimized; topmost is enforced before each slot")
    print("       Buttons located dynamically via cv2 + auto-scroll for long pages")
    time.sleep(3)
    results = rpa.replace_many_dynamic(pairs, language=LANG)
    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    for r in results:
        print(f"  slot {r['slot']}: {r['status']}  {r.get('error', '')}")
    ok = sum(1 for r in results if r['status'] == 'ok')
    print(f"\nSuccess: {ok}/{len(results)}")


if __name__ == "__main__":
    main()
