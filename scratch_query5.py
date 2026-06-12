import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query

print("=== Querying products mapped to mk_id 315 ===")
p315 = query("SELECT id, name, product_code, created_at, updated_at FROM media_products WHERE mk_id=315 AND deleted_at IS NULL")
for p in p315:
    print(p)

print("\n=== Querying media_products for code containing wig ===")
wigs = query("SELECT id, name, product_code, mk_id, created_at FROM media_products WHERE (product_code LIKE '%wig%' OR name LIKE '%wig%') AND deleted_at IS NULL")
for w in wigs:
    print(w)
