import sys
sys.path.append('/opt/autovideosrt')
from appcore.mk_import import _fetch_mk_product_detail
from appcore.db import query
import json

print("=== Fetching Wedev Detail for mk_id 315 ===")
detail = _fetch_mk_product_detail(315)
print(json.dumps(detail, indent=2, ensure_ascii=False))

print("\n=== Fetching Wedev Detail for a correct Wig mk_id (e.g. 8859) ===")
detail_wig = _fetch_mk_product_detail(8859)
print(json.dumps(detail_wig, indent=2, ensure_ascii=False))

print("\n=== Querying all mingkong product tables ===")
# List all table names in DB
tables = query("SHOW TABLES")
for t in tables:
    name = list(t.values())[0]
    if 'mingkong' in name or 'mk' in name or 'dianxiaomi' in name:
        print(name)
