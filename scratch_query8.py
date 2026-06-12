import sys
sys.path.append('/opt/autovideosrt')
from appcore.db import query

ids = (8859, 27711, 28250, 28908)
print("=== Querying mingkong_products columns ===")
rows = query(f"SELECT * FROM mingkong_products WHERE id IN {ids}")
for r in rows:
    # Print the whole dict to check keys and values
    print(r)
