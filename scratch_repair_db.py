import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query, execute

print("=== Before Repair ===")
row_before = query("SELECT id, name, product_code, mk_id, main_image FROM media_products WHERE id=774")
print(row_before)

print("\n=== Performing Data Repair ===")
# Set mk_id to 28908, name to '修颜层次感假发', and set main_image
correct_name = "修颜层次感假发"
correct_mk_id = 28908
correct_main_image = "https://cdn.shopify.com/s/files/1/0780/4678/9920/files/418802218780111_7644bcb4-4855-481b-b369-3bdb2f01e90e.webp?v=1724750936"

execute(
    "UPDATE media_products SET name=%s, mk_id=%s, main_image=%s, updated_at=NOW() WHERE id=774",
    (correct_name, correct_mk_id, correct_main_image)
)

print("\n=== After Repair ===")
row_after = query("SELECT id, name, product_code, mk_id, main_image, updated_at FROM media_products WHERE id=774")
print(row_after)
