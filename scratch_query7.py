import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query

ids = (8859, 27711, 28250, 28908)
print("=== Querying mingkong_products for correct wig ===")
rows = query(f"SELECT * FROM mingkong_products WHERE id IN {ids}")
for r in rows:
    # Only print key fields to keep it clean
    print({k: r[k] for k in r if k in ('id', 'product_name', 'product_code', 'main_image')})

print("\n=== Querying mingkong_material_daily_snapshots for correct wig ===")
rows2 = query(f"SELECT DISTINCT mk_product_name, product_code, video_name FROM mingkong_material_daily_snapshots WHERE product_code LIKE '%wig%'")
for r in rows2:
    print(r)
