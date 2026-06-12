import sys
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse, urljoin

sys.path.insert(0, '/opt/autovideosrt')
from appcore.link_check_fetcher import _is_placeholder_src, _image_source, _image_dedupe_key, _absolute_image_url

url = "https://newjoyloo.com/it/products/effortless-precision-toenail-trimmer-rjc?nocache=1779866750493&variant=46041794445485"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    
    html = page.content()
    base_url = page.url
    browser.close()

soup = BeautifulSoup(html, "html.parser")
items = []
seen = set()

print("--- DEBUGGING extract_images_from_html STEP BY STEP ---")

carousel_selectors = [
    "[data-media-id] img",
    ".t4s-product__media-item img",
    ".product__media img",
    ".featured img",
]

# 1. Variant featured images
print("\n--- 1. Variant Featured Images ---")
from appcore.link_check_fetcher import _variant_featured_images
var_imgs = _variant_featured_images(soup, base_url=base_url)
print("Variant images:", var_imgs)
for s_url in var_imgs:
    parsed_s = urlparse(s_url)
    d_key = _image_dedupe_key(s_url)
    print(f"Adding variant image: {s_url}")
    print(f"  Dedupe key: {d_key}")
    seen.add(d_key)
    items.append({"kind": "carousel", "source_url": s_url})

# 2. Carousel selectors
print("\n--- 2. Carousel Selectors ---")
for selector in carousel_selectors:
    print(f"\nProcessing selector: {selector}")
    nodes = soup.select(selector)
    print(f"Found {len(nodes)} nodes matching selector.")
    for idx, node in enumerate(nodes):
        src = _image_source(node)
        if not src:
            print(f"  Node {idx}: No src extracted from {str(node)[:100]}")
            continue
        absolute = _absolute_image_url(src, base_url)
        d_key = _image_dedupe_key(absolute)
        in_seen = d_key in seen
        print(f"  Node {idx}: tag={str(node)[:80]}")
        print(f"    extracted src: {src}")
        print(f"    absolute src: {absolute}")
        print(f"    dedupe key: {d_key} (Already in seen? {in_seen})")
        if not in_seen:
            seen.add(d_key)
            items.append({"kind": "carousel", "source_url": absolute})

# 3. Swapping matching English carousel URLs with localized URLs
print("\n--- 3. Localized Swapping Heuristics ---")
all_urls = []
for node in soup.find_all("img"):
    for attr in ("src", "data-src", "data-master"):
        val = node.get(attr)
        if val:
            all_urls.append(_absolute_image_url(val, base_url))

import re
token_to_localized = {}
loc_pattern = re.compile(r"from_url_en_\d+_(?P<token>[a-f0-9]{28,})", re.I)
for s_url in all_urls:
    match = loc_pattern.search(s_url)
    if match:
        token = match.group("token").lower()
        token_to_localized[token] = s_url

print("Token to localized map:")
for k, v in token_to_localized.items():
    print(f"  {k} -> {v}")

carousel_token_re = re.compile(r"([a-f0-9]{28,})", re.I)
for idx, item in enumerate(items):
    if item["kind"] == "carousel":
        s_url = item["source_url"]
        if loc_pattern.search(s_url):
            print(f"Item #{idx}: already localized, skipping swapping. url={s_url}")
            continue
        token_match = carousel_token_re.search(s_url.lower())
        if token_match:
            token = token_match.group(1).lower()
            has_match = token in token_to_localized
            print(f"Item #{idx}: English URL has token={token} (In map? {has_match})")
            if has_match:
                print(f"  Swapping {s_url} -> {token_to_localized[token]}")
                item["source_url"] = token_to_localized[token]
