import sys
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

sys.path.insert(0, '/opt/autovideosrt-test')
from appcore.link_check_fetcher import LinkCheckFetcher, extract_images_from_html

url = "https://newjoyloo.com/it/products/effortless-precision-toenail-trimmer-rjc"

fetcher = LinkCheckFetcher()
print("Fetching page using LinkCheckFetcher...")
page = fetcher.fetch_page(url, "it")

print("\n--- ALL EXTRACTED IMAGES ---")
for idx, img in enumerate(page.images):
    print(f"Image #{idx}: {img}")
