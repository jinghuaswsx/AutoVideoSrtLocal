import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query

print("=== Current state of product 774 ===")
row = query("SELECT id, name, product_code, mk_id, main_image, updated_at FROM media_products WHERE id=774")
print(row)
