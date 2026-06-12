import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query
import json

print("=== Querying mingkong_products for ID 315 ===")
p315 = query("SELECT * FROM mingkong_products WHERE id=315")
if p315:
    for p in p315:
        print(p)
else:
    print("No product 315 in mingkong_products")

print("\n=== Querying mingkong_material_daily_snapshots for product_code containing wig ===")
snapshots = query("SELECT DISTINCT product_code, mk_product_name, product_name, video_name FROM mingkong_material_daily_snapshots WHERE product_code LIKE '%wig%'")
for s in snapshots:
    print(s)

print("\n=== Querying dianxiaomi_product_assets for product_code containing wig ===")
assets = query("SELECT id, product_code, product_name, product_cn_name, product_main_image_url FROM dianxiaomi_product_assets WHERE product_code LIKE '%wig%'")
for a in assets:
    print(a)
