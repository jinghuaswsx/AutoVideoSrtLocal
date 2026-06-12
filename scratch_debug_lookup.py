import sys
sys.path.append('/opt/autovideosrt')
import requests
from appcore.pushes import get_localized_texts_base_url, build_localized_texts_headers
import json

base = get_localized_texts_base_url()
headers = build_localized_texts_headers()
url = f"{base}/api/marketing/medias"
code = 'face-framing-layered-wig-collection-rjc'
params = {"page": 1, "q": code, "source": "", "level": "", "show_attention": 0}

print("Sending request to:", url, "with params:", params)
try:
    resp = requests.get(url, params=params, headers=headers, timeout=90)
    print("status_code:", resp.status_code)
    data = resp.json()
except Exception as e:
    print("Request failed:", e)
    sys.exit(1)

items = (data.get("data") or {}).get("items") or []
print("Total items returned:", len(items))
for item in items:
    print(f"ID: {item.get('id')}, Title: {item.get('title') or item.get('name')}")
    print(f"  Product Links: {item.get('product_links')}")
