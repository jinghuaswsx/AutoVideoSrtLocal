import os
import requests
import hashlib
from PIL import Image

sys_path = "/opt/autovideosrt"
import sys
sys.path.insert(0, sys_path)

url_correct_v = "https://cdn.shopify.com/s/files/1/0727/2831/4029/files/6a153b398faf9_20260526_d635f611_20260526_5d24dccc_from_url_en_01_397272e4c57da3bde45de1a933041431.webp?v=1779776316"
headers = {"User-Agent": "Mozilla/5.0"}
content = requests.get(url_correct_v, headers=headers).content

# Save downloaded content temporarily
temp_path = "/tmp/downloaded_verify.jpg"
with open(temp_path, "wb") as f:
    f.write(content)

print("--- DOWNLOADED IMAGE PROPERTIES ---")
img = Image.open(temp_path)
print(f"Format: {img.format}")
print(f"Size: {img.size}")
print(f"Mode: {img.mode}")

# Perform image compare using link_check_compare
from appcore.link_check_compare import compare_images

ref_path = "/opt/autovideosrt/output/link_check/b69aab1d-09b3-45a8-88f9-4dd42fbad2ab/reference/detail_002.png"
orig_path = "/opt/autovideosrt/output/link_check/b69aab1d-09b3-45a8-88f9-4dd42fbad2ab/original/detail_002.jpg"

if os.path.exists(ref_path):
    res = compare_images(temp_path, ref_path)
    print(f"\n--- Comparison with Italian Reference detail_002.png ---")
    print(f"  Result: {res}")

if os.path.exists(orig_path):
    res = compare_images(temp_path, orig_path)
    print(f"\n--- Comparison with English Original detail_002.jpg ---")
    print(f"  Result: {res}")

os.remove(temp_path)
